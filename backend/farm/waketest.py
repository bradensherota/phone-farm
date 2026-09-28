"""Overnight wake test: lock the phone, then at growing intervals wake it, check it
unlocked, and lock it again. Every step is logged so the app shows whether the runner
survived each stretch of locked idle time. State lives in the settings table as JSON."""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timedelta

from . import settings as settings_mod
from .devices.registry import DeviceManager

log = logging.getLogger("farm.waketest")

DEFAULT_STEPS = [15, 30, 60, 180]
_running: set[str] = set()


def _key(device_id: str) -> str:
    return f"wake_test:{device_id}"


def get(device_id: str) -> dict | None:
    raw = settings_mod.get(_key(device_id), "")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def _save(device_id: str, state: dict) -> dict:
    settings_mod.set_many({_key(device_id): json.dumps(state)})
    return state


def _event(state: dict, step: int, phase: str, ok: bool, detail: str) -> None:
    state["events"].append({"at": datetime.now().isoformat(timespec="seconds"), "step": step, "phase": phase, "ok": ok, "detail": detail})
    (log.info if ok else log.warning)("wake test %s step %s %s: %s", state["device_id"], step, phase, detail)


def start(manager: DeviceManager, device_id: str, steps: list[int] | None = None) -> dict:
    steps = [int(m) for m in (steps or DEFAULT_STEPS) if int(m) > 0] or DEFAULT_STEPS
    drv = manager.driver(device_id)
    if drv is None or not hasattr(drv, "sleep"):
        raise RuntimeError("no runner for this phone")
    lock = manager.op_lock(device_id)
    if not lock.acquire(timeout=2):
        raise RuntimeError("phone is busy (posting or preparing)")
    try:
        now = datetime.now()
        state = {
            "device_id": device_id, "steps": steps, "index": 0, "status": "running",
            "started_at": now.isoformat(timespec="seconds"),
            "next_at": (now + timedelta(minutes=steps[0])).isoformat(timespec="seconds"),
            "events": [],
        }
        err = drv.sleep() or ""
        locked = drv.is_locked()
        _event(state, 0, "start", locked, "Locked to begin the test" if locked else f"Could not lock: {err or 'screen did not lock'}")
        return _save(device_id, state)
    finally:
        lock.release()


def cancel(device_id: str) -> dict | None:
    state = get(device_id)
    if state and state["status"] == "running":
        state["status"] = "cancelled"
        _event(state, state["index"] + 1, "cancel", True, "Stopped by hand")
        _save(device_id, state)
    return state


def due(now: datetime) -> list[str]:
    """Device ids whose next wake-test step is due."""
    out = []
    for k, v in settings_mod.get_all().items():
        if not k.startswith("wake_test:"):
            continue
        try:
            st = json.loads(v)
            if st.get("status") == "running" and datetime.fromisoformat(st["next_at"]) <= now and st["device_id"] not in _running:
                out.append(st["device_id"])
        except Exception:
            continue
    return out


def run_step(manager: DeviceManager, device_id: str) -> None:
    """One step: note the lock state, wake + unlock, verify, wait a moment, lock again, verify."""
    if device_id in _running:
        return
    _running.add(device_id)
    try:
        state = get(device_id)
        if not state or state["status"] != "running":
            return
        step = state["index"] + 1
        minutes = state["steps"][state["index"]]
        drv = manager.driver(device_id)
        if drv is None or not hasattr(drv, "wake"):
            _event(state, step, "wake", False, "Phone runner is not connected")
            _advance(device_id, state)
            return
        lock = manager.op_lock(device_id)
        if not lock.acquire(timeout=5):
            _event(state, step, "wake", False, "Phone was busy; will retry next tick")
            _save(device_id, state)
            return
        try:
            was_locked = drv.is_locked()
            had_standby = bool(getattr(drv, "_standby_sid", None))
            t0 = time.time()
            try:
                drv.wake()
                awake = not drv.is_locked()
                _event(state, step, "wake", awake,
                       f"After {minutes} min locked: {'woke and unlocked' if awake else 'still locked after wake attempt'}"
                       f" ({time.time() - t0:.0f}s; was {'locked' if was_locked else 'already awake'}; standby session {'present' if had_standby else 'missing'})")
            except Exception as e:
                _event(state, step, "wake", False, f"Wake raised: {e}")
            time.sleep(5)
            try:
                err = drv.sleep() or ""
                locked = drv.is_locked()
                _event(state, step, "sleep", locked, "Locked again" if locked else f"Could not lock: {err or 'screen did not lock'}")
            except Exception as e:
                _event(state, step, "sleep", False, f"Sleep raised: {e}")
        finally:
            lock.release()
        _advance(device_id, state)
    except Exception:
        log.exception("wake test step failed for %s", device_id)
    finally:
        _running.discard(device_id)


def _advance(device_id: str, state: dict) -> None:
    state["index"] += 1
    if state["index"] >= len(state["steps"]):
        state["status"] = "done"
        state["next_at"] = None
        _event(state, state["index"], "done", True, "Test finished; the phone stays locked until its next scheduled job")
    else:
        state["next_at"] = (datetime.now() + timedelta(minutes=state["steps"][state["index"]])).isoformat(timespec="seconds")
    _save(device_id, state)
