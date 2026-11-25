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

from .auth import COOKIE, Auth
from .config import Config
from .controller import Controller
from .diag_server import build_report, lan_ips
from .effects import EFFECTS
from .fx import FX
from .modulation import SHAPES
from .palettes import BUILTIN, all_palettes, categories
from .scene import BLEND_INFO, BLENDS

log = logging.getLogger("goveelights")
WEB = Path(__file__).resolve().parent / "web"


class Server(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR lets a second copy bind the same port; refuse instead.
    allow_reuse_address = os.name != "nt"


CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
PUBLIC = {"/login", "/api/login", "/api/auth"}


def make_handler(ctl: Controller, on_quit, auth: Auth):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *a):
            log.debug("%s " + fmt, self.address_string(), *a)

        def _sec_headers(self):
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", CSP)

        def _send(self, code, body: bytes, ctype="application/json", headers=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self._sec_headers()
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _https(self) -> bool:
            return (self.headers.get("X-Forwarded-Proto", "").lower() == "https"
                    or "https" in self.headers.get("Cf-Visitor", ""))

        def _guard(self, path: str) -> bool:
            """True if the request may proceed; otherwise a response has been sent."""
            auth.refresh()
            if path in PUBLIC or auth.authorized(self):
                return True
            if not auth.password_set:
                msg = ("Remote access is switched off until a password is set. From home Wi-Fi open "
                       "More → Security, or on the PC run: python -m goveelights set-password")
                if path.startswith("/api/"):
                    self._json({"ok": False, "error": msg, "login": False}, 403)
                else:
                    self._send(403, f"<!doctype html><meta name=viewport content='width=device-width'>"
                                    f"<body style='font:16px system-ui;background:#0b0e13;color:#e8edf3;padding:24px'>"
                                    f"<h2>House Lights</h2><p>{msg}</p>".encode(), "text/html; charset=utf-8")
                return False
            if path.startswith("/api/"):
                self._json({"ok": False, "error": "login required", "login": True}, 401)
            else:
                self.send_response(302)
                self.send_header("Location", "/login")
                self.send_header("Content-Length", "0")
                self._sec_headers()
                self.end_headers()
            return False

        # ---------------------------------------------------------- GET
        def do_GET(self):
            u = urlparse(self.path)
            path, q = u.path, parse_qs(u.query)
            if not self._guard(path):
                return
            if path == "/login":
                return self._send(200, (WEB / "login.html").read_bytes(), "text/html; charset=utf-8")
            if path == "/api/auth":
                return self._json(auth.status(self))
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
                    return self._json({"effects": [e.to_dict() for e in EFFECTS.values()],
                                       "fx": [f.to_dict() for f in FX.values()], "blends": BLENDS,
                                       "blend_info": BLEND_INFO,
                                       "shapes": SHAPES})
                if path == "/api/palettes":
                    return self._json({"palettes": all_palettes(ctl.user_palettes), "builtin": list(BUILTIN),
                                       "user": list(ctl.user_palettes), "categories": categories(ctl.user_palettes)})
                if path == "/api/thumb":
                    pid = q.get("id", [""])[0]
                    meta = next((p for p in ctl.presets.list() if p["id"] == pid), None)
                    if not meta:
                        raise KeyError(pid)
                    return self._json(ctl.thumbnail(ctl.presets.get(pid), (pid, meta["mtime"])))
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
                if path == "/api/extras":
                    from .live import LEAGUES
                    from .pictures import PICTURES
                    return self._json({"events": ctl.events, "game": ctl.cfg.data.get("game", {}),
                                       "weather": ctl.cfg.data.get("weather", {}),
                                       "leagues": {k: v[2] for k, v in LEAGUES.items()}, "pictures": list(PICTURES)})
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
            self.send_header("Cache-Control", "no-store, no-transform")
            self.send_header("X-Accel-Buffering", "no")       # don't let proxies buffer the stream
            self.send_header("Connection", "close")
            self._sec_headers()
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
            if self.headers.get("X-Requested-With") != "lights":     # CSRF guard
                return self._json({"ok": False, "error": "missing request header"}, 403)
            if not self._guard(path):
                return
            try:
                ln = int(self.headers.get("Content-Length") or 0)
                if ln > 1_000_000:
                    raise ValueError("request too large")
                b = json.loads(self.rfile.read(ln) or b"{}")
                if path == "/api/login":
                    return self._login(b)
                if path == "/api/logout":
                    auth.end_session(auth.cookie(self))
                    return self._json({"ok": True}, 200, {"Set-Cookie": f"{COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"})
                if path == "/api/auth/password":
                    if auth.password_set and not auth.check_password(str(b.get("current", ""))):
                        auth.record_fail(auth.client_ip(self))
                        return self._json({"ok": False, "error": "current password is wrong"}, 403)
                    auth.set_password(str(b.get("new", "")))
                    return self._json({"ok": True})
                if path == "/api/auth/lan":
                    with auth.lock:
                        auth.data["lan_without_login"] = bool(b.get("on"))
                        auth._save()
                    return self._json({"ok": True})
                if path == "/api/auth/revoke":
                    auth.revoke_all()
                    return self._json({"ok": True})
                if path.startswith("/api/diag/") and path != "/api/diag/exit":
                    action = path[len("/api/diag/"):]
                    if action not in ("scan", "add_ip", "device", "note", "landmark", "status", "check"):
                        ctl.enter_diag()       # anything that drives the lights pauses effects
                    res = ctl.diag.handle(action, b)
                    if action == "device" and not ctl.diag_active:
                        ctl.build_engine()     # names / counts feed the virtual strip
                    return self._json(res)
                return self._json(self._api(path[len("/api/"):], b))
            except (KeyError, ValueError, OSError, TypeError) as e:
                return self._json({"ok": False, "error": str(e)}, 400)

        def _json(self, obj, code=200, headers=None):
            self._send(code, json.dumps(obj).encode(), headers=headers)

        def _login(self, b: dict):
            ip = auth.client_ip(self)
            if not auth.allow_attempt(ip):
                return self._json({"ok": False, "error": "too many attempts, wait 10 minutes"}, 429)
            if not auth.password_set or not auth.check_password(str(b.get("password", ""))):
                auth.record_fail(ip)
                time.sleep(0.6)
                return self._json({"ok": False, "error": "wrong password"}, 403)
            token, ttl = auth.new_session()
            cookie = f"{COOKIE}={token}; Path=/; Max-Age={ttl}; HttpOnly; SameSite=Lax" + ("; Secure" if self._https() else "")
            log.info("login from %s", ip)
            return self._json({"ok": True}, 200, {"Set-Cookie": cookie})

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
                                            "id": b.get("id")}, fade=0, continuous=True)
                return {"ok": True}
            if a == "master":
                ctl.set_master(b)
                return {"ok": True}
            if a == "palettes":
                pals = b["palettes"]
                if not isinstance(pals, dict) or not all(isinstance(v, list) and v for v in pals.values()):
                    raise ValueError("palettes must be {name: [colours]}")
                with cfg.lock:
                    cfg.data["palettes"] = pals
                    cfg.save()
                return {"ok": True}
            if a == "preset/meta":
                ctl.presets.set_meta(b["id"], b.get("favorite"), b.get("tags"))
                return {"ok": True}
            if a == "thumb":                       # thumbnail for an unsaved scene (randomizer)
                import hashlib
                frames = max(4, min(32, int(b.get("frames", 16))))
                key = ("adhoc", hashlib.sha1(json.dumps(b["scene"], sort_keys=True).encode()).hexdigest(), frames)
                return ctl.thumbnail(b["scene"], key=key, frames=frames, fps=float(b.get("fps", 8)))
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
            if a in ("events", "game", "weather"):
                val = b["value"]
                if a == "events":
                    if not isinstance(val, list):
                        raise ValueError("events must be a list")
                    for ev in val:
                        int(ev["month"]), int(ev["day"]), str(ev.get("time", "00:00")).split(":")
                elif not isinstance(val, dict):
                    raise ValueError(f"{a} must be an object")
                with cfg.lock:
                    cfg.data[a] = val
                    cfg.save()
                return {"ok": True}
            if a == "live/test":
                return {"ok": True, "message": ctl.test_live(str(b.get("kind")))}
            if a == "interrupt/end":
                ctl.end_interrupt()
                return {"ok": True}
            if a == "map/start":
                return {"ok": True, **ctl.map_start()}
            if a == "map/stop":
                ctl.map_stop()
                return {"ok": True}
            if a == "map/apply":
                return {"ok": True, **ctl.map_apply(b["points"], b.get("target", "all"))}
            if a == "map/undo":
                return {"ok": True, "restored": ctl.map_undo()}
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
    auth = Auth(Path(os.environ.get("GOVEE_AUTH", Path(cfg.path).with_name("govee_auth.json"))))
    done = threading.Event()

    def quit_(*_):
        if done.is_set():
            return
        done.set()
        log.info("shutting down, releasing lights to the Govee app")
        ctl.shutdown()
        threading.Thread(target=srv.shutdown, daemon=True).start()

    srv.RequestHandlerClass = make_handler(ctl, quit_, auth)
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
