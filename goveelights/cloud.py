"""Govee cloud fallback (Govee Platform API v2) for a box that won't take LAN commands.

Slow, coarse and opt-in. A run marked for the cloud in the config
(device["cloud"] = {"enabled": true, "sku": ..., "device": ..., "segments": 15}) still
takes part in the show: the engine renders its bulbs like any other run, and this output
turns that into a few HTTPS commands a minute: on/off, brightness, the run's main
colour and, when the box has segment colours, a coarse gradient of up to four colours.

Limits (https://developer.govee.com/reference/rate-limiting, Nov 2026): 10,000 requests
per day per account, 10 control commands per minute per device. This output stays well
under: at most CONTROL_PER_MIN commands a minute, one round of changes every
MIN_ROUND_S seconds, and DAILY_BUDGET a day; it sends only what changed, and backs off
on HTTP 429 (Retry-After) and when the day's remaining count gets low.

The API key lives in its own secret file (govee_cloud.json next to the config, owner-only
permissions), never in the config, and is never logged or sent back to the browser.
The render thread only adds frames into per-segment sums; all HTTP happens on this
output's own thread.
"""
from __future__ import annotations

import collections
import json
import logging
import re
import threading
import time
import uuid
from datetime import date
from pathlib import Path
from typing import Callable, Sequence

from .config import write_json

log = logging.getLogger(__name__)

API = "https://openapi.api.govee.com/router/api/v1"
GOVEE_DAILY_LIMIT = 10_000          # per account (Govee's published limit)
GOVEE_CONTROL_PER_MIN = 10          # per device
DAILY_BUDGET = 2_000                # ours: a fifth of Govee's, leaves room for the Govee app & others
CONTROL_PER_MIN = 6                 # ours, per device
MIN_ROUND_S = 20.0                  # seconds between rounds of colour changes for one run
MAX_COLOURS = 4                     # a "coarse gradient": at most this many segment colours
LOW_REMAINING = 300                 # Govee's day counter this low: wait for its reset
KEY_RE = re.compile(r"^[A-Za-z0-9-]{16,80}$")

T_POWER = ("devices.capabilities.on_off", "powerSwitch")
T_BRIGHT = ("devices.capabilities.range", "brightness")
T_COLOUR = ("devices.capabilities.color_setting", "colorRgb")
T_SEGMENT = ("devices.capabilities.segment_color_setting", "segmentedColorRgb")


# ------------------------------------------------------------------ key file
class CloudKey:
    """The Govee API key, in a file of its own (chmod 600), plus today's request count."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.data: dict = {}
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
            self.data = d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            self.data = {}

    @property
    def key(self) -> str:
        k = self.data.get("api_key")
        return k if isinstance(k, str) else ""

    @property
    def present(self) -> bool:
        return bool(self.key)

    def masked(self) -> str:
        k = self.key
        return ("•" * 8 + k[-4:]) if len(k) >= 8 else ("•" * 8 if k else "")

    def set(self, key: str):
        key = (key or "").strip()
        if not KEY_RE.match(key):
            raise ValueError("that doesn't look like a Govee API key (letters, digits and dashes)")
        with self.lock:
            self.data["api_key"] = key
            self._save()

    def clear(self):
        with self.lock:
            self.data.pop("api_key", None)
            self._save()

    def usage(self) -> dict:
        u = self.data.get("usage")
        if not isinstance(u, dict) or u.get("day") != date.today().isoformat():
            return {"day": date.today().isoformat(), "sent": 0}
        return u

    def save_usage(self, u: dict):
        with self.lock:
            self.data["usage"] = dict(u)
            try:
                self._save()
            except OSError as e:
                log.debug("cloud usage not saved: %s", e)

    def _save(self):
        write_json(self.path, self.data, mode=0o600, indent=1)


# ------------------------------------------------------------------ HTTP client
class CloudError(Exception):
    def __init__(self, status: int, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


def _urllib_transport(method: str, url: str, headers: dict, body: bytes | None, timeout: float):
    import urllib.error
    import urllib.request
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, e.read() or b""


class GoveeCloud:
    """Tiny client for the three calls we need. transport(method, url, headers, body,
    timeout) -> (status, headers with lower-case names, body bytes); tests pass a fake."""

    def __init__(self, key: Callable[[], str], transport=None, timeout: float = 8.0):
        self._key = key
        self.transport = transport or _urllib_transport
        self.timeout = timeout
        self.day_remaining: int | None = None     # Govee's own count, from the response headers
        self.day_reset: float | None = None       # epoch seconds

    def _req(self, method: str, path: str, body: dict | None = None) -> dict:
        key = self._key()
        if not key:
            raise CloudError(0, "no Govee API key saved")
        headers = {"Govee-API-Key": key, "Content-Type": "application/json", "Accept": "application/json"}
        data = json.dumps(body).encode() if body is not None else None
        try:
            status, hdrs, raw = self.transport(method, API + path, headers, data, self.timeout)
        except OSError as e:
            raise CloudError(0, f"Govee cloud unreachable ({type(e).__name__})") from None
        try:
            if "x-ratelimit-remaining" in hdrs:
                self.day_remaining = int(hdrs["x-ratelimit-remaining"])
            if "x-ratelimit-reset" in hdrs:
                self.day_reset = float(hdrs["x-ratelimit-reset"])
        except (TypeError, ValueError):
            pass
        if status == 429:
            try:
                ra = float(hdrs.get("retry-after") or 0) or None
            except (TypeError, ValueError):
                ra = None
            raise CloudError(429, "Govee says: too many requests", ra)
        if status in (401, 403):
            raise CloudError(status, "Govee rejected the API key")
        try:
            out = json.loads(raw or b"{}")
        except ValueError:
            out = {}
        code = out.get("code", status) if isinstance(out, dict) else status
        if status >= 400 or (isinstance(code, int) and code >= 400):
            msg = out.get("message") or out.get("msg") if isinstance(out, dict) else ""
            raise CloudError(status if status >= 400 else int(code), f"Govee cloud error {status}: {str(msg)[:120]}")
        return out if isinstance(out, dict) else {}

    def devices(self) -> list[dict]:
        """Your Govee account's lights: [{sku, device, name, segments, caps}]."""
        out = []
        for d in self._req("GET", "/user/devices").get("data") or []:
            if not isinstance(d, dict) or not d.get("device"):
                continue
            caps = [c for c in d.get("capabilities") or [] if isinstance(c, dict)]
            out.append({"sku": str(d.get("sku", "")), "device": str(d["device"]),
                        "name": str(d.get("deviceName") or d.get("sku") or d["device"]),
                        "segments": segment_count(caps),
                        "caps": sorted({str(c.get("instance")) for c in caps if c.get("instance")})})
        return out

    def control(self, sku: str, device: str, cap: tuple[str, str], value) -> dict:
        body = {"requestId": str(uuid.uuid4()), "payload": {
            "sku": sku, "device": device, "capability": {"type": cap[0], "instance": cap[1], "value": value}}}
        return self._req("POST", "/device/control", body)

    def state(self, sku: str, device: str) -> dict:
        """{"online": bool | None, "power": 0/1 | None, "brightness": int | None}"""
        r = self._req("POST", "/device/state", {"requestId": str(uuid.uuid4()),
                                                 "payload": {"sku": sku, "device": device}})
        st = {"online": None, "power": None, "brightness": None}
        for c in ((r.get("payload") or {}).get("capabilities") or []):
            if not isinstance(c, dict):
                continue
            v = (c.get("state") or {}).get("value")
            inst = c.get("instance")
            if inst == "online":
                st["online"] = bool(v) if v not in (None, "") else None
            elif inst == "powerSwitch":
                st["power"] = v
            elif inst == "brightness":
                st["brightness"] = v
        return st


def segment_count(caps: list[dict]) -> int:
    """How many segments a device's segmentedColorRgb capability takes (0 = none)."""
    for c in caps:
        if c.get("instance") != "segmentedColorRgb":
            continue
        for f in ((c.get("parameters") or {}).get("fields") or []):
            if f.get("fieldName") != "segment":
                continue
            if f.get("options"):
                return len(f["options"])
            er = f.get("elementRange") or {}
            if "max" in er:
                return int(er["max"]) + 1
            sz = f.get("size") or {}
            if "max" in sz:
                return int(sz["max"])
        return 15
    return 0


# ------------------------------------------------------------------ output
def _q(c, step: int = 40) -> tuple:
    return tuple(min(255, int(v) // step) for v in c)


class CloudRunOutput:
    """Output for runs played through the Govee cloud. send_frame (render thread) only
    adds the frame into per-segment sums; pump() (its own thread) averages what was shown
    since the last round and sends what changed, within the rate budget."""

    def __init__(self, cloud: GoveeCloud, store: CloudKey | None = None, devices: dict | None = None,
                 thread: bool = True, clock=time.monotonic):
        self.cloud = cloud
        self.store = store
        self.devices: dict[str, dict] = devices or {}      # run id -> config entry with "cloud"
        self.clock = clock
        self.lock = threading.Lock()
        self.acc: dict[str, list] = {}                     # run id -> [[r, g, b, n] per segment]
        self.sent: dict[str, dict] = {}                    # run id -> what the box was last told
        self.recent: dict[str, collections.deque] = {}    # run id -> command times (per minute)
        self.next_round: dict[str, float] = {}
        self.backoff_until = 0.0
        self.backoff_n = 0
        self.stopped_reason = ""                           # e.g. the key was rejected
        self.last_error = ""
        self.last_ok: float | None = None
        self.want_power: int | None = None                 # 1 live, 0 released, None untouched
        self.flash: set[str] = set()
        self._hidden: dict[str, frozenset] = {}
        self.hold: dict[str, float] = {}                   # run id -> no changes before (after a blink)
        self.usage = store.usage() if store else {"day": date.today().isoformat(), "sent": 0}
        self._dirty_usage = 0
        self.ev = threading.Event()
        self._quit = False
        self.daily_budget = DAILY_BUDGET
        self.per_min = CONTROL_PER_MIN
        self.min_round_s = MIN_ROUND_S
        self._th = None
        if thread:
            self._th = threading.Thread(target=self._loop, name="govee-cloud", daemon=True)
            self._th.start()

    # ------------------------------------------------ the Output interface
    def set_devices(self, devices: dict):
        with self.lock:
            self.devices = dict(devices)
            self._hidden = {}
            for k in list(self.acc):
                if k not in self.devices:
                    self.acc.pop(k, None)

    def key_changed(self):
        self.stopped_reason = ""
        self.backoff_until, self.backoff_n = 0.0, 0
        self.sent.clear()
        self.ev.set()

    def start(self, force: bool = False):
        self.want_power = 1
        if force:
            self.sent.clear()
        self.ev.set()

    def stop(self):
        self.want_power = 0
        with self.lock:
            self.acc.clear()
        self.ev.set()

    def send_frame(self, run_id: str, pixels: Sequence[tuple]):
        d = self.devices.get(run_id)
        if d is None:
            return
        segs = self._segments(d)
        hidden = self._hidden.get(run_id)
        if hidden is None:
            hidden = self._hidden[run_id] = frozenset(d.get("hidden") or ())
        n = len(pixels)
        with self.lock:
            a = self.acc.get(run_id)
            if a is None or len(a) != segs:
                a = self.acc[run_id] = [[0, 0, 0, 0] for _ in range(segs)]
            for i, px in enumerate(pixels):
                if i in hidden:
                    continue
                s = a[i * segs // n] if n else a[0]
                s[0] += px[0]
                s[1] += px[1]
                s[2] += px[2]
                s[3] += 1

    def blink(self, run_id: str):
        """Flash the run white once (the next round puts its colours back)."""
        self.flash.add(run_id)
        self.ev.set()

    def close(self, timeout: float = 5.0):
        """App quitting: send the pending power-off, then end the thread."""
        self._quit = True
        self.ev.set()
        if self._th and self._th.is_alive():
            self._th.join(timeout)

    # ------------------------------------------------ worker
    def _loop(self):
        while True:
            self.ev.wait(1.0)
            self.ev.clear()
            try:
                self.pump()
            except Exception:  # noqa: BLE001 - never let the cloud thread die
                log.exception("cloud output failed")
            if self._quit:
                return

    @staticmethod
    def _segments(d: dict) -> int:
        c = d.get("cloud") or {}
        n = int(c.get("segments") or 0)
        return max(1, min(15, n)) if c.get("gradient", True) and n > 1 else 1

    def _budget_left(self, run_id: str, now: float) -> int:
        q = self.recent.setdefault(run_id, collections.deque())
        while q and now - q[0] >= 60:
            q.popleft()
        today = date.today().isoformat()
        if self.usage.get("day") != today:
            self.usage = {"day": today, "sent": 0}
        return max(0, min(self.per_min - len(q), self.daily_budget - int(self.usage.get("sent", 0))))

    def desired(self, run_id: str) -> dict | None:
        """What the run showed on average since the last round: {power, brightness, colour,
        segments: [rgb] or None}. None when no frame arrived."""
        with self.lock:
            a = self.acc.pop(run_id, None)
        if not a or not any(s[3] for s in a):
            return None
        avg = [(s[0] / s[3], s[1] / s[3], s[2] / s[3]) if s[3] else (0.0, 0.0, 0.0) for s in a]
        level = max(max(c) for c in avg)
        if level < 6:
            return {"power": 0}
        bri = max(1, min(100, round(level / 255 * 100)))
        norm = [tuple(min(255, round(v * 255 / level)) for v in c) for c in avg]
        # the main colour: the brightest-weighted average, at full value
        wt = [max(c) for c in avg]
        tot = sum(wt) or 1.0
        main = tuple(sum(c[k] * w for c, w in zip(norm, wt)) / tot for k in range(3))
        mx = max(main) or 1.0
        main = tuple(min(255, round(v * 255 / mx)) for v in main)
        segs = None
        if len(norm) > 1:
            groups = {_q(c) for c in norm}
            step = 40
            while len(groups) > MAX_COLOURS and step < 256:
                step *= 2
                groups = {_q(c, step) for c in norm}
            if 1 < len(groups) <= MAX_COLOURS:
                segs = [list(c) for c in norm]
                segs = [c if max(c) > 20 else [0, 0, 0] for c in segs]
                segs = (segs, step)
        out = {"power": 1, "brightness": bri, "colour": main}
        if segs:
            out["segments"], out["step"] = segs
        return out

    def pump(self, now: float | None = None) -> list[tuple]:
        """One pass over the cloud runs: sends what changed, within the budget. Returns
        [(run id, instance, value)] of the commands sent (for tests and the status)."""
        now = self.clock() if now is None else now
        done = []
        if self.stopped_reason or now < self.backoff_until:
            return done
        if not (self.store is None or self.store.present):
            return done
        if self.cloud.day_remaining is not None and self.cloud.day_remaining < LOW_REMAINING:
            reset = self.cloud.day_reset
            wait = max(600.0, (reset - time.time()) if reset else 3600.0)
            self._backoff(now, wait, f"Govee's daily count is nearly used up ({self.cloud.day_remaining} left)")
            self.cloud.day_remaining = None
            return done
        for run_id, d in list(self.devices.items()):
            c = d.get("cloud") or {}
            if not c.get("device") or not c.get("sku"):
                continue
            sent = self.sent.setdefault(run_id, {})
            cmds = []
            if run_id in self.flash:
                self.flash.discard(run_id)
                cmds = [(T_POWER, 1, {"power": 1}), (T_BRIGHT, 100, {"brightness": 100}),
                        (T_COLOUR, 0xFFFFFF, {"colq": _q((255, 255, 255)), "segq": None})]
                self.hold[run_id] = self.next_round[run_id] = now + 4.0     # then the show's colours again
            elif now < self.hold.get(run_id, 0.0):
                pass
            elif self.want_power == 0:
                with self.lock:
                    self.acc.pop(run_id, None)
                if sent.get("power") != 0:
                    cmds = [(T_POWER, 0, {"power": 0})]
            elif now >= self.next_round.get(run_id, 0.0):
                want = self.desired(run_id)
                if want is None:
                    if self.want_power == 1 and sent.get("power") != 1:
                        cmds = [(T_POWER, 1, {"power": 1})]
                else:
                    self.next_round[run_id] = now + self.min_round_s
                    cmds = self._diff(want, sent)
            for cap, value, upd in cmds:
                if self._budget_left(run_id, now) <= 0:
                    break                                     # the rest goes in a later round
                try:
                    self.cloud.control(c["sku"], c["device"], cap, value)
                except CloudError as e:
                    self._error(now, e)
                    return done
                self.recent[run_id].append(now)
                self.usage["sent"] = int(self.usage.get("sent", 0)) + 1
                self._dirty_usage += 1
                sent.update(upd)
                done.append((run_id, cap[1], value))
                self.last_ok, self.last_error, self.backoff_n = time.time(), "", 0
                if self.cloud.day_remaining is not None and self.cloud.day_remaining < LOW_REMAINING:
                    break                                     # the next pass waits for Govee's reset
        if self.store and self._dirty_usage and (self._dirty_usage >= 20 or self.want_power == 0 or self._quit):
            self._dirty_usage = 0
            self.store.save_usage(self.usage)
        return done

    def _diff(self, want: dict, sent: dict) -> list[tuple]:
        cmds = []
        if want.get("power") == 0:
            if sent.get("power") != 0:
                cmds.append((T_POWER, 0, {"power": 0}))
            return cmds
        if sent.get("power") != 1:
            cmds.append((T_POWER, 1, {"power": 1}))
        b = want["brightness"]
        if sent.get("brightness") is None or abs(int(sent["brightness"]) - b) >= 8:
            cmds.append((T_BRIGHT, b, {"brightness": b}))
        segs = want.get("segments")
        if segs:
            step = want.get("step", 40)
            q = [_q(c, step) for c in segs]
            old = sent.get("segq")
            if not old or len(old) != len(q):
                old = [None] * len(q)
            groups: dict[tuple, list[int]] = {}
            for i, k in enumerate(q):
                if old[i] != k:
                    groups.setdefault(k, []).append(i)
            cur = list(old)
            for k, idx in groups.items():
                # one command per colour (the average of those segments); each records,
                # once sent, its segments' new colour, so a round cut short by the
                # budget resends only what is still wrong
                rgb = [round(sum(segs[i][j] for i in idx) / len(idx)) for j in range(3)]
                for i in idx:
                    cur[i] = k
                cmds.append((T_SEGMENT, {"segment": idx, "rgb": (rgb[0] << 16) | (rgb[1] << 8) | rgb[2]},
                             {"segq": list(cur), "colq": None}))
        else:
            col = tuple(want["colour"])
            if sent.get("colq") != _q(col) or sent.get("segq") is not None:
                v = (col[0] << 16) | (col[1] << 8) | col[2]
                cmds.append((T_COLOUR, v, {"colq": _q(col), "segq": None}))
        return cmds

    def _backoff(self, now: float, secs: float, why: str):
        self.backoff_until = now + secs
        self.last_error = why
        log.warning("Govee cloud: %s; pausing %d s", why, secs)

    def _error(self, now: float, e: CloudError):
        if e.status in (401, 403):
            self.stopped_reason = "Govee rejected the API key: paste it again (More -> Fourth run)"
            self.last_error = self.stopped_reason
            log.warning("Govee cloud: API key rejected (HTTP %s); cloud runs paused", e.status)
            return
        self.backoff_n += 1
        if e.status == 429:
            secs = e.retry_after if e.retry_after and e.retry_after > 0 else min(3600.0, 60.0 * 2 ** (self.backoff_n - 1))
        else:
            secs = min(900.0, 30.0 * 2 ** (self.backoff_n - 1))
        self._backoff(now, secs, str(e))

    def status(self) -> dict:
        now = self.clock()
        return {"runs": [d.get("name") or k for k, d in self.devices.items()],
                "sent_today": int(self.usage.get("sent", 0)), "daily_budget": self.daily_budget,
                "govee_daily_limit": GOVEE_DAILY_LIMIT, "per_minute": self.per_min,
                "every_s": self.min_round_s,
                "paused_for": max(0, round(self.backoff_until - now)) if now < self.backoff_until else 0,
                "stopped": self.stopped_reason, "last_error": self.last_error,
                "last_ok_ago": round(time.time() - self.last_ok) if self.last_ok else None}
