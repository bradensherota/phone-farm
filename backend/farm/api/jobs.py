from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session, selectinload

from ..db import get_session
from ..models import ACTIONS, Account, Action, Device, Job, JobLog, JobStatus, MediaItem
from ..scheduler import next_run_for
from ..schemas import JobIn, JobLogOut, JobOut, MediaOut
from ..state import runner

router = APIRouter(prefix="/jobs", tags=["jobs"])

_LOAD = (selectinload(Job.account).selectinload(Account.device), selectinload(Job.media))


def job_out(j: Job) -> JobOut:
    out = JobOut.model_validate(j)
    if j.media is not None:
        out.media = MediaOut.model_validate(j.media)
        out.media.has_thumb = bool(j.media.thumb_path)
    out.has_screenshot = bool(j.screenshot_path) and Path(j.screenshot_path).exists()
    out.handle = j.account.handle
    out.platform = j.account.platform.value
    out.device_name = j.account.device.name if j.account.device else ""
    out.next_run = next_run_for(j, j.account.device) if j.status == JobStatus.scheduled else None
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
    return [job_out(j) for j in rows]


@router.get("/activity", response_model=list[JobOut])
def activity(limit: int = Query(100, le=1000), s: Session = Depends(get_session)):
    rows = (
        s.query(Job).options(*_LOAD)
        .filter(Job.status.in_([JobStatus.running, JobStatus.done, JobStatus.failed]))
        .order_by(Job.updated_at.desc())
        .limit(limit)
        .all()
    )
    return [job_out(j) for j in rows]


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

        if item.id is not None:
            job = s.get(Job, item.id)
            if job is None:
                raise HTTPException(404, f"job {item.id} not found")
            if job.status in (JobStatus.running, JobStatus.done):
                raise HTTPException(409, f"job {item.id} is {job.status.value} and can't be edited")
        else:
            job = Job(account_id=account.id, device_id=account.device_id, action=action)
            s.add(job)

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
    s.commit()
    ids = [j.id for j in saved]
    rows = s.query(Job).options(*_LOAD).filter(Job.id.in_(ids)).all()
    by_id = {j.id: j for j in rows}
    return [job_out(by_id[i]) for i in ids]


@router.delete("/{job_id}")
def delete_job(job_id: int, s: Session = Depends(get_session)):
    j = s.get(Job, job_id)
    if j is None:
        raise HTTPException(404, "job not found")
    if j.status == JobStatus.running:
        raise HTTPException(409, "job is running")
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
    j.status = JobStatus.scheduled
    j.attempts = 0
    j.last_error = ""
    j.run_at = datetime.now()
    s.add(JobLog(job_id=j.id, level="info", message="Run now requested"))
    s.commit()
    s.refresh(j)
    runner.kick(j.device_id)
    return job_out(j)


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
    return job_out(j)


@router.get("/{job_id}/logs", response_model=list[JobLogOut])
def logs(job_id: int, s: Session = Depends(get_session)):
    if s.get(Job, job_id) is None:
        raise HTTPException(404, "job not found")
    return s.query(JobLog).filter(JobLog.job_id == job_id).order_by(JobLog.id).all()


@router.get("/{job_id}/screenshot.png")
def screenshot(job_id: int, s: Session = Depends(get_session)):
    j = s.get(Job, job_id)
    if j is None or not j.screenshot_path or not Path(j.screenshot_path).exists():
        raise HTTPException(404, "no screenshot")
    return FileResponse(j.screenshot_path, media_type="image/png", headers={"Cache-Control": "no-store"})


__all__ = ["router", "Device"]
