import json
import time
import unittest

from goveelights.effects import EFFECTS
from goveelights.fx import Ctx
from goveelights.layout import Layout, Run
from goveelights.scene import Scene

from test_rack import house, valid


def run_effect(name, L, target="all", params=None, frames=40, dt=0.05, t0=0.0, state=None):
    eff = EFFECTS[name]
    P = eff.resolve(params or {})
    idx = L.indices(target)
    st = {} if state is None else state
    out = None
    for k in range(frames):
        d = 0.0 if k == 0 else dt
        out = eff.fn(t0 + k * dt, Ctx(L, idx, t0 + k * dt, d), P, st)
    return out, st


def lit(cols, idx=None):
    return [i for i, c in enumerate(cols) if (idx is None or i in idx) and max(c) > 0.02]


class FireworksTests(unittest.TestCase):
    def setUp(self):
        self.L = house()

    def test_registered_with_groups(self):
        self.assertEqual(EFFECTS["fireworks"].group, "Physics")
        self.assertEqual(EFFECTS["snowdrift"].group, "Weather")
        self.assertTrue(EFFECTS["fireworks"].frame and EFFECTS["snowdrift"].frame)

    def test_shell_rises_then_bursts(self):
        L = self.L
        idx = set(L.indices("Whole front"))
        eff = EFFECTS["fireworks"]
        P = eff.resolve({})
        st, ys, burst = {}, [], False
        for k in range(60):
            cols = eff.fn(k * 0.05, Ctx(L, L.indices("Whole front"), k * 0.05, 0.05 if k else 0.0), P, st)
            valid(self, cols, L.n)
            self.assertTrue(all(max(c) == 0 for i, c in enumerate(cols) if i not in idx))
            shells = [p for p in st["parts"] if p[0] == 0]
            if shells:
                ys.append(shells[0][2])
            if any(p[0] == 1 for p in st["parts"]):
                burst = True
        self.assertTrue(burst, "a shell should burst within 3 s")
        self.assertGreater(len(ys), 5)
        self.assertEqual(ys, sorted(ys), "the shell climbs monotonically")
        self.assertLess(ys[0], -3)          # launched from the porch (y = -5)
        sparks = [p for p in st["parts"] if p[0] == 1]
        self.assertGreaterEqual(len(sparks), 15)

    def test_launch_from_porch_bulbs(self):
        L = self.L
        _, st = run_effect("fireworks", L, "Whole front", frames=2)
        porch_x = {round(L.pixels[i].x, 6) for i in L.indices("Porch")}
        self.assertTrue(all(round(x, 6) in porch_x for x, _ in st["launch"]))

    def test_determinism(self):
        a, _ = run_effect("fireworks", self.L, "Whole front", frames=80)
        b, _ = run_effect("fireworks", self.L, "Whole front", frames=80)
        self.assertEqual(a, b)
        c, _ = run_effect("fireworks", self.L, "Whole front", {"seed": 99}, frames=80)
        self.assertNotEqual(a, c)

    def test_finale_launches_many_shells(self):
        L = self.L
        eff = EFFECTS["fireworks"]
        P = eff.resolve({"finale": 10, "finale_shells": 12, "rate": 2})
        st, launches, prev = {}, 0, set()
        for k in range(int(16 / 0.05)):
            t = k * 0.05
            eff.fn(t, Ctx(L, L.indices("all"), t, 0.05 if k else 0.0), P, st)
            if t >= 10:
                ids = {p[10] for p in st["parts"] if p[0] == 0}
                launches += len(ids - prev)
                prev = ids
        self.assertGreaterEqual(launches, 10)

    def test_bounded_and_values(self):
        L = self.L
        cols, st = run_effect("fireworks", L, "all", {"rate": 120, "burst_size": 60, "finale": 5,
                                                      "finale_shells": 40, "embers": 2}, frames=400)
        valid(self, cols, L.n)
        self.assertLessEqual(len(st["parts"]), 900)


class SnowTests(unittest.TestCase):
    def setUp(self):
        self.L = house()

    def test_snow_accumulates_on_eaves_and_porch(self):
        L = self.L
        cols, st = run_effect("snowdrift", L, "all", {"accumulate": 6, "melt": 0}, frames=1800)
        valid(self, cols, L.n)
        snow = st["snow"]
        eaves, porch = L.indices("Flat eaves"), L.indices("Porch")
        self.assertGreater(sum(snow.get(i, 0) for i in eaves) / len(eaves), 0.3)
        self.assertGreater(sum(snow.get(i, 0) for i in porch) / len(porch), 0.3)
        # some snow settles on the zigzag tree's rows, but they hold less than the eaves
        tree = L.indices("Peak")
        self.assertGreater(sum(snow.get(i, 0) for i in tree), 1.0)
        cap = EFFECTS["snowdrift"].resolve({})["max_snow"]
        self.assertLessEqual(max(snow.get(i, 0) for i in tree), cap * 0.5 + 1e-9)
        # settled snow is lit, cool white with a touch of blue
        c = cols[eaves[len(eaves) // 2]]
        self.assertGreater(c[2], 0.2)
        self.assertGreaterEqual(c[2], c[0])

    def test_snow_caps_and_melts(self):
        L = self.L
        cols, st = run_effect("snowdrift", L, "all", {"accumulate": 8, "max_snow": 0.5, "melt": 0}, frames=2400)
        self.assertLessEqual(max(st["snow"].values()), 0.5 + 1e-9)
        eff = EFFECTS["snowdrift"]
        P = eff.resolve({"rate": 0, "melt": 2})
        before = sum(st["snow"].values())
        for k in range(200):
            eff.fn(200 + k * 0.1, Ctx(L, L.indices("all"), 200 + k * 0.1, 0.1), P, st)
        self.assertLess(sum(st["snow"].values()), before * 0.6)

    def test_flakes_fall_and_are_bounded(self):
        L = self.L
        _, st = run_effect("snowdrift", L, "all", {"rate": 40}, frames=600)
        self.assertLessEqual(len(st["flakes"]), 400)
        self.assertGreater(len(st["flakes"]), 10)
        y0 = {id(f): f[1] for f in st["flakes"]}
        run_effect("snowdrift", L, "all", {"rate": 40}, frames=2, state=st, t0=30)
        moved = [f for f in st["flakes"] if id(f) in y0]
        self.assertTrue(moved and all(f[1] < y0[id(f)] for f in moved))

    def test_determinism(self):
        a, _ = run_effect("snowdrift", self.L, "all", frames=200)
        b, _ = run_effect("snowdrift", self.L, "all", frames=200)
        self.assertEqual(a, b)


class RobustnessTests(unittest.TestCase):
    def setUp(self):
        self.L = house()

    def test_any_target_any_param_extreme(self):
        L = self.L
        for name in ("fireworks", "snowdrift"):
            eff = EFFECTS[name]
            lo = {p.name: p.min for p in eff.params if p.min is not None}
            hi = {p.name: p.max for p in eff.params if p.max is not None}
            choices = [{p.name: o} for p in eff.params if p.options for o in p.options]
            for params in [{}, lo, hi] + choices:
                for target in ("all", "Eaves", "Peak", "Accent", "Porch", "Whole front gable", "Flat eaves", []):
                    if target == []:
                        target = "nonexistent zone"
                    cols, _ = run_effect(name, L, target, params, frames=12, dt=0.3)
                    valid(self, cols, L.n)

    def test_empty_target_and_zero_dt(self):
        L = self.L
        for name in ("fireworks", "snowdrift"):
            eff = EFFECTS[name]
            P = eff.resolve({})
            st = {}
            cols = eff.fn(0, Ctx(L, [], 0, 0), P, st)
            valid(self, cols, L.n)
            for k in range(5):
                cols = eff.fn(1.0, Ctx(L, L.indices("all"), 1.0, 0.0), P, st)
                valid(self, cols, L.n)
            cols = eff.fn(500.0, Ctx(L, L.indices("all"), 500.0, 50.0), P, st)   # huge dt is capped
            valid(self, cols, L.n)

    def test_layout_change_resets(self):
        small = Layout([Run(mac="a", name="Line", count=30, geometry=[[0, 0, 0], [29, 29, 0]]),
                        Run(mac="b", name="Roof", count=20, geometry=[[0, 0, 4], [10, 10, 9], [19, 19, 4]])])
        for name in ("fireworks", "snowdrift"):
            st = {}
            run_effect(name, self.L, "all", frames=100, state=st)
            cols, st = run_effect(name, small, "all", frames=200, state=st)
            valid(self, cols, small.n)
            cols, st = run_effect(name, self.L, "Porch", frames=20, state=st)
            valid(self, cols, self.L.n)

    def test_long_run_stays_bounded(self):
        L = self.L
        eff = EFFECTS["snowdrift"]
        P = eff.resolve({"rate": 40, "accumulate": 8})
        st = {}
        for k in range(3000):        # ~5 minutes at 0.1 s steps
            eff.fn(k * 0.1, Ctx(L, L.indices("all"), k * 0.1, 0.1), P, st)
        self.assertLessEqual(len(st["flakes"]), 400)
        self.assertLessEqual(len(st["snow"]), L.n)


class PresetTests(unittest.TestCase):
    def test_presets_render_fast(self):
        L = house()
        for pid in ("porch-fireworks", "grand-finale", "snow-piling-up", "snowy-evening"):
            sc = Scene.from_dict(json.load(open(f"presets/{pid}.json")))
            t0 = time.perf_counter()
            for k in range(200):
                cols = sc.render(k / 20, L)
            valid(self, cols, L.n)
            per = (time.perf_counter() - t0) / 200 * 1000
            self.assertLess(per, 20, pid)


class ReviewFixTests(unittest.TestCase):
    def setUp(self):
        self.L = house()

    def test_bursts_land_where_there_are_bulbs(self):
        from goveelights.geometry import near
        L = self.L
        eff = EFFECTS["fireworks"]
        P = eff.resolve({"launch_zone": "Lower roofline", "rate": 40})
        idx = L.indices("all")
        allowed, st, seen, counts = set(idx), {}, set(), []
        for k in range(int(90 / 0.05)):
            t = k * 0.05
            eff.fn(t, Ctx(L, idx, t, 0.05 if k else 0.0), P, st)
            for p in st["parts"]:
                if p[0] == 3 and p[8] == "burst" and id(p) not in seen:
                    seen.add(id(p))
                    counts.append(sum(1 for i, _ in near(L, p[1], p[2], 5) if i in allowed))
        self.assertGreater(len(counts), 15)
        self.assertLessEqual(sum(c < 15 for c in counts), len(counts) * 0.05)

    def test_master_speed_substeps_keep_pace(self):
        L = self.L
        eff = EFFECTS["fireworks"]
        idx = L.indices("Whole front")
        means = []
        for dt in (0.05, 0.25):
            P, st, n = eff.resolve({"rate": 30}), {}, []
            for k in range(int(120 / dt)):
                eff.fn(k * dt, Ctx(L, idx, k * dt, dt if k else 0.0), P, st)
                n.append(len(st["parts"]))
            means.append(sum(n) / len(n))
        self.assertLess(abs(means[1] - means[0]), 0.35 * means[0])

    def test_modulated_finale_still_fires(self):
        import math
        L = self.L
        eff = EFFECTS["fireworks"]
        P = eff.resolve({"rate": 2, "finale": 20})
        st, fins, was = {}, 0, False
        for k in range(int(70 / 0.05)):
            t = k * 0.05
            P["finale"] = 20 + 8 * math.sin(t)        # what a modulation does to the param
            eff.fn(t, Ctx(L, L.indices("all"), t, 0.05 if k else 0.0), P, st)
            on = bool(st.get("pending"))
            fins += on and not was
            was = on
        self.assertGreaterEqual(fins, 2)

    def test_unborn_embers_do_not_move(self):
        from goveelights.effects_ext.fireworks_snow import EMBER, _fw_step
        st = {"parts": [[EMBER, 1.0, 2.0, 3.0, 4.0, -0.2, 3.0, (1, 1, 1), "Peony", None, 5,
                         0, 0, 0, 0, False]], "ylo": -10}
        _fw_step(st, EFFECTS["fireworks"].resolve({}), None, 0.1, 3.0)
        e = st["parts"][0]
        self.assertEqual((e[1], e[2], e[3], e[4]), (1.0, 2.0, 3.0, 4.0))

    def test_settled_snow_is_conserved_without_melt(self):
        L = self.L
        idx = L.indices("Whole front")
        _, st = run_effect("snowdrift", L, "Whole front", {"accumulate": 4, "melt": 0}, frames=1200)
        eff = EFFECTS["snowdrift"]
        P = eff.resolve({"rate": 0, "melt": 0})
        for k in range(200):                         # let the flakes in the air land first
            eff.fn(60 + k * 0.1, Ctx(L, idx, 60 + k * 0.1, 0.1), P, st)
        before = sum(st["snow"].values())
        for k in range(3000):
            t = 80 + k * 0.1
            eff.fn(t, Ctx(L, idx, t, 0.1), P, st)
        self.assertGreater(before, 3)
        self.assertAlmostEqual(sum(st["snow"].values()), before, delta=before * 0.01)

    def test_isolated_deposit_stays(self):
        L = self.L
        idx = L.indices("all")
        eff = EFFECTS["snowdrift"]
        P = eff.resolve({"rate": 0, "melt": 0})
        st = {}
        eff.fn(0, Ctx(L, idx, 0, 0.0), P, st)
        i = L.indices("Flat eaves")[5]
        st["snow"] = {i: 0.3}
        for k in range(600):
            eff.fn(k * 0.1, Ctx(L, idx, k * 0.1, 0.1), P, st)
        self.assertAlmostEqual(st["snow"].get(i, 0), 0.3, places=6)

    def test_snow_fills_slowly_and_unevenly(self):
        L = self.L
        idx = L.indices("all")
        eff = EFFECTS["snowdrift"]
        P = eff.resolve({})
        st, at = {}, {}
        for k in range(int(480 / 0.1) + 1):
            t = k * 0.1
            eff.fn(t, Ctx(L, idx, t, 0.1 if k else 0.0), P, st)
            if k in (600, 4800):
                v = [st["snow"].get(i, 0) for i in L.indices("Flat eaves")]
                m = sum(v) / len(v)
                at[k] = (m, (sum((x - m) ** 2 for x in v) / len(v)) ** 0.5)
        self.assertLess(at[600][0], 0.2)                 # one minute in: a light dusting
        self.assertGreater(at[4800][0], 0.25)            # eight minutes: clearly snowy
        self.assertLess(at[4800][0], 0.75)               # ... but not a flat, capped line
        self.assertGreater(at[4800][1], 0.04)            # drifts, not a uniform tone

    def test_snow_stays_white_under_a_palette(self):
        L = self.L
        params = {"accumulate": 6, "color": "#ff0000", "snow_color": "#00ff00"}
        cols, st = run_effect("snowdrift", L, "all", params, frames=1200)
        lit_snow = [cols[i] for i, v in st["snow"].items() if v > 0.3]
        self.assertTrue(lit_snow)
        for c in lit_snow:
            self.assertGreater(min(c), 0.4 * max(c))     # still whitish, not pure green

    def test_clearing_gust(self):
        L = self.L
        idx = L.indices("all")
        eff = EFFECTS["snowdrift"]
        P = eff.resolve({"accumulate": 6, "clear": 1, "melt": 0})
        st, peak = {}, 0.0
        for k in range(int(75 / 0.1)):
            t = k * 0.1
            eff.fn(t, Ctx(L, idx, t, 0.1 if k else 0.0), P, st)
            if 55 < t < 59.9:
                peak = max(peak, sum(st["snow"].values()))
        self.assertGreater(peak, 5)
        self.assertLess(sum(st["snow"].values()), peak * 0.5)


if __name__ == "__main__":
    unittest.main()
