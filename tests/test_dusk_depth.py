import time
import unittest
from datetime import date

from goveelights.effects import EFFECTS
from goveelights.effects_ext import dusk_depth as dd
from goveelights.fx import FX, Ctx
from goveelights.layout import Layout, Run
from goveelights.scene import Scene
from goveelights.schedule import sun_times
from test_rack import house

LAT, LON = 40.3764, -104.6922


def valid(test, cols, n):
    test.assertEqual(len(cols), n)
    for c in cols:
        test.assertEqual(len(c), 3)
        test.assertTrue(all(0.0 <= v <= 1.0 for v in c), c)


def line(n=30):
    return Layout([Run(mac="m", name="Line", count=n, geometry=[[0, 0, 0], [n - 1, n - 1, 0]])])


def lum(c):
    return sum(c)


class SunTests(unittest.TestCase):
    def test_elevation_matches_schedule_sunrise_sunset(self):
        for d in (date(2026, 11, 26), date(2026, 6, 21), date(2027, 3, 20)):
            rise, sset = sun_times(d, LAT, LON)
            for dt in (rise, sset):
                el, _ = dd.sun_position(dt.timestamp(), LAT, LON)
                self.assertAlmostEqual(el, -0.833, delta=0.5)
            _, az = dd.sun_position(sset.timestamp(), LAT, LON)
            self.assertTrue(200 < az < 320, az)           # sets in the west
            _, az = dd.sun_position(rise.timestamp(), LAT, LON)
            self.assertTrue(40 < az < 140, az)

    def test_elevation_falls_through_dusk(self):
        _, sset = sun_times(date(2026, 12, 21), LAT, LON)
        els = [dd.sun_position(sset.timestamp() + m * 60, LAT, LON)[0] for m in range(0, 120, 10)]
        self.assertEqual(els, sorted(els, reverse=True))
        self.assertLess(els[-1], -12)

    def test_polar_latitude_no_crash(self):
        self.assertIsInstance(dd.sunset_azimuth(89, 0, date(2026, 6, 21)), float)
        dd.sun_position(time.time(), 89.9, 179)


class DuskTests(unittest.TestCase):
    def render(self, params, t=1.0, target="all"):
        L = house()
        sc = Scene.from_dict({"layers": [{"effect": "dusk", "params": params, "target": target}]})
        return L, sc.render(t, L)

    def test_registered(self):
        self.assertEqual(EFFECTS["dusk"].group, "Scenes")
        self.assertIn("heartbeat", EFFECTS)

    def test_valid_and_gradient_over_height(self):
        L, cols = self.render({"demo": 60, "clouds": 0, "sun_glow": 0}, t=15)   # around sunset
        valid(self, cols, L.n)
        px = L.pixels
        low = [cols[i] for i in range(L.n) if px[i].y < -4]
        high = [cols[i] for i in range(L.n) if px[i].y > 5]
        warm = sum(c[0] - c[2] for c in low) / len(low)
        cool = sum(c[2] - c[0] for c in high) / len(high)
        self.assertGreater(warm, 0.3)                    # porch: orange horizon
        self.assertGreater(cool, -0.2)                   # apex: towards blue/violet

    def test_demo_reaches_night_palette(self):
        night = ["#0000ff", "#0000ff"]
        L, cols = self.render({"demo": 20, "colors": night, "clouds": 0, "stars": 0, "night_level": 1}, t=22)
        for c in cols:
            self.assertLess(c[0], 0.05)
            self.assertGreater(c[2], 0.9)

    def test_real_time_and_param_extremes(self):
        L = house()
        eff = EFFECTS["dusk"]
        for over in ({}, {"shift": 720}, {"shift": -720}, {"lat": 89, "lon": 180}, {"lat": -89, "lon": -180},
                     {"clouds": 1, "stars": 0.5, "speed": -10, "sun_glow": 1, "night_level": 0.2},
                     {"height": "each run"}, {"demo": 1}, {"demo": 600}, {"colors": ["#ffffff"]}):
            sc = Scene.from_dict({"layers": [{"effect": "dusk", "params": over}]})
            for k in range(3):
                valid(self, sc.render(k * 0.05 + 3.7, L), L.n)
        self.assertTrue(eff.resolve({"demo": 99999})["demo"] <= 600)

    def test_deterministic(self):
        a = self.render({"demo": 30}, t=12.3)[1]
        dd._memo.__dict__.clear()
        b = self.render({"demo": 30}, t=12.3)[1]
        self.assertEqual(a, b)

    def test_stars_twinkle_out_and_move(self):
        L = house()
        P = EFFECTS["dusk"].resolve({"demo": 20, "clouds": 0, "stars": 0.3})
        base = EFFECTS["dusk"].resolve({"demo": 20, "clouds": 0, "stars": 0})
        px = L.pixels

        def starred(t):
            return {i for i in range(L.n)
                    if abs(lum(EFFECTS["dusk"].fn(t, px[i], P)) - lum(EFFECTS["dusk"].fn(t, px[i], base))) > 0.02}
        times = [22.0 + k * 0.1 for k in range(40)]
        sets = [starred(t) for t in times]
        every = set.intersection(*sets)
        self.assertTrue(any(sets))
        self.assertLess(len(every), max(1, len(set.union(*sets))) // 2)   # stars go dark too
        # a new set of stars in a later 30 s window
        self.assertNotEqual(starred(22.0), starred(22.0 + 27 * 2))

    def test_demo_loop_is_continuous(self):
        prev = dd._demo_elevation(0, 10)
        for k in range(1, 400):
            e = dd._demo_elevation(k * 0.05, 10)
            self.assertLess(abs(e - prev), 2.5)
            prev = e

    def test_heartbeat(self):
        L = house()
        sc = Scene.from_dict({"layers": [{"effect": "heartbeat", "params": {"bpm": 60}}]})
        a = sc.render(0.04, L)
        b = sc.render(0.9, L)
        valid(self, a, L.n)
        self.assertGreater(lum(a[0]), lum(b[0]) + 0.5)


class CascadeTests(unittest.TestCase):
    def ctx(self, L, target="all", dt=0.05, clock=0.0, cache=None):
        return Ctx(L, L.indices(target), clock, dt, 120.0, cache if cache is not None else {})

    def test_registered_in_fx(self):
        self.assertIn("cascade", FX)
        self.assertIn("apex_mirror", FX)
        self.assertEqual(FX["cascade"].label, "Depth cascade")

    def test_height_down_delays_lower_bulbs(self):
        L = house()
        fn, P = FX["cascade"].fn, FX["cascade"].resolve({"delay": 1.0, "depth_fade": 0})
        st, cache = {}, {}
        dark = [(0.0, 0.0, 0.0)] * L.n
        lit = [(1.0, 1.0, 1.0)] * L.n
        top = max(range(L.n), key=lambda i: L.pixels[i].y)
        bottom = min(range(L.n), key=lambda i: L.pixels[i].y)
        for k in range(30):                              # 1.5 s of darkness
            fn(dark, self.ctx(L, dt=0.05 if k else 0.0, cache=cache), P, st)
        out = fn(lit, self.ctx(L, cache=cache), P, st)  # flash: the roof sees it first
        self.assertEqual(out[top], (1.0, 1.0, 1.0))
        self.assertEqual(out[bottom], (0.0, 0.0, 0.0))
        for _ in range(21):
            out = fn(lit, self.ctx(L, cache=cache), P, st)
        self.assertAlmostEqual(out[bottom][0], 1.0)     # a second later it reached the porch

    def test_modes_and_bounded_memory(self):
        L = house()
        for mode in dd.CASCADE_MODES:
            for target in ("all", "Peak", "Porch", "Whole front gable"):
                sc = Scene.from_dict({"layers": [{"effect": "comet", "target": target, "fx": [
                    {"type": "cascade", "params": {"mode": mode, "delay": 2, "curve": 0.3}}]}]})
                for k in range(60):
                    valid(self, sc.render(k * 0.05, L), L.n)
        # 10 s at 1000 fps with the longest delay keeps a bounded history
        fn, P, st = FX["cascade"].fn, FX["cascade"].resolve({"delay": 2}), {}
        fr = [(0.5, 0.2, 0.1)] * L.n
        for _ in range(10000):
            fn(fr, self.ctx(L, dt=0.001), P, st)
        self.assertLess(len(st["hist"]), 300)

    def test_edge_cases(self):
        L = house()
        fn, P = FX["cascade"].fn, FX["cascade"].resolve({})
        st = {}
        fr = [(0.3, 0.3, 0.3)] * L.n
        self.assertEqual(fn(fr, Ctx(L, [], 0, 0), P, st), fr)                    # empty target
        out = fn(fr, self.ctx(L, dt=0.0), P, st)
        out = fn(fr, self.ctx(L, dt=0.0), P, st)                                 # dt == 0 twice
        valid(self, out, L.n)
        out = fn(fr, self.ctx(L, dt=50.0), P, st)                                # huge dt
        self.assertEqual(len(out), L.n)
        self.assertLessEqual(st["t"], 0.2)
        L2 = line(20)                                                            # layout change
        out = fn([(1.0, 0.0, 0.0)] * L2.n, Ctx(L2, L2.indices("all"), 0, 0.05), P, st)
        self.assertEqual(len(out), L2.n)
        single = Layout([Run(mac="s", name="S", count=1)])
        out = fn([(1.0, 1.0, 1.0)], Ctx(single, single.indices("all"), 0, 0.05), P, {})
        self.assertEqual(out, [(1.0, 1.0, 1.0)])
        P2 = dict(P, mode="nonsense")
        valid(self, fn(fr, self.ctx(L), P2, {}), L.n)

    def test_longer_delay_live_does_not_blank(self):
        L = house()
        fn, st = FX["cascade"].fn, {}
        lit = [(0.6, 0.6, 0.6)] * L.n
        for k in range(60):
            fn(lit, self.ctx(L, dt=0.05 if k else 0.0), FX["cascade"].resolve({"delay": 0.2}), st)
        out = fn(lit, self.ctx(L), FX["cascade"].resolve({"delay": 2.0, "depth_fade": 0}), st)
        for i in range(L.n):
            self.assertAlmostEqual(out[i][0], 0.6)

    def test_changing_curve_keeps_layer_lit(self):
        L = house()
        fn, st = FX["cascade"].fn, {}
        lit = [(0.8, 0.8, 0.8)] * L.n
        for k in range(60):                              # curve modulated every frame
            P = FX["cascade"].resolve({"curve": 1.0 + 0.04 * (k % 2), "depth_fade": 0})
            out = fn(lit, self.ctx(L, dt=0.05 if k else 0.0, clock=k * 0.05), P, st)
        self.assertTrue(all(lum(out[i]) > 2.0 for i in range(L.n)))
        P = FX["cascade"].resolve({"mode": "from apex", "depth_fade": 0})
        out = fn(lit, self.ctx(L, clock=3.0), P, st)    # a mode change keeps it lit too
        self.assertTrue(all(lum(out[i]) > 2.0 for i in range(L.n)))

    def test_restart_cascades_in_afresh(self):
        L = house()
        fn, st = FX["cascade"].fn, {}
        P = FX["cascade"].resolve({"delay": 1.0, "depth_fade": 0})
        red = [(1.0, 0.0, 0.0)] * L.n
        for k in range(40):
            fn(red, self.ctx(L, dt=0.05 if k else 0.0, clock=k * 0.05), P, st)
        blue = [(0.0, 0.0, 1.0)] * L.n
        out = fn(blue, self.ctx(L, dt=0.0, clock=20.0), P, st)   # the clock jumped: restarted
        bottom = min(range(L.n), key=lambda i: L.pixels[i].y)
        self.assertEqual(out[bottom], (0.0, 0.0, 0.0))  # not stale red: cascading in again

    def test_depth_fade_dims_the_far_bulbs(self):
        L = house()
        fn, st = FX["cascade"].fn, {}
        P = FX["cascade"].resolve({"delay": 0.05, "depth_fade": 0.5})
        lit = [(1.0, 1.0, 1.0)] * L.n
        for k in range(20):
            out = fn(lit, self.ctx(L, dt=0.05 if k else 0.0, clock=k * 0.05), P, st)
        top = max(range(L.n), key=lambda i: L.pixels[i].y)
        bottom = min(range(L.n), key=lambda i: L.pixels[i].y)
        self.assertLess(lum(out[top]), lum(out[bottom]))  # the roof is farther from the street

    def test_deterministic(self):
        L = house()
        runs = []
        for _ in range(2):
            sc = Scene.from_dict({"layers": [{"effect": "heartbeat", "fx": [
                {"type": "cascade", "params": {"mode": "from apex"}}]}]})
            for k in range(40):
                cols = sc.render(k * 0.05, L)
            runs.append(cols)
        self.assertEqual(runs[0], runs[1])

    def test_from_apex_reaches_apex_first(self):
        L = house()
        fn, P = FX["cascade"].fn, FX["cascade"].resolve({"mode": "from apex", "delay": 1.0, "depth_fade": 0})
        st = {}
        fn([(0.0, 0.0, 0.0)] * L.n, self.ctx(L, dt=0.0), P, st)
        for _ in range(25):
            fn([(0.0, 0.0, 0.0)] * L.n, self.ctx(L), P, st)
        out = fn([(1.0, 1.0, 1.0)] * L.n, self.ctx(L), P, st)
        self.assertEqual(out[42], (1.0, 1.0, 1.0))      # Eaves 42 = the gable apex
        self.assertEqual(out[0], (0.0, 0.0, 0.0))       # north end, far away


class ApexMirrorTests(unittest.TestCase):
    def test_apex_is_front_gable(self):
        L = house()
        ax, ay = dd.apex_point(L, L.indices("Whole front gable"))
        self.assertAlmostEqual(ax, (L.pixels[42].x + L.pixels[43].x) / 2, places=6)
        ax, _ = dd.apex_point(L, L.indices("all"))
        self.assertAlmostEqual(ax, (L.pixels[42].x + L.pixels[43].x) / 2, places=6)
        gx, _ = dd.apex_point(L, L.indices("Garage peak"))
        self.assertLess(gx, -55)                         # the garage peak is its own apex

    def test_mirror_is_symmetric(self):
        L = house()
        idx = L.indices("Whole front gable")
        fn = FX["apex_mirror"].fn
        frame = [(0.0, 0.0, 0.0)] * L.n
        for i in idx:
            frame[i] = (L.pixels[i].ru, 0.5, 0.0)       # asymmetric input
        P = FX["apex_mirror"].resolve({"flare": 0})
        out = fn(frame, Ctx(L, idx, 0, 0.05), P, {})
        valid(self, out, L.n)
        # the gable outline mirrors onto itself: the apex pair swaps, the left slope copies the right
        self.assertEqual(out[43], frame[42])
        right = {frame[i] for i in range(30, 43)}
        for i in range(43, 54):
            self.assertIn(out[i], right)
        # right (north) half unchanged, left half copied from it
        ax, _ = dd.apex_point(L, idx)
        for i in idx:
            if L.pixels[i].x > ax + 0.3:
                self.assertEqual(out[i], frame[i])

    def test_sources_targets_and_flare(self):
        L = house()
        for src in dd.MIRROR_SOURCES:
            for centre in dd.MIRROR_CENTRES:
                for target in ("all", "Eaves", "Accent", "Porch", "Garage peak", "Zigzag top half"):
                    sc = Scene.from_dict({"layers": [{"effect": "comet", "target": target, "fx": [
                        {"type": "apex_mirror", "params": {"source": src, "centre": centre, "flare": 1}}]}]})
                    for k in range(3):
                        cols = sc.render(k * 0.05, L)
                    valid(self, cols, L.n)
        fn = FX["apex_mirror"].fn
        self.assertEqual(fn([(1.0, 0.0, 0.0)], Ctx(L, [], 0, 0), FX["apex_mirror"].resolve({}), {}), [(1.0, 0.0, 0.0)])
        L2 = line(11)
        out = fn([(i / 10, 0.0, 0.0) for i in range(11)], Ctx(L2, L2.indices("all"), 0, 0),
                 FX["apex_mirror"].resolve({"flare": 0, "source": "left half (south)"}), {})
        self.assertEqual([round(c[0], 2) for c in out], [0, .1, .2, .3, .4, .5, .4, .3, .2, .1, 0])
        # flare brightens the apex when light arrives there
        idx = L.indices("Whole front gable")
        frame = [(0.0, 0.0, 0.1)] * L.n
        for i in (41, 42, 43, 44):
            frame[i] = (0.5, 0.0, 0.0)
        off = fn(frame, Ctx(L, idx, 0, 0), FX["apex_mirror"].resolve({"flare": 0}), {})
        on = fn(frame, Ctx(L, idx, 0, 0), FX["apex_mirror"].resolve({"flare": 1}), {})
        self.assertGreater(lum(on[42]), lum(off[42]) + 0.2)

    def test_flare_ignores_a_steady_background(self):
        L = house()
        idx = L.indices("Whole front gable")
        frame = [(0.4, 0.2, 0.6)] * L.n
        out = FX["apex_mirror"].fn(frame, Ctx(L, idx, 0, 0), FX["apex_mirror"].resolve({"flare": 1}), {})
        self.assertEqual(out, frame)

    def test_flare_stays_in_range(self):
        L = house()
        idx = L.indices("Whole front gable")
        white = [(1.0, 1.0, 1.0)] * L.n
        out = FX["apex_mirror"].fn(white, Ctx(L, idx, 0, 0), FX["apex_mirror"].resolve({"flare": 1}), {})
        valid(self, out, L.n)
        frame = [(0.0, 0.0, 0.0)] * L.n
        for i in (41, 42, 43, 44):
            frame[i] = (1.0, 1.0, 1.0)
        out = FX["apex_mirror"].fn(frame, Ctx(L, idx, 0, 0), FX["apex_mirror"].resolve({"flare": 1}), {})
        valid(self, out, L.n)

    def test_unpaired_bulbs_go_dark(self):
        L = house()
        idx = L.indices(["Flat eaves", "Lower roofline"])
        fn = FX["apex_mirror"].fn
        frame = [(0.0, 0.0, 0.2)] * L.n
        for i in idx:
            frame[i] = (1.0, 0.5, 0.0) if i in (92, 250) else (0.0, 0.0, 0.2)
        out = fn(frame, Ctx(L, idx, 0, 0), FX["apex_mirror"].resolve({"flare": 0, "centre": "main gable apex"}), {})
        self.assertEqual(out[92], (0.0, 0.0, 0.2))      # far south corner: no partner, background
        self.assertEqual(out[250], (0.0, 0.0, 0.2))     # garage peak: no partner
        keep = fn(frame, Ctx(L, idx, 0, 0), FX["apex_mirror"].resolve({"flare": 0, "centre": "main gable apex", "blank_unpaired": False}), {})
        self.assertEqual(keep[92], (1.0, 0.5, 0.0))


class PresetTests(unittest.TestCase):
    def test_presets_render(self):
        L = house()
        for name in ("dusk-to-night", "heartbeat-from-the-apex", "falling-waves", "mirror-comets"):
            sc = Scene.load(f"presets/{name}.json")
            for k in range(30):
                cols = sc.render(k * 0.05, L)
            valid(self, cols, L.n)
            self.assertTrue(any(lum(c) > 0.1 for c in cols), name)


class DuskVividTests(unittest.TestCase):
    def test_vivid_saturates_at_same_brightness_and_default_unchanged(self):
        self.assertEqual(dd._vivid((0.75, 0.38, 0.44), 0.0), (0.75, 0.38, 0.44))
        c = dd._vivid((0.75, 0.38, 0.44), 0.7)
        self.assertAlmostEqual(max(c), 0.75)
        self.assertLess(min(c), 0.2)
        self.assertGreater(c[2], c[1])                        # hue kept: still pink, not orange
        self.assertEqual(dd._vivid((0.5, 0.5, 0.5), 1.0), (0.5, 0.5, 0.5))
        L = house()
        a = Scene.from_dict({"layers": [{"effect": "dusk", "params": {"demo": 60}}]}).render(15, L)
        b = Scene.from_dict({"layers": [{"effect": "dusk", "params": {"demo": 60, "vivid": 0.8}}]}).render(15, L)
        for c in b:
            for v in c:
                self.assertTrue(0.0 <= v <= 1.0)
        sat = lambda cols: sum((max(c) - min(c)) / max(c) for c in cols if max(c) > 0.05) / len(cols)
        self.assertGreater(sat(b), sat(a))


if __name__ == "__main__":
    unittest.main()
