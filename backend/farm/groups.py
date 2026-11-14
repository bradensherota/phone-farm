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


def carry_group_stories(s: Session, account, today) -> int:
    """An account that is active again takes its copy of every every-account story planned from
    `today` on that hasn't posted yet (it was left out while it was paused or banned). Same rules
    as planning one: an every-phone story reaches Instagram accounts on any phone, an
    every-account one only the origin's own phone; a day the account already has a story on is
    left alone (one story a day). Returns how many stories were added."""
    from .models import ACTIONS, Account, Platform

    action = next((a for a, m in ACTIONS.items() if m["platform"] == account.platform and m["label"] == "Story"), None)
    if action is None:
        return 0
    from . import settings as settings_mod
    from .models import Action
    if action == Action.tt_story and not settings_mod.as_bool(settings_mod.get("tiktok_stories", "false")):
        return 0  # switched off in Settings: the scheduler would cancel them straight away
    origins = (
        s.query(Job)
        .filter(Job.group_key.like("allstories:%"), Job.run_date >= today, Job.account_id != account.id,
                Job.status.in_([JobStatus.scheduled, JobStatus.failed]))
        .order_by(Job.run_date, Job.id)
        .all()
    )
    added = 0
    for o in origins:
        if o.group_key != f"allstories:{o.account_id}:{o.run_date.isoformat()}":
            continue  # a copy, not the story the group was planned from
        same_phone = o.device_id == account.device_id
        if not same_phone and not (o.group_scope == "all" and account.platform == Platform.instagram):
            continue
        mine = s.query(Job).filter(Job.account_id == account.id, Job.run_date == o.run_date, Job.action == action).all()
        if any(j.status != JobStatus.cancelled for j in mine):
            continue
        twin = next((j for j in mine if j.group_key == o.group_key), None)  # its old cancelled copy, if any
        found = None
        if not same_phone and o.media_id is not None:
            media = s.get(MediaItem, o.media_id)
            found = find_twin_media(s, account.device_id, media) if media is not None else None
        new = twin is None
        if new:
            twin = Job(account_id=account.id, device_id=account.device_id, action=action)
        if same_phone:
            twin.media_id, twin.origin_media_id = o.media_id, None
        else:
            twin.origin_media_id = o.media_id
            twin.media_id = found.id if found is not None else None
        twin.group_key, twin.group_scope = o.group_key, o.group_scope
        twin.caption = o.caption
        twin.link = account.story_link or o.link
        twin.link_text = o.link_text
        twin.run_date, twin.run_at = o.run_date, None
        twin.sort_order = account.sort_order * 10 + ACTIONS[action]["rank"]
        twin.status, twin.attempts, twin.last_error = JobStatus.scheduled, 0, ""
        if new:
            s.add(twin)  # only once it is complete: a query in this loop may flush the session
        added += 1
    return added

