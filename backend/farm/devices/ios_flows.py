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


class FlowError(Exception):
    pass


class Phone:
    """Thin convenience wrapper around a facebook-wda client for one phone."""

    def __init__(self, base_url: str, log: LogFn):
        self.base = base_url
        self.c = wda.Client(base_url)
        self.c.timeout = 60
        self.log = log
        self.s: wda.Session | None = None

    # ---- app ---------------------------------------------------------------
    def open(self, bundle_id: str) -> None:
        self.log("info", f"Open {bundle_id}")
        self.s = self.c.session(bundle_id)
        time.sleep(2.5)

    def home(self) -> None:
        try:
            self.c.home()
        except Exception:
            pass

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
        return bool(self.el(**q).wait(timeout=timeout, raise_error=False))

    def wait(self, timeout: float = 15, what: str = "", **q):
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

    def screenshot(self) -> bytes:
        buf = io.BytesIO()
        self.c.screenshot().save(buf, "PNG")
        return buf.getvalue()


# ---- Instagram building blocks ---------------------------------------------------

def ig_reset(p: Phone) -> None:
    """Get Instagram back to a tab-bar screen if a previous run left a composer or gallery open."""
    for _ in range(3):
        if p.exists(name="profile-tab", timeout=1.5):
            return
        if p.tap_if(name="discard-story-preview", what="Discard leftover story", timeout=1):
            p.tap_if(predicate="label CONTAINS 'Discard'", what="Confirm discard", timeout=3)
            continue
        if p.tap_if(name="discard-reel-preview", what="Discard leftover reel", timeout=1):
            p.tap_if(name="camera-discard-draft", what="Start over", timeout=3)
            continue
        if p.tap_if(name="gallery-close-button", what="Close leftover gallery", timeout=1):
            continue
        if p.tap_if(name="BackButton", what="Back", timeout=1):
            continue
        p.tap_if(name="Cancel", what="Cancel", timeout=1)
    if not p.exists(name="profile-tab", timeout=2):
        p.log("warn", "Instagram isn't on a tab screen; relaunching")
        p.open(BUNDLES["instagram"])


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


def ig_select_album(p: Phone, title: str) -> None:
    """Switch Instagram's gallery from Recents to a Photos album by name (title may carry a
    trailing space on the phone, so match by prefix)."""
    title = title.strip()
    if p.exists(predicate=f"label BEGINSWITH 'Gallery Selection, {title}'", timeout=1.5):
        p.log("info", f"Album '{title}' already selected")
        return
    p.log("info", f"Switch gallery to album '{title}'")
    # the dropdown button carries the current selection's name
    sel = p.el(predicate="label BEGINSWITH 'Gallery Selection'").wait(timeout=5, raise_error=False)
    if not sel:
        raise FlowError("Gallery album dropdown not found")
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
            # close: a full flick moves ~25 cells, so scale it to the distance
            if gap > 0:
                p.flick("up", strength=1.0 if gap > 20 else max(0.25, gap / 25))
            elif gap < -3:
                p.flick("down", strength=1.0 if -gap > 20 else max(0.25, -gap / 25))
            else:
                p.flick("up", strength=0.25)  # should be on screen but isn't instantiated yet; nudge
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
                    break
            if target is not None:
                break
            raise FlowError("Planned media not found near its expected position in Instagram's gallery. "
                            "Sync the camera roll and re-plan.")
        p.swipe_up()
    if target is None:
        raise FlowError(f"Recents #{index + 1} not found in Instagram's gallery after scrolling")
    p.log("info", f"Pick: {target.label}")
    target.tap()
    time.sleep(2.5)


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

def ig_story(p: Phone, spec: ActionSpec):
    if spec.media_index is None:
        raise FlowError("No media chosen")
    p.open(BUNDLES["instagram"])
    ig_reset(p)
    ig_ensure_account(p, spec.handle)
    p.tap(name="mainfeed-tab", what="Home tab")
    p.tap(name="story-tray-add-button", what="Add to your story")
    p.wait(name="gallery-header-title", what="story gallery")
    ig_pick_gallery_item(p, spec)
    if p.tap_if(name="story-trimmer-done-button", what="Trim: Done", timeout=4):
        pass
    p.wait(name="share-to-your-story", what="story editor")
    if spec.link:
        ig_add_link_sticker(p, spec.link, spec.link_text)
    p.tap(name="share-to-your-story", what="Share to your story", pause=2)
    p.wait(name="story-tray-add-button", what="feed after sharing", timeout=40)
    return True, "Story shared"


def ig_reel(p: Phone, spec: ActionSpec):
    if spec.media_index is None:
        raise FlowError("No media chosen")
    p.open(BUNDLES["instagram"])
    ig_reset(p)
    ig_ensure_account(p, spec.handle)
    p.tap(name="profile-add-button", what="Create (+)")
    p.tap(name="creation-reel", what="Reel")
    p.wait(name="gallery-header-title", what="reel gallery")
    ms = p.el(name="multi-select").wait(timeout=3, raise_error=False)
    if ms and (ms.label or "").lower() == "cancel":
        p.log("info", "Turn off multi-select")
        ms.tap()
        time.sleep(1)
    ig_pick_gallery_item(p, spec)
    p.tap(name="sundial-right-chevron-next-button", what="Next", timeout=30, pause=2.5)
    p.wait(name="share-sheet-share-button", what="reel share screen", timeout=30)
    if spec.caption:
        p.tap(name="caption-cell-text-view", what="caption")
        p.type(spec.caption, "caption")
        # The caption opens a full-screen editor; its checkmark is the "OK" button.
        if not p.tap_if(name="OK", what="OK (save caption)", timeout=3):
            p.dismiss_keyboard()
    p.tap(name="share-sheet-share-button", what="Share", timeout=20, pause=3)
    # Instagram returns to the feed/profile while it uploads in the background
    for _ in range(30):
        if p.exists(name="mainfeed-tab", timeout=1) or p.exists(name="profile-tab", timeout=1):
            return True, "Reel shared (uploading)"
        time.sleep(1)
    raise FlowError("Did not return to the feed after Share")


def tt_post(p: Phone, spec: ActionSpec):
    p.open(BUNDLES["tiktok"])
    return False, "TikTok post flow not written yet"


def tt_story(p: Phone, spec: ActionSpec):
    p.open(BUNDLES["tiktok"])
    return False, "TikTok story flow not written yet"


def x_post(p: Phone, spec: ActionSpec):
    p.open(BUNDLES["x"])
    return False, "X post flow not written yet"


FLOWS = {
    "ig_reel": ig_reel,
    "ig_story": ig_story,
    "tt_post": tt_post,
    "tt_story": tt_story,
    "x_post": x_post,
}


def run(driver, spec: ActionSpec, log: LogFn) -> ActionResult:
    phone = Phone(driver.base, log)
    fn = FLOWS.get(spec.action)
    if fn is None:
        return ActionResult(False, f"No flow for {spec.action}")
    try:
        ok, message = fn(phone, spec)
    except FlowError as e:
        ok, message = False, str(e)
    shot = None
    try:
        shot = phone.screenshot()
    except Exception:
        pass
    if ok:
        phone.home()
    return ActionResult(ok, message, shot)
