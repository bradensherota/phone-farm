"""The queue album: an automation-owned Photos album on each phone that holds exactly the
items still to be posted, so every platform's gallery picker sees a short list.

prepare(): put every item referenced by upcoming scheduled jobs into the album.
release(): take a posted item out again.
Both drive the Photos app through the phone's runner, then re-read the library index.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from sqlalchemy.orm import selectinload

from .db import session_scope
from .devices.ios_photos import QueueItem
from .devices.registry import DeviceManager
from .models import Device, Job, JobLog, JobStatus, MediaItem

log = logging.getLogger("farm.queue")


def _titles(row: MediaItem) -> list[str]:
    return [t for t in (row.albums or "").split("|") if t]


def _qitem(m: MediaItem) -> QueueItem:
    return QueueItem(media_id=m.id, filename=m.filename, kind=m.kind, created_at=m.created_at, duration_s=m.duration_s or 0.0)


def pending_items(device_id: str, days: int = 7) -> tuple[str, list[QueueItem]]:
    """(album name, items scheduled in the next `days` days that aren't in the queue album yet)."""
    with session_scope() as s:
        dev = s.get(Device, device_id)
        if dev is None or not dev.queue_album:
            return "", []
        album = dev.queue_album.strip()
        horizon = date.today() + timedelta(days=days)
        jobs = (
            s.query(Job).options(selectinload(Job.media))
            .filter(Job.device_id == device_id, Job.status == JobStatus.scheduled, Job.run_date <= horizon, Job.media_id.isnot(None))
            .order_by(Job.run_date, Job.sort_order)
            .all()
        )
        seen: set[int] = set()
        out: list[QueueItem] = []
        for j in jobs:
            m = j.media
            if m is None or not m.present or m.id in seen:
                continue
            seen.add(m.id)
            if album in _titles(m):
                continue
            out.append(_qitem(m))
        return album, out


def prepare(manager: DeviceManager, device_id: str, days: int = 7, log_fn=None) -> dict:
    """Fill the queue album with upcoming scheduled items. Returns a summary dict."""
    log_fn = log_fn or (lambda level, msg: log.info("%s: %s", device_id, msg))
    album, items = pending_items(device_id, days)
    if not album:
        return {"album": "", "added": 0, "pending": 0, "message": "No queue album configured"}
    if not items:
        return {"album": album, "added": 0, "pending": 0, "message": "Queue album already has everything scheduled"}
    drv = manager.driver(device_id)
    if drv is None or not drv.is_connected():
        return {"album": album, "added": 0, "pending": len(items), "message": "Phone offline"}
    log_fn("info", f"Preparing '{album}': {len(items)} item(s) to add")
    added = drv.add_to_album(items, album, log_fn)
    # mirror the result locally right away, then confirm from the phone's index
    with session_scope() as s:
        for it in added:
            m = s.get(MediaItem, it.media_id)
            if m is not None and album not in _titles(m):
                m.albums = "|".join(_titles(m) + [album])
    try:
        manager.sync_media(device_id)
    except Exception as e:
        log.warning("sync after prepare failed: %s", e)
    missing = len(items) - len(added)
    msg = f"Added {len(added)} item(s) to '{album}'" + (f"; {missing} not found in Photos" if missing else "")
    log_fn("info" if not missing else "warn", msg)
    return {"album": album, "added": len(added), "pending": missing, "message": msg}


def _album_members_still_needed(s, device_id: str, album: str) -> int:
    """How many items in the album are still referenced by scheduled jobs on this phone."""
    rows = s.query(MediaItem).filter(MediaItem.device_id == device_id, MediaItem.present.is_(True)).all()
    member_ids = {m.id for m in rows if album in _titles(m)}
    if not member_ids:
        return 0
    return (
        s.query(Job.media_id).filter(Job.device_id == device_id, Job.status == JobStatus.scheduled, Job.media_id.in_(member_ids))
        .distinct().count()
    )


def clear(manager: DeviceManager, device_id: str, log_fn=None) -> bool:
    """Empty the queue album on the phone (Select All, Remove from Album). Media is never deleted."""
    log_fn = log_fn or (lambda level, msg: log.info("%s: %s", device_id, msg))
    with session_scope() as s:
        dev = s.get(Device, device_id)
        if dev is None or not dev.queue_album:
            return False
        album = dev.queue_album.strip()
    drv = manager.driver(device_id)
    if drv is None or not drv.is_connected():
        return False
    log_fn("info", f"Emptying '{album}'")
    try:
        ok = drv.clear_album(album, log_fn)
    except Exception as e:
        log_fn("warn", f"Could not empty '{album}': {e}")
        return False
    if ok:
        with session_scope() as s:
            for m in s.query(MediaItem).filter(MediaItem.device_id == device_id).all():
                if album in _titles(m):
                    m.albums = "|".join(t for t in _titles(m) if t != album)
    return ok


def release(manager: DeviceManager, device_id: str, media_id: int, log_fn=None) -> bool:
    """After a post: take the item out of the queue album unless another scheduled job still
    needs it. When nothing scheduled needs anything left in the album, empty it in one go."""
    log_fn = log_fn or (lambda level, msg: log.info("%s: %s", device_id, msg))
    with session_scope() as s:
        dev = s.get(Device, device_id)
        m = s.get(MediaItem, media_id)
        if dev is None or m is None or not dev.queue_album:
            return False
        album = dev.queue_album.strip()
        if album not in _titles(m):
            return False
        still_needed = (
            s.query(Job.id)
            .filter(Job.device_id == device_id, Job.media_id == media_id, Job.status == JobStatus.scheduled)
            .count()
        )
        if still_needed:
            log_fn("info", f"Keeping {m.filename} in '{album}': {still_needed} more scheduled post(s) use it")
            return False
        others_needed = _album_members_still_needed(s, device_id, album)
        item = _qitem(m)
    if others_needed == 0:
        return clear(manager, device_id, log_fn)
    drv = manager.driver(device_id)
    if drv is None or not drv.is_connected():
        return False
    log_fn("info", f"Removing {item.filename.split('/')[-1]} from '{album}'")
    try:
        ok = drv.remove_from_album(item, album, log_fn)
    except Exception as e:
        log_fn("warn", f"Could not remove from '{album}': {e}")
        return False
    if ok:
        with session_scope() as s:
            m = s.get(MediaItem, media_id)
            if m is not None:
                m.albums = "|".join(t for t in _titles(m) if t != album)
    return ok


def job_log(job_id: int):
    def fn(level: str, message: str) -> None:
        with session_scope() as s:
            s.add(JobLog(job_id=job_id, level=level, message=message))
    return fn
