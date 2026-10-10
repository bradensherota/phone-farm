"""Check in with Braden's license service: a new Mac is on a 7-day trial at once; after that
Braden activates it, and he can switch any Mac off and on. The last answer is kept, so a bad
connection never pauses anyone; only a real "no" from the service or the trial's end does."""

from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
import urllib.request

from . import settings as settings_mod
from .config import LICENSE_URL

log = logging.getLogger("farm.license")

CHECK_EVERY_S = 900
STATE_KEY = "license_state"
DEFAULT_PHONES = 3


def mac_id() -> str:
    try:
        out = subprocess.run(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"], capture_output=True, text=True, timeout=10).stdout
        for line in out.splitlines():
            if "IOPlatformUUID" in line:
                return line.split('"')[-2]
    except Exception:
        pass
    return "unknown-mac"


def mac_name() -> str:
    try:
        return subprocess.run(["scutil", "--get", "ComputerName"], capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return ""


def _stored() -> dict:
    try:
        return json.loads(settings_mod.get(STATE_KEY, "") or "{}")
    except Exception:
        return {}


def status() -> dict:
    """What this Mac may do right now, from the last answer (trial end enforced even offline)."""
    s = _stored()
    if not s:
        return {"state": "unknown", "active": True, "message": "", "max_phones": DEFAULT_PHONES, "trial_until": None, "checked_at": None}
    if s.get("state") == "trial" and s.get("trial_until") and time.time() * 1000 > float(s["trial_until"]):
        s = {**s, "state": "expired", "active": False, "message": "Your trial is over. Ask Braden to activate Phone Farm on this Mac."}
    return s


def allowed() -> tuple[bool, str]:
    st = status()
    return bool(st.get("active", True)), str(st.get("message") or "")


def max_phones() -> int:
    try:
        return int(status().get("max_phones", DEFAULT_PHONES))
    except (TypeError, ValueError):
        return DEFAULT_PHONES


def checkin(phones: int, version: str) -> dict | None:
    body = {"mac_id": mac_id(), "mac_name": mac_name(), "user_name": (settings_mod.get("your_name", "") or "").strip(),
            "phones": phones, "version": version}
    req = urllib.request.Request(LICENSE_URL + "/v1/checkin", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            ans = json.loads(r.read() or b"{}")
    except Exception as e:
        log.warning("license check-in failed (keeping the last answer): %s", e)
        return None
    ans["checked_at"] = time.time()
    settings_mod.set_many({STATE_KEY: json.dumps(ans)})
    return ans


def start(manager, version_fn) -> None:
    """Background check-in: once shortly after start, then every 15 minutes."""
    def loop():
        time.sleep(8)
        while True:
            try:
                from .db import session_scope
                from .models import Device
                with session_scope() as s:
                    phones = s.query(Device).filter(Device.kind != "sim").count()
                ans = checkin(phones, version_fn())
                if ans is not None:
                    log.info("license: %s (%s)", ans.get("state"), ans.get("message") or "active")
            except Exception:
                log.exception("license check-in")
            time.sleep(CHECK_EVERY_S)
    threading.Thread(target=loop, name="license-checkin", daemon=True).start()
