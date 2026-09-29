from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now()


class Platform(str, enum.Enum):
    instagram = "instagram"
    tiktok = "tiktok"
    x = "x"


class Action(str, enum.Enum):
    ig_reel = "ig_reel"
    ig_story = "ig_story"
    tt_post = "tt_post"
    tt_story = "tt_story"
    x_post = "x_post"


# What each action needs. `media`: "video" | "any". `link`: whether the flow adds a link sticker.
ACTIONS: dict[Action, dict] = {
    Action.ig_reel: {"platform": Platform.instagram, "label": "Reel", "media": "video", "link": False, "rank": 0},
    Action.ig_story: {"platform": Platform.instagram, "label": "Story", "media": "any", "link": True, "rank": 1},
    Action.tt_post: {"platform": Platform.tiktok, "label": "Post", "media": "video", "link": False, "rank": 0},
    Action.tt_story: {"platform": Platform.tiktok, "label": "Story", "media": "any", "link": True, "rank": 1},
    Action.x_post: {"platform": Platform.x, "label": "Post", "media": "any", "link": False, "rank": 0},
}


class AccountStatus(str, enum.Enum):
    active = "active"
    paused = "paused"
    banned = "banned"


class JobStatus(str, enum.Enum):
    scheduled = "scheduled"
    running = "running"
    done = "done"
    failed = "failed"
    cancelled = "cancelled"


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # udid, or "sim-..." for simulated
    name: Mapped[str] = mapped_column(String)
    model: Mapped[str] = mapped_column(String, default="")
    kind: Mapped[str] = mapped_column(String, default="sim")  # "sim" | "ios"
    routine_start: Mapped[str] = mapped_column(String, default="07:00")  # HH:MM local
    gap_min_s: Mapped[int] = mapped_column(Integer, default=90)
    gap_max_s: Mapped[int] = mapped_column(Integer, default=300)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    story_album: Mapped[str] = mapped_column(String, default="Stories")  # Photos album the flows pick from when the item is in it
    queue_album: Mapped[str] = mapped_column(String, default="Farm Queue")  # album the app fills with scheduled items and empties after posting
    prepare_lead_min: Mapped[int] = mapped_column(Integer, default=60)  # fill the queue album this many minutes before the routine
    passcode: Mapped[str] = mapped_column(String, default="")  # lock-screen passcode, typed by the runner to wake the phone (local DB only)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    accounts: Mapped[list[Account]] = relationship(
        back_populates="device", cascade="all, delete-orphan", order_by="Account.sort_order"
    )
    media: Mapped[list[MediaItem]] = relationship(back_populates="device", cascade="all, delete-orphan")


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    platform: Mapped[Platform] = mapped_column(Enum(Platform))
    handle: Mapped[str] = mapped_column(String)
    status: Mapped[AccountStatus] = mapped_column(Enum(AccountStatus), default=AccountStatus.active)
    story_link: Mapped[str] = mapped_column(String, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    device: Mapped[Device] = relationship(back_populates="accounts")
    jobs: Mapped[list[Job]] = relationship(back_populates="account", cascade="all, delete-orphan")


class MediaItem(Base):
    """A photo or video that exists in the phone's camera roll (synced from the device)."""

    __tablename__ = "media"
    __table_args__ = (UniqueConstraint("device_id", "filename", name="uq_media_device_file"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String)  # "photo" | "video"
    duration_s: Mapped[float] = mapped_column(Float, default=0.0)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)  # capture/added time on device
    thumb_path: Mapped[str] = mapped_column(String, default="")
    present: Mapped[bool] = mapped_column(Boolean, default=True)  # false once it disappears from the roll
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    probed: Mapped[bool] = mapped_column(Boolean, default=False)  # capture date/duration read from the file itself
    added_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # when it entered the library: the gallery's sort key
    albums: Mapped[str] = mapped_column(String, default="")  # '|'-joined user album titles this item is in

    device: Mapped[Device] = relationship(back_populates="media")


class Job(Base):
    """One planned action: a reel, a story, a post, for one account on one day."""

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    action: Mapped[Action] = mapped_column(Enum(Action))
    media_id: Mapped[int | None] = mapped_column(ForeignKey("media.id", ondelete="SET NULL"), nullable=True)
    caption: Mapped[str] = mapped_column(Text, default="")
    link: Mapped[str] = mapped_column(String, default="")
    link_text: Mapped[str] = mapped_column(String, default="")
    run_date: Mapped[date] = mapped_column(Date, index=True)
    run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # explicit time, else device routine
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.scheduled, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")
    screenshot_path: Mapped[str] = mapped_column(String, default="")
    group_key: Mapped[str] = mapped_column(String, default="")  # "allstories:<origin account id>:<date>" for fan-out stories
    last_stage: Mapped[str] = mapped_column(String, default="")  # last stage that passed (open/pick/compose/post)
    failed_stage: Mapped[str] = mapped_column(String, default="")  # stage that broke on the last attempt
    run_stage: Mapped[str] = mapped_column(String, default="")  # manual request: which stage
    run_mode: Mapped[str] = mapped_column(String, default="")  # manual request: only | from | restart
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    account: Mapped[Account] = relationship(back_populates="jobs")
    media: Mapped[MediaItem | None] = relationship()
    logs: Mapped[list[JobLog]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="JobLog.id"
    )


class JobLog(Base):
    __tablename__ = "job_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    level: Mapped[str] = mapped_column(String, default="info")
    message: Mapped[str] = mapped_column(Text)

    job: Mapped[Job] = relationship(back_populates="logs")


class DaySchedule(Base):
    """Per phone, per day: when that day's queue starts (overrides the phone's routine_start)."""

    __tablename__ = "day_schedules"
    __table_args__ = (UniqueConstraint("device_id", "run_date", name="uq_day_schedule"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    run_date: Mapped[date] = mapped_column(Date, index=True)
    start: Mapped[str] = mapped_column(String)  # HH:MM local


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


DEFAULT_SETTINGS: dict[str, str] = {
    "runner_enabled": "true",
    "pace_multiplier": "1.0",  # multiplies device gaps; set low while testing with simulated phones
    "sim_step_seconds": "0.6",  # how long each simulated step takes
    "sim_failure_rate": "0.0",  # 0..1, inject failures to test retry/alerts
    "max_attempts": "2",
    "dry_run": "false",  # true = flows stop right before the final Post/Share button
    "sleep_after_queue": "false",  # true = lock the phone when its day's queue is done (the runner has died twice while locked; keep off until that's solved)
    "queue_auto": "false",  # true = the app fills/empties the queue album itself; false = you manage the album on the phone
}
