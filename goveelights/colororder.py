"""Per-run colour order ("Colours look wrong?") and the phone test that finds it.

Some boxes read the three bytes of each bulb in a different order than red, green, blue:
a box wired R-B-G shows the red we send as red but swaps green and blue, so orange looks
pink, blue looks green-ish cyan and slightly orange reds look pink. That is wiring, not
taste, so it is fixed per run in its config entry:

    "color_order": "RBG"      # one of RGB, RBG, GRB, GBR, BRG, BGR (default RGB: no change)

The name is the order the box reads the bytes in: "RBG" means the box takes byte 0 as
red, byte 1 as blue and byte 2 as green. To show the colour we want, byte i carries the
channel order[i] names (apply()).

It is applied at the very last step of every path that sends colours to that box (after
calibration, gain, gamma and the broken-bulb mapping): the realtime (razer) frames in
output.GoveeRazerOutput, the Govee cloud's colour and segment commands (cloud.CloudRunOutput),
WLED frames, the Fourth run card's test colours and the diagnostics page's colour tests.
The preview on the phone is never permuted: it shows the colours the show means. A run
without "color_order" (or with "RGB") sends exactly the bytes it always did.

ColourTest is the phone test (More -> Match the runs -> "Colours look wrong?"): it lights
every run with raw byte 0 ("red"), then byte 1 ("green"), the owner says per run which
colour he sees, and the two answers give the order (the third is implied; an optional
raw byte 2 step checks it). It takes over the lights like the Bulb check (Engine.takeover),
so whatever was playing carries on underneath and is back when it ends.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Sequence

from . import calibrate

log = logging.getLogger(__name__)

ORDERS = ("RGB", "RBG", "GRB", "GBR", "BRG", "BGR")
DEFAULT = "RGB"
_IDX = {"R": 0, "G": 1, "B": 2}
_PERM = {o: tuple(_IDX[c] for c in o) for o in ORDERS}
NAMES = {"R": "red", "G": "green", "B": "blue"}


def norm(order) -> str:
    """A valid order (upper case) or the default; a hand-edited typo must not stop the lights."""
    o = str(order or "").upper().replace("-", "").replace(" ", "")
    return o if o in _PERM else DEFAULT


def check(order) -> str:
    """Like norm(), but a bad value is an error (for the API)."""
    o = str(order or "").upper().replace("-", "").replace(" ", "")
    if o not in _PERM:
        raise ValueError(f"colour order must be one of {', '.join(ORDERS)}")
    return o


def of(dev: dict | None) -> str:
    return norm((dev or {}).get("color_order"))


def label(order) -> str:
    """'R-B-G' for the phone."""
    return "-".join(norm(order))


def say(order) -> str:
    """Plain words: what the order does to the colours."""
    o = norm(order)
    if o == "RGB":
        return "normal (red, green, blue)"
    swaps = [f"{NAMES[a]}/{NAMES[b]}" for a, b in (("R", "G"), ("G", "B"), ("R", "B"))
             if o.index(a) == _IDX[b] and o.index(b) == _IDX[a]]
    if swaps:
        return f"{swaps[0]} swapped"
    return "all three colours rotated"


def apply_px(order: str, c: Sequence[int]) -> tuple:
    """One colour as the box must receive it."""
    i, j, k = _PERM[order]
    return (c[i], c[j], c[k])


def apply(order: str, pixels: Sequence[Sequence[int]]):
    """A frame as the box must receive it (the same object back for RGB)."""
    if order == DEFAULT or order not in _PERM:
        return pixels
    i, j, k = _PERM[order]
    return [(c[i], c[j], c[k]) for c in pixels]


def raw_for(order: str, c: Sequence[int]) -> tuple:
    """The colour to hand the output so that, after apply(order), the box gets exactly the
    bytes c (the test needs the raw bytes, whatever order is saved)."""
    order = norm(order)
    out = [0, 0, 0]
    for pos, ch in enumerate(_PERM[order]):
        out[ch] = c[pos]
    return tuple(out)


def derive(first: str, second: str, third: str | None = None) -> str:
    """The order from what the owner saw: raw byte 0 looked `first`, byte 1 looked `second`
    (and, to check, byte 2 looked `third`); each one of 'red', 'green', 'blue'."""
    letters = {"red": "R", "green": "G", "blue": "B"}
    try:
        a, b = letters[first], letters[second]
    except KeyError:
        raise ValueError("answer red, green or blue") from None
    if a == b:
        raise ValueError(f"both tests can't look {first}: the box reads three different colours. Try again.")
    c = ({"R", "G", "B"} - {a, b}).pop()
    if third is not None:
        if letters.get(third) != c:
            raise ValueError(f"that doesn't add up: the last colour should have looked {NAMES[c]}. "
                             "Try the test again from the street.")
    return a + b + c


# --------------------------------------------------------------- the phone test
STEPS = ("red", "green", "blue")           # raw byte 0, 1, 2
RAW = {"red": (255, 0, 0), "green": (0, 255, 0), "blue": (0, 0, 255)}
BRONCOS = ((255, 74, 0), (32, 80, 255))    # the compare picture: orange and blue halves
SEG = 6                                    # bulbs per colour in the compare picture


class ColourTest:
    """More -> Match the runs -> "Colours look wrong?". States (st["step"]):
    red / green / blue: every run shows raw byte 0 / 1 / 2 and the owner answers per run;
    compare: every run shows orange and blue stripes, with the saved order ("after") or
    the order from before the test ("before"). Ends like the Bulb check: Done, lights off,
    stop, picking something, a show starting, diagnostics, mapping, or no tap for IDLE_S
    (the phone also stops it when the page goes away). Refused during a Bulb check (and
    the Bulb check and Match the runs are refused during it); starting it ends a Match test."""

    IDLE_S = 240.0
    MAX_S = 20 * 60.0

    def __init__(self, ctl, clock=time.time):
        self.ctl = ctl
        self.clock = clock
        self.st: dict | None = None
        self.lock = threading.RLock()

    @property
    def active(self) -> bool:
        return self.st is not None

    def _runs(self):
        return [r for r in self.ctl.engine.layout.runs
                if self.ctl.cfg.devices.get(r.mac, {}).get("kind", "govee") in ("govee", "wled")]

    def runs(self) -> list[dict]:
        out = []
        for r in self._runs():
            o = of(self.ctl.cfg.devices.get(r.mac))
            out.append({"mac": r.mac, "name": r.name, "order": o, "label": label(o), "say": say(o)})
        return out

    def view(self) -> dict | None:
        st = self.st
        if not st:
            return None
        return {"step": st["step"], "answers": {m: dict(a) for m, a in st["answers"].items()},
                "before": dict(st["before"]), "showing": st.get("showing", "after"),
                "result": dict(st.get("result") or {}), "problems": dict(st.get("problems") or {}),
                "nudges": dict(st.get("nudges") or {}),
                "left": max(0, round(st["touched"] + self.IDLE_S - self.clock()))}

    # ------------------------------------------------------------ frames
    def frames(self, t: float = 0.0) -> dict | None:
        st = self.st
        if not st:
            return None
        out = {}
        for r in self.ctl.engine.layout.runs:
            d = self.ctl.cfg.devices.get(r.mac, {})
            hid = set(d.get("hidden") or [])
            o = of(d)
            if st["step"] in RAW:
                col = raw_for(o, RAW[st["step"]])          # the box gets exactly this byte lit
                fr = [(0, 0, 0) if k in hid else col for k in range(r.count)]
            else:                                          # compare: orange and blue stripes
                want = st["before"].get(r.mac, o) if st.get("showing") == "before" else o
                fr = []
                for k in range(r.count):
                    c = BRONCOS[(k // SEG) % 2]
                    # 'before': what the old order made of it, sent raw through the new one
                    fr.append((0, 0, 0) if k in hid else raw_for(o, apply_px(want, c)) if want != o else c)
            out[r.mac] = fr
        return out

    # ----------------------------------------------------------- control
    def start(self) -> dict:
        c = self.ctl
        if c.diag_active or c.map_state:
            raise ValueError("close diagnostics or camera mapping first")
        if (c.interrupt or {}).get("show") and self.st is None:
            raise ValueError("A show is playing. Try again after it ends.")
        if not self._runs():
            raise ValueError("no runs in the show yet")
        if c.bulbcheck.active:                             # one test on the lights at a time: neither ends the other
            raise ValueError("The bulb check is using the lights. Tap Done first.")
        with c.lock:
            if c.match_state:                              # Match the runs is an overlay: this ends it
                c._end_match()
        with self.lock:
            now = self.clock()
            fresh = self.st is None
            if fresh:
                borrowed = c._borrowed_lights()            # on only for a forced celebration: they go off again
                self.st = {"started": now, "live_before": c.engine.live and not borrowed, "borrowed": borrowed,
                           "before": {r.mac: of(c.cfg.devices.get(r.mac)) for r in self._runs()}}
            self.st.update(step="red", answers={}, result={}, problems={}, nudges={}, showing="after", touched=now)
            if fresh:
                self._take(self.st["borrowed"])
            log.info("colour order test started")
            return self.view()

    def _take(self, borrowed: bool = False):
        e = self.ctl.engine
        if not e.live or borrowed:
            if not e.live:
                try:
                    e.output.start(force=True)
                except TypeError:
                    e.output.start()
            e.takeover_live = True                 # the test keeps the boxes when borrowed lights go off
        else:
            e.takeover_live = False
        e.takeover = self.frames

    def answer(self, mac: str, saw: str) -> dict:
        """What one run looks like in the current step: red, green, blue or other."""
        with self.lock:
            st = self._need()
            if st["step"] not in RAW:
                raise ValueError("the test is showing the result: start it again to answer")
            if saw not in ("red", "green", "blue", "other"):
                raise ValueError("answer red, green, blue or other")
            if mac not in st["before"]:
                raise ValueError("that run isn't in the test")
            st["answers"].setdefault(mac, {})[st["step"]] = saw
            return self.view()

    def step(self, to: str) -> dict:
        """Show the next test colour (green after red; blue is the optional check)."""
        with self.lock:
            st = self._need()
            if to not in STEPS:
                raise ValueError("unknown step")
            st["step"] = to
            return self.view()

    def finish(self) -> dict:
        """Work out each run's order from its answers, save the ones that changed, and show
        the orange-and-blue compare picture with the new orders."""
        with self.lock:
            st = self._need()
            result, problems = {}, {}
            names = {r.mac: r.name for r in self._runs()}
            for mac in st["before"]:
                a = st["answers"].get(mac) or {}
                r1, r2, r3 = a.get("red"), a.get("green"), a.get("blue")
                if r1 is None or r2 is None:
                    problems[mac] = "not answered: left as it was"
                    continue
                if "other" in (r1, r2) or r3 == "other":
                    problems[mac] = ("it didn't show a clear red, green or blue: left as it was. "
                                     "If it stayed dark, check it in the Fourth run card or diagnostics.")
                    continue
                try:
                    result[mac] = derive(r1, r2, r3)
                except ValueError as ex:
                    problems[mac] = str(ex)
            cfg = self.ctl.cfg
            with cfg.lock:
                for mac, o in result.items():
                    set_order(cfg.devices.get(mac) or {}, o)
                if result:
                    cfg.save()
            nudges = {}
            for mac, o in result.items():
                log.info("colour order of %s: %s (was %s)", names.get(mac, mac), o, st["before"].get(mac))
                if o != st["before"].get(mac) and tuned(cfg.devices.get(mac)):
                    nudges[mac] = NUDGE_NOTE
            st.update(step="compare", result=result, problems=problems, nudges=nudges, showing="after")
            return self.view()

    def show(self, which: str) -> dict:
        """compare: 'before' (the old orders) or 'after' (the saved ones)."""
        with self.lock:
            st = self._need()
            if st["step"] != "compare":
                raise ValueError("finish the test first")
            if which not in ("before", "after"):
                raise ValueError("before or after")
            st["showing"] = which
            return self.view()

    def undo(self) -> dict:
        """Put every run's colour order back to what it was before the test."""
        with self.lock:
            st = self._need()
            cfg = self.ctl.cfg
            with cfg.lock:
                for mac, o in st["before"].items():
                    if mac in cfg.devices:
                        set_order(cfg.devices[mac], o)
                cfg.save()
            st.update(result={}, nudges={}, showing="after")
            return self.view()

    def _need(self) -> dict:
        st = self.st
        if not st:
            raise ValueError("the colour order test isn't running (it stops by itself after a few minutes without a tap)")
        st["touched"] = self.clock()
        return st

    def stop(self, why: str = "done"):
        with self.lock:
            st, self.st = self.st, None
            if not st:
                return
            e = self.ctl.engine
            if e.takeover == self.frames:
                e.takeover = None
                own, e.takeover_live = e.takeover_live, False
                if own and not e.live:
                    e.output.stop()
            log.info("colour order test ended (%s)", why)

    def tick(self, now: float):
        with self.lock:                          # (start() sets the state and the takeover together)
            self._tick(now)

    def _tick(self, now: float):
        st = self.st
        if not st:
            return
        e = self.ctl.engine
        if now - st["touched"] > self.IDLE_S:
            self.stop("no tap for a few minutes")
        elif now - st["started"] > self.MAX_S:
            self.stop("time limit")
        elif st["live_before"] and not e.live:
            self.stop("the lights were switched off")
        elif self.ctl.diag_active or self.ctl.map_state:
            self.stop("diagnostics or mapping")
        elif e.takeover != self.frames:
            self.stop("something else took the lights")


NUDGE_NOTE = ("its colour nudges were set before this fix, so they now act on the other colour: "
              "check it in Match the runs (or reset that run there)")


def tuned(dev: dict | None) -> bool:
    """The run has Match-the-runs colour nudges (gains that differ between channels): set
    while its channels were mixed up, they act on another LED once the order changes."""
    g = calibrate.read(dev or {})["gain"]
    return max(g) - min(g) > 1e-6


def set_order(dev: dict, order) -> str:
    """Store a run's colour order; the default removes the key, so an untouched entry
    looks as it always did."""
    o = check(order)
    if o == DEFAULT:
        dev.pop("color_order", None)
    else:
        dev["color_order"] = o
    return o
