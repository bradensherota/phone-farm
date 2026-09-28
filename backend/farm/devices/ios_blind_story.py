"""TikTok story, driven blind (see ios_blind.py).

Mapped from Braden's recording on the 17 Pro Max (2026-09-28):
  Add to Story gallery (Recents dropdown, All/Favorites/Photos/Videos) -> tap the item ->
  editor (right rail: gear, Aa, stickers, music, effects) -> stickers -> "Link" in the
  Recommended row -> Add link form (Link, Display text, Done) -> sticker appears top-centre ->
  spread to max, drag to the standard spot -> "Your Story".
"""

from __future__ import annotations

import re
import time

from .base import ActionSpec
from .ios_blind import BlindPhone, _lattice_pick, tt_dismiss_prompt
from .ios_flows import FlowError

# Where the link sticker ends up: centred, this far down the screen (from the reference shot).
STORY_STICKER_Y_FRAC = 0.78


def _spread(bp: BlindPhone, cx: float, cy: float, start_gap: float = 40, end_gap: float = 420, move_ms: int = 600, steps: int = 12) -> None:
    """Horizontal two-finger spread centred on (cx, cy)."""
    def finger(sign: int, fid: str):
        acts = [
            {"type": "pointerMove", "duration": 0, "x": int(cx + sign * start_gap / 2), "y": int(cy)},
            {"type": "pointerDown", "button": 0},
            {"type": "pause", "duration": 150},
        ]
        for i in range(1, steps + 1):
            gap = start_gap + (end_gap - start_gap) * i / steps
            x = max(4, min(bp.pt_w - 4, cx + sign * gap / 2))
            acts.append({"type": "pointerMove", "duration": int(move_ms / steps), "x": int(x), "y": int(cy)})
        acts.append({"type": "pause", "duration": 100})
        acts.append({"type": "pointerUp", "button": 0})
        return {"type": "pointer", "id": fid, "parameters": {"pointerType": "touch"}, "actions": acts}
    bp.c.http.post(f"/session/{bp.s.session_id}/actions", {"actions": [finger(-1, "f1"), finger(1, "f2")]})
    time.sleep(1.2)


def _drag(bp: BlindPhone, x1, y1, x2, y2, hold_ms=350, move_ms=700, steps=12) -> None:
    acts = [{"type": "pointerMove", "duration": 0, "x": int(x1), "y": int(y1)}, {"type": "pointerDown", "button": 0}, {"type": "pause", "duration": hold_ms}]
    for i in range(1, steps + 1):
        t = i / steps
        acts.append({"type": "pointerMove", "duration": int(move_ms / steps), "x": int(x1 + (x2 - x1) * t), "y": int(y1 + (y2 - y1) * t)})
    acts.append({"type": "pause", "duration": 150})
    acts.append({"type": "pointerUp", "button": 0})
    bp.c.http.post(f"/session/{bp.s.session_id}/actions", {"actions": [{"type": "pointer", "id": "f1", "parameters": {"pointerType": "touch"}, "actions": acts}]})
    time.sleep(1.2)


def _find_sticker(bp: BlindPhone, text: str):
    """The link sticker shows its display text next to a link icon; find that word."""
    words = bp.read()
    w = bp.find(rf"{re.escape(text)}$", words) or bp.find(re.escape(text[:8]), words)
    return w


def tt_story_blind(bp: BlindPhone, spec: ActionSpec, open_gallery) -> tuple[bool, str]:
    """`open_gallery(bp)` must leave TikTok on the 'Add to Story' gallery."""
    extra = spec.extra or {}
    album = (extra.get("album") or "").strip()
    if not album or extra.get("album_from_end") is None:
        raise FlowError("TikTok stories need the item in the queue or story album")
    if not spec.link:
        raise FlowError("TikTok story needs a link")
    W, H = bp.pt_w, bp.pt_h
    open_gallery(bp)
    bp.wait_for(r"^Add to Story$", timeout=20, what="Add to Story gallery")
    # album
    top = (0, 0, W, 0.35 * H)
    cur = bp.find(rf"^{re.escape(album)}", region=top) or bp.find(r"^Recents", region=top)
    if not cur:
        raise FlowError("Story gallery album dropdown not found")
    if not cur.text.startswith(album):
        bp.tap_word(cur, "album dropdown", pause=1.5)
        bp.tap_text(rf"^{re.escape(album)}", timeout=8, what=f"album '{album}'", pause=2.0)
    words = bp.read()
    cx, cy = _lattice_pick(bp, words, int(extra["album_from_end"]), int(extra.get("album_count") or 0),
                           float(extra.get("duration_s") or 0) or None, spec.media_kind or "video")
    bp.tap(cx, cy, "planned item", pause=3.0)
    # editor: the right rail has "Aa" (text); the sticker button is the next icon below it
    aa = bp.wait_for(r"^Aa$", timeout=20, what="story editor")
    pitch = max(44.0, aa.h * 3.2)
    opened = False
    for k in (1, 2):
        bp.tap(aa.cx, aa.cy + pitch * k, "stickers", pause=2.0)
        if bp.find(r"^(Mention|Hashtag|Recommended|Search GIFs)", bp.read()):
            opened = True
            break
        bp.tap(35, 90, "close", pause=1.0)  # in case we opened text/music instead
    if not opened:
        raise FlowError("Sticker tray did not open")
    # Link sticker in the Recommended rows (may be cut off at the right edge)
    link = bp.find(r"^Link$|^Li$|Link$", bp._words)
    if link is None:
        row = bp.find(r"^(Mention|Hashtag)", bp._words)
        y = row.cy + (row.h * 2.2) if row else H * 0.45
        bp.swipe(W - 30, y, 60, y, 0.3)
        link = bp.wait_for(r"^Link$|^Li$|Link$", timeout=5, what="Link sticker")
    bp.tap_word(link, "Link sticker", pause=2.0)
    # Add link form
    bp.wait_for(r"^Add link$", timeout=10, what="Add link form")
    lab = bp.find(r"^Link$", bp._words)
    if lab is None:
        raise FlowError("Link field label not found")
    bp.tap(W / 2, lab.cy + lab.h * 2.4, "Link field", pause=0.8)
    bp.type_keys(spec.link)
    dl = bp.find(r"^Display text$", bp.read())
    if dl is None:
        raise FlowError("Display text field not found")
    bp.tap(W / 2, dl.cy + dl.h * 2.4, "Display text field", pause=0.8)
    if spec.link_text:
        # clear any default ("My link") then type ours
        bp.c.http.post(f"/session/{bp.s.session_id}/wda/keys", {"value": ["\b"] * 20, "frequency": 60})
        bp.type_keys(spec.link_text)
    bp.tap_text(r"^Done$", timeout=8, what="Done", pause=2.5)
    # sticker placement: spread to max (several times, until it stops growing), then drag down
    label = spec.link_text or "My link"
    st = _find_sticker(bp, label)
    if st is None:
        raise FlowError("Link sticker not visible after Done")
    last_w = st.w
    for i in range(5):
        _spread(bp, st.cx, st.cy)
        st2 = _find_sticker(bp, label)
        if st2 is None:
            break
        bp.log("info", f"Sticker width {st2.w:.0f}pt")
        if st2.w <= last_w + 4:
            st = st2
            break
        last_w, st = st2.w, st2
    tx, ty = W / 2, H * STORY_STICKER_Y_FRAC
    for _ in range(3):
        st = _find_sticker(bp, label) or st
        if abs(st.cx - tx) < 14 and abs(st.cy - ty) < 14:
            break
        bp.log("info", f"Drag sticker ({st.cx:.0f},{st.cy:.0f}) → ({tx:.0f},{ty:.0f})")
        _drag(bp, st.cx, st.cy, tx, ty)
    # share
    btn = bp.wait_for(r"^Your St", timeout=10, region=(0, H * 0.8, W, H), what="Your Story button")
    bp.tap_word(btn, "Your Story", pause=4.0)
    for _ in range(4):
        words = bp.read()
        if tt_dismiss_prompt(bp, words):
            continue
        if not bp.find(r"^Your St", words, region=(0, H * 0.8, W, H)):
            break
        time.sleep(2)
    if bp.find(r"^Your St", bp.read(), region=(0, H * 0.8, W, H)):
        raise FlowError("Still on the story editor after tapping Your Story")
    return True, "TikTok story shared"
