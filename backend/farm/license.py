"""Check in with Braden's license service. A Mac has to be on his list before anything runs: the
first check-in registers it (a short trial starts at once); after that he puts it on a paid plan,
activates it, or switches it off and on.

Every answer is signed by the service and the app trusts only a signed answer made for this Mac,
so an edited saved answer or a stand-in service is refused. The last good answer is kept, so a bad
connection never pauses anyone on the spot; trial and plan ends are enforced offline, and an
answer older than a week stops counting (a blocked service can't keep a Mac alive for good).
Time is counted from the service's own clock at the last check-in and never runs backwards, so
setting the Mac's date back buys nothing."""

from __future__ import annotations

import base64
import json
import logging
import math
import os
import ssl
import subprocess
import threading
import time
import urllib.request

from . import ed25519
from . import settings as settings_mod
from .config import LICENSE_PUBKEYS, LICENSE_URL

log = logging.getLogger("farm.license")

CHECK_EVERY_S = 900
RETRY_S = 30            # while this Mac isn't registered yet
STATE_KEY = "license_state"
HIGH_KEY = "license_clock"   # the latest time this Mac's clock has shown since the last check-in
DEFAULT_PHONES = 3
DAY_MS = 86400000
STALE_DAYS = 7          # an answer older than this no longer counts
WARN_DAYS = 4           # from here the sidebar says how long is left

_lock = threading.Lock()
_mac_id = ""
_last_error = ""        # why the last check-in failed (shown on the registering screen)
_high = 0.0             # in-memory copy of HIGH_KEY
_high_saved = 0.0
_verified_for: tuple[str, str] | None = None
_verified_lic: dict | None = None


def mac_id() -> str:
    global _mac_id
    if _mac_id:
        return _mac_id
    try:
        out = subprocess.run(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"], capture_output=True, text=True, timeout=10).stdout
        for line in out.splitlines():
            if "IOPlatformUUID" in line:
                _mac_id = line.split('"')[-2]
                return _mac_id
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


def _verified(st: dict) -> dict | None:
    """The license inside a saved or fresh answer, or None unless the service signed it for this Mac."""
    global _verified_for, _verified_lic
    payload, sig = st.get("payload"), st.get("sig")
    if not isinstance(payload, str) or not isinstance(sig, str) or not payload or not sig:
        return None
    if _verified_for == (payload, sig):
        return _verified_lic
    lic = None
    try:
        raw = base64.b64decode(sig)
        if any(ed25519.verify(base64.b64decode(pk), payload.encode(), raw) for pk in LICENSE_PUBKEYS):
            got = json.loads(payload)
            me = mac_id()
            if isinstance(got, dict) and me != "unknown-mac" and str(got.get("mac_id")) == me and got.get("now"):
                lic = got
            elif me == "unknown-mac":
                return None  # couldn't read this Mac's id just now: don't remember that as a refusal
    except Exception:
        lic = None
    _verified_for, _verified_lic = (payload, sig), lic
    return lic


def _service_now_ms(st: dict, lic: dict) -> float:
    """The service's clock now: its time at the last check-in plus what has passed here since.
    'Since' is measured to the latest time this Mac's clock has shown, so it never shrinks."""
    global _high, _high_saved
    t = time.time()
    checked = float(st.get("checked_at") or t)
    with _lock:
        if not _high:
            try:
                _high = float(settings_mod.get(HIGH_KEY, "0") or 0)
            except ValueError:
                _high = 0.0
            _high_saved = _high
        _high = max(_high, t)
        high = _high
        save = high - _high_saved > 300
        if save:
            _high_saved = high
    if save:
        try:
            settings_mod.set_many({HIGH_KEY: repr(high)})
        except Exception:
            pass
    return float(lic["now"]) + max(0.0, high - checked) * 1000


def status() -> dict:
    """What this Mac may do right now, from the last signed answer."""
    st = _stored()
    lic = _verified(st)
    if lic is None:
        msg = "This Mac isn't registered with Phone Farm yet. Check the internet connection, then press Check again."
        if _last_error:
            msg += f" ({_last_error})"
        return {"state": "unregistered", "active": False, "message": msg, "max_phones": DEFAULT_PHONES,
                "trial_until": None, "plan_until": None, "checked_at": st.get("checked_at")}
    now = _service_now_ms(st, lic)
    s = {"state": lic.get("state"), "active": bool(lic.get("active")), "message": str(lic.get("message") or ""),
         "max_phones": lic.get("max_phones", DEFAULT_PHONES), "trial_until": lic.get("trial_until"),
         "plan_until": lic.get("plan_until"), "now": lic.get("now"), "checked_at": st.get("checked_at")}
    if s["state"] == "trial" and s["trial_until"] and now > float(s["trial_until"]):
        s.update(state="expired", active=False, message="Your trial is over. Ask Braden to activate Phone Farm on this Mac.")
    if s["state"] == "plan" and s["plan_until"] and now > float(s["plan_until"]):
        s.update(state="plan_ended", active=False, message="Your plan has ended. Ask Braden to renew Phone Farm on this Mac.")
    if s["active"]:
        age = (now - float(lic["now"])) / DAY_MS
        if age > STALE_DAYS:
            s.update(state="stale", active=False,
                     message=f"Phone Farm hasn't reached its license service for {STALE_DAYS} days. Connect this Mac to the internet, then press Check again.")
        elif age > WARN_DAYS:
            left = max(1, math.ceil(STALE_DAYS - age))
            warn = f"Can't reach the license service: pauses in {left} day{'' if left == 1 else 's'}"
            s["message"] = f"{warn} · {s['message']}" if s["message"] else warn
    return s


def last_error() -> str:
    return _last_error


def allowed() -> tuple[bool, str]:
    st = status()
    return bool(st.get("active")), str(st.get("message") or "")


def max_phones() -> int:
    try:
        return int(status().get("max_phones", DEFAULT_PHONES))
    except (TypeError, ValueError):
        return DEFAULT_PHONES


def _tls() -> ssl.SSLContext:
    """Certificates for the check-in. Not every Python finds a certificate list by itself (one
    installed from python.org has none until a separate setup step is run, and then every
    check-in fails with CERTIFICATE_VERIFY_FAILED), so the list that ships with macOS is loaded
    as well, and certifi's when it is installed."""
    ctx = ssl.create_default_context()
    for cafile in ("/etc/ssl/cert.pem", "/private/etc/ssl/cert.pem"):
        if os.path.exists(cafile):
            try:
                ctx.load_verify_locations(cafile)
                break
            except Exception:
                pass
    try:
        import certifi
        ctx.load_verify_locations(certifi.where())
    except Exception:
        pass
    return ctx


def checkin(phones: int, version: str) -> dict | None:
    """Ask the service; keep its answer if it is signed for this Mac. None when it couldn't be
    reached or the answer wasn't good (the last good answer stays)."""
    global _last_error, _high, _high_saved
    body = {"mac_id": mac_id(), "mac_name": mac_name(), "user_name": (settings_mod.get("your_name", "") or "").strip(),
            "phones": phones, "version": version}
    req = urllib.request.Request(LICENSE_URL + "/v1/checkin", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": f"PhoneFarm/{version or 'dev'}"})  # Cloudflare blocks the default Python agent
    try:
        with urllib.request.urlopen(req, timeout=20, context=_tls()) as r:
            ans = json.loads(r.read() or b"{}")
    except Exception as e:
        _last_error = f"couldn't reach the license service: {getattr(e, 'reason', e)}"[:200]
        log.warning("license check-in failed (keeping the last answer): %s", e)
        return None
    now = time.time()
    st = {"payload": ans.get("payload"), "sig": ans.get("sig"), "checked_at": now}
    if _verified(st) is None:
        _last_error = "the license service's answer couldn't be verified for this Mac"
        log.warning("license check-in: answer not signed for this Mac (keeping the last answer)")
        return None
    with _lock:
        _high = _high_saved = now  # the clock's high-water mark starts again from a known time
    settings_mod.set_many({STATE_KEY: json.dumps(st), HIGH_KEY: repr(now)})
    _last_error = ""
    return status()


def start(manager, version_fn) -> None:
    """Background check-in: right after start, then every 15 minutes (every half minute while
    this Mac isn't registered yet, so the registering screen clears by itself)."""
    def loop():
        time.sleep(2)
        while True:
            wait = CHECK_EVERY_S
            try:
                from .db import session_scope
                from .models import Device
                with session_scope() as s:
                    phones = s.query(Device).filter(Device.kind != "sim").count()
                ans = checkin(phones, version_fn())
                if ans is not None:
                    log.info("license: %s (%s)", ans.get("state"), ans.get("message") or "active")
                if status().get("state") == "unregistered":
                    wait = RETRY_S
            except Exception:
                log.exception("license check-in")
            time.sleep(wait)
    threading.Thread(target=loop, name="license-checkin", daemon=True).start()
