"""Render loop: one clock drives every run, so effects stay in sync around
the whole roofline."""
from __future__ import annotations

import logging
import math
import threading
import time

from .color import Color
from .layout import Layout
from .output import Output
from .scene import Scene, blend

log = logging.getLogger(__name__)


def to_device_frames(layout: Layout, colors: list[Color], gamma: float = 1.0,
                     dither: list | None = None) -> dict[str, list[tuple[int, int, int]]]:
    """Virtual-strip colours -> per-run frames in physical bulb order (hidden bulbs dark).
    Applies each run's colour trim. With a `dither` error buffer (one [r, g, b] per
    pixel, kept between frames), values between two 8-bit levels alternate over time
    so slow dim fades look smooth instead of stepping (temporal dithering)."""
    frames = {r.mac: [(0, 0, 0)] * r.count for r in layout.runs}
    trims = {r.mac: getattr(r, "trim", None) for r in layout.runs}
    for k, (p, c) in enumerate(zip(layout.pixels, colors)):
        tr = trims[p.mac]
        vals = (c[0] * tr[0], c[1] * tr[1], c[2] * tr[2]) if tr else c
        out = []
        for ch in range(3):
            v = min(1.0, max(0.0, vals[ch]))
            if gamma != 1.0:
                v = v ** gamma
            v *= 255.0
            if dither is not None and v < 0.5:             # truly dark stays dark (no stray blips)
                q = 0
                dither[k][ch] = 0.0
                out.append(q)
                continue
            if dither is not None and v < 48.0:            # only where steps are visible
                v += dither[k][ch]
                q = int(min(255, max(0, round(v))))
                dither[k][ch] = max(-0.5, min(0.5, v - q))
            else:
                q = int(v + 0.5)
                if dither is not None:
                    dither[k][ch] = 0.0
            out.append(q)
        frames[p.mac][p.local] = (out[0], out[1], out[2])
    return frames


class _Still:
    """A frozen frame standing in for the outgoing scene when a fade starts mid-fade."""
    def __init__(self, cols):
        self.cols = list(cols)

    def render(self, t, layout, master=None):
        return self.cols


class Engine:
    def __init__(self, layout: Layout, output: Output, fps: float = 20.0, gamma: float = 1.0,
                 brightness: float = 1.0):
        self.layout, self.output = layout, output
        self.fps, self.gamma, self.brightness = fps, gamma, brightness
        self.live = False          # False: render for preview only, lights untouched
        self.master = {"speed": 1.0, "hue": 0.0, "palette": "", "bpm": 120.0}
        self.blackout = False
        self.dither = True                       # smooth dim fades (temporal dithering)
        self._dither_buf = [[0.0, 0.0, 0.0] for _ in layout.pixels]
        self.overlays: dict[str, tuple] = {}   # name -> (scene, blend, opacity[, until, start, decay]): weather, pad hits
        self.dim = 1.0                          # late-night dimming from the schedule
        self.hue_sweep: tuple[float, float] | None = None   # (start, seconds): one trip round the colour wheel
        self.transition = "crossfade"
        self.scene: Scene | None = None
        self._prev: Scene | None = None
        self._mixed: list[Color] | None = None     # last crossfade frame (before overlays)
        self._fade_start, self._fade_s = 0.0, 0.0
        # perf_counter: before 3.13, Windows' monotonic()/time() tick only every 15.6 ms,
        # which would make the t handed to effects step unevenly at 20-60 fps.
        self.t0 = time.perf_counter()
        self._wall0, self._pc0 = time.time(), self.t0
        self.last_colors: list[Color] = []
        self.stats = {"frames": 0, "render_ms": 0.0, "late": 0}
        self._run = False
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._live_lock = threading.Lock()

    def now(self) -> float:
        return time.perf_counter() - self.t0

    def wall(self) -> float:
        """time.time() with perf_counter resolution, for overlay fades and the hue sweep
        (their start/until are wall-clock times). Re-anchors if the clocks drift apart
        (clock set, NTP step, sleep)."""
        pc = time.perf_counter()
        w = self._wall0 + (pc - self._pc0)
        real = time.time()
        if abs(real - w) > 0.25:
            self._wall0, self._pc0, w = real, pc, real
        return w

    TRANSITIONS = ["cut", "crossfade", "wipe", "wipe-back", "rise", "iris", "flash", "black"]

    def set_scene(self, scene: Scene | None, fade_s: float = 0.0, continuous: bool = False,
                  transition: str = "crossfade"):
        """continuous=True: an edited version of the playing scene (keeps motion state).
        transition: how the old scene gives way to the new one over fade_s seconds."""
        if transition == "cut":
            fade_s = 0.0
        self.transition = transition if transition in self.TRANSITIONS else "crossfade"
        with self._lock:
            if continuous and scene is not None:
                # copy the effect memory if the old scene keeps rendering in a fade
                scene.adopt_state(self.scene, copy=fade_s > 0 and self.scene is not None)
            now = self.now()
            mid = self._prev is not None and now < self._fade_start + self._fade_s
            if continuous and fade_s <= 0 and mid and scene is not None:
                pass                       # a live edit keeps the running crossfade going
            elif self.scene is not None and scene is not None and fade_s > 0:
                out = self.scene
                if mid and self._mixed is not None and len(self._mixed) == self.layout.n:
                    out = _Still(self._mixed)  # fade from what is on screen, not the incoming scene
                self._prev, self._fade_start, self._fade_s = out, now, fade_s
            else:
                self._prev = None
            self.scene = scene

    def render(self, t: float) -> list[Color]:
        with self._lock:
            scene, prev = self.scene, self._prev
            fade_start, fade_s = self._fade_start, self._fade_s
        if scene is None:
            # Only timed overlays (pad hits, visitor moments) play and expire over black;
            # persistent ones (weather) need a scene under them.
            if not any(len(o) > 3 for o in list(self.overlays.values())):
                return [(0.0, 0.0, 0.0)] * self.layout.n
            cols = [(0.0, 0.0, 0.0)] * self.layout.n
        else:
            cols = scene.render(t, self.layout, self.master)
        self._mixed = None
        if prev is not None and scene is not None:
            f = max(0.0, (t - fade_start) / fade_s)    # t is read before a set_scene on another thread
            if f >= 1:
                with self._lock:
                    if self._prev is prev:
                        self._prev = None
            else:
                old = prev.render(t, self.layout, self.master)
                cols = self._transition(old, cols, f)
                self._mixed = cols
        now = self.wall()
        for name, ov in list(self.overlays.items()):
            sc, mode, op = ov[0], ov[1], ov[2]
            if len(ov) > 3:
                until, start, decay = ov[3], ov[4], ov[5]
                if now >= until:
                    if self.overlays.get(name) is ov:      # not re-triggered meanwhile
                        self.overlays.pop(name, None)
                    continue
                if decay:
                    op *= math.exp(-(now - start) / decay)
            elif scene is None:
                continue
            oc = sc.render(t, self.layout)
            cols = [blend(mode, a, b, op) for a, b in zip(cols, oc)]
        sweep = self.hue_sweep
        if sweep:
            st, dur = sweep
            k = (now - st) / dur
            if k >= 1:
                if self.hue_sweep is sweep:            # not restarted meanwhile by a pad hit
                    self.hue_sweep = None
            else:
                import colorsys
                sh = k
                cols = [colorsys.hsv_to_rgb((colorsys.rgb_to_hsv(*c)[0] + sh) % 1.0, *colorsys.rgb_to_hsv(*c)[1:])
                        if (c[0] or c[1] or c[2]) else c for c in cols]
        if self.overlays or self.hue_sweep:            # overlays can add light above 1.0
            cols = [(min(1.0, max(0.0, c[0])), min(1.0, max(0.0, c[1])), min(1.0, max(0.0, c[2]))) for c in cols]
        return cols

    def _transition(self, old, new, f):
        """Mix old → new at progress f (0..1) in the current transition style."""
        kind, px = self.transition, self.layout.pixels
        lerp = lambda a, b, k: (a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k)  # noqa: E731
        if kind in ("wipe", "wipe-back", "rise", "iris"):
            soft = 0.15
            out = []
            for p, a, b in zip(px, old, new):
                pos = {"wipe": 1 - p.nx, "wipe-back": p.nx, "rise": p.ny,
                       "iris": min(1.0, math.hypot(p.nx - 0.47, (p.ny - 1.0) * 0.5) * 1.6)}[kind]
                k = min(1.0, max(0.0, (f * (1 + soft) - pos) / soft))
                out.append(lerp(a, b, k))
            return out
        if kind == "flash":
            w = (1.0, 1.0, 1.0)
            return [lerp(a, w, f * 2) for a in old] if f < 0.5 else [lerp(w, b, (f - 0.5) * 2) for b in new]
        if kind == "black":
            return [lerp(a, (0, 0, 0), f * 2) for a in old] if f < 0.5 else [lerp((0, 0, 0), b, (f - 0.5) * 2) for b in new]
        return [lerp(a, b, f) for a, b in zip(old, new)]

    def tick(self):
        t0 = time.perf_counter()
        cols = self.render(self.now())
        if self.blackout:
            cols = [(0.0, 0.0, 0.0)] * len(cols)
        elif self.brightness * self.dim != 1.0:
            b = self.brightness * self.dim
            cols = [(c[0] * b, c[1] * b, c[2] * b) for c in cols]
        self.last_colors = cols
        if self.live:
            for mac, frame in to_device_frames(self.layout, cols, self.gamma,
                                               self._dither_buf if self.dither else None).items():
                self.output.send_frame(mac, frame)
        st = self.stats
        st["frames"] += 1
        st["render_ms"] = round((time.perf_counter() - t0) * 1000, 2)

    def set_live(self, on: bool, release: bool = True, force: bool = False):
        """Start/stop sending to the lights. Rendering (preview) continues either way.
        force=True re-takes every box even if we're already live."""
        with self._live_lock:          # schedule and UI can race here
            if on and (force or not self.live):
                try:
                    self.output.start(force=force)
                except TypeError:                  # outputs without a force option
                    self.output.start()
                self.live = True
            elif not on and self.live:
                self.live = False
                if release:
                    self.output.stop()

    def start(self, live: bool = True):
        """Start the render loop (and, if live, take control of the lights)."""
        if live:
            self.set_live(True)
        if self._run:
            return
        self._run = True
        self._thread = threading.Thread(target=self._loop, name="engine", daemon=True)
        self._thread.start()

    def stop(self, release: bool = True):
        self._run = False
        if self._thread:
            self._thread.join(timeout=2)
        self.set_live(False, release=release)

    def _loop(self):
        nxt = time.perf_counter()
        while self._run:
            try:
                self.tick()
            except Exception:  # noqa: BLE001
                log.exception("render failed")
            nxt += 1.0 / max(1.0, self.fps)      # fps can change while running
            delay = nxt - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            else:
                self.stats["late"] += 1
                nxt = time.perf_counter()
