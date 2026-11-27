"""Render loop: one clock drives every run, so effects stay in sync around
the whole roofline."""
from __future__ import annotations

import logging
import math
import threading
import time

from . import calibrate
from .audio import AudioState
from .color import Color
from .layout import Layout
from .output import Output
from .scene import Scene, blend

log = logging.getLogger(__name__)


def to_device_frames(layout: Layout, colors: list[Color], gamma: float = 1.0,
                     dither: list | None = None) -> dict[str, list[tuple[int, int, int]]]:
    """Virtual-strip colours -> per-run frames in physical bulb order (hidden bulbs dark).
    Applies each run's calibration (colour gain, brightness, gamma, far-end fade
    compensation; see calibrate.py), which is a no-op for an untouched run. With a `dither` error buffer (one [r, g, b] per
    pixel, kept between frames), values between two 8-bit levels alternate over time
    so slow dim fades look smooth instead of stepping (temporal dithering)."""
    frames = {r.mac: [(0, 0, 0)] * r.count for r in layout.runs}
    mult, rgam = calibrate.pixel_gains(layout)
    for k, (p, c) in enumerate(zip(layout.pixels, colors)):
        m = mult[k] if mult is not None else None
        vals = (c[0] * m[0], c[1] * m[1], c[2] * m[2]) if m else c
        rg = rgam[k] if rgam is not None else None
        out = []
        for ch in range(3):
            v = min(1.0, max(0.0, vals[ch]))
            if gamma != 1.0:
                v = v ** gamma
            if rg is not None:
                v = v ** rg
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


def calibrated_preview(layout: Layout, colors: list[Color]) -> list[Color]:
    """The colours with each run's calibration (brightness, gain, far-end fade, run gamma)
    applied as the boxes get them, but still linear (no global gamma), for the preview.
    The same list when no run is calibrated."""
    mult, rgam = calibrate.pixel_gains(layout)
    if mult is None and rgam is None:
        return colors
    out = []
    for k, c in enumerate(colors):
        m = mult[k] if mult is not None else None
        if m:
            c = (min(1.0, max(0.0, c[0] * m[0])), min(1.0, max(0.0, c[1] * m[1])), min(1.0, max(0.0, c[2] * m[2])))
        rg = rgam[k] if rgam is not None else None
        if rg is not None:
            c = tuple(min(1.0, max(0.0, v)) ** rg for v in c)
        out.append(c)
    return out


def _px(frame, k: int):
    """One bulb of a physical frame (ints) as a 0..1 colour, for the preview."""
    if not frame or not 0 <= k < len(frame):
        return (0.0, 0.0, 0.0)
    c = frame[k]
    return (c[0] / 255.0, c[1] / 255.0, c[2] / 255.0)


class _Still:
    """A frozen frame standing in for the outgoing scene when a fade starts mid-fade."""
    def __init__(self, cols):
        self.cols = list(cols)

    def render(self, t, layout, master=None, audio=None):
        return self.cols


class FrameGovernor:
    """Automatic frame rate for a slow computer (a Raspberry Pi, an old laptop).

    The render loop reports how long each frame took (render + send). While that stays
    well inside the frame budget (1/fps) nothing happens: the configured rate is used and
    the frames are exactly what they would be without it. If the average cost stays above
    `high` x the budget for `down_after` seconds, the rate steps down (20 -> 15 -> 12 fps);
    once the average is below `low` x the budget of the next faster step for `up_after`
    seconds it steps back up. Effects are driven by time, not frame count, so a lower rate
    only means fewer in-between frames, never a different look or a slower show."""

    STEPS = (20.0, 15.0, 12.0)

    def __init__(self, enabled: bool = True, down_after: float = 3.0, up_after: float = 10.0,
                 high: float = 0.9, low: float = 0.6, alpha: float = 0.2):
        self.enabled = enabled
        self.down_after, self.up_after, self.high, self.low, self.alpha = down_after, up_after, high, low, alpha
        self.configured = 0.0
        self.ladder: list[float] = []
        self.level = 0
        self.cost = 0.0                 # smoothed seconds per frame
        self._over_since: float | None = None
        self._cheap_since: float | None = None
        self.changes = 0
        self.last_change = 0.0          # time.time() of the last step
        self.reason = ""

    def _setup(self, fps: float):
        self.configured = fps
        self.ladder = [fps] + [s for s in self.STEPS if s < fps - 1e-9]
        self.level = 0
        self._over_since = self._cheap_since = None
        self.reason = ""

    def fps_for(self, configured: float) -> float:
        """The rate to run at now, for this configured rate."""
        configured = max(1.0, float(configured))
        if configured != self.configured:
            self._setup(configured)
        if not self.enabled:
            return configured
        return self.ladder[min(self.level, len(self.ladder) - 1)]

    def update(self, cost_s: float, now: float, configured: float) -> float:
        """Record one frame's cost (seconds) at monotonic time `now`; returns the rate to use."""
        fps = self.fps_for(configured)
        if not self.enabled:
            return fps
        self.cost = cost_s if self.cost <= 0 else self.cost + self.alpha * (cost_s - self.cost)
        budget = 1.0 / fps
        if self.cost > self.high * budget and self.level < len(self.ladder) - 1:
            self._cheap_since = None
            if self._over_since is None:
                self._over_since = now
            elif now - self._over_since >= self.down_after:
                self._step(+1, f"frames took {self.cost * 1000:.0f} ms, more than the "
                               f"{budget * 1000:.0f} ms a frame has at {fps:g} fps")
        else:
            self._over_since = None
            if self.level > 0 and self.cost < self.low / self.ladder[self.level - 1]:
                if self._cheap_since is None:
                    self._cheap_since = now
                elif now - self._cheap_since >= self.up_after:
                    self._step(-1, "back up: frames are cheap again")
            else:
                self._cheap_since = None
        return self.ladder[self.level]

    def _step(self, d: int, why: str):
        old = self.ladder[self.level]
        self.level += d
        self._over_since = self._cheap_since = None
        self.changes += 1
        self.last_change = time.time()
        self.reason = "" if self.level == 0 else why
        log.warning("frame rate %g -> %g fps (%s)", old, self.ladder[self.level], why)

    def state(self, configured: float | None = None) -> dict:
        """For /api/status: `configured` is the engine's frame-rate setting."""
        if configured is not None:
            self.fps_for(configured)
        fps = self.ladder[self.level] if self.ladder and self.enabled else self.configured
        return {"enabled": self.enabled, "configured_fps": self.configured, "fps": fps,
                "slowed": bool(self.enabled and self.level > 0), "cost_ms": round(self.cost * 1000, 1),
                "budget_ms": round(1000.0 / fps, 1) if fps else 0.0, "changes": self.changes,
                "last_change": round(self.last_change) if self.last_change else None, "reason": self.reason}


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
        self.audio = AudioState()               # music from a listening phone (POST /api/beat)
        self.hue_sweep: tuple[float, float] | None = None   # (start, seconds): one trip round the colour wheel
        self.transition = "crossfade"
        # The Bulb check: a callable t -> {mac: physical frame (ints)} that stands in for
        # the scene on the lights (and the preview) while it is set; nothing else changes,
        # so the scene, shows and schedule carry on underneath and are back the moment it
        # is cleared. takeover_live: send its frames even while the engine isn't live.
        self.takeover = None
        self.takeover_live = False
        self.scene: Scene | None = None
        self._prev: Scene | None = None
        self._mixed: list[Color] | None = None     # last crossfade frame (before overlays)
        self._fade_start, self._fade_s = 0.0, 0.0
        # perf_counter: before 3.13, Windows' monotonic()/time() tick only every 15.6 ms,
        # which would make the t handed to effects step unevenly at 20-60 fps.
        self.t0 = time.perf_counter()
        self._wall0, self._pc0 = time.time(), self.t0
        self.last_colors: list[Color] = []      # what was rendered (before each run's calibration)
        self.preview_colors: list[Color] = []   # the same with each run's calibration: the phone's preview
        self.stats = {"frames": 0, "render_ms": 0.0, "late": 0}
        self.governor = FrameGovernor()          # slows to 15/12 fps only if frames can't keep up
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
        audio = self.audio.snapshot(self.wall()) if self.audio is not None else None
        if scene is None:
            # Only timed overlays (pad hits, visitor moments) play and expire over black;
            # persistent ones (weather) need a scene under them.
            if not any(len(o) > 3 for o in list(self.overlays.values())):
                return [(0.0, 0.0, 0.0)] * self.layout.n
            cols = [(0.0, 0.0, 0.0)] * self.layout.n
        else:
            cols = scene.render(t, self.layout, self.master, audio)
        self._mixed = None
        if prev is not None and scene is not None:
            f = max(0.0, (t - fade_start) / fade_s)    # t is read before a set_scene on another thread
            if f >= 1:
                with self._lock:
                    if self._prev is prev:
                        self._prev = None
            else:
                old = prev.render(t, self.layout, self.master, audio)
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
            # overlays (pad hits, weather) keep their own look but hear the music and the tempo
            oc = sc.render(t, self.layout, {"bpm": self.master.get("bpm", 120.0)}, audio)
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

    def _tick_takeover(self, tk, t0: float) -> bool:
        try:
            frames = tk(self.now())
        except Exception:  # noqa: BLE001
            log.exception("bulb check frame failed")
            return False
        if frames is None:
            return False
        if self.blackout:                         # Blackout wins over the check too
            frames = {mac: [(0, 0, 0)] * len(fr) for mac, fr in frames.items()}
        self.last_colors = self.preview_colors = [_px(frames.get(p.mac), p.local) for p in self.layout.pixels]
        if self.live or self.takeover_live:
            for mac, frame in frames.items():
                self.output.send_frame(mac, frame)
        self.stats["frames"] += 1
        self.stats["render_ms"] = round((time.perf_counter() - t0) * 1000, 2)
        return True

    def tick(self):
        t0 = time.perf_counter()
        tk = self.takeover
        if tk is not None and self._tick_takeover(tk, t0):
            return
        cols = self.render(self.now())
        if self.blackout:
            cols = [(0.0, 0.0, 0.0)] * len(cols)
        elif self.brightness * self.dim != 1.0:
            b = self.brightness * self.dim
            cols = [(c[0] * b, c[1] * b, c[2] * b) for c in cols]
        self.last_colors = cols
        self.preview_colors = calibrated_preview(self.layout, cols)
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
                if release and not (self.takeover is not None and self.takeover_live):
                    self.output.stop()     # a lights-off bulb check still drives the boxes

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
            t0 = time.perf_counter()
            try:
                self.tick()
            except Exception:  # noqa: BLE001
                log.exception("render failed")
            t1 = time.perf_counter()
            fps = self.governor.update(t1 - t0, t1, self.fps)   # fps can change while running
            nxt += 1.0 / max(1.0, fps)
            delay = nxt - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            else:
                self.stats["late"] += 1
                nxt = time.perf_counter()
