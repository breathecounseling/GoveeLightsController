"""Remote access + installable app: the manifest and icons are served to anyone (no login,
nothing private, correct types, cached), while the app's pages and API still need the login
through a tunnel; the visitor page gets its icon from the one path neighbours can reach;
More -> "Use it away from home" learns how the phone is connected (a label only); the
committed icons match tools/make_icons.py; and, on an iPhone-sized screen, the manifest
link resolves with no console errors (tests/ui_install.js)."""
import http.client
import json
import os
import struct
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from test_auth import FakeReq  # noqa: E402
from test_r10_ui_playwright import NODE_PATH  # noqa: E402

from goveelights import app_server  # noqa: E402
from goveelights.auth import COOKIE, Auth  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "goveelights" / "web"
ICONS = {"/icons/icon-192.png": 192, "/icons/icon-512.png": 512, "/icons/apple-touch-icon.png": 180,
         "/apple-touch-icon.png": 180}
TUNNEL = {"Cf-Connecting-IP": "203.0.113.7", "Cf-Ray": "8a1b2c3d4e5f-DEN", "X-Forwarded-For": "203.0.113.7",
          "X-Forwarded-Proto": "https", "Host": "lights.example.com"}


def png_size(b: bytes):
    assert b[:8] == b"\x89PNG\r\n\x1a\n" and b[12:16] == b"IHDR"
    return struct.unpack(">II", b[16:24])


class Served(unittest.TestCase):
    """A real app server on a stand-in controller (these routes only need the visitor settings)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.auth = Auth(Path(tmp.name) / "auth.json")
        self.noted = []
        ctl = types.SimpleNamespace(vcfg={"enabled": True, "token": "tok-abc", "presets": []},
                                    note_remote=self.noted.append)
        self.srv = app_server.Server(("127.0.0.1", 0), app_server.make_handler(ctl, lambda: None, self.auth))
        threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    def req(self, path, headers=None, method="GET", body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1], timeout=10)
        c.request(method, path, body, headers or {})
        r = c.getresponse()
        out = r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()
        c.close()
        return out

    # ------------------------------------------------------------ the install files
    def check_static(self, headers):
        s, h, b = self.req("/manifest.webmanifest", headers)
        self.assertEqual(s, 200)
        self.assertEqual(h["content-type"], "application/manifest+json")
        self.assertEqual(h["cache-control"], "public, max-age=3600")
        self.assertEqual(h["x-content-type-options"], "nosniff")
        self.assertEqual(h["x-frame-options"], "DENY")
        self.assertNotIn("set-cookie", h)
        m = json.loads(b)
        self.assertEqual((m["name"], m["short_name"], m["start_url"], m["scope"], m["display"]),
                         ("House Lights", "Lights", "/", "/", "standalone"))
        self.assertEqual(m["theme_color"], "#0b0e13")             # the app's --bg
        self.assertEqual(m["background_color"], "#0b0e13")
        self.assertIn("--bg:#0b0e13", (WEB / "app.html").read_text(encoding="utf-8"))
        srcs = {i["src"] for i in m["icons"]}
        self.assertTrue({"/icons/icon-192.png", "/icons/icon-512.png", "/icons/icon.svg"} <= srcs)
        self.assertIn("maskable", {i.get("purpose") for i in m["icons"]})
        for path, size in ICONS.items():
            s, h, b = self.req(path, headers)
            self.assertEqual((s, h["content-type"]), (200, "image/png"), path)
            self.assertEqual(png_size(b), (size, size), path)
            self.assertEqual(h["cache-control"], "public, max-age=604800")
        s, h, b = self.req("/icons/icon.svg", headers)
        self.assertEqual((s, h["content-type"]), (200, "image/svg+xml"))
        self.assertTrue(b.startswith(b"<svg"))
        self.assertNotIn(b"<script", b)
        s, h, _ = self.req("/favicon.ico", headers)
        self.assertEqual((s, h["content-type"]), (200, "image/png"))

    def test_install_files_need_no_login(self):
        self.check_static({"Host": "192.168.1.5:8080"})                   # home Wi-Fi
        self.check_static(TUNNEL)                                          # tunnel, no password yet
        self.auth.set_password("correct horse battery")
        self.check_static(TUNNEL)                                          # tunnel, not signed in
        self.check_static({**TUNNEL, "Cookie": f"{COOKIE}=not-a-session"})

    def test_only_those_exact_paths(self):
        self.auth.set_password("correct horse battery")
        for p in ("/icons/", "/icons/../app.html", "/icons/icon-999.png", "/manifest.json", "/icons/icon.svg/x"):
            s, h, _ = self.req(p, TUNNEL)
            self.assertIn(s, (302, 401, 404), p)
            self.assertNotEqual(h.get("content-type"), "image/png", p)

    # ------------------------------------------------------------ everything else stays locked
    def test_pages_and_api_still_need_the_login_through_a_tunnel(self):
        for p in ("/", "/index.html", "/guide/remote", "/static/qrcode.js", "/diag"):
            s, h, _ = self.req(p, TUNNEL)
            self.assertEqual(s, 403, p)                                    # no password: refused outright
        self.auth.set_password("correct horse battery")
        for p in ("/", "/guide/remote", "/static/qrcode.js"):
            s, h, _ = self.req(p, TUNNEL)
            self.assertEqual((s, h.get("location")), (302, "/login"), p)
            self.assertEqual(h["x-frame-options"], "DENY")
        for p in ("/api/status", "/api/presets"):
            self.assertEqual(self.req(p, TUNNEL)[0], 401, p)
        # a tunnel request from the PC's own loopback is still remote
        s, _, b = self.req("/api/auth", TUNNEL)
        self.assertEqual(json.loads(b), {"password_set": True, "trusted": False, "logged_in": False})
        # POSTs: the CSRF header is still required, and the install files take no POST
        s, _, b = self.req("/manifest.webmanifest", {"Host": "127.0.0.1"}, "POST", b"{}")
        self.assertEqual(s, 403)
        self.assertIn(b"missing request header", b)
        s, _, _ = self.req("/manifest.webmanifest", {**TUNNEL, "X-Requested-With": "lights"}, "POST", b"{}")
        self.assertEqual(s, 302)                                          # to /login, not the file

    def test_signed_in_through_the_tunnel(self):
        self.auth.set_password("correct horse battery")
        tok, _ = self.auth.new_session()
        hdr = {**TUNNEL, "Cookie": f"{COOKIE}={tok}"}
        s, h, b = self.req("/", hdr)
        self.assertEqual(s, 200)
        self.assertEqual(h["cache-control"], "no-store")                  # the UI is never cached
        self.assertIn("frame-ancestors 'none'", h["content-security-policy"])
        self.assertIn(b'<link rel="manifest" href="/manifest.webmanifest" crossorigin="use-credentials">', b)
        self.assertEqual(self.noted, ["lights.example.com"])
        s, _, b = self.req("/api/auth", hdr)
        a = json.loads(b)
        self.assertEqual((a["access"], a["logged_in"], a["trusted"]), ("tunnel", True, False))
        s, h, b = self.req("/guide/remote", hdr)
        self.assertEqual((s, h["content-type"]), (200, "text/html; charset=utf-8"))
        self.assertIn(b"Control the lights from anywhere", b)
        self.assertIn(b"&lt;secret&gt;", b)                               # escaped, not markup

    def test_home_wifi(self):
        s, h, b = self.req("/", {"Host": "127.0.0.1:8080"})
        self.assertEqual((s, h["cache-control"]), (200, "no-store"))
        a = json.loads(self.req("/api/auth", {"Host": "127.0.0.1:8080"})[2])
        self.assertEqual((a["access"], a["trusted"]), ("this_pc", True))

    def test_visitor_page_icon(self):
        s, h, b = self.req("/api/visit/tok-abc/icon", TUNNEL)
        self.assertEqual((s, h["content-type"]), (200, "image/png"))
        self.assertEqual(png_size(b), (180, 180))
        self.assertEqual(self.req("/api/visit/wrong/icon", TUNNEL)[0], 404)


class AccessMode(unittest.TestCase):
    def test_labels(self):
        m = Auth.access_mode
        self.assertEqual(m(FakeReq("127.0.0.1", {"Host": "localhost:8080"})), "this_pc")
        self.assertEqual(m(FakeReq("192.168.1.20", {"Host": "192.168.1.5:8080"})), "home")
        self.assertEqual(m(FakeReq("::ffff:10.0.0.4", {"Host": "pc.local"})), "home")
        self.assertEqual(m(FakeReq("127.0.0.1", TUNNEL)), "tunnel")
        self.assertEqual(m(FakeReq("100.101.102.103", {"Host": "raspberrypi:8080"})), "tailscale")
        self.assertEqual(m(FakeReq("fd7a:115c:a1e0::5", {"Host": "raspberrypi:8080"})), "tailscale")
        self.assertEqual(m(FakeReq("127.0.0.1", {"Host": "raspberrypi.tail1234.ts.net",       # tailscale serve
                                                 "X-Forwarded-For": "100.90.1.2"})), "tailscale")
        self.assertEqual(m(FakeReq("127.0.0.1", {"Host": "lights.example.com", "X-Forwarded-For": "1.2.3.4"})), "proxy")
        self.assertEqual(m(FakeReq("203.0.113.9", {"Host": "1.2.3.4:8080"})), "internet")

    def test_tailscale_is_never_home_wifi(self):
        a = Auth(Path(tempfile.mkdtemp()) / "auth.json")
        self.assertFalse(a.trusted(FakeReq("100.101.102.103", {"Host": "raspberrypi:8080"})))
        self.assertFalse(a.trusted(FakeReq("127.0.0.1", {"Host": "raspberrypi.tail1234.ts.net",
                                                         "X-Forwarded-For": "100.90.1.2"})))


class Files(unittest.TestCase):
    def test_icons_match_the_drawing(self):
        sys.path.insert(0, str(ROOT / "tools"))
        try:
            import make_icons
        finally:
            sys.path.remove(str(ROOT / "tools"))
        self.assertEqual(make_icons.OUT, WEB / "icons")
        self.assertEqual((WEB / "icons" / "apple-touch-icon.png").read_bytes(), make_icons.png(180),
                         "re-run python3 tools/make_icons.py and commit the icons")
        self.assertEqual((WEB / "icons" / "icon.svg").read_text(encoding="utf-8"), make_icons.svg())
        # the bulbs and the house stay inside the middle 80 % circle (maskable safe zone)
        for x, y, _ in make_icons.BULBS:
            self.assertLess(((x - .5) ** 2 + (y - .5) ** 2) ** .5 + make_icons.BULB_R, 0.4)

    def test_heads(self):
        for name in ("app.html", "login.html"):
            s = (WEB / name).read_text(encoding="utf-8")
            self.assertIn('rel="manifest" href="/manifest.webmanifest" crossorigin="use-credentials"', s, name)
            self.assertIn('rel="apple-touch-icon" href="/icons/apple-touch-icon.png"', s, name)
            self.assertNotIn("serviceWorker", s, name)               # no offline cache: never stale
        v = (WEB / "visit.html").read_text(encoding="utf-8")
        self.assertNotIn('rel="manifest"', v)                         # a bookmark, not an app
        self.assertIn("API + 'icon'", v)

    def test_packaged(self):
        t = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('"web/*.webmanifest"', t)
        self.assertIn('"web/icons/*"', t)

    def test_guide_uses_placeholders(self):
        g = (ROOT / "REMOTE.md").read_text(encoding="utf-8")
        for s in ("yourdomain.com", "cloudflared service install", "tailscale serve", "docs/RASPBERRY_PI.md",
                  "api/visit", "One-time PIN", "localhost:8080"):
            self.assertIn(s, g)


@unittest.skipUnless(NODE_PATH, "node + Playwright not installed")
class InstallOnAPhone(unittest.TestCase):
    def test_iphone(self):
        from test_integration import Rig
        from goveelights.app_server import Server, make_handler
        r = Rig(self, datetime(2026, 11, 27, 14, 0).astimezone(), {"schedule": {"enabled": False}})
        srv = Server(("127.0.0.1", 0), make_handler(r.ctl, lambda: None, Auth(Path(r.tmp) / "auth.json")))
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        res = subprocess.run(["node", str(ROOT / "tests" / "ui_install.js"), f"http://127.0.0.1:{srv.server_address[1]}/",
                              os.environ.get("INSTALL_SHOT", "")],
                             capture_output=True, text=True, timeout=120, env={**os.environ, "NODE_PATH": NODE_PATH})
        self.assertEqual(res.returncode, 0, res.stderr)
        o = json.loads(res.stdout.strip().splitlines()[-1])
        self.assertEqual(o["errors"], [], o)
        for page in (o["app"], o["login"]):
            self.assertEqual(page["manifest /manifest.webmanifest"]["status"], 200, page)
            self.assertEqual(page["manifest /manifest.webmanifest"]["type"], "application/manifest+json")
            self.assertEqual(page["apple-touch-icon /icons/apple-touch-icon.png"]["type"], "image/png")
            self.assertEqual(page["icon /icons/icon.svg"]["type"], "image/svg+xml")
        self.assertEqual(o["app"]["manifest"]["short_name"], "Lights")
        self.assertEqual(o["icons"], {"/icons/icon-192.png": [192, 192], "/icons/icon-512.png": [512, 512],
                                      "/icons/icon.svg": [512, 512]})
        self.assertIn("computer that runs the lights", o["away"]["text"])
        self.assertIn("Listen", o["away"]["text"])
        self.assertEqual(o["away"]["href"], "/guide/remote")
        self.assertLessEqual(o["away"]["right"], o["away"]["width"])
        self.assertLessEqual(o["away"]["scroll"], o["away"]["width"])
        self.assertEqual(o["guide"]["path"], "/guide/remote")
        self.assertIn("Control the lights from anywhere", o["guide"]["text"])


if __name__ == "__main__":
    unittest.main()
