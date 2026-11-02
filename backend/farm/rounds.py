"""Rounds: an account may have several reels (or trial reels) planned on one day, but they go out
one at a time. Each account+kind forms a line for the day (its unposted jobs in queue order); the
head of the line is what "Run day", "Start today" and the routine start fire. The rest stay planned
until the next press, or until their own set time. Stories are one a day, so their line is one long.
Pressing "Run day" three times on a day with three reels posts them one press at a time."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy.orm import Session

from .blocks import LABEL, kind_of, lock_reason
from .models import Job, JobLog, JobStatus


def line_heads(s: Session, device_id: str, day: date) -> dict[int, Job]:
    """job id -> job for the head of every account+kind line on this phone for `day`.
    A line holds the day's jobs of that kind that haven't posted (cancelled ones included: "Run day"
    re-arms them), in queue order; a running job is a head too, so nothing else of its kind starts."""
    rows = (
        s.query(Job)
        .filter(Job.device_id == device_id, Job.run_date == day, Job.status != JobStatus.done)
        .order_by(Job.sort_order, Job.id)
        .all()
    )
    heads: dict[int, Job] = {}
    seen: set[tuple[int, str]] = set()
    for j in rows:
        k = (j.account_id, kind_of(j.action))
        if k in seen:
            continue
        seen.add(k)
        heads[j.id] = j
    return heads


def first_in_line(s: Session, device_id: str, day: date) -> set[int]:
    """Ids of the first planned job of every account+kind line for `day`, whatever their status.
    The routine start fires only these (round one); once the first reel has posted, the second
    doesn't become automatic, it waits for "Run day" or a time of its own."""
    rows = (
        s.query(Job.id, Job.account_id, Job.action)
        .filter(Job.device_id == device_id, Job.run_date == day)
        .order_by(Job.sort_order, Job.id)
        .all()
    )
    out: set[int] = set()
    seen: set[tuple[int, str]] = set()
    for jid, aid, action in rows:
        k = (aid, kind_of(action))
        if k in seen:
            continue
        seen.add(k)
        out.add(jid)
    return out


def arm_round(s: Session, device_id: str, day: date, now: datetime, by: str) -> tuple[int, list[str]]:
    """Arm one round on this phone: the head of every account+kind line for `day` runs now.
    Returns (started, skipped reasons). Lines whose head is already running or waiting its turn
    are left alone, so a second press while round one is still going can't double up."""
    skipped: list[str] = []
    started = 0
    for j in line_heads(s, device_id, day).values():
        handle = j.account.handle if j.account is not None else "?"
        what = LABEL[kind_of(j.action)][:-1] if LABEL[kind_of(j.action)].endswith("s") else LABEL[kind_of(j.action)]
        if j.status == JobStatus.running:
            skipped.append(f"{what} on @{handle} is still running")
            continue
        if j.status == JobStatus.scheduled and j.run_at is not None and j.run_at <= now:
            skipped.append(f"{what} on @{handle} is already waiting its turn")
            continue
        reason = lock_reason(s, j)
        if reason:
            skipped.append(reason)
            continue
        j.status = JobStatus.scheduled
        j.attempts = 0
        j.last_error = ""
        j.last_stage, j.failed_stage = "", ""
        j.run_at = now
        s.add(JobLog(job_id=j.id, level="info", message=f"Started by '{by}'"))
        started += 1
    return started, skipped


def remaining_after(s: Session, device_id: str, day: date) -> int:
    """How many planned jobs of `day` still wait for a later round (not armed, not posted)."""
    heads = line_heads(s, device_id, day)
    rows = s.query(Job).filter(Job.device_id == device_id, Job.run_date == day, Job.status == JobStatus.scheduled).all()
    return sum(1 for j in rows if j.id not in heads and j.run_at is None)
