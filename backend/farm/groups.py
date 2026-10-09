"""Stories posted on every phone: the same clip lives in each phone's roll under a different
filename, but an AirDropped copy keeps the capture time to the millisecond, so that's the key."""

from __future__ import annotations

from sqlalchemy.orm import Session

from .models import Device, Job, JobStatus, MediaItem

NO_COPY_MSG = "No copy of this clip on this phone yet: AirDrop it here, add it to the album, then Sync"


def find_twin_media(s: Session, device_id: str, origin: MediaItem) -> MediaItem | None:
    """The item on `device_id` that is the same clip as `origin` (same capture time, kind and
    length). Prefers a copy that is already in the phone's queue or story album."""
    if origin is None or origin.created_at is None:
        return None
    from datetime import timedelta
    lo, hi = origin.created_at - timedelta(seconds=1), origin.created_at + timedelta(seconds=1)
    cands = (
        s.query(MediaItem)
        .filter(MediaItem.device_id == device_id, MediaItem.present.is_(True),
                MediaItem.created_at >= lo, MediaItem.created_at <= hi, MediaItem.kind == origin.kind)
        .all()
    )
    want = float(origin.duration_s or 0)
    cands = [m for m in cands if abs(float(m.duration_s or 0) - want) < 0.6]
    if not cands:
        return None
    dev = s.get(Device, device_id)
    albums = {(dev.queue_album or "").strip(), (dev.story_album or "").strip()} - {""} if dev else set()

    def in_album(m: MediaItem) -> bool:
        return any(t.strip() in albums for t in (m.albums or "").split("|") if t)

    cands.sort(key=lambda m: (not in_album(m), m.id))
    return cands[0]


def resolve_pending(s: Session, device_id: str) -> int:
    """After a sync: fill in the media of this phone's story copies still waiting for their clip,
    and re-point any pending job whose clip isn't in the album at an identical copy that is.
    Returns how many jobs changed."""
    n = 0
    dev = s.get(Device, device_id)
    albums = {(dev.queue_album or "").strip(), (dev.story_album or "").strip()} - {""} if dev is not None else set()

    def in_album(m: MediaItem) -> bool:
        return any(t.strip() in albums for t in (m.albums or "").split("|") if t)

    rows = (
        s.query(Job)
        .filter(Job.device_id == device_id, Job.status.in_([JobStatus.scheduled, JobStatus.failed]))
        .all()
    )
    for job in rows:
        if job.media_id is None:
            if job.origin_media_id is None:
                continue
            origin = s.get(MediaItem, job.origin_media_id)
            twin = find_twin_media(s, device_id, origin) if origin is not None else None
            if twin is not None:
                job.media_id = twin.id
                if job.status == JobStatus.failed and job.last_error == NO_COPY_MSG:
                    job.status, job.attempts, job.last_error = JobStatus.scheduled, 0, ""
                n += 1
            continue
        cur = s.get(MediaItem, job.media_id)
        if cur is None or in_album(cur) or not albums:
            continue
        twin = find_twin_media(s, device_id, cur)
        if twin is not None and twin.id != cur.id and in_album(twin):
            job.media_id = twin.id
            n += 1
    return n
