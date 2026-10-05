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
    # xcodebuild output that means the runner's signature is gone (free Apple ID: 7 days) or this
    # phone isn't in its provisioning profile yet: a rebuild fixes both
    _REBUILD_SIGNS = ("expired", "could not be verified", "no longer valid", "provisioning profile",
                      "0xe800", "failed to install", "doesn't include", "no profiles", "not registered",
                      "unable to install", "not eligible", "invalid code signature", "invalid signature")

    def _log_tail(self, n: int = 400) -> str:
        try:
            with open(self._log_path, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 200_000))
                return "\n".join(f.read().decode("utf-8", "replace").splitlines()[-n:])
        except Exception:
            return ""

    def rebuild_runner(self) -> None:
        """Build and install the runner for this phone with the Apple ID Xcode is signed into
        (tools/wda-build.sh). Phone must be plugged in and unlocked. Takes 2-5 minutes."""
        script = config.ROOT / "tools" / "wda-build.sh"
        from .. import settings as settings_mod
        team = (settings_mod.get("apple_team_id", "") or "").strip()
        args = ["/bin/zsh", str(script), self.device_id] + ([team] if team else [])
        log.info("%s: building the runner (%s, team %s)", self.name, script, team or "auto")
        with open(self._log_path, "ab") as logf:
            logf.write(b"\n==== rebuilding the runner ====\n")
            r = subprocess.run(args, stdout=logf, stderr=logf, timeout=1500)
        if r.returncode == 2:
            raise RuntimeError("No Apple team found on this Mac. Sign an Apple ID into Xcode (Xcode → Settings → Accounts), "
                               "or enter your Team ID in Settings → Apple Team ID, then press Rebuild runner.")
        if r.returncode != 0:
            raise RuntimeError(f"Runner build failed (code {r.returncode}); see {self._log_path}. "
                               "Is the phone plugged in and unlocked, with Developer Mode on?")
        self._rebuilt_at = time.time()
        log.info("%s: runner built", self.name)

    def connect(self, _retry_build: bool = True) -> None:
        with self._lock:
            if self._status_ok():
                return
            xctestrun = find_xctestrun()
            if not xctestrun:
                if not _retry_build:
                    raise RuntimeError("WebDriverAgent is not built (tools/wda-build.sh <UDID>)")
                log.info("%s: no runner built yet; building it now (first time, 2-5 min)", self.name)
                self.rebuild_runner()
                xctestrun = find_xctestrun()
                if not xctestrun:
                    raise RuntimeError("Runner build produced nothing; see the phone's log")
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
                    self._new_standby_session()
                    return
                if self._xcodebuild.poll() is not None:
                    tail = self._log_tail().lower()
                    recently = time.time() - getattr(self, "_rebuilt_at", 0) < 600
                    if _retry_build and not recently and any(sign in tail for sign in self._REBUILD_SIGNS):
                        log.warning("%s: runner signature expired or phone not registered; rebuilding the runner", self.name)
                        self._stop_processes()
                        self.rebuild_runner()
                        break
                    raise RuntimeError(f"xcodebuild exited early (see {self._log_path})")
                time.sleep(2)
            else:
                raise RuntimeError("WebDriverAgent did not come up within 90s")
        # only reached after a rebuild: start the fresh runner (no second rebuild)
        self.connect(_retry_build=False)

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

    def is_locked(self, default=False):
        """True/False from the phone; `default` when the runner doesn't answer."""
        try:
            v = bool(self._get("/wda/locked", timeout=5).get("value"))
            self._locked_cache = (time.time(), v)
            return v
        except Exception:
            return default

    def locked_recently(self, max_age_s: float = 8.0) -> bool | None:
        """Last known lock state without asking the phone again within `max_age_s`."""
        t, v = getattr(self, "_locked_cache", (0.0, None))
        if time.time() - t <= max_age_s and v is not None:
            return v
        return self.is_locked(default=None)

    # ---- wake / sleep ----------------------------------------------------
    # A "standby" session is kept open so the lock screen can be driven for the wake. Older
    # builds refused to open a session while locked, so one is opened before every lock; on
    # this build it also works while locked, so we always try and keep the old one if it fails.
    def _new_standby_session(self) -> str | None:
        try:
            self._standby_sid = self._post("/session", {"capabilities": {}}, timeout=25).get("sessionId")
            self._post(f"/session/{self._standby_sid}/appium/settings",
                       {"settings": {"snapshotMaxDepth": 2, "customSnapshotTimeout": 5, "waitForIdleTimeout": 0}}, timeout=10)
        except Exception as e:
            log.warning("%s: standby session failed: %s", self.name, e)
        return getattr(self, "_standby_sid", None)

    def _sid(self) -> str | None:
        sid = getattr(self, "_standby_sid", None)
        if sid:
            try:
                self._get(f"/session/{sid}/window/size", timeout=8)
                return sid
            except Exception:
                pass
        return self._new_standby_session()

    def _actions(self, sid: str, acts: list, timeout: float = 20) -> None:
        self._post(f"/session/{sid}/actions", {"actions": [{"type": "pointer", "id": "f1", "parameters": {"pointerType": "touch"}, "actions": acts}]}, timeout=timeout)
        try:
            urllib.request.urlopen(urllib.request.Request(f"{self.base}/session/{sid}/actions", method="DELETE"), timeout=10).read()
        except Exception:
            pass

    def _tap(self, sid: str, x: float, y: float) -> None:
        self._actions(sid, [{"type": "pointerMove", "duration": 0, "x": int(x), "y": int(y)}, {"type": "pointerDown", "button": 0},
                            {"type": "pause", "duration": 60}, {"type": "pointerUp", "button": 0}])

    def wake(self) -> None:
        """Wake and unlock the phone: home button, swipe up, and the passcode if one is set."""
        try:
            if not self.is_locked():
                self._new_standby_session()
                return
            log.info("%s: waking", self.name)
            sid = self._sid()
            if not sid:
                log.warning("%s: no standby session; can't wake a locked phone (unlock it once by hand)", self.name)
                return
            self._post(f"/session/{sid}/wda/pressButton", {"name": "home"}, timeout=10)
            time.sleep(1.2)
            if not self.is_locked():
                return
            try:
                size = self._get(f"/session/{sid}/window/size", timeout=10).get("value") or {}
                w, h = float(size.get("width") or 390), float(size.get("height") or 844)
            except Exception:
                w, h = 390.0, 844.0
            self._actions(sid, [{"type": "pointerMove", "duration": 0, "x": int(w / 2), "y": int(h * 0.98)}, {"type": "pointerDown", "button": 0},
                                {"type": "pointerMove", "duration": 250, "x": int(w / 2), "y": int(h * 0.35)}, {"type": "pointerUp", "button": 0}])
            time.sleep(1.5)
            if self.is_locked() and getattr(self, "passcode", ""):
                self._type_passcode(sid, w, h)
            if self.is_locked():
                log.warning("%s: still locked after wake attempts", self.name)
            else:
                self._new_standby_session()
        except Exception as e:
            log.warning("%s: wake failed: %s", self.name, e)

    def _type_passcode(self, sid: str, w: float, h: float) -> None:
        """Type the lock-screen passcode: read the keypad digits off a screenshot (local OCR)
        and tap them through the standby session. The code lives only in the local database."""
        import base64, json as _json, subprocess as _sp, tempfile as _tf
        from pathlib import Path as _P
        code = getattr(self, "passcode", "") or ""
        ocr = _P.home() / "PhoneFarm" / "ocr"
        keys: dict[str, tuple[float, float]] = {}
        for _ in range(3):
            png = base64.b64decode(self._get("/screenshot", timeout=20)["value"])
            with _tf.NamedTemporaryFile(suffix=".png", delete=False) as f:
                f.write(png)
                path = f.name
            try:
                words = _json.loads(_sp.run([str(ocr), path], capture_output=True, timeout=30).stdout or b"[]")
            finally:
                _P(path).unlink(missing_ok=True)
            if not words:
                time.sleep(1.0)
                continue
            from PIL import Image
            import io as _io
            scale = Image.open(_io.BytesIO(png)).width / w
            import re as _re
            for wd in words:
                t = wd["text"].strip()
                m = _re.match(r"^(\d)(?:\s*[A-Z]{2,4})?$", t)  # keys read as "2", "2 ABC" or "2ABC"
                if m and (wd["y"] / scale) > h * 0.3:
                    cx = (wd["x"] + wd["w"] / 2) / scale
                    if len(t) > 1:  # letters widen the box; the digit sits centred above them
                        cx = (wd["x"] + wd["w"] / 2) / scale
                    keys.setdefault(m.group(1), (cx, (wd["y"] + wd["h"] / 2) / scale))
            if len(keys) >= 10:
                break
            time.sleep(1.0)
        if 6 <= len(keys) < 10:
            # fill gaps from the keypad lattice: 3 columns, rows 1-3 / 4-6 / 7-9 / 0 (centre column)
            cols = {0: [], 1: [], 2: []}
            rows = {0: [], 1: [], 2: [], 3: []}
            for d, (x, y) in keys.items():
                n = int(d)
                c, r = (1, 3) if n == 0 else ((n - 1) % 3, (n - 1) // 3)
                cols[c].append(x)
                rows[r].append(y)
            med = lambda v: sorted(v)[len(v) // 2] if v else None
            cx = {c: med(v) for c, v in cols.items()}
            ry = {r: med(v) for r, v in rows.items()}
            known_c = [c for c in cx if cx[c] is not None]
            known_r = [r for r in ry if ry[r] is not None]
            if len(known_c) >= 2 and len(known_r) >= 2:
                pc = (cx[known_c[-1]] - cx[known_c[0]]) / (known_c[-1] - known_c[0])
                pr = (ry[known_r[-1]] - ry[known_r[0]]) / (known_r[-1] - known_r[0])
                for c in cx:
                    if cx[c] is None:
                        cx[c] = cx[known_c[0]] + (c - known_c[0]) * pc
                for r in ry:
                    if ry[r] is None:
                        ry[r] = ry[known_r[0]] + (r - known_r[0]) * pr
                for n in range(10):
                    d = str(n)
                    if d not in keys:
                        c, r = (1, 3) if n == 0 else ((n - 1) % 3, (n - 1) // 3)
                        keys[d] = (cx[c], ry[r])
                log.info("%s: keypad inferred from %d read keys", self.name, len(known_c) + len(known_r))
        if len(keys) < 10:
            log.warning("%s: keypad not visible (found %d digits); cannot type passcode", self.name, len(keys))
            return
        log.info("%s: typing passcode (%d digits)", self.name, len(code))
        for ch in code:
            k = keys.get(ch)
            if k is None:
                log.warning("%s: digit not on keypad", self.name)
                return
            self._tap(sid, *k)
            time.sleep(0.3)
        time.sleep(2.5)

    def go_home(self) -> None:
        """Press Home so the phone rests on the home screen, not inside an app's feed."""
        try:
            sid = self._sid()
            if sid:
                self._post(f"/session/{sid}/wda/pressButton", {"name": "home"}, timeout=10)
        except Exception as e:
            log.warning("%s: home press failed: %s", self.name, e)

    def sleep(self) -> str:
        """Lock the screen so the phone can rest between routines (the "sleep" step at the end
        of every day's queue). A fresh standby session is opened first so the next wake can
        drive the lock screen. Returns "" when the phone is locked, else the reason it isn't."""
        try:
            if self.is_locked():
                return ""
            sid = self._new_standby_session()
            log.info("%s: locking screen", self.name)
            err = ""
            try:
                self._post("/wda/lock", timeout=8)
            except Exception as e:
                err = str(e)
            if self.is_locked():
                return ""
            # Face ID phones on iOS 26 ignore WDA's lock command; press the side button instead.
            if sid:
                log.info("%s: lock command didn't lock (%s); pressing the side button", self.name, err or "no change")
                try:
                    self._post(f"/session/{sid}/wda/performIoHidEvent", {"page": 0x0C, "usage": 0x30, "duration": 0.5}, timeout=15)
                    time.sleep(1.5)
                except Exception as e:
                    err = str(e)
            if self.is_locked():
                return ""
            err = err or "screen did not lock"
            log.warning("%s: lock failed: %s", self.name, err)
            return err
        except Exception as e:
            log.warning("%s: lock failed: %s", self.name, e)
            return str(e)

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
                    "SELECT Z_PK, ZDIRECTORY, ZFILENAME, ZKIND, ZDURATION, ZDATECREATED, ZADDEDDATE "
                    "FROM ZASSET WHERE ZTRASHEDSTATE=0 AND ZHIDDEN=0 AND ZFILENAME IS NOT NULL "
                    "ORDER BY ZADDEDDATE DESC"
                ).fetchall()
                albums_by_asset = _album_membership(db)
                self.album_sorts = _album_sorts(db)
            finally:
                db.close()
        items: list[RollItem] = []
        for pk, directory, fname, zkind, dur, created, added in rows:
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
                albums=tuple(albums_by_asset.get(pk, ())),
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

    def pull_file(self, filename: str) -> bytes | None:
        return self._pull(filename)

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

    # ---- Photos albums (queue) ------------------------------------------
    # ---- stop button -----------------------------------------------------
    # Every flow logs before each tap; the guarded log function raises when a stop was asked
    # for, so the flow ends at its next step and the phone is left where it is.
    abort_requested = False

    def abort(self) -> None:
        self.abort_requested = True

    def _guarded(self, log_fn: LogFn) -> LogFn:
        from .base import FlowAborted
        self.abort_requested = False

        def check() -> None:
            if self.abort_requested:
                self.abort_requested = False
                log_fn("warn", "Stopped by hand")
                raise FlowAborted("Stopped by hand")

        def guarded(level: str, message: str) -> None:
            log_fn(level, message)
            check()
        guarded.check = check  # type: ignore[attr-defined]
        return guarded

    def add_to_album(self, items: list, album: str, log_fn: LogFn) -> list:
        log_fn = self._guarded(log_fn)
        from . import ios_flows, ios_photos
        self.wake()
        phone = ios_flows.Phone(self.base, log_fn)
        try:
            return ios_photos.photos_add_to_album(phone, items, album)
        finally:
            try:
                phone.home()
            except Exception:
                pass
            phone.close()
            self._new_standby_session()

    def remove_from_album(self, item, album: str, log_fn: LogFn) -> bool:
        log_fn = self._guarded(log_fn)
        from . import ios_flows, ios_photos
        self.wake()
        phone = ios_flows.Phone(self.base, log_fn)
        try:
            return ios_photos.photos_remove_from_album(phone, item, album)
        finally:
            try:
                phone.home()
            except Exception:
                pass
            phone.close()
            self._new_standby_session()

    def clear_album(self, album: str, log_fn: LogFn) -> bool:
        log_fn = self._guarded(log_fn)
        from . import ios_flows, ios_photos
        self.wake()
        phone = ios_flows.Phone(self.base, log_fn)
        try:
            return ios_photos.photos_clear_album(phone, album)
        finally:
            try:
                phone.home()
            except Exception:
                pass
            phone.close()
            self._new_standby_session()

    # ---- actions ---------------------------------------------------------
    def run_action(self, spec: ActionSpec, log_fn: LogFn) -> ActionResult:
        from . import ios_flows
        from .base import FlowAborted
        log_fn = self._guarded(log_fn)
        self.wake()
        try:
            result = ios_flows.run(self, spec, log_fn)
            self._new_standby_session()
            return result
        except FlowAborted:
            shot = None
            try:
                shot = self.screenshot()
            except Exception:
                pass
            return ActionResult(False, "Stopped by hand", shot)
        except Exception as e:
            log.exception("%s: flow failed", self.name)
            shot = None
            try:
                shot = self.screenshot()
            except Exception:
                pass
            return ActionResult(False, f"{type(e).__name__}: {e}", shot)


def _album_membership(db) -> dict[int, list[str]]:
    """asset Z_PK -> user album titles. The join table's name changes between iOS versions
    (Z_<n>ASSETS with Z_<n>ALBUMS / Z_<m>ASSETS columns), so find it from the schema."""
    import re
    out: dict[int, list[str]] = {}
    try:
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Z\\_%ASSETS' ESCAPE '\\'")]
    except Exception:
        return out
    for t in tables:
        cols = [r[1] for r in db.execute(f"PRAGMA table_info({t})")]
        album_col = next((c for c in cols if re.fullmatch(r"Z_\d+ALBUMS", c)), None)
        asset_col = next((c for c in cols if re.fullmatch(r"Z_\d+ASSETS", c)), None)
        if not album_col or not asset_col:
            continue
        try:
            rows = db.execute(
                f"SELECT j.{asset_col}, g.ZTITLE FROM {t} j JOIN ZGENERICALBUM g ON g.Z_PK = j.{album_col} "
                "WHERE g.ZKIND = 2 AND g.ZTRASHEDSTATE = 0 AND g.ZTITLE IS NOT NULL"
            ).fetchall()
        except Exception:
            continue
        for asset_pk, title in rows:
            out.setdefault(asset_pk, []).append(title.strip())
        if rows:
            break
    return out


def _album_sorts(db) -> dict[str, dict]:
    """album title -> how the Photos app orders it: {"by": "date" | "custom", "ascending": bool}.
    ZCUSTOMSORTKEY 1 = by capture date (ascending = oldest first); 0 = arranged by hand."""
    out: dict[str, dict] = {}
    try:
        rows = db.execute(
            "SELECT ZTITLE, ZCUSTOMSORTKEY, ZCUSTOMSORTASCENDING FROM ZGENERICALBUM "
            "WHERE ZKIND = 2 AND ZTRASHEDSTATE = 0 AND ZTITLE IS NOT NULL"
        ).fetchall()
    except Exception:
        return out
    for title, key, asc in rows:
        out[title.strip()] = {"by": "custom" if key == 0 else "date", "ascending": bool(asc) if asc is not None else True}
    return out


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
