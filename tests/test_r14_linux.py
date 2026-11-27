"""Round 14: Linux / Raspberry Pi as a first-class home for the app.

- 'install-service' on Linux writes a systemd unit (rendered here, never installed);
- the frame-rate governor steps 20 -> 15 -> 12 fps on a slow computer and back up, and
  does nothing at all on a fast one;
- tools/bench.py runs;
- secrets copied over from Windows are made private; multicast on several interfaces."""
import io
import logging
import os
import socket
import stat
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).parent))

from goveelights import cli, lan  # noqa: E402
from goveelights import config as config_mod  # noqa: E402
from goveelights.engine import Engine, FrameGovernor  # noqa: E402
from goveelights.output import MemoryOutput  # noqa: E402


class Done(Exception):
    pass


class _Quiet:
    """The governor logs each step as a warning; keep the test output clean."""
    def setUp(self):
        lg = logging.getLogger("goveelights.engine")
        self.addCleanup(lg.setLevel, lg.level)
        lg.setLevel(logging.ERROR)


# ------------------------------------------------------------ systemd unit
class SystemdUnitTests(unittest.TestCase):
    def unit(self, **k):
        a = dict(user="pi", group="pi", root="/home/pi/GoveeLightsController", python="/usr/bin/python3")
        a.update(k)
        return cli.systemd_unit(**a)

    def lines(self, text):
        return [x for x in text.splitlines() if x and not x.startswith("#")]

    def test_the_unit_runs_as_the_user_in_the_project_folder_at_boot(self):
        L = self.lines(self.unit())
        for want in ("User=pi", "Group=pi", "WorkingDirectory=/home/pi/GoveeLightsController",
                     "ExecStart=/usr/bin/python3 -m goveelights serve", "Restart=always",
                     "After=network-online.target time-sync.target", "Wants=network-online.target",
                     "WantedBy=multi-user.target", "KillSignal=SIGTERM", "Type=simple"):
            self.assertIn(want, L)
        self.assertEqual([x for x in L if x.startswith("[")], ["[Unit]", "[Service]", "[Install]"])
        self.assertIn("StartLimitIntervalSec=0", L)         # never gives up restarting
        self.assertFalse(any("--log" in x for x in L))        # output goes to the journal

    def test_paths_with_spaces_are_quoted_in_execstart(self):
        u = self.unit(python="/home/pi/my lights/.venv/bin/python", root="/home/pi/my lights")
        self.assertIn('ExecStart="/home/pi/my lights/.venv/bin/python" -m goveelights serve', u)
        self.assertIn("WorkingDirectory=/home/pi/my lights", u)
        self.assertEqual(cli._sd_quote('a"b'), '"a\\"b"')

    def test_the_example_unit_matches_what_install_writes(self):
        ex = (ROOT / "docs" / "examples" / "goveelights.service").read_text(encoding="utf-8")
        self.assertEqual(self.lines(ex), self.lines(self.unit()))

    def test_plan_uses_sudo_unless_root(self):
        _, cmds = cli.linux_service_plan(False, "pi", "/p", "/usr/bin/python3", euid=1000)
        self.assertTrue(all(c[0] == "sudo" for c in cmds))
        self.assertEqual(cmds[0][-1], cli.UNIT_PATH)
        self.assertEqual([c[1:] for c in cmds[1:]], [["systemctl", "daemon-reload"], ["systemctl", "enable", "goveelights"],
                                                     ["systemctl", "restart", "goveelights"]])
        _, cmds = cli.linux_service_plan(True, "pi", "/p", "/usr/bin/python3", euid=0)
        self.assertEqual(cmds[0], ["systemctl", "disable", "--now", "goveelights"])
        self.assertIn(["rm", "-f", cli.UNIT_PATH], cmds)


class LinuxServiceCommandTests(unittest.TestCase):
    def call(self, uninstall=False, argv=(), euid=1000, systemd=True, rc=0, env=None):
        ran, out, seen = [], [], {}

        def run(c):
            ran.append(c)
            if c[1:2] == ["install"] or c[:1] == ["install"]:
                src = c[-2]
                seen["unit"] = Path(src).read_text(encoding="utf-8")
                seen["tmp"] = src
            return mock.Mock(returncode=rc(c) if callable(rc) else rc)
        with mock.patch.object(cli, "_has_systemd", return_value=systemd), \
                mock.patch.dict(os.environ, env or {}), \
                mock.patch("goveelights.diag_server.lan_ips", return_value=["192.0.2.10"]):
            r = cli.linux_service(uninstall, argv, run=run, euid=euid, write=out.append)
        return r, ran, "\n".join(out), seen

    def test_print_shows_the_unit_and_touches_nothing(self):
        r, ran, out, _ = self.call(argv=["--print", "--user", "pi"])
        self.assertEqual((r, ran), (0, []))
        self.assertIn("User=pi", out)
        self.assertIn("Would run:", out)
        self.assertIn("sudo systemctl enable goveelights", out)
        r, ran, out, _ = self.call(argv=["--dry-run", "--user", "pi"])
        self.assertEqual(ran, [])

    def test_install_writes_the_unit_through_sudo_and_says_how_to_see_logs(self):
        r, ran, out, seen = self.call(argv=["--user", "pi"])
        self.assertEqual(r, 0)
        self.assertEqual(len(ran), 4)
        self.assertIn("User=pi", seen["unit"])
        self.assertFalse(Path(seen["tmp"]).exists())          # the temp copy is cleaned up
        self.assertIn("journalctl -u goveelights -f", out)
        self.assertIn("http://192.0.2.10:", out)
        self.assertIn("+ sudo systemctl restart goveelights", out)

    def test_sudo_user_is_the_service_user(self):
        r, ran, out, seen = self.call(euid=0, env={"SUDO_USER": "alice"})
        self.assertIn("User=alice", seen["unit"])
        self.assertEqual(ran[0][0], "install")                 # already root: no sudo

    def test_refuses_to_run_the_service_as_root(self):
        with mock.patch.dict(os.environ, {"SUDO_USER": ""}), mock.patch("getpass.getuser", return_value="root"):
            with self.assertRaises(SystemExit) as e:
                cli.linux_service(False, [], run=lambda c: self.fail("ran"), euid=0, write=lambda s: None)
        self.assertIn("normal user", str(e.exception))

    def test_no_systemd(self):
        with self.assertRaises(SystemExit) as e:
            self.call(argv=["--user", "pi"], systemd=False)
        self.assertIn("start.sh", str(e.exception))

    def test_a_failing_step_stops_the_install(self):
        with self.assertRaises(SystemExit):
            self.call(argv=["--user", "pi"], rc=lambda c: 1 if "daemon-reload" in c else 0)

    def test_uninstall_carries_on_when_it_was_not_installed(self):
        r, ran, out, _ = self.call(uninstall=True, rc=lambda c: 5 if "disable" in c else 0)
        self.assertEqual(r, 0)
        self.assertEqual(len(ran), 3)
        self.assertIn("no longer starts at boot", out)

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux only")
    def test_the_command_line_reaches_it(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(cli.main(["install-service", "--print", "--user", "pi"]), 0)
        self.assertIn("[Service]", buf.getvalue())
        self.assertIn("install-service", cli.not_windows("install-startup"))

    def test_windows_still_uses_the_scheduled_task(self):
        with mock.patch.object(cli.os, "name", "nt"), \
                mock.patch("subprocess.run", side_effect=Done) as run, \
                mock.patch.object(cli, "linux_service", side_effect=AssertionError("linux path on Windows")):
            with self.assertRaises(Done):
                cli.cmd_service(uninstall=True)
        self.assertEqual(run.call_args[0][0][:2], ["schtasks", "/Delete"])


# ------------------------------------------------------------ governor
class GovernorTests(_Quiet, unittest.TestCase):
    def feed(self, g, cost_ms, seconds, t0=0.0, fps=20.0):
        t, out = t0, fps
        while t < t0 + seconds:
            out = g.update(cost_ms / 1000, t, fps)
            t += 1.0 / out
        return t, out

    def test_a_fast_machine_never_changes_anything(self):
        g = FrameGovernor()
        _, fps = self.feed(g, 8, 120)
        self.assertEqual((fps, g.changes, g.level), (20.0, 0, 0))
        self.assertFalse(g.state()["slowed"])

    def test_steps_down_then_back_up(self):
        g = FrameGovernor()
        t, fps = self.feed(g, 1, 1)
        self.assertEqual(fps, 20)
        t, fps = self.feed(g, 58, 2.5, t)                 # over 45 ms, but not for 3 s yet
        self.assertEqual(fps, 20)
        t, fps = self.feed(g, 58, 2, t)
        self.assertEqual(fps, 15)                          # 58 ms fits 15 fps (66 ms)
        st = g.state()
        self.assertTrue(st["slowed"])
        self.assertEqual((st["fps"], st["configured_fps"], st["budget_ms"]), (15.0, 20.0, 66.7))
        self.assertIn("50 ms", st["reason"])
        t, fps = self.feed(g, 80, 6, t)
        self.assertEqual(fps, 12)                          # the floor
        t, fps = self.feed(g, 200, 10, t)
        self.assertEqual(fps, 12)
        t, fps = self.feed(g, 35, 15, t)                   # cheap enough for 15 (< 40 ms) but not 20
        self.assertEqual(fps, 15)
        t, fps = self.feed(g, 35, 30, t)
        self.assertEqual(fps, 15)                          # no flapping: 35 ms is over 60% of 50
        t, fps = self.feed(g, 10, 12, t)
        self.assertEqual(fps, 20)
        self.assertEqual(g.changes, 4)
        self.assertFalse(g.state()["slowed"])

    def test_one_slow_frame_is_not_enough(self):
        g = FrameGovernor()
        t, _ = self.feed(g, 5, 2)
        for _ in range(3):
            g.update(0.5, t, 20)                           # a hiccup (garbage collection, a save)
            t += 0.05
        _, fps = self.feed(g, 5, 10, t)
        self.assertEqual((fps, g.changes), (20.0, 0))

    def test_ladders(self):
        g = FrameGovernor()
        g.fps_for(30)
        self.assertEqual(g.ladder, [30, 20, 15, 12])
        g.fps_for(10)
        self.assertEqual(g.ladder, [10])
        g.fps_for(15)
        self.assertEqual(g.ladder, [15, 12])

    def test_changing_the_frame_rate_starts_over(self):
        g = FrameGovernor()
        self.feed(g, 70, 10)
        self.assertGreater(g.level, 0)
        self.assertEqual(g.fps_for(25), 25)
        self.assertEqual(g.level, 0)

    def test_switched_off(self):
        g = FrameGovernor(enabled=False)
        _, fps = self.feed(g, 300, 20)
        self.assertEqual((fps, g.changes), (20.0, 0))
        self.assertFalse(g.state()["slowed"])


class _SlowScene:
    """A scene whose drawing takes `cost` seconds (changeable while it runs)."""
    def __init__(self, cost):
        self.cost = cost

    def render(self, t, layout, master=None, audio=None):
        time.sleep(self.cost)
        return [(0.1, 0.2, 0.3)] * layout.n

    def adopt_state(self, *a, **k):
        pass


class EngineGovernorTests(_Quiet, unittest.TestCase):
    def engine(self):
        from test_rack import house
        e = Engine(house(), MemoryOutput(), fps=20)
        e.governor = FrameGovernor(down_after=0.25, up_after=0.25)
        return e

    def wait_for(self, e, fps, timeout=5.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if e.governor.state()["fps"] == fps:
                return True
            time.sleep(0.02)
        return False

    def test_the_render_loop_slows_down_and_recovers(self):
        e = self.engine()
        sc = _SlowScene(0.07)                     # 70 ms a frame: too slow for 20 and 15 fps
        e.set_scene(sc)
        e.start(live=False)
        self.addCleanup(e.stop, False)
        self.assertTrue(self.wait_for(e, 12.0), e.governor.state())
        self.assertEqual(e.fps, 20)               # the setting itself is untouched
        sc.cost = 0.0                             # cheap again
        self.assertTrue(self.wait_for(e, 20.0), e.governor.state())

    def test_tick_is_untouched(self):
        """Frames come from tick(); the governor only paces the loop, so frames rendered
        for the same times are identical with it on, off or stepped down."""
        a, b = self.engine(), self.engine()
        b.governor.level = 2
        for e in (a, b):
            e.set_scene(_SlowScene(0))
            e.now = lambda: 1.5
            e.tick()
        self.assertEqual(a.last_colors, b.last_colors)


class StatusTests(unittest.TestCase):
    def test_status_reports_the_governor_and_settings_switch_it(self):
        from test_integration import Rig
        rig = Rig(self, datetime(2026, 12, 10, 18, 0))
        st = rig.ctl.status()
        self.assertEqual(st["governor"]["fps"], 20)
        self.assertTrue(st["governor"]["enabled"])
        rig.ctl.settings({"auto_fps": False})
        self.assertFalse(rig.ctl.status()["governor"]["enabled"])
        self.assertIs(rig.ctl.ecfg["auto_fps"], False)

    def test_the_page_shows_it(self):
        html = (ROOT / "goveelights" / "web" / "app.html").read_text(encoding="utf-8")
        self.assertIn('id="govEn"', html)
        self.assertIn("s.governor", html)


# ------------------------------------------------------------ bench
class BenchTests(unittest.TestCase):
    def test_bench_smoke(self):
        import bench
        out = []
        from test_rack import house
        items = bench.presets({"warm-white", "comets"})
        self.assertEqual([p for p, _ in items], ["comets", "warm-white"])
        rows = bench.run(house(), "test", items, 0.2, 2.5, 5.0, write=out.append)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["mean_ms"] > 0 and r["frames"] >= 2 for r in rows))
        text = "\n".join(out)
        self.assertIn("Pi 5 (x2.5)", text)
        self.assertIn("warm-white", text)

    def test_verdicts(self):
        import bench
        self.assertEqual(bench.verdict(5), "20 fps")
        self.assertEqual(bench.verdict(40), "20 fps (tight)")
        self.assertEqual(bench.verdict(55), "15 fps")
        self.assertEqual(bench.verdict(70), "12 fps")
        self.assertEqual(bench.verdict(90), "< 12 fps")

    def test_main_runs(self):
        import bench
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(bench.main(["--seconds", "0.1", "--only", "warm-white",
                                         "--layout", str(ROOT / "layouts" / "examples" / "gable-house.json")]), 0)
        self.assertIn("assume a Pi 5 is 2.5x", buf.getvalue())


# ------------------------------------------------------------ Linux details
@unittest.skipIf(os.name == "nt", "POSIX permissions")
class PrivateFilesTests(unittest.TestCase):
    def test_copied_secrets_become_owner_only(self):
        with tempfile.TemporaryDirectory() as d:
            for n, mode in (("govee_auth.json", 0o644), ("govee_cloud.json", 0o755), ("govee_config.json", 0o644)):
                (Path(d) / n).write_text("{}")
                os.chmod(Path(d) / n, mode)
            self.assertEqual(sorted(config_mod.make_private(Path(d))), ["govee_auth.json", "govee_cloud.json"])
            for n in ("govee_auth.json", "govee_cloud.json"):
                self.assertEqual(stat.S_IMODE((Path(d) / n).stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE((Path(d) / "govee_config.json").stat().st_mode), 0o644)
            self.assertEqual(config_mod.make_private(Path(d)), [])
            self.assertEqual(config_mod.make_private(Path(d) / "missing"), [])


class MulticastTests(unittest.TestCase):
    def fake_socket(self, calls):
        class S:
            def setsockopt(self, *a):
                calls.append(("setsockopt", *a))

            def bind(self, addr):
                calls.append(("bind", addr))

            def sendto(self, data, addr):
                calls.append(("sendto", addr))

            def ioctl(self, *a):
                calls.append(("ioctl", *a))
        return S()

    def open(self, ifaces, bind_ip=""):
        calls = []
        with mock.patch.object(lan.os, "name", "posix"), mock.patch.object(lan, "local_ipv4s", return_value=ifaces), \
                mock.patch.object(lan.socket, "socket", lambda *a: self.fake_socket(calls)):
            g = object.__new__(lan.GoveeLan)
            g.bind_ip = bind_ip
            g.sock = g._open()
            g.scan()
        return g, calls

    def test_joins_and_scans_on_every_interface(self):
        g, calls = self.open(["192.0.2.5", "198.51.100.7"])
        joins = [c for c in calls if c[:3] == ("setsockopt", socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP)]
        self.assertEqual(len(joins), 3)                    # "any", then each interface
        self.assertEqual(g._mcast_ifaces, ["192.0.2.5", "198.51.100.7"])
        ifs = [c[3] for c in calls if c[:3] == ("setsockopt", socket.IPPROTO_IP, socket.IP_MULTICAST_IF)]
        self.assertEqual(ifs, [socket.inet_aton("192.0.2.5"), socket.inet_aton("198.51.100.7"), socket.inet_aton("0.0.0.0")])
        self.assertEqual(len([c for c in calls if c[0] == "sendto"]), 3)

    def test_one_interface_is_as_before(self):
        g, calls = self.open(["192.0.2.5"])
        self.assertEqual(g._mcast_ifaces, [])
        self.assertFalse([c for c in calls if c[:3] == ("setsockopt", socket.IPPROTO_IP, socket.IP_MULTICAST_IF)])
        self.assertEqual(len([c for c in calls if c[0] == "sendto"]), 1)

    def test_bind_ip_wins(self):
        g, calls = self.open(["192.0.2.5", "198.51.100.7"], bind_ip="198.51.100.7")
        self.assertEqual(g._mcast_ifaces, [])
        ifs = [c[3] for c in calls if c[:3] == ("setsockopt", socket.IPPROTO_IP, socket.IP_MULTICAST_IF)]
        self.assertEqual(ifs, [socket.inet_aton("198.51.100.7")])

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux only")
    def test_local_addresses(self):
        ips = lan.local_ipv4s()
        self.assertTrue(all(not ip.startswith("127.") for ip in ips))
        self.assertEqual(len(ips), len(set(ips)))

    def test_not_linux_is_empty(self):
        with mock.patch.object(lan.sys, "platform", "darwin"):
            self.assertEqual(lan.local_ipv4s(), [])


class StartScriptTests(unittest.TestCase):
    def test_start_sh(self):
        f = ROOT / "start.sh"
        text = f.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("#!/bin/sh\n"))
        self.assertNotIn("\r", text)                       # CRLF would break it on Linux
        self.assertIn('-m goveelights "$@"', text)
        if os.name != "nt":
            self.assertTrue(f.stat().st_mode & stat.S_IXUSR)


if __name__ == "__main__":
    unittest.main()
