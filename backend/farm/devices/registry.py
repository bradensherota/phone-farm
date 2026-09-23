"""Holds one live driver per device and keeps the camera-roll mirror in sync."""

from __future__ import annotations

import hashlib
import logging
import threading
from datetime import datetime

from ..config import THUMB_DIR
from ..db import session_scope
from ..models import Account, AccountStatus, Device, MediaItem, Platform
from .. import settings as settings_mod
from .base import Driver
from .ios import IOSDriver
from .simulated import SimulatedDriver

log = logging.getLogger("farm.devices")

# Simulated stand-ins for the real phones until the iOS driver lands.
SEED_DEVICES = [
    {
        "id": "sim-17promax", "name": "iPhone 17 Pro Max", "model": "iPhone 17 Pro Max", "sort_order": 0,
        "accounts": [
            (Platform.instagram, "ig_main_17"), (Platform.instagram, "ig_alt_17"),
            (Platform.tiktok, "tt_main_17"), (Platform.tiktok, "tt_alt_17"),
            (Platform.x, "x_main_17"),
        ],
    },
    {
        "id": "sim-15pro", "name": "iPhone 15 Pro", "model": "iPhone 15 Pro", "sort_order": 1,
        "accounts": [
            (Platform.instagram, "ig_a_15"), (Platform.instagram, "ig_b_15"), (Platform.instagram, "ig_c_15"),
        ],
    },
    {
        "id": "sim-13", "name": "iPhone 13", "model": "iPhone 13", "sort_order": 2,
        "accounts": [(Platform.instagram, "ig_main_13")],
    },
]


class DeviceManager:
    def __init__(self):
        self._drivers: dict[str, Driver] = {}
        self._lock = threading.Lock()

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

    def load(self) -> None:
        with session_scope() as s:
            devices = s.query(Device).order_by(Device.sort_order).all()
        with self._lock:
            for dev in devices:
                if dev.id in self._drivers:
                    continue
                if dev.kind == "sim":
                    drv: Driver = SimulatedDriver(dev.id, dev.name, settings_mod.get_all)
                else:
                    drv = IOSDriver(dev.id, dev.name, wda_port=8100 + dev.sort_order)
                try:
                    drv.connect()
                except Exception as e:  # real phones without tooling just show offline
                    log.warning("Device %s offline: %s", dev.name, e)
                self._drivers[dev.id] = drv

    # ---- access ----------------------------------------------------------
    def driver(self, device_id: str) -> Driver | None:
        return self._drivers.get(device_id)

    def status(self, device_id: str) -> dict:
        drv = self._drivers.get(device_id)
        if drv is None:
            return {"connected": False, "kind": "unknown"}
        try:
            return drv.info()
        except Exception as e:
            return {"connected": False, "kind": drv.kind, "error": str(e)}

    # ---- camera roll mirror ---------------------------------------------
    def sync_media(self, device_id: str) -> int:
        """Pull the phone's roll listing and mirror it into the DB, generating thumbs for new files."""
        drv = self._drivers.get(device_id)
        if drv is None or not drv.is_connected():
            return 0
        items = drv.list_camera_roll()
        now = datetime.now()
        thumb_dir = THUMB_DIR / device_id
        thumb_dir.mkdir(parents=True, exist_ok=True)

        with session_scope() as s:
            existing = {m.filename: m for m in s.query(MediaItem).filter_by(device_id=device_id).all()}
            seen: set[str] = set()
            for it in items:
                seen.add(it.filename)
                row = existing.get(it.filename)
                if row is None:
                    row = MediaItem(device_id=device_id, filename=it.filename, kind=it.kind)
                    s.add(row)
                row.kind = it.kind
                row.duration_s = it.duration_s
                row.size_bytes = it.size_bytes
                row.created_at = it.created_at
                row.present = True
                row.last_seen = now
                if not row.thumb_path:
                    key = hashlib.sha1(f"{device_id}:{it.filename}".encode()).hexdigest()[:16]
                    out = thumb_dir / f"{key}.jpg"
                    if not out.exists():
                        data = drv.thumbnail(it.filename)
                        if data:
                            out.write_bytes(data)
                    if out.exists():
                        row.thumb_path = str(out)
            for fname, row in existing.items():
                if fname not in seen and row.present:
                    row.present = False
        return len(items)

    def sync_all(self) -> None:
        for did in list(self._drivers):
            try:
                self.sync_media(did)
            except Exception as e:
                log.warning("sync %s failed: %s", did, e)


manager = DeviceManager()
