"""Per-run colour and brightness balance ("Match the runs"): the calibration maths at the
output stage, byte-identical output with the defaults (tonight's Thanksgiving looks
included), the nudges, the config round trip, the live test colour that takes over the
lights safely and gives back what was playing, the HTTP endpoints and the render cost."""
import copy
import json
import math
import random
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from test_integration import Rig  # noqa: E402
from test_rack import house  # noqa: E402

from goveelights import calibrate  # noqa: E402
from goveelights.config import Config  # noqa: E402
from goveelights.engine import Engine, to_device_frames  # noqa: E402
from goveelights.layout import Layout  # noqa: E402
from goveelights.output import MemoryOutput  # noqa: E402
from goveelights.scene import Scene  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OWNER_LAYOUT = ROOT / "layouts" / "house.json"
TONIGHT = ["turkey-fan", "thanksgiving-harvest", "harvest-sunset", "gratitude-candlelight", "text-give-thanks",
           "warm-white"]


def old_to_device_frames(layout, colors, gamma=1.0, dither=None):
    """to_device_frames exactly as it was before calibration (the reference)."""
    frames = {r.mac: [(0, 0, 0)] * r.count for r in layout.runs}
    trims = {r.mac: getattr(r, "trim", None) for r in layout.runs}
    for k, (p, c) in enumerate(zip(layout.pixels, colors)):
        tr = trims[p.mac]
        vals = (c[0] * tr[0], c[1] * tr[1], c[2] * tr[2]) if tr else c
        out = []
        for ch in range(3):
            v = min(1.0, max(0.0, vals[ch]))
            if gamma != 1.0:
                v = v ** gamma
            v *= 255.0
            if dither is not None and v < 0.5:
                dither[k][ch] = 0.0
                out.append(0)
                continue
            if dither is not None and v < 48.0:
                v += dither[k][ch]
                q = int(min(255, max(0, round(v))))
                dither[k][ch] = max(-0.5, min(0.5, v - q))
            else:
                q = int(v + 0.5)
                if dither is not None:
                    dither[k][ch] = 0.0
            out.append(q)
        frames[p.mac][p.local] = (out[0], out[1], out[2])
    return frames


def owner_cfg(test, extra=None) -> Config:
    import shutil
    import tempfile
    d = tempfile.mkdtemp()
    test.addCleanup(shutil.rmtree, d, True)
    c = Config(Path(d) / "govee_config.json")
    src = OWNER_LAYOUT if OWNER_LAYOUT.exists() else ROOT / "layouts" / "examples" / "gable-house.json"
    c.import_layout(src, create_missing=True)
    for mac, d in c.devices.items():
        d.update(copy.deepcopy((extra or {}).get(d.get("name"), {})))
    return c


# ================================================================ identity
class DefaultsChangeNothing(unittest.TestCase):
    def test_untouched_runs_have_no_gains(self):
        L = Layout.from_config(owner_cfg(self))
        self.assertEqual(calibrate.pixel_gains(L), (None, None))
        for r in L.runs:
            self.assertEqual((r.trim, r.bright, r.rgamma, r.fade), (None, 1.0, 1.0, 0.0))

    def test_identity_calibration_is_byte_identical_for_tonights_looks(self):
        """Every built-in preset (tonight's first) through the output stage, with the global
        gamma and the dither on and off: bytes equal to the pre-calibration code, with no
        calibration, with an explicit identity calibration, and with an old colour trim."""
        ident = {"calibration": dict(calibrate.IDENTITY)}
        plain = Layout.from_config(owner_cfg(self))
        explicit = Layout.from_config(owner_cfg(self, {"Eaves": ident, "Peak": ident, "Accent": ident}))
        trimmed = Layout.from_config(owner_cfg(self, {"Peak": {"trim": [1.0, 0.93, 0.81]}}))
        self.assertEqual(calibrate.pixel_gains(explicit), (None, None))
        pids = TONIGHT + sorted(p.stem for p in (ROOT / "presets").glob("*.json") if p.stem not in TONIGHT)
        for pid in pids:
            sc = Scene.from_dict(json.loads((ROOT / "presets" / f"{pid}.json").read_text()))
            for L in (plain, explicit, trimmed):
                bufs = [[[0.0] * 3 for _ in L.pixels] for _ in range(2)]
                for k in range(4):
                    cols = sc.render(k / 5, L)
                    for gamma in (1.0, 2.2):
                        self.assertEqual(to_device_frames(L, cols, gamma), old_to_device_frames(L, cols, gamma), pid)
                    self.assertEqual(to_device_frames(L, cols, 1.0, bufs[0]),
                                     old_to_device_frames(L, cols, 1.0, bufs[1]), pid)

    def test_engine_tick_output_unchanged_with_global_brightness_and_dim(self):
        L = Layout.from_config(owner_cfg(self))
        out = MemoryOutput()
        e = Engine(L, out, brightness=0.7)
        e.dim = 0.6
        e.set_scene(Scene.from_dict(json.loads((ROOT / "presets" / "turkey-fan.json").read_text())))
        e.live = True
        e.tick()
        cols = [(c[0], c[1], c[2]) for c in e.last_colors]
        buf = [[0.0] * 3 for _ in L.pixels]
        self.assertEqual(out.frames, old_to_device_frames(L, cols, 1.0, buf))


# ================================================================ maths
class CalibrationMaths(unittest.TestCase):
    def setUp(self):
        self.L = house()
        self.r = self.L.runs[0]
        self.white = [(1.0, 1.0, 1.0)] * self.L.n

    def test_brightness_and_gain(self):
        calibrate.apply_to_run(self.r, {"brightness": 0.5, "gain": [1.0, 0.8, 0.6], "gamma": 1.0, "fade": 0.0})
        f = to_device_frames(self.L, self.white)
        px = self.r.pixels[0]
        self.assertEqual(f[self.r.mac][px.local], (128, 102, 77))
        other = self.L.runs[1]
        self.assertEqual(f[other.mac][other.pixels[0].local], (255, 255, 255))

    def test_run_gamma_only_moves_mid_tones(self):
        calibrate.apply_to_run(self.r, {"brightness": 1.0, "gain": [1, 1, 1], "gamma": 1.5, "fade": 0.0})
        f = to_device_frames(self.L, [(0.5, 1.0, 0.0)] * self.L.n)
        self.assertEqual(f[self.r.mac][self.r.pixels[0].local], (round(255 * 0.5 ** 1.5), 255, 0))
        f = to_device_frames(self.L, [(0.5, 1.0, 0.0)] * self.L.n, gamma=2.0)
        self.assertEqual(f[self.r.mac][self.r.pixels[0].local][0], int(255 * (0.25 ** 1.5) + 0.5))

    def test_far_end_fade_is_linear_along_the_wire(self):
        calibrate.apply_to_run(self.r, {"brightness": 1.0, "gain": [1, 1, 1], "gamma": 1.0, "fade": 0.2})
        f = to_device_frames(self.L, self.white)[self.r.mac]
        lit = sorted(p.local for p in self.r.pixels)
        first, last = lit[0], lit[-1]
        self.assertEqual(f[first][0], int(255 * (1 - 0.2 * (1 - first / (self.r.count - 1))) + 0.5))
        self.assertEqual(f[last][0], int(255 * (1 - 0.2 * (1 - last / (self.r.count - 1))) + 0.5))
        vals = [f[i][0] for i in lit]
        self.assertEqual(vals, sorted(vals))                  # brighter towards the far end, never darker

    def test_cache_follows_changes(self):
        self.assertEqual(calibrate.pixel_gains(self.L), (None, None))
        self.r.bright = 0.5
        self.assertIsNotNone(calibrate.pixel_gains(self.L)[0])
        self.r.bright = 1.0
        self.assertEqual(calibrate.pixel_gains(self.L), (None, None))

    def test_never_brighter_than_full(self):
        for c in ({"brightness": 7, "gain": [3, 3, 3], "gamma": 1, "fade": 0},):
            dev = {}
            w = calibrate.write(dev, c)
            self.assertEqual((w["brightness"], w["gain"]), (1.0, [1.0, 1.0, 1.0]))
            self.assertNotIn("calibration", dev)


class ConfigRoundTrip(unittest.TestCase):
    def test_identity_is_not_stored_and_trim_is_folded(self):
        dev = {"trim": [1.0, 0.9, 0.8]}
        self.assertEqual(calibrate.read(dev)["gain"], [1.0, 0.9, 0.8])
        calibrate.write(dev, dict(calibrate.read(dev), brightness=0.9))
        self.assertNotIn("trim", dev)
        self.assertEqual(dev["calibration"], {"brightness": 0.9, "gain": [1.0, 0.9, 0.8], "gamma": 1.0, "fade": 0.0})
        calibrate.write(dev, dict(calibrate.IDENTITY))
        self.assertEqual(dev, {})

    def test_bad_values(self):
        self.assertEqual(calibrate.read({"calibration": {"brightness": "x"}}), dict(calibrate.IDENTITY))
        with self.assertRaises(ValueError):
            calibrate.write({}, {"brightness": float("nan")})
        with self.assertRaises(ValueError):
            calibrate.write({}, {"gain": [1, 1]})
        self.assertEqual(calibrate.read({"calibration": {"fade": 9, "gamma": 0.1}})["fade"], 0.4)

    def test_layout_reads_it(self):
        cfg = owner_cfg(self, {"Peak": {"calibration": {"brightness": 0.8, "gain": [1, 0.9, 0.7], "fade": 0.1}}})
        r = Layout.from_config(cfg).run("Peak")
        self.assertEqual((r.trim, r.bright, r.rgamma, r.fade), ((1.0, 0.9, 0.7), 0.8, 1.0, 0.1))


class Nudges(unittest.TestCase):
    def cals(self):
        return {m: dict(calibrate.IDENTITY, gain=[1.0, 1.0, 1.0]) for m in ("a", "b", "c")}

    def test_warmer_then_cooler_comes_back(self):
        c = self.cals()
        w = calibrate.nudge(c, "a", "warmer")["a"]
        self.assertLess(w["gain"][2], 1.0)
        self.assertLess(w["gain"][1], 1.0)
        self.assertEqual(w["gain"][0], 1.0)
        c["a"] = w
        back = calibrate.nudge(c, "a", "cooler")["a"]
        self.assertEqual(back["gain"], [1.0, 1.0, 1.0])
        c["a"] = back
        cool = calibrate.nudge(c, "a", "cooler")["a"]
        self.assertLess(cool["gain"][0], 1.0)
        self.assertEqual(cool["gain"][2], 1.0)

    def test_brighter_at_full_dims_the_others(self):
        c = self.cals()
        ch = calibrate.nudge(c, "a", "brighter")
        self.assertEqual(set(ch), {"b", "c"})
        self.assertEqual(ch["b"]["brightness"], 0.95)
        c.update(ch)
        c["b"] = dict(c["b"])
        ch = calibrate.nudge(c, "b", "brighter")
        self.assertEqual(ch, {"b": dict(c["b"], brightness=1.0)})

    def test_limits(self):
        c = self.cals()
        for _ in range(40):
            c.update(calibrate.nudge(c, "a", "dimmer"))
            c.update(calibrate.nudge(c, "a", "warmer"))
            c.update(calibrate.nudge(c, "a", "even"))
        self.assertEqual(c["a"]["brightness"], calibrate.BRIGHT[0])
        self.assertGreaterEqual(min(c["a"]["gain"]), calibrate.GAIN[0])
        self.assertEqual(c["a"]["fade"], calibrate.FADE[1])
        with self.assertRaises(ValueError):
            calibrate.nudge(c, "a", "sideways")
        with self.assertRaises(ValueError):
            calibrate.nudge(c, "zz", "warmer")


# ================================================================ render cost
class RenderCost(unittest.TestCase):
    def test_negligible_at_20_fps(self):
        L = Layout.from_config(owner_cfg(self))
        rnd = random.Random(4)
        frames = [[(rnd.random(), rnd.random(), rnd.random()) for _ in L.pixels] for _ in range(20)]

        def cost():
            buf = [[0.0] * 3 for _ in L.pixels]
            t0 = time.perf_counter()
            for _ in range(3):
                for cols in frames:
                    to_device_frames(L, cols, 1.0, buf)
            return (time.perf_counter() - t0) / 60 * 1000

        base = min(cost() for _ in range(3))
        for i, r in enumerate(L.runs):
            calibrate.apply_to_run(r, {"brightness": 0.9, "gain": [1, 0.95, 0.85], "gamma": 1.1 if i == 1 else 1.0,
                                       "fade": 0.1})
        cal = min(cost() for _ in range(3))
        # 20 fps leaves 50 ms a frame: calibration must stay a small part of it
        self.assertLess(cal - base, 2.5, (base, cal))
        self.assertLess(cal, 10.0, (base, cal))


# ================================================================ controller
class MatchTheRuns(unittest.TestCase):
    def rig(self, sch=None):
        r = Rig(self, datetime(2026, 11, 26, 14, 0).astimezone(), {"schedule": sch or {"enabled": False}})
        return r, r.ctl

    def test_calibrate_applies_at_once_and_is_saved(self):
        r, ctl = self.rig()
        mac = ctl.engine.layout.runs[1].mac
        cal = ctl.calibrate({"mac": mac, "nudge": "warmer"})
        self.assertLess(cal[mac]["gain"][2], 1.0)
        self.assertEqual(ctl.engine.layout.run(mac).trim, tuple(cal[mac]["gain"]))
        saved = json.loads(Path(ctl.cfg.path).read_text())["devices"][mac]["calibration"]
        self.assertEqual(saved["gain"], cal[mac]["gain"])
        ctl.calibrate({"mac": mac, "brightness": 0.8, "fade": 0.15})
        ctl.build_engine()                                       # survives a rebuild (and a restart)
        run = ctl.engine.layout.run(mac)
        self.assertEqual((run.bright, run.fade), (0.8, 0.15))
        self.assertIn("cal", ctl.status()["runs"][1])
        ctl.calibrate({"reset": "all"})
        self.assertNotIn("calibration", ctl.cfg.device(mac))
        self.assertEqual(calibrate.pixel_gains(ctl.engine.layout), (None, None))
        with self.assertRaises(ValueError):
            ctl.calibrate({"mac": "nope", "nudge": "warmer"})

    def test_test_colour_over_a_playing_look_gives_it_back(self):
        r, ctl = self.rig()
        ctl.play_preset("warm-white")
        ctl.set_live(True)
        e = ctl.engine
        scene, playing = e.scene, dict(ctl.playing)
        res = ctl.match("start", "red")
        self.assertEqual((res["on"], res["took_lights"]), (True, False))
        cols = e.render(e.now())
        self.assertTrue(all(c == (1.0, 0.0, 0.0) for c in cols))
        ctl.match("color", "blue")
        self.assertTrue(all(c == (0.0, 0.0, 1.0) for c in e.render(e.now())))
        self.assertEqual(ctl.status()["match"], {"color": "blue"})
        ctl.match("stop")
        self.assertIs(e.scene, scene)
        self.assertEqual(ctl.playing, playing)
        self.assertNotIn("match", e.overlays)
        self.assertTrue(e.live)
        self.assertIsNone(ctl.status()["match"])

    def test_lights_off_go_on_for_the_test_and_off_after(self):
        r, ctl = self.rig()
        e = ctl.engine
        self.assertFalse(e.live)
        self.assertTrue(ctl.match("start", "warm")["took_lights"])
        self.assertTrue(e.live)
        ctl.match("stop")
        self.assertFalse(e.live)
        # forgotten phone: it ends by itself
        ctl.match("start", "warm")
        r.tick(ctl.MATCH_HOLD - 10, step=5, schedule=False)
        self.assertIsNotNone(ctl.match_state)
        ctl.match("ping")
        r.tick(60, step=5, schedule=False)
        self.assertIsNotNone(ctl.match_state)
        r.tick(ctl.MATCH_HOLD + 5, step=5, schedule=False)
        self.assertIsNone(ctl.match_state)
        self.assertFalse(e.live)
        self.assertNotIn("match", e.overlays)

    def test_lights_switched_on_meanwhile_stay_on(self):
        r, ctl = self.rig()
        ctl.match("start", "warm")
        ctl.set_live(True, manual=False)                       # e.g. the schedule's lights-on time
        ctl.match("stop")
        self.assertTrue(ctl.engine.live)

    def test_a_show_ends_the_test_and_is_never_covered(self):
        r, ctl = self.rig()
        ctl.play_preset("warm-white")
        ctl.set_live(True)
        ctl.match("start", "green")
        self.assertTrue(ctl.play_show("Thanksgiving tonight", manual=False))
        self.assertIsNone(ctl.match_state)
        self.assertNotIn("match", ctl.engine.overlays)
        self.assertTrue(ctl.engine.live)
        with self.assertRaises(ValueError):
            ctl.match("start", "red")                          # not while a show plays

    def test_while_only_the_test_lit_the_house_nothing_else_starts(self):
        r, ctl = self.rig()
        ctl.match("start", "warm")
        self.assertFalse(ctl.start_interrupt({"layers": [{"effect": "solid"}]}, 10, "Celebration"))
        self.assertIsNotNone(ctl.match_state)

    def test_lights_off_and_diagnostics_end_it(self):
        r, ctl = self.rig()
        ctl.set_live(True)
        ctl.match("start", "warm")
        ctl.set_live(False)
        self.assertIsNone(ctl.match_state)
        self.assertNotIn("match", ctl.engine.overlays)
        ctl.set_live(True)
        ctl.match("start", "warm")
        ctl.enter_diag()
        r.tick(1, schedule=False)
        self.assertIsNone(ctl.match_state)
        with self.assertRaises(ValueError):
            ctl.match("start", "warm")

    def test_calibration_reaches_the_lights_during_the_test(self):
        r, ctl = self.rig()
        ctl.set_live(True)
        ctl.match("start", "cool")
        e = ctl.engine
        mac = e.layout.runs[0].mac
        ctl.calibrate({"mac": mac, "brightness": 0.5})
        e.tick()
        run = e.layout.run(mac)
        self.assertEqual(e.output.frames[mac][run.pixels[0].local], (128, 128, 128))
        other = e.layout.runs[1]
        self.assertEqual(e.output.frames[other.mac][other.pixels[0].local], (255, 255, 255))

    def test_http(self):
        from goveelights.app_server import Server, make_handler
        from goveelights.auth import Auth
        r, ctl = self.rig()
        srv = Server(("127.0.0.1", 0), make_handler(ctl, lambda: None, Auth(Path(r.tmp) / "auth.json")))
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        base = f"http://127.0.0.1:{srv.server_address[1]}/api/"

        def call(path, body):
            req = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                         headers={"X-Requested-With": "lights", "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=5) as resp:
                    return resp.status, json.loads(resp.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read())
        mac = ctl.engine.layout.runs[0].mac
        code, res = call("calibrate", {"mac": mac, "nudge": "dimmer"})
        self.assertEqual((code, res["cal"][mac]["brightness"]), (200, 0.95))
        code, res = call("calibrate", {"mac": mac, "gain": [1, 2]})
        self.assertEqual(code, 400)
        code, res = call("trim", {"mac": mac, "trim": [1, 0.9, 0.9]})       # the older endpoint still works
        self.assertEqual(code, 200)
        self.assertEqual(ctl.calibrations()[mac]["gain"], [1.0, 0.9, 0.9])
        self.assertEqual(ctl.calibrations()[mac]["brightness"], 0.95)
        code, res = call("match", {"action": "start", "color": "gold"})
        self.assertEqual((code, res["color"]), (200, "gold"))
        code, res = call("match", {"action": "stop"})
        self.assertEqual((code, res["on"]), (200, False))
        code, res = call("match", {"action": "dance"})
        self.assertEqual(code, 400)


def _node_path():
    import shutil
    import subprocess
    if not shutil.which("node") or not shutil.which("npm"):
        return None
    try:
        root = subprocess.run(["npm", "root", "-g"], capture_output=True, text=True, timeout=20).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return root if root and (Path(root) / "playwright").exists() else None


NODE_PATH = _node_path()


@unittest.skipUnless(NODE_PATH, "node + Playwright not installed")
class MatchTheRunsInTheBrowser(unittest.TestCase):
    def test_phone_width_flow(self):
        import os
        import subprocess
        from goveelights.app_server import Server, make_handler
        from goveelights.auth import Auth
        r = Rig(self, datetime(2026, 11, 26, 14, 0).astimezone(), {"schedule": {"enabled": False}})
        ctl = r.ctl
        ctl.play_preset("warm-white")
        ctl.set_live(True)
        srv = Server(("127.0.0.1", 0), make_handler(ctl, lambda: None, Auth(Path(r.tmp) / "auth.json")))
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        res = subprocess.run(["node", str(ROOT / "tests" / "ui_match_runs.js"), f"http://127.0.0.1:{srv.server_address[1]}/",
                              os.environ.get("MATCH_SHOT", ""), os.environ.get("MATCH_SHOT2", "")],
                             capture_output=True, text=True, timeout=120, env={**os.environ, "NODE_PATH": NODE_PATH})
        self.assertEqual(res.returncode, 0, res.stderr)
        out = json.loads(res.stdout.strip().splitlines()[-1])
        self.assertEqual(out["errors"], [], out)
        self.assertEqual(out["overflow"], [])
        self.assertEqual(out["small"], [])
        self.assertEqual(out["steps"], ["card", "colour", "warmer", "brighter", "slider", "stop", "reset", "leave"])
        self.assertEqual(out["runs"], [x.name for x in ctl.engine.layout.runs])
        self.assertTrue(out["stopHidden"])
        self.assertEqual((out["match1"], out["match2"], out["match3"], out["match4"]),
                         ({"color": "warm"}, {"color": "red"}, None, None))
        self.assertIn("comes back", out["note"])
        self.assertIn("warmer", out["words"])
        cal = out["cal"]
        self.assertLess(cal[1]["gain"][2], 1.0)                           # Peak: a bit warmer
        self.assertEqual([c["brightness"] for c in cal], [1.0, 0.95, 0.95])   # "brighter" on a full run dims the others
        self.assertEqual(cal[2]["fade"], 0.15)
        self.assertEqual(out["fadeOut"], "15%")
        self.assertEqual(len(set(out["bar"])), 2)                          # Eaves full, the other two dimmed a little
        self.assertTrue(all(c == dict(calibrate.IDENTITY) for c in out["calAfter"]))
        self.assertEqual(ctl.playing["id"], "warm-white")                  # what was playing is still playing
        self.assertTrue(ctl.engine.live)


class SwatchesAreRealColours(unittest.TestCase):
    def test_hex(self):
        for key, (name, hexc) in calibrate.SWATCHES.items():
            self.assertRegex(hexc, r"^#[0-9a-f]{6}$")
            self.assertTrue(name)
        self.assertTrue(math.isclose(len(calibrate.SWATCHES), 6))


if __name__ == "__main__":
    unittest.main()
