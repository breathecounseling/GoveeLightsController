"""UDP transport for the Govee LAN API: discovery, commands, status replies."""
from __future__ import annotations

import json
import logging
import socket
import struct
import threading
import time
from typing import Callable

from . import protocol as p

log = logging.getLogger(__name__)


class GoveeLan:
    """One socket bound to UDP 4002 receives scan and devStatus replies; the
    same socket sends commands to <ip>:4003."""

    def __init__(self, bind_ip: str = "", on_device: Callable[[dict], None] | None = None):
        self.bind_ip = bind_ip or ""
        self.on_device = on_device
        self._status: dict[str, dict] = {}
        self._status_ev: dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self.sock = self._open()
        self._closed = False
        threading.Thread(target=self._rx_loop, name="govee-rx", daemon=True).start()

    def _open(self) -> socket.socket:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("", p.LISTEN_PORT))
        except OSError as e:
            raise SystemExit(
                f"Cannot bind UDP port {p.LISTEN_PORT} ({e}). Another Govee LAN tool "
                "(Home Assistant, Homebridge, another copy of this app) may be using it.")
        iface = socket.inet_aton(self.bind_ip) if self.bind_ip else struct.pack("=I", socket.INADDR_ANY)
        try:
            s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, socket.inet_aton(p.SCAN_GROUP) + iface)
        except OSError as e:
            log.warning("multicast join failed (%s); 'add by IP' still works", e)
        if self.bind_ip:
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, iface)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        return s

    # ----------------------------------------------------------- receive
    def _rx_loop(self):
        while not self._closed:
            try:
                data, (ip, _port) = self.sock.recvfrom(65535)
            except OSError:
                if self._closed:
                    return
                time.sleep(0.1)
                continue
            try:
                msg = json.loads(data.decode("utf-8", "replace"))["msg"]
            except (ValueError, KeyError, TypeError):
                continue
            cmd, d = msg.get("cmd"), msg.get("data") or {}
            if cmd == "scan" and "device" in d:
                d.setdefault("ip", ip)
                if self.on_device:
                    try:
                        self.on_device(d)
                    except Exception:  # noqa: BLE001
                        log.exception("on_device failed")
            elif cmd == "devStatus":
                with self._lock:
                    self._status[ip] = {"t": time.time(), **d}
                    ev = self._status_ev.get(ip)
                if ev:
                    ev.set()

    # -------------------------------------------------------------- send
    def scan(self, extra_ips: list[str] | None = None):
        data = p.encode(p.scan_msg())
        try:
            self.sock.sendto(data, (p.SCAN_GROUP, p.SCAN_PORT))
        except OSError as e:
            log.warning("multicast scan send failed: %s", e)
        for ip in extra_ips or []:
            try:
                self.sock.sendto(data, (ip, p.SCAN_PORT))
            except OSError as e:
                log.warning("unicast scan to %s failed: %s", ip, e)

    def send(self, ip: str, msg: dict):
        self.sock.sendto(p.encode(msg), (ip, p.CONTROL_PORT))

    def send_razer(self, ip: str, packet: bytes):
        self.send(ip, p.razer_msg(packet))

    def status(self, ip: str, timeout: float = 1.5) -> dict | None:
        ev = threading.Event()
        with self._lock:
            self._status_ev[ip] = ev
        t0 = time.time()
        self.send(ip, p.status_msg())
        ok = ev.wait(timeout)
        with self._lock:
            self._status_ev.pop(ip, None)
            st = self._status.get(ip)
        if ok and st and st["t"] >= t0:
            return {**st, "latency_ms": round((st["t"] - t0) * 1000)}
        return None

    def close(self):
        self._closed = True
        try:
            self.sock.close()
        except OSError:
            pass
