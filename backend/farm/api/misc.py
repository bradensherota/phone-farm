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
from ..state import manager, runner
from ..config import DATA_DIR
import subprocess, tempfile

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


PREVIEW_DIR = DATA_DIR / "previews"
PREVIEW_DIR.mkdir(parents=True, exist_ok=True)


@router.get("/media/{media_id}/preview")
def preview(media_id: int, s: Session = Depends(get_session)):
    """Browser-playable copy of a roll item: H.264 720p mp4 for video, JPEG for photos. Cached."""
    m = s.get(MediaItem, media_id)
    if m is None:
        raise HTTPException(404, "no such media")
    ext = ".mp4" if m.kind == "video" else ".jpg"
    out = PREVIEW_DIR / f"{media_id}{ext}"
    if not out.exists():
        drv = manager.driver(m.device_id)
        if drv is None:
            raise HTTPException(404, "phone not connected")
        data = drv.pull_file(m.filename)
        if not data:
            raise HTTPException(404, "could not read the file from the phone")
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / Path(m.filename).name
            src.write_bytes(data)
            tmp = out.with_suffix(out.suffix + ".part")
            if m.kind == "video":
                cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vf", "scale=-2:'min(720,ih)'", "-c:v", "libx264", "-preset", "veryfast",
                       "-crf", "26", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", "-f", "mp4", str(tmp)]
            else:
                cmd = ["sips", "-s", "format", "jpeg", "-Z", "1600", str(src), "--out", str(tmp)]
            r = subprocess.run(cmd, capture_output=True, timeout=600)
            if r.returncode != 0 or not tmp.exists():
                raise HTTPException(500, f"preview conversion failed: {r.stderr.decode(errors='ignore')[-200:]}")
            tmp.rename(out)
    return FileResponse(out, media_type="video/mp4" if m.kind == "video" else "image/jpeg", headers={"Cache-Control": "max-age=86400"})
