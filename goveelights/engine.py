"""Render loop: one clock drives every run, so effects stay in sync around
the whole roofline."""
from __future__ import annotations

import logging
import threading
import time

from .color import Color
from .layout import Layout
from .output import Output
from .scene import Scene, blend

log = logging.getLogger(__name__)


def to_device_frames(layout: Layout, colors: list[Color], gamma: float = 1.0) -> dict[str, list[tuple[int, int, int]]]:
    """Virtual-strip colours -> per-run frames in physical bulb order (hidden bulbs dark)."""
    frames = {r.mac: [(0, 0, 0)] * r.count for r in layout.runs}
    if gamma == 1.0:
        conv = lambda v: int(v * 255 + 0.5)  # noqa: E731
    else:
        conv = lambda v: int((v ** gamma) * 255 + 0.5)  # noqa: E731
    for p, c in zip(layout.pixels, colors):
        frames[p.mac][p.local] = (conv(c[0]), conv(c[1]), conv(c[2]))
    return frames


class Engine:
    def __init__(self, layout: Layout, output: Output, fps: float = 20.0, gamma: float = 1.0,
                 brightness: float = 1.0):
        self.layout, self.output = layout, output
        self.fps, self.gamma, self.brightness = fps, gamma, brightness
        self.live = False          # False: render for preview only, lights untouched
        self.master = {"speed": 1.0, "hue": 0.0, "palette": "", "bpm": 120.0}
        self.blackout = False
        self.overlays: dict[str, tuple[Scene, str, float]] = {}   # name -> (scene, blend, opacity), e.g. weather
        self.scene: Scene | None = None
        self._prev: Scene | None = None
        self._fade_start, self._fade_s = 0.0, 0.0
        self.t0 = time.monotonic()
        self.last_colors: list[Color] = []
        self.stats = {"frames": 0, "render_ms": 0.0, "late": 0}
        self._run = False
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._live_lock = threading.Lock()

    def now(self) -> float:
        return time.monotonic() - self.t0

    def set_scene(self, scene: Scene | None, fade_s: float = 0.0, continuous: bool = False):
        """continuous=True: an edited version of the playing scene (keeps motion state)."""
        with self._lock:
            if continuous and scene is not None:
                scene.adopt_state(self.scene)
            if self.scene is not None and scene is not None and fade_s > 0:
                self._prev, self._fade_start, self._fade_s = self.scene, self.now(), fade_s
            else:
                self._prev = None
            self.scene = scene

    def render(self, t: float) -> list[Color]:
        with self._lock:
            scene, prev = self.scene, self._prev
            fade_start, fade_s = self._fade_start, self._fade_s
        if scene is None:
            return [(0.0, 0.0, 0.0)] * self.layout.n
        cols = scene.render(t, self.layout, self.master)
        if prev is not None:
            f = (t - fade_start) / fade_s
            if f >= 1:
                with self._lock:
                    self._prev = None
            else:
                old = prev.render(t, self.layout, self.master)
                cols = [(a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f)
                        for a, b in zip(old, cols)]
        for sc, mode, op in list(self.overlays.values()):
            oc = sc.render(t, self.layout)
            cols = [blend(mode, a, b, op) for a, b in zip(cols, oc)]
        return cols

    def tick(self):
        t0 = time.perf_counter()
        cols = self.render(self.now())
        if self.blackout:
            cols = [(0.0, 0.0, 0.0)] * len(cols)
        elif self.brightness != 1.0:
            b = self.brightness
            cols = [(c[0] * b, c[1] * b, c[2] * b) for c in cols]
        self.last_colors = cols
        if self.live:
            for mac, frame in to_device_frames(self.layout, cols, self.gamma).items():
                self.output.send_frame(mac, frame)
        st = self.stats
        st["frames"] += 1
        st["render_ms"] = round((time.perf_counter() - t0) * 1000, 2)

    def set_live(self, on: bool, release: bool = True):
        """Start/stop sending to the lights. Rendering (preview) continues either way."""
        with self._live_lock:          # schedule and UI can race here
            if on and not self.live:
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
