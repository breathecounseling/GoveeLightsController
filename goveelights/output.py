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


class WledDdpOutput:
    """WLED controllers (ESP32 + addressable pixels) over DDP, WLED's realtime protocol
    (UDP port 4048). WLED shows our frames while they keep arriving and falls back to its
    own effects ~2.5 s after they stop, so unchanged frames are refreshed every refresh_s."""

    PORT = 4048
    MAX_BYTES = 1440                       # 480 RGB pixels per packet

    def __init__(self, devices: dict[str, dict], refresh_s: float = 1.0):
        import socket
        self.devices = devices             # id -> config entry (ip, optional port)
        self.refresh_s = min(refresh_s, 1.5)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._seq = 0
        self._last: dict[str, tuple] = {}
        self._last_t: dict[str, float] = {}

    @staticmethod
    def packets(pixels, seq: int) -> list[bytes]:
        data = bytes(v for px in pixels for v in px)
        out = []
        for off in range(0, max(1, len(data)), WledDdpOutput.MAX_BYTES):
            chunk = data[off:off + WledDdpOutput.MAX_BYTES]
            last = off + WledDdpOutput.MAX_BYTES >= len(data)
            hdr = bytes([0x40 | (0x01 if last else 0), seq & 0x0F, 0x0B, 0x01]) + \
                off.to_bytes(4, "big") + len(chunk).to_bytes(2, "big")
            out.append(hdr + chunk)
        return out

    def start(self, force: bool = False):
        self._last.clear()

    def send_frame(self, run_id: str, pixels: Sequence[RGB]):
        key = tuple(pixels)
        now = time.monotonic()
        if self._last.get(run_id) == key and now - self._last_t.get(run_id, 0) < self.refresh_s:
            return
        self._last[run_id], self._last_t[run_id] = key, now
        d = self.devices[run_id]
        self._seq = self._seq % 15 + 1
        try:
            for pkt in self.packets(pixels, self._seq):
                self.sock.sendto(pkt, (d["ip"], int(d.get("port", self.PORT))))
        except OSError as e:
            log.warning("WLED %s: send failed: %s", d.get("name", run_id), e)

    def stop(self):
        self._last.clear()                 # WLED returns to its own effects after its timeout


class MultiOutput:
    """Routes each run to the output that drives it (Govee boxes, WLED controllers…)."""

    def __init__(self, govee: GoveeRazerOutput, others: dict[str, object] | None = None):
        self.govee = govee
        self.others = others or {}         # run id -> output
        self.devices = govee.devices       # self-healing and IP checks concern the Govee boxes

    def _all(self):
        yield self.govee
        seen = set()
        for o in self.others.values():
            if id(o) not in seen:
                seen.add(id(o))
                yield o

    def start(self, force: bool = False):
        for o in self._all():
            try:
                o.start(force=force)
            except TypeError:
                o.start()

    def send_frame(self, run_id, pixels):
        (self.others.get(run_id) or self.govee).send_frame(run_id, pixels)

    def stop(self):
        for o in self._all():
            o.stop()

    def _streamer(self, mac):
        return self.govee._streamer(mac)

    @property
    def _last(self):
        return self.govee._last
