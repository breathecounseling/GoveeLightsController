"""Phase 1 diagnostic + mapping server. Serves a phone-friendly page and a
small JSON API that drives the lights directly."""
from __future__ import annotations

import json
import logging
import os
import signal
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import colororder
from . import protocol as p
from .auth import Auth
from . import winconsole
from .config import Config
from .lan import GoveeLan
from .streamer import Streamer

log = logging.getLogger("goveelights")


class Server(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR lets a second copy bind the same port; refuse instead.
    allow_reuse_address = os.name != "nt"


WEB = Path(__file__).resolve().parent / "web"
BLACK = (0, 0, 0)


class App:
    def __init__(self, cfg: Config, bind_ip: str = ""):
        self.cfg = cfg
        self.lan = GoveeLan(bind_ip or cfg.data["server"].get("bind_ip", ""), on_device=self._on_device)
        self.streamers: dict[str, Streamer] = {}
        self.lock = threading.RLock()
        self.last_scan = 0.0
        self.seen: dict[str, float] = {}

    # ------------------------------------------------------- devices
    def _on_device(self, info: dict):
        new = self.cfg.upsert_discovered(info)
        mac = info["device"]
        self.seen[mac] = time.time()
        # Use the address the config settled on (the reply's source), not the "ip" the
        # device reports, which can be stale after a DHCP change.
        ip = (self.cfg.devices.get(mac) or {}).get("ip") or info.get("source_ip") or info.get("ip")
        with self.lock:
            s = self.streamers.get(mac)
            if s and ip and s.ip != ip:
                s.ip = ip
        if new:
            log.info("discovered %s %s at %s", info.get("sku"), mac, ip)

    def scan(self, extra=None):
        ips = list(self.cfg.data.get("manual_ips", [])) + list(extra or [])
        ips += [d["ip"] for d in self.cfg.devices.values() if d.get("ip")]
        self.last_scan = time.time()
        self.lan.scan(sorted(set(ips)))

    def streamer(self, mac: str) -> Streamer:
        dev = self.cfg.device(mac)
        if not dev.get("ip"):
            raise ValueError("device has no IP yet; scan first")
        with self.lock:
            s = self.streamers.get(mac)
            if s is None:
                s = self.streamers[mac] = Streamer(self.lan, dev["ip"])
            return s

    def rt(self, mac: str) -> Streamer:
        """Streamer with realtime on. The official colour/temperature command
        drops the box out of realtime, so test buttons re-enable as needed."""
        s = self.streamer(mac)
        if not s.realtime:
            s.enable()
        return s

    def fix(self, mac: str, rgb, raw: bool = False):
        """A test colour as this box must receive it (its colour order, colororder.py), so
        the tests here show the colour you picked; raw=True sends the bytes as they are."""
        if raw:
            return tuple(rgb)
        return colororder.apply_px(colororder.of(self.cfg.devices.get(mac)), rgb)

    def n_for(self, mac: str, n=None) -> int:
        if n:
            return max(1, min(p.MAX_RAZER_COLORS, int(n)))
        d = self.cfg.device(mac)
        return int(d.get("pixel_count") or d.get("estimated_count") or 72)

    def note(self, mac: str, key: str, value):
        with self.cfg.lock:
            diag = self.cfg.device(mac).setdefault("diag", {})
            diag[key] = {"value": value, "at": time.strftime("%Y-%m-%d %H:%M:%S")}
            self.cfg.save()

    def release_all(self):
        with self.lock:
            for s in self.streamers.values():
                if s.realtime:
                    s.disable()

    # ------------------------------------------------------- actions
    def identify(self, mac: str):
        dev = self.cfg.device(mac)
        ip = dev["ip"]
        s = self.streamers.get(mac)

        def run():
            # Always use the official on/off command, which every LAN-capable box
            # supports. If realtime was on, drop it first and restore it after.
            was_rt = bool(s and s.realtime)
            if was_rt:
                s.disable(repeats=2)
            st = self.lan.status(ip)
            was_on = (st or {}).get("onOff", 1)
            for _ in range(3):                    # slow enough for boxes that fade in/out
                self.lan.send(ip, p.turn_msg(False)); time.sleep(1.0)
                self.lan.send(ip, p.turn_msg(True)); time.sleep(1.2)
            if not was_on:
                self.lan.send(ip, p.turn_msg(False))
            if was_rt:
                s.enable()
                if s.frame:
                    s.send_frame(s.frame)
        threading.Thread(target=run, daemon=True).start()

    def state(self) -> dict:
        devs = []
        now = time.time()
        for mac, d in sorted(self.cfg.devices.items(), key=lambda kv: kv[1].get("order", 0)):
            s = self.streamers.get(mac)
            devs.append({
                "mac": mac, **{k: d.get(k) for k in ("name", "ip", "reported_ip", "sku", "firmware", "estimated_count",
                                                      "pixel_count", "reversed", "order", "landmarks", "diag")},
                "n": self.n_for(mac),
                "seen_ago": round(now - self.seen[mac], 1) if mac in self.seen else None,
                "realtime": bool(s and s.realtime),
                "animating": bool(s and s.anim),
                "keepalive_s": s.keepalive_s if s else 0,
                "since_last_frame": round(now - s.last_send, 1) if s and s.last_send else None,
                "since_enable": round(now - s.last_enable, 1) if s and s.last_enable else None,
                "stats": dict(s.stats) if s else None,
            })
        return {"devices": devs, "manual_ips": self.cfg.data.get("manual_ips", []), "now": now,
                "config_path": str(self.cfg.path)}

    def handle(self, path: str, b: dict):
        mac = b.get("mac")
        if path == "scan":
            self.scan(b.get("ips"))
            return {"ok": True}
        if path == "add_ip":
            ip = b["ip"].strip()
            socket.inet_aton(ip)
            with self.cfg.lock:
                lst = self.cfg.data.setdefault("manual_ips", [])
                if ip not in lst:
                    lst.append(ip)
                self.cfg.save()
            self.scan([ip])
            return {"ok": True}
        if path == "device":                      # rename / edit fields
            upd = {k: b[k] for k in ("name", "estimated_count", "pixel_count", "reversed", "order") if k in b}
            for k in ("estimated_count", "pixel_count"):
                v = upd.get(k)
                if v not in (None, ""):
                    v = int(v)                    # ValueError -> 400
                    if not 1 <= v <= 255:         # one razer frame carries 1..255 bulbs
                        raise ValueError(f"{k} must be 1..255, got {v}")
                    upd[k] = v
            with self.cfg.lock:
                self.cfg.device(mac).update(upd)
                self.cfg.save()
            return {"ok": True}
        if path == "identify":
            self.identify(mac)
            return {"ok": True}
        if path == "status":
            st = self.lan.status(self.cfg.device(mac)["ip"])
            return {"ok": st is not None, "status": st}
        if path == "check":                       # is the box listening on the control port?
            ip = self.cfg.device(mac)["ip"]
            replies, last = 0, None
            for _ in range(3):
                st = self.lan.status(ip, timeout=1.2)
                if st:
                    replies, last = replies + 1, st
            seen = self.seen.get(mac)
            verdict = ("answering commands" if replies else
                       "answers the scan but ignores commands" if seen else "not answering at all")
            self.note(mac, "lan_check", {"status_replies": f"{replies}/3", "ip": ip, "verdict": verdict,
                                         "wifi_fw": self.cfg.device(mac).get("firmware", {}).get("wifiVersionSoft")})
            return {"ok": True, "replies": replies, "verdict": verdict, "status": last}
        if path == "realtime":
            s = self.streamer(mac)
            if b.get("on"):
                s.enable(repeats=int(b.get("repeats", 3)))
            else:
                s.disable()
            return {"ok": True}
        if path == "solid":                       # n copies of one colour
            s = self.rt(mac)
            rgb = self.fix(mac, p.hex_rgb(b.get("color", "#00ff00")), b.get("raw"))
            n = self.n_for(mac, b.get("n")) if b.get("n") else 1
            s.show([rgb] * n, gradient=False)
            return {"ok": True, "n": n}
        if path == "pixel":                       # walker: one lit bulb
            s = self.rt(mac)
            n = self.n_for(mac, b.get("n"))
            idx = int(b.get("index", 0))
            frame = [BLACK] * n
            on = self.fix(mac, p.hex_rgb(b.get("color", "#00ff00")), b.get("raw"))
            for k in range(int(b.get("width", 1))):
                if 0 <= idx + k < n:
                    frame[idx + k] = on
            s.show(frame)
            return {"ok": True, "n": n, "index": idx, "in_range": 0 <= idx < n}
        if path == "gradient_test":
            s = self.rt(mac)
            cols = [self.fix(mac, p.hex_rgb(c), b.get("raw")) for c in b.get("colors", ["#ff0000", "#0000ff"])]
            s.show(cols, gradient=bool(b.get("gradient", True)))
            return {"ok": True}
        if path == "landmark":
            with self.cfg.lock:
                d = self.cfg.device(mac)
                if b.get("delete") is not None:
                    d["landmarks"].pop(int(b["delete"]))
                else:
                    lm = {"index": int(b["index"]), "label": str(b.get("label", "")).strip()}
                    d["landmarks"] = [x for x in d["landmarks"] if x != lm] + [lm]
                    d["landmarks"].sort(key=lambda x: x["index"])
                self.cfg.save()
            return {"ok": True}
        if path == "chase":
            s = self.rt(mac)
            n = self.n_for(mac, b.get("n"))
            col = self.fix(mac, p.hex_rgb(b.get("color", "#ffffff")), b.get("raw"))

            def anim(i, n=n, col=col):
                f = [BLACK] * n
                f[i % n] = col
                return f
            s.play(anim, float(b.get("fps", 10)), float(b.get("seconds", 0)))
            return {"ok": True}
        if path == "stop":
            self.streamer(mac).stop_anim()
            return {"ok": True}
        if path == "keepalive":
            self.streamer(mac).set_keepalive(float(b.get("seconds", 0)))
            return {"ok": True}
        if path == "timeout_test":                # one frame then silence
            s = self.streamer(mac)
            s.stop_anim()
            s.set_keepalive(0)
            if not s.realtime:
                s.enable()
            s.show([self.fix(mac, p.hex_rgb(b.get("color", "#ff00ff")), b.get("raw"))] * self.n_for(mac))
            return {"ok": True}
        if path == "colorwc":
            ip = self.cfg.device(mac)["ip"]
            self.lan.send(ip, p.colorwc_msg(self.fix(mac, p.hex_rgb(b.get("color", "#000000")), b.get("raw")),
                                            int(b.get("kelvin", 0))))
            s = self.streamers.get(mac)
            if s:                                  # observed on H805A: this exits realtime
                s.stop_anim()
                s.realtime = False
            return {"ok": True}
        if path == "brightness":
            self.lan.send(self.cfg.device(mac)["ip"], p.brightness_msg(int(b["value"])))
            return {"ok": True}
        if path == "turn":
            self.lan.send(self.cfg.device(mac)["ip"], p.turn_msg(bool(b.get("on"))))
            return {"ok": True}
        if path == "rgbw_probe":
            s = self.rt(mac)
            n = self.n_for(mac)
            s.send_raw(p.razer_rgbw_probe_packet([tuple(b.get("rgbw", [0, 0, 0, 255]))] * n))
            return {"ok": True}
        if path == "finger_sketch":               # ptReal fallback (experimental)
            dev = self.cfg.device(mac)
            idx = int(b.get("index", 0))
            col = self.fix(mac, p.hex_rgb(b.get("color", "#00ff00")), b.get("raw"))
            bg = self.fix(mac, p.hex_rgb(b.get("background", "#000000")), b.get("raw"))
            pk = p.finger_sketch_packets([(col, [idx])], background=bg) + [p.FINGER_SKETCH_ACTIVATE]
            self.lan.send(dev["ip"], p.ptreal_msg(pk))
            return {"ok": True, "packets": [x.hex() for x in pk]}
        if path == "note":
            self.note(mac, str(b["key"]), b.get("value"))
            return {"ok": True}
        if path == "release":
            if mac:
                self.streamer(mac).disable()
            else:
                self.release_all()
            return {"ok": True}
        raise KeyError(f"unknown action {path}")


def make_handler(app: App):
    hosts = list(app.cfg.data["server"].get("hosts", []))

    class H(BaseHTTPRequestHandler):
        timeout = 30

        def log_message(self, fmt, *a):
            log.debug("%s " + fmt, self.address_string(), *a)

        def _send(self, code, body: bytes, ctype="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _home_only(self) -> bool:
            """This tool has no login: it answers only direct requests from the home network
            (never through the tunnel, never to a web page posing under its own domain)."""
            if Auth.direct_lan(self, hosts):
                return True
            self.close_connection = True
            self._send(403, b'{"ok":false,"error":"the diagnostic tool only works from the home network"}')
            return False

        def do_GET(self):
            if not self._home_only():
                return
            if self.path in ("/", "/index.html"):
                return self._send(200, (WEB / "diag.html").read_bytes(), "text/html; charset=utf-8")
            if self.path == "/api/state":
                return self._send(200, json.dumps(app.state()).encode())
            if self.path == "/api/report":
                return self._send(200, json.dumps(build_report(app), indent=2).encode())
            self._send(404, b'{"error":"not found"}')

        def do_POST(self):
            if not self._home_only():
                return
            try:
                ln = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                ln = -1
            if self.headers.get("Transfer-Encoding") is not None or not 0 <= ln <= 1_000_000:
                self.close_connection = True
                return self._send(413, b'{"ok":false,"error":"bad request size"}')
            raw = self.rfile.read(ln) if ln else b""
            if self.headers.get("X-Requested-With") != "lights":     # CSRF guard, as in the app
                return self._send(403, b'{"ok":false,"error":"missing request header"}')
            if not self.path.startswith("/api/"):
                return self._send(404, b'{"error":"not found"}')
            try:
                body = json.loads(raw or b"{}")
                res = app.handle(self.path[5:], body)
                self._send(200, json.dumps(res).encode())
            except (KeyError, ValueError, OSError, TypeError) as e:
                self._send(400, json.dumps({"ok": False, "error": str(e)}).encode())
    return H


def build_report(app: App) -> dict:
    out = []
    for d in app.state()["devices"]:
        out.append({k: d[k] for k in ("name", "mac", "ip", "reported_ip", "sku", "firmware", "pixel_count",
                                      "estimated_count", "landmarks", "diag")})
    return {"generated": time.strftime("%Y-%m-%d %H:%M:%S"), "devices": out}


def lan_ips() -> list[str]:
    """All IPv4 addresses of this PC (best guess first). A PC with several
    adapters (lights network, main network, VPN, Hyper-V) has several."""
    ips = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ips.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.append(info[4][0])
    except OSError:
        pass
    out = [ip for ip in dict.fromkeys(ips) if not ip.startswith("127.")]
    return out or ["127.0.0.1"]


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Govee lights Phase 1 diagnostic / mapping tool")
    ap.add_argument("--config", help="config file (default govee_config.json next to the code)")
    ap.add_argument("--port", type=int, help="web port (default 8080)")
    ap.add_argument("--bind-ip", help="this PC's LAN IP to use for multicast (if discovery finds nothing)")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    winconsole.disable_quickedit()
    cfg = Config(a.config) if a.config else Config()
    app = App(cfg, a.bind_ip or "")
    host = cfg.data["server"].get("host", "0.0.0.0")
    port = a.port or cfg.data["server"].get("port", 8080)
    srv = Server((host, port), make_handler(app))

    done = threading.Event()

    def shutdown(*_):
        if done.is_set():
            return
        done.set()
        log.info("releasing lights to the Govee app...")
        app.release_all()
        threading.Thread(target=srv.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    if hasattr(signal, "SIGBREAK"):          # Windows console close / Ctrl+Break
        signal.signal(signal.SIGBREAK, shutdown)
    winconsole.on_console_close(shutdown)    # closing the window ends us before SIGBREAK runs

    app.scan()
    threading.Thread(target=lambda: (time.sleep(1.5), app.scan()), daemon=True).start()
    urls = "\n".join(f"      http://{ip}:{port}/" for ip in lan_ips())
    print(f"\n  Govee diagnostic tool running.\n"
          f"  On this PC open:  http://localhost:{port}/\n"
          f"  On your phone (same network as this PC) try the address on that network:\n{urls}\n"
          f"  If the phone just spins: allow TCP port {port} in Windows Firewall (see README).\n"
          f"  Config file: {cfg.path}\n  Press Ctrl+C to stop (lights are released to the Govee app).\n", flush=True)
    try:
        srv.serve_forever(poll_interval=0.5)
    finally:
        shutdown()
        app.release_all() if not done.is_set() else None
        app.lan.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
