"""Per-device realtime (razer) streamer: static frames, animations at a target
fps, keepalive resends, timing statistics."""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Sequence

from . import protocol as p
from .lan import GoveeLan

log = logging.getLogger(__name__)

Frame = list[p.RGB]
Animation = Callable[[int], Frame]   # frame_number -> colours


class Streamer:
    def __init__(self, lan: GoveeLan, ip: str):
        self.lan, self.ip = lan, ip
        self.realtime = False
        self.gradient = False
        self.frame: Frame | None = None
        self.anim: Animation | None = None
        self.anim_fps = 10.0
        self.anim_until = 0.0
        self.keepalive_s = 0.0
        self.last_send = 0.0
        self.last_enable = 0.0
        self.stats = self._new_stats()
        self._cv = threading.Condition()
        self._stop = False
        threading.Thread(target=self._loop, name=f"stream-{ip}", daemon=True).start()

    @staticmethod
    def _new_stats():
        return {"frames": 0, "started": 0.0, "max_gap_ms": 0.0, "late": 0, "target_fps": 0, "actual_fps": 0.0}

    # --------------------------------------------------------- realtime
    def enable(self, repeats: int = 3, spacing: float = 0.5):
        """Issue #404: a single enable right after power-on is sometimes
        ignored, so it is repeated a few times. A box that is switched off
        ignores realtime frames, so it is switched on first."""
        self.lan.send(self.ip, p.turn_msg(True))
        time.sleep(0.3)
        for i in range(repeats):
            self.lan.send_razer(self.ip, p.razer_enable_packet(True))
            if i < repeats - 1:
                time.sleep(spacing)
        self.realtime = True
        self.last_enable = time.time()

    def disable(self, repeats: int = 3):
        self.stop_anim()
        for _ in range(repeats):
            try:
                self.lan.send_razer(self.ip, p.razer_enable_packet(False))
            except OSError:
                pass
            time.sleep(0.05)
        self.realtime = False

    # ----------------------------------------------------------- frames
    def send_frame(self, colors: Sequence[p.RGB], gradient: bool | None = None):
        g = self.gradient if gradient is None else gradient
        self.lan.send_razer(self.ip, p.razer_frame_packet(colors, g))
        self.last_send = time.time()

    def show(self, colors: Sequence[p.RGB], gradient: bool = False):
        """Display a static frame (stops any animation)."""
        with self._cv:
            self.anim = None
            self.frame, self.gradient = list(colors), gradient
            self._cv.notify()
        self.send_frame(self.frame, gradient)

    def send_raw(self, packet: bytes):
        self.lan.send_razer(self.ip, packet)
        self.last_send = time.time()

    def play(self, anim: Animation, fps: float, seconds: float = 0, gradient: bool = False):
        with self._cv:
            self.gradient = gradient
            self.anim, self.anim_fps = anim, max(0.5, min(100.0, fps))
            self.anim_until = time.time() + seconds if seconds else 0
            self.stats = self._new_stats()
            self.stats["target_fps"] = self.anim_fps
            self._cv.notify()

    def stop_anim(self):
        with self._cv:
            self.anim = None
            self._cv.notify()

    def set_keepalive(self, seconds: float):
        with self._cv:
            self.keepalive_s = max(0.0, seconds)
            self._cv.notify()

    def close(self):
        with self._cv:
            self._stop = True
            self._cv.notify()

    # ------------------------------------------------------------- loop
    def _loop(self):
        n = 0
        next_t = 0.0
        prev = 0.0
        while True:
            with self._cv:
                if self._stop:
                    return
                anim = self.anim
                if anim is None:
                    n, next_t, prev = 0, 0.0, 0.0
                    wait = None
                    if self.keepalive_s and self.realtime and self.frame:
                        due = self.last_send + self.keepalive_s
                        wait = max(0.0, due - time.time())
                        if wait == 0.0:
                            frame = self.frame
                        else:
                            self._cv.wait(wait)
                            continue
                    else:
                        self._cv.wait(wait)
                        continue
            if anim is None:
                try:
                    self.send_frame(frame)
                except OSError as e:
                    log.warning("keepalive send failed: %s", e)
                continue

            now = time.perf_counter()
            if not next_t or not self.stats["started"]:   # new play(): restart timing
                next_t, prev = now, 0.0
                self.stats["started"] = time.time()
            if now < next_t:
                time.sleep(next_t - now)
                continue
            if self.anim_until and time.time() > self.anim_until:
                self.stop_anim()
                continue
            try:
                colors = anim(n)
                self.frame = colors
                self.send_frame(colors)
            except Exception as e:  # noqa: BLE001
                log.warning("animation frame failed: %s", e)
                self.stop_anim()
                continue
            t = time.perf_counter()
            st = self.stats
            if prev:
                st["max_gap_ms"] = max(st["max_gap_ms"], (t - prev) * 1000)
            prev = t
            st["frames"] += 1
            el = time.time() - st["started"]
            st["actual_fps"] = round(st["frames"] / el, 2) if el > 0.5 else 0.0
            n += 1
            period = 1.0 / self.anim_fps
            next_t += period
            if t - next_t > period:          # fell behind: resync, count it
                st["late"] += 1
                next_t = t
