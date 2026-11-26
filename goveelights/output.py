"""Output layer. Effects never see this: the engine hands each output a frame
per run (physical bulb order, ints 0..255), and the output delivers it.

To add WLED later, implement Output for DDP/E1.31 and select it in config.
"""
from __future__ import annotations

import logging
import time
from typing import Protocol, Sequence

from .lan import GoveeLan
from .streamer import Streamer

log = logging.getLogger(__name__)
RGB = tuple[int, int, int]


class Output(Protocol):
    def start(self) -> None: ...
    def send_frame(self, run_id: str, pixels: Sequence[RGB]) -> None: ...
    def stop(self) -> None: ...


class GoveeRazerOutput:
    """Per-pixel frames over the undocumented `razer` LAN command.

    Identical frames are not resent every tick (saves Wi-Fi airtime), but are
    refreshed every `refresh_s` so a lost UDP packet or a dropped realtime
    session self-heals."""

    def __init__(self, lan: GoveeLan, devices: dict[str, dict], refresh_s: float = 1.0,
                 streamers: dict[str, Streamer] | None = None):
        self.lan = lan
        self.devices = devices       # mac -> config entry (ip, rgbw)
        self.refresh_s = refresh_s
        self.streamers = streamers if streamers is not None else {}
        self._last: dict[str, tuple] = {}
        self._last_t: dict[str, float] = {}

    def _streamer(self, mac: str) -> Streamer:
        s = self.streamers.get(mac)
        if s is None:
            s = self.streamers[mac] = Streamer(self.lan, self.devices[mac]["ip"])
        s.ip = self.devices[mac]["ip"]
        return s

    def start(self, force: bool = False):
        """Put every box in realtime mode. force=True re-sends the enable even if we
        think a box is already in realtime: the Govee app may have taken it back."""
        for mac in self.devices:
            s = self._streamer(mac)
            s.stop_anim()
            s.set_keepalive(0)
            if force or not s.realtime:
                s.enable()
        self._last.clear()                       # resend every frame

    def send_frame(self, run_id: str, pixels: Sequence[RGB]):
        key = tuple(pixels)
        now = time.monotonic()
        if self._last.get(run_id) == key and now - self._last_t.get(run_id, 0) < self.refresh_s:
            return
        self._last[run_id], self._last_t[run_id] = key, now
        s = self._streamer(run_id)
        try:
            # RGB only: tested on H805A, 4-byte RGBW frames are read as RGB triplets
            s.send_frame(pixels, gradient=False)
        except OSError as e:
            log.warning("send to %s failed: %s", run_id, e)

    def stop(self):
        for mac in self.devices:
            self._streamer(mac).disable()
        self._last.clear()


class MemoryOutput:
    """Keeps the latest frame per run (tests, preview)."""

    def __init__(self):
        self.frames: dict[str, list[RGB]] = {}
        self.count = 0
        self.started = False

    def start(self):
        self.started = True

    def send_frame(self, run_id, pixels):
        self.frames[run_id] = list(pixels)
        self.count += 1

    def stop(self):
        self.started = False
