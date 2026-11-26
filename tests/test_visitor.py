"""Visitor page moments: the snow flurry and colour ripple effects, the "over" overlay
blend, the controller's visitor actions (validation, rate limits, settings) and the
public HTTP routes (token, CSRF header, sizes, generic errors)."""
import http.client
import json
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, "tests")
from test_integration import Rig  # noqa: E402
from test_rack import house  # noqa: E402

from goveelights import controller as C  # noqa: E402
from goveelights.effects import EFFECTS  # noqa: E402
from goveelights.fx import Ctx  # noqa: E402
from goveelights.scene import Scene, blend  # noqa: E402

EVENING = datetime(2026, 12, 20, 19, 0).astimezone()


def run(effect, params, layout, target="all", seconds=6.0, fps=20.0, dts=None, t0=100.0):
    """Render a frame effect frame by frame; returns the list of frames."""
    fx = EFFECTS[effect]
    P = fx.resolve(params)
    idx = layout.indices(target)
    st, frames, t = {}, [], t0
    steps = dts if dts is not None else [1 / fps] * int(seconds * fps)
    first = True
    for dt in steps:
        d = 0.0 if first else dt
        t += d
        first = False
        frames.append(fx.fn(t, Ctx(layout, idx, t, d, 120.0, {}), P, st))
    return frames, st


def lit(frame, eps=0.02):
    return sum(1 for c in frame if max(c) > eps)


class EffectTests(unittest.TestCase):
    def setUp(self):
        self.L = house()

    def check_frames(self, frames, n):
        for f in frames:
            self.assertEqual(len(f), n)
            for c in f:
                self.assertEqual(len(c), 3)
                for v in c:
                    self.assertTrue(0.0 <= v <= 1.0, c)

    def test_flurry_burst_builds_then_clears(self):
        frames, st = run("flurry", {"repeat": False, "seconds": 6}, self.L, seconds=7)
        self.check_frames(frames, self.L.n)
        self.assertEqual(lit(frames[0]), 0)                 # starts empty: flakes drift in from above
        self.assertGreater(max(lit(f) for f in frames[20:80]), 15)
        self.assertEqual(lit(frames[-1]), 0)                # the burst is over
        # snow reaches the lower roofline too, not just the gable
        acc = set(self.L.indices("Accent"))
        self.assertTrue(any(max(f[i]) > 0.3 for f in frames for i in acc))

    def test_flurry_repeat_keeps_snowing_with_bounded_state(self):
        frames, st = run("flurry", {"repeat": True, "seconds": 3, "fall": 0.3}, self.L, seconds=40)
        self.assertGreater(min(lit(f) for f in frames[200:]), 3)
        self.assertLessEqual(len(st["cycles"]), st["keep"] + 1)

    def test_colour_ripple_starts_at_the_front_gable_apex(self):
        frames, _ = run("colour_ripple", {"repeat": False, "color": "#00d040"}, self.L, seconds=4.2)
        self.check_frames(frames, self.L.n)
        early = frames[3]
        lit_i = [i for i, c in enumerate(early) if max(c) > 0.2]
        self.assertTrue(lit_i)
        apex = {42, 43}
        self.assertTrue(apex & set(lit_i))
        south_apex = 81
        self.assertLess(max(early[south_apex]), 0.05)       # not the (higher) south gable
        mid = frames[40]                                    # 2 s: the whole house is tinted green
        green = [i for i, c in enumerate(mid) if c[1] > 0.25 and c[1] >= c[0]]
        self.assertGreater(len(green), self.L.n * 0.8)
        self.assertEqual(lit(frames[-1]), 0)

    def test_robust_to_targets_and_steps(self):
        for name, params in (("flurry", {"repeat": False}), ("flurry", {}),
                             ("colour_ripple", {"repeat": False}), ("colour_ripple", {}),
                             ("veil", {}), ("veil", {"repeat": True})):
            for target in ("all", "Peak", "Porch", "nothing-like-this"):
                dts = [0.0, 0.0, 0.05, 3.0, 0.0, 120.0, 0.05, 1e-6, 7.0, 0.05]
                frames, _ = run(name, params, self.L, target=target, dts=dts)
                self.check_frames(frames, self.L.n)
                tgt = set(self.L.indices(target))
                for f in frames:
                    for i, c in enumerate(f):
                        if i not in tgt:
                            self.assertEqual(c, (0.0, 0.0, 0.0))
            # extreme params are clamped by resolve, and still render
            fx = EFFECTS[name]
            lo = {p.name: p.min for p in fx.params if p.min is not None}
            hi = {p.name: p.max for p in fx.params if p.max is not None}
            for P in (lo, hi):
                frames, _ = run(name, P, self.L, seconds=3)
                self.check_frames(frames, self.L.n)

    def test_deterministic(self):
        for name in ("flurry", "colour_ripple"):
            a, _ = run(name, {"seed": 9}, self.L, seconds=3)
            b, _ = run(name, {"seed": 9}, self.L, seconds=3)
            self.assertEqual(a, b)

    def test_fast_enough(self):
        for name, params in (("flurry", {"amount": 120, "fall": 0.3}), ("colour_ripple", {})):
            fx = EFFECTS[name]
            P, idx, st = fx.resolve(params), self.L.indices("all"), {}
            t, worst = 0.0, 0.0
            for k in range(200):
                t0 = time.perf_counter()
                fx.fn(t, Ctx(self.L, idx, t, 0.05 if k else 0.0, 120.0, {}), P, st)
                worst = max(worst, time.perf_counter() - t0)
                t += 0.05
            self.assertLess(worst, 0.008 * 3, name)          # generous for slow CI; ~1-2 ms here


    def test_veil_dims_smoothly_and_lets_go(self):
        frames, _ = run("veil", {"seconds": 6, "depth": 0.5}, self.L, seconds=6.5)
        g = [f[0][0] for f in frames]
        self.assertAlmostEqual(g[0], 1.0)
        self.assertAlmostEqual(min(g), 0.5)
        self.assertAlmostEqual(g[-1], 1.0)
        self.assertLess(max(abs(a - b) for a, b in zip(g, g[1:])), 0.05)     # no jumps


class OverBlendTests(unittest.TestCase):
    def test_brightness_is_opacity(self):
        b = (0.2, 0.4, 0.6)
        self.assertEqual(blend("over", b, (0, 0, 0), 1.0), b)             # dark: see-through
        self.assertEqual(blend("over", b, (1.0, 0, 0), 1.0), (1.0, 0, 0))  # saturated: covers
        r = blend("over", b, (0.5, 0.0, 0.0), 1.0)
        self.assertAlmostEqual(r[0], 0.6)
        self.assertAlmostEqual(r[1], 0.2)

    def test_overlays_play_and_expire_with_no_scene(self):
        from goveelights.engine import Engine
        from goveelights.output import MemoryOutput
        L = house()
        e = Engine(L, MemoryOutput())
        now = time.time()
        sc = Scene.from_dict({"layers": [{"effect": "solid", "params": {"color": "#00ff00"}}]})
        e.overlays["visit:colour0"] = (sc, "over", 1.0, now + 0.3, now, 0)
        self.assertEqual(e.render(1.0)[0], (0.0, 1.0, 0.0))
        e.overlays["visit:colour0"] = (sc, "over", 1.0, now - 1, now - 2, 0)
        self.assertEqual(e.render(1.1)[0], (0.0, 0.0, 0.0))
        self.assertFalse(e.overlays)

    def test_not_offered_for_layers(self):
        with self.assertRaises(ValueError):
            Scene.from_dict({"layers": [{"effect": "solid"}, {"effect": "solid", "blend": "over"}]})


class ControllerTests(unittest.TestCase):
    def rig(self, visitor=None):
        r = Rig(self, EVENING, {"visitor": {"enabled": True, "token": "tok-123", **(visitor or {})}})
        r.ctl.play_preset("warm-white")
        r.ctl.engine.set_live(True)
        return r

    def test_snow_and_colour_add_overlays(self):
        r = self.rig()
        ctl = r.ctl
        self.assertEqual(ctl.visitor_snow("1.1.1.1"), "snow")
        self.assertEqual(ctl.visitor_colour("#00D040", "1.1.1.2"), "colour")
        keys = [k for k in ctl.engine.overlays if k.startswith("visit:")]
        self.assertEqual(len(keys), 3)
        veil = ctl.engine.overlays[keys[0]]                 # snow dims the scene first, flakes on top
        self.assertEqual((veil[0].layers[0].effect, veil[1]), ("veil", "multiply"))
        keys = keys[1:]
        ov = ctl.engine.overlays[keys[1]]
        self.assertEqual(ov[1], "over")
        self.assertEqual(ov[0].layers[0].effect, "colour_ripple")
        self.assertEqual(ov[0].layers[0].resolved["repeat"], False)
        self.assertEqual(ctl.engine.overlays[keys[0]][0].layers[0].effect, "flurry")
        cols = ctl.engine.render(ctl.engine.now())
        self.assertEqual(len(cols), ctl.engine.layout.n)
        self.assertIn("visit:snow", "".join(ctl.status()["overlays"]))

    def test_only_listed_colours(self):
        ctl = self.rig().ctl
        for bad in ("#123456", "red", "", "#ff1a1a; drop", None, "#FF1A1A" * 40):
            with self.assertRaises(ValueError) as e:
                ctl.visitor_colour(bad, "9.9.9.9")
            self.assertEqual(str(e.exception), "not available")
        self.assertFalse([k for k in ctl.engine.overlays if k.startswith("visit:")])
        for _, h in C.VISITOR_COLOURS:
            self.assertRegex(h, r"^#[0-9a-f]{6}$")
        self.assertEqual(len(C.VISITOR_COLOURS), 8)

    def test_rate_limits_per_visitor_and_global(self):
        r = self.rig()
        ctl = r.ctl
        ctl.visitor_snow("a")
        with self.assertRaises(ValueError):
            ctl.visitor_snow("b")                      # someone else, too soon after: global gap
        r.clock.t += C.VISITOR_LIMITS["snow"][1] + 0.1
        ctl.visitor_snow("b")
        r.clock.t += C.VISITOR_LIMITS["snow"][1] + 0.1
        with self.assertRaises(ValueError):
            ctl.visitor_snow("a")                      # same visitor: per-person gap
        r.clock.t += C.VISITOR_LIMITS["snow"][0]
        ctl.visitor_snow("a")
        # colours have their own limits; a few overlap in rotating slots, never piling up
        for k in range(10):
            r.clock.t += C.VISITOR_LIMITS["colour"][0] + 0.1
            ctl.visitor_colour(C.VISITOR_COLOURS[k % 8][1], "a")
        self.assertLessEqual(len([k for k in ctl.engine.overlays if k.startswith("visit:colour")]), 3)
        self.assertLessEqual(len([k for k in ctl.engine.overlays if k.startswith("visit:snow")]), 4)

    def test_settings_and_lights_off(self):
        r = self.rig({"snow": False, "colours": False, "sparkle": False})
        ctl = r.ctl
        for f in (lambda: ctl.visitor_snow("a"), lambda: ctl.visitor_colour("#ff1a1a", "a"),
                  lambda: ctl.visitor_sparkle("a")):
            with self.assertRaises(ValueError):
                f()
        st = ctl.visitor_state("a")
        self.assertEqual((st["snow"], st["sparkle"], st["colours"]), (False, False, []))
        ctl.vcfg.update(snow=True, colours=True)
        ctl.engine.set_live(False)
        with self.assertRaises(ValueError):
            ctl.visitor_snow("a")
        self.assertEqual(ctl.visitor_frame(), "")
        ctl.engine.set_live(True)
        ctl.engine.blackout = True
        with self.assertRaises(ValueError):
            ctl.visitor_colour("#ff1a1a", "a")

    def test_state_tells_a_visitor_about_their_own_pick_only(self):
        r = self.rig({"presets": ["candy-cane", "warm-white"]})
        ctl = r.ctl
        ctl.visitor_pick("candy-cane", "me")
        st = ctl.visitor_state("me")
        self.assertTrue(st["you"]["name"])
        self.assertIn("position", st["you"])
        self.assertIsNone(ctl.visitor_state("someone-else")["you"])
        r.tick(2, schedule=False)                     # the queue starts it
        self.assertTrue(ctl.visitor_state("me")["you"]["playing"])
        self.assertIsNone(ctl.visitor_state("someone-else")["you"])
        self.assertEqual(len(ctl.visitor_state()["colours"]), 8)

    def test_playing_name_only_when_the_visitor_may_see_it(self):
        ctl = self.rig({"presets": ["candy-cane"]}).ctl
        self.assertIsNone(ctl.visitor_state()["playing"])          # warm-white is not on their list
        ctl.play_preset("candy-cane")
        self.assertEqual(ctl.visitor_state()["playing"], ctl.presets.get("candy-cane")["name"])
        ctl.play_show("Winter wonderland loop")
        self.assertEqual(ctl.visitor_state()["playing"], "Winter wonderland loop")

    def test_frame_is_hex_for_every_bulb(self):
        ctl = self.rig().ctl
        ctl.engine.tick()
        h = ctl.visitor_frame()
        self.assertEqual(len(h), ctl.engine.layout.n * 6)
        int(h, 16)


class HttpTests(unittest.TestCase):
    """The public routes on a real server (loopback, ephemeral port)."""

    def setUp(self):
        from goveelights import app_server
        from goveelights.auth import Auth
        self.r = Rig(self, EVENING, {"visitor": {"enabled": True, "token": "tok-abc_123"}})
        self.r.ctl.play_preset("warm-white")
        self.r.ctl.engine.set_live(True)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        H = app_server.make_handler(self.r.ctl, lambda: None, Auth(Path(tmp.name) / "auth.json"))
        self.srv = app_server.Server(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    def req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1], timeout=10)
        h = {"X-Requested-With": "lights", "Content-Type": "application/json"}
        h.update(headers or {})
        data = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode()
        c.request(method, path, data, h)
        res = c.getresponse()
        out = res.status, res.read()
        c.close()
        try:
            return out[0], json.loads(out[1])
        except ValueError:
            return out

    def test_routes(self):
        T = "/api/visit/tok-abc_123/"
        s, st = self.req("GET", T + "state")
        self.assertEqual(s, 200)
        self.assertTrue(st["snow"])
        self.assertEqual(len(st["colours"]), 8)
        s, f = self.req("GET", T + "frame")
        self.assertEqual(s, 200)
        self.assertIsInstance(f["hex"], str)
        self.assertEqual(self.req("POST", T + "snow", {})[0], 200)
        s, b = self.req("POST", T + "snow", {})
        self.assertEqual(s, 429)
        self.assertFalse(b["ok"])
        self.assertEqual(self.req("POST", T + "colour", {"hex": "#a030ff"})[0], 200)
        for bad in ({"hex": "#abcdef"}, {"hex": ["#ff1a1a"]}, {"hex": {"x": 1}}, {}):
            s, b = self.req("POST", T + "colour", bad)
            self.assertEqual(s, 400, bad)
            self.assertEqual(b["error"], "not available")
        self.assertEqual(self.req("POST", T + "colour", b"[1,2]")[0], 400)
        self.assertEqual(self.req("POST", T + "colour", b"{not json")[0], 400)

    def test_guards(self):
        self.assertEqual(self.req("POST", "/api/visit/wrong/snow", {})[0], 404)
        self.assertEqual(self.req("GET", "/api/visit/wrong/frame")[0], 404)
        self.assertEqual(self.req("GET", "/api/visit/tok-abc_123/nope")[0], 404)
        self.assertEqual(self.req("POST", "/api/visit/tok-abc_123/nope", {})[0], 404)
        self.assertEqual(self.req("POST", "/api/visit/tok-abc_123/snow", {}, {"X-Requested-With": "x"})[0], 403)
        self.assertEqual(self.req("POST", "/api/visit/tok-abc_123/colour", b"{" + b" " * 20000 + b"}")[0], 413)
        self.r.ctl.vcfg["enabled"] = False
        self.assertEqual(self.req("POST", "/api/visit/tok-abc_123/snow", {})[0], 404)
        # no overlays were added by any refused request
        self.assertFalse([k for k in self.r.ctl.engine.overlays if k.startswith("visit:")])


class PageTests(unittest.TestCase):
    def test_visit_page_has_the_new_moments(self):
        html = Path("goveelights/web/visit.html").read_text(encoding="utf-8")
        for s in ("devicemotion", "requestPermission", "Tap, then shake your phone", "Let it snow",
                  "'colour'", "'frame'", "Now playing"):
            self.assertIn(s, html)
        self.assertNotIn("<script src", html)          # no libraries

    def test_owner_page_settings(self):
        html = Path("goveelights/web/app.html").read_text(encoding="utf-8")
        for s in ('id="vSnow"', 'id="vCol"', "snow: $('vSnow').checked", "colours: $('vCol').checked",
                  'data-f="only_when_on"', 'data-f="date"', 'data-f="countdown_to"'):
            self.assertIn(s, html)

    def test_refusals_lift_the_page_cooldown(self):
        html = Path("goveelights/web/visit.html").read_text(encoding="utf-8")
        self.assertIn("else { snowAt = 0;", html)
        self.assertIn("else { colourAt = 0;", html)
        self.assertIn("motionDenied", html)
        self.assertIn("function nice(", html)


class FollowUpTests(unittest.TestCase):
    """Review follow-ups: slots per kind, one shared veil, mapping, shows, the budget,
    and weather overlays with nothing playing."""

    def rig(self, visitor=None):
        r = Rig(self, EVENING, {"visitor": {"enabled": True, "token": "tok-123", **(visitor or {})}})
        r.ctl.play_preset("warm-white")
        r.ctl.engine.set_live(True)
        return r

    def test_a_new_moment_never_cuts_off_a_running_one_of_its_kind(self):
        r = self.rig()
        ctl, ov = r.ctl, r.ctl.engine.overlays
        ctl.visitor_snow("a")
        first = {k: v for k, v in ov.items() if k.startswith("visit:snow")}
        r.clock.t += 1.6
        ctl.visitor_colour("#ff1a1a", "b")
        r.clock.t += 2.5
        ctl.visitor_snow("c")                       # 4.1 s on: the first flurry is still running
        for k, v in first.items():
            self.assertIs(ov.get(k), v)
        self.assertEqual(len([k for k in ov if k.startswith("visit:snow")]), 2)
        # colour, snow, colour, colour 1.6 s apart: every ripple plays to its end
        ov.clear()
        r.clock.t += 20
        ctl.visitor_colour("#ff1a1a", "a")
        r.clock.t += 0.8
        ctl.visitor_snow("b")
        r.clock.t += 0.8
        ctl.visitor_colour("#00d040", "b")
        firsts = dict(ov)
        r.clock.t += 1.6
        ctl.visitor_colour("#1a4dff", "c")          # 3.2 s after the first 4 s ripple
        for k, v in firsts.items():
            if k.startswith("visit:colour"):
                self.assertIs(ov.get(k), v)
        self.assertEqual(sorted(k for k in ov if k.startswith("visit:colour")),
                         ["visit:colour0", "visit:colour1", "visit:colour2"])

    def test_overlapping_flurries_share_one_veil(self):
        r = self.rig()
        ctl, ov = r.ctl, r.ctl.engine.overlays
        ctl.visitor_snow("a")
        veil = ov["visit:veil"]
        r.clock.t += 4.1
        ctl.visitor_snow("b")
        self.assertEqual([k for k in ov if "veil" in k], ["visit:veil"])
        self.assertIs(ov["visit:veil"][0], veil[0])            # extended, not restarted
        self.assertGreater(ov["visit:veil"][3], veil[3])
        self.assertGreaterEqual(veil[0].layers[0].resolved["seconds"], 6.0)
        e = ctl.engine
        t0 = e.now()
        e.render(t0)                                             # the veil starts on its first frame
        for t in (t0 + 0.6, t0 + 2.0, t0 + 6.0):                # fade-in, hold, the extended hold
            cols = e.render(t)
            base = e.scene.render(t, e.layout, e.master)
            dim = min(sum(c) / sum(b) for c, b in zip(cols, base) if sum(b) > 0.3 and sum(c) < sum(b))
            self.assertGreater(dim, 0.45)                       # never below about half

    def test_mapping_clears_visitor_and_pad_overlays(self):
        r = self.rig()
        ctl = r.ctl
        ctl.visitor_colour("#ff1a1a", "a")
        r.clock.t += 4.1
        ctl.visitor_snow("b")
        ctl.pad("sparkle")
        ctl.engine.overlays["weather"] = (Scene.from_dict({"layers": [{"effect": "solid"}]}), "add", 0.2)
        ctl.map_start()
        try:
            self.assertFalse([k for k in ctl.engine.overlays if k.startswith(("visit:", "pad:"))])
            self.assertIn("weather", ctl.engine.overlays)
        finally:
            ctl.map_stop()

    def test_no_moments_during_a_show(self):
        r = self.rig()
        ctl = r.ctl
        ctl.play_show("Winter wonderland loop")
        for f in (lambda: ctl.visitor_snow("a"), lambda: ctl.visitor_colour("#ff1a1a", "a"),
                  lambda: ctl.visitor_sparkle("a")):
            with self.assertRaises(ValueError) as e:
                f()
            self.assertIn("show", str(e.exception))
        self.assertFalse([k for k in ctl.engine.overlays if k.startswith("visit:")])

    def test_budget_across_kinds(self):
        r = self.rig()
        ctl = r.ctl
        n, win = C.VISITOR_BUDGET
        times = []
        for k in range(30):                 # many visitors, each within every per-kind limit
            r.clock.t += 0.8
            try:
                [lambda: ctl.visitor_colour("#ff1a1a", f"c{k}"), lambda: ctl.visitor_snow(f"s{k}"),
                 lambda: ctl.visitor_sparkle(f"p{k}")][k % 3]()
                times.append(r.clock.t)
            except ValueError:
                pass
        for t in times:
            self.assertLessEqual(len([u for u in times if 0 <= u - t < win]), n)
        self.assertLessEqual(len(ctl._vmoments), n)
        r.clock.t += win + 1
        ctl.visitor_colour("#ff1a1a", "late")

    def test_persistent_overlays_need_a_scene(self):
        from goveelights.engine import Engine
        from goveelights.output import MemoryOutput
        e = Engine(house(), MemoryOutput())
        sc = Scene.from_dict({"layers": [{"effect": "solid", "params": {"color": "#ffffff"}}]})
        e.overlays["weather"] = (sc, "add", 1.0)
        self.assertEqual(set(e.render(1.0)), {(0.0, 0.0, 0.0)})
        now = time.time()
        e.overlays["visit:colour0"] = (Scene.from_dict({"layers": [{"effect": "solid", "params": {"color": "#00ff00"}}]}),
                                       "over", 1.0, now + 5, now, 0)
        self.assertEqual(e.render(1.1)[0], (0.0, 1.0, 0.0))     # timed ones play; weather still doesn't
        self.assertIn("weather", e.overlays)


if __name__ == "__main__":
    unittest.main()
