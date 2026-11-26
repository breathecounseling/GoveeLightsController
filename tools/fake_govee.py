"""Simulated Govee LAN devices for testing the tool without hardware.

    python tools/fake_govee.py --count 72 --devices 2

Each fake device binds 127.0.0.<2+i> (or <base+i> with --base) on ports 4001 (scan) and 4003 (control),
razer frames with n > bulb count are ignored (as observed on H805A), or with
--oversize dark the string goes dark (H706B, govee-local-api issue #404).
Add the devices in the web UI with "Add by IP" (127.0.0.2, ...)."""
from __future__ import annotations

import argparse
import base64
import json
import socket
import threading
import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from goveelights import protocol as p  # noqa: E402


class FakeDevice:
    def __init__(self, ip, count, timeout, sku, idx, quiet, oversize="ignore"):
        self.oversize = oversize
        self.ip, self.count, self.timeout, self.sku, self.quiet = ip, count, timeout, sku, quiet
        self.mac = f"AA:BB:CC:DD:EE:FF:00:{idx:02X}"
        self.on, self.bright, self.color = 1, 100, (255, 160, 60)
        self.realtime, self.last_frame = False, 0.0
        self.pixels = [self.color] * count
        self.frames = 0
        self.log: list[str] = []
        for port, fn in ((p.SCAN_PORT, self._scan), (p.CONTROL_PORT, self._ctl)):
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((ip, port))
            threading.Thread(target=self._serve, args=(s, fn), daemon=True).start()
        threading.Thread(target=self._watchdog, daemon=True).start()

    def _serve(self, s, fn):
        while True:
            data, addr = s.recvfrom(65535)
            try:
                fn(s, json.loads(data)["msg"], addr)
            except Exception as e:  # noqa: BLE001
                self._p(f"error {e!r}")

    def _p(self, m):
        self.log.append(m)
        if not self.quiet:
            print(f"[{self.ip}] {m}", flush=True)

    def _reply(self, s, addr, msg):
        s.sendto(json.dumps({"msg": msg}).encode(), (addr[0], p.LISTEN_PORT))

    def _scan(self, s, msg, addr):
        if msg.get("cmd") == "scan":
            self._reply(s, addr, {"cmd": "scan", "data": {"ip": self.ip, "device": self.mac, "sku": self.sku,
                        "bleVersionHard": "3.01.01", "bleVersionSoft": "1.00.10",
                        "wifiVersionHard": "1.00.10", "wifiVersionSoft": "1.02.03"}})

    def _ctl(self, s, msg, addr):
        cmd, d = msg.get("cmd"), msg.get("data", {})
        if cmd == "devStatus":
            r, g, b = self.color
            self._reply(s, addr, {"cmd": "devStatus", "data": {"onOff": self.on, "brightness": self.bright,
                        "color": {"r": r, "g": g, "b": b}, "colorTemInKelvin": 0}})
        elif cmd == "turn":
            self.on = d["value"]; self._p(f"turn {self.on}")
        elif cmd == "brightness":
            self.bright = d["value"]; self._p(f"brightness {self.bright}")
        elif cmd == "colorwc":
            c = d["color"]; self.color = (c["r"], c["g"], c["b"]); self.realtime = False
            self.pixels = [self.color] * self.count; self._p(f"colorwc {self.color} K={d.get('colorTemInKelvin')} (realtime off)")
        elif cmd == "razer":
            self._razer(base64.b64decode(d["pt"]))
        elif cmd == "ptReal":
            self._p("ptReal " + " ".join(base64.b64decode(x).hex() for x in d["command"]))

    def _razer(self, pkt):
        try:
            r = p.decode_razer(pkt)
        except ValueError as e:
            self._p(f"razer rejected ({e}): {pkt.hex()}"); return
        if r["cmd"] == p.RAZER_CMD_MODE:
            self.realtime = r["on"]; self.last_frame = time.time(); self._p(f"realtime {'ON' if r['on'] else 'OFF'}")
            if not r["on"]:
                self.pixels = [self.color] * self.count
            return
        if not self.realtime:
            self._p("frame ignored (realtime off)"); return
        cols, n = r["colors"], len(r["colors"])
        self.last_frame = time.time(); self.frames += 1
        if n > self.count:
            if self.oversize == "dark":
                self.pixels = [(0, 0, 0)] * self.count
                self._p(f"frame n={n} > {self.count}: REJECTED, dark")
            else:
                self._p(f"frame n={n} > {self.count}: IGNORED, previous frame stays")
            return
        if r["gradient"] and n > 1:
            out = []
            for i in range(self.count):
                x = i * (n - 1) / max(1, self.count - 1); a = int(x); f = x - a; b = min(a + 1, n - 1)
                out.append(tuple(int(cols[a][k] * (1 - f) + cols[b][k] * f) for k in range(3)))
            self.pixels = out
        else:
            self.pixels = [cols[i * n // self.count] for i in range(self.count)]
        lit = [i for i, c in enumerate(self.pixels) if c != (0, 0, 0)]
        desc = f"lit={lit}" if len(lit) <= 6 else f"{len(lit)} lit"
        if not self.quiet or self.frames % 50 == 0:
            self._p(f"frame n={n} grad={int(r['gradient'])} {desc}")

    def _watchdog(self):
        while True:
            time.sleep(0.2)
            if self.timeout and self.realtime and self.last_frame and time.time() - self.last_frame > self.timeout:
                self.realtime = False; self.pixels = [self.color] * self.count
                self._p(f"realtime TIMED OUT after {self.timeout}s without frames")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--devices", type=int, default=2)
    ap.add_argument("--count", type=int, default=72)
    ap.add_argument("--counts", help="comma-separated bulb count per device, e.g. 96,108")
    ap.add_argument("--timeout", type=float, default=0, help="simulate realtime timeout (s), 0 = none")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--base", type=int, default=2, help="first device binds 127.0.0.<base> (default 2)")
    ap.add_argument("--oversize", choices=["ignore", "dark"], default="ignore",
                    help="frames with n > bulbs: ignore (seen on H805A) or go dark (H706B)")
    a = ap.parse_args()
    counts = [int(x) for x in a.counts.split(",")] if a.counts else [a.count + i for i in range(a.devices)]
    devs = [FakeDevice(f"127.0.0.{a.base + i}", counts[i], a.timeout, ("H705E", "H805A")[i % 2], i, a.quiet, a.oversize)
            for i in range(len(counts) if a.counts else a.devices)]
    print("fake devices:", ", ".join(f"{d.ip} ({d.sku}, {d.count} bulbs)" for d in devs), flush=True)
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
