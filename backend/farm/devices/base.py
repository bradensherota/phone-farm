"""Driver interface. One driver instance per physical (or simulated) phone.

Everything the scheduler needs from a phone goes through this interface, so the
simulated driver and the real iOS driver are interchangeable.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable


@dataclass
class RollItem:
    filename: str
    kind: str  # "photo" | "video"
    created_at: datetime  # capture date (what the gallery shows on the cell)
    duration_s: float = 0.0
    size_bytes: int = 0
    added_at: datetime | None = None  # when it entered the library (the gallery's sort order)
    authoritative: bool = False  # True when these fields came from the library index, not file stats
    albums: tuple[str, ...] = ()  # user album titles this item belongs to


@dataclass
class ActionSpec:
    action: str  # ig_reel | ig_story | tt_post | tt_story | x_post
    platform: str  # instagram | tiktok | x
    handle: str
    media_filename: str | None
    media_kind: str | None  # photo | video
    media_index: int | None  # 0-based position in the phone's Recents grid (newest first)
    caption: str = ""
    link: str = ""
    link_text: str = ""
    extra: dict = field(default_factory=dict)


@dataclass
class ActionResult:
    ok: bool
    message: str = ""
    screenshot_png: bytes | None = None


LogFn = Callable[[str, str], None]  # (level, message)


class Driver(ABC):
    kind: str = "base"

    def __init__(self, device_id: str, name: str):
        self.device_id = device_id
        self.name = name

    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def is_connected(self) -> bool: ...

    @abstractmethod
    def screenshot(self) -> bytes:
        """Current screen as PNG bytes."""

    @abstractmethod
    def list_camera_roll(self) -> list[RollItem]: ...

    @abstractmethod
    def thumbnail(self, filename: str, max_px: int = 400) -> bytes | None:
        """JPEG thumbnail bytes for a roll item, or None if unavailable."""

    @abstractmethod
    def run_action(self, spec: ActionSpec, log: LogFn) -> ActionResult: ...

    def add_to_album(self, items: list, album: str, log: LogFn) -> list:
        """Optional: put library items into a Photos album on the phone. Returns the items added."""
        return []

    def remove_from_album(self, item, album: str, log: LogFn) -> bool:
        """Optional: take one item out of a Photos album (never deletes the media)."""
        return False

    def clear_album(self, album: str, log: LogFn) -> bool:
        """Optional: take every item out of a Photos album (never deletes the media)."""
        return False

    def media_meta(self, filename: str) -> dict | None:
        """Optional: {'duration_s': float, 'captured_at': datetime} read from the file itself.
        Real phones sort their gallery by capture date, which can differ from the file's timestamps."""
        return None

    def info(self) -> dict:
        return {"kind": self.kind, "connected": self.is_connected()}
