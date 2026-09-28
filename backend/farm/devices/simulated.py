"""Simulated iPhone.

Camera roll = files in sim_media/<device_id>/. Drop real photos/videos there and
they show up in the panel exactly as they would after an AirDrop. Actions are
played out step by step with a fake screen so the scheduler, planner and
activity views can be exercised end to end before real phones are wired in.
"""

from __future__ import annotations

import io
import random
import subprocess
import textwrap
import threading
import time
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ..config import SIM_MEDIA_DIR
from .base import ActionResult, ActionSpec, Driver, LogFn, RollItem

VIDEO_EXT = {".mp4", ".mov", ".m4v"}
PHOTO_EXT = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp"}

W, H = 390, 844


def _font(size: int):
    for p in (
        "/System/Library/Fonts/SFNS.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ):
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


class SimulatedDriver(Driver):
    kind = "sim"

    def __init__(self, device_id: str, name: str, settings_getter):
        super().__init__(device_id, name)
        self.folder: Path = SIM_MEDIA_DIR / device_id
        self.folder.mkdir(parents=True, exist_ok=True)
        self._settings = settings_getter  # callable -> dict[str,str]
        self._lock = threading.Lock()
        self._screen_title = "Home"
        self._screen_lines: list[str] = ["Idle"]
        self._screen_app = ""
        self._connected = False

    # ---- lifecycle -------------------------------------------------------
    def connect(self) -> None:
        self._connected = True

    def is_connected(self) -> bool:
        return self._connected

    # ---- camera roll -----------------------------------------------------
    def list_camera_roll(self) -> list[RollItem]:
        items: list[RollItem] = []
        for p in self.folder.iterdir():
            if not p.is_file() or p.name.startswith("."):
                continue
            ext = p.suffix.lower()
            if ext in VIDEO_EXT:
                kind = "video"
            elif ext in PHOTO_EXT:
                kind = "photo"
            else:
                continue
            st = p.stat()
            items.append(
                RollItem(
                    filename=p.name,
                    kind=kind,
                    created_at=datetime.fromtimestamp(st.st_mtime),
                    duration_s=_probe_duration(p) if kind == "video" else 0.0,
                    size_bytes=st.st_size,
                    added_at=datetime.fromtimestamp(st.st_mtime),
                    authoritative=True,
                )
            )
        items.sort(key=lambda r: r.created_at, reverse=True)
        return items

    def pull_file(self, filename: str) -> bytes | None:
        p = self.folder / filename
        return p.read_bytes() if p.exists() else None

    def thumbnail(self, filename: str, max_px: int = 400) -> bytes | None:
        p = self.folder / filename
        if not p.exists():
            return None
        if p.suffix.lower() in VIDEO_EXT:
            return _video_thumb(p, max_px)
        return _image_thumb(p, max_px)

    # ---- screen ----------------------------------------------------------
    def screenshot(self) -> bytes:
        with self._lock:
            title, lines, app = self._screen_title, list(self._screen_lines), self._screen_app
        img = Image.new("RGB", (W, H), (12, 12, 14))
        d = ImageDraw.Draw(img)
        # status bar
        d.text((28, 18), datetime.now().strftime("%-I:%M"), fill=(240, 240, 240), font=_font(17))
        d.rounded_rectangle((W - 60, 20, W - 28, 36), radius=4, outline=(200, 200, 200), width=2)
        d.rectangle((W - 58, 22, W - 34, 34), fill=(120, 220, 120))
        # app chrome
        accent = {"instagram": (225, 48, 108), "tiktok": (37, 244, 238), "x": (255, 255, 255)}.get(app, (90, 90, 90))
        d.rectangle((0, 60, W, 108), fill=(22, 22, 26))
        d.text((24, 74), title, fill=accent, font=_font(20))
        y = 140
        for line in lines[-14:]:
            for sub in textwrap.wrap(line, 40) or [""]:
                d.text((24, y), sub, fill=(220, 220, 220), font=_font(16))
                y += 26
            y += 6
        # home indicator
        d.rounded_rectangle((W // 2 - 66, H - 18, W // 2 + 66, H - 12), radius=3, fill=(230, 230, 230))
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return buf.getvalue()

    def _set_screen(self, title: str, app: str = "", reset: bool = False):
        with self._lock:
            self._screen_title = title
            self._screen_app = app
            if reset:
                self._screen_lines = []

    def _say(self, log: LogFn, msg: str, level: str = "info"):
        with self._lock:
            self._screen_lines.append(msg)
        log(level, msg)

    # ---- actions ---------------------------------------------------------
    def run_action(self, spec: ActionSpec, log: LogFn) -> ActionResult:
        s = self._settings()
        step = float(s.get("sim_step_seconds", "0.6"))
        fail_rate = float(s.get("sim_failure_rate", "0"))

        app_name = {"instagram": "Instagram", "tiktok": "TikTok", "x": "X"}[spec.platform]
        self._set_screen(app_name, spec.platform, reset=True)

        steps = self._steps_for(spec)
        for i, msg in enumerate(steps):
            self._say(log, msg)
            time.sleep(step * random.uniform(0.6, 1.4))
            if fail_rate and random.random() < fail_rate / max(1, len(steps)):
                self._say(log, "Unexpected screen: 'Something went wrong' dialog", "error")
                shot = self.screenshot()
                self._set_screen("Home", reset=True)
                self._say(lambda *_: None, "Idle")
                return ActionResult(False, "Unexpected screen while posting (simulated)", shot)

        shot = self.screenshot()
        self._set_screen("Home", reset=True)
        with self._lock:
            self._screen_lines = ["Idle"]
        return ActionResult(True, "Posted", shot)

    def _steps_for(self, spec: ActionSpec) -> list[str]:
        media = spec.media_filename or "(no media)"
        idx = f"#{spec.media_index + 1}" if spec.media_index is not None else "?"
        pick = f"Pick {media} from Recents ({idx})"
        cap = f"Type caption ({len(spec.caption)} chars)" if spec.caption else "Skip caption"
        base = [f"Open {spec.platform}", f"Switch to @{spec.handle}", "Verify handle on profile"]
        if spec.action == "ig_reel":
            return base + ["Tap +", "Choose Reel", pick, "Next", "Next", cap, "Share"]
        if spec.action == "ig_story":
            return base + [
                "Tap Your story +",
                pick,
                "Open stickers",
                "Choose Link sticker",
                f"Paste {spec.link}",
                f"Set sticker text: {spec.link_text}",
                "Done",
                "Share to Your story",
            ]
        if spec.action == "tt_post":
            return base + ["Tap +", "Upload", pick, "Next", cap, "Post"]
        if spec.action == "tt_story":
            return base + ["Tap +", "Upload", pick, "Add link", f"Paste {spec.link}", f"Text: {spec.link_text}", "Your Story"]
        if spec.action == "x_post":
            return base + ["Tap compose", cap, "Add media", pick, "Post"]
        return base + ["Unknown action"]


# ---- helpers --------------------------------------------------------------

def _probe_duration(p: Path) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
            capture_output=True, text=True, timeout=10,
        )
        return round(float(out.stdout.strip() or 0), 1)
    except Exception:
        return 0.0


def _video_thumb(p: Path, max_px: int) -> bytes | None:
    try:
        out = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", "1", "-i", str(p), "-frames:v", "1",
             "-vf", f"scale='min({max_px},iw)':-2", "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "4", "-"],
            capture_output=True, timeout=30,
        )
        if out.stdout:
            return out.stdout
        # video shorter than 1s: grab first frame
        out = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(p), "-frames:v", "1",
             "-vf", f"scale='min({max_px},iw)':-2", "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "4", "-"],
            capture_output=True, timeout=30,
        )
        return out.stdout or None
    except Exception:
        return None


def _image_thumb(p: Path, max_px: int) -> bytes | None:
    try:
        if p.suffix.lower() in {".heic", ".heif"}:
            # macOS converts HEIC natively; sips needs a real output file
            import tempfile
            with tempfile.TemporaryDirectory() as td:
                out_path = Path(td) / "thumb.jpg"
                subprocess.run(
                    ["sips", "-s", "format", "jpeg", "-Z", str(max_px), str(p), "--out", str(out_path)],
                    capture_output=True, timeout=30,
                )
                return out_path.read_bytes() if out_path.exists() else None
        img = Image.open(p)
        img.thumbnail((max_px, max_px))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "JPEG", quality=85)
        return buf.getvalue()
    except Exception:
        return None
