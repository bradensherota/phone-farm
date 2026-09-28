from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from .. import settings as settings_mod
from ..db import get_session
from ..models import ACTIONS, DaySchedule, Device, MediaItem
from datetime import date
from ..schemas import RunnerState
from ..state import runner

router = APIRouter(tags=["misc"])


@router.get("/media/{media_id}/thumb.jpg")
def thumb(media_id: int, s: Session = Depends(get_session)):
    m = s.get(MediaItem, media_id)
    if m is None or not m.thumb_path or not Path(m.thumb_path).exists():
        raise HTTPException(404, "no thumbnail")
    return FileResponse(m.thumb_path, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})


@router.get("/settings")
def get_settings():
    return settings_mod.get_all()


@router.put("/settings")
def put_settings(body: dict[str, str]):
    return settings_mod.set_many(body)


@router.get("/runner", response_model=RunnerState)
def runner_state():
    return RunnerState(
        enabled=settings_mod.as_bool(settings_mod.get("runner_enabled", "true")),
        busy_devices=sorted(runner.busy),
        current=dict(runner.current),
    )


@router.get("/actions")
def actions():
    return [
        {"action": a.value, "platform": m["platform"].value, "label": m["label"], "media": m["media"], "link": m["link"], "rank": m["rank"]}
        for a, m in ACTIONS.items()
    ]


@router.get("/schedule")
def get_schedule(start: date, end: date, s: Session = Depends(get_session)):
    rows = s.query(DaySchedule).filter(DaySchedule.run_date >= start, DaySchedule.run_date <= end).all()
    return [{"device_id": r.device_id, "run_date": r.run_date.isoformat(), "start": r.start} for r in rows]


@router.put("/schedule")
def put_schedule(body: dict, s: Session = Depends(get_session)):
    """{device_id, run_date, start}: start 'HH:MM' sets that day's queue start; '' clears it."""
    device_id, run_date, start = body.get("device_id"), date.fromisoformat(body["run_date"]), (body.get("start") or "").strip()
    if s.get(Device, device_id) is None:
        raise HTTPException(404, "device not found")
    row = s.query(DaySchedule).filter_by(device_id=device_id, run_date=run_date).first()
    if not start:
        if row:
            s.delete(row)
    else:
        if row is None:
            row = DaySchedule(device_id=device_id, run_date=run_date, start=start)
            s.add(row)
        row.start = start
    s.commit()
    return {"ok": True}
