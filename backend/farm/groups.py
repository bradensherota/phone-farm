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
    cands = (
        s.query(MediaItem)
        .filter(MediaItem.device_id == device_id, MediaItem.present.is_(True),
                MediaItem.created_at == origin.created_at, MediaItem.kind == origin.kind)
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
    """Fill in the media of this phone's story copies that are still waiting for the clip to
    arrive (after a sync). Returns how many were filled."""
    n = 0
    rows = (
        s.query(Job)
        .filter(Job.device_id == device_id, Job.media_id.is_(None), Job.origin_media_id.isnot(None),
                Job.status.in_([JobStatus.scheduled, JobStatus.failed]))
        .all()
    )
    for job in rows:
        origin = s.get(MediaItem, job.origin_media_id)
        twin = find_twin_media(s, device_id, origin) if origin is not None else None
        if twin is not None:
            job.media_id = twin.id
            if job.status == JobStatus.failed and job.last_error == NO_COPY_MSG:
                job.status, job.attempts, job.last_error = JobStatus.scheduled, 0, ""
            n += 1
    return n
