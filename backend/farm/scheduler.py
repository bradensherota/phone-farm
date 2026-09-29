"""The runner: turns planned jobs into taps on phones.

Every device is its own serial queue (one action at a time, like a person holding
the phone). Devices run in parallel with each other. A job is due when its explicit
time has passed, or, if it has none, when its day's routine start on that device
has passed. Between actions on the same device the runner waits a random gap.
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import date, datetime, time as dtime, timedelta

from sqlalchemy import or_
from sqlalchemy.orm import Session, selectinload

from . import settings as settings_mod
from .config import SHOT_DIR
from .db import session_scope
from .devices.base import ActionSpec
from .devices.registry import DeviceManager
from .models import Account, DaySchedule, Device, Job, JobLog, JobStatus, MediaItem

log = logging.getLogger("farm.runner")

TICK_SECONDS = 10


def _parse_hhmm(v: str) -> dtime:
    try:
        h, m = v.split(":")
        return dtime(int(h), int(m))
    except Exception:
        return dtime(7, 0)


def day_start(s: Session, device: Device, day: date) -> datetime:
    """When this phone's queue starts on `day`: the planner's per-day time, else the routine."""
    row = s.query(DaySchedule).filter_by(device_id=device.id, run_date=day).first()
    return datetime.combine(day, _parse_hhmm(row.start if row and row.start else device.routine_start))


def due_jobs(s: Session, device: Device, now: datetime) -> list[Job]:
    today = now.date()
    routine = day_start(s, device, today)
    q = (
        s.query(Job)
        .options(selectinload(Job.account), selectinload(Job.media))
        .filter(Job.device_id == device.id, Job.status == JobStatus.scheduled)
        .filter(
            or_(
                Job.run_at.isnot(None) & (Job.run_at <= now),
                # a job from an earlier day never fires on its own (see `_expire_missed`);
                # "Run day" or "Run now" give it an explicit time if it should still go out
                Job.run_at.is_(None) & (Job.run_date == today) & (routine <= now),
            )
        )
        .order_by(Job.run_date, Job.sort_order, Job.id)
    )
    return q.all()


def next_run_for(job: Job, device: Device, s: Session | None = None) -> datetime:
    if job.run_at:
        return job.run_at
    if s is not None:
        return day_start(s, device, job.run_date)
    return datetime.combine(job.run_date, _parse_hhmm(device.routine_start))


class Runner:
    def __init__(self, manager: DeviceManager):
        self.manager = manager
        self.busy: set[str] = set()
        self.current: dict[str, int] = {}  # device_id -> job_id
        self._task: asyncio.Task | None = None
        self._workers: dict[str, asyncio.Task] = {}
        self._stopping = False
        self._prepared_on: dict[str, object] = {}
        self.force: set[str] = set()  # phones allowed to keep going through the day although paused

    # ---- lifecycle -------------------------------------------------------
    def start(self) -> None:
        self._stopping = False
        self._event_loop = asyncio.get_running_loop()
        self._task = asyncio.create_task(self._loop(), name="runner-loop")

    async def stop(self) -> None:
        self._stopping = True
        if self._task:
            self._task.cancel()
        for t in self._workers.values():
            t.cancel()

    async def _loop(self) -> None:
        # Recover anything left "running" by a crash
        with session_scope() as s:
            for j in s.query(Job).filter(Job.status == JobStatus.running).all():
                j.status = JobStatus.scheduled
                s.add(JobLog(job_id=j.id, level="warn", message="Recovered after restart; re-queued"))
        while not self._stopping:
            try:
                await self.tick()
            except Exception:
                log.exception("runner tick failed")
            await asyncio.sleep(TICK_SECONDS)

    def _expire_missed(self, now: datetime) -> None:
        """Planned jobs whose day has passed without running are marked failed ("missed"), so
        they show up in the planner instead of firing unexpectedly the next time the phone is
        resumed or the app starts."""
        with session_scope() as s:
            stale = s.query(Job).filter(Job.status == JobStatus.scheduled, Job.run_at.is_(None), Job.run_date < now.date()).all()
            for j in stale:
                j.status = JobStatus.failed
                j.last_error = "Missed: its day passed without the phone running it"
                s.add(JobLog(job_id=j.id, level="error", message=j.last_error))
            if stale:
                log.warning("%d planned job(s) from earlier days marked missed", len(stale))

    async def tick(self) -> None:
        if not settings_mod.as_bool(settings_mod.get("runner_enabled", "true")):
            return
        now = datetime.now()
        self._expire_missed(now)
        with session_scope() as s:
            devices = s.query(Device).filter(Device.paused.is_(False)).all()
            wake = [d.id for d in devices if d.id not in self.busy and due_jobs(s, d, now)]
            auto_album = settings_mod.as_bool(settings_mod.get("queue_auto", "false"))
            prep = [
                d.id for d in devices
                if auto_album and d.id not in self.busy and d.kind != "sim" and self._prepare_window(d, now)
                and self._prepared_on.get(d.id) != now.date()
            ]
        for did in prep:
            self._prepared_on[did] = now.date()
            asyncio.create_task(asyncio.to_thread(self._prepare_device, did), name=f"prepare-{did}")
        for did in wake:
            self._spawn_worker(did)
        from . import waketest
        for did in waketest.due(now):
            if did not in self.busy:
                asyncio.create_task(asyncio.to_thread(waketest.run_step, self.manager, did), name=f"waketest-{did}")

    @staticmethod
    def _prepare_window(device: Device, now: datetime) -> bool:
        """True from `prepare_lead_min` minutes before the routine until the routine starts."""
        with session_scope() as s2:
            routine = day_start(s2, s2.get(Device, device.id) or device, now.date())
        lead = timedelta(minutes=max(1, device.prepare_lead_min or 60))
        return routine - lead <= now < routine

    def _prepare_device(self, device_id: str) -> None:
        from . import queue as queue_mod
        lock = self.manager.op_lock(device_id)
        if not lock.acquire(timeout=1):
            return
        try:
            drv = self.manager.driver(device_id)
            if drv is not None and hasattr(drv, "wake"):
                drv.wake()  # first thing in the morning: wake and unlock, whatever else is queued
            res = queue_mod.prepare(self.manager, device_id)
            log.info("%s: queue prepare: %s", device_id, res.get("message"))
        except Exception:
            log.exception("queue prepare failed for %s", device_id)
        finally:
            lock.release()
            drv = self.manager.driver(device_id)
            if drv is not None and hasattr(drv, "sleep") and device_id not in self.busy and settings_mod.as_bool(settings_mod.get("sleep_after_queue", "false")):
                drv.sleep()

    def _spawn_worker(self, device_id: str) -> None:
        if device_id in self.busy:
            return
        self.busy.add(device_id)
        self._workers[device_id] = asyncio.create_task(self._device_worker(device_id), name=f"worker-{device_id}")

    def run_manual(self, device_id: str, job_id: int, then_continue: bool = False) -> bool:
        """Run one job right now in its own thread, even if the phone is paused (used for
        stage-by-stage testing). With `then_continue`, a finished job hands over to the normal
        queue so the rest of the day keeps going, paused or not, until Stop is pressed.
        False when the phone is already busy."""
        if device_id in self.busy:
            return False
        self.busy.add(device_id)
        self.current[device_id] = job_id

        def go():
            done = False
            try:
                self._execute(device_id, job_id)
                with session_scope() as s:
                    j = s.get(Job, job_id)
                    done = j is not None and j.status == JobStatus.done
            except Exception:
                log.exception("manual run failed")
            finally:
                self.busy.discard(device_id)
                self.current.pop(device_id, None)
            if then_continue and done:
                self.force.add(device_id)
                self.kick(device_id)
        import threading
        threading.Thread(target=go, name=f"manual-{job_id}", daemon=True).start()
        return True

    def kick(self, device_id: str) -> None:
        """Start a worker for this device right away (used by run-now). Safe to call from any thread."""
        loop = getattr(self, "_event_loop", None)
        if loop is None:
            return
        loop.call_soon_threadsafe(self._spawn_worker, device_id)

    # ---- per-device queue -----------------------------------------------
    async def _device_worker(self, device_id: str) -> None:
        try:
            first = True
            while not self._stopping:
                with session_scope() as s:
                    device = s.get(Device, device_id)
                    if device is None or (device.paused and device_id not in self.force):
                        return
                    if not settings_mod.as_bool(settings_mod.get("runner_enabled", "true")):
                        return
                    jobs = due_jobs(s, device, datetime.now())
                    if not jobs:
                        return
                    job_id = jobs[0].id
                    gap_min, gap_max = device.gap_min_s, device.gap_max_s
                if not first:
                    pace = float(settings_mod.get("pace_multiplier", "1.0") or 1.0)
                    gap = random.uniform(gap_min, gap_max) * pace
                    log.info("%s: waiting %.0fs before next action", device_id, gap)
                    await asyncio.sleep(gap)
                first = False
                await asyncio.to_thread(self._execute, device_id, job_id)
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("worker %s crashed", device_id)
        finally:
            self.busy.discard(device_id)
            self.current.pop(device_id, None)
            self._workers.pop(device_id, None)
            self.force.discard(device_id)
            # queue done: let the phone go back to sleep until the next routine (if wanted)
            drv = self.manager.driver(device_id)
            if drv is not None and hasattr(drv, "sleep") and settings_mod.as_bool(settings_mod.get("sleep_after_queue", "false")):
                try:
                    await asyncio.to_thread(drv.sleep)
                except Exception:
                    pass

    # ---- one job ---------------------------------------------------------
    def _execute(self, device_id: str, job_id: int) -> None:
        lock = self.manager.op_lock(device_id)
        with lock:
            self._execute_locked(device_id, job_id)

    def _execute_locked(self, device_id: str, job_id: int) -> None:
        drv = self.manager.driver(device_id)
        self.current[device_id] = job_id
        # stage plan: a manual request wins; otherwise continue after the last passed stage
        with session_scope() as s:
            job = s.get(Job, job_id)
            if job is None:
                return
            manual = job.run_mode
            stage_only = job.run_stage if manual == "only" else ""
            if manual == "from":
                stage_from = job.run_stage
            elif manual == "restart":
                stage_from = ""
            else:
                stage_from = _next_stage(job.last_stage)
            if manual == "restart" or (manual == "from" and job.run_stage == "open"):
                job.last_stage = ""
            job.run_stage, job.run_mode = "", ""
            from_start = not stage_only and not stage_from
        # the runner must be up before anything else: the album check reads the phone
        if drv is not None and not drv.is_connected():
            with session_scope() as s:
                s.add(JobLog(job_id=job_id, level="warn", message="Phone's runner isn't answering; restarting it"))
            try:
                drv.connect()
            except Exception as e:
                log.warning("%s: runner restart failed: %s", device_id, e)
        if drv is not None and hasattr(drv, "wake") and drv.is_connected():
            drv.wake()  # wake and unlock before anything touches the screen
        auto_album = settings_mod.as_bool(settings_mod.get("queue_auto", "false"))
        if auto_album and (from_start or stage_only == "open" or stage_from == "open"):
            self._ensure_queued(device_id, job_id)
        else:
            self._refresh_album_membership(device_id, job_id)

        stage_now = {"name": ""}

        def jlog(level: str, message: str) -> None:
            with session_scope() as s:
                if message.startswith("stage-start:"):
                    stage_now["name"] = message.split(":", 1)[1]
                    s.add(JobLog(job_id=job_id, level="info", message=f"▶ Stage: {stage_now['name']}"))
                    return
                if message.startswith("checkpoint:"):
                    name = message.split(":", 1)[1]
                    j = s.get(Job, job_id)
                    if j is not None:
                        j.last_stage = name
                        j.failed_stage = ""
                    s.add(JobLog(job_id=job_id, level="ok", message=f"✓ Stage {name} done"))
                    return
                s.add(JobLog(job_id=job_id, level=level, message=message))

        with session_scope() as s:
            job = s.query(Job).options(selectinload(Job.account), selectinload(Job.media)).get(job_id)
            if job is None or job.status not in (JobStatus.scheduled, JobStatus.failed, JobStatus.cancelled) and not manual:
                return
            if job is None:
                return
            job.status = JobStatus.running
            job.attempts += 1
            job.started_at = datetime.now()
            job.last_error = ""
            account: Account = job.account
            media: MediaItem | None = job.media
            device: Device = s.get(Device, device_id)
            album, album_index = None, None
            if media is not None and device is not None:
                titles = [t for t in (media.albums or "").split("|") if t]
                for cand in ((device.queue_album or "").strip(), (device.story_album or "").strip()):
                    if cand and cand in titles:
                        album = cand
                        album_index = _album_index(s, device_id, album, media)
                        break
            spec = ActionSpec(
                action=job.action.value,
                platform=account.platform.value,
                handle=account.handle,
                media_filename=media.filename if media else None,
                media_kind=media.kind if media else None,
                media_index=_media_index(s, device_id, media) if media else None,
                caption=job.caption,
                link=job.link,
                link_text=job.link_text,
                extra={
                    "created_at": media.created_at.isoformat() if media and media.created_at else None,
                    "duration_s": media.duration_s if media else None,
                    "filename": media.filename if media else None,
                    "roll_total": s.query(MediaItem).filter(MediaItem.device_id == device_id, MediaItem.present.is_(True)).count(),
                    "album": album,
                    "album_index": album_index,  # newest-first position (Instagram's album order)
                    "album_count": _album_count(s, device_id, album) if album else None,
                    "album_newest_s": _album_edge_durations(s, device_id, album)[0] if album else None,
                    "album_oldest_s": _album_edge_durations(s, device_id, album)[1] if album else None,
                    # oldest-first grids (TikTok): places before the newest item, 0 = newest
                    "album_from_end": album_index,
                    "dry_run": settings_mod.as_bool(settings_mod.get("dry_run", "false")),
                    "stage_only": stage_only or None,
                    "stage_from": stage_from or None,
                },
            )
            attempt = job.attempts
        how = f"stage '{stage_only}' only" if stage_only else (f"from stage '{stage_from}'" if stage_from else "all stages")
        jlog("info", f"Attempt {attempt}: {spec.action} as @{spec.handle} ({how})")

        if drv is None or not drv.is_connected():
            self._finish(job_id, ok=False, message="Phone offline", shot=None, attempt=attempt)
            return
        if media is not None and not media.present:
            self._finish(job_id, ok=False, message=f"{media.filename} is no longer in the camera roll", shot=None, attempt=attempt)
            return
        try:
            result = drv.run_action(spec, jlog)
        except Exception as e:
            log.exception("driver error")
            shot = None
            try:
                shot = drv.screenshot()
            except Exception:
                pass
            self._finish(job_id, ok=False, message=f"Driver error: {e}", shot=shot, attempt=attempt, stage=stage_now["name"], manual=bool(manual))
            return
        self._finish(job_id, ok=result.ok, message=result.message, shot=result.screenshot_png, attempt=attempt,
                     stage=stage_now["name"], partial=result.partial, manual=bool(manual))
        if result.ok and not result.partial and auto_album:
            self._release_after_post(device_id, job_id)

    def _refresh_album_membership(self, device_id: str, job_id: int) -> None:
        """You put items into the album on the phone yourself; make sure the app's copy of the
        album lists is fresh before it decides where to look for this job's media."""
        with session_scope() as s:
            job = s.query(Job).options(selectinload(Job.media)).get(job_id)
            dev = s.get(Device, device_id)
            if job is None or dev is None or job.media is None or dev.kind == "sim":
                return
            titles = [t for t in (job.media.albums or "").split("|") if t]
            wanted = [a.strip() for a in (dev.queue_album, dev.story_album) if a and a.strip()]
            if any(a in titles for a in wanted):
                return
        drv = self.manager.driver(device_id)
        if drv is None or not drv.is_connected():
            return
        jl_msg = f"Checking the phone's albums for {job.media.filename.split('/')[-1]}"
        with session_scope() as s:
            s.add(JobLog(job_id=job_id, level="info", message=jl_msg))
        try:
            self.manager.sync_media(device_id)
        except Exception as e:
            log.warning("album refresh failed: %s", e)

    def _ensure_queued(self, device_id: str, job_id: int) -> None:
        """Safety net: if this job's media isn't in the queue album yet, add it now."""
        from . import queue as queue_mod
        with session_scope() as s:
            job = s.query(Job).options(selectinload(Job.media)).get(job_id)
            dev = s.get(Device, device_id)
            if job is None or dev is None or job.media is None or not dev.queue_album or dev.kind == "sim":
                return
            album = dev.queue_album.strip()
            titles = [t for t in (job.media.albums or "").split("|") if t]
            if album in titles:
                return
            item = queue_mod._qitem(job.media)
        drv = self.manager.driver(device_id)
        if drv is None or not drv.is_connected():
            return
        jl = queue_mod.job_log(job_id)
        jl("info", f"Media not in '{album}' yet; adding it first")
        try:
            added = drv.add_to_album([item], album, jl)
        except Exception as e:
            jl("warn", f"Could not add to '{album}': {e}. Falling back to Recents.")
            return
        if added:
            with session_scope() as s:
                m = s.get(MediaItem, item.media_id)
                if m is not None:
                    m.albums = "|".join([t for t in (m.albums or "").split("|") if t] + [album])

    def _release_after_post(self, device_id: str, job_id: int) -> None:
        from . import queue as queue_mod
        with session_scope() as s:
            job = s.get(Job, job_id)
            media_id = job.media_id if job else None
        if media_id is None:
            return
        try:
            queue_mod.release(self.manager, device_id, media_id, queue_mod.job_log(job_id))
        except Exception:
            log.exception("queue release failed")

    def _finish(self, job_id: int, ok: bool, message: str, shot: bytes | None, attempt: int,
                stage: str = "", partial: bool = False, manual: bool = False) -> None:
        max_attempts = int(settings_mod.get("max_attempts", "2") or 2)
        shot_path = ""
        if shot:
            p = SHOT_DIR / f"job_{job_id}_a{attempt}.png"
            p.write_bytes(shot)
            shot_path = str(p)
        with session_scope() as s:
            job = s.get(Job, job_id)
            if job is None:
                return
            job.finished_at = datetime.now()
            if shot_path:
                job.screenshot_path = shot_path
            if ok and partial:
                # one stage ran by hand; the job stays in the queue, checkpoint kept
                job.status = JobStatus.scheduled
                job.attempts = max(0, job.attempts - 1)
                s.add(JobLog(job_id=job_id, level="ok", message=message))
            elif ok:
                job.status = JobStatus.done
                job.last_stage, job.failed_stage = "", ""
                s.add(JobLog(job_id=job_id, level="ok", message=message or "Done"))
            elif manual or message == "Stopped by hand":
                job.status = JobStatus.failed
                job.last_error = message
                job.failed_stage = stage
                s.add(JobLog(job_id=job_id, level="error", message=f"{message} (stage: {stage or '?'})"))
            else:
                job.last_error = message
                job.failed_stage = stage
                if job.attempts < max_attempts:
                    pace = float(settings_mod.get("pace_multiplier", "1.0") or 1.0)
                    job.status = JobStatus.scheduled
                    job.run_at = datetime.now() + timedelta(seconds=180 * pace)
                    s.add(JobLog(job_id=job_id, level="error", message=f"{message}. Will retry."))
                else:
                    job.status = JobStatus.failed
                    s.add(JobLog(job_id=job_id, level="error", message=f"{message}. Giving up after {job.attempts} attempts."))


_STAGES = ("open", "pick", "compose", "post")


def _next_stage(last: str) -> str:
    """Stage to continue from after `last` passed ('' = start over)."""
    if not last or last not in _STAGES or last == _STAGES[-1]:
        return ""
    return _STAGES[_STAGES.index(last) + 1]


def _album_index(s: Session, device_id: str, album: str, media: MediaItem) -> int | None:
    """0-based position of this file inside a Photos album as Instagram shows it (capture date, newest first)."""
    rows = (
        s.query(MediaItem.id, MediaItem.albums)
        .filter(MediaItem.device_id == device_id, MediaItem.present.is_(True))
        .order_by(MediaItem.created_at.desc(), MediaItem.id.desc())
        .all()
    )
    i = 0
    for mid, albums in rows:
        if album in [t for t in (albums or "").split("|") if t]:
            if mid == media.id:
                return i
            i += 1
    return None


def _album_edge_durations(s: Session, device_id: str, album: str) -> tuple[float | None, float | None]:
    """(newest item's length, oldest item's length) in an album; used to tell which way a grid runs."""
    rows = (
        s.query(MediaItem.albums, MediaItem.duration_s)
        .filter(MediaItem.device_id == device_id, MediaItem.present.is_(True))
        .order_by(MediaItem.created_at.desc(), MediaItem.id.desc())
        .all()
    )
    members = [d for albums, d in rows if album in [t for t in (albums or "").split("|") if t]]
    if not members:
        return None, None
    return members[0], members[-1]


def _album_count(s: Session, device_id: str, album: str) -> int:
    rows = s.query(MediaItem.albums).filter(MediaItem.device_id == device_id, MediaItem.present.is_(True)).all()
    return sum(1 for (albums,) in rows if album in [t for t in (albums or "").split("|") if t])


def _media_index(s: Session, device_id: str, media: MediaItem) -> int | None:
    """0-based position of this file in the phone's Recents grid (newest first)."""
    from sqlalchemy import func
    rows = (
        s.query(MediaItem.id)
        .filter(MediaItem.device_id == device_id, MediaItem.present.is_(True))
        .order_by(func.coalesce(MediaItem.added_at, MediaItem.created_at).desc(), MediaItem.id.desc())
        .all()
    )
    for i, (mid,) in enumerate(rows):
        if mid == media.id:
            return i
    return None


__all__ = ["Runner", "due_jobs", "next_run_for", "date"]
