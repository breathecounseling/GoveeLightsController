"""Regression tests for the round-6 auth fixes: DNS rebinding (Host check), lockout on the
change-password route, the login race, the global lockout, the diagnostic server, the
auth status leak and slow/large unauthenticated bodies."""
import json
import socket
import tempfile
import threading
import unittest
from pathlib import Path

from goveelights import diag_server
from goveelights.app_server import Server, make_handler
from goveelights.auth import Auth, local_host


class _StubCtl:
    vcfg = {"enabled": False}

    def status(self):
        return {"ok": "status"}


class _Srv(unittest.TestCase):
    password = "a long enough password"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.auth = Auth(Path(self.tmp.name) / "auth.json")
        if self.password:
            self.auth.set_password(self.password)
        self.handler = self.make()
        self.srv = Server(("127.0.0.1", 0), self.handler)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def make(self):
        return make_handler(_StubCtl(), lambda: None, self.auth)

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def req(self, method, path, host="localhost:8080", headers=None, body=None):
        data = b"" if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
        h = {"Host": host, "Connection": "close", **(headers or {})}
        if method == "POST":
            h.setdefault("X-Requested-With", "lights")
            h.setdefault("Content-Length", str(len(data)))
        raw = f"{method} {path} HTTP/1.1\r\n" + "".join(f"{k}: {v}\r\n" for k, v in h.items()) + "\r\n"
        s = socket.create_connection(("127.0.0.1", self.port), timeout=10)
        s.sendall(raw.encode() + data)
        out = b""
        while True:
            c = s.recv(65536)
            if not c:
                break
            out += c
        s.close()
        head, _, body = out.partition(b"\r\n\r\n")
        return int(head.split()[1]), body


class LocalHostTests(unittest.TestCase):
    def test_names(self):
        for h in (None, "localhost", "localhost:8080", "127.0.0.1:8080", "192.168.1.50", "[::1]:8080",
                  "DESKTOP-1234:8080", "pc.local", "pc.lan.", "pc.home.arpa:8080"):
            self.assertTrue(local_host(h), h)
        for h in ("evil.attacker.example", "evil.attacker.example:8080", "127.0.0.1.nip.io", "", "lights.example.com"):
            self.assertFalse(local_host(h), h)
        self.assertTrue(local_host("Lights.Example.com:8080", ["lights.example.com"]))


class RebindingTests(_Srv):
    def test_foreign_host_gets_no_lan_trust(self):
        self.assertEqual(self.req("GET", "/api/status")[0], 200)
        self.assertEqual(self.req("GET", "/api/status", host="192.168.1.50:8080")[0], 200)
        code, body = self.req("GET", "/api/status", host="evil.attacker.example:8080")
        self.assertEqual(code, 401, body)
        code, body = self.req("POST", "/api/auth/lan", host="evil.attacker.example:8080", body={"on": False})
        self.assertEqual(code, 401, body)
        self.auth.refresh()
        self.assertTrue(self.auth.data["lan_without_login"])
        self.assertFalse(json.loads(self.req("GET", "/api/auth", host="evil.attacker.example")[1])["trusted"])


class FirstRunRebindingTests(_Srv):
    password = None

    def test_rebinding_page_cannot_choose_the_first_password(self):
        code, _ = self.req("POST", "/api/auth/password", host="evil.attacker.example:8080",
                           body={"new": "attacker chosen pw"})
        self.assertEqual(code, 403)
        self.auth.refresh()
        self.assertFalse(self.auth.password_set)


class PasswordRouteLockoutTests(_Srv):
    def test_current_password_guesses_are_limited(self):
        codes = [self.req("POST", "/api/auth/password", body={"current": f"guess {k}", "new": "x" * 12})[0]
                 for k in range(7)]
        self.assertEqual(codes[:5], [403] * 5)
        self.assertEqual(codes[5:], [429, 429])
        self.assertTrue(self.auth.check_password(self.password))


class LoginRaceTests(_Srv):
    def test_parallel_burst_gets_only_five_checks(self):
        codes = []

        def one(k):
            codes.append(self.req("POST", "/api/login", headers={"Cf-Connecting-IP": "203.0.113.7"},
                                  body={"password": f"wrong {k}"})[0])
        ts = [threading.Thread(target=one, args=(k,)) for k in range(30)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(codes.count(403), 5, codes)
        self.assertEqual(codes.count(429), 25, codes)

    def test_begin_attempt_is_atomic(self):
        got = []
        barrier = threading.Barrier(40)

        def one():
            barrier.wait()
            got.append(self.auth.begin_attempt("198.51.100.3"))
        ts = [threading.Thread(target=one) for _ in range(40)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(sum(g is not None for g in got), 5)

    def test_right_password_does_not_count(self):
        for _ in range(6):
            code, _ = self.req("POST", "/api/login", headers={"Cf-Connecting-IP": "203.0.113.8"},
                               body={"password": self.password})
            self.assertEqual(code, 200)


class GlobalLockoutTests(unittest.TestCase):
    def test_many_addresses_cannot_lock_out_a_fresh_one(self):
        with tempfile.TemporaryDirectory() as d:
            a = Auth(Path(d) / "auth.json")
            for k in range(6):
                for _ in range(5):
                    a.record_fail(f"203.0.113.{k + 1}")
            self.assertTrue(a.allow_attempt("198.51.100.9"))
            r = a.begin_attempt("198.51.100.9")
            self.assertIsNotNone(r)
            self.assertGreater(r[1], 0)            # slowed down, not blocked
            self.assertIsNone(a.begin_attempt("203.0.113.1"))     # per-address limit still holds


class StatusLeakTests(_Srv):
    def test_stranger_sees_only_login_fields(self):
        self.auth.new_session()
        code, body = self.req("GET", "/api/auth", headers={"Cf-Connecting-IP": "203.0.113.9"})
        self.assertEqual(code, 200)
        self.assertEqual(set(json.loads(body)), {"password_set", "trusted", "logged_in"})
        full = json.loads(self.req("GET", "/api/auth")[1])
        self.assertEqual(full["sessions"], 1)


class SlowBodyTests(_Srv):
    def test_socket_timeout_and_small_unauthenticated_bodies(self):
        self.assertEqual(self.handler.timeout, 30)
        code, _ = self.req("POST", "/api/login", headers={"Cf-Connecting-IP": "203.0.113.9", "Content-Length": "20000"})
        self.assertEqual(code, 413)
        self.assertEqual(self.req("POST", "/api/stop", host="evil.example", headers={"Content-Length": "20000"})[0], 413)

    def test_quiet_client_is_dropped(self):
        self.handler.timeout = 0.3
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        s.sendall(b"POST /api/login HTTP/1.1\r\nHost: x\r\nX-Requested-With: lights\r\nContent-Length: 500\r\n\r\n")
        s.settimeout(5)
        self.assertEqual(s.recv(100), b"")             # server hung up instead of waiting forever
        s.close()


class _DiagApp:
    def __init__(self):
        self.cfg = type("C", (), {"data": {"server": {}}})()
        self.calls = []

    def handle(self, path, body):
        self.calls.append((path, body))
        return {"ok": True}


class DiagServerTests(_Srv):
    password = None

    def make(self):
        self.app = _DiagApp()
        return diag_server.make_handler(self.app)

    def test_diag_tool_is_home_only_and_csrf_guarded(self):
        self.assertEqual(self.req("POST", "/api/identify", body={"mac": "A"})[0], 200)
        self.assertEqual(self.req("POST", "/api/identify", body=b'{"mac":"B"}',
                                  headers={"X-Requested-With": "x", "Content-Type": "text/plain"})[0], 403)
        self.assertEqual(self.req("POST", "/api/identify", body={"mac": "C"},
                                  headers={"Cf-Connecting-IP": "203.0.113.9"})[0], 403)
        self.assertEqual(self.req("POST", "/api/identify", host="evil.attacker.example", body={"mac": "D"})[0], 403)
        self.assertEqual(self.req("GET", "/api/report", headers={"Cf-Connecting-IP": "203.0.113.9"})[0], 403)
        self.assertEqual(self.req("POST", "/api/identify", headers={"Content-Length": "1000001"})[0], 413)
        self.assertEqual(self.app.calls, [("identify", {"mac": "A"})])


if __name__ == "__main__":
    unittest.main()
