from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---- accounts --------------------------------------------------------------
class AccountOut(ORM):
    id: int
    device_id: str
    platform: str
    handle: str
    status: str
    story_link: str
    notes: str
    sort_order: int


class AccountIn(BaseModel):
    device_id: str
    platform: str
    handle: str
    story_link: str = ""
    notes: str = ""
    status: str = "active"


class AccountPatch(BaseModel):
    handle: str | None = None
    status: str | None = None
    story_link: str | None = None
    notes: str | None = None
    sort_order: int | None = None
    device_id: str | None = None


# ---- media -----------------------------------------------------------------
class MediaOut(ORM):
    id: int
    device_id: str
    filename: str
    kind: str
    duration_s: float
    size_bytes: int
    created_at: datetime
    present: bool
    has_thumb: bool = False
    index: int | None = None  # position in Recents, newest first (0-based)


# ---- devices ---------------------------------------------------------------
class TodaySummary(BaseModel):
    total: int = 0
    done: int = 0
    failed: int = 0
    scheduled: int = 0
    running: int = 0


class DeviceOut(ORM):
    id: str
    name: str
    model: str
    kind: str
    routine_start: str
    gap_min_s: int
    gap_max_s: int
    paused: bool
    sort_order: int
    connected: bool = False
    connecting: bool = False
    error: str = ""
    busy: bool = False
    current_job_id: int | None = None
    accounts: list[AccountOut] = Field(default_factory=list)
    media_count: int = 0
    today: TodaySummary = Field(default_factory=TodaySummary)


class DevicePatch(BaseModel):
    name: str | None = None
    routine_start: str | None = None
    gap_min_s: int | None = None
    gap_max_s: int | None = None
    paused: bool | None = None
    sort_order: int | None = None


# ---- jobs ------------------------------------------------------------------
class JobOut(ORM):
    id: int
    account_id: int
    device_id: str
    action: str
    media_id: int | None
    media: MediaOut | None = None
    caption: str
    link: str
    link_text: str
    run_date: date
    run_at: datetime | None
    sort_order: int
    status: str
    attempts: int
    last_error: str
    has_screenshot: bool = False
    started_at: datetime | None
    finished_at: datetime | None
    next_run: datetime | None = None
    handle: str = ""
    platform: str = ""
    device_name: str = ""


class JobIn(BaseModel):
    id: int | None = None
    account_id: int
    action: str
    media_id: int | None = None
    caption: str = ""
    link: str = ""
    link_text: str = ""
    run_date: date
    run_at: datetime | None = None


class JobLogOut(ORM):
    id: int
    ts: datetime
    level: str
    message: str


class RunnerState(BaseModel):
    enabled: bool
    busy_devices: list[str]
    current: dict[str, int]
