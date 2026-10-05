"""Holds one live driver per device and keeps the camera-roll mirror in sync."""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from datetime import datetime

from ..config import THUMB_DIR, WDA_BASE_PORT
from ..db import session_scope
from ..models import Account, AccountStatus, Device, MediaItem, Platform
from .. import settings as settings_mod
from .base import Driver
from .ios import IOSDriver, discover_ios_devices
from .simulated import SimulatedDriver

log = logging.getLogger("farm.devices")

MAX_THUMBS_PER_SYNC = 25  # real phones: each video thumbnail means pulling the whole file over USB

# Simulated stand-ins, seeded only when no real phone has ever been seen.
SEED_DEVICES = [
    {
        "id": "sim-17promax", "name": "iPhone 17 Pro Max (sim)", "model": "iPhone 17 Pro Max", "sort_order": 0,
        "accounts": [
            (Platform.instagram, "ig_main_17"), (Platform.instagram, "ig_alt_17"),
            (Platform.tiktok, "tt_main_17"), (Platform.tiktok, "tt_alt_17"),
            (Platform.x, "x_main_17"),
        ],
    },
    {
        "id": "sim-15pro", "name": "iPhone 15 Pro (sim)", "model": "iPhone 15 Pro", "sort_order": 1,
        "accounts": [
            (Platform.instagram, "ig_a_15"), (Platform.instagram, "ig_b_15"), (Platform.instagram, "ig_c_15"),
        ],
    },
    {
        "id": "sim-13", "name": "iPhone 13 (sim)", "model": "iPhone 13", "sort_order": 2,
        "accounts": [(Platform.instagram, "ig_main_13")],
    },
]


class DeviceManager:
    def __init__(self):
        self._drivers: dict[str, Driver] = {}
        self._lock = threading.Lock()
        self._connecting: set[str] = set()
        self.last_error: dict[str, str] = {}
        self.busy_provider = lambda: set()  # set by state.py to the runner's busy set
        self._op_locks: dict[str, threading.Lock] = {}

    def op_lock(self, device_id: str) -> threading.Lock:
        """One phone does one thing at a time: a post, a queue prepare, or a queue release."""
        with self._lock:
            return self._op_locks.setdefault(device_id, threading.Lock())

    def busy_devices(self) -> set[str]:
        try:
            return set(self.busy_provider())
        except Exception:
            return set()

    # ---- bootstrap -------------------------------------------------------
    def seed_if_empty(self) -> None:
        with session_scope() as s:
            if s.query(Device).count():
                return
            for d in SEED_DEVICES:
                dev = Device(
                    id=d["id"], name=d["name"], model=d["model"], kind="sim",
                    sort_order=d["sort_order"], gap_min_s=5, gap_max_s=15,
                )
                for i, (platform, handle) in enumerate(d["accounts"]):
                    dev.accounts.append(Account(
                        platform=platform, handle=handle, sort_order=i,
                        status=AccountStatus.active,
                        story_link="https://example.com/link" if platform != Platform.x else "",
                    ))
                s.add(dev)
            log.info("Seeded %d simulated devices", len(SEED_DEVICES))

    def discover(self) -> list[str]:
        """Register any iPhone plugged in over USB. Returns the ids seen."""
        seen = []
        found = discover_ios_devices()
        if not found:
            return seen
        with session_scope() as s:
            n = s.query(Device).count()
            for i, d in enumerate(found):
                dev = s.get(Device, d["udid"])
                if dev is None:
                    dev = Device(id=d["udid"], name=d["name"], model=d["model"], kind="ios", sort_order=n + i)
                    s.add(dev)
                    log.info("New iPhone: %s (%s)", d["name"], d["udid"])
                else:
                    dev.model = d["model"] or dev.model
                seen.append(d["udid"])
        return seen

    def load(self, connect: bool = True) -> None:
        self.discover()
        with session_scope() as s:
            devices = s.query(Device).order_by(Device.sort_order).all()
        for dev in devices:
            with self._lock:
                if dev.id in self._drivers:
                    continue
                if dev.kind == "sim":
                    drv: Driver = SimulatedDriver(dev.id, dev.name, settings_mod.get_all)
                else:
                    drv = IOSDriver(dev.id, dev.name, wda_port=WDA_BASE_PORT + 1 + dev.sort_order)
                    drv.passcode = dev.passcode or ""
                self._drivers[dev.id] = drv
            if connect and not dev.parked:
                self.connect_async(dev.id)

    # ---- parked: runner stopped by hand ------------------------------------
    def is_parked(self, device_id: str) -> bool:
        with session_scope() as s:
            d = s.get(Device, device_id)
            return bool(d is not None and d.parked)

    def set_parked(self, device_id: str, parked: bool) -> None:
        with session_scope() as s:
            d = s.get(Device, device_id)
            if d is not None:
                d.parked = parked

    def park(self, device_id: str) -> None:
        """Disconnect: stop the runner on the phone (the "Automation Running" overlay goes with
        it) and remember not to start it again until Connect is pressed or a job needs it."""
        self.set_parked(device_id, True)
        drv = self._drivers.get(device_id)
        if drv is not None and hasattr(drv, "disconnect"):
            try:
                drv.disconnect()
            except Exception as e:
                log.warning("%s: disconnect failed: %s", device_id, e)
        self.last_error.pop(device_id, None)
        log.info("%s: runner stopped (disconnected)", getattr(drv, "name", device_id))

    def connect_async(self, device_id: str) -> None:
        """Start a phone's runner in the background (takes up to ~90s for real phones)."""
        drv = self._drivers.get(device_id)
        if drv is None:
            return
        with self._lock:
            if device_id in self._connecting:
                return
            self._connecting.add(device_id)

        def work():
            try:
                drv.connect()
                self.last_error.pop(device_id, None)
                self.sync_media(device_id)
            except Exception as e:
                log.warning("Device %s offline: %s", drv.name, e)
                self.last_error[device_id] = str(e)
            finally:
                with self._lock:
                    self._connecting.discard(device_id)

        threading.Thread(target=work, name=f"connect-{device_id}", daemon=True).start()

    # ---- watchdog --------------------------------------------------------
    def start_watchdog(self, interval_s: float = 30.0) -> None:
        """Real phones: if a runner that was up stops answering, start it again."""
        retry_every_s = 180.0

        def loop():
            was_up: dict[str, bool] = {}
            misses: dict[str, int] = {}
            next_retry: dict[str, float] = {}
            while True:
                time.sleep(interval_s)
                for did, drv in list(self._drivers.items()):
                    if drv.kind != "ios" or did in self._connecting or did in self.busy_devices():
                        continue  # never restart a runner that is mid-job; a long gesture can block its HTTP briefly
                    if self.is_parked(did):
                        was_up[did] = False
                        misses[did] = 0
                        next_retry.pop(did, None)
                        continue  # stopped by hand: stays stopped until Connect or a job
                    try:
                        up = drv.is_connected()
                    except Exception:
                        up = False
                    if up:
                        misses[did] = 0
                        was_up[did] = True
                        next_retry.pop(did, None)
                        continue
                    if was_up.get(did):
                        misses[did] = misses.get(did, 0) + 1
                        if misses[did] >= 3:  # ~90s of silence, not a busy blip
                            log.warning("%s: runner stopped answering; restarting", drv.name)
                            was_up[did] = False
                            misses[did] = 0
                            next_retry[did] = time.time() + retry_every_s
                            self.connect_async(did)
                    elif did in next_retry and time.time() >= next_retry[did]:
                        # A restart can't succeed while the phone is locked; keep trying every few
                        # minutes so it comes back on its own once the phone is unlocked.
                        log.info("%s: runner still down; trying to start it again", drv.name)
                        next_retry[did] = time.time() + retry_every_s
                        self.connect_async(did)
        threading.Thread(target=loop, name="device-watchdog", daemon=True).start()

    def remove(self, device_id: str) -> None:
        drv = self._drivers.pop(device_id, None)
        if drv is not None and hasattr(drv, "disconnect"):
            try:
                drv.disconnect()
            except Exception:
                pass

    def shutdown(self) -> None:
        for did in list(self._drivers):
            self.remove(did)

    # ---- access ----------------------------------------------------------
    def driver(self, device_id: str) -> Driver | None:
        return self._drivers.get(device_id)

    def status(self, device_id: str) -> dict:
        drv = self._drivers.get(device_id)
        if drv is None:
            return {"connected": False, "kind": "unknown"}
        try:
            info = drv.info()
        except Exception as e:
            info = {"connected": False, "kind": drv.kind, "error": str(e)}
        info["connecting"] = device_id in self._connecting
        if device_id in self.last_error:
            info["error"] = self.last_error[device_id]
        return info

    # ---- camera roll mirror ---------------------------------------------
    def sync_media(self, device_id: str, albums_only: bool = False) -> int:
        """Pull the phone's roll listing and mirror it into the DB, generating thumbs for new files.
        `albums_only`: just refresh the listing (album membership, order, lengths) and skip the
        slow thumbnail pulls; used before every job so the queue album is known exactly."""
        drv = self._drivers.get(device_id)
        if drv is None or not drv.is_connected():
            return 0
        items = drv.list_camera_roll()
        now = datetime.now()
        thumb_dir = THUMB_DIR / device_id
        thumb_dir.mkdir(parents=True, exist_ok=True)
        try:
            return self._sync_media_items(drv, device_id, items, now, thumb_dir, albums_only)
        finally:
            # story copies for this phone that were waiting for their clip: fill them in now
            try:
                from ..groups import resolve_pending
                with session_scope() as s:
                    resolve_pending(s, device_id)
            except Exception as e:
                log.warning("%s: could not resolve pending story copies: %s", device_id, e)

    def _sync_media_items(self, drv, device_id: str, items, now, thumb_dir, albums_only: bool) -> int:

        # Pass 1: mirror the listing. Short transaction, no device I/O inside it.
        todo: list[tuple[int, str, bool, bool]] = []  # (row id, filename, needs_thumb, needs_probe)
        with session_scope() as s:
            sorts = getattr(drv, "album_sorts", None)
            if isinstance(sorts, dict):
                dev = s.get(Device, device_id)
                if dev is not None:
                    import json as _json
                    dev.album_sorts = _json.dumps(sorts)
            existing = {m.filename: m for m in s.query(MediaItem).filter_by(device_id=device_id).all()}
            seen: set[str] = set()
            for it in items:  # newest first
                seen.add(it.filename)
                row = existing.get(it.filename)
                if row is None:
                    row = MediaItem(device_id=device_id, filename=it.filename, kind=it.kind)
                    s.add(row)
                row.kind = it.kind
                if it.size_bytes:
                    row.size_bytes = it.size_bytes
                if it.authoritative:
                    # from the phone's own library index: exact, no file pull needed
                    row.duration_s = it.duration_s
                    row.created_at = it.created_at
                    row.added_at = it.added_at
                    row.albums = "|".join(it.albums)
                    row.probed = True
                elif not row.probed:  # once probed, the file's own capture date/duration win
                    row.duration_s = it.duration_s
                    row.created_at = it.created_at
                    row.added_at = it.added_at or it.created_at
                row.present = True
                row.last_seen = now
                s.flush()
                if not row.thumb_path or not row.probed:
                    todo.append((row.id, it.filename, not row.thumb_path, not row.probed))
            for fname, row in existing.items():
                if fname not in seen and row.present:
                    row.present = False

        if albums_only:
            return len(items)
        # Pass 2: pull files for thumbnails + capture metadata, newest first, a bounded number per
        # sync. Each row is written in its own short transaction so the UI and runner never wait.
        pulls = 0
        for row_id, filename, needs_thumb, needs_probe in todo:
            if pulls >= MAX_THUMBS_PER_SYNC:
                break
            update: dict = {}
            if needs_thumb:
                key = hashlib.sha1(f"{device_id}:{filename}".encode()).hexdigest()[:16]
                out = thumb_dir / f"{key}.jpg"
                if not out.exists():
                    pulls += 1
                    try:
                        data = drv.thumbnail(filename)
                    except Exception as e:
                        log.warning("thumb %s failed: %s", filename, e)
                        data = None
                    if data:
                        out.write_bytes(data)
                if out.exists():
                    update["thumb_path"] = str(out)
            if needs_probe:
                cached = filename in getattr(drv, "_meta_cache", {})
                if not cached:
                    pulls += 1
                try:
                    meta = drv.media_meta(filename)
                except Exception as e:
                    log.warning("probe %s failed: %s", filename, e)
                    meta = None
                if meta:
                    if meta.get("duration_s"):
                        update["duration_s"] = float(meta["duration_s"])
                    if meta.get("captured_at"):
                        update["created_at"] = meta["captured_at"]
                update["probed"] = True  # None = can't probe (sim, or file too big); don't keep retrying
            if update:
                with session_scope() as s:
                    row = s.get(MediaItem, row_id)
                    if row is not None:
                        for k, v in update.items():
                            setattr(row, k, v)
        return len(items)

    def sync_all(self) -> None:
        for did in list(self._drivers):
            try:
                self.sync_media(did)
            except Exception as e:
                log.warning("sync %s failed: %s", did, e)


manager = DeviceManager()
