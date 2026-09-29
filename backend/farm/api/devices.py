from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from ..db import get_session
from ..models import Device, Job, JobStatus, MediaItem
from ..schemas import DeviceOut, DevicePatch, MediaOut, TodaySummary
from ..state import manager, runner

router = APIRouter(prefix="/devices", tags=["devices"])


def _today_summary(s: Session, device_id: str) -> TodaySummary:
    rows = (
        s.query(Job.status, func.count(Job.id))
        .filter(Job.device_id == device_id, Job.run_date == date.today())
        .group_by(Job.status)
        .all()
    )
    out = TodaySummary()
    for status, n in rows:
        key = status.value if hasattr(status, "value") else str(status)
        if hasattr(out, key):
            setattr(out, key, n)
        out.total += n
    return out


def device_out(s: Session, d: Device) -> DeviceOut:
    info = manager.status(d.id)
    media_count = s.query(func.count(MediaItem.id)).filter(MediaItem.device_id == d.id, MediaItem.present.is_(True)).scalar()
    out = DeviceOut.model_validate(d)
    out.connected = bool(info.get("connected"))
    out.has_passcode = bool(d.passcode)
    out.connecting = bool(info.get("connecting"))
    out.error = str(info.get("error") or "")
    out.busy = d.id in runner.busy
    out.current_job_id = runner.current.get(d.id)
    out.media_count = media_count or 0
    out.today = _today_summary(s, d.id)
    return out


@router.get("", response_model=list[DeviceOut])
def list_devices(s: Session = Depends(get_session)):
    devices = s.query(Device).options(selectinload(Device.accounts)).order_by(Device.sort_order).all()
    return [device_out(s, d) for d in devices]


@router.post("/discover", response_model=list[DeviceOut])
def discover(s: Session = Depends(get_session)):
    """Look for iPhones plugged in over USB, register new ones, and start their runners."""
    manager.load(connect=True)
    return list_devices(s)


@router.post("/{device_id}/connect", response_model=DeviceOut)
def connect_device(device_id: str, s: Session = Depends(get_session)):
    d = s.get(Device, device_id)
    if d is None:
        raise HTTPException(404, "device not found")
    if manager.driver(device_id) is None:
        manager.load(connect=False)
    manager.last_error.pop(device_id, None)
    manager.connect_async(device_id)
    return device_out(s, d)


@router.post("/{device_id}/run-day")
def run_day(device_id: str, run_date: date, s: Session = Depends(get_session)):
    """Start this phone's whole queue for `run_date` now: every job that hasn't posted yet
    (scheduled, failed or cancelled) is re-armed in order and the worker is kicked."""
    if s.get(Device, device_id) is None:
        raise HTTPException(404, "device not found")
    from datetime import datetime
    jobs = (
        s.query(Job)
        .filter(Job.device_id == device_id, Job.run_date == run_date,
                Job.status.in_([JobStatus.scheduled, JobStatus.failed, JobStatus.cancelled]))
        .order_by(Job.sort_order, Job.id)
        .all()
    )
    now = datetime.now()
    for j in jobs:
        j.status = JobStatus.scheduled
        j.attempts = 0
        j.last_error = ""
        j.run_at = now
    s.commit()
    runner.kick(device_id)
    return {"started": len(jobs)}


@router.post("/{device_id}/stop")
def stop_device(device_id: str, s: Session = Depends(get_session)):
    """Stop button: pause the phone so nothing else starts, and end the running flow at its
    next step. The phone is left on whatever screen it was on."""
    d = s.get(Device, device_id)
    if d is None:
        raise HTTPException(404, "device not found")
    d.paused = True
    s.commit()
    runner.force.discard(device_id)
    drv = manager.driver(device_id)
    if drv is not None and hasattr(drv, "abort"):
        drv.abort()
    return {"stopped": True, "was_busy": device_id in runner.busy}


@router.post("/{device_id}/wake")
def wake_device(device_id: str):
    drv = manager.driver(device_id)
    if drv is None or not hasattr(drv, "wake"):
        raise HTTPException(404, "no runner for this phone")
    drv.wake()
    return {"locked": bool(getattr(drv, "is_locked", lambda: False)())}


@router.post("/{device_id}/sleep")
def sleep_device(device_id: str):
    drv = manager.driver(device_id)
    if drv is None or not hasattr(drv, "sleep"):
        raise HTTPException(404, "no runner for this phone")
    err = drv.sleep() or ""
    locked = bool(getattr(drv, "is_locked", lambda: False)())
    return {"locked": locked, "error": "" if locked else (err or "screen did not lock")}


@router.get("/{device_id}/wake-test")
def get_wake_test(device_id: str):
    from .. import waketest
    return waketest.get(device_id) or {"device_id": device_id, "status": "none", "steps": waketest.DEFAULT_STEPS, "index": 0, "events": [], "next_at": None}


@router.post("/{device_id}/wake-test")
def start_wake_test(device_id: str, body: dict | None = None):
    """Lock the phone now, then wake+lock it after each interval in `steps` (minutes)."""
    from .. import waketest
    steps = (body or {}).get("steps") or waketest.DEFAULT_STEPS
    try:
        return waketest.start(manager, device_id, steps)
    except RuntimeError as e:
        raise HTTPException(409, str(e))


@router.delete("/{device_id}/wake-test")
def cancel_wake_test(device_id: str):
    from .. import waketest
    return waketest.cancel(device_id) or {"status": "none"}


@router.post("/{device_id}/prepare")
def prepare_queue(device_id: str, days: int = 7, s: Session = Depends(get_session)):
    """Put every item scheduled in the next `days` days into the phone's queue album now."""
    from .. import queue as queue_mod
    if s.get(Device, device_id) is None:
        raise HTTPException(404, "device not found")
    lock = manager.op_lock(device_id)
    if not lock.acquire(timeout=2):
        raise HTTPException(409, "phone is busy (posting or preparing)")
    runner.busy.add(device_id)
    try:
        return queue_mod.prepare(manager, device_id, days)
    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        runner.busy.discard(device_id)
        lock.release()


@router.post("/{device_id}/queue/release")
def release_queue_item(device_id: str, media_id: int, s: Session = Depends(get_session)):
    """Take one item out of the queue album (manual cleanup)."""
    from .. import queue as queue_mod
    if s.get(Device, device_id) is None:
        raise HTTPException(404, "device not found")
    lock = manager.op_lock(device_id)
    if not lock.acquire(timeout=2):
        raise HTTPException(409, "phone is busy (posting or preparing)")
    try:
        return {"removed": queue_mod.release(manager, device_id, media_id)}
    finally:
        lock.release()


@router.post("/{device_id}/queue/clear")
def clear_queue(device_id: str, s: Session = Depends(get_session)):
    """Empty the queue album on the phone (Select All, Remove from Album). Media is never deleted."""
    from .. import queue as queue_mod
    if s.get(Device, device_id) is None:
        raise HTTPException(404, "device not found")
    lock = manager.op_lock(device_id)
    if not lock.acquire(timeout=2):
        raise HTTPException(409, "phone is busy (posting or preparing)")
    runner.busy.add(device_id)
    try:
        return {"cleared": queue_mod.clear(manager, device_id)}
    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        runner.busy.discard(device_id)
        lock.release()


@router.delete("/{device_id}")
def delete_device(device_id: str, s: Session = Depends(get_session)):
    d = s.get(Device, device_id)
    if d is None:
        raise HTTPException(404, "device not found")
    if device_id in runner.busy:
        raise HTTPException(409, "phone is posting right now")
    manager.remove(device_id)
    s.delete(d)
    s.commit()
    return {"ok": True}


@router.get("/{device_id}", response_model=DeviceOut)
def get_device(device_id: str, s: Session = Depends(get_session)):
    d = s.get(Device, device_id)
    if d is None:
        raise HTTPException(404, "device not found")
    return device_out(s, d)


@router.patch("/{device_id}", response_model=DeviceOut)
def patch_device(device_id: str, body: DevicePatch, s: Session = Depends(get_session)):
    d = s.get(Device, device_id)
    if d is None:
        raise HTTPException(404, "device not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(d, k, v)
    s.commit()
    s.refresh(d)
    drv = manager.driver(device_id)
    if drv is not None and hasattr(drv, "passcode"):
        drv.passcode = d.passcode or ""
    return device_out(s, d)


@router.post("/{device_id}/sync", response_model=list[MediaOut])
def sync_device(device_id: str, albums_only: bool = False, s: Session = Depends(get_session)):
    if s.get(Device, device_id) is None:
        raise HTTPException(404, "device not found")
    manager.sync_media(device_id, albums_only=albums_only)
    return list_media(device_id, s)


@router.get("/{device_id}/media", response_model=list[MediaOut])
def list_media(device_id: str, s: Session = Depends(get_session)):
    rows = (
        s.query(MediaItem)
        .filter(MediaItem.device_id == device_id, MediaItem.present.is_(True))
        .order_by(func.coalesce(MediaItem.added_at, MediaItem.created_at).desc(), MediaItem.id.desc())
        .all()
    )
    out = []
    for i, m in enumerate(rows):
        mo = MediaOut.model_validate(m)
        mo.has_thumb = bool(m.thumb_path)
        mo.index = i
        out.append(mo)
    return out


@router.get("/{device_id}/screen.png")
def screen(device_id: str):
    drv = manager.driver(device_id)
    if drv is None:
        raise HTTPException(404, "device offline")
    # A locked phone has nothing to show; asking its runner for screenshots every second
    # while locked leaks connections on the phone until the runner dies. Don't ask.
    if hasattr(drv, "locked_recently") and drv.locked_recently() is True:
        return Response(status_code=204, headers={"Cache-Control": "no-store"})
    try:
        png = drv.screenshot()
    except Exception as e:
        raise HTTPException(500, f"screenshot failed: {e}")
    return Response(png, media_type="image/png", headers={"Cache-Control": "no-store"})
