from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session, selectinload

from ..db import get_session
from ..groups import NO_COPY_MSG, find_twin_media
from ..models import ACTIONS, Account, AccountStatus, Action, Device, Job, JobLog, JobStatus, MediaItem, Platform
from ..scheduler import next_run_for
from ..schemas import JobIn, JobLogOut, JobOut, MediaOut
from ..state import runner

router = APIRouter(prefix="/jobs", tags=["jobs"])

_LOAD = (selectinload(Job.account).selectinload(Account.device), selectinload(Job.media))


def job_out(j: Job, s: Session | None = None) -> JobOut:
    out = JobOut.model_validate(j)
    if j.media is not None:
        out.media = MediaOut.model_validate(j.media)
        out.media.has_thumb = bool(j.media.thumb_path)
        out.media.has_hover = bool(j.media.hover_path) and j.media.hover_path != "skip"
    from ..paths import resolve as _resolve
    out.has_screenshot = _resolve(j.screenshot_path) is not None
    if j.group_key.startswith("allstories:"):
        try:
            out.group_origin_account_id = int(j.group_key.split(":")[1])
        except (IndexError, ValueError):
            pass
        if out.group_origin_account_id is not None and out.group_origin_account_id != j.account_id and s is not None:
            origin = s.get(Account, out.group_origin_account_id)
            if origin is not None:
                out.group_origin_handle = origin.handle
                out.group_origin_device = origin.device.name if origin.device else ""
    if j.media is None and j.origin_media_id is not None and j.status == JobStatus.scheduled:
        out.last_error = NO_COPY_MSG
    if s is not None and j.status in (JobStatus.scheduled, JobStatus.failed):
        from ..blocks import lock_reason
        out.locked = lock_reason(s, j)
    out.handle = j.account.handle
    out.platform = j.account.platform.value
    out.device_name = j.account.device.name if j.account.device else ""
    out.next_run = next_run_for(j, j.account.device, s) if j.status == JobStatus.scheduled else None
    return out


@router.get("", response_model=list[JobOut])
def list_jobs(
    start: date | None = None,
    end: date | None = None,
    device_id: str | None = None,
    status: str | None = None,
    limit: int = Query(500, le=5000),
    s: Session = Depends(get_session),
):
    q = s.query(Job).options(*_LOAD)
    if start:
        q = q.filter(Job.run_date >= start)
    if end:
        q = q.filter(Job.run_date <= end)
    if device_id:
        q = q.filter(Job.device_id == device_id)
    if status:
        q = q.filter(Job.status == JobStatus(status))
    rows = q.order_by(Job.run_date, Job.device_id, Job.sort_order, Job.id).limit(limit).all()
    return [job_out(j, s) for j in rows]


@router.get("/activity", response_model=list[JobOut])
def activity(limit: int = Query(100, le=1000), s: Session = Depends(get_session)):
    rows = (
        s.query(Job).options(*_LOAD)
        .filter(Job.status.in_([JobStatus.running, JobStatus.done, JobStatus.failed]))
        .order_by(Job.updated_at.desc())
        .limit(limit)
        .all()
    )
    return [job_out(j, s) for j in rows]


@router.post("", response_model=list[JobOut])
def upsert_jobs(body: list[JobIn], s: Session = Depends(get_session)):
    """Create or update a batch of planned actions. Returns the saved rows."""
    saved: list[Job] = []
    for item in body:
        try:
            action = Action(item.action)
        except ValueError:
            raise HTTPException(400, f"unknown action {item.action}")
        account = s.get(Account, item.account_id)
        if account is None:
            raise HTTPException(404, f"account {item.account_id} not found")
        meta = ACTIONS[action]
        if meta["platform"] != account.platform:
            raise HTTPException(400, f"{action.value} is not valid for a {account.platform.value} account")
        media = None
        if item.media_id is not None:
            media = s.get(MediaItem, item.media_id)
            if media is None or media.device_id != account.device_id:
                raise HTTPException(400, "media must come from the same phone as the account")
            if meta["media"] == "video" and media.kind != "video":
                raise HTTPException(400, f"{meta['label']} needs a video")

        origin_posted = False  # the origin already posted; only its unposted copies take the edit
        if item.id is not None:
            job = s.get(Job, item.id)
            if job is None:
                raise HTTPException(404, f"job {item.id} not found")
            if job.status in (JobStatus.running, JobStatus.done):
                is_group_origin = meta["label"] == "Story" and item.all_accounts and job.group_key.startswith(f"allstories:{account.id}:")
                if not is_group_origin or job.status == JobStatus.running:
                    raise HTTPException(409, f"job {item.id} is {job.status.value} and can't be edited")
                origin_posted = True
        else:
            job = Job(account_id=account.id, device_id=account.device_id, action=action)
            s.add(job)

        if not origin_posted:
            job.account_id = account.id
            job.device_id = account.device_id
            job.media_id = media.id if media else None
            job.caption = item.caption
            job.link = item.link
            job.link_text = item.link_text
            job.run_date = item.run_date
            job.run_at = item.run_at
            job.sort_order = account.sort_order * 10 + meta["rank"]
            if job.status in (JobStatus.failed, JobStatus.cancelled):
                job.status = JobStatus.scheduled
                job.attempts = 0
                job.last_error = ""
        saved.append(job)
        # values the mirrored copies take (the edit, even when the origin itself already posted)
        m_media_id = media.id if media else None
        m_caption, m_link, m_link_text = item.caption, item.link, item.link_text
        # "post this story on every account on this phone" (and, with all_phones, on every
        # Instagram account of every other phone): mirror it to the other story-capable accounts
        if meta["label"] == "Story":
            key = f"allstories:{account.id}:{item.run_date.isoformat()}"
            all_phones = bool(item.all_phones) and bool(item.all_accounts)
            if item.all_accounts:
                job.group_key = key
                job.group_scope = "all" if all_phones else "phone"
                s.flush()
                others = (
                    s.query(Account)
                    .filter(Account.device_id == account.device_id, Account.id != account.id, Account.status == AccountStatus.active)
                    .all()
                )
                if all_phones:
                    others += (
                        s.query(Account)
                        .filter(Account.device_id != account.device_id, Account.status == AccountStatus.active,
                                Account.platform == Platform.instagram)
                        .all()
                    )
                else:
                    # scope narrowed back to this phone: drop the other phones' unposted copies
                    for twin in s.query(Job).filter(Job.group_key == key, Job.device_id != account.device_id,
                                                    Job.status.in_([JobStatus.scheduled, JobStatus.failed])).all():
                        s.delete(twin)
                for other in others:
                    o_action = next((a for a, m in ACTIONS.items() if m["platform"] == other.platform and m["label"] == "Story"), None)
                    if o_action is None:
                        continue
                    existing = (
                        s.query(Job)
                        .filter(Job.account_id == other.id, Job.run_date == item.run_date, Job.action == o_action)
                        .all()
                    )
                    # never post the same clip twice on one account
                    if any(e.media_id == m_media_id and e.status in (JobStatus.running, JobStatus.done) for e in existing):
                        continue
                    # reuse this group's own copy, else an unposted story of theirs; a story that already
                    # posted stays and this one is added alongside it
                    twin = next((e for e in existing if e.group_key == key), None) or \
                           next((e for e in existing if e.status not in (JobStatus.running, JobStatus.done)), None)
                    if twin is None:
                        twin = Job(account_id=other.id, device_id=other.device_id, action=o_action)
                        s.add(twin)
                    if other.device_id == account.device_id:
                        twin.media_id = m_media_id
                        twin.origin_media_id = None
                    else:
                        # another phone: its own copy of the clip, matched by capture time; may
                        # still be missing (AirDrop + Sync fills it in later)
                        twin.origin_media_id = m_media_id
                        found = find_twin_media(s, other.device_id, media) if media is not None else None
                        twin.media_id = found.id if found is not None else None
                    twin.group_scope = job.group_scope
                    twin.caption = m_caption
                    twin.link = other.story_link or m_link
                    twin.link_text = m_link_text
                    twin.run_date = item.run_date
                    twin.run_at = None
                    twin.sort_order = other.sort_order * 10 + ACTIONS[o_action]["rank"]
                    twin.group_key = key
                    if twin.status in (JobStatus.failed, JobStatus.cancelled):
                        twin.status = JobStatus.scheduled
                        twin.attempts = 0
                        twin.last_error = ""
                    saved.append(twin)
            elif job.group_key == key:
                # toggle turned off on the origin: drop the mirrored copies (keep the origin)
                for twin in s.query(Job).filter(Job.group_key == key, Job.id != job.id, Job.status == JobStatus.scheduled).all():
                    s.delete(twin)
                job.group_key = ""
    s.commit()
    ids = [j.id for j in saved]
    rows = s.query(Job).options(*_LOAD).filter(Job.id.in_(ids)).all()
    by_id = {j.id: j for j in rows}
    return [job_out(by_id[i], s) for i in ids]


@router.delete("/{job_id}")
def delete_job(job_id: int, s: Session = Depends(get_session)):
    j = s.get(Job, job_id)
    if j is None:
        raise HTTPException(404, "job not found")
    if j.status == JobStatus.running:
        raise HTTPException(409, "job is running")
    if j.group_key.startswith("allstories:") and j.group_key.split(":")[1] == str(j.account_id):
        for twin in s.query(Job).filter(Job.group_key == j.group_key, Job.id != j.id, Job.status == JobStatus.scheduled).all():
            s.delete(twin)
    s.delete(j)
    s.commit()
    return {"ok": True}


@router.post("/{job_id}/run-now", response_model=JobOut)
def run_now(job_id: int, s: Session = Depends(get_session)):
    j = s.query(Job).options(*_LOAD).get(job_id)
    if j is None:
        raise HTTPException(404, "job not found")
    if j.status == JobStatus.running:
        raise HTTPException(409, "already running")
    from ..license import allowed as _lic_allowed
    ok, why = _lic_allowed()
    if not ok:
        raise HTTPException(409, why)
    from ..blocks import lock_reason
    reason = lock_reason(s, j)
    if reason:
        raise HTTPException(409, reason)
    j.status = JobStatus.scheduled
    j.attempts = 0
    j.last_error = ""
    j.last_stage, j.failed_stage = "", ""
    j.run_stage, j.run_mode = "", "restart"
    j.run_at = datetime.now()
    s.add(JobLog(job_id=j.id, level="info", message="Run now requested"))
    s.commit()
    s.refresh(j)
    runner.kick(j.device_id)
    return job_out(j, s)


@router.post("/{job_id}/run-stage", response_model=JobOut)
def run_stage(job_id: int, stage: str, mode: str = "only", s: Session = Depends(get_session)):
    """Run one stage of this job now (`mode=only`), or this stage and everything after it
    (`mode=from`). Works while the phone is paused, so a day can be stepped through by hand."""
    from ..devices.ios_flows import STAGES
    if stage not in STAGES or mode not in ("only", "from"):
        raise HTTPException(400, f"stage must be one of {', '.join(STAGES)}; mode only|from")
    j = s.query(Job).options(*_LOAD).get(job_id)
    if j is None:
        raise HTTPException(404, "job not found")
    if j.status == JobStatus.running:
        raise HTTPException(409, "already running")
    from ..license import allowed as _lic_allowed
    ok, why = _lic_allowed()
    if not ok:
        raise HTTPException(409, why)
    from ..blocks import lock_reason
    reason = lock_reason(s, j)
    if reason:
        raise HTTPException(409, reason)
    j.run_stage, j.run_mode = stage, mode
    j.last_error = ""
    if j.status in (JobStatus.failed, JobStatus.cancelled, JobStatus.done):
        j.status = JobStatus.scheduled
    s.add(JobLog(job_id=j.id, level="info", message=f"Manual run: stage '{stage}' ({'only this stage' if mode == 'only' else 'and everything after'})"))
    if mode == "from":
        # "and everything after" means the rest of the day: every job of the day that failed or
        # was stopped is re-armed (earlier ones too), so nothing planned is left behind
        later = (
            s.query(Job)
            .filter(Job.device_id == j.device_id, Job.run_date == j.run_date, Job.id != j.id,
                    Job.status.in_([JobStatus.failed, JobStatus.cancelled]))
            .all()
        )
        for k in later:
            k.status = JobStatus.scheduled
            k.attempts = 0
            k.last_error = ""
            k.last_stage, k.failed_stage = "", ""
            k.run_at = datetime.now()
            s.add(JobLog(job_id=k.id, level="info", message=f"Re-armed by 'Continue from here' on job {j.id}"))
    s.commit()
    s.refresh(j)
    if not runner.run_manual(j.device_id, j.id, then_continue=(mode == "from")):
        raise HTTPException(409, "phone is busy with another job")
    return job_out(j, s)


@router.post("/{job_id}/cancel", response_model=JobOut)
def cancel(job_id: int, s: Session = Depends(get_session)):
    j = s.query(Job).options(*_LOAD).get(job_id)
    if j is None:
        raise HTTPException(404, "job not found")
    if j.status in (JobStatus.running, JobStatus.done):
        raise HTTPException(409, f"job is {j.status.value}")
    j.status = JobStatus.cancelled
    s.commit()
    s.refresh(j)
    return job_out(j, s)


@router.get("/{job_id}/logs", response_model=list[JobLogOut])
def logs(job_id: int, s: Session = Depends(get_session)):
    if s.get(Job, job_id) is None:
        raise HTTPException(404, "job not found")
    return s.query(JobLog).filter(JobLog.job_id == job_id).order_by(JobLog.id).all()


@router.get("/{job_id}/screenshot.png")
def screenshot(job_id: int, s: Session = Depends(get_session)):
    from ..paths import resolve as _resolve
    j = s.get(Job, job_id)
    f = _resolve(j.screenshot_path) if j is not None else None
    if f is None:
        raise HTTPException(404, "no screenshot")
    return FileResponse(str(f), media_type="image/png", headers={"Cache-Control": "no-store"})


__all__ = ["router", "Device"]
