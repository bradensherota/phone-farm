"""Blind driving: taps by screen position, verified by screenshots + local OCR.

For apps that misbehave when the UI-testing harness attaches to them (TikTok freezes and
shows "Video isn't available"), we never launch or query the app through the harness.
The runner stays attached to Springboard only; we take screenshots (device-level), read
text off them with Apple Vision on the Mac (`tools/ocr.swift`, compiled to ~/PhoneFarm/ocr),
and tap coordinates. Captions are typed by putting text on the phone's clipboard and pasting.
"""

from __future__ import annotations

import base64
import io
import json
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import wda

from .base import ActionSpec, LogFn
from .ios_flows import FlowError

OCR_BIN = Path.home() / "PhoneFarm" / "ocr"


@dataclass
class Word:
    text: str
    x: float  # points, top-left
    y: float
    w: float
    h: float
    conf: float

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2


class BlindPhone:
    def __init__(self, base_url: str, log: LogFn):
        self.base = base_url
        self.log = log
        self.c = wda.Client(base_url)
        self.c.timeout = 60
        self.s = self.c.session()  # bare session: attached to whatever is in front, no launch
        try:
            self.c.http.post(f"/session/{self.s.session_id}/appium/settings",
                             {"settings": {"snapshotMaxDepth": 2, "customSnapshotTimeout": 5, "waitForIdleTimeout": 0,
                                           "animationCoolOffTimeout": 0}})
        except Exception:
            pass
        w, h = self.s.window_size()
        self.pt_w, self.pt_h = float(w), float(h)
        self._scale: float | None = None
        self._words: list[Word] = []

    # ---- seeing --------------------------------------------------------------
    def screenshot_png(self) -> bytes:
        import urllib.request
        with urllib.request.urlopen(self.base + "/screenshot", timeout=30) as r:
            return base64.b64decode(json.loads(r.read())["value"])

    def read(self) -> list[Word]:
        """Fresh screenshot -> words in points."""
        png = self.screenshot_png()
        if self._scale is None:
            from PIL import Image
            im = Image.open(io.BytesIO(png))
            self._scale = im.width / self.pt_w
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(png)
            path = f.name
        try:
            out = subprocess.run([str(OCR_BIN), path], capture_output=True, timeout=30).stdout
        finally:
            Path(path).unlink(missing_ok=True)
        sc = self._scale or 3.0
        self._words = [Word(w["text"], w["x"] / sc, w["y"] / sc, w["w"] / sc, w["h"] / sc, w["conf"]) for w in json.loads(out or b"[]")]
        return self._words

    def find(self, pattern: str, words: list[Word] | None = None, region: tuple | None = None, exact: bool = False) -> Word | None:
        """First word matching `pattern` (regex, case-insensitive) optionally inside region (x0,y0,x1,y1) in points."""
        rx = re.compile(pattern, re.I)
        for w in (words if words is not None else self._words):
            if region and not (region[0] <= w.cx <= region[2] and region[1] <= w.cy <= region[3]):
                continue
            if (rx.fullmatch(w.text.strip()) if exact else rx.search(w.text)):
                return w
        return None

    def wait_for(self, pattern: str, timeout: float = 15, region: tuple | None = None, what: str = "") -> Word:
        deadline = time.time() + timeout
        while True:
            w = self.find(pattern, self.read(), region)
            if w:
                return w
            if time.time() > deadline:
                raise FlowError(f"Never saw {what or pattern!r} on screen (waited {timeout:.0f}s)")
            time.sleep(1.0)

    def gone(self, pattern: str, timeout: float = 15, region: tuple | None = None) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not self.find(pattern, self.read(), region):
                return True
            time.sleep(1.0)
        return False

    # ---- acting --------------------------------------------------------------
    def tap(self, x: float, y: float, what: str = "", pause: float = 1.2) -> None:
        self.log("info", f"Tap {what or ''} ({x:.0f},{y:.0f})".rstrip())
        self.s.tap(int(x), int(y))
        time.sleep(pause)

    def tap_word(self, w: Word, what: str = "", pause: float = 1.2) -> None:
        self.tap(w.cx, w.cy, what or w.text, pause)

    def tap_text(self, pattern: str, timeout: float = 15, region: tuple | None = None, what: str = "", pause: float = 1.2) -> Word:
        w = self.wait_for(pattern, timeout, region, what)
        self.tap_word(w, what or w.text, pause)
        return w

    def long_press(self, x: float, y: float, seconds: float = 0.8) -> None:
        self.c.http.post(f"/session/{self.s.session_id}/wda/touchAndHold", {"x": int(x), "y": int(y), "duration": seconds})
        time.sleep(0.8)

    def swipe(self, x1, y1, x2, y2, duration=0.3) -> None:
        self.s.swipe(int(x1), int(y1), int(x2), int(y2), duration)
        time.sleep(1.0)

    def home(self) -> None:
        try:
            self.c.home()
        except Exception:
            pass
        time.sleep(1.0)

    def type_keys(self, text: str) -> None:
        """Type into the focused field with the runner's keyboard input (works on TikTok; the
        clipboard route does not, TikTok never shows a Paste menu)."""
        self.log("info", f"Type {len(text)} chars")
        self.c.http.post(f"/session/{self.s.session_id}/wda/keys", {"value": list(text), "frequency": 30})
        time.sleep(0.5)

    def set_clipboard(self, text: str) -> None:
        self.c.http.post(f"/session/{self.s.session_id}/wda/setPasteboard",
                         {"content": base64.b64encode(text.encode()).decode(), "contentType": "plaintext"})

    def paste_into(self, x: float, y: float, text: str, what: str = "field") -> None:
        """Put text on the clipboard, long-press the field, tap Paste."""
        self.set_clipboard(text)
        self.tap(x, y, what, pause=0.8)
        self.long_press(x, y)
        w = self.wait_for(r"^Paste$", timeout=5, what="Paste menu")
        self.tap_word(w, "Paste", pause=1.0)

    def launch_from_home(self, app_label: str, pages: int = 4) -> None:
        """Open an app by tapping its icon on the home screen (found by its label)."""
        self.home()
        self.home()  # second press: first home-screen page
        for _ in range(pages):
            w = self.find(rf"^{re.escape(app_label)}$", self.read())
            if w:
                self.log("info", f"Open {app_label} from the home screen")
                self.tap(w.cx, w.y - 32, app_label, pause=4.0)  # icon sits above its label
                return
            self.swipe(330, 400, 60, 400)
        raise FlowError(f"{app_label} icon not found on the home screen")

    def release(self) -> None:
        try:
            self.c.http.delete(f"/session/{self.s.session_id}/actions")
        except Exception:
            pass


# ---- TikTok --------------------------------------------------------------------------

DUR_RX = re.compile(r"^\d\d:\d\d$")


def _lattice_pick(bp: BlindPhone, words: list[Word], from_end: int, count: int, want_secs: float | None, kind: str) -> tuple[float, float]:
    """Cell center for the item `from_end` places before the newest, using the duration
    overlays as the lattice reference (3 columns, oldest first, ending at the newest)."""
    overlays = [w for w in words if DUR_RX.match(w.text.strip())]
    if not overlays:
        raise FlowError("No duration overlays visible in TikTok's grid (album needs at least one video)")
    cols, cell_w = 3, bp.pt_w / 3
    # rows: cluster overlays by their bottom y
    bottoms = sorted({round(w.y + w.h) for w in overlays})
    rows = []
    for b in bottoms:
        if not rows or b - rows[-1] > 20:
            rows.append(b)
    pitch = min(b - a for a, b in zip(rows, rows[1:])) if len(rows) > 1 else cell_w * 0.79
    last_index = count - 1
    target = last_index - from_end
    if target < 0:
        raise FlowError("Item position is before the start of the album")
    rows_up = last_index // cols - target // cols
    col = target % cols
    bottom = rows[-1] + 6  # overlay sits a few points above the cell's bottom edge
    cy = bottom - rows_up * pitch - pitch / 2
    cx = col * cell_w + cell_w / 2
    here = [w for w in overlays if abs(w.cy - (cy + pitch / 2 - 12)) < pitch / 2 and abs(w.cx - (cx + cell_w / 2 - 22)) < cell_w / 2]
    if kind == "video":
        if not here:
            raise FlowError(f"Cell {from_end + 1} from the end shows no duration; expected a video")
        if want_secs:
            got = int(here[0].text[:2]) * 60 + int(here[0].text[3:5])
            if abs(got - int(want_secs)) > 1:
                raise FlowError(f"Cell {from_end + 1} from the end is {here[0].text}, expected ~{int(want_secs)}s; re-sync the album and re-plan")
    elif here:
        raise FlowError(f"Cell {from_end + 1} from the end is a video ({here[0].text}) but a photo was planned")
    if cy < 90:
        raise FlowError("Planned item is scrolled out of view; keep the queue album short")
    bp.log("info", f"TikTok: cell {from_end + 1} from the end" + (f" ({here[0].text})" if here else " (photo)"))
    return cx, cy


PROMPT_BUTTONS = r"^(Not now|Not Now|Skip|Later|Maybe later|Don.t allow|Cancel|No thanks)$"


def tt_dismiss_prompt(bp: BlindPhone, words: list[Word]) -> bool:
    """TikTok pops sheets ("Get notified…", "Find contacts", …). Prefer a decline button;
    otherwise the sheet's X at its top-right; otherwise tap the dimmed area above it."""
    btn = bp.find(PROMPT_BUTTONS, words)
    if btn:
        bp.tap_word(btn, "decline prompt", pause=1.5)
        return True
    headline = bp.find(r"^(Get notified|Find contacts|Allow|Turn on)", words)
    if headline:
        bp.log("info", f"Dismissing prompt: {headline.text}")
        # the sheet's X sits at the top-right of the sheet; estimate the sheet top from the topmost sheet word
        sheet_words = [w for w in words if w.cy > bp.pt_h * 0.45]
        top = min((w.y for w in sheet_words), default=headline.y) - 150
        bp.tap(bp.pt_w - 53, max(top + 40, 120), "sheet X", pause=1.5)
        return True
    return False


def tt_post_blind(bp: BlindPhone, spec: ActionSpec) -> tuple[bool, str]:
    extra = spec.extra or {}
    album = (extra.get("album") or "").strip()
    if not album or extra.get("album_from_end") is None:
        raise FlowError("TikTok posts need the item in the queue or story album")
    W, H = bp.pt_w, bp.pt_h
    bp.launch_from_home("TikTok")
    # if a camera/composer was left open, close it (X top-left) rather than hitting the shutter
    for _ in range(7):
        words = bp.read()
        if bp.find(r"^(Home|Inbox|Friends|Profile)$", words, region=(0, H - 90, W, H)):
            break
        if tt_dismiss_prompt(bp, words):
            continue
        if bp.find(r"^(CAMERA|CREATE|Add sound|Next|Post|Add description)", words):
            bp.log("info", "Closing a leftover TikTok screen")
            bp.tap(35, 90, "close (X)", pause=2.0)
            continue
        time.sleep(1.5)
    bp.wait_for(r"^(Home|Inbox|Friends|Profile)$", timeout=20, region=(0, H - 90, W, H), what="TikTok tab bar")
    bp.tap(W / 2, H - 60, "Create (+)", pause=3.0)
    # the upload entry is the camera-roll thumbnail at the bottom-left, on the CAMERA/CREATE row
    row = bp.wait_for(r"^CAMERA$", timeout=20, what="camera screen")
    bp.tap(32, row.cy, "Upload (camera roll thumbnail)", pause=3.0)
    # gallery: album dropdown ("Recents ⌄" or the current album name) sits top-middle
    bp.wait_for(r"^Next$", timeout=20, what="gallery")
    top = (0, 0, W, 120)
    cur = bp.find(rf"^{re.escape(album)}", region=top) or bp.find(r"^Recents", region=top)
    if not cur:
        raise FlowError("TikTok album dropdown not found")
    if not cur.text.startswith(album):
        bp.tap_word(cur, "album dropdown", pause=1.5)
        bp.tap_text(rf"^{re.escape(album)}", timeout=8, what=f"album '{album}'", pause=2.0)
    words = bp.read()
    cx, cy = _lattice_pick(bp, words, int(extra["album_from_end"]), int(extra.get("album_count") or 0),
                           float(extra.get("duration_s") or 0) or None, spec.media_kind or "video")
    bp.tap(cx, cy, "planned item", pause=1.5)
    bp.tap_text(r"^Next$", timeout=10, region=(W / 2, H / 2, W, H), what="Next (to editor)", pause=3.0)
    # editor: TikTok may auto-add a sound after a few seconds when it hears no speech
    bp.wait_for(r"^Next$", timeout=40, region=(W / 2, 0, W, H), what="editor")
    time.sleep(5)
    bp.tap_text(r"^Next$", timeout=10, region=(W / 2, 0, W, H), what="Next (to caption)", pause=3.0)
    bp.wait_for(r"Post$", timeout=20, region=(0, H * 0.8, W, H), what="Post screen")
    if spec.caption:
        field = bp.find(r"Add description|Add caption|Describe", bp._words)
        if field is None:
            raise FlowError("Caption field not found on TikTok's post screen")
        bp.tap(field.x + 20, field.cy, "caption", pause=1.0)
        bp.type_keys(spec.caption)
        time.sleep(1.0)
        if not bp.find(re.escape(spec.caption[:12]), bp.read()):
            raise FlowError("Caption did not appear in the field")
    # with the keyboard up, Post sits top-right; otherwise it's the red button at the bottom
    words = bp._words
    post = bp.find(r"^Post$", words, region=(W * 0.7, 0, W, 120)) or bp.find(r"Post$", words, region=(0, H * 0.8, W, H))
    if post is None:
        raise FlowError("Post button not found")
    bp.tap_word(post, "Post", pause=4.0)
    for _ in range(5):
        words = bp.read()
        if bp.find(r"Video posted", words):
            break
        if tt_dismiss_prompt(bp, words):
            continue
        if not bp.find(r"Post$", words, region=(0, H * 0.8, W, H)) and not bp.find(r"^Post$", words, region=(W * 0.7, 0, W, 120)):
            break
        time.sleep(2)
    words = bp.read()
    if bp.find(r"Post$", words, region=(0, H * 0.8, W, H)) and not bp.find(r"Video posted", words):
        raise FlowError("Still on the post screen after tapping Post")
    tt_dismiss_prompt(bp, words)
    return True, "TikTok post shared"


def run_blind(driver, spec: ActionSpec, log: LogFn):
    from .base import ActionResult
    bp = BlindPhone(driver.base, log)
    try:
        ok, message = tt_post_blind(bp, spec)
    except FlowError as e:
        ok, message = False, str(e)
    shot = None
    try:
        shot = bp.screenshot_png()
    except Exception:
        pass
    try:
        if ok:
            bp.home()
    finally:
        bp.release()
    return ActionResult(ok, message, shot)
