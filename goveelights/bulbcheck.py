"""Bulb check: walk a run with a bright marker to find bulbs that are dark, stuck or the
wrong colour (More -> Bulb check), keep a per-run list of broken bulbs, and mark
sections that were re-hung so camera mapping can redo just those.

The check takes over the lights through Engine.takeover: the scene, shows, playlists and
the schedule keep running underneath, untouched, and are on the lights again the moment
the check ends. It ends by itself after IDLE_S without a tap (the phone went in a pocket),
after MAX_S in all, when a scheduled show is due, when the lights are switched off or
stopped, or when the owner picks something to play. It can't start while a show plays.

Config, per device entry:
  dead    [physical index, ...]  broken bulbs: kept dark and skipped (see layout.py)
  rehung  [[from, to], ...]      physical ranges re-hung since they were camera-mapped
"""
from __future__ import annotations

import logging
import threading
import time

from .layout import dead_bulbs

log = logging.getLogger(__name__)

WHITE = (255, 255, 255)
SWEEP = {"red": (255, 0, 0), "green": (0, 255, 0), "blue": (0, 0, 255), "white": WHITE}
SWEEP_ORDER = ("red", "green", "blue", "white")
DONE = (0, 40, 0)                # bulbs already checked: dim green
TODO = (22, 18, 12)              # not checked yet: dim warm white
NEAR = (60, 50, 30)              # neighbours of the single bulb being checked
GAP_B = (255, 140, 0)            # the gap check alternates white and amber
GROUP = 10
MAX_RANGES = 20


def rehung_ranges(d: dict) -> list[list[int]]:
    """The device entry's re-hung sections as clean [from, to] pairs within its count."""
    n = int(d.get("pixel_count") or 0)
    out = []
    v = d.get("rehung")
    for r in v if isinstance(v, (list, tuple)) else ():
        if not isinstance(r, (list, tuple)):
            continue
        try:
            a, b = int(r[0]), int(r[1])
        except (TypeError, ValueError, IndexError, OverflowError):
            continue
        a, b = min(a, b), max(a, b)
        if n:
            a, b = max(0, a), min(n - 1, b)
        if 0 <= a <= b:
            out.append([a, b])
    return out


class BulbCheck:
    IDLE_S = 240.0               # no tap for this long: give the house back
    MAX_S = 30 * 60.0            # and never longer than this in all

    def __init__(self, ctl, clock=time.time):
        self.ctl = ctl
        self.clock = clock
        self.st: dict | None = None
        self.lock = threading.RLock()
        self._rebuild = False    # marks changed during the check: rebuild the strip after it

    # ------------------------------------------------------------- info
    def _run(self, mac: str):
        r = self.ctl.engine.layout.run(mac)
        if r is None:
            raise ValueError("that run isn't in the show (no bulb count yet)")
        return r

    def bulbs(self, d: dict) -> list[int]:
        """The bulbs the walk visits: every physical bulb except the hidden ones (dead ones
        included, so a bulb marked by mistake shows up again and can be cleared)."""
        n = int(d.get("pixel_count") or 0)
        hid = set(d.get("hidden") or [])
        return [k for k in range(n) if k not in hid]

    def runs(self) -> list[dict]:
        out = []
        for r in self.ctl.engine.layout.runs:
            d = self.ctl.cfg.devices.get(r.mac, {})
            out.append({"mac": r.mac, "name": r.name, "count": r.count, "walk": len(self.bulbs(d)),
                        "hidden": sorted(int(k) for k in d.get("hidden") or []),
                        "dead": sorted(dead_bulbs(d)), "rehung": rehung_ranges(d),
                        "cloud": bool((d.get("cloud") or {}).get("enabled"))})
        return out

    def view(self) -> dict | None:
        st = self.st
        if not st:
            return None
        d = self.ctl.cfg.devices.get(st["mac"], {}) if st["mac"] != "all" else {}
        v = {"mac": st["mac"], "mode": st["mode"], "name": st["name"],
             "left": max(0, round(st["touched"] + self.IDLE_S - self.clock()))}
        if st["mode"] in ("groups", "single"):
            walk = self.bulbs(d)
            dead = dead_bulbs(d)
            if st["mode"] == "groups":
                g = st["pos"]
                part = walk[g * st["size"]:(g + 1) * st["size"]]
                v.update(group=g + 1, groups=max(1, -(-len(walk) // st["size"])),
                         first=part[0] if part else None, last=part[-1] if part else None,
                         dead_here=[k for k in part if k in dead])
            else:
                k = walk[st["pos"]] if walk else None
                v.update(bulb=k, step=st["pos"] + 1, steps=len(walk), dead_here=bool(k in dead))
        elif st["mode"] == "sweep":
            v["color"] = st["color"]
        elif st["mode"] == "gap":
            v.update(first=st["from"], last=st["to"])
        return v

    @property
    def active(self) -> bool:
        return self.st is not None

    # ------------------------------------------------------------ frames
    def frames(self, t: float = 0.0) -> dict | None:
        st = self.st
        if not st:
            return None
        L = self.ctl.engine.layout
        out = {r.mac: [(0, 0, 0)] * r.count for r in L.runs}
        if st["mode"] == "sweep":
            col = SWEEP.get(st["color"], WHITE)
            for r in L.runs:
                if st["mac"] in ("all", r.mac):
                    hid = set(self.ctl.cfg.devices.get(r.mac, {}).get("hidden") or [])
                    out[r.mac] = [(0, 0, 0) if k in hid else col for k in range(r.count)]
            return out
        r = L.run(st["mac"])
        if r is None:
            return out
        d = self.ctl.cfg.devices.get(r.mac, {})
        walk = self.bulbs(d)
        fr = [(0, 0, 0)] * r.count
        if st["mode"] == "groups":
            lo, hi = st["pos"] * st["size"], (st["pos"] + 1) * st["size"]
            for j, k in enumerate(walk):
                fr[k] = WHITE if lo <= j < hi else DONE if j < lo else TODO
        elif st["mode"] == "single":
            p = st["pos"]
            for j, k in enumerate(walk):
                fr[k] = WHITE if j == p else NEAR if abs(j - p) <= 4 else DONE if j < p else TODO
        elif st["mode"] == "gap":
            a, b = st["from"], st["to"]
            for j, k in enumerate(walk):
                if a <= k <= b:
                    fr[k] = WHITE if (k - a) % 2 == 0 else GAP_B
                elif a - 3 <= k <= b + 3:
                    fr[k] = NEAR
                else:
                    fr[k] = TODO
        out[r.mac] = fr
        return out

    # ----------------------------------------------------------- control
    def start(self, mac: str, mode: str = "groups", **kw) -> dict:
        e = self.ctl.engine
        if self.ctl.diag_active or self.ctl.map_state:
            raise ValueError("close diagnostics or camera mapping first")
        if (self.ctl.interrupt or {}).get("show") and self.st is None:
            raise ValueError("A show is playing. Try again after it ends.")
        if mac != "all" or mode != "sweep":
            r = self._run(mac)
            name = r.name
            if not self.bulbs(self.ctl.cfg.devices.get(mac, {})):
                raise ValueError("that run has no bulbs to check")
        else:
            name = "all runs"
        with self.lock:
            fresh = self.st is None
            now = self.clock()
            st = self.st if not fresh else {"started": now, "live_before": e.live}
            st.update(mac=mac, name=name, mode="groups", pos=0, size=GROUP, color="red", touched=now)
            self.st = st
            try:
                self._set_mode(mode, **kw)
            except (ValueError, TypeError):
                if fresh:
                    self.st = None
                raise
            if fresh:
                self._take()
            log.info("bulb check: %s on %s", st["mode"], name)
            return self.view()

    def _take(self):
        e = self.ctl.engine
        if not e.live:
            try:
                e.output.start(force=True)       # the lights were off: switch them on for the check
            except TypeError:
                e.output.start()
            e.takeover_live = True
        else:
            e.takeover_live = False
        e.takeover = self.frames

    def _set_mode(self, mode: str, **kw):
        st = self.st
        d = self.ctl.cfg.devices.get(st["mac"], {})
        if st["mac"] == "all" and mode != "sweep":
            raise ValueError("pick one run to walk")
        if mode == "groups":
            st["mode"], st["size"] = "groups", max(2, min(50, int(kw.get("size") or GROUP)))
            if "at" in kw and kw["at"] is not None:
                walk = self.bulbs(d)
                at = int(kw["at"])
                j = next((j for j, k in enumerate(walk) if k >= at), max(0, len(walk) - 1))
                st["pos"] = j // st["size"]
            else:
                st["pos"] = 0
        elif mode == "single":
            walk = self.bulbs(d)
            at = kw.get("at")
            if at is None and st.get("mode") == "groups":
                j = st["pos"] * st["size"]        # from the start of the group on the lights
            elif at is None:
                j = 0
            else:
                j = next((j for j, k in enumerate(walk) if k >= int(at)), len(walk) - 1)
            st["mode"], st["pos"] = "single", max(0, min(len(walk) - 1, j))
        elif mode == "sweep":
            st["mode"] = "sweep"
            st["color"] = kw.get("color") if kw.get("color") in SWEEP else "red"
        elif mode == "gap":
            n = int(d.get("pixel_count") or 0)
            a, b = int(kw.get("from", 0)), int(kw.get("to", n - 1))
            a, b = max(0, min(a, b)), min(n - 1, max(a, b))
            st.update(mode="gap", **{"from": a, "to": b})
        else:
            raise ValueError(f"unknown check {mode!r}")

    def act(self, action: str, **kw) -> dict:
        with self.lock:
            st = self.st
            if not st:
                raise ValueError("the bulb check isn't running (it stops by itself after a few minutes without a tap)")
            st["touched"] = self.clock()
            d = self.ctl.cfg.devices.get(st["mac"], {})
            walk = self.bulbs(d) if st["mac"] != "all" else []
            if action in ("next", "back"):
                step = 1 if action == "next" else -1
                if st["mode"] == "groups":
                    last = max(0, -(-len(walk) // st["size"]) - 1)
                    st["pos"] = max(0, min(last, st["pos"] + step))
                elif st["mode"] == "single":
                    st["pos"] = max(0, min(len(walk) - 1, st["pos"] + step))
                elif st["mode"] == "sweep":
                    i = SWEEP_ORDER.index(st["color"]) if st["color"] in SWEEP_ORDER else 0
                    st["color"] = SWEEP_ORDER[(i + step) % len(SWEEP_ORDER)]
            elif action == "mode":
                self._set_mode(str(kw.get("mode")), **{k: v for k, v in kw.items() if k != "mode"})
            elif action in ("dark", "ok"):
                if st["mode"] != "single" or not walk:
                    raise ValueError("step to one bulb first")
                self.mark(st["mac"], walk[st["pos"]], action == "dark")
                if action == "dark" and st["pos"] < len(walk) - 1:
                    st["pos"] += 1                    # on to the next one
            elif action == "rehung":
                if st["mode"] == "groups":
                    part = walk[st["pos"] * st["size"]:(st["pos"] + 1) * st["size"]]
                    a, b = (part[0], part[-1]) if part else (0, 0)
                elif st["mode"] == "single":
                    a = b = walk[st["pos"]]
                elif st["mode"] == "gap":
                    a, b = st["from"], st["to"]
                else:
                    raise ValueError("pick a group of bulbs first")
                self.mark_rehung(st["mac"], a, b, True)
            elif action == "color":
                if kw.get("color") not in SWEEP:
                    raise ValueError("pick red, green, blue or white")
                st["mode"], st["color"] = "sweep", kw["color"]
            else:
                raise ValueError(f"unknown step {action!r}")
            return self.view()

    def stop(self, why: str = "done"):
        with self.lock:
            st, self.st = self.st, None
            if not st:
                return
            e = self.ctl.engine
            e.takeover = None
            own, e.takeover_live = e.takeover_live, False
            if own and not e.live:
                e.output.stop()                   # it was off before the check: give the boxes back
            log.info("bulb check ended (%s)", why)
        if self._rebuild:
            self._rebuild = False
            self.ctl.build_engine()

    def tick(self, now: float):
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

    # ------------------------------------------------------------- marks
    def mark(self, mac: str, k: int, dead: bool):
        cfg = self.ctl.cfg
        with cfg.lock:
            d = cfg.device(mac)
            n = int(d.get("pixel_count") or 0)
            if not 0 <= int(k) < n:
                raise ValueError(f"bulb {int(k) + 1} is past the end of {d.get('name') or mac}")
            cur = dead_bulbs(d)
            cur = cur | {int(k)} if dead else cur - {int(k)}
            self._save_dead(d, cur)
        self._changed()

    def set_dead(self, mac: str, bulbs) -> list[int]:
        cfg = self.ctl.cfg
        with cfg.lock:
            d = cfg.device(mac)
            n = int(d.get("pixel_count") or 0)
            cur = {int(k) for k in bulbs or [] if 0 <= int(k) < n}
            self._save_dead(d, cur)
        self._changed()
        return sorted(cur)

    def _save_dead(self, d: dict, cur: set):
        if len(cur) > max(0, len(self.bulbs(d)) // 2):          # half the bulbs you can see
            raise ValueError("that's more than half the run: check the box and its first connector instead")
        if cur:
            d["dead"] = sorted(cur)
        else:
            d.pop("dead", None)
        self.ctl.cfg.save()

    def mark_rehung(self, mac: str, a: int, b: int, on: bool) -> list:
        cfg = self.ctl.cfg
        with cfg.lock:
            d = cfg.device(mac)
            n = int(d.get("pixel_count") or 0)
            a, b = max(0, min(int(a), int(b))), min(n - 1, max(int(a), int(b)))
            cur = rehung_ranges(d)
            if on:                                # joins any marked section it touches
                over = [r for r in cur if r[0] <= b + 1 and a - 1 <= r[1]]
                rest = [r for r in cur if r not in over]
                new = [min([a] + [r[0] for r in over]), max([b] + [r[1] for r in over])]
                cur = sorted(rest + [new])[-MAX_RANGES:]
            else:
                cur = [r for r in cur if r[1] < a or r[0] > b]
            if cur:
                d["rehung"] = sorted(cur)
            else:
                d.pop("rehung", None)
            cfg.save()
            return rehung_ranges(d)

    def _changed(self):
        if self.st:
            self._rebuild = True                  # the walk shows every bulb anyway; rebuild after it
        else:
            self.ctl.build_engine()


def section_indices(layout, mac: str, a: int, b: int, pad: int = 4) -> tuple[list[int], list[int]]:
    """(virtual indexes to re-map, virtual indexes of the re-hung bulbs themselves) for a
    run's physical bulbs a..b. A few neighbours each side are re-mapped too, so the
    re-hung part joins the rest of the run smoothly and short sections have enough bulbs."""
    r = layout.run(mac)
    if r is None:
        raise ValueError("that run isn't in the show")
    a, b = min(a, b), max(a, b)
    moved = [p.i for p in r.pixels if a <= p.local <= b]
    area = [p.i for p in r.pixels if a - pad <= p.local <= b + pad]
    return area, moved


def clear_rehung(cfg, layout, measured: dict):
    """A re-hung section is done once the camera has seen most of it (call with cfg.lock
    held; the caller saves)."""
    for r in layout.runs:
        d = cfg.devices.get(r.mac, {})
        secs = rehung_ranges(d)
        if not secs:
            continue
        keep = []
        for a, b in secs:
            vis = [p.i for p in r.pixels if a <= p.local <= b]
            if not vis or sum(1 for i in vis if i in measured) * 2 < len(vis):
                keep.append([a, b])
        if keep:
            d["rehung"] = keep
        else:
            d.pop("rehung", None)
