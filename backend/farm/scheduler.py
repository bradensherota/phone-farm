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
from .models import Account, Device, Job, JobLog, JobStatus, MediaItem

log = logging.getLogger("farm.runner")

TICK_SECONDS = 10


def _parse_hhmm(v: str) -> dtime:
    try:
        h, m = v.split(":")
        return dtime(int(h), int(m))
    except Exception:
        return dtime(7, 0)


def due_jobs(s: Session, device: Device, now: datetime) -> list[Job]:
    today = now.date()
    routine = datetime.combine(today, _parse_hhmm(device.routine_start))
    q = (
        s.query(Job)
        .options(selectinload(Job.account), selectinload(Job.media))
        .filter(Job.device_id == device.id, Job.status == JobStatus.scheduled)
        .filter(
            or_(
                Job.run_at.isnot(None) & (Job.run_at <= now),
                Job.run_at.is_(None) & (Job.run_date < today),
                Job.run_at.is_(None) & (Job.run_date == today) & (routine <= now),
            )
        )
        .order_by(Job.run_date, Job.sort_order, Job.id)
    )
    return q.all()


def next_run_for(job: Job, device: Device) -> datetime:
    if job.run_at:
        return job.run_at
    return datetime.combine(job.run_date, _parse_hhmm(device.routine_start))


class Runner:
    def __init__(self, manager: DeviceManager):
        self.manager = manager
        self.busy: set[str] = set()
        self.current: dict[str, int] = {}  # device_id -> job_id
        self._task: asyncio.Task | None = None
        self._workers: dict[str, asyncio.Task] = {}
        self._stopping = False

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

    async def tick(self) -> None:
        if not settings_mod.as_bool(settings_mod.get("runner_enabled", "true")):
            return
        now = datetime.now()
        with session_scope() as s:
            devices = s.query(Device).filter(Device.paused.is_(False)).all()
            wake = [d.id for d in devices if d.id not in self.busy and due_jobs(s, d, now)]
        for did in wake:
            self._spawn_worker(did)

    def _spawn_worker(self, device_id: str) -> None:
        if device_id in self.busy:
            return
        self.busy.add(device_id)
        self._workers[device_id] = asyncio.create_task(self._device_worker(device_id), name=f"worker-{device_id}")

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
                    if device is None or device.paused:
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

    # ---- one job ---------------------------------------------------------
    def _execute(self, device_id: str, job_id: int) -> None:
        drv = self.manager.driver(device_id)
        self.current[device_id] = job_id

        def jlog(level: str, message: str) -> None:
            with session_scope() as s:
                s.add(JobLog(job_id=job_id, level=level, message=message))

        with session_scope() as s:
            job = s.query(Job).options(selectinload(Job.account), selectinload(Job.media)).get(job_id)
            if job is None or job.status != JobStatus.scheduled:
                return
            job.status = JobStatus.running
            job.attempts += 1
            job.started_at = datetime.now()
            job.last_error = ""
            account: Account = job.account
            media: MediaItem | None = job.media
            device: Device = s.get(Device, device_id)
            album, album_index = None, None
            if media is not None and device is not None and device.story_album:
                titles = [t for t in (media.albums or "").split("|") if t]
                if device.story_album.strip() in titles:
                    album = device.story_album.strip()
                    album_index = _album_index(s, device_id, album, media)
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
                    "album_index": album_index,
                },
            )
            attempt = job.attempts
        jlog("info", f"Attempt {attempt}: {spec.action} as @{spec.handle}")

        if drv is not None and not drv.is_connected():
            jlog("warn", "Phone's runner isn't answering; restarting it")
            try:
                drv.connect()
            except Exception as e:
                self._finish(job_id, ok=False, message=f"Phone offline: {e}", shot=None, attempt=attempt)
                return
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
            self._finish(job_id, ok=False, message=f"Driver error: {e}", shot=shot, attempt=attempt)
            return
        self._finish(job_id, ok=result.ok, message=result.message, shot=result.screenshot_png, attempt=attempt)

    def _finish(self, job_id: int, ok: bool, message: str, shot: bytes | None, attempt: int) -> None:
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
            if ok:
                job.status = JobStatus.done
                s.add(JobLog(job_id=job_id, level="ok", message=message or "Done"))
            else:
                job.last_error = message
                if job.attempts < max_attempts:
                    pace = float(settings_mod.get("pace_multiplier", "1.0") or 1.0)
                    job.status = JobStatus.scheduled
                    job.run_at = datetime.now() + timedelta(seconds=180 * pace)
                    s.add(JobLog(job_id=job_id, level="error", message=f"{message}. Will retry."))
                else:
                    job.status = JobStatus.failed
                    s.add(JobLog(job_id=job_id, level="error", message=f"{message}. Giving up after {job.attempts} attempts."))


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
