import datetime as dt
import json
import time
import unittest

from goveelights.effects import EFFECTS
from goveelights.effects_ext import winter_night as wn
from goveelights.fx import Ctx
from goveelights.scene import Scene

from test_rack import house, valid

NAMES = ("sleigh", "icicles", "ball_drop")


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def step(name, L, idx, P, st, t, d):
    return EFFECTS[name].fn(t, Ctx(L, idx, t, d), P, st)


def run(name, L, target="all", params=None, frames=40, dt_=0.05, t0=0.0, state=None):
    eff = EFFECTS[name]
    P = eff.resolve(params or {})
    idx = L.indices(target) if isinstance(target, str) else target
    st = {} if state is None else state
    out = None
    for k in range(frames):
        out = step(name, L, idx, P, st, t0 + k * dt_, 0.0 if k == 0 else dt_)
    return out, st


class RegistrationTests(unittest.TestCase):
    def test_registered(self):
        self.assertEqual(EFFECTS["sleigh"].group, "Scenes")
        self.assertEqual(EFFECTS["icicles"].group, "Weather")
        self.assertEqual(EFFECTS["ball_drop"].group, "Scenes")
        self.assertEqual(EFFECTS["sleigh"].label, "Santa's sleigh")
        self.assertEqual(EFFECTS["icicles"].label, "Dripping icicles")
        self.assertEqual(EFFECTS["ball_drop"].label, "New Year's ball drop")
        for n in NAMES:
            e = EFFECTS[n]
            self.assertTrue(e.frame)
            self.assertTrue(e.description)
            for p in e.params:
                self.assertTrue(p.label, (n, p.name))


class RobustnessTests(unittest.TestCase):
    def setUp(self):
        self.L = house()

    def test_targets_frames_and_outside_dark(self):
        L = self.L
        targets = ["all", "Eaves", "Peak", "Accent", "Porch", "Whole front", "Whole front gable",
                   "Front gable outline", "Flat eaves", [5], [], [204, 205]]
        for n in NAMES:
            for tg in targets:
                idx = L.indices(tg) if isinstance(tg, str) else tg
                cols, _ = run(n, L, idx, frames=30, dt_=0.08)
                valid(self, cols, L.n)
                s = set(idx)
                self.assertTrue(all(max(c) == 0 for i, c in enumerate(cols) if i not in s), (n, tg))

    def test_param_extremes(self):
        L = self.L
        for n in NAMES:
            eff = EFFECTS[n]
            for pick in ("min", "max"):
                params = {}
                for p in eff.params:
                    if p.kind in ("number", "int"):
                        params[p.name] = getattr(p, pick)
                    elif p.kind == "choice":
                        params[p.name] = p.options[0 if pick == "min" else -1]
                    elif p.kind == "colors":
                        params[p.name] = [] if pick == "min" else ["#000000"]
                    elif p.kind == "text":
                        params[p.name] = "" if pick == "min" else "99:99 garbage"
                cols, _ = run(n, L, "all", params, frames=60, dt_=0.1)
                valid(self, cols, L.n)

    def test_all_choices(self):
        L = self.L
        for n in NAMES:
            for p in EFFECTS[n].params:
                if p.kind == "choice":
                    for o in p.options:
                        cols, _ = run(n, L, "all", {p.name: o}, frames=40, dt_=0.1, t0=8.0)
                        valid(self, cols, L.n)

    def test_dt_zero_huge_negative_and_clock_jumps(self):
        L = self.L
        idx = L.indices("all")
        for n in NAMES:
            P = EFFECTS[n].resolve({})
            st = {}
            for t, d in [(0, 0), (0, 0), (1, 5.0), (1.1, -1.0), (1.2, float("nan")), (500, 0.05),
                         (3, 0.05), (3.05, 0.05), (1e6, 0.05), (1e6 + 0.05, 0.05)]:
                valid(self, step(n, L, idx, P, st, t, d), L.n)

    def test_deterministic(self):
        L = self.L
        for n in NAMES:
            a, _ = run(n, L, "all", frames=200, dt_=0.05)
            b, _ = run(n, L, "all", frames=200, dt_=0.05)
            self.assertEqual(a, b, n)

    def test_bounded_state_over_time(self):
        L = self.L
        idx = L.indices("all")
        for n, params in (("sleigh", {"every": 6, "sparkle": 3, "dust": 1, "trail": 8}),
                          ("icicles", {"rate": 240}),
                          ("ball_drop", {"celebrate": 30, "pause": 1, "seconds": 3, "sparks": 2})):
            P = EFFECTS[n].resolve(params)
            st = {}
            for k in range(1500):                      # 2.5 minutes at 10 fps
                step(n, L, idx, P, st, k * 0.1, 0.1 if k else 0.0)
            if n == "sleigh":
                self.assertLessEqual(len(st["sparks"]), wn.SL_MAX_SPARKS)
                self.assertLessEqual(len(st["dust"]), wn.SL_MAX_DUST)
            elif n == "icicles":
                self.assertLessEqual(len(st["drops"]), wn.IC_MAX_DROPS)
                self.assertLessEqual(len(st["splash"]), 25)
                self.assertLessEqual(len(st["wet"]), L.n)
            else:
                self.assertLessEqual(len(st["sparks"]), wn.BD_MAX_SPARKS)
                self.assertLessEqual(len(st["glow"]), L.n)
                self.assertLessEqual(len(st["txt"]), 130)

    def test_speed_under_budget(self):
        L = self.L
        idx = L.indices("all")
        for n in NAMES:
            P = EFFECTS[n].resolve({})
            st, ms = {}, []
            for k in range(600):
                t0 = time.perf_counter()
                step(n, L, idx, P, st, k * 0.05, 0.05 if k else 0.0)
                ms.append((time.perf_counter() - t0) * 1000)
            self.assertLess(sum(ms) / len(ms), 8.0, n)


class SleighTests(unittest.TestCase):
    def setUp(self):
        self.L = house()

    def head_positions(self, frames, params=None):
        L = self.L
        idx = L.indices("Eaves")
        P = EFFECTS["sleigh"].resolve(params or {})
        st, out = {}, []
        for k in range(frames):
            t = k * 0.05
            step("sleigh", L, idx, P, st, t, 0.05 if k else 0.0)
            ps = st["pass"]
            out.append((t, None if ps is None else (ps["dir"], ps["u"])))
        return out, st

    def test_flies_across_and_alternates(self):
        out, st = self.head_positions(int(60 / 0.05), {"every": 20, "speed": 13})
        dirs, last = [], None
        for _, p in out:
            if p is not None and (last is None or p[0] != last):
                dirs.append(p[0])
            last = p[0] if p is not None else last
        self.assertGreaterEqual(len(dirs), 3)
        self.assertEqual(dirs[:3], [1, -1, 1])
        # the head moves forward monotonically within a pass and crosses the whole roofline
        us = [p[1] for _, p in out if p is not None and p[0] == 1][:150]
        self.assertEqual(us, sorted(us))
        self.assertGreater(max(p[1] for _, p in out if p is not None), st["path"].length)

    def test_red_nose_leads_and_trail_follows(self):
        L = self.L
        idx = L.indices("Eaves")
        P = EFFECTS["sleigh"].resolve({"stars": 0, "jingle": 0})
        st = {}
        cols = None
        for k in range(int(4.0 / 0.05)):
            cols = step("sleigh", L, idx, P, st, k * 0.05, 0.05 if k else 0.0)
        lit = [i for i in idx if max(cols[i]) > 0.15]
        self.assertTrue(lit)
        # first pass runs north -> south (increasing bulb index): the front is the highest index
        front = max(lit)
        r, g, _ = cols[front]
        self.assertGreater(r, 2 * g + 0.1)                   # Rudolph's red nose
        self.assertTrue(st["sparks"])
        self.assertTrue(all(s[0] < st["pass"]["u"] for s in st["sparks"]))  # sparkles behind

    def test_fixed_direction(self):
        out, _ = self.head_positions(int(50 / 0.05), {"every": 15, "direction": "south to north"})
        self.assertEqual({p[0] for _, p in out if p is not None}, {-1})

    def test_rides_over_both_gables(self):
        L = self.L
        idx = L.indices("Eaves")
        P = EFFECTS["sleigh"].resolve({"stars": 0, "jingle": 0, "sparkle": 0})
        st, apex_lit = {}, set()
        for k in range(int(14 / 0.05)):
            cols = step("sleigh", L, idx, P, st, k * 0.05, 0.05 if k else 0.0)
            for i in (42, 43, 81):
                if max(cols[i]) > 0.3:
                    apex_lit.add(i)
        self.assertTrue({42, 81} <= apex_lit or {43, 81} <= apex_lit)


class IcicleTests(unittest.TestCase):
    def setUp(self):
        self.L = house()

    def test_drops_slide_downhill_fall_and_splash(self):
        L = self.L
        idx = L.indices("Whole front")
        P = EFFECTS["icicles"].resolve({"rate": 120})
        st, tracks = {}, None
        slid = fell = 0
        landed = set()
        prev = {}
        for k in range(int(40 / 0.05)):
            t = k * 0.05
            step("icicles", L, idx, P, st, t, 0.05 if k else 0.0)
            tracks = st["tracks"]
            for d in st["drops"]:
                key = d["id"]
                if d["m"] == wn.SLIDE:
                    pth = tracks[d["k"]]
                    if key in prev and prev[key][0] == wn.SLIDE:
                        y0 = pth.point(prev[key][1])[1]
                        y1 = pth.point(d["s"])[1]
                        self.assertLessEqual(y1, y0 + 1e-6, "drops only slide downhill")
                        slid += 1
                if d["m"] == wn.FALL:
                    if key in prev and prev[key][0] == wn.FALL:
                        self.assertLess(d["y"], prev[key][2] + 1e-9)
                        fell += 1
                prev[key] = (d["m"], d["s"], d["y"])
            for s in st["splash"]:
                landed.add(s["i"])
        self.assertGreater(slid, 10)
        self.assertGreater(fell, 5)
        accent = set(L.indices("Accent"))
        self.assertTrue(landed and landed <= set(idx))
        self.assertTrue(landed & accent, "drops off the eaves land on the lower roofline")

    def test_ice_glows_between_drops(self):
        cols, _ = run("icicles", self.L, "Whole front", {"rate": 2}, frames=3)
        idx = self.L.indices("Whole front")
        lit = [i for i in idx if max(cols[i]) > 0.08]
        self.assertGreater(len(lit), len(idx) * 0.6)
        for i in lit:
            self.assertGreaterEqual(cols[i][2], cols[i][0])     # cool tints


class BallDropTests(unittest.TestCase):
    def setUp(self):
        self.L = house()
        self._now = wn._now

    def tearDown(self):
        wn._now = self._now

    def test_loop_timeline(self):
        L = self.L
        idx = L.indices("all")
        P = EFFECTS["ball_drop"].resolve({"mode": "loop", "seconds": 10, "pause": 4, "celebrate": 20})
        st, ys, phases = {}, [], []
        burst = msg = False
        zig = set(L.indices("Peak"))
        for k in range(int(36 / 0.05)):
            t = k * 0.05
            cols = step("ball_drop", L, idx, P, st, t, 0.05 if k else 0.0)
            phases.append(st["phase"])
            if st["phase"] == "drop":
                ys.append(st["ball"][1])
            if st["phase"] == "boom" and st["sparks"]:
                burst = True
            if st["phase"] == "boom" and st["remaining"] < -4 and sum(max(cols[i]) > 0.3 for i in zig) > 10:
                msg = True
            valid(self, cols, L.n)
        self.assertEqual(phases[0], "wait")
        self.assertIn("drop", phases)
        self.assertIn("boom", phases)
        self.assertEqual(ys, sorted(ys, reverse=True))
        self.assertGreater(ys[0], 4.5)                         # starts at the apex star
        self.assertLess(ys[-1], 0.6)                           # lands on the bottom row
        self.assertTrue(burst)
        self.assertTrue(msg)
        self.assertEqual(phases[int(35 / 0.05)], "wait")       # loops back round

    def test_boom_lights_the_whole_target(self):
        L = self.L
        idx = L.indices("all")
        P = EFFECTS["ball_drop"].resolve({"mode": "loop", "seconds": 3, "pause": 1})
        st = {}
        cols = None
        for k in range(int(4.1 / 0.05)):
            cols = step("ball_drop", L, idx, P, st, k * 0.05, 0.05 if k else 0.0)
        self.assertEqual(st["phase"], "boom")
        self.assertGreater(sum(max(cols[i]) > 0.4 for i in idx), len(idx) * 0.8)

    def test_digits_count_down(self):
        L = self.L
        idx = L.indices("Peak")
        P = EFFECTS["ball_drop"].resolve({"mode": "loop", "seconds": 10, "pause": 2})
        st = {}
        for k in range(int(5.1 / 0.05) + 1):
            step("ball_drop", L, idx, P, st, k * 0.05, 0.05 if k else 0.0)
        self.assertEqual(st["phase"], "drop")
        self.assertIn(7, st["dg"])                    # 6.9 s to go at t = 5.1: shows 7

    def clock_phase(self, offset, params=None):
        midnight = dt.datetime(2027, 1, 1, 0, 0, 0).timestamp()
        L = self.L
        idx = L.indices("all")
        P = EFFECTS["ball_drop"].resolve(dict({"mode": "clock", "seconds": 10, "celebrate": 60}, **(params or {})))
        st = {}
        for k in range(5):
            wn._now = lambda k=k: midnight + offset + k * 0.05
            valid(self, step("ball_drop", L, idx, P, st, k * 0.05, 0.05 if k else 0.0), L.n)
        return st

    def test_clock_mode_lands_at_midnight(self):
        st = self.clock_phase(-3600)
        self.assertEqual(st["phase"], "wait")
        self.assertAlmostEqual(st["ball"][1], st["ystar"])
        st = self.clock_phase(-5.0)
        self.assertEqual(st["phase"], "drop")
        self.assertAlmostEqual(st["remaining"], 5.0 - 0.2, places=3)
        st = self.clock_phase(-0.3)
        self.assertEqual(st["phase"], "drop")
        st = self.clock_phase(0.1)
        self.assertEqual(st["phase"], "boom")
        self.assertTrue(st["sparks"])
        st = self.clock_phase(59.0)
        self.assertEqual(st["phase"], "boom")
        st = self.clock_phase(61.0)
        self.assertEqual(st["phase"], "wait")

    def test_clock_mode_custom_time(self):
        st = self.clock_phase(-3 * 3600 - 4.0, {"at": "21:00"})   # 4 s before 9 pm
        self.assertEqual(st["phase"], "drop")
        self.assertEqual(wn._parse_hhmm("nope"), (0, 0))
        self.assertEqual(wn._parse_hhmm("21.30"), (21, 30))
        self.assertEqual(wn._parse_hhmm("25:00"), (0, 0))


class ReviewFixTests(unittest.TestCase):
    def setUp(self):
        self.L = house()

    def test_ball_never_vanishes_during_drop(self):
        from goveelights import geometry as geo
        L = self.L
        for target in ("all", "Whole front gable"):
            for cd in ("digits", "rows", "off"):
                P = EFFECTS["ball_drop"].resolve({"mode": "loop", "seconds": 10, "pause": 4, "countdown": cd})
                st, seen = {}, 0
                for k in range(330):
                    t = k * 0.05
                    out = step("ball_drop", L, L.indices(target), P, st, t, 0.0 if k == 0 else 0.05)
                    if st["phase"] != "drop":
                        continue
                    seen += 1
                    x, y = st["ball"]
                    m = max([max(out[i]) for i, d in geo.near(L, x, y, 1.6) if i in st["allowed"]] or [0.0])
                    self.assertGreater(m, 0.6, (target, cd, t, y))
                self.assertGreater(seen, 150)

    def test_sleigh_outshines_its_trail(self):
        L = self.L
        P = EFFECTS["sleigh"].resolve({"stars": 0, "jingle": 0})
        idx = L.indices("Eaves")
        st, frames = {}, 0
        for k in range(400):
            t = k * 0.05
            out = step("sleigh", L, idx, P, st, t, 0.0 if k == 0 else 0.05)
            pth = st["path"]
            ps = st["pass"]
            if ps is None:
                continue
            frames += 1
            # every trail bulb (more than 4 bulbs behind the sleigh's tail) stays capped
            _, sl0, sl1 = wn._sl_train(P)
            Lp, d = pth.length, ps["dir"]
            for j, i in enumerate(pth.order):
                u = pth.s[j] if d > 0 else Lp - pth.s[j]
                if u < ps["u"] - sl1 - 4:
                    self.assertLessEqual(max(out[i]), wn.SL_SPARK_MAX + 1e-6, (t, i))
            if ps["u"] - sl1 > 1 and ps["u"] - sl0 < Lp - 1:                      # the sleigh itself is full-bright
                self.assertGreater(max(max(out[i]) for i in pth.order), 0.9)
        self.assertGreater(frames, 50)

    def test_sleigh_dust_only_with_somewhere_to_fall(self):
        L = self.L
        _, st = run("sleigh", L, "Eaves", frames=200)
        self.assertFalse(st["dust_ok"])
        self.assertEqual(st["dust"], [])
        _, st = run("sleigh", L, "all", frames=200)
        self.assertTrue(st["dust_ok"])

    def test_icicle_drops_always_have_somewhere_to_land(self):
        L = self.L
        for grav in (4, 18, 40):
            P = EFFECTS["icicles"].resolve({"gravity": grav, "rate": 120})
            idx = L.indices("Whole front")
            st, falls = {}, set()
            for k in range(1200):
                t = k * 0.05
                step("icicles", L, idx, P, st, t, 0.0 if k == 0 else 0.05)
                for d in st["drops"]:
                    if d["m"] == wn.FALL and d["id"] not in falls:
                        falls.add(d["id"])
                        self.assertIsNotNone(d["land"], (grav, t))
            self.assertGreater(len(falls), 20)


class PresetTests(unittest.TestCase):
    def test_presets(self):
        L = house()
        for pid, eff in (("santas-sleigh", "sleigh"), ("icicles", "icicles"),
                         ("ball-drop-preview-loop", "ball_drop"), ("ball-drop-at-midnight", "ball_drop")):
            d = load(f"presets/{pid}.json")
            self.assertIn(eff, [l["effect"] for l in d["layers"]])
            sc = Scene.from_dict(d)
            for k in range(40):
                valid(self, sc.render(k * 0.05, L), L.n)
        d = load("presets/ball-drop-at-midnight.json")
        self.assertEqual(d["layers"][0]["params"]["mode"], "clock")


if __name__ == "__main__":
    unittest.main()
