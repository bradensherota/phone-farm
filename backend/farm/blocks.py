"""Blocks: a kind of post on an account only runs when that account has that kind planned on
`block_days` consecutive days that include the job's day. Plan four days of stories in a row and
those four days' stories unlock; a kind you don't plan isn't required. Days already run count.
The point is to make planning ahead the only way to use the automation."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from . import settings as settings_mod
from .models import Job, JobStatus

KIND = {"ig_reel": "reel", "ig_trial": "trial", "ig_story": "story", "tt_post": "reel", "tt_story": "story", "x_post": "reel"}
LABEL = {"reel": "reels", "trial": "trial reels", "story": "stories"}


def enabled() -> bool:
    return settings_mod.as_bool(settings_mod.get("require_blocks", "true"))


def block_days() -> int:
    try:
        return max(1, int(settings_mod.get("block_days", "4") or 4))
    except ValueError:
        return 4


def kind_of(action) -> str:
    a = action.value if hasattr(action, "value") else str(action)
    return KIND.get(a, "reel")


def planned_days(s: Session, account_id: int, kind: str) -> set[date]:
    rows = s.query(Job.run_date, Job.action).filter(Job.account_id == account_id, Job.status != JobStatus.cancelled).all()
    return {d for d, a in rows if kind_of(a) == kind}


def run_length(days: set[date], d: date) -> tuple[int, date, date]:
    """Length of the consecutive run of `days` that contains `d` (0 if `d` isn't in it), and its ends."""
    if d not in days:
        return 0, d, d
    lo = d
    while lo - timedelta(days=1) in days:
        lo -= timedelta(days=1)
    hi = d
    while hi + timedelta(days=1) in days:
        hi += timedelta(days=1)
    return (hi - lo).days + 1, lo, hi


def lock_reason(s: Session, job: Job) -> str:
    """'' when the job may run; otherwise why it can't yet."""
    if not enabled():
        return ""
    need = block_days()
    kind = kind_of(job.action)
    days = planned_days(s, job.account_id, kind) | {job.run_date}  # its own day counts even if it was cancelled
    n, lo, hi = run_length(days, job.run_date)
    if n >= need:
        return ""
    handle = job.account.handle if job.account is not None else "?"
    span = lo.strftime("%a %-d") if lo == hi else f"{lo.strftime('%a %-d')}–{hi.strftime('%a %-d')}"
    return f"Locked: {LABEL[kind]} on @{handle} need {need} days in a row; this run has {n} ({span}). Plan {need - n} more."
