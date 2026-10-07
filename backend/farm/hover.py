"""Silent hover previews: a tiny muted copy of each album video (320 px wide, 15 fps, ≤20 s,
~150–300 KB) kept on the Mac so the roll can play it the moment the mouse is over a tile.

Made from the same pull as the thumbnail for new clips; a background warmer backfills the rest
while a phone is on the cable and idle (it uses the file connection, not the runner)."""

from __future__ import annotations

import hashlib
import logging
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from sqlalchemy import or_

from .config import DATA_DIR
from .db import session_scope
from .models import Device, MediaItem

log = logging.getLogger("farm.hover")

HOVER_DIR = DATA_DIR / "hover"
PREVIEW_DIR = DATA_DIR / "previews"  # the 720p click-to-watch copies; a free local source
SKIP = "skip"  # hover_path value for a clip that can't be done (too big, unreadable)
MAX_SECONDS = 20


def hover_path_for(device_id: str, filename: str) -> Path:
    key = hashlib.sha1(f"{device_id}:{filename}".encode()).hexdigest()[:16]
    return HOVER_DIR / device_id / f"{key}.mp4"


def has_hover(row: MediaItem) -> bool:
    return bool(row.hover_path) and row.hover_path != SKIP


def make_hover(src: Path, out: Path) -> bool:
    """Transcode `src` (any video the Mac can read) into the small silent clip at `out`."""
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".part.mp4")
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(src), "-t", str(MAX_SECONDS),
           "-vf", "scale=320:-2,fps=15", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
           "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-f", "mp4", str(tmp)]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=300)
    except Exception as e:
        log.warning("hover %s: %s", src.name, e)
        return False
    if r.returncode != 0 or not tmp.exists():
        log.warning("hover %s failed: %s", src.name, r.stderr.decode(errors="ignore")[-200:])
        tmp.unlink(missing_ok=True)
        return False
    tmp.rename(out)
    return True


def _album_filter(dev: Device):
    names = [n for n in ((dev.queue_album or "").strip(), (dev.story_album or "").strip()) if n]
    return or_(*[MediaItem.albums.contains(n) for n in names]) if names else None


def wanted(row: MediaItem, dev: Device) -> bool:
    """Only album videos get a hover clip: the queue and story albums, never the whole roll."""
    if row.kind != "video":
        return False
    names = {(dev.queue_album or "").strip(), (dev.story_album or "").strip()} - {""}
    return any(t.strip() in names for t in (row.albums or "").split("|") if t)


def _set(row_id: int, value: str) -> None:
    with session_scope() as s:
        row = s.get(MediaItem, row_id)
        if row is not None:
            row.hover_path = value


def warm_from_local_previews() -> int:
    """Clips that already have a 720p preview on the Mac need no phone: make their hover clip now."""
    n = 0
    with session_scope() as s:
        devs = {d.id: d for d in s.query(Device).all()}
        rows = [(r.id, r.device_id, r.filename) for r in s.query(MediaItem).filter(MediaItem.kind == "video", MediaItem.hover_path == "").all()
                if r.device_id in devs and wanted(r, devs[r.device_id])]
    for row_id, device_id, filename in rows:
        src = PREVIEW_DIR / f"{row_id}.mp4"
        if not src.exists():
            continue
        out = hover_path_for(device_id, filename)
        if out.exists() or make_hover(src, out):
            _set(row_id, str(out))
            n += 1
    return n


def warm_device(manager, device_id: str, per_cycle: int = 6) -> int:
    """Pull up to `per_cycle` album videos from the (plugged-in, idle) phone and make their hover
    clips. Stops early the moment the phone gets busy."""
    drv = manager.driver(device_id)
    if drv is None or getattr(drv, "kind", "") != "ios" or not hasattr(drv, "pull_file"):
        return 0
    with session_scope() as s:
        dev = s.get(Device, device_id)
        if dev is None:
            return 0
        flt = _album_filter(dev)
        if flt is None:
            return 0
        rows = (s.query(MediaItem)
                .filter(MediaItem.device_id == device_id, MediaItem.present.is_(True), MediaItem.kind == "video", MediaItem.hover_path == "", flt)
                .order_by(MediaItem.id.desc()).all())
        todo = [(r.id, r.filename) for r in rows if wanted(r, dev)][:per_cycle]
    n = 0
    for row_id, filename in todo:
        if device_id in manager.busy_devices():
            break
        out = hover_path_for(device_id, filename)
        if out.exists():
            _set(row_id, str(out)); n += 1
            continue
        try:
            data = drv.pull_file(filename)
        except Exception as e:
            log.warning("%s: hover pull %s failed: %s", device_id, filename, e)
            data = None
        if not data:
            _set(row_id, SKIP)
            continue
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / Path(filename).name
            src.write_bytes(data)
            ok = make_hover(src, out)
        _set(row_id, str(out) if ok else SKIP)
        n += int(ok)
    return n


def start_warmer(manager, interval_s: float = 45.0) -> None:
    from .devices.ios import discover_ios_devices

    def loop():
        time.sleep(15)
        try:
            n = warm_from_local_previews()
            if n:
                log.info("hover clips made from cached previews: %d", n)
        except Exception:
            log.exception("hover warm-up from previews failed")
        while True:
            try:
                try:
                    usb = {d["udid"] for d in discover_ios_devices()}
                except Exception:
                    usb = set()
                busy = manager.busy_devices()
                for did in usb - busy:
                    n = warm_device(manager, did)
                    if n:
                        log.info("%s: %d hover clip(s) made", did, n)
            except Exception:
                log.exception("hover warmer")
            time.sleep(interval_s)

    threading.Thread(target=loop, name="hover-warmer", daemon=True).start()
