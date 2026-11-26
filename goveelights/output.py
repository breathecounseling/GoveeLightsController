"""Output layer. Effects never see this: the engine hands each output a frame
per run (physical bulb order, ints 0..255), and the output delivers it.

To add WLED later, implement Output for DDP/E1.31 and select it in config.
"""
from __future__ import annotations

import logging
import threading
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
        self._retry_at: dict[str, float] = {}    # mac -> when to retry a failed enable
        self._retrying: set[str] = set()
        self.retry_s = 15.0
        # Bumped by stop(): an enable already running (retry thread) from an earlier
        # session must not put a released box back in realtime.
        self.gen = 0
        self.release_retry_s = 3.0               # 0 = no second disable pass
        self._bad: set[str] = set()              # boxes whose frames the protocol rejects

    def _streamer(self, mac: str) -> Streamer:
        s = self.streamers.get(mac)
        if s is None:
            s = self.streamers[mac] = Streamer(self.lan, self.devices[mac]["ip"])
        s.ip = self.devices[mac]["ip"]
        return s

    def start(self, force: bool = False):
        """Put every box in realtime mode. force=True re-sends the enable even if we
        think a box is already in realtime: the Govee app may have taken it back."""
        self.gen += 1                            # cancels a pending second release pass
        for mac in list(self.devices):
            try:
                s = self._streamer(mac)
                s.stop_anim()
                s.set_keepalive(0)
                if force or not s.realtime:
                    s.enable()
                self._retry_at.pop(mac, None)
            except OSError as e:
                # One unreachable box (Wi-Fi drop, stale IP) must not keep the others dark,
                # or make going live fail; send_frame retries its enable in the background.
                log.warning("enable %s failed: %s (will retry)", self.devices.get(mac, {}).get("name", mac), e)
                self._retry_at[mac] = time.monotonic() + self.retry_s
        self._last.clear()                       # resend every frame

    def active(self, gen: int) -> bool:
        return self.gen == gen

    def _retry_enable(self, mac: str, gen: int | None = None):
        gen = self.gen if gen is None else gen
        try:
            if not self._streamer(mac).enable(wanted=lambda: self.gen == gen):
                return                           # lights released meanwhile
            self._retry_at.pop(mac, None)
            self._last.pop(mac, None)            # resend the current frame
            log.info("enable %s succeeded on retry", mac)
        except OSError as e:
            log.debug("enable %s retry failed: %s", mac, e)
            if self.gen == gen:
                self._retry_at[mac] = time.monotonic() + self.retry_s
        finally:
            self._retrying.discard(mac)

    def send_frame(self, run_id: str, pixels: Sequence[RGB]):
        key = tuple(pixels)
        now = time.monotonic()
        due = self._retry_at.get(run_id)
        if due is not None and now >= due and run_id not in self._retrying:
            self._retrying.add(run_id)           # enable sleeps ~1.3 s: keep it off the render thread
            threading.Thread(target=self._retry_enable, args=(run_id, self.gen), daemon=True).start()
        if self._last.get(run_id) == key and now - self._last_t.get(run_id, 0) < self.refresh_s:
            return
        self._last[run_id], self._last_t[run_id] = key, now
        s = self._streamer(run_id)
        try:
            # RGB only: tested on H805A, 4-byte RGBW frames are read as RGB triplets
            s.send_frame(pixels, gradient=False)
            self._bad.discard(run_id)
        except OSError as e:
            log.warning("send to %s failed: %s", run_id, e)
        except ValueError as e:
            # e.g. a pixel_count the razer frame cannot carry: keep the other boxes going
            if run_id not in self._bad:
                self._bad.add(run_id)
                log.error("frame for %s rejected: %s", self.devices.get(run_id, {}).get("name", run_id), e)

    def stop(self):
        self.gen += 1
        self._retry_at.clear()
        self._release()
        self._last.clear()
        if self.release_retry_s > 0:
            # Release is fire-and-forget UDP: send it again shortly in case those
            # packets were lost, unless the lights were taken again meanwhile.
            gen = self.gen
            t = threading.Timer(self.release_retry_s, lambda: self.gen == gen and self._release(again=True))
            t.daemon = True
            t.start()

    def _release(self, again: bool = False):
        for mac in list(self.devices):
            try:
                s = self._streamer(mac)
                if again and (s.realtime or getattr(s, "anim", None) is not None):
                    continue                     # someone (diagnostics) has taken it since
                s.disable()
            except OSError as e:
                log.warning("disable %s failed: %s", mac, e)


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
        for o in self._all():                  # one failing output must not stop the others
            try:
                try:
                    o.start(force=force)
                except TypeError:
                    o.start()
            except OSError as e:
                log.warning("output start failed: %s", e)

    def send_frame(self, run_id, pixels):
        (self.others.get(run_id) or self.govee).send_frame(run_id, pixels)

    def stop(self):
        for o in self._all():
            try:
                o.stop()
            except OSError as e:
                log.warning("output stop failed: %s", e)

    def _streamer(self, mac):
        return self.govee._streamer(mac)

    @property
    def _last(self):
        return self.govee._last
