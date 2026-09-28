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
        self.check = getattr(log, "check", lambda: None)  # raises FlowAborted after Stop was pressed
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
        self.check()
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

    def _settings(self, depth: int) -> None:
        try:
            self.c.http.post(f"/session/{self.s.session_id}/appium/settings",
                             {"settings": {"snapshotMaxDepth": depth, "customSnapshotTimeout": 10, "waitForIdleTimeout": 0, "animationCoolOffTimeout": 0}})
        except Exception:
            pass

    def launch_from_home(self, app_label: str, pages: int = 4, bundle_id: str | None = None) -> None:
        """Open an app without attaching to it. First choice: WebDriverAgent's unattached launch
        by bundle id (no icon hunting). Fallback: the home screen, where Springboard is asked for
        the icon's position (harmless; only the app itself must never be attached to), pages are
        swiped until it is on screen, and the tap is a plain coordinate tap; OCR as a last resort."""
        if bundle_id:
            try:
                self.log("info", f"Open {app_label} ({bundle_id})")
                self.c.http.post("/wda/apps/launchUnattached", {"bundleId": bundle_id})
                time.sleep(4.0)
                return
            except Exception as e:
                self.log("warn", f"Unattached launch failed ({e}); using the home screen")
        self.home()
        self.home()  # second press: first home-screen page
        W = self.pt_w
        try:
            self._settings(60)  # Springboard's tree is shallow-ish and cheap; icons sit deep in it
            for _ in range(pages + 1):
                icon = self.s(predicate=f"label == '{app_label}' AND type == 'XCUIElementTypeIcon'").wait(timeout=3, raise_error=False)
                if not icon:
                    break
                b = icon.bounds
                cx, cy = b.x + b.width / 2, b.y + b.height / 2
                if 0 <= cx <= W and b.width > 10:
                    self.log("info", f"Open {app_label} from the home screen (icon at {cx:.0f},{cy:.0f})")
                    self._settings(2)
                    self.tap(cx, cy, app_label, pause=4.0)
                    return
                # icon lives on another page: swipe towards it
                if cx > W:
                    self.swipe(W - 40, 400, 40, 400)
                else:
                    self.swipe(40, 400, W - 40, 400)
        except Exception as e:
            self.log("warn", f"Springboard lookup failed ({e}); falling back to reading the screen")
        finally:
            self._settings(2)
        for _ in range(pages):
            w = self.find(rf"^{re.escape(app_label)}$", self.read())
            if w:
                self.log("info", f"Open {app_label} from the home screen")
                self.tap(w.cx, w.y - 32, app_label, pause=4.0)  # icon sits above its label
                return
            self.swipe(W - 60, 400, 60, 400)
        raise FlowError(f"{app_label} icon not found on the home screen")

    def release(self) -> None:
        try:
            self.c.http.delete(f"/session/{self.s.session_id}/actions")
        except Exception:
            pass


# ---- TikTok --------------------------------------------------------------------------

DUR_RX = re.compile(r"^\d\d:\d\d$")
TIKTOK_BUNDLE = "com.zhiliaoapp.musically"


def _secs(text: str) -> int:
    return int(text[:2]) * 60 + int(text[3:5])


def _close(a, b) -> bool:
    return a is not None and b is not None and abs(int(a) - int(b)) <= 1


def read_grid(bp: BlindPhone, tries: int = 3) -> list[Word]:
    """Read the screen, waiting briefly for the grid's duration overlays to render."""
    words = bp.read()
    for _ in range(tries - 1):
        if any(DUR_RX.match(w.text.strip()) for w in words):
            break
        time.sleep(1.2)
        words = bp.read()
    return words


def _lattice_pick(bp: BlindPhone, words: list[Word], from_end: int, count: int, want_secs: float | None, kind: str,
                  lead_tiles: int = 0, newest_s: float | None = None, oldest_s: float | None = None,
                  tile_ratio: float = 1.0) -> tuple[float, float]:
    """Cell centre for the planned item in TikTok's 3-column album grid.

    `from_end` is the item's newest-first position (0 = newest). TikTok builds differ in
    which way the grid runs, so the order is checked against the first media tile's duration
    overlay (newest item's length vs oldest item's); newest-first is assumed when it can't be
    told apart. Row pitch comes from the overlays when readable, else from `tile_ratio`
    (height / width of a tile). The overlay on the chosen tile is a cross-check only."""
    cols, cell_w = 3, bp.pt_w / 3
    overlays = [w for w in words if DUR_RX.match(w.text.strip())]
    if overlays:
        bottoms = sorted({round(w.y + w.h) for w in overlays})
        rows = []
        for b in bottoms:
            if not rows or b - rows[-1] > 20:
                rows.append(b)
        pitch = min(b - a for a, b in zip(rows, rows[1:])) if len(rows) > 1 else cell_w * tile_ratio
        top = rows[0] + 6 - pitch  # top of the first row that has a readable overlay (the grid's first row when not scrolled)
    else:
        tabs = bp.find(r"^(Favorites|Videos|Photos|All)$", words)
        if tabs is None:
            raise FlowError("Can't locate TikTok's grid (no duration overlays and no tab row)")
        pitch = cell_w * tile_ratio
        top = tabs.y + tabs.h + 12

    def overlay_in(row: int, col: int):
        x0, y1 = col * cell_w, top + (row + 1) * pitch
        return next((w for w in overlays if x0 <= w.cx <= x0 + cell_w and abs((w.y + w.h) - y1) < pitch * 0.35), None)

    # which way does the grid run? look at the first media tile
    newest_first = True
    first = overlay_in(lead_tiles // cols, lead_tiles % cols)
    if first is not None:
        got = _secs(first.text)
        if _close(got, oldest_s) and not _close(got, newest_s):
            newest_first = False
        elif _close(got, newest_s) and not _close(got, oldest_s):
            newest_first = True
    last_index = count - 1 + lead_tiles
    target = lead_tiles + from_end if newest_first else last_index - from_end
    if target < lead_tiles or target > last_index:
        raise FlowError("Item position falls outside the album grid; re-sync the camera roll and re-plan")
    row, col = target // cols, target % cols
    cy = top + row * pitch + pitch / 2
    cx = col * cell_w + cell_w / 2
    here = overlay_in(row, col)
    if here and kind == "video" and want_secs and not _close(_secs(here.text), want_secs):
        raise FlowError(f"Cell {target + 1} is {here.text}, expected ~{int(want_secs)}s; re-sync the album and re-plan")
    if here and kind == "photo":
        raise FlowError(f"Cell {target + 1} is a video ({here.text}) but a photo was planned")
    if not here and kind == "video":
        bp.log("warn", "No duration overlay readable on the target cell; trusting the position")
    if cy < 90 or cy > bp.pt_h - 60:
        raise FlowError("Planned item is scrolled out of view; keep the queue album short")
    bp.log("info", f"TikTok: cell {target + 1} ({'newest first' if newest_first else 'oldest first'} grid)" + (f" ({here.text})" if here else ""))
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


LIST_MARKERS = r"^(Recently Saved|Recently Added)$"  # only ever shown inside the album list, never as gallery tabs


def tt_select_album_blind(bp: BlindPhone, album: str, dropdown_region: tuple, list_x: float | None = None) -> None:
    """Make TikTok's picker show `album`: tap the dropdown ONCE, then only scroll the list
    until the album is visible and tap it. Never re-taps the dropdown (that toggles it closed)."""
    W, H = bp.pt_w, bp.pt_h
    pat = rf"^{re.escape(album)}"
    x = list_x if list_x is not None else W * 0.3
    words = bp.read()
    if bp.find(pat, words, region=dropdown_region) and not bp.find(LIST_MARKERS, words):
        return  # already selected
    target = bp.find(pat, words)
    if target is None and not bp.find(LIST_MARKERS, words):
        dd = bp.find(r"^Recents", words, region=dropdown_region) or bp.find(r"^(Stories|Farm|Queue)", words, region=dropdown_region)
        if not dd:
            raise FlowError("TikTok album dropdown not found")
        bp.tap_word(dd, "album dropdown", pause=2.0)
        words = bp.read()
        target = bp.find(pat, words)
    for i in range(12):
        if target:
            bp.tap_word(target, f"album '{album}'", pause=2.0)
            return
        bp.log("info", "Scroll album list")
        bp.s.swipe(int(x), int(H * 0.82), int(x), int(H * 0.45), 0.35)
        time.sleep(1.2)
        words = bp.read()
        target = bp.find(pat, words)
    raise FlowError(f"Album '{album}' not found in TikTok's album list")


def tt_go_home(bp: BlindPhone) -> None:
    """Open TikTok and get to its tab bar, closing any leftover camera/composer screen."""
    W, H = bp.pt_w, bp.pt_h
    bp.launch_from_home("TikTok", bundle_id=TIKTOK_BUNDLE)
    for _ in range(7):
        words = bp.read()
        if bp.find(r"^(Home|Inbox|Friends|Profile)$", words, region=(0, H - 90, W, H)):
            break
        if tt_dismiss_prompt(bp, words):
            continue
        if bp.find(r"^(CAMERA|CREATE|Add sound|Next|Post|Add description|Select multiple|Add to Story|Recents)", words):
            bp.log("info", "Closing a leftover TikTok screen")
            bp.tap(35, 90, "close (X)", pause=2.0)
            continue
        time.sleep(1.5)
    bp.wait_for(r"^(Home|Inbox|Friends|Profile)$", timeout=20, region=(0, H - 90, W, H), what="TikTok tab bar")


def tt_post_blind(bp: BlindPhone, spec: ActionSpec):
    from .ios_flows import run_stages
    extra = spec.extra or {}
    album = (extra.get("album") or "").strip()
    if not album or extra.get("album_from_end") is None:
        raise FlowError("TikTok posts need the item in the queue or story album")
    W, H = bp.pt_w, bp.pt_h

    def open_():
        tt_go_home(bp)
        tt_ensure_account(bp, spec.handle)

    def pick():
        words = bp.read()
        if not bp.find(r"^(Next|Select multiple)$", words):
            if not bp.find(r"^(CAMERA|POST|CREATE)$", words, region=(0, H * 0.85, W, H)):
                if not bp.find(r"^(Home|Inbox|Friends|Profile)$", words, region=(0, H - 90, W, H)):
                    raise FlowError("TikTok isn't on its tab bar (run the 'open' stage first)")
                bp.tap(W / 2, H - 60, "Create (+)", pause=3.0)
            # the upload entry is the camera-roll thumbnail at the bottom-left, on the CAMERA/CREATE row
            row = bp.wait_for(r"^(CAMERA|POST|CREATE)$", timeout=20, region=(0, H * 0.85, W, H), what="camera screen")
            bp.tap(32, row.cy, "Upload (camera roll thumbnail)", pause=3.0)
        bp.wait_for(r"^(Next|Select multiple)$", timeout=20, what="gallery")
        tt_select_album_blind(bp, album, dropdown_region=(0, 0, W, 120))
        words = read_grid(bp)
        cx, cy = _lattice_pick(bp, words, int(extra["album_from_end"]), int(extra.get("album_count") or 0),
                               float(extra.get("duration_s") or 0) or None, spec.media_kind or "video",
                               newest_s=extra.get("album_newest_s"), oldest_s=extra.get("album_oldest_s"), tile_ratio=1.33)
        bp.tap(cx, cy, "planned item", pause=1.5)
        bp.tap_text(r"^Next$", timeout=10, region=(W / 2, H / 2, W, H), what="Next (to editor)", pause=3.0)
        time.sleep(3)  # let TikTok attach a sound when it detects no speech
        w = bp.wait_for(r"^Next$|^Add description|Post$", timeout=40, what="editor or post screen")
        if w.text.strip() == "Next":
            time.sleep(5)
            bp.tap_text(r"^Next$", timeout=10, region=(W / 2, 0, W, H), what="Next (to caption)", pause=3.0)
        bp.wait_for(r"Post$", timeout=20, region=(0, H * 0.8, W, H), what="Post screen")

    def compose():
        if not bp.find(r"Post$", bp.read(), region=(0, H * 0.8, W, H)) and not bp.find(r"Add description|Add caption|Describe", bp._words):
            raise FlowError("Not on TikTok's post screen (run the 'pick' stage first)")
        if spec.caption:
            field = bp.find(r"Add description|Add caption|Describe", bp._words)
            if field is None:
                raise FlowError("Caption field not found on TikTok's post screen")
            bp.tap(field.x + 20, field.cy, "caption", pause=1.0)
            bp.type_keys(spec.caption)
            time.sleep(1.0)
            if not bp.find(re.escape(spec.caption[:12]), bp.read()):
                raise FlowError("Caption did not appear in the field")

    def post():
        # with the keyboard up, Post sits top-right; otherwise it's the red button at the bottom
        words = bp.read()
        post_btn = bp.find(r"^Post$", words, region=(W * 0.7, 0, W, 120)) or bp.find(r"Post$", words, region=(0, H * 0.8, W, H))
        if post_btn is None:
            raise FlowError("Post button not found (run the 'pick' stage first)")
        if extra.get("dry_run"):
            bp.log("info", f"DRY RUN: would tap Post at ({post_btn.cx:.0f},{post_btn.cy:.0f}); stopping here")
            return True, "Dry run reached the Post screen"
        bp.tap_word(post_btn, "Post", pause=4.0)
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

    return run_stages(bp.log, spec, [("open", open_), ("pick", pick), ("compose", compose), ("post", post)])


def tt_current_handle(bp: BlindPhone):
    """(handle word, display-name word) from TikTok's profile header, or (None, None)."""
    W, H = bp.pt_w, bp.pt_h
    words = bp.read()
    handle = bp.find(r"^@", words, region=(0, 0, W * 0.7, H * 0.3))
    if not handle:
        return None, None
    name = min((w for w in words if 0 < handle.y - w.y < 60 and w.cx < W * 0.7), key=lambda w: handle.y - w.y, default=None)
    return handle, name


def tt_ensure_account(bp: BlindPhone, handle: str) -> None:
    """On the Profile tab: switch to `handle` through the display-name sheet if needed."""
    W, H = bp.pt_w, bp.pt_h
    prof = bp.wait_for(r"^Profile$", timeout=15, region=(0, H - 90, W, H), what="Profile tab")
    bp.tap_word(prof, "Profile tab", pause=3.0)
    cur, name = tt_current_handle(bp)
    if cur is None:
        raise FlowError("TikTok profile header not readable")
    if cur.text.lstrip("@").lower() == handle.lower():
        bp.log("info", f"Already on @{handle}")
        return
    if name is None:
        raise FlowError("TikTok display name (account switcher) not found")
    bp.log("info", f"On {cur.text}; switching to @{handle}")
    bp.tap_word(name, "account switcher", pause=2.5)
    row = bp.find(rf"^{re.escape(handle)}$", bp.read(), region=(0, H * 0.4, W, H))
    if row is None:
        raise FlowError(f"@{handle} is not one of the TikTok accounts on this phone")
    bp.tap_word(row, f"@{handle}", pause=4.0)
    cur, _ = tt_current_handle(bp)
    if cur is None or cur.text.lstrip("@").lower() != handle.lower():
        raise FlowError(f"TikTok account switch failed (on {cur.text if cur else '?'})")


def tt_open_story_gallery(bp: BlindPhone) -> None:
    """Get TikTok to the 'Add to Story' gallery: Profile tab, then the blue + on the avatar."""
    W, H = bp.pt_w, bp.pt_h
    bp.launch_from_home("TikTok", bundle_id=TIKTOK_BUNDLE)
    for _ in range(6):
        words = bp.read()
        if bp.find(r"^Add to Story$", words):
            return
        if bp.find(r"^(Home|Inbox|Friends|Profile)$", words, region=(0, H - 90, W, H)):
            break
        if tt_dismiss_prompt(bp, words):
            continue
        bp.tap(35, 90, "close (X)", pause=2.0)
    tt_ensure_account(bp, bp.handle)
    # the avatar sits top-right of the profile header with a blue + at its bottom-right corner,
    # level with the Following / Followers / Likes row
    row = bp.wait_for(r"^(Followers|Following|Likes)$", timeout=15, what="profile header")
    bp.tap(W * 0.925, row.cy - row.h * 0.4, "avatar +", pause=3.0)
    for _ in range(4):
        words = bp.read()
        if bp.find(r"^Add to Story$", words):
            return
        # a chooser may appear first ("Create story" / "Story")
        opt = bp.find(r"^(Create story|Add to story|Story)$", words)
        if opt:
            bp.tap_word(opt, opt.text, pause=3.0)
            continue
        if tt_dismiss_prompt(bp, words):
            continue
        time.sleep(1.5)
    raise FlowError("Did not reach the 'Add to Story' gallery from the profile avatar")


def run_blind(driver, spec: ActionSpec, log: LogFn):
    from .base import ActionResult
    bp = BlindPhone(driver.base, log)
    bp.handle = spec.handle
    partial = False
    try:
        if spec.action == "tt_story":
            from .ios_blind_story import tt_story_blind
            ok, message, partial = tt_story_blind(bp, spec, tt_open_story_gallery)
        else:
            ok, message, partial = tt_post_blind(bp, spec)
    except FlowError as e:
        ok, message = False, str(e)
    except Exception:
        bp.release()
        raise
    shot = None
    try:
        shot = bp.screenshot_png()
    except Exception:
        pass
    bp.release()  # stay where we are; the next job opens what it needs
    return ActionResult(ok, message, shot, partial=partial)
