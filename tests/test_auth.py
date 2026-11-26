import tempfile
import unittest
from email.message import Message
from pathlib import Path

from goveelights.auth import COOKIE, Auth


class FakeReq:
    def __init__(self, ip, headers=None):
        self.client_address = (ip, 50000)
        self.headers = Message()
        for k, v in (headers or {}).items():
            self.headers[k] = v


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.a = Auth(Path(self.tmp.name) / "auth.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_trust_rules(self):
        a = self.a
        self.assertTrue(a.trusted(FakeReq("192.168.1.20")))
        self.assertTrue(a.trusted(FakeReq("127.0.0.1")))
        self.assertFalse(a.trusted(FakeReq("203.0.113.5")))
        # anything that came through a proxy/tunnel is never trusted, even from loopback
        self.assertFalse(a.trusted(FakeReq("127.0.0.1", {"Cf-Connecting-IP": "203.0.113.5"})))
        self.assertFalse(a.trusted(FakeReq("192.168.1.2", {"X-Forwarded-For": "1.2.3.4"})))
        a.data["lan_without_login"] = False
        self.assertFalse(a.trusted(FakeReq("192.168.1.20")))

    def test_password_and_sessions(self):
        a = self.a
        with self.assertRaises(ValueError):
            a.set_password("short")
        a.set_password("correct horse battery")
        self.assertTrue(a.check_password("correct horse battery"))
        self.assertFalse(a.check_password("correct horse batterY"))
        tok, _ = a.new_session()
        req = FakeReq("203.0.113.5", {"Cookie": f"x=1; {COOKIE}={tok}", "X-Forwarded-For": "203.0.113.5"})
        self.assertTrue(a.authorized(req))
        self.assertNotIn(tok, (Path(self.tmp.name) / "auth.json").read_text())   # only the hash is stored
        a.end_session(tok)
        self.assertFalse(a.authorized(req))
        tok2, _ = a.new_session()
        a.set_password("another long password")        # changing the password signs everyone out
        self.assertFalse(a.session_ok(tok2))

    def test_lockout(self):
        a = self.a
        for _ in range(5):
            self.assertTrue(a.allow_attempt("1.2.3.4"))
            a.record_fail("1.2.3.4")
        self.assertFalse(a.allow_attempt("1.2.3.4"))
        self.assertTrue(a.allow_attempt("5.6.7.8"))


if __name__ == "__main__":
    unittest.main()


class ClientIpTests(unittest.TestCase):
    def test_cf_header_only_trusted_from_tunnel(self):
        self.assertEqual(Auth.client_ip(FakeReq("127.0.0.1", {"Cf-Connecting-IP": "203.0.113.9"})), "203.0.113.9")
        self.assertEqual(Auth.client_ip(FakeReq("198.51.100.4", {"Cf-Connecting-IP": "1.2.3.4"})), "198.51.100.4")
        self.assertEqual(Auth.client_ip(FakeReq("127.0.0.1", {"Cf-Connecting-IP": "not-an-ip"})), "127.0.0.1")
