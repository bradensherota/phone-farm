"""Real iPhone driver, over USB.

Pieces:
  * pymobiledevice3  - discovery (usbmux), camera roll listing + pulls via AFC (/DCIM),
                       and a TCP forward from a local port to WebDriverAgent's 8100 on the phone.
  * WebDriverAgent   - XCUITest runner built once per Mac (~/PhoneFarm/wda-build). Started per
                       phone with `xcodebuild test-without-building`, which keeps it alive.
  * facebook-wda     - HTTP client for WebDriverAgent used by the flows.

Each phone gets its own local port (8100 + slot) so several phones run in parallel.
"""

from __future__ import annotations

import glob
import io
import json
import logging
import os
import signal
import subprocess
import tempfile
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from .. import config
from .base import ActionResult, ActionSpec, Driver, LogFn, RollItem

log = logging.getLogger("farm.ios")

VIDEO_EXT = {".mov", ".mp4", ".m4v"}
PHOTO_EXT = {".jpg", ".jpeg", ".heic", ".heif", ".png", ".dng"}
MAX_THUMB_PULL_BYTES = 400 * 1024 * 1024  # skip thumbnailing videos bigger than this

MODEL_NAMES = {
    "iPhone14,5": "iPhone 13", "iPhone14,4": "iPhone 13 mini", "iPhone14,2": "iPhone 13 Pro", "iPhone14,3": "iPhone 13 Pro Max",
    "iPhone14,7": "iPhone 14", "iPhone14,8": "iPhone 14 Plus", "iPhone15,2": "iPhone 14 Pro", "iPhone15,3": "iPhone 14 Pro Max",
    "iPhone15,4": "iPhone 15", "iPhone15,5": "iPhone 15 Plus", "iPhone16,1": "iPhone 15 Pro", "iPhone16,2": "iPhone 15 Pro Max",
    "iPhone17,3": "iPhone 16", "iPhone17,4": "iPhone 16 Plus", "iPhone17,1": "iPhone 16 Pro", "iPhone17,2": "iPhone 16 Pro Max",
    "iPhone18,1": "iPhone 17 Pro", "iPhone18,2": "iPhone 17 Pro Max", "iPhone18,3": "iPhone 17", "iPhone18,4": "iPhone Air",
}


def _pmd3() -> str:
    """Path to the pymobiledevice3 CLI inside our venv."""
    return str(Path(__file__).resolve().parents[2] / ".venv" / "bin" / "pymobiledevice3")


def _run(args: list[str], timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def discover_ios_devices() -> list[dict]:
    """iPhones currently connected over USB (Wi-Fi-only entries are ignored)."""
    try:
        out = _run([_pmd3(), "usbmux", "list"], timeout=20)
        rows = json.loads(out.stdout or "[]")
    except Exception as e:
        log.debug("discovery failed: %s", e)
        return []
    found = []
    for r in rows:
        if r.get("ConnectionType") != "USB" or r.get("DeviceClass") != "iPhone":
            continue
        udid = r.get("UniqueDeviceID") or r.get("Identifier")
        pt = r.get("ProductType", "")
        found.append({
            "udid": udid,
            "name": r.get("DeviceName") or MODEL_NAMES.get(pt, pt) or udid,
            "model": MODEL_NAMES.get(pt, pt),
            "ios": r.get("ProductVersion", ""),
        })
    return found


def find_xctestrun() -> str | None:
    hits = sorted(glob.glob(str(config.WDA_BUILD_DIR / "Build" / "Products" / "WebDriverAgentRunner_iphoneos*.xctestrun")))
    return hits[-1] if hits else None


class IOSDriver(Driver):
    kind = "ios"

    def __init__(self, device_id: str, name: str, wda_port: int):
        super().__init__(device_id, name)
        self.wda_port = wda_port
        self.base = f"http://127.0.0.1:{wda_port}"
        self._xcodebuild: subprocess.Popen | None = None
        self._forward: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._log_path = config.DATA_DIR / "logs" / f"wda-{device_id}.log"
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._meta_cache: dict[str, dict] = {}

    # ---- lifecycle -------------------------------------------------------
    def connect(self) -> None:
        with self._lock:
            if self._status_ok():
                return
            xctestrun = find_xctestrun()
            if not xctestrun:
                raise RuntimeError("WebDriverAgent is not built. See README: build it once into ~/PhoneFarm/wda-build.")
            self._stop_processes()
            logf = open(self._log_path, "ab")
            self._forward = subprocess.Popen(
                [_pmd3(), "usbmux", "forward", str(self.wda_port), "8100", "--udid", self.device_id],
                stdout=logf, stderr=logf,
            )
            self._xcodebuild = subprocess.Popen(
                ["xcrun", "xcodebuild", "test-without-building", "-xctestrun", xctestrun,
                 "-destination", f"id={self.device_id}"],
                cwd=str(config.WDA_BUILD_DIR), stdout=logf, stderr=logf, start_new_session=True,
            )
            deadline = time.time() + 90
            while time.time() < deadline:
                if self._status_ok():
                    log.info("%s: WebDriverAgent up on port %d", self.name, self.wda_port)
                    return
                if self._xcodebuild.poll() is not None:
                    raise RuntimeError(f"xcodebuild exited early (see {self._log_path})")
                time.sleep(2)
            raise RuntimeError("WebDriverAgent did not come up within 90s")

    def disconnect(self) -> None:
        with self._lock:
            self._stop_processes()

    def _stop_processes(self) -> None:
        for p in (self._xcodebuild, self._forward):
            if p and p.poll() is None:
                try:
                    os.killpg(os.getpgid(p.pid), signal.SIGTERM) if p is self._xcodebuild else p.terminate()
                    p.wait(timeout=10)
                except Exception:
                    try:
                        p.kill()
                    except Exception:
                        pass
        self._xcodebuild = None
        self._forward = None

    def is_connected(self) -> bool:
        return self._status_ok()

    def _status_ok(self) -> bool:
        try:
            with urllib.request.urlopen(self.base + "/status", timeout=3) as r:
                return r.status == 200
        except Exception:
            return False

    def _get(self, path: str, timeout: float = 20) -> dict:
        with urllib.request.urlopen(self.base + path, timeout=timeout) as r:
            return json.loads(r.read())

    def _post(self, path: str, body: dict | None = None, timeout: float = 30) -> dict:
        data = json.dumps(body or {}).encode()
        req = urllib.request.Request(self.base + path, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())

    def info(self) -> dict:
        return {"kind": self.kind, "connected": self.is_connected(), "wda_port": self.wda_port}

    # ---- screen ----------------------------------------------------------
    def screenshot(self) -> bytes:
        import base64
        d = self._get("/screenshot", timeout=20)
        return base64.b64decode(d["value"])

    def wake(self) -> None:
        try:
            if self._get("/wda/locked").get("value"):
                self._post("/wda/unlock")
        except Exception as e:
            log.warning("%s: wake failed: %s", self.name, e)

    # ---- camera roll (read-only, over AFC) ------------------------------
    # pymobiledevice3's services are async; each call below runs its own short event loop.

    def _afc(self, coro_fn):
        import asyncio
        from pymobiledevice3.lockdown import create_using_usbmux
        from pymobiledevice3.services.afc import AfcService

        async def go():
            ld = await create_using_usbmux(self.device_id)
            afc = AfcService(ld)
            try:
                return await coro_fn(afc)
            finally:
                try:
                    await afc.aclose()
                except Exception:
                    pass
        return asyncio.run(go())

    def list_camera_roll(self) -> list[RollItem]:
        """Read the phone's Photos library index (Photos.sqlite over AFC, read-only).

        This is authoritative: capture date, duration, kind, and the date-added order that
        Instagram's Recents grid uses. Falls back to walking /DCIM if the index can't be read.
        """
        try:
            items = self._list_from_photos_db()
            if items:
                return items
        except Exception as e:
            log.warning("%s: Photos index unreadable (%s); falling back to /DCIM listing", self.name, e)
        return self._list_from_dcim()

    def _list_from_photos_db(self) -> list[RollItem]:
        import sqlite3

        async def pull(afc):
            out = {}
            for f in ("Photos.sqlite", "Photos.sqlite-wal", "Photos.sqlite-shm"):
                try:
                    out[f] = await afc.get_file_contents(f"/PhotoData/{f}")
                except Exception:
                    if f == "Photos.sqlite":
                        raise
            return out

        files = self._afc(pull)
        with tempfile.TemporaryDirectory() as td:
            for name, data in files.items():
                (Path(td) / name).write_bytes(data)
            db = sqlite3.connect(f"file:{Path(td) / 'Photos.sqlite'}?mode=ro", uri=True)
            try:
                rows = db.execute(
                    "SELECT ZDIRECTORY, ZFILENAME, ZKIND, ZDURATION, ZDATECREATED, ZADDEDDATE "
                    "FROM ZASSET WHERE ZTRASHEDSTATE=0 AND ZHIDDEN=0 AND ZFILENAME IS NOT NULL "
                    "ORDER BY ZADDEDDATE DESC"
                ).fetchall()
            finally:
                db.close()
        items: list[RollItem] = []
        for directory, fname, zkind, dur, created, added in rows:
            if not directory or not directory.startswith("DCIM/"):
                continue
            ext = Path(fname).suffix.lower()
            kind = "video" if zkind == 1 or ext in VIDEO_EXT else "photo"
            items.append(RollItem(
                filename=f"{directory[len('DCIM/'):]}/{fname}",
                kind=kind,
                created_at=_coredata_ts(created) or datetime.now(),
                duration_s=round(float(dur or 0), 1),
                added_at=_coredata_ts(added),
                authoritative=True,
            ))
        return items

    def _list_from_dcim(self) -> list[RollItem]:
        async def go(afc):
            items: list[RollItem] = []
            for album in await afc.listdir("/DCIM"):
                if "APPLE" not in album.upper():
                    continue
                for name in await afc.listdir(f"/DCIM/{album}"):
                    ext = Path(name).suffix.lower()
                    kind = "video" if ext in VIDEO_EXT else "photo" if ext in PHOTO_EXT else None
                    if not kind:
                        continue
                    try:
                        st = await afc.stat(f"/DCIM/{album}/{name}")
                    except Exception:
                        continue
                    if st.get("st_ifmt") != "S_IFREG":
                        continue
                    created = st.get("st_birthtime") or st.get("st_mtime") or datetime.now()
                    items.append(RollItem(
                        filename=f"{album}/{name}", kind=kind,
                        created_at=created, size_bytes=int(st.get("st_size", 0) or 0),
                        added_at=created,
                    ))
            items.sort(key=lambda r: r.created_at, reverse=True)
            return items
        return self._afc(go)

    def _pull(self, filename: str) -> bytes | None:
        ext = Path(filename).suffix.lower()

        async def go(afc):
            st = await afc.stat(f"/DCIM/{filename}")
            if ext in VIDEO_EXT and int(st.get("st_size", 0) or 0) > MAX_THUMB_PULL_BYTES:
                return None
            return await afc.get_file_contents(f"/DCIM/{filename}")

        return self._afc(go)

    def thumbnail(self, filename: str, max_px: int = 400) -> bytes | None:
        """Pull the file once; make the thumbnail and remember its capture date + duration."""
        ext = Path(filename).suffix.lower()
        data = self._pull(filename)
        if not data:
            return None
        with tempfile.TemporaryDirectory() as td:
            local = Path(td) / Path(filename).name
            local.write_bytes(data)
            self._meta_cache[filename] = _probe_media(local)
            from .simulated import _image_thumb, _video_thumb
            return _video_thumb(local, max_px) if ext in VIDEO_EXT else _image_thumb(local, max_px)

    def media_meta(self, filename: str) -> dict | None:
        cached = self._meta_cache.get(filename)
        if cached is not None:
            return cached
        data = self._pull(filename)
        if not data:
            return None
        with tempfile.TemporaryDirectory() as td:
            local = Path(td) / Path(filename).name
            local.write_bytes(data)
            meta = _probe_media(local)
        self._meta_cache[filename] = meta
        return meta

    # ---- actions ---------------------------------------------------------
    def run_action(self, spec: ActionSpec, log_fn: LogFn) -> ActionResult:
        from . import ios_flows
        self.wake()
        try:
            return ios_flows.run(self, spec, log_fn)
        except Exception as e:
            log.exception("%s: flow failed", self.name)
            shot = None
            try:
                shot = self.screenshot()
            except Exception:
                pass
            return ActionResult(False, f"{type(e).__name__}: {e}", shot)


def _coredata_ts(value) -> datetime | None:
    """Core Data stores seconds since 2001-01-01 UTC; return local naive datetime."""
    if value is None:
        return None
    try:
        from datetime import timezone
        dt = datetime.fromtimestamp(float(value) + 978307200, tz=timezone.utc)
        return dt.astimezone().replace(tzinfo=None)
    except Exception:
        return None


def _probe_media(local: Path) -> dict:
    """Capture date + duration from the file's own metadata (what Instagram's gallery sorts by)."""
    meta: dict = {"duration_s": 0.0, "captured_at": None}
    ext = local.suffix.lower()
    try:
        if ext in VIDEO_EXT:
            out = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration:format_tags=creation_time",
                        "-of", "json", str(local)], timeout=30).stdout
            fmt = json.loads(out or "{}").get("format", {})
            meta["duration_s"] = round(float(fmt.get("duration") or 0), 1)
            ct = (fmt.get("tags") or {}).get("creation_time")
            if ct:
                meta["captured_at"] = _parse_iso_local(ct)
        else:
            out = _run(["sips", "-g", "creation", str(local)], timeout=20).stdout
            for line in out.splitlines():
                if "creation:" in line:
                    raw = line.split("creation:", 1)[1].strip()  # e.g. 2026:09:27 16:37:09
                    try:
                        meta["captured_at"] = datetime.strptime(raw, "%Y:%m:%d %H:%M:%S")
                    except ValueError:
                        pass
    except Exception as e:
        log.debug("probe %s failed: %s", local.name, e)
    return meta


def _parse_iso_local(value: str) -> datetime | None:
    """QuickTime creation_time is UTC ('2026-09-27T20:37:09.000000Z'); convert to local naive."""
    from datetime import timezone
    try:
        v = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(v)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone().replace(tzinfo=None)
    except Exception:
        return None


__all__ = ["IOSDriver", "discover_ios_devices", "find_xctestrun", "io"]
