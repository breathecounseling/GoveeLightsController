"""Regression tests for the round-3 server fixes (request smuggling, API validation,
output start resilience, per-/64 rate limits)."""
import json
import socket
import tempfile
import threading
import time
import unittest
from datetime import datetime
from email.message import Message
from pathlib import Path

from goveelights import app_server
from goveelights.app_server import Server, make_handler, validate_schedule, validate_showtimes, validate_shows
from goveelights.auth import Auth
from goveelights.output import GoveeRazerOutput, MultiOutput


class _StubCtl:
    vcfg = {"enabled": True, "token": "goodtoken", "presets": []}

    def status(self):
        return {"ok": "status"}

    def visitor_state(self):
        return {"queue": []}

    def shows_list(self):
        raise TypeError("'NoneType' object is not iterable")


class _ServerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.auth = Auth(Path(self.tmp.name) / "auth.json")
        self.auth.set_password("a long enough password")
        self.srv = Server(("127.0.0.1", 0), make_handler(_StubCtl(), lambda: None, self.auth))
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def raw(self, data: bytes) -> bytes:
        s = socket.create_connection(("127.0.0.1", self.port), timeout=3)
        s.sendall(data)
        out = b""
        try:
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    break
                out += chunk
        except socket.timeout:
            pass
        s.close()
        return out


class SmugglingTests(_ServerCase):
    EMBED = (b"POST /api/auth/lan HTTP/1.1\r\nHost: x\r\nX-Requested-With: lights\r\n"
             b"Content-Type: application/json\r\nContent-Length: 13\r\n\r\n{\"on\":false}\n")

    def _get_with_body(self, path: bytes) -> bytes:
        return (b"GET " + path + b" HTTP/1.1\r\nHost: x\r\nCf-Connecting-IP: 203.0.113.9\r\nCf-Ray: abc\r\n"
                b"X-Forwarded-For: 203.0.113.9\r\nContent-Length: " + str(len(self.EMBED)).encode() +
                b"\r\n\r\n" + self.EMBED)

    def test_get_body_is_not_run_as_a_second_request(self):
        for path in (b"/api/status", b"/api/visit/bad/state", b"/api/visit/goodtoken/state"):
            out = self.raw(self._get_with_body(path))
            self.assertEqual(out.count(b"HTTP/1.1 "), 1, out)
            self.assertNotIn(b'{"ok": true}', out)
            self.auth.refresh()
            self.assertTrue(self.auth.data["lan_without_login"], path)

    def test_connection_that_carried_a_proxied_request_is_never_trusted(self):
        # a well-formed tunnelled GET, then a header-less POST pipelined on the same connection
        out = self.raw(b"GET /api/visit/goodtoken/state HTTP/1.1\r\nHost: x\r\nCf-Connecting-IP: 203.0.113.9\r\n\r\n"
                       + self.EMBED.replace(b"\r\n\r\n", b"\r\nConnection: close\r\n\r\n", 1))
        self.assertIn(b"401", out)
        self.auth.refresh()
        self.assertTrue(self.auth.data["lan_without_login"])

    def test_chunked_post_is_refused_and_closed(self):
        out = self.raw(b"POST /api/stop HTTP/1.1\r\nHost: x\r\nX-Requested-With: lights\r\n"
                       b"Transfer-Encoding: chunked\r\n\r\n0\r\n\r\n" + self.EMBED)
        self.assertIn(b" 411 ", out)
        self.assertEqual(out.count(b"HTTP/1.1 "), 1)

    def test_plain_keepalive_still_works(self):
        req = b"GET /api/status HTTP/1.1\r\nHost: x\r\n\r\n"
        out = self.raw(req + req.replace(b"\r\n\r\n", b"\r\nConnection: close\r\n\r\n"))
        self.assertEqual(out.count(b"HTTP/1.1 200"), 2)

    def test_unexpected_error_still_answers(self):
        out = self.raw(b"GET /api/shows HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
        self.assertIn(b"HTTP/1.1 500", out)


class ValidationTests(unittest.TestCase):
    def test_showtimes(self):
        good = [{"show": "Quick", "from": "17:00", "to": "22:00", "every": 60, "enabled": True, "days": [0, 6]},
                {"show": "NYE", "date": "12-31"}, {"show": "X", "dates": ["12-24", "12-25"]}]
        validate_showtimes(good)
        for bad in ([{"show": "Quick", "every": None}], [{"show": "Quick", "every": "abc"}], ["Quick"],
                    [{"show": "Quick", "days": "135"}], [{"show": "Quick", "date": 1231}],
                    [{"show": "Quick", "from": None}], [{"show": "Quick", "to": ""}], [{"every": 60}],
                    [{"show": "Quick", "every": 2}], {"show": "Quick"}):
            with self.assertRaises(ValueError, msg=bad):
                validate_showtimes(bad)

    def test_shows(self):
        validate_shows({"Mine": {"end": "resume", "cues": []}, "B": {"cues": [{"preset": "x", "seconds": 5}]}})
        for bad in ({"Bad": {"cues": None}}, {"Bad": {"cues": 7}}, {"Bad": None}, {"Bad": {"cues": ["x"]}}, []):
            with self.assertRaises(ValueError, msg=bad):
                validate_shows(bad)

    def test_schedule(self):
        base = {"enabled": True, "on": "sunset", "off_time": "23:00", "default": {"preset": "warm-white"}, "rules": []}
        validate_schedule(base)
        for bad in ({"on": "time", "on_time": "05:00", "off_time": ""}, {"off_time": None}, {"lat": "x"},
                    {"rules": None}):
            with self.assertRaises(ValueError, msg=bad):
                validate_schedule({**base, **bad})

    def test_api_refuses_and_keeps_old_value(self):
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from test_integration import SCH, Rig
        r = Rig(self, datetime(2026, 12, 24, 19, 0).astimezone(), {"schedule": dict(SCH)})
        h = object.__new__(make_handler(r.ctl, lambda: None, None))
        with self.assertRaises(ValueError):
            h._api("schedule", {"schedule": {"on": "time", "on_time": "05:00", "off_time": ""}})
        self.assertEqual(r.ctl.cfg.data["schedule"]["off_time"], "23:00")
        with self.assertRaises(ValueError):
            h._api("showtimes", {"value": [{"show": "Quick", "from": "00:00", "to": "23:59", "every": None}]})
        with self.assertRaises(ValueError):
            h._api("shows", {"value": {"Bad": {"cues": None}}})
        r.tick(30)                                  # the tick loop is still healthy
        self.assertTrue(h._api("schedule", {"schedule": {"off_time": "22:30"}})["ok"])


class _Lan:
    def __init__(self, bad):
        self.bad, self.sent = set(bad), []

    def send(self, ip, msg):
        if ip in self.bad:
            raise OSError(101, "Network is unreachable")
        self.sent.append(ip)

    def send_razer(self, ip, pkt):
        self.send(ip, pkt)


class _Streamer:
    def __init__(self, lan, ip):
        self.lan, self.ip, self.realtime = lan, ip, False

    def stop_anim(self):
        pass

    def set_keepalive(self, s):
        pass

    def enable(self, wanted=None):
        if wanted and not wanted():
            return False
        self.lan.send(self.ip, b"on")
        self.realtime = True
        return True

    def disable(self):
        self.lan.send(self.ip, b"off")
        self.realtime = False

    def send_frame(self, pixels, gradient=False):
        self.lan.send(self.ip, b"frame")


class OutputStartTests(unittest.TestCase):
    def test_one_unreachable_box_does_not_stop_the_others(self):
        lan = _Lan({"10.0.0.1"})
        devs = {"A": {"ip": "10.0.0.1"}, "B": {"ip": "10.0.0.2"}}
        g = GoveeRazerOutput(lan, devs, streamers={m: _Streamer(lan, d["ip"]) for m, d in devs.items()})
        g.retry_s = 0.0
        MultiOutput(g).start()                      # must not raise
        self.assertIn("10.0.0.2", lan.sent)
        self.assertTrue(g.streamers["B"].realtime)
        self.assertFalse(g.streamers["A"].realtime)
        lan.bad.clear()                             # the network comes back
        g.send_frame("A", [(1, 2, 3)])
        for _ in range(100):
            if g.streamers["A"].realtime:
                break
            time.sleep(0.01)
        self.assertTrue(g.streamers["A"].realtime)
        lan.bad.add("10.0.0.2")
        MultiOutput(g).stop()                       # must not raise either


class RateKeyTests(unittest.TestCase):
    def test_ipv6_64_is_one_client(self):
        self.assertEqual(Auth.rate_key("2001:db8:1:2::abcd"), Auth.rate_key("2001:db8:1:2:ffff::1"))
        self.assertNotEqual(Auth.rate_key("2001:db8:1:2::1"), Auth.rate_key("2001:db8:1:3::1"))
        self.assertEqual(Auth.rate_key("203.0.113.9"), "203.0.113.9")
        self.assertEqual(Auth.rate_key("::ffff:203.0.113.9"), "203.0.113.9")

    def test_login_lockout_by_64_and_fails_pruned(self):
        with tempfile.TemporaryDirectory() as d:
            a = Auth(Path(d) / "auth.json")
            for k in range(5):
                self.assertTrue(a.allow_attempt(f"2001:db8::{k + 1:x}"))
                a.record_fail(f"2001:db8::{k + 1:x}")
            self.assertFalse(a.allow_attempt("2001:db8::99"))
            self.assertEqual(len(a.fails), 1)
            for q in a.fails.values():               # ten minutes later the entries go away
                for i in range(len(q)):
                    q[i] -= 700
            a.all_fails.clear()
            self.assertTrue(a.allow_attempt("2001:db8::99"))
            for k in range(1000):
                a.allow_attempt(f"2001:db8:{k:x}::1")
            self.assertEqual(len(a.fails), 0)

    def test_visitor_client_key(self):
        class R:
            client_address = ("127.0.0.1", 1)
            headers = Message()
        R.headers["Cf-Connecting-IP"] = "2001:db8:5:6::77"
        self.assertEqual(Auth.rate_key(Auth.client_ip(R)), "2001:db8:5:6::/64")


if __name__ == "__main__":
    unittest.main()
