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


import threading, hashlib
from ..config import THUMB_DIR
_thumb_locks: dict[int, threading.Lock] = {}
_thumb_locks_guard = threading.Lock()


@router.get("/media/{media_id}/thumb.jpg")
def thumb(media_id: int, s: Session = Depends(get_session)):
    """Thumbnail; made on demand (pulled from the phone) if the background sync hasn't got to it."""
    m = s.get(MediaItem, media_id)
    if m is None:
        raise HTTPException(404, "no such media")
    from ..paths import resolve
    f = resolve(m.thumb_path)
    if f is not None:
        return FileResponse(str(f), media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})
    with _thumb_locks_guard:
        lock = _thumb_locks.setdefault(media_id, threading.Lock())
    with lock:
        s.refresh(m)
        if resolve(m.thumb_path) is None:
            drv = manager.driver(m.device_id)
            if drv is None or (not drv.is_connected() and not manager.on_usb(m.device_id)):
                raise HTTPException(404, "phone not connected")
            out_dir = THUMB_DIR / m.device_id
            out_dir.mkdir(parents=True, exist_ok=True)
            key = hashlib.sha1(f"{m.device_id}:{m.filename}".encode()).hexdigest()[:16]
            out = out_dir / f"{key}.jpg"
            if not out.exists():
                data = drv.thumbnail(m.filename)
                if not data:
                    raise HTTPException(404, "could not make a thumbnail")
                out.write_bytes(data)
            m.thumb_path = str(out)
            meta = drv.media_meta(m.filename) if not m.probed else None
            if meta:
                if meta.get("duration_s"):
                    m.duration_s = float(meta["duration_s"])
                m.probed = True
            s.commit()
    return FileResponse(m.thumb_path, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})


@router.get("/settings")
def get_settings():
    return settings_mod.get_all()


@router.put("/settings")
def put_settings(body: dict[str, str]):
    return settings_mod.set_many(body)


@router.post("/settings/apply-link")
def apply_default_link(platform: str, s: Session = Depends(get_session)):
    """Set the platform's default story link on every account of that platform, and on their
    stories that haven't posted yet. One change, everywhere."""
    from ..models import Account, Job, JobStatus, Platform
    try:
        plat = Platform(platform)
    except ValueError:
        raise HTTPException(400, "platform must be instagram or tiktok")
    link = (settings_mod.get(f"default_link_{platform}", "") or "").strip()
    if not link:
        raise HTTPException(400, "set the default link first")
    accounts = s.query(Account).filter(Account.platform == plat).all()
    n_acc = n_jobs = 0
    for a in accounts:
        if (a.story_link or "") != link:
            a.story_link = link
            n_acc += 1
        for j in s.query(Job).filter(Job.account_id == a.id, Job.status.in_([JobStatus.scheduled, JobStatus.failed])).all():
            if j.action.value.endswith("_story") and j.link != link:
                j.link = link
                n_jobs += 1
    s.commit()
    return {"link": link, "accounts": n_acc, "pending_stories": n_jobs}


def _app_version() -> str:
    from ..config import ROOT
    try:
        v = (ROOT / "VERSION").read_text().strip()
        if v:
            return v
    except Exception:
        pass
    if (ROOT / ".git").exists():
        try:
            r = subprocess.run(["git", "-C", str(ROOT), "log", "-1", "--format=%cs %h"], capture_output=True, text=True, timeout=5)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
        except Exception:
            pass
    return "dev"


@router.post("/license/checkin")
def license_checkin_now():
    """Ask the license service right now (the 'Check again' button on the paused screen)."""
    from .. import license as license_mod
    from ..db import session_scope
    from ..models import Device
    with session_scope() as s:
        phones = s.query(Device).filter(Device.kind != "sim").count()
    ans = license_mod.checkin(phones, _app_version())
    return license_mod.status() if ans is not None else {**license_mod.status(), "error": "Could not reach the license service"}


@router.get("/update/check")
def update_check():
    """Is a newer version on GitHub? Compares this copy's commit with origin/main."""
    from ..config import ROOT
    if not (ROOT / ".git").exists():
        return {"installed_from_git": False, "local": _app_version(), "remote": "", "behind": False, "error": "This copy wasn't installed from GitHub"}
    try:
        local = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5).stdout.strip()
        r = subprocess.run(["git", "-C", str(ROOT), "ls-remote", "--heads", "origin", "main"], capture_output=True, text=True, timeout=20)
        if r.returncode != 0:
            return {"installed_from_git": True, "local": _app_version(), "remote": "", "behind": False, "error": (r.stderr.strip() or "could not reach GitHub")[-200:]}
        remote = (r.stdout.split() or [""])[0]
        return {"installed_from_git": True, "local": _app_version(), "remote": remote[:7], "behind": bool(remote) and remote != local, "error": ""}
    except Exception as e:
        return {"installed_from_git": True, "local": _app_version(), "remote": "", "behind": False, "error": str(e)[-200:]}


@router.post("/update")
def update_now():
    """Pull the latest from GitHub and reinstall. The server restarts itself as part of it."""
    from ..config import ROOT
    if not (ROOT / ".git").exists():
        raise HTTPException(400, "This copy wasn't installed from GitHub; install the new zip instead")
    if runner.busy:
        raise HTTPException(409, "A phone is posting; update when it's idle")
    log_path = DATA_DIR / "logs" / "update.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "ab") as logf:
        subprocess.Popen(["/bin/zsh", str(ROOT / "update.sh")], cwd=str(ROOT), stdout=logf, stderr=subprocess.STDOUT, start_new_session=True)
    return {"started": True, "log": str(log_path)}


@router.get("/runner", response_model=RunnerState)
def runner_state():
    return RunnerState(
        enabled=settings_mod.as_bool(settings_mod.get("runner_enabled", "true")),
        busy_devices=sorted(runner.busy),
        current=dict(runner.current),
        version=_app_version(),
        license=__import__("farm.license", fromlist=["status"]).status(),
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


@router.get("/media/{media_id}/hover.mp4")
def hover_clip(media_id: int, s: Session = Depends(get_session)):
    """The silent 320px hover clip, if it has been made (see farm/hover.py)."""
    from ..paths import resolve
    m = s.get(MediaItem, media_id)
    f = resolve(m.hover_path) if m is not None else None
    if f is None:
        raise HTTPException(404, "no hover clip yet")
    return FileResponse(str(f), media_type="video/mp4", headers={"Cache-Control": "max-age=604800"})


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
