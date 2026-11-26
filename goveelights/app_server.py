"""Creator app server: python -m goveelights (or start.bat)."""
from __future__ import annotations

import hmac
import json
import logging
import os
import secrets
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .auth import COOKIE, Auth
from .config import Config
from .controller import Controller
from .diag_server import Server, build_report, lan_ips
from .effects import EFFECTS, GROUP_ORDER
from .fx import FX
from .modulation import SHAPES
from .palettes import BUILTIN, all_palettes, categories
from .scene import BLEND_INFO, BLENDS
from . import calendar_builtin
from . import schedule as sched
from . import shows_builtin
from . import winconsole

log = logging.getLogger("goveelights")
WEB = Path(__file__).resolve().parent / "web"


CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
PUBLIC = {"/login", "/api/login", "/api/auth"}


def _check_hm(v, what: str):
    if not isinstance(v, str):
        raise ValueError(f"{what} must be a time like 18:30")
    try:
        sched._hm(v)
    except (ValueError, TypeError):
        raise ValueError(f"{what} must be a time like 18:30") from None


_MONTH_DAYS = (31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)    # 02-29 allowed (leap years)


def _check_mmdd(v, what: str):
    try:
        ok = (isinstance(v, str) and len(v) == 5 and v[2] == "-" and 1 <= int(v[:2]) <= 12
              and 1 <= int(v[3:]) <= _MONTH_DAYS[int(v[:2]) - 1])
    except ValueError:
        ok = False
    if not ok:
        raise ValueError(f"{what} must be a date like 12-31")


def validate_showtimes(val) -> None:
    """Reject anything the controller's showtime check couldn't digest (a bad entry would
    otherwise raise every second inside the tick loop and starve the schedule)."""
    if not isinstance(val, list):
        raise ValueError("showtimes must be a list")
    for k, st in enumerate(val, 1):
        w = f"showtime {k}"
        if not isinstance(st, dict):
            raise ValueError(f"{w} must be an object")
        if not isinstance(st.get("show"), str) or not st["show"]:
            raise ValueError(f"{w}: pick a show")
        for f in ("from", "to"):
            if f in st:
                _check_hm(st[f], f"{w}: '{f}'")
        if "every" in st:
            e = st["every"]
            if isinstance(e, bool) or not isinstance(e, (int, float)) or not 5 <= e <= 1440 or int(e) != e:
                raise ValueError(f"{w}: 'every' must be a whole number of minutes, 5 to 1440")
        days = st.get("days")
        if days is not None and (not isinstance(days, list) or
                                 not all(isinstance(d, int) and not isinstance(d, bool) and 0 <= d <= 6 for d in days)):
            raise ValueError(f"{w}: 'days' must be a list of weekdays 0 (Mon) to 6 (Sun)")
        for f in ("date", "dates"):
            d = st.get(f)
            if d is None or d == "" or d == []:
                continue
            for x in ([d] if isinstance(d, str) else d if isinstance(d, list) else [None]):
                _check_mmdd(x, f"{w}: '{f}'")


def validate_shows(val) -> None:
    if not isinstance(val, dict):
        raise ValueError("shows must be an object")
    for name, sh in val.items():
        if not isinstance(sh, dict):
            raise ValueError(f"show '{name}' must be an object")
        cues = sh.get("cues", [])
        if not isinstance(cues, list) or not all(isinstance(c, dict) for c in cues):
            raise ValueError(f"show '{name}': cues must be a list of objects")


def validate_schedule(sch) -> None:
    """Evaluate a schedule over the coming year: a value it can't handle (an emptied time
    box, a bad latitude, rules: null...) would otherwise fail on every schedule tick."""
    from datetime import datetime, timedelta
    if not isinstance(sch, dict):
        raise ValueError("schedule must be an object")
    start = datetime.now().astimezone().replace(hour=0, minute=30, second=0, microsecond=0)
    try:
        for day in range(367):
            for hour in (0, 12, 20):
                sched.evaluate(start + timedelta(days=day, hours=hour), sch)
    except Exception as e:                  # noqa: BLE001 - any failure means the schedule is unusable
        raise ValueError(f"schedule not saved, a value is invalid ({type(e).__name__}: {e})") from None


def make_handler(ctl: Controller, on_quit, auth: Auth):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        timeout = 30          # a client that goes quiet mid-request can't hold a thread forever

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

        def _visitor(self, path: str):
            """Public visitor routes: /visit/<token>, /api/visit/<token>/<action>. Returns
            (token_ok, action) or None if the path isn't a visitor route."""
            parts = path.strip("/").split("/")
            if parts[0] == "visit" and len(parts) == 2:
                tok, action = parts[1], "page"
            elif parts[:2] == ["api", "visit"] and len(parts) == 4:
                tok, action = parts[2], parts[3]
            else:
                return None
            v = ctl.vcfg
            good = bool(v.get("enabled") and v.get("token") and hmac.compare_digest(tok.encode(), v["token"].encode()))
            return good, action

        def _visitor_get(self, action: str, q: dict):
            if action == "page":
                return self._send(200, (WEB / "visit.html").read_bytes(), "text/html; charset=utf-8")
            if action == "state":
                return self._json(ctl.visitor_state(auth.rate_key(auth.client_ip(self))))
            if action == "frame":                     # the little live view of the house
                return self._json({"hex": ctl.visitor_frame()})
            if action == "layout":
                info = ctl.layout_info()
                return self._json({"pixels": info["pixels"], "bounds": info["bounds"]})
            if action == "thumb":
                pid = q.get("id", [""])[0]
                if pid not in ctl.vcfg.get("presets", []):
                    return self._json({"error": "not found"}, 404)
                meta = next((p for p in ctl.presets.list() if p["id"] == pid), None)
                return self._json(ctl.thumbnail(ctl.presets.get(pid), (pid, meta["mtime"] if meta else 0)))
            return self._json({"error": "not found"}, 404)

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
        def _no_body_expected(self):
            """GET carries no body here. If one is announced, close the connection after the
            reply: otherwise its bytes would be parsed as a further request on the same
            connection, arriving from the tunnel's loopback address without proxy headers."""
            cl = (self.headers.get("Content-Length") or "").strip()
            if self.headers.get("Transfer-Encoding") is not None or cl not in ("", "0"):
                self.close_connection = True

        def do_GET(self):
            self._no_body_expected()
            auth.proxied(self)            # remember a tunnelled connection (see Auth.proxied)
            u = urlparse(self.path)
            path, q = u.path, parse_qs(u.query)
            vis = self._visitor(path)
            if vis is not None:
                if not vis[0]:
                    return self._json({"error": "not found"}, 404)
                try:
                    return self._visitor_get(vis[1], q)
                except Exception:                                      # noqa: BLE001
                    log.exception("visitor page request failed")      # details stay in the log
                    return self._json({"ok": False, "error": "unavailable"}, 400)
            if not self._guard(path):
                return
            if path == "/static/qrcode.js":
                return self._send(200, (WEB / "vendor" / "qrcode.js").read_bytes(), "text/javascript")
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
                                       "blend_info": BLEND_INFO, "group_order": GROUP_ORDER,
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
                if path == "/api/shows":           # built-in and user shows, for pickers
                    return self._json(ctl.shows_list())
                if path == "/api/schedule":
                    return self._json(ctl.cfg.data.get("schedule", {}))
                if path == "/api/tonight":         # the plan + checklist (cached state only, no network)
                    return self._json(ctl.tonight(password_set=auth.password_set))
                if path == "/api/calendar":        # preview of the one-tap holiday calendar
                    return self._json(ctl.calendar_preview())
                if path == "/api/extras":
                    from .live import LEAGUES
                    from .pictures import PICTURES
                    from .engine import Engine
                    return self._json({"events": ctl.events, "game": ctl.cfg.data.get("game", {}),
                                       "weather": ctl.cfg.data.get("weather", {}),
                                       "shows": ctl.cfg.data.get("shows", {}), "showtimes": ctl.cfg.data.get("showtimes", []),
                                       "builtin_shows": shows_builtin.builtin_shows(),
                                       "visitor": ctl.vcfg, "transitions": Engine.TRANSITIONS,
                                       "self_heal": ctl.ecfg.get("self_heal", True), "reenable_min": ctl.ecfg.get("reenable_min", 0),
                                       "leagues": {k: v[2] for k, v in LEAGUES.items()}, "pictures": list(PICTURES)})
                if path == "/api/diag/state":
                    return self._json(ctl.diag.state())
                if path == "/api/diag/report":
                    return self._send(200, json.dumps(build_report(ctl.diag), indent=2).encode())
            except (KeyError, ValueError, OSError) as e:
                return self._json({"ok": False, "error": str(e)}, 400)
            except Exception:                      # noqa: BLE001 - always answer, never drop the connection
                log.exception("GET %s failed", path)
                return self._json({"ok": False, "error": "internal error (see the log)"}, 500)
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
            auth.proxied(self)            # remember a tunnelled connection (see Auth.proxied)
            # Read (or refuse) the body first, so every response leaves the connection clean.
            try:
                ln = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                ln = -1
            vis = self._visitor(path)
            auth.refresh()
            small = vis is not None or not auth.authorized(self)   # visitors, logins, strangers
            limit = 10_000 if small else 1_000_000
            if self.headers.get("Transfer-Encoding") is not None:      # chunked bodies aren't read
                self.close_connection = True
                return self._json({"ok": False, "error": "Content-Length required"}, 411)
            if ln < 0 or ln > limit:
                self.close_connection = True
                return self._json({"ok": False, "error": "bad request size"}, 413)
            raw = self.rfile.read(ln) if ln else b""
            if self.headers.get("X-Requested-With") != "lights":     # CSRF guard
                return self._json({"ok": False, "error": "missing request header"}, 403)
            if vis is not None:
                if not vis[0]:
                    return self._json({"error": "not found"}, 404)
                client = auth.rate_key(auth.client_ip(self))      # an IPv6 /64 is one client
                try:
                    b = json.loads(raw or b"{}")
                    if not isinstance(b, dict):
                        raise ValueError("bad request")
                    if vis[1] == "pick":
                        return self._json({"ok": True, **ctl.visitor_pick(str(b.get("id", "")), client)})
                    if vis[1] == "sparkle":
                        ctl.visitor_sparkle(client)
                        return self._json({"ok": True})
                    if vis[1] == "snow":
                        ctl.visitor_snow(client)
                        return self._json({"ok": True})
                    if vis[1] == "colour":
                        h = b.get("hex")
                        ctl.visitor_colour(h if isinstance(h, str) else "", client)
                        return self._json({"ok": True})
                except (ValueError, TypeError) as e:
                    return self._json({"ok": False, "error": str(e)}, 429 if "again" in str(e) or "seconds" in str(e) else 400)
                except Exception:                                  # noqa: BLE001
                    log.exception("visitor request failed")
                    return self._json({"ok": False, "error": "unavailable"}, 503)
                return self._json({"error": "not found"}, 404)
            if not self._guard(path):
                return
            try:
                b = json.loads(raw or b"{}")
                if not isinstance(b, dict):
                    raise ValueError("request body must be a JSON object")
                if path == "/api/login":
                    return self._login(b)
                if path == "/api/logout":
                    auth.end_session(auth.cookie(self))
                    return self._json({"ok": True}, 200, {"Set-Cookie": f"{COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"})
                if path == "/api/auth/password":
                    if auth.password_set:              # same limit as logging in
                        ip = auth.client_ip(self)
                        tries = auth.begin_attempt(ip)
                        if tries is None:
                            return self._json({"ok": False, "error": "too many attempts, wait 10 minutes"}, 429)
                        time.sleep(tries[1])
                        if not auth.check_password(str(b.get("current", ""))):
                            return self._json({"ok": False, "error": "current password is wrong"}, 403)
                        auth.attempt_ok(ip, tries[0])
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
            except Exception as e:                 # noqa: BLE001 - always answer, never drop the connection
                log.exception("POST %s failed", path)
                return self._json({"ok": False, "error": f"bad request ({type(e).__name__})"}, 400)

        def _json(self, obj, code=200, headers=None):
            self._send(code, json.dumps(obj).encode(), headers=headers)

        def _login(self, b: dict):
            ip = auth.client_ip(self)
            tries = auth.begin_attempt(ip)     # counted as a failure until it proves right
            if tries is None:
                return self._json({"ok": False, "error": "too many attempts, wait 10 minutes"}, 429)
            time.sleep(tries[1])
            if not auth.password_set or not auth.check_password(str(b.get("password", ""))):
                time.sleep(0.6)
                return self._json({"ok": False, "error": "wrong password"}, 403)
            auth.attempt_ok(ip, tries[0])
            token, ttl = auth.new_session()
            cookie = f"{COOKIE}={token}; Path=/; Max-Age={ttl}; HttpOnly; SameSite=Lax" + ("; Secure" if self._https() else "")
            log.info("login from %s", ip)
            return self._json({"ok": True}, 200, {"Set-Cookie": cookie})

        def _api(self, a: str, b: dict):
            cfg = ctl.cfg
            if a == "play":
                if b.get("evolve"):
                    ctl.play_evolve(b["evolve"], float(b.get("minutes", 3)), float(b.get("amount", 0.2)))
                elif b.get("preset"):
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
            if a == "wled/add":
                return {"ok": True, **ctl.add_wled(str(b["address"]), str(b.get("name", "")))}
            if a == "device/remove":
                if ctl.cfg.devices.get(b["id"], {}).get("kind") != "wled":
                    raise ValueError("only WLED controllers can be removed here")
                ctl.remove_device(b["id"])
                return {"ok": True}
            if a == "trim":
                mac, tr = b["mac"], b.get("trim")
                tr = [max(0.2, min(1.0, float(v))) for v in tr] if tr else None
                with cfg.lock:
                    cfg.device(mac)["trim"] = tr
                    cfg.save()
                r = ctl.engine.layout.run(mac)
                if r:
                    r.trim = tuple(tr) if tr else None
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
                if not isinstance(b["schedule"], dict):
                    raise ValueError("schedule must be an object")
                merged = {**cfg.data.get("schedule", {}), **b["schedule"]}
                validate_schedule(merged)
                with cfg.lock:
                    cfg.data["schedule"] = {**cfg.data.get("schedule", {}), **b["schedule"]}
                    if cfg.data.get("showtimes"):          # the calendar's Santa show stays after lights-on
                        cfg.data["showtimes"] = calendar_builtin.refit(cfg.data["showtimes"], cfg.data["schedule"])
                    cfg.save()
                ctl.resume_schedule()
                return {"ok": True}
            if a == "calendar/apply":
                dis = b.get("disable") or []
                if not isinstance(dis, list):
                    raise ValueError("disable must be a list of rule numbers")
                return {"ok": True, **ctl.calendar_apply(dis, bool(b.get("enable")))}
            if a == "calendar/remove":
                return {"ok": True, **ctl.calendar_remove()}
            if a == "schedule/resume":
                ctl.resume_schedule()
                return {"ok": True}
            if a in ("events", "game", "weather", "shows", "showtimes", "visitor"):
                val = b["value"]
                if a == "visitor":
                    if not isinstance(val, dict):
                        raise ValueError("visitor must be an object")
                    if val.get("new_link") or not ctl.vcfg.get("token"):
                        val["token"] = secrets.token_urlsafe(12)
                    else:
                        val["token"] = ctl.vcfg["token"]           # the link only changes when asked
                    val.pop("new_link", None)
                elif a == "shows":
                    validate_shows(val)
                    val = shows_builtin.without_builtin_copies(val)     # built-ins live in code
                elif a == "showtimes":
                    validate_showtimes(val)
                if a == "events":
                    if not isinstance(val, list):
                        raise ValueError("events must be a list")
                    for ev in val:
                        int(ev["month"]), int(ev["day"]), str(ev.get("time", "00:00")).split(":")
                elif a in ("game", "weather") and not isinstance(val, dict):
                    raise ValueError(f"{a} must be an object")
                with cfg.lock:
                    cfg.data[a] = val
                    cfg.save()
                return {"ok": True}
            if a == "show/copy":                  # e.g. copy a built-in show to edit it
                return {"ok": True, "name": ctl.copy_show(str(b["name"]), str(b.get("as") or ""))}
            if a == "show/play":
                if not ctl.play_show(str(b["name"])):
                    raise ValueError("can't start a show while diagnostics or mapping is running")
                return {"ok": True}
            if a == "pad":
                return {"ok": True, "message": ctl.pad(str(b["action"]), b.get("on"), str(b.get("color", "#ff2000")))}
            if a == "live/test":
                return {"ok": True, "message": ctl.test_live(str(b.get("kind")))}
            if a == "interrupt/end":
                ctl.end_interrupt()
                return {"ok": True}
            if a == "map/start":
                return {"ok": True, **ctl.map_start(float(b.get("level", 0.35)))}
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
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S",
                        encoding="utf-8")       # device names can hold any character
    winconsole.disable_quickedit()             # a click in the console must not freeze logging
    winconsole.fine_sleep()
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
    auth.hosts = list(cfg.data["server"].get("hosts", []))    # extra home names for this PC
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
    winconsole.on_console_close(quit_)         # closing the window ends us before SIGBREAK runs
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
