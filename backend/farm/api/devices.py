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
    try:
        import json as _json
        out.album_sorts = _json.loads(d.album_sorts or "{}")
    except Exception:
        out.album_sorts = {}
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
    d.parked = False
    s.commit()
    manager.last_error.pop(device_id, None)
    manager.connect_async(device_id)
    return device_out(s, d)


@router.post("/{device_id}/rebuild-runner", response_model=DeviceOut)
def rebuild_runner(device_id: str, s: Session = Depends(get_session)):
    """Build and install the runner for this phone again (signature expired, new phone, new Mac).
    Runs in the background; the phone connects when it's done."""
    d = s.get(Device, device_id)
    if d is None:
        raise HTTPException(404, "device not found")
    if d.id in runner.busy:
        raise HTTPException(409, "the phone is busy; press Stop first")
    drv = manager.driver(device_id)
    if drv is None or not hasattr(drv, "rebuild_runner"):
        raise HTTPException(400, "not a real phone")
    import threading

    def work():
        try:
            manager.last_error[device_id] = "Building the runner… 2-5 minutes (phone plugged in and unlocked)"
            drv.disconnect()
            drv.rebuild_runner()
            manager.last_error.pop(device_id, None)
            manager.set_parked(device_id, False)
            manager.connect_async(device_id)
        except Exception as e:
            manager.last_error[device_id] = str(e)
    threading.Thread(target=work, name=f"rebuild-{device_id}", daemon=True).start()
    d.parked = False
    s.commit()
    return device_out(s, d)


@router.post("/{device_id}/disconnect", response_model=DeviceOut)
def disconnect_device(device_id: str, s: Session = Depends(get_session)):
    """Stop the phone's runner (clears the "Automation Running" overlay). Nothing starts it again
    until Connect is pressed or a job on this phone needs it."""
    d = s.get(Device, device_id)
    if d is None:
        raise HTTPException(404, "device not found")
    if d.id in runner.busy:
        raise HTTPException(409, "the phone is busy; press Stop first")
    s.expunge(d)
    manager.park(device_id)
    d = s.get(Device, device_id)
    return device_out(s, d)


@router.post("/start-today")
def start_today(s: Session = Depends(get_session)):
    """The morning button: every connected phone runs its whole queue for today, in order.
    Phones that are paused are resumed; phones that aren't connected are skipped."""
    from datetime import datetime
    today = date.today()
    now = datetime.now()
    out = []
    try:
        plugged = set(manager.discover())
    except Exception:
        plugged = set()
    for d in s.query(Device).filter(Device.kind != "sim").all():
        jobs = (
            s.query(Job)
            .filter(Job.device_id == d.id, Job.run_date == today, Job.status.in_([JobStatus.scheduled, JobStatus.failed]))
            .order_by(Job.sort_order, Job.id)
            .all()
        )
        drv = manager.driver(d.id)
        connected = drv is not None and drv.is_connected()
        if not jobs:
            out.append({"device_id": d.id, "name": d.name, "started": 0, "connected": connected, "note": "nothing planned today"})
            continue
        if not connected and d.parked and d.id in plugged:
            # disconnected by hand but plugged in: the first job connects it again
            connected = True
        if not connected:
            out.append({"device_id": d.id, "name": d.name, "started": 0, "connected": False, "note": "not connected; plug it in and unlock it, then press again"})
            continue
        d.paused = False
        for j in jobs:
            j.status = JobStatus.scheduled
            j.attempts = 0
            j.last_error = ""
            j.last_stage, j.failed_stage = "", ""
            j.run_at = now
            s.add(JobLog(job_id=j.id, level="info", message="Started by 'Start today'"))
        s.commit()
        runner.kick(d.id)
        out.append({"device_id": d.id, "name": d.name, "started": len(jobs), "connected": True, "note": ""})
    return out


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
    d = s.get(Device, device_id)
    if d is None:
        raise HTTPException(404, "device not found")
    drv = manager.driver(device_id)
    if d.parked and (drv is None or not drv.is_connected()):
        raise HTTPException(409, "The phone is disconnected; press Connect first")
    manager.sync_media(device_id, albums_only=albums_only, force=True)  # the button always reads fresh
    return list_media(device_id, s)


@router.get("/{device_id}/media", response_model=list[MediaOut])
def list_media(device_id: str, s: Session = Depends(get_session)):
    rows = (
        s.query(MediaItem)
        .filter(MediaItem.device_id == device_id, MediaItem.present.is_(True))
        .order_by(func.coalesce(MediaItem.added_at, MediaItem.created_at).desc(), MediaItem.id.desc())
        .all()
    )
    # what has already gone out with each item, so the picker can mark it
    from ..models import ACTIONS, JobStatus as _JS
    posted: dict[int, list] = {}
    done = (
        s.query(Job).options(selectinload(Job.account))
        .filter(Job.device_id == device_id, Job.status == _JS.done, Job.media_id.isnot(None))
        .order_by(Job.finished_at.desc())
        .all()
    )
    for j in done:
        posted.setdefault(j.media_id, []).append(j)
    planned: dict[int, list] = {}
    for j in (
        s.query(Job).options(selectinload(Job.account))
        .filter(Job.device_id == device_id, Job.status.in_([_JS.scheduled, _JS.running, _JS.failed]), Job.media_id.isnot(None))
        .order_by(Job.run_date.asc())
        .all()
    ):
        planned.setdefault(j.media_id, []).append(j)
    out = []
    for i, m in enumerate(rows):
        mo = MediaOut.model_validate(m)
        mo.has_thumb = bool(m.thumb_path)
        mo.has_hover = bool(m.hover_path) and m.hover_path != "skip"
        mo.index = i
        js = posted.get(m.id)
        if js:
            mo.posted_at = js[0].finished_at
            mo.posted_on = ", ".join(f"@{j.account.handle} ({ACTIONS[j.action]['label']})" for j in js if j.account)
        ps = planned.get(m.id)
        if ps:
            mo.scheduled_for = ps[0].run_date
            mo.scheduled_on = ", ".join(f"@{j.account.handle} ({ACTIONS[j.action]['label']}) {j.run_date.strftime('%b %-d')}" for j in ps if j.account)
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
