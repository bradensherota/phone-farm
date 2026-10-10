"""Files the app keeps (thumbnails, hover clips, screenshots) are recorded by full path. When the
data folder moves with an update or a reinstall, those paths go stale while the files sit right
there under the new data folder. `resolve` finds them; `rebase_all` fixes the records once."""

from __future__ import annotations

import logging
from pathlib import Path

from .config import DATA_DIR

log = logging.getLogger("farm.paths")


def resolve(path: str | None) -> Path | None:
    """The file at `path`, or the same file under the current data folder, or None."""
    if not path or path == "skip":
        return None
    p = Path(path)
    if p.exists():
        return p
    marker = "/data/"
    s = str(p)
    if marker in s:
        q = DATA_DIR / s.split(marker, 1)[1]
        if q.exists():
            return q
    return None


def rebase_all() -> int:
    """Rewrite stale full paths in the database to where the files are now. Returns how many."""
    from .db import session_scope
    from .models import Job, MediaItem
    n = 0
    with session_scope() as s:
        for row in s.query(MediaItem).filter((MediaItem.thumb_path != "") | (MediaItem.hover_path != "")).all():
            for attr in ("thumb_path", "hover_path"):
                v = getattr(row, attr)
                if not v or v == "skip" or Path(v).exists():
                    continue
                q = resolve(v)
                if q is not None:
                    setattr(row, attr, str(q)); n += 1
                else:
                    setattr(row, attr, ""); n += 1  # gone for good: let it be made again
        for job in s.query(Job).filter(Job.screenshot_path != "").all():
            v = job.screenshot_path
            if Path(v).exists():
                continue
            q = resolve(v)
            job.screenshot_path = str(q) if q is not None else ""
            n += 1
    if n:
        log.info("repaired %d file paths after the data folder moved", n)
    return n
