"""Creator app server: python -m goveelights (or start.bat)."""
from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .config import Config
from .controller import Controller
from .diag_server import build_report, lan_ips
from .effects import EFFECTS
from .scene import BLENDS

log = logging.getLogger("goveelights")
WEB = Path(__file__).resolve().parent / "web"


class Server(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR lets a second copy bind the same port; refuse instead.
    allow_reuse_address = os.name != "nt"


def make_handler(ctl: Controller, on_quit):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *a):
            log.debug("%s " + fmt, self.address_string(), *a)

        def _send(self, code, body: bytes, ctype="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj).encode())

        # ---------------------------------------------------------- GET
        def do_GET(self):
            u = urlparse(self.path)
            path, q = u.path, parse_qs(u.query)
            if path in ("/", "/index.html"):
                return self._send(200, (WEB / "app.html").read_bytes(), "text/html; charset=utf-8")
            if path in ("/diag", "/diag/"):
                return self._send(200, (WEB / "diag.html").read_bytes(), "text/html; charset=utf-8")
            if path == "/api/stream":
                return self._stream()
            try:
                if path == "/api/status":
                    return self._json(ctl.status())
                if path == "/api/layout":
                    return self._json(ctl.layout_info())
                if path == "/api/effects":
                    return self._json({"effects": [e.to_dict() for e in EFFECTS.values()], "blends": BLENDS})
                if path == "/api/presets":
                    return self._json(ctl.presets.list())
                if path == "/api/preset":
                    return self._json(ctl.presets.get(q.get("id", [""])[0]))
                if path == "/api/scene":
                    return self._json({"scene": ctl.scene_dict, "playing": ctl.playing})
                if path == "/api/playlists":
                    return self._json(ctl.cfg.data.get("playlists", {}))
                if path == "/api/schedule":
                    return self._json(ctl.cfg.data.get("schedule", {}))
                if path == "/api/diag/state":
                    return self._json(ctl.diag.state())
                if path == "/api/diag/report":
                    return self._send(200, json.dumps(build_report(ctl.diag), indent=2).encode())
            except (KeyError, ValueError, OSError) as e:
                return self._json({"ok": False, "error": str(e)}, 400)
            self._json({"error": "not found"}, 404)

        def _stream(self):
            """Server-sent events: the engine's current colours, ~12 times a second."""
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            try:
                last = None
                while True:
                    h = ctl.frame_hex()
                    if h != last:
                        self.wfile.write(f"data: {h}\n\n".encode())
                        self.wfile.flush()
                        last = h
                    time.sleep(1 / 12)
            except (OSError, ValueError):
                return

        # --------------------------------------------------------- POST
        def do_POST(self):
            path = urlparse(self.path).path
            try:
                ln = int(self.headers.get("Content-Length") or 0)
                b = json.loads(self.rfile.read(ln) or b"{}")
                if path.startswith("/api/diag/") and path != "/api/diag/exit":
                    action = path[len("/api/diag/"):]
                    if action not in ("scan", "add_ip", "device", "note", "landmark", "status"):
                        ctl.enter_diag()       # anything that drives the lights pauses effects
                    res = ctl.diag.handle(action, b)
                    if action == "device" and not ctl.diag_active:
                        ctl.build_engine()     # names / counts feed the virtual strip
                    return self._json(res)
                return self._json(self._api(path[len("/api/"):], b))
            except (KeyError, ValueError, OSError, TypeError) as e:
                return self._json({"ok": False, "error": str(e)}, 400)

        def _api(self, a: str, b: dict):
            cfg = ctl.cfg
            if a == "play":
                if b.get("preset"):
                    ctl.play_preset(b["preset"], fade=float(b.get("fade", 1)))
                elif b.get("playlist"):
                    ctl.play_playlist(b["playlist"])
                elif b.get("scene"):
                    ctl.play_scene(b["scene"], fade=float(b.get("fade", 0)))
                if b.get("live"):
                    ctl.set_live(True)
                return {"ok": True}
            if a == "scene":                       # editor: replace the playing scene instantly
                ctl.play_scene(b["scene"], {"kind": "scene", "name": b["scene"].get("name", "untitled"),
                                            "id": b.get("id")}, fade=0)
                return {"ok": True}
            if a == "live":
                ctl.set_live(bool(b.get("on")))
                return {"ok": True}
            if a == "stop":
                ctl.stop()
                return {"ok": True}
            if a == "release":                     # hand the lights back to the Govee app
                ctl.stop()
                ctl.diag.release_all()
                return {"ok": True}
            if a == "settings":
                ctl.settings(b)
                return {"ok": True}
            if a == "preset/save":
                return {"ok": True, "id": ctl.presets.save(b["scene"], b.get("id"))}
            if a == "preset/delete":
                ctl.presets.delete(b["id"])
                return {"ok": True}
            if a == "playlists":
                pls = b["playlists"]
                if not isinstance(pls, dict):
                    raise ValueError("playlists must be an object")
                with cfg.lock:
                    cfg.data["playlists"] = pls
                    cfg.save()
                return {"ok": True}
            if a == "schedule":
                with cfg.lock:
                    cfg.data["schedule"] = {**cfg.data.get("schedule", {}), **b["schedule"]}
                    cfg.save()
                ctl.resume_schedule()
                return {"ok": True}
            if a == "schedule/resume":
                ctl.resume_schedule()
                return {"ok": True}
            if a == "diag/exit":
                ctl.exit_diag()
                return {"ok": True}
            if a == "quit":
                threading.Thread(target=on_quit, daemon=True).start()
                return {"ok": True}
            raise KeyError(f"unknown action {a}")
    return H


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="python -m goveelights", description="Govee lights effects creator")
    ap.add_argument("--config")
    ap.add_argument("--port", type=int)
    ap.add_argument("--bind-ip", help="this PC's address on the lights network (if discovery finds nothing)")
    ap.add_argument("--log", help="log to this file (used when running in the background)")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    background = sys.stdout is None or a.log
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        filename=a.log or (str(Path(__file__).resolve().parent.parent / "goveelights.log") if sys.stdout is None else None),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    cfg = Config(a.config) if a.config else Config()
    port = a.port or cfg.data["server"].get("port", 8080)
    try:
        srv = Server((cfg.data["server"].get("host", "0.0.0.0"), port), BaseHTTPRequestHandler)
    except OSError as e:
        msg = f"Port {port} is busy ({e}). Is the app already running? Open http://localhost:{port}/"
        log.error(msg)
        raise SystemExit(msg)
    ctl = Controller(cfg, a.bind_ip or "")
    done = threading.Event()

    def quit_(*_):
        if done.is_set():
            return
        done.set()
        log.info("shutting down, releasing lights to the Govee app")
        ctl.shutdown()
        threading.Thread(target=srv.shutdown, daemon=True).start()

    srv.RequestHandlerClass = make_handler(ctl, quit_)
    for sig in ("SIGINT", "SIGTERM", "SIGBREAK"):
        if hasattr(signal, sig):
            signal.signal(getattr(signal, sig), quit_)
    urls = "\n".join(f"      http://{ip}:{port}/" for ip in lan_ips())
    banner = (f"\n  Govee lights creator running.\n  On this PC:  http://localhost:{port}/\n"
              f"  On your phone (same network):\n{urls}\n  Config: {cfg.path}\n"
              f"  Ctrl+C to stop (lights are released to the Govee app).\n")
    log.info("listening on port %d", port)
    if not background:
        print(banner, flush=True)
    try:
        srv.serve_forever(poll_interval=0.5)
    finally:
        quit_()
        ctl.lan.close()
    return 0
