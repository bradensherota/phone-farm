"""Posting flows for real iPhones, driven through WebDriverAgent.

Every flow is a sequence of "find element by label, tap, type" steps against the live
app. Labels are verified on-device and will need touch-ups when Instagram/TikTok/X
ship UI changes. Keep each flow small and log every step so failures are obvious.

Status: helpers ready; the per-platform flows are written against the live apps once
the accounts are logged in on the phone.
"""

from __future__ import annotations

import time

import wda  # facebook-wda

from .base import ActionResult, ActionSpec, LogFn

BUNDLES = {
    "instagram": "com.burbn.instagram",
    "tiktok": "com.zhiliaoapp.musically",
    "x": "com.atebits.Tweetie2",
}


class Phone:
    """Thin convenience wrapper around a facebook-wda client for one phone."""

    def __init__(self, base_url: str, log: LogFn):
        self.c = wda.Client(base_url)
        self.log = log
        self.s: wda.Session | None = None

    def open(self, bundle_id: str) -> None:
        self.log("info", f"Open {bundle_id}")
        self.s = self.c.session(bundle_id)
        time.sleep(2)

    def tap(self, label: str | None = None, *, name: str | None = None, timeout: float = 10, **kw) -> None:
        sel = self._sel(label=label, name=name, **kw)
        self.log("info", f"Tap {label or name or kw}")
        sel.get(timeout=timeout).tap()
        time.sleep(0.8)

    def exists(self, label: str | None = None, *, name: str | None = None, timeout: float = 3, **kw) -> bool:
        return bool(self._sel(label=label, name=name, **kw).wait(timeout=timeout, raise_error=False))

    def type(self, text: str, label: str | None = None, *, name: str | None = None, **kw) -> None:
        sel = self._sel(label=label, name=name, **kw)
        self.log("info", f"Type {len(text)} chars into {label or name or kw}")
        el = sel.get(timeout=10)
        el.tap()
        el.set_text(text)
        time.sleep(0.5)

    def _sel(self, label=None, name=None, **kw):
        assert self.s is not None, "open() an app first"
        q = {}
        if label:
            q["label"] = label
        if name:
            q["name"] = name
        q.update(kw)
        return self.s(**q)

    def screenshot(self) -> bytes:
        import io
        buf = io.BytesIO()
        self.c.screenshot().save(buf, "PNG")
        return buf.getvalue()


def run(driver, spec: ActionSpec, log: LogFn) -> ActionResult:
    phone = Phone(driver.base, log)
    fn = FLOWS.get(spec.action)
    if fn is None:
        return ActionResult(False, f"No flow for {spec.action}")
    ok, message = fn(phone, spec)
    shot = phone.screenshot()
    return ActionResult(ok, message, shot)


# ---- flows ---------------------------------------------------------------------
# Each returns (ok, message). They are filled in against the live apps.

def ig_reel(phone: Phone, spec: ActionSpec):
    phone.open(BUNDLES["instagram"])
    return False, "Instagram reel flow not written yet"


def ig_story(phone: Phone, spec: ActionSpec):
    phone.open(BUNDLES["instagram"])
    return False, "Instagram story flow not written yet"


def tt_post(phone: Phone, spec: ActionSpec):
    phone.open(BUNDLES["tiktok"])
    return False, "TikTok post flow not written yet"


def tt_story(phone: Phone, spec: ActionSpec):
    phone.open(BUNDLES["tiktok"])
    return False, "TikTok story flow not written yet"


def x_post(phone: Phone, spec: ActionSpec):
    phone.open(BUNDLES["x"])
    return False, "X post flow not written yet"


FLOWS = {
    "ig_reel": ig_reel,
    "ig_story": ig_story,
    "tt_post": tt_post,
    "tt_story": tt_story,
    "x_post": x_post,
}
