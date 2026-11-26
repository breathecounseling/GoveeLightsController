"""Windows-only behaviour, checked with fakes so it runs anywhere."""
import ctypes
import json
import socket
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

from goveelights import app_server, cli, config, diag_server, lan, winconsole
from goveelights.controller import PresetStore, slug
from goveelights.engine import Engine
from goveelights.layout import Layout, Run
from goveelights.output import MemoryOutput
from goveelights.scene import Scene


class ClockTests(unittest.TestCase):
    """Before 3.13, Windows' monotonic() and time() tick every 15.6 ms: the engine must
    not take animation time or overlay fades from them."""

    def test_engine_time_is_fine_grained_with_coarse_system_clocks(self):
        e = Engine(Layout([Run("a", "A", 4)]), MemoryOutput())
        with mock.patch("time.monotonic", return_value=1000.0), mock.patch("time.time", return_value=5e8):
            t1, w1 = e.now(), e.wall()
            end = time.perf_counter() + 0.003
            while time.perf_counter() < end:
                pass
            t2, w2 = e.now(), e.wall()
        self.assertGreater(t2 - t1, 0.002)
        self.assertGreater(w2 - w1, 0.002)
        self.assertLess(abs(w2 - 5e8), 0.25)

    def test_wall_reanchors_when_the_clock_is_set(self):
        e = Engine(Layout([Run("a", "A", 4)]), MemoryOutput())
        with mock.patch("time.time", return_value=time.time() + 3600):
            self.assertAlmostEqual(e.wall(), time.time(), delta=0.01)

    def test_overlay_decay_uses_the_fine_clock(self):
        e = Engine(Layout([Run("a", "A", 4)]), MemoryOutput())
        sc = Scene.from_dict({"layers": [{"effect": "solid", "params": {"color": "#ffffff"}}]})
        now = time.time()
        e.overlays["pad:x"] = (sc, "add", 1.0, now + 60, now - 1.0, 1.0)
        with mock.patch.object(e, "wall", return_value=now):
            v = e.render(0.0)[0][0]
        self.assertAlmostEqual(v, 0.3679, places=3)          # exp(-1)


class FakeSock:
    def __init__(self, items, owner):
        self.items, self.owner = list(items), owner

    def recvfrom(self, n):
        if not self.items:
            self.owner._closed = True
            raise OSError("closed")
        it = self.items.pop(0)
        if isinstance(it, BaseException):
            raise it
        return it, ("10.0.0.9", 4003)


def bare_lan(items, on_device=None):
    g = object.__new__(lan.GoveeLan)
    g.on_device, g._status, g._status_ev, g._closed = on_device, {}, {}, False
    g._lock = __import__("threading").Lock()
    g.sock = FakeSock(items, g)
    return g


def dg(msg):
    return json.dumps({"msg": msg}).encode()


class LanTests(unittest.TestCase):
    def test_connreset_does_not_throttle_the_receive_loop(self):
        got = []
        g = bare_lan([ConnectionResetError(10054)] * 50
                     + [dg({"cmd": "scan", "data": {"device": "AA", "ip": "1.2.3.4"}})], got.append)
        with mock.patch.object(lan.time, "sleep") as sl:
            g._rx_loop()
        self.assertEqual([d["device"] for d in got], ["AA"])
        sl.assert_not_called()

    def test_malformed_datagrams_never_kill_the_receive_loop(self):
        got = []
        bad = [dg("hello"), dg(["x"]), dg({"cmd": "scan", "data": ["device"]}),
               dg({"cmd": "scan", "data": "device"}), dg({"cmd": "devStatus", "data": [1, 2]}),
               dg({"cmd": "devStatus", "data": "on"}), b"\xff\xfe", b"[]", b"7", dg(None)]
        g = bare_lan(bad + [dg({"cmd": "scan", "data": {"device": "BB"}}),
                            dg({"cmd": "devStatus", "data": {"onOff": 1}})], got.append)
        g._rx_loop()
        self.assertEqual([d["device"] for d in got], ["BB"])
        self.assertEqual(g._status["10.0.0.9"]["onOff"], 1)

    def test_on_device_errors_are_contained(self):
        def boom(d):
            raise AttributeError("x")
        g = bare_lan([dg({"cmd": "scan", "data": {"device": "CC"}}),
                      dg({"cmd": "devStatus", "data": {"onOff": 0}})], boom)
        with self.assertLogs("goveelights.lan", "ERROR"):
            g._rx_loop()
        self.assertIn("10.0.0.9", g._status)

    def test_windows_socket_is_exclusive_and_ignores_connreset(self):
        calls = []

        class S:
            def setsockopt(self, *a):
                calls.append(("setsockopt",) + a)

            def ioctl(self, *a):
                calls.append(("ioctl",) + a)

            def bind(self, addr):
                calls.append(("bind", addr))

        with mock.patch.object(lan, "os", types.SimpleNamespace(name="nt")), \
                mock.patch.object(lan.socket, "socket", lambda *a: S()):
            g = object.__new__(lan.GoveeLan)
            g.bind_ip = ""
            g._open()
        opts = [c for c in calls if c[0] == "setsockopt" and c[1] == socket.SOL_SOCKET]
        self.assertEqual(opts, [("setsockopt", socket.SOL_SOCKET, getattr(socket, "SO_EXCLUSIVEADDRUSE", -5), 1)])
        self.assertIn(("ioctl", getattr(socket, "SIO_UDP_CONNRESET", 0x9800000C), False), calls)
        self.assertLess(calls.index(opts[0]), [c[0] for c in calls].index("bind"))


class ServerTests(unittest.TestCase):
    def test_both_servers_refuse_port_sharing_on_windows(self):
        self.assertIs(app_server.Server, diag_server.Server)
        self.assertTrue(diag_server.Server.daemon_threads)
        src = Path(diag_server.__file__).read_text(encoding="utf-8")
        self.assertIn('allow_reuse_address = os.name != "nt"', src)
        self.assertNotIn("ThreadingHTTPServer((", src)          # diag uses Server too

    def test_log_file_is_utf8(self):
        seen = {}

        def fake(**kw):
            seen.update(kw)
            raise SystemExit("stop")
        with mock.patch.object(app_server.logging, "basicConfig", fake), self.assertRaises(SystemExit):
            app_server.main(["--log", "x.log"])
        self.assertEqual(seen.get("encoding"), "utf-8")


class FakeKernel32:
    def __init__(self, window=1, mode=0x1F7):
        self.window, self.mode, self.handlers, self.set_mode = window, mode, [], None

    def GetConsoleWindow(self):
        return self.window

    def SetConsoleCtrlHandler(self, cb, add):
        self.handlers.append(cb)
        return 1

    def GetStdHandle(self, n):
        return 7 if n == -10 else 0

    def GetConsoleMode(self, h, ref):
        if self.mode is None:
            return 0
        ref._obj.value = self.mode
        return 1

    def SetConsoleMode(self, h, mode):
        self.set_mode = mode
        return 1


class ConsoleTests(unittest.TestCase):
    def test_close_logoff_shutdown_run_cleanup_before_returning(self):
        k, ran = FakeKernel32(), []
        self.assertTrue(winconsole.on_console_close(lambda: ran.append(1), kernel32=k))
        cb = k.handlers[0]
        for ev in (2, 5, 6):
            self.assertTrue(cb(ev))
        self.assertEqual(len(ran), 3)
        self.assertFalse(cb(0))            # Ctrl+C stays with Python's signal handler
        self.assertFalse(cb(1))
        self.assertEqual(len(ran), 3)

    def test_cleanup_errors_still_return_handled(self):
        k = FakeKernel32()
        winconsole.on_console_close(lambda: 1 / 0, kernel32=k)
        with self.assertLogs("goveelights.winconsole", "ERROR"):
            self.assertTrue(k.handlers[0](2))

    def test_no_console_window_no_handler(self):
        k = FakeKernel32(window=0)
        self.assertFalse(winconsole.on_console_close(lambda: None, kernel32=k))
        self.assertEqual(k.handlers, [])

    def test_noop_off_windows(self):
        if winconsole.os.name != "nt":
            self.assertFalse(winconsole.on_console_close(lambda: None))
            self.assertFalse(winconsole.disable_quickedit())
            self.assertFalse(winconsole.fine_sleep())

    def test_quickedit_cleared(self):
        k = FakeKernel32(mode=0x1F7)
        self.assertTrue(winconsole.disable_quickedit(kernel32=k))
        self.assertEqual(k.set_mode & 0x40, 0)
        self.assertEqual(k.set_mode & 0x80, 0x80)
        self.assertEqual(k.set_mode | 0x40, 0x1F7 | 0x80)
        k = FakeKernel32(mode=None)                 # pythonw / redirected stdin
        self.assertFalse(winconsole.disable_quickedit(kernel32=k))
        self.assertIsNone(k.set_mode)

    def test_fine_sleep_asks_for_1ms(self):
        w = mock.Mock()
        w.timeBeginPeriod.return_value = 0
        self.assertTrue(winconsole.fine_sleep(winmm=w))
        w.timeBeginPeriod.assert_called_once_with(1)
        self.assertTrue(ctypes)


class SaveTests(unittest.TestCase):
    def test_replace_retries_on_permission_error(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.json"
            (Path(d) / "c.tmp").write_text("stale")        # an old fixed-name temp file is irrelevant
            real, n = config.os.replace, []

            def flaky(a, b):
                n.append(1)
                if len(n) < 3:
                    raise PermissionError(32, "in use")
                real(a, b)
            with mock.patch.object(config.os, "replace", flaky), mock.patch.object(config.time, "sleep"):
                config.write_json(p, {"a": 1})
            self.assertEqual(json.loads(p.read_text(encoding="utf-8")), {"a": 1})
            self.assertEqual(len(n), 3)
            self.assertEqual(sorted(f.name for f in Path(d).iterdir()), ["c.json", "c.tmp"])

    def test_gives_up_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.json"
            with mock.patch.object(config.os, "replace", side_effect=PermissionError(5, "denied")), \
                    mock.patch.object(config.time, "sleep"), self.assertRaises(PermissionError):
                config.write_json(p, {"a": 1})
            self.assertEqual(list(Path(d).iterdir()), [])

    def test_identical_scan_replies_do_not_rewrite_the_config(self):
        with tempfile.TemporaryDirectory() as d:
            c = config.Config(Path(d) / "c.json")
            info = {"device": "AA:BB", "sku": "H705A", "ip": "10.0.0.5", "source_ip": "10.0.0.5"}
            with mock.patch.object(c, "save") as sv:
                self.assertTrue(c.upsert_discovered(dict(info)))
                self.assertFalse(c.upsert_discovered(dict(info)))
                self.assertEqual(sv.call_count, 1)
                c.upsert_discovered({**info, "source_ip": "10.0.0.6"})
                self.assertEqual(sv.call_count, 2)

    def test_auth_file_saved_owner_only(self):
        from goveelights.auth import Auth
        with tempfile.TemporaryDirectory() as d:
            a = Auth(Path(d) / "auth.json")
            a.set_password("correct horse battery")
            import os
            if os.name != "nt":
                self.assertEqual((Path(d) / "auth.json").stat().st_mode & 0o777, 0o600)
            self.assertEqual([f.name for f in Path(d).iterdir()], ["auth.json"])


class InstallTests(unittest.TestCase):
    def test_vbs_launcher_has_a_bom(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "José" / "GoveeLights.vbs"
            f.parent.mkdir()
            with mock.patch.object(cli, "_startup_file", return_value=f), mock.patch("builtins.print"):
                cli.cmd_install_startup()
            raw = f.read_bytes()
            self.assertEqual(raw[:2], b"\xff\xfe")
            self.assertIn("CurrentDirectory", raw.decode("utf-16"))

    def test_firewall_rules_for_the_boot_task(self):
        cmds = cli.firewall_cmds(8123)
        adds = [c for c in cmds if "add" in c]
        self.assertEqual(len(adds), 2)
        self.assertTrue(all("profile=private" in c and "dir=in" in c for c in adds))
        self.assertIn("localport=4002", adds[0])
        self.assertIn("protocol=UDP", adds[0])
        self.assertIn("localport=8123", adds[1])
        self.assertIn("protocol=TCP", adds[1])
        self.assertLess(cmds.index(adds[0]), len(cmds))
        self.assertTrue(all("delete" in c for c in cli.firewall_cmds(0, add=False)))


class ReservedNameTests(unittest.TestCase):
    def test_device_names_are_not_preset_ids(self):
        for n in ("Con", "AUX", "nul", "Prn", "com1", "LPT9"):
            self.assertTrue(slug(n).endswith("-preset"), n)
        self.assertEqual(slug("Console"), "console")
        self.assertEqual(slug("com10"), "com10")
        with tempfile.TemporaryDirectory() as d:
            st = PresetStore(Path(d))
            pid = st.save({"name": "Con", "layers": [{"effect": "solid"}]})
            self.assertEqual(pid, "con-preset")
            with self.assertRaises(ValueError):
                st.get("con")
            with self.assertRaises(ValueError):
                st.save({"name": "x", "layers": [{"effect": "solid"}]}, "nul")


class PreviewToolTests(unittest.TestCase):
    def test_paths_and_encoding_are_portable(self):
        src = Path("tools/preview.py").read_text(encoding="utf-8")
        self.assertNotIn('"/tmp/preview"', src)
        self.assertNotIn("file://", src)
        self.assertIn("as_uri()", src)
        self.assertIn('write_text(html, encoding="utf-8")', src)
        self.assertIn('read_text(encoding="utf-8")', src)


if __name__ == "__main__":
    unittest.main()
