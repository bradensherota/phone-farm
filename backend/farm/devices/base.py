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
    created_at: datetime
    duration_s: float = 0.0
    size_bytes: int = 0


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

    def info(self) -> dict:
        return {"kind": self.kind, "connected": self.is_connected()}
