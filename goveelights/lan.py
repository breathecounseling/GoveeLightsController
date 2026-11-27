"""UDP transport for the Govee LAN API: discovery, commands, status replies."""
from __future__ import annotations

import json
import logging
import os
import socket
import struct
import sys
import threading
import time
from typing import Callable

from . import protocol as p

log = logging.getLogger(__name__)


def local_ipv4s() -> list[str]:
    """This computer's IPv4 addresses, one per network interface, loopback left out.
    Linux only (a Raspberry Pi with both Ethernet and Wi-Fi up has two); elsewhere an
    empty list, and the operating system's choice of interface is used as before."""
    if not sys.platform.startswith("linux"):
        return []
    try:
        import fcntl
        names = [n for _, n in socket.if_nameindex()]
    except (ImportError, OSError, AttributeError):
        return []
    out = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        for n in names:
            try:
                req = struct.pack("256s", n.encode()[:15])
                ip = socket.inet_ntoa(fcntl.ioctl(s.fileno(), 0x8915, req)[20:24])   # SIOCGIFADDR
            except OSError:                        # down, or no IPv4 address
                continue
            if not ip.startswith("127.") and ip not in out:
                out.append(ip)
    return out


class GoveeLan:
    """One socket bound to UDP 4002 receives scan and devStatus replies; the
    same socket sends commands to <ip>:4003."""

    def __init__(self, bind_ip: str = "", on_device: Callable[[dict], None] | None = None):
        self.bind_ip = bind_ip or ""
        self.on_device = on_device
        self._status: dict[str, dict] = {}
        self._status_ev: dict[str, set[threading.Event]] = {}   # ip -> waiting status() calls
        self._lock = threading.Lock()
        self.sock = self._open()
        self._closed = False
        threading.Thread(target=self._rx_loop, name="govee-rx", daemon=True).start()

    def _open(self) -> socket.socket:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        if os.name == "nt":
            # Windows SO_REUSEADDR would let a second copy bind 4002 too; claim it alone.
            # And don't let an ICMP port-unreachable (a box rebooting, a stale IP) make
            # the next recvfrom fail with WinError 10054.
            s.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_EXCLUSIVEADDRUSE", -5), 1)
            try:
                s.ioctl(getattr(socket, "SIO_UDP_CONNRESET", 0x9800000C), False)
            except (OSError, AttributeError, ValueError) as e:
                log.debug("SIO_UDP_CONNRESET not set: %s", e)
        else:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("", p.LISTEN_PORT))
        except OSError as e:
            raise SystemExit(
                f"Cannot bind UDP port {p.LISTEN_PORT} ({e}). Another Govee LAN tool "
                "(Home Assistant, Homebridge, another copy of this app) may be using it.")
        if self.bind_ip:
            # A govee_config.json copied from another computer (the Windows PC) can carry
            # that computer's address: ignore it rather than fail to start.
            mine = local_ipv4s()
            if mine and self.bind_ip not in mine:
                log.warning("bind_ip %s is not an address of this computer (%s); ignoring it. "
                            "Set server.bind_ip to \"\" in govee_config.json.", self.bind_ip, ", ".join(mine))
                self.bind_ip = ""
        any_if = struct.pack("=I", socket.INADDR_ANY)
        iface = socket.inet_aton(self.bind_ip) if self.bind_ip else any_if
        try:
            s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, socket.inet_aton(p.SCAN_GROUP) + iface)
        except OSError as e:
            log.warning("multicast join failed (%s); 'add by IP' still works", e)
        self._mcast_ifaces: list[str] = []
        if self.bind_ip:
            try:
                s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, iface)
            except OSError as e:
                log.warning("can't use bind_ip %s (%s); letting the system choose the network. "
                            "Set server.bind_ip to \"\" in govee_config.json.", self.bind_ip, e)
                self.bind_ip = ""
                try:
                    s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, socket.inet_aton(p.SCAN_GROUP) + any_if)
                except OSError:                    # already joined, or no multicast: 'add by IP' still works
                    pass
        if not self.bind_ip and os.name != "nt":
            # Linux / Raspberry Pi with Ethernet and Wi-Fi both up: "any" joins (and scans
            # on) only the interface of the default route, so a box on the other one would
            # never be found. Join on every interface and scan on each (see scan()).
            ifs = local_ipv4s()
            for ip in ifs:
                try:
                    s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                                 socket.inet_aton(p.SCAN_GROUP) + socket.inet_aton(ip))
                except OSError:                    # already joined there through "any"
                    pass
            if len(ifs) > 1:
                self._mcast_ifaces = ifs
                log.info("network interfaces %s: scanning for lights on each", ", ".join(ifs))
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        return s

    # ----------------------------------------------------------- receive
    def _rx_loop(self):
        while not self._closed:
            try:
                data, (ip, _port) = self.sock.recvfrom(65535)
            except ConnectionResetError:          # Windows: ICMP unreachable from an earlier send
                continue
            except OSError:
                if self._closed:
                    return
                time.sleep(0.1)
                continue
            try:
                self._handle(data, ip)
            except Exception:  # noqa: BLE001 - anyone on the LAN can send to 4002; never die
                log.exception("bad datagram from %s", ip)

    def _handle(self, data: bytes, ip: str):
        try:
            msg = json.loads(data.decode("utf-8", "replace"))["msg"]
        except (ValueError, KeyError, TypeError):
            return
        if not isinstance(msg, dict):
            return
        cmd, d = msg.get("cmd"), msg.get("data") or {}
        if not isinstance(d, dict):
            return
        if cmd == "scan" and "device" in d:
            d["source_ip"] = ip
            if self.on_device:
                try:
                    self.on_device(d)
                except Exception:  # noqa: BLE001
                    log.exception("on_device failed")
        elif cmd == "devStatus":
            with self._lock:
                self._status[ip] = {"t": time.time(), **d}
                evs = list(self._status_ev.get(ip, ()))
            for ev in evs:
                ev.set()

    # -------------------------------------------------------------- send
    def scan(self, extra_ips: list[str] | None = None):
        data = p.encode(p.scan_msg())
        try:
            self.sock.sendto(data, (p.SCAN_GROUP, p.SCAN_PORT))
        except OSError as e:
            log.warning("multicast scan send failed: %s", e)
        ifs = getattr(self, "_mcast_ifaces", None) or []
        for ip in ifs:                             # several interfaces (Linux): one scan out of each
            try:
                self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(ip))
                self.sock.sendto(data, (p.SCAN_GROUP, p.SCAN_PORT))
            except OSError as e:
                log.debug("multicast scan on %s failed: %s", ip, e)
        if ifs:
            try:
                self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton("0.0.0.0"))
            except OSError:
                pass
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
            self._status_ev.setdefault(ip, set()).add(ev)
        t0 = time.time()
        self.send(ip, p.status_msg())
        ok = ev.wait(timeout)
        with self._lock:
            waiters = self._status_ev.get(ip)
            if waiters is not None:
                waiters.discard(ev)
                if not waiters:
                    del self._status_ev[ip]
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
