from __future__ import annotations

from datetime import date, datetime

from pydantic import field_validator, BaseModel, ConfigDict, Field


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
    added_at: datetime | None = None
    albums: str = ""
    present: bool
    has_thumb: bool = False
    index: int | None = None  # position in Recents, newest first (0-based)
    posted_at: datetime | None = None  # latest finished post that used this item
    posted_on: str = ""  # "@handle (Reel), @other (Story)" for the tooltip
    scheduled_for: date | None = None  # nearest day this item is still planned for
    scheduled_on: str = ""  # "@handle (Reel) Sep 30, …" for the tooltip


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
    parked: bool = False
    sort_order: int
    story_album: str = "Stories"
    queue_album: str = "Farm Queue"
    prepare_lead_min: int = 60
    has_passcode: bool = False
    album_sorts: dict = {}

    @field_validator("album_sorts", mode="before")
    @classmethod
    def _album_sorts_json(cls, v):
        # stored as JSON text on the device row
        if isinstance(v, str):
            import json
            try:
                return json.loads(v or "{}")
            except Exception:
                return {}
        return v or {}
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
    story_album: str | None = None
    queue_album: str | None = None
    prepare_lead_min: int | None = None
    passcode: str | None = None


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
    group_key: str = ""
    group_origin_account_id: int | None = None
    group_scope: str = ""
    group_origin_handle: str = ""
    group_origin_device: str = ""
    origin_media_id: int | None = None
    last_stage: str = ""
    failed_stage: str = ""
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
    all_accounts: bool = False  # stories only: fan out to every story-capable account on the phone
    all_phones: bool = False  # stories only: also every Instagram account on every other phone


class JobLogOut(ORM):
    id: int
    ts: datetime
    level: str
    message: str


class RunnerState(BaseModel):
    enabled: bool
    busy_devices: list[str]
    current: dict[str, int]
