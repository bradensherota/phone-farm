"""Real iPhone driver (to be completed on the Mac that has Xcode).

Planned stack, all over USB:
  * pymobiledevice3  - device discovery, developer-mode tunnel (iOS 17+), AFC access
                       to /DCIM for a read-only camera roll listing, screenshots.
  * WebDriverAgent   - XCUITest runner installed once per phone from Xcode. Exposes the
                       accessibility tree of the frontmost app so flows tap by label
                       ("Your story", "Add link", "Share") instead of pixel coordinates.
  * facebook-wda     - thin Python client for WebDriverAgent (no Appium server needed).

Each phone gets its own WDA port. The scheduler already runs one worker per device,
so parallel phones just work once this class is filled in.

Flow scripts (post_reel / post_story / ...) will live in ios_flows/ and be written
against the real apps with the phones in hand, since exact labels change with app
updates and have to be verified on-device.
"""

from __future__ import annotations

from datetime import datetime

from .base import ActionResult, ActionSpec, Driver, LogFn, RollItem


def discover_ios_devices() -> list[dict]:
    """UDIDs + names of iPhones currently connected over USB. Empty if the ios extra isn't installed."""
    try:
        from pymobiledevice3.usbmux import list_devices  # type: ignore
        from pymobiledevice3.lockdown import create_using_usbmux  # type: ignore
    except Exception:
        return []
    found = []
    for d in list_devices():
        try:
            ld = create_using_usbmux(d.serial)
            found.append({
                "udid": d.serial,
                "name": ld.get_value(key="DeviceName") or d.serial,
                "model": ld.product_type,
                "ios": ld.product_version,
            })
        except Exception:
            found.append({"udid": d.serial, "name": d.serial, "model": "", "ios": ""})
    return found


class IOSDriver(Driver):
    kind = "ios"

    def __init__(self, device_id: str, name: str, wda_port: int):
        super().__init__(device_id, name)
        self.wda_port = wda_port
        self._client = None

    def connect(self) -> None:
        raise RuntimeError(
            "iOS driver not implemented yet. Requires Xcode + WebDriverAgent signed onto this phone."
        )

    def is_connected(self) -> bool:
        return self._client is not None

    def screenshot(self) -> bytes:
        raise NotImplementedError

    def list_camera_roll(self) -> list[RollItem]:
        # Plan: AFC walk of /DCIM/*APPLE, files sorted by mtime desc. Read-only.
        raise NotImplementedError

    def thumbnail(self, filename: str, max_px: int = 400) -> bytes | None:
        raise NotImplementedError

    def run_action(self, spec: ActionSpec, log: LogFn) -> ActionResult:
        raise NotImplementedError


__all__ = ["IOSDriver", "discover_ios_devices", "datetime"]
