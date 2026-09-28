"""Drive the iPhone Photos app to put items into / take them out of an album.

Mapped on-device (iOS 26.6, 2026-09-28). Photos labels every grid cell like
    "Photo, September 06, 2:11 PM"  /  "Video, four seconds, September 06, 2:08 PM"
(with a narrow no-break space before AM/PM), so an item is identified by kind + capture
time to the minute, which the app knows exactly from the phone's Photos index.

Adding: Search tab -> type "September 6, 2026" -> Select -> tap the matching cell(s) ->
More -> "Add to <album>" (or "Add to Album" -> album tile / "+" to create it).
Removing: Collections tab -> album tile -> Select -> tap the cell -> trash -> "Remove from Album".
"""

from __future__ import annotations

import json
import re
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime

from .ios_flows import FlowError, Phone

PHOTOS_BUNDLE = "com.apple.mobileslideshow"
NNBSP = " "

_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
         "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty"]


def _words(n: int) -> str:
    if n < 20:
        return _ONES[n]
    return _TENS[n // 10] + ("" if n % 10 == 0 else "-" + _ONES[n % 10])


@dataclass
class QueueItem:
    media_id: int
    filename: str
    kind: str  # photo | video
    created_at: datetime
    duration_s: float = 0.0

    @property
    def day_query(self) -> str:  # what to type into Photos search: the day, narrowed to videos/photos
        day = self.created_at.strftime("%B %-d, %Y")
        return f"{day} videos" if self.kind == "video" else f"{day} photos"

    @property
    def cell_predicate(self) -> str:  # finds this exact item in the grid without a full tree dump
        kind = "Video" if self.kind == "video" else "Photo"
        return f"label BEGINSWITH '{kind}' AND label CONTAINS '{self.time_part.rsplit(' ', 1)[0]}'"

    @property
    def time_part(self) -> str:  # "September 06, 2:11 PM" as Photos labels it (space-normalised)
        return self.created_at.strftime("%B %d, %-I:%M %p")

    def matches(self, label: str) -> bool:
        lab = (label or "").replace(NNBSP, " ")
        kind_ok = lab.startswith("Video") if self.kind == "video" else (lab.startswith("Photo") or lab.startswith("Live Photo"))
        if not kind_ok or self.time_part not in lab:
            return False
        if self.kind == "video" and 0 < self.duration_s < 60:
            secs = int(self.duration_s)
            # Photos floors; accept +-1
            cands = {f"{_words(s)} second" for s in (secs - 1, secs, secs + 1) if s >= 0}
            if not any(cw in lab for cw in cands):
                return False
        return True


# ---- helpers ---------------------------------------------------------------------

def _grid_cells(p: Phone) -> list[tuple[str, dict]]:
    """(label, rect) for every asset cell in the current Photos grid, on-screen or not."""
    src = json.loads(urllib.request.urlopen(p.base + "/source?format=json", timeout=60).read())["value"]
    out = []
    def walk(n):
        if n.get("name") == "PXGGridLayout-Info" and n.get("label"):
            out.append((n["label"], n.get("rect") or {}))
        for ch in n.get("children", []) or []:
            walk(ch)
    walk(src)
    # de-duplicate (the tree can list the same cell twice)
    seen, uniq = set(), []
    for lab, r in out:
        key = (lab, r.get("x"), r.get("y"))
        if key not in seen:
            seen.add(key)
            uniq.append((lab, r))
    return uniq


def _tap_rect(p: Phone, r: dict) -> None:
    p.s.tap(int(r["x"] + r["width"] / 2), int(r["y"] + r["height"] / 2))
    time.sleep(0.8)


def _press_search_key(p: Phone) -> None:
    """Press the keyboard's blue search key so Photos applies the typed filter (not just the
    suggestion list)."""
    if p.tap_if(label="search", what="search key", timeout=3, pause=1.5):
        return
    if p.tap_if(name="Search", what="search key", timeout=1.5, pause=1.5):
        return
    p.log("info", "Press return to search")
    p.s.send_keys("\n")
    time.sleep(1.5)


def _caption(p: Phone) -> str:
    e = p.el(name="PXBarItemIdentifierSelectModeCaption").wait(timeout=2, raise_error=False)
    return (e.label or "") if e else ""


def photos_reset(p: Phone) -> None:
    """Back to the Library tab with nothing selected and no search open."""
    for _ in range(4):
        if p.exists(name="LibraryTab", timeout=1) and p.exists(name="CollectionsTab", timeout=1):
            return
        for name in ("Cancel", "xmark", "Close"):
            if p.tap_if(name=name, timeout=0.8, pause=0.8):
                break
        else:
            p.tap_if(name="Library", timeout=0.8, pause=0.8) or p.tap_if(label="Back", timeout=0.8, pause=0.8)
    if not p.exists(name="LibraryTab", timeout=1):
        p.open(PHOTOS_BUNDLE)


def _select_cells_for(p: Phone, wanted: list[QueueItem]) -> list[QueueItem]:
    """In Select mode on the current grid, tap the cells matching `wanted`. Returns those found.

    Each item is looked up by an exact predicate (kind + capture minute, then length checked on
    the label), which is fast and sees loaded cells even off-screen; the grid is scrolled only
    when nothing matched."""
    found: list[QueueItem] = []
    H = p.window_height()
    for attempt in range(10):
        remaining = [w for w in wanted if w not in found]
        progressed = False
        for w in remaining:
            for e in p.s(predicate=w.cell_predicate).find_elements():
                lab = e.label or ""
                if not w.matches(lab):
                    continue
                r = e.bounds
                if r.y < 0 or r.y + r.height > H - 80:
                    break  # loaded but off-screen: scroll it in first
                p.log("info", f"Select {w.filename.split('/')[-1]} ({w.time_part})")
                _tap_rect(p, {"x": r.x, "y": r.y, "width": r.width, "height": r.height})
                found.append(w)
                progressed = True
                break
        if len(found) == len(wanted):
            break
        if not progressed:
            p.flick("up", strength=0.6)
    return found


# ---- public flows ----------------------------------------------------------------

def photos_add_to_album(p: Phone, items: list[QueueItem], album: str) -> list[QueueItem]:
    """Add items to a Photos album, creating the album if needed. Returns the items added."""
    p.open(PHOTOS_BUNDLE)
    photos_reset(p)
    added: list[QueueItem] = []
    by_day: dict[str, list[QueueItem]] = {}
    for it in items:
        by_day.setdefault(it.day_query, []).append(it)

    for day, group in by_day.items():
        p.log("info", f"Photos: search '{day}' for {len(group)} item(s)")
        p.tap(name="SearchTab", what="Search tab")
        p.type(day, "search")
        _press_search_key(p)
        if not p.exists(predicate="label BEGINSWITH 'Photos (' OR label ENDSWITH 'Results' OR label ENDSWITH 'Result'", timeout=8):
            p.log("warn", f"No results for '{day}'")
            photos_reset(p)
            continue
        p.tap(name="Select", what="Select")
        picked = _select_cells_for(p, group)
        if not picked:
            p.log("warn", f"None of the {len(group)} item(s) for {day} were found in Photos search")
            photos_reset(p)
            continue
        cap = _caption(p)
        p.log("info", f"Photos: {cap}")
        p.tap(name="PXBarItemIdentifierSelectModeMenu", what="More")
        quick = f"Add to “{album}”"
        if p.tap_if(predicate=f"label == '{quick}' OR label BEGINSWITH 'Add to “{album}'", what=quick, timeout=2):
            pass
        else:
            p.tap(label="Add to Album", what="Add to Album")
            tile = p.el(predicate=f"label BEGINSWITH '{album} Album'").wait(timeout=4, raise_error=False)
            if tile:
                p.log("info", f"Album '{album}'")
                tile.tap()
                time.sleep(1.5)
            else:
                p.log("info", f"Create album '{album}'")
                p.tap(name="Add", what="+ New album")
                f = p.wait(name="LemonadeCollectionCustomizationTitleField", what="album name field")
                f.tap()
                time.sleep(0.4)
                p.type(album, "album name")
                p.tap(name="LemonadeCollectionCustomizationNavigationViewCreateButton", what="Create", timeout=5)
        time.sleep(1.5)
        added.extend(picked)
        photos_reset(p)
    p.home()
    return added


def _open_album(p: Phone, album: str) -> bool:
    p.open(PHOTOS_BUNDLE)
    photos_reset(p)
    p.tap(name="CollectionsTab", what="Collections tab")
    tile = None
    for _ in range(6):
        tile = p.el(predicate=f"label BEGINSWITH '{album} Album'").wait(timeout=1.5, raise_error=False)
        if tile:
            break
        p.flick("up", strength=0.7)
    if not tile:
        p.log("warn", f"Album '{album}' not found in Collections")
        p.home()
        return False
    p.log("info", f"Open album: {tile.label}")
    tile.tap()
    time.sleep(1.5)
    return True


def _remove_selected(p: Phone) -> None:
    # the trash is the 'Delete' button in the bottom bar (there's also one inside the More menu)
    dels = p.el(predicate="label == 'Delete' AND type == 'XCUIElementTypeButton'").find_elements()
    if not dels:
        raise FlowError("Trash button not found in album")
    max(dels, key=lambda b: b.bounds.y).tap()
    time.sleep(1.2)
    p.tap(label="Remove from Album", what="Remove from Album", timeout=5)
    time.sleep(1.2)
    p.tap_if(label="Remove from Album", what="Confirm remove", timeout=2)  # some versions confirm


def photos_clear_album(p: Phone, album: str) -> bool:
    """Remove every item from an album with Select All (the items stay in the library)."""
    if not _open_album(p, album):
        return False
    if not _grid_cells(p):
        p.log("info", f"Album '{album}' is already empty")
        photos_reset(p)
        p.home()
        return True
    p.tap(name="Select", what="Select")
    p.tap(label="Select All", what="Select All", timeout=5)
    p.log("info", f"Photos: {_caption(p)}")
    _remove_selected(p)
    photos_reset(p)
    p.home()
    return True


def photos_remove_from_album(p: Phone, item: QueueItem, album: str) -> bool:
    """Remove one item from an album (the item stays in the library)."""
    if not _open_album(p, album):
        return False
    p.tap(name="Select", what="Select")
    found = _select_cells_for(p, [item])
    if not found:
        p.log("warn", f"{item.filename} not in album '{album}' on the phone")
        photos_reset(p)
        p.home()
        return False
    _remove_selected(p)
    photos_reset(p)
    p.home()
    return True


__all__ = ["QueueItem", "photos_add_to_album", "photos_remove_from_album", "photos_clear_album", "re"]
