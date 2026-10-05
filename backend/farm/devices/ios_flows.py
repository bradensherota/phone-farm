"""Posting flows for real iPhones, driven through WebDriverAgent.

Every flow is "find element by accessibility id, tap, type" against the live app.
Instagram ships stable accessibility identifiers (e.g. `story-tray-add-button`,
`gallery-video-cell-3`, `share-to-your-story`), mapped on-device 2026-09-27 on
iOS 26.6 / Instagram current. They change with app updates; each flow logs every
step so a broken step is obvious in Activity.

Media selection: Instagram's gallery names cells by position in Recents (newest = 0),
which is exactly `ActionSpec.media_index`. Cells further down are reached by swiping.
"""

from __future__ import annotations

import io
import time

import wda  # facebook-wda

from .base import ActionResult, ActionSpec, LogFn

BUNDLES = {
    "instagram": "com.burbn.instagram",
    "tiktok": "com.zhiliaoapp.musically",
    "x": "com.atebits.Tweetie2",
}


class FlowGiveUp(Exception):
    """A failure a retry can't fix; the job is marked failed at once and the queue moves on."""


class FlowError(Exception):
    pass


class Phone:
    """Thin convenience wrapper around a facebook-wda client for one phone."""

    def __init__(self, base_url: str, log: LogFn):
        self.base = base_url
        self.c = wda.Client(base_url)
        self.c.timeout = 60
        self.log = log
        self.check = getattr(log, "check", lambda: None)  # raises FlowAborted after Stop was pressed
        self.s: wda.Session | None = None

    # ---- app ---------------------------------------------------------------
    # Snapshotting a huge view tree (TikTok's feed) can freeze both the app and the runner
    # for tens of seconds. Capping depth and snapshot time keeps queries cheap.
    WDA_SETTINGS = {"snapshotMaxDepth": 30, "customSnapshotTimeout": 15, "waitForIdleTimeout": 3,
                    "animationCoolOffTimeout": 1, "shouldUseCompactResponses": True}

    def apply_settings(self) -> None:
        if self.s is None:
            return
        try:
            self.c.http.post(f"/session/{self.s.session_id}/appium/settings", {"settings": self.WDA_SETTINGS})
        except Exception as e:
            self.log("warn", f"Could not apply runner settings: {e}")

    def open(self, bundle_id: str) -> None:
        """Attach to the app, bringing it to the front. It is never relaunched if it's already
        running, and never terminated when the session ends, so Instagram stays open all day."""
        self.log("info", f"Open {bundle_id}")
        caps = {
            "bundleId": bundle_id, "arguments": [], "environment": {},
            "shouldWaitForQuiescence": False,
            "forceAppLaunch": False,      # activate a running app instead of restarting it
            "shouldTerminateApp": False,  # leave the app running when the session is dropped
        }
        res = self.c.http.post("session", {"capabilities": {"alwaysMatch": caps}, "desiredCapabilities": caps})
        self.s = wda.Client(self.base, _session_id=res.sessionId)
        self.s.timeout = 60
        self.apply_settings()
        time.sleep(1.0)

    def attach(self) -> None:
        """Attach to whatever is on screen without launching anything (a stage run on its own)."""
        self.log("info", "Attach to the app on screen")
        self.s = self.c.session()
        self.apply_settings()
        time.sleep(0.8)

    def relaunch(self, bundle_id: str) -> None:
        """Last resort when the app is wedged on a screen nothing else can leave: quit it and
        start it again. Only used when no story editor is open (that would lose a sticker)."""
        self.log("warn", f"Relaunch {bundle_id}")
        assert self.s is not None, "open() an app first"
        for path, pause in (("terminate", 1.5), ("launch", 4.0)):
            try:
                self.c.http.post(f"/session/{self.s.session_id}/wda/apps/{path}", {"bundleId": bundle_id})
            except Exception as e:
                self.log("warn", f"{path} failed: {e}")
            time.sleep(pause)

    def home(self) -> None:
        try:
            self.c.home()
        except Exception:
            pass

    def release_touches(self) -> None:
        """Make sure no synthetic finger is left pressed on the glass (a held touch makes the
        phone ignore real taps and later automation taps until it clears)."""
        if self.s is None:
            return
        try:
            self.c.http.delete(f"/session/{self.s.session_id}/actions")  # W3C: release all pressed inputs
        except Exception:
            pass
        try:
            # belt and braces: a clean tap-and-lift on an empty spot of the status bar area
            self.c.http.post(f"/session/{self.s.session_id}/actions", {"actions": [
                {"type": "pointer", "id": "finger1", "parameters": {"pointerType": "touch"}, "actions": [
                    {"type": "pointerMove", "duration": 0, "x": 195, "y": 20},
                    {"type": "pointerDown", "button": 0}, {"type": "pause", "duration": 30}, {"type": "pointerUp", "button": 0}]}]})
        except Exception:
            pass

    def close(self, terminate_app: bool = False) -> None:
        """Release touches and end the runner's session with the app. By default the app is
        left running (WebDriverAgent would otherwise terminate it with the session)."""
        self.release_touches()
        if self.s is None:
            return
        try:
            if terminate_app:
                self.c.http.post("session", {"capabilities": {"alwaysMatch": {"bundleId": "", "shouldTerminateApp": True}}})
            self.s.close()  # sessions are opened with shouldTerminateApp off, so this leaves the app running
        except Exception:
            pass
        self.s = None

    # ---- finding -----------------------------------------------------------
    def el(self, *, name: str | None = None, label: str | None = None, predicate: str | None = None):
        assert self.s is not None, "open() an app first"
        q = {}
        if name is not None:
            q["name"] = name
        if label is not None:
            q["label"] = label
        if predicate is not None:
            q["predicate"] = predicate
        return self.s(**q)

    def exists(self, timeout: float = 3, **q) -> bool:
        self.check()
        return bool(self.el(**q).wait(timeout=timeout, raise_error=False))

    def wait(self, timeout: float = 15, what: str = "", **q):
        self.check()
        e = self.el(**q).wait(timeout=timeout, raise_error=False)
        if not e:
            raise FlowError(f"Never saw {what or q} (waited {timeout}s)")
        return e

    def tap(self, timeout: float = 15, what: str = "", pause: float = 1.2, **q) -> None:
        self.log("info", f"Tap {what or q}")
        self.wait(timeout=timeout, what=what, **q).tap()
        time.sleep(pause)

    def tap_if(self, timeout: float = 3, what: str = "", pause: float = 1.2, **q) -> bool:
        e = self.el(**q).wait(timeout=timeout, raise_error=False)
        if not e:
            return False
        self.log("info", f"Tap {what or q}")
        e.tap()
        time.sleep(pause)
        return True

    def type(self, text: str, what: str = "") -> None:
        self.log("info", f"Type {len(text)} chars{' into ' + what if what else ''}")
        self.s.send_keys(text)
        time.sleep(0.6)

    def dismiss_keyboard(self) -> None:
        try:
            self.s.keyboard_dismiss()
        except Exception:
            pass
        time.sleep(0.5)

    def flick(self, direction: str = "up", strength: float = 1.0, settle: float = 1.3) -> None:
        """Quick scroll gesture. 'up' reveals content further down the list. The instant drag
        primitive doesn't scroll Instagram's grids at all; a fast pointer move does."""
        self.check()
        w, h = self.s.window_size()
        span = int(h * 0.5 * max(0.2, min(strength, 1.0)))
        y0, y1 = (int(h * 0.8), int(h * 0.8) - span) if direction == "up" else (int(h * 0.3), int(h * 0.3) + span)
        acts = [
            {"type": "pointerMove", "duration": 0, "x": w // 2, "y": y0},
            {"type": "pointerDown", "button": 0},
            {"type": "pointerMove", "duration": 120, "x": w // 2, "y": y1},
            {"type": "pointerUp", "button": 0},
        ]
        self.c.http.post(f"/session/{self.s.session_id}/actions", {"actions": [
            {"type": "pointer", "id": "finger1", "parameters": {"pointerType": "touch"}, "actions": acts}
        ]})
        if settle:
            time.sleep(settle)

    def swipe_up(self, n: int = 1) -> None:
        for _ in range(n):
            self.flick("up")

    def element_name(self, e) -> str:
        """Accessibility identifier of a found element (the client library hides it)."""
        try:
            return self.c.http.get(f"/session/{self.s.session_id}/element/{e.id}/attribute/name").value or ""
        except Exception:
            return ""

    def drag(self, x1: float, y1: float, x2: float, y2: float, hold_ms: int = 350, move_ms: int = 700, steps: int = 12) -> None:
        """Finger-like drag: press, hold, then move in small steps. Apps treat the instant
        drag primitive as a flick, so stickers don't follow it; this one they do."""
        acts = [
            {"type": "pointerMove", "duration": 0, "x": int(x1), "y": int(y1)},
            {"type": "pointerDown", "button": 0},
            {"type": "pause", "duration": hold_ms},
        ]
        for i in range(1, steps + 1):
            t = i / steps
            acts.append({"type": "pointerMove", "duration": int(move_ms / steps),
                         "x": int(x1 + (x2 - x1) * t), "y": int(y1 + (y2 - y1) * t)})
        acts.append({"type": "pause", "duration": 150})
        acts.append({"type": "pointerUp", "button": 0})
        self.c.http.post(f"/session/{self.s.session_id}/actions", {"actions": [
            {"type": "pointer", "id": "finger1", "parameters": {"pointerType": "touch"}, "actions": acts}
        ]})

    def pinch_out(self, cx: float, cy: float, start_gap: float = 40, end_gap: float = 300, move_ms: int = 600, steps: int = 10) -> None:
        """Two-finger spread centered on (cx, cy), from start_gap apart to end_gap apart."""
        def finger(sign: int, fid: str):
            acts = [
                {"type": "pointerMove", "duration": 0, "x": int(cx + sign * start_gap / 2), "y": int(cy)},
                {"type": "pointerDown", "button": 0},
                {"type": "pause", "duration": 150},
            ]
            for i in range(1, steps + 1):
                gap = start_gap + (end_gap - start_gap) * i / steps
                acts.append({"type": "pointerMove", "duration": int(move_ms / steps), "x": int(cx + sign * gap / 2), "y": int(cy)})
            acts.append({"type": "pause", "duration": 100})
            acts.append({"type": "pointerUp", "button": 0})
            return {"type": "pointer", "id": fid, "parameters": {"pointerType": "touch"}, "actions": acts}
        self.c.http.post(f"/session/{self.s.session_id}/actions", {"actions": [finger(-1, "f1"), finger(1, "f2")]})

    def window_height(self) -> float:
        try:
            return float(self.s.window_size().height)
        except Exception:
            return 844.0

    def screenshot(self) -> bytes:
        buf = io.BytesIO()
        self.c.screenshot().save(buf, "PNG")
        return buf.getvalue()


# ---- Instagram building blocks ---------------------------------------------------

_KEEP_EDITING = "type == 'XCUIElementTypeButton' AND (label == 'Keep editing' OR label == 'Keep Editing')"
_STORY_EDITOR_MSG = "A story editor is open on the phone; share or close it by hand, then run this again"


def ig_story_editor_open(p: Phone) -> bool:
    return p.exists(name="share-to-your-story", timeout=0.8) or p.exists(name="discard-story-preview", timeout=0.5)


def ig_reset(p: Phone) -> None:
    """Get Instagram back to a tab-bar screen if a previous run left a composer or gallery open.

    A story editor is never closed: its X asks "Discard media?" when a sticker is on it, and
    discarding throws away a link someone set up. If that sheet is up, "Keep editing" is pressed
    and the job gives up so the next one can run. The reel gallery is closed after clearing any
    selected clip, so Instagram has nothing to ask about."""
    for _ in range(4):
        if p.exists(name="profile-tab", timeout=1.5):
            return
        if p.tap_if(predicate=_KEEP_EDITING, what="Keep editing (never discard a story)", timeout=0.5):
            raise FlowGiveUp(_STORY_EDITOR_MSG)
        if ig_story_editor_open(p):
            raise FlowGiveUp(_STORY_EDITOR_MSG)
        if p.tap_if(name="discard-reel-preview", what="Discard leftover reel", timeout=1):
            p.tap_if(name="camera-discard-draft", what="Start over", timeout=3)
            continue
        if p.exists(name="gallery-close-button", timeout=1):
            ms = p.el(name="multi-select").wait(timeout=1, raise_error=False)
            if ms and (ms.label or "").lower() == "cancel":
                p.log("info", "Clear the gallery's selected clip")
                ms.tap()
                time.sleep(1.0)
            p.tap(name="gallery-close-button", what="Close leftover gallery", timeout=3)
            if p.tap_if(predicate=_KEEP_EDITING, what="Keep editing", timeout=0.8):
                raise FlowGiveUp("Instagram asked about discarding when closing the gallery; sort it out by hand, then run again")
            continue
        if p.tap_if(name="BackButton", what="Back", timeout=1):
            continue
        p.tap_if(name="Cancel", what="Cancel", timeout=1)
    if not p.exists(name="profile-tab", timeout=2):
        if ig_story_editor_open(p):
            raise FlowGiveUp(_STORY_EDITOR_MSG)
        p.log("warn", "Instagram isn't on a tab screen and nothing closes it; relaunching Instagram")
        p.relaunch(BUNDLES["instagram"])
        if not p.exists(name="profile-tab", timeout=10):
            raise FlowError("Instagram did not come back to a tab screen after relaunch")


def ig_current_handle(p: Phone) -> str:
    """Handle of the account currently active, read from the profile tab header."""
    p.tap(name="profile-tab", what="Profile tab")
    e = p.wait(name="user-switch-title-button", what="profile header")
    return (e.label or "").strip()


def ig_ensure_account(p: Phone, handle: str) -> None:
    current = ig_current_handle(p)
    if current.lower() == handle.lower():
        p.log("info", f"Already on @{handle}")
        return
    p.log("info", f"On @{current}; switching to @{handle}")
    p.tap(name="user-switch-title-button", what="account switcher")
    pred = f"label BEGINSWITH 'INSTAGRAM profile, {handle}'"
    if not p.exists(predicate=pred, timeout=5):
        raise FlowError(f"@{handle} is not one of the accounts on this phone")
    p.tap(predicate=pred, what=f"@{handle} row", pause=3)
    now = ig_current_handle(p)
    if now.lower() != handle.lower():
        raise FlowError(f"Account switch failed: on @{now}, wanted @{handle}")


def _expected_label_parts(spec: ActionSpec) -> tuple[str | None, str | None]:
    """What Instagram's cell label should contain for this media: the date, and for video the M:SS."""
    from datetime import datetime
    extra = spec.extra or {}
    date_part = None
    if extra.get("created_at"):
        try:
            d = datetime.fromisoformat(extra["created_at"])
            date_part = d.strftime("%B %-d, %Y").upper()  # SEPTEMBER 27, 2026
        except Exception:
            pass
    dur_part = None
    if spec.media_kind == "video" and extra.get("duration_s"):
        secs = int(float(extra["duration_s"]))
        dur_part = f"{secs // 60}:{secs % 60:02d}"
    return date_part, dur_part


def _label_matches(label: str, date_part: str | None, dur_part: str | None, kind: str | None = None) -> bool:
    lab = (label or "").upper()
    is_video_cell = "VIDEO" in lab
    if kind == "video" and not is_video_cell:
        return False
    if kind == "photo" and is_video_cell:
        return False
    if date_part and date_part not in lab:
        return False
    if dur_part and "DURATION: " in lab:
        # Instagram floors the duration; accept ±1s
        m, s = dur_part.split(":")
        want = int(m) * 60 + int(s)
        import re
        found = re.search(r"DURATION: (\d+):(\d+)", lab)
        if found:
            got = int(found.group(1)) * 60 + int(found.group(2))
            if abs(got - want) > 1:
                return False
    return True


def ig_gallery_bar(p: Phone):
    """The gallery grid's vertical scroll indicator. It's in the tree even while hidden, and its
    value is the current scroll position as a percentage. Returns (element, pages) or None."""
    import re
    # only multi-page bars; the grid's is the tall one
    for b in p.el(predicate="label BEGINSWITH 'Vertical scroll bar' AND NOT label CONTAINS ' 1 page'").find_elements():
        try:
            if b.bounds.height >= 200:
                m = re.search(r"(\d+) pages", b.label or "")
                return b, (int(m.group(1)) if m else 20)
        except Exception:
            continue
    return None


def _bar_pct(bar) -> float:
    try:
        return float((bar.value or "0%").rstrip("%")) / 100.0
    except ValueError:
        return 0.0


def ig_gallery_position(p: Phone, total: int) -> int | None:
    """Estimated index of the top visible row, from the scroll indicator's percentage."""
    found = ig_gallery_bar(p)
    if found is None or total <= 0:
        return None
    bar, _ = found
    return int(round(_bar_pct(bar) * total))


def ig_scrub_to(p: Phone, frac: float) -> None:
    """Fast scroll with the scroll indicator (the date bubble on the right edge). It shows only
    while the grid moves and disappears about a second after it stops, so: read the bar first,
    nudge the grid, and press the thumb within a fraction of a second, then drag it.
    iOS draws the thumb at the very right edge; its length is track / pages."""
    frac = max(0.0, min(1.0, frac))
    found = ig_gallery_bar(p)
    if found is None:
        p.log("warn", "Scroll indicator not found; falling back to flicks")
        return
    bar, pages = found
    r = bar.bounds
    cur = _bar_pct(bar)
    thumb = max(20.0, r.height / max(pages, 1))
    track = r.height - thumb
    x = r.x + r.width - 4  # the indicator hugs the screen edge
    y_from = r.y + cur * track + thumb / 2
    y_to = r.y + frac * track + thumb / 2
    p.log("info", f"Scrub gallery {cur * 100:.0f}% → {frac * 100:.0f}% (thumb {thumb:.0f}pt at y={y_from:.0f})")
    p.flick("up", strength=0.2, settle=0)  # reveal the indicator (moves ~1%)
    time.sleep(0.2)
    p.drag(x, y_from + 0.01 * track, x, y_to, hold_ms=600, move_ms=1000, steps=20)
    time.sleep(1.0)
    after = ig_gallery_bar(p)
    p.log("info", f"Scrub landed at {(after[0].value if after else '?')}")


def ig_dismiss_draft_prompt(p: Phone) -> bool:
    """Instagram sometimes asks "Continue editing your draft?" when the reel composer opens,
    even after a successful post. "Start new video" clears it and leaves the gallery as it
    should be. Returns True when the prompt was there."""
    if p.tap_if(label="Start new video", what="Start new video (dismiss draft prompt)", timeout=1.0, pause=1.5):
        return True
    return False


def ig_select_album(p: Phone, title: str) -> None:
    """Switch Instagram's gallery from Recents to a Photos album by name (title may carry a
    trailing space on the phone, so match by prefix)."""
    title = title.strip()
    if p.exists(predicate=f"label BEGINSWITH 'Gallery Selection, {title}'", timeout=1.5):
        p.log("info", f"Album '{title}' already selected")
        return
    p.log("info", f"Switch gallery to album '{title}'")
    ig_dismiss_draft_prompt(p)
    # the dropdown button carries the current selection's name
    sel = p.el(predicate="label BEGINSWITH 'Gallery Selection'").wait(timeout=5, raise_error=False)
    if not sel:
        raise FlowError("Gallery album dropdown not found")
    sel.tap()
    time.sleep(1.2)
    if not p.exists(name="menu-item-All albums", timeout=3):
        # the dropdown didn't open: something (the draft prompt) is over the gallery
        if ig_dismiss_draft_prompt(p):
            p.log("info", "Draft prompt was in the way; tapping the dropdown again")
        else:
            p.log("warn", "Dropdown didn't open; tapping it again")
        sel = p.el(predicate="label BEGINSWITH 'Gallery Selection'").wait(timeout=5, raise_error=False) or sel
        sel.tap()
        time.sleep(1.2)
    p.tap(name="menu-item-All albums", what="All albums", timeout=8)
    p.wait(label="Select album", what="album sheet", timeout=8)
    pred = f"label BEGINSWITH '{title}' AND label CONTAINS 'items'"
    if not p.exists(predicate=pred, timeout=4):
        p.tap_if(label="See all", what="See all albums", timeout=2)
        if not p.exists(predicate=pred, timeout=4):
            raise FlowError(f"Album '{title}' not found in Instagram's album list")
    p.tap(predicate=pred, what=f"album '{title}'", pause=2)
    p.wait(predicate=f"label BEGINSWITH 'Gallery Selection, {title}'", what="album grid", timeout=10)


def ig_pick_gallery_item(p: Phone, spec: ActionSpec) -> None:
    """Tap the planned media in Instagram's gallery. Prefers the phone's story album when the
    item is in it (short list, no scrolling); otherwise navigates Recents."""
    extra = spec.extra or {}
    if extra.get("album") and extra.get("album_index") is not None:
        ig_select_album(p, extra["album"])
        return _ig_pick_cell(p, spec, int(extra["album_index"]), total=0)
    if extra.get("albums_required", True):
        name = (extra.get("filename") or "the item").split("/")[-1]
        raise FlowError(f"{name} is not in the phone's queue or story album. Add it in Photos, then run this stage again.")
    if p.exists(predicate="label BEGINSWITH 'Gallery Selection' AND NOT label CONTAINS 'Recents'", timeout=1):
        # a previous run left an album selected; Recents indices only make sense in Recents
        ig_select_album_recents(p)
    return _ig_pick_cell(p, spec, spec.media_index, total=int(extra.get("roll_total") or 0))


def ig_select_album_recents(p: Phone) -> None:
    p.log("info", "Switch gallery back to Recents")
    sel = p.el(predicate="label BEGINSWITH 'Gallery Selection'").wait(timeout=5, raise_error=False)
    if sel:
        sel.tap()
        time.sleep(1.0)
        p.tap(name="menu-item-Recents", what="Recents", timeout=6)
        p.wait(predicate="label BEGINSWITH 'Gallery Selection, Recents'", what="Recents grid", timeout=8)


def _ig_pick_cell(p: Phone, spec: ActionSpec, index: int, total: int) -> None:
    """Tap the planned media in Instagram's gallery grid.

    Cells are named gallery-<kind>-cell-N with N = position in Recents (newest = 0), and their
    label carries the capture date and, for video, the duration. We go to the expected N first,
    verify the label, and if it doesn't match search the neighbouring cells for one that does.
    """
    if index is None:
        raise FlowError("No media position for this item; sync the camera roll and re-plan")
    date_part, dur_part = _expected_label_parts(spec)
    p.log("info", f"Looking for cell #{index + 1}" + (f" ({date_part}{', ' + dur_part if dur_part else ''})" if date_part else ""))

    def cell(n):
        return p.el(predicate=f"name BEGINSWITH 'gallery-' AND name ENDSWITH '-cell-{n}'").wait(timeout=0.8, raise_error=False)

    def visible_indices() -> list[int]:
        out = []
        for e in p.el(predicate="name BEGINSWITH 'gallery-' AND name CONTAINS '-cell-'").find_elements():
            try:
                out.append(int(p.element_name(e).rsplit("-", 1)[1]))
            except Exception:
                pass
        return sorted(out)

    target = None
    found_n = index
    # total == 0 means an album grid: short, no scrubbing
    scrubs = 0
    for _ in range(40):
        e = cell(index)
        if not e:
            # where are we? one call via the scroll indicator; fall back to reading visible cells
            pos = ig_gallery_position(p, total) if total else None
            if pos is None:
                vis = visible_indices()
                pos = vis[0] if vis else None
            if pos is None:
                p.flick("up")
                continue
            gap = index - pos
            # far away: grab the scroll indicator (the date bubble) and drag straight there
            if abs(gap) > 60 and scrubs < 2 and total > 0:
                scrubs += 1
                ig_scrub_to(p, index / total)
                continue
            # close: a full flick moves ~25 cells, so scale it to the distance. Instagram only
            # instantiates the cells on screen, so a target just above the first visible one
            # (gap -1..-3) needs a small scroll *up*, never down.
            if gap > 0:
                p.flick("up", strength=1.0 if gap > 20 else max(0.2, gap / 25))
            elif gap < 0:
                p.flick("down", strength=1.0 if -gap > 20 else max(0.2, -gap / 25))
            else:
                p.flick("down", strength=0.2)  # should be on screen but isn't instantiated yet; nudge
            continue
        if e:
            if _label_matches(e.label, date_part, dur_part, spec.media_kind):
                target = e
                break
            p.log("warn", f"Recents #{index + 1} is '{e.label}', not the planned item; checking neighbours")
            # probe nearby positions, nearest first (Instagram orders by capture date, we by our best guess)
            for delta in (1, -1, 2, -2, 3, -3, 4, -4, 5, -5, 6, -6, 8, -8):
                n = index + delta
                if n < 0:
                    continue
                c = cell(n)
                if c and _label_matches(c.label, date_part, dur_part, spec.media_kind):
                    p.log("info", f"Using Recents #{n + 1} instead: {c.label}")
                    target = c
                    found_n = n
                    break
            if target is not None:
                break
            raise FlowError("Planned media not found near its expected position in Instagram's gallery. "
                            "Sync the camera roll and re-plan.")
        p.swipe_up()
    if target is None:
        raise FlowError(f"Recents #{index + 1} not found in Instagram's gallery after scrolling")
    p.log("info", f"Pick: {target.label}")
    _tap_gallery_cell(p, target, lambda: cell(found_n))


def _tap_gallery_cell(p: Phone, target, refind) -> None:
    """Tap a gallery cell where a finger would land, inside the band between the gallery header
    and Instagram's bottom mode bar. Tap first; only when the tap doesn't open the item is the
    grid nudged, gently, and the tap tried again. If the tap merely selects the clip (the
    multi-select tray), its Next button is pressed instead."""
    H = p.window_height()
    TOP, BOTTOM = 130, H - 120
    # the header is taller on newer Instagram builds (Edits / Drafts / Templates row): measure it
    hdr = p.el(predicate="label BEGINSWITH 'Gallery Selection'").wait(timeout=1.5, raise_error=False)
    if hdr:
        try:
            hr = hdr.bounds
            TOP = max(TOP, int(hr.y + hr.height) + 6)
        except Exception:
            pass

    def bounds(e):
        try:
            return e.bounds
        except Exception:  # stale: the grid re-laid itself out
            return None

    def find_again():
        for _ in range(3):
            e = refind()
            if e:
                r = bounds(e)
                if r:
                    return e, r
            time.sleep(0.7)
        return None, None

    def tap_point(r):
        x = int(r.x + r.width / 2)
        lo, hi = r.y + 24, r.y + r.height - 24
        y = int(min(max(r.y + r.height / 2, TOP + 8), BOTTOM - 8))
        y = int(min(max(y, lo), hi))
        return x, y

    def opened() -> bool:
        return (p.exists(name="sundial-right-chevron-next-button", timeout=2) or p.exists(name="story-trimmer-done-button", timeout=0.5)
                or p.exists(name="share-to-your-story", timeout=0.5) or not p.exists(name="gallery-header-title", timeout=1))

    NEXT = "type == 'XCUIElementTypeButton' AND label == 'Next'"
    r = bounds(target)
    for attempt in range(4):
        if r is None:
            target, r = find_again()
            if r is None:
                raise FlowError("Lost the planned item in the gallery (it moved out of view)")
        x, y = tap_point(r)
        p.log("info", f"Tap item at ({x},{y})" if attempt == 0 else f"Tapping again ({attempt + 1}/4) at ({x},{y})")
        p.s.tap(x, y)
        time.sleep(2.0)
        if opened():
            return
        # the tap selected the clip into the tray instead of opening it: Next opens it
        if p.exists(name="gallery-header-title", timeout=0.5) and p.exists(predicate=NEXT, timeout=0.5):
            p.tap(predicate=NEXT, what="Next (selected clip)", timeout=3, pause=2.5)
            if opened():
                return
        target, r = find_again()
        if r is None:
            raise FlowError("Lost the planned item in the gallery after tapping it")
        if r.y < TOP:
            direction = "down"
        elif r.y + r.height > BOTTOM:
            direction = "up"
        else:
            direction = "down" if r.y + r.height / 2 < H / 2 else "up"
        p.log("info", "Item is under the top edge; scrolling back up a little" if direction == "down" else "Item is under the bottom bar; scrolling down a little")
        p.flick(direction, strength=0.2)
        time.sleep(0.8)
        target, r = find_again()
    raise FlowError("Tapped the planned item but Instagram stayed on the gallery")


def ig_add_link_sticker(p: Phone, link: str, link_text: str) -> None:
    p.tap(name="add-stickers-button", what="Stickers")
    p.tap(name="link-sticker-tray-cell", what="Link sticker", timeout=10)
    p.wait(label="Add link", what="link form")
    time.sleep(0.5)
    p.type(link, "URL")
    if link_text:
        p.tap(label="Customize sticker text", what="Customize sticker text")
        # second text field is the sticker text
        fields = p.el(predicate="type == 'XCUIElementTypeTextField'").find_elements()
        if len(fields) >= 2:
            fields[1].tap()
            time.sleep(0.4)
        p.type(link_text, "sticker text")
    p.tap(name="Done", what="Done (link)")
    p.wait(predicate="label BEGINSWITH 'Link Sticker'", what="link sticker on story", timeout=10)
    ig_place_link_sticker(p)


# Where the link sticker goes on every story: centered, this far down the story canvas.
LINK_STICKER_X_FRAC = 0.50
LINK_STICKER_Y_FRAC = 0.86


def ig_place_link_sticker(p: Phone) -> None:
    """Pinch the link sticker to its maximum size, then drag it to the standard spot near the bottom."""
    sticker_pred = "label BEGINSWITH 'Link Sticker'"

    def sticker():
        return p.wait(predicate=sticker_pred, what="link sticker", timeout=5)

    def center(e):
        b = e.bounds
        return b.x + b.width / 2, b.y + b.height / 2, b.width

    # 1) grow to max with a single wide spread. Instagram caps the size, so one big gesture
    #    saturates it; a second one just shows the overshoot-and-snap-back animation.
    cx, cy, w0 = center(sticker())
    p.log("info", f"Pinch sticker to max (from {w0:.0f}pt)")
    p.pinch_out(cx, cy, start_gap=40, end_gap=380)
    time.sleep(1.5)

    # 2) drag to the standard position on the story canvas
    canvas = p.wait(name="story-media-composition-view", what="story canvas", timeout=5).bounds
    tx = canvas.x + canvas.width * LINK_STICKER_X_FRAC
    ty = canvas.y + canvas.height * LINK_STICKER_Y_FRAC
    for attempt in range(3):
        cx, cy, _ = center(sticker())
        if abs(cx - tx) < 12 and abs(cy - ty) < 12:
            break
        p.log("info", f"Drag sticker from ({cx:.0f},{cy:.0f}) to ({tx:.0f},{ty:.0f})")
        p.drag(cx, cy, tx, ty)
        time.sleep(1.2)
    cx, cy, w = center(sticker())
    p.log("info", f"Sticker placed at ({cx:.0f},{cy:.0f}), width {w:.0f}pt")


# ---- flows -------------------------------------------------------------------------

# ---- stages ------------------------------------------------------------------------
# Every flow is a list of named stages (open -> pick -> compose -> post). The job remembers the
# last stage that passed, so a failed or hand-stopped run continues from where it broke instead
# of starting over, and any single stage can be run on its own while testing.
STAGES = ("open", "pick", "compose", "post")


def run_stages(log: LogFn, spec: ActionSpec, stages: list[tuple[str, object]]):
    """Run `stages` honouring spec.extra['stage_only'] / ['stage_from'].

    A stage function returns None to continue, or (ok, message) to end the flow early (dry run)
    or as the final result. Returns (ok, message, partial)."""
    extra = spec.extra or {}
    names = [n for n, _ in stages]
    only, start = extra.get("stage_only"), extra.get("stage_from")
    for v in (only, start):
        if v and v not in names:
            raise FlowError(f"This flow has no stage '{v}' (stages: {', '.join(names)})")
    if only:
        todo = [(n, f) for n, f in stages if n == only]
    else:
        i = names.index(start) if start else 0
        todo = stages[i:]
        if i:
            log("info", f"Continuing from stage '{start}' (earlier stages already done)")
    for n, f in todo:
        log("info", f"stage-start:{n}")
        r = f()
        log("ok", f"checkpoint:{n}")
        if r is not None:
            ok, message = r
            return ok, message, False
    if only and only != names[-1]:
        return True, f"Stage '{only}' done", True
    return True, "Done", False


def ig_story(p: Phone, spec: ActionSpec):
    if spec.media_index is None:
        raise FlowError("No media chosen")

    # an earlier attempt of this same job got as far as the editor (runner dropped, Stop, ...):
    # the editor it left open is this story, so carry on in it instead of discarding it
    resumable = (spec.extra or {}).get("last_stage") in ("pick", "compose")
    state = {"resume": False}

    def open_():
        p.open(BUNDLES["instagram"])
        if resumable and (ig_story_editor_open(p) or p.exists(predicate=_KEEP_EDITING, timeout=0.5)):
            p.tap_if(predicate=_KEEP_EDITING, what="Keep editing", timeout=0.5)
            p.log("info", "The story editor from the earlier attempt is still open; continuing in it")
            state["resume"] = True
            return
        ig_reset(p)
        ig_ensure_account(p, spec.handle)

    def pick():
        if state["resume"] and p.exists(name="share-to-your-story", timeout=2):
            p.log("info", "Already on the story editor")
            return
        if not p.exists(name="gallery-header-title", timeout=1):
            ig_open_story_gallery(p)
        ig_pick_gallery_item(p, spec)
        if p.tap_if(name="story-trimmer-done-button", what="Trim: Done", timeout=4):
            pass
        p.wait(name="share-to-your-story", what="story editor")

    def compose():
        if not p.exists(name="share-to-your-story", timeout=3):
            raise FlowError("Not on the story editor (run the 'pick' stage first)")
        if spec.link:
            if p.exists(predicate="label BEGINSWITH 'Link Sticker'", timeout=1.5):
                p.log("info", "Link sticker is already on the story")
                return
            ig_add_link_sticker(p, spec.link, spec.link_text)

    def post():
        if not p.exists(name="share-to-your-story", timeout=3):
            raise FlowError("Not on the story editor (run the 'pick' stage first)")
        if (spec.extra or {}).get("dry_run"):
            p.log("info", "DRY RUN: stopping before 'Share to your story'")
            return True, "Dry run reached Share to your story"
        p.tap(name="share-to-your-story", what="Share to your story", pause=0.5)
        # the editor closing is the signal; Instagram uploads in the background
        for _ in range(40):
            if not p.exists(name="share-to-your-story", timeout=0.5):
                return True, "Story shared"
            time.sleep(0.5)
        raise FlowError("Story editor did not close after Share")

    return run_stages(p.log, spec, [("open", open_), ("pick", pick), ("compose", compose), ("post", post)])


def ig_open_story_gallery(p: Phone) -> None:
    """Open the 'Add to story' gallery the way Braden does: Profile tab, then the black + badge
    on the profile picture. Falls back to the Home tab's story tray if the badge isn't there."""
    p.tap(name="profile-tab", what="Profile tab")
    badge = (p.el(name="Prism-Add-to-Story-Badge-20").wait(timeout=4, raise_error=False)
             or p.el(predicate="label == 'Add to story' AND type == 'XCUIElementTypeImage'").wait(timeout=2, raise_error=False))
    if badge:
        r = badge.bounds
        p.log("info", "Tap + on the profile picture (Add to story)")
        p.s.tap(int(r.x + r.width / 2), int(r.y + r.height / 2))
        time.sleep(2.0)
        if not p.exists(name="gallery-header-title", timeout=4):
            # some builds show a chooser first
            p.tap_if(predicate="label == 'Story' OR label == 'Add to story'", what="Story option", timeout=3)
    else:
        p.log("warn", "No + badge on the profile picture; using the Home story tray")
        p.tap(name="mainfeed-tab", what="Home tab")
        p.tap(name="story-tray-add-button", what="Add to your story")
    p.wait(name="gallery-header-title", what="story gallery", timeout=15)


def ig_turn_on_trial(p: Phone) -> None:
    """On the reel share screen: scroll down to Instagram's "Trial" row and switch it on.
    Not every account has the row; that's a give-up, not a retry."""
    # Instagram names the control "trial-switch" (value "0"/"1"); it sits lower on the share sheet
    def find():
        return p.el(name="trial-switch").wait(timeout=2, raise_error=False)

    sw = find()
    for _ in range(3):
        if sw:
            break
        p.flick("up", strength=0.5)
        sw = find()
    if not sw:
        raise FlowGiveUp("Trial isn't available on this account")
    r, H = sw.bounds, p.window_height()
    if r.y < 100 or r.y + r.height > H - 120:
        try:
            sw.scroll("visible")
        except Exception:
            pass
        time.sleep(0.6)
        sw = find() or sw
    if ig_trial_is_on(p):
        p.log("info", "Trial is already on")
        return
    p.log("info", "Turn on Trial")
    sw.tap()
    for _ in range(4):
        time.sleep(1.0)
        if ig_trial_is_on(p):
            p.log("info", "Trial is on")
            return
    raise FlowGiveUp("The Trial switch would not turn on")


def ig_trial_is_on(p: Phone) -> bool:
    """Instagram reports the state two ways: the control's value (0/1) and the row's label,
    which starts with "Checked" or "Not checked". Either saying on counts."""
    sw = p.el(name="trial-switch").wait(timeout=1.5, raise_error=False)
    if sw and str(sw.value) in ("1", "true", "True"):
        return True
    row = p.el(predicate="type == 'XCUIElementTypeSwitch' AND label BEGINSWITH 'Checked, Trial'").wait(timeout=1, raise_error=False)
    return bool(row)


def ig_trial(p: Phone, spec: ActionSpec):
    return ig_reel(p, spec, trial=True)


def ig_reel(p: Phone, spec: ActionSpec, trial: bool = False):
    if spec.media_index is None:
        raise FlowError("No media chosen")

    def open_():
        p.open(BUNDLES["instagram"])
        ig_reset(p)
        ig_ensure_account(p, spec.handle)

    def pick():
        if not p.exists(name="gallery-header-title", timeout=1):
            p.tap(name="profile-add-button", what="Create (+)")
            p.tap(name="creation-reel", what="Reel")
            p.wait(name="gallery-header-title", what="reel gallery")
        ig_dismiss_draft_prompt(p)
        for _ in range(2):
            ms = p.el(name="multi-select").wait(timeout=3, raise_error=False)
            if not (ms and (ms.label or "").lower() == "cancel"):
                break
            p.log("info", "Turn off multi-select")
            ms.tap()
            time.sleep(1)
        ig_pick_gallery_item(p, spec)
        p.tap(name="sundial-right-chevron-next-button", what="Next", timeout=30, pause=2.5)
        p.wait(name="share-sheet-share-button", what="reel share screen", timeout=30)

    def compose():
        if not p.exists(name="share-sheet-share-button", timeout=3):
            raise FlowError("Not on the reel share screen (run the 'pick' stage first)")
        if spec.caption:
            p.tap(name="caption-cell-text-view", what="caption")
            p.type(spec.caption, "caption")
            # The caption opens a full-screen editor; its checkmark is the "OK" button.
            if not p.tap_if(name="OK", what="OK (save caption)", timeout=3):
                p.dismiss_keyboard()
        if trial:
            ig_turn_on_trial(p)

    def post():
        if not p.exists(name="share-sheet-share-button", timeout=3):
            raise FlowError("Not on the reel share screen (run the 'pick' stage first)")
        if trial and not ig_trial_is_on(p):
            raise FlowGiveUp("Trial is off on the share screen; not posting it as a normal reel")
        if (spec.extra or {}).get("dry_run"):
            p.log("info", "DRY RUN: stopping before 'Share'")
            return True, "Dry run reached Share"
        p.tap(name="share-sheet-share-button", what="Share", timeout=20, pause=0.5)
        # Posted = the share screen is gone; Instagram uploads in the background and the next
        # job starts right away. A pop-up (the trial reel notice, for one) can swallow the tap,
        # so a share screen that is still up gets Share pressed again.
        def posted() -> bool:
            return not p.exists(name="share-sheet-share-button", timeout=0.5)
        for attempt in range(4):
            for _ in range(8):
                if posted():
                    return True, ("Trial reel shared (uploading)" if trial else "Reel shared (uploading)")
                time.sleep(0.5)
            # clear a pop-up if one is sitting over the sheet, then press Share again
            p.tap_if(predicate="type == 'XCUIElementTypeButton' AND (label == 'OK' OR label == 'Got it' OR label == 'Continue' OR label == 'Share anyway')",
                     what="dismiss pop-up", timeout=0.5, pause=0.8)
            if p.exists(name="share-sheet-share-button", timeout=1.5):
                p.log("info", f"Share screen still up; pressing Share again ({attempt + 2}/4)")
                p.tap(name="share-sheet-share-button", what="Share", timeout=5, pause=0.5)
        if posted():
            return True, ("Trial reel shared (uploading)" if trial else "Reel shared (uploading)")
        raise FlowError("Did not leave the share screen after Share")

    return run_stages(p.log, spec, [("open", open_), ("pick", pick), ("compose", compose), ("post", post)])


# ---- TikTok -----------------------------------------------------------------------
# Mapped on-device 2026-09-28. Path: Create (+) -> Upload (bottom left) -> "Recents"
# dropdown top-middle to choose an album -> tap the item -> Next (TikTok may auto-add a
# sound; let it settle) -> Next -> caption -> Post (top right).
# TikTok's picker cells are unlabeled; only the duration overlay ("00:21") is a StaticText.
# The album is laid out oldest-first, 3 per row, ending at the newest, so an item is found
# by counting back from the end (the app knows the album's members and capture order),
# then cross-checked against the duration overlay (video) or its absence (photo).

def tt_select_album(p: Phone, title: str) -> None:
    title = title.strip()
    cur = p.el(predicate="type == 'XCUIElementTypeButton' AND (label == 'Recents' OR label CONTAINS 'Stories' OR label CONTAINS 'Queue')").wait(timeout=5, raise_error=False)
    if cur and (cur.label or "").strip() == title:
        return
    p.log("info", f"TikTok: switch picker to album '{title}'")
    if not cur:
        raise FlowError("TikTok album dropdown not found")
    cur.tap()
    time.sleep(1.2)
    p.tap(predicate=f"type == 'XCUIElementTypeStaticText' AND label BEGINSWITH '{title}'", what=f"album '{title}'", timeout=6)


def tt_pick_cell(p: Phone, spec: ActionSpec, from_end: int, count: int) -> None:
    """Tap the item that is `from_end` places before the newest (0 = newest) in an album of
    `count` items, using the grid lattice, then verify with the duration overlay."""
    durs = p.el(predicate="type == 'XCUIElementTypeStaticText' AND label MATCHES '^\\\\d\\\\d:\\\\d\\\\d$'").find_elements()
    overlays = [(e.label, e.bounds) for e in durs]
    if not overlays:
        raise FlowError("TikTok gallery shows no cells (need at least one video in the album to read the grid)")
    # lattice: 3 columns; overlays sit at the bottom-right inside their cell
    cols = 3
    xs = sorted({round(b.x + b.width) for _, b in overlays})
    cell_w = 130.0
    ys = sorted({round(b.y + b.height) for _, b in overlays})
    pitch = min((b - a) for a, b in zip(ys, ys[1:])) if len(ys) > 1 else 103.0
    bottom_row_y = ys[-1]  # bottom edge of the lowest visible row
    last_index = count - 1
    target = last_index - from_end
    if target < 0:
        raise FlowError("Item position is before the start of the album")
    rows_up = last_index // cols - target // cols
    col = target % cols
    cy = bottom_row_y - rows_up * pitch - pitch / 2
    cx = col * cell_w + cell_w / 2
    # verification
    want = None
    if spec.media_kind == "video" and (spec.extra or {}).get("duration_s"):
        sec = int(float(spec.extra["duration_s"]))
        want = {f"{s // 60:02d}:{s % 60:02d}" for s in (sec - 1, sec, sec + 1) if s >= 0}
    here = [lab for lab, b in overlays if abs((b.y + b.height) - (cy + pitch / 2)) < pitch / 2 and abs((b.x + b.width) - (cx + cell_w / 2)) < cell_w / 2]
    if want is not None:
        if not here or here[0] not in want:
            raise FlowError(f"Cell {from_end + 1} from the end shows {here or 'no duration'}, expected {sorted(want)}; re-sync the album and re-plan")
    elif here:
        raise FlowError(f"Cell {from_end + 1} from the end is a video ({here[0]}) but a photo was planned")
    if cy < 60:
        raise FlowError("Planned item is scrolled out of view; keep the queue album short")
    p.log("info", f"TikTok: pick cell {from_end + 1} from the end" + (f" ({here[0]})" if here else " (photo)"))
    p.s.tap(int(cx), int(cy))
    time.sleep(1.5)


def tt_post(p: Phone, spec: ActionSpec):
    if spec.media_index is None:
        raise FlowError("No media chosen")
    extra = spec.extra or {}
    album = extra.get("album")
    if not album or extra.get("album_from_end") is None:
        raise FlowError("TikTok posts need the item in the queue or story album")
    p.open(BUNDLES["tiktok"])
    p.tap(predicate="label == 'Create' AND type == 'XCUIElementTypeButton'", what="Create (+)")
    p.tap(label="Upload", what="Upload", timeout=15)
    p.wait(label="Next", what="gallery", timeout=15)
    tt_select_album(p, album)
    time.sleep(1.0)
    tt_pick_cell(p, spec, int(extra["album_from_end"]), int(extra.get("album_count") or 0))
    p.tap(label="Next", what="Next (to editor)", timeout=15, pause=2)
    # TikTok may detect no speech and auto-add a sound a few seconds in; let it settle
    p.wait(label="Next", what="editor", timeout=30)
    time.sleep(5)
    p.tap(label="Next", what="Next (to caption)", timeout=15, pause=3)
    return False, "TikTok post flow: caption/post screen not mapped yet"


def tt_story(p: Phone, spec: ActionSpec):
    p.open(BUNDLES["tiktok"])
    return False, "TikTok story flow not written yet"


def x_post(p: Phone, spec: ActionSpec):
    p.open(BUNDLES["x"])
    return False, "X post flow not written yet"


FLOWS = {
    "ig_reel": ig_reel,
    "ig_trial": ig_trial,
    "ig_story": ig_story,
    "tt_post": tt_post,
    "tt_story": tt_story,
    "x_post": x_post,
}


def run(driver, spec: ActionSpec, log: LogFn) -> ActionResult:
    if spec.platform == "tiktok":
        # TikTok freezes and misbehaves when the harness attaches; drive it blind instead.
        from .ios_blind import run_blind
        return run_blind(driver, spec, log)
    phone = Phone(driver.base, log)
    fn = FLOWS.get(spec.action)
    if fn is None:
        return ActionResult(False, f"No flow for {spec.action}")
    partial = False
    retry = True
    try:
        extra = spec.extra or {}
        first = extra.get("stage_only") or extra.get("stage_from")
        if first and first != "open":
            phone.attach()  # the 'open' stage isn't running, so work with what's on screen
        r = fn(phone, spec)
        ok, message = r[0], r[1]
        partial = bool(r[2]) if len(r) > 2 else False
    except FlowGiveUp as e:
        ok, message, retry = False, str(e), False
    except FlowError as e:
        ok, message = False, str(e)
    except Exception:
        phone.close()
        raise
    shot = None
    try:
        shot = phone.screenshot()
    except Exception:
        pass
    # stay in the app: the next job on this phone opens whatever it needs itself
    phone.close()  # release any held touch, detach from the app
    return ActionResult(ok, message, shot, partial=partial, retry=retry)
