import json
import os
import unittest

from goveelights.effects import EFFECTS
from goveelights.fx import Ctx
from goveelights.layout import Layout, Run
from goveelights.scene import Scene
from goveelights.effects_ext import roofline_physics as rp

from test_rack import house

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_frames(name, layout, target="all", params=None, frames=60, dt=0.05, state=None):
    eff = EFFECTS[name]
    P = eff.resolve(params or {})
    idx = layout.indices(target)
    state = {} if state is None else state
    out, t = None, 0.0
    for k in range(frames):
        d = 0.0 if k == 0 else dt
        t += d
        out = eff.fn(t, Ctx(layout, idx, t, d, 120.0, {}), P, state)
    return out, state


class RooflinePhysicsTests(unittest.TestCase):
    def setUp(self):
        self.L = house()

    def check(self, frame, L=None):
        L = L or self.L
        self.assertEqual(len(frame), len(L.pixels))
        for c in frame:
            self.assertEqual(len(c), 3)
            self.assertTrue(all(0.0 <= v <= 1.0 for v in c), c)

    def test_registered_in_physics_group(self):
        for name in ("coaster", "embers"):
            self.assertTrue(EFFECTS[name].frame)
            self.assertEqual(EFFECTS[name].group, "Physics")

    def test_eaves_is_one_track_and_all_is_three(self):
        self.assertEqual(len(rp._tracks(self.L, self.L.indices("Eaves"))), 1)
        self.assertEqual(len(rp._tracks(self.L, self.L.indices("all"))), 3)

    def test_coaster_runs_and_lights_target_only(self):
        frame, st = run_frames("coaster", self.L, "Eaves", frames=100)
        self.check(frame)
        eaves = set(self.L.indices("Eaves"))
        lit = [i for i, c in enumerate(frame) if max(c) > 0.02]
        self.assertTrue(lit)
        self.assertTrue(set(lit) <= eaves)
        self.assertEqual(len(st["cars"]), 3)

    def test_coaster_gravity_speeds_up_downhill(self):
        # one car, no lift, starting at rest on the apex shoulder of the south gable
        P = EFFECTS["coaster"].resolve({"lift": 0, "friction": 0})
        pth = rp._tracks(self.L, self.L.indices("Eaves"))[0]
        top = max(range(len(pth.order)), key=lambda j: pth.y[j])
        car = rp._Car(0, pth.s[top] + 0.6, 0.0, 0)
        for _ in range(30):
            rp._coaster_step(car, pth, P, 1 / 60)
        self.assertGreater(abs(car.v), 5.0)
        # energy: speed at the bottom ~ sqrt(2 g h)
        for _ in range(600):
            rp._coaster_step(car, pth, P, 1 / 60)
            if pth.point(car.s)[1] < 0.05:
                break
        vmax = (2 * P["gravity"] * pth.y[top]) ** 0.5
        self.assertLess(abs(car.v), vmax * 1.05)
        self.assertGreater(abs(car.v), vmax * 0.8)

    def test_coaster_lift_keeps_it_moving(self):
        _, st = run_frames("coaster", self.L, "Eaves", {"cars": 1, "friction": 1.0}, frames=400)
        self.assertGreater(abs(st["cars"][0].v), 1.0)

    def test_coaster_stays_on_track_wrap_and_bounce(self):
        for ends in ("bounce", "wrap"):
            _, st = run_frames("coaster", self.L, "all", {"ends": ends, "cars": 6, "gravity": 120}, frames=200)
            for c in st["cars"]:
                self.assertTrue(0 <= c.s <= st["tracks"][c.tr].length)

    def test_cars_param_change_keeps_existing_cars(self):
        _, st = run_frames("coaster", self.L, "Eaves", {"cars": 2}, frames=20)
        first = st["cars"][0]
        _, st = run_frames("coaster", self.L, "Eaves", {"cars": 4}, frames=2, state=st)
        self.assertEqual(len(st["cars"]), 4)
        self.assertIs(st["cars"][0], first)

    def test_embers_pour_and_land_on_porch(self):
        frame, st = run_frames("embers", self.L, "Whole front", {"spill": 0.6}, frames=200)
        self.check(frame)
        modes = {sp.mode for sp in st["sparks"]}
        self.assertIn(rp.SLIDE, modes)
        porch = set(self.L.indices("Porch"))
        lit_porch = [i for i in porch if max(frame[i]) > 0.05]
        self.assertTrue(lit_porch, "embers should land on the porch line")
        allowed = set(self.L.indices("Whole front"))
        self.assertTrue(all(max(frame[i]) == 0 for i in range(len(frame)) if i not in allowed))

    def test_embers_sources(self):
        L = self.L
        for target, want in (("Whole front", {42}), ("Eaves", {42, 81})):
            idx = L.indices(target)
            st = {}
            rp._embers_reset(st, L, idx, EFFECTS["embers"].resolve({}))
            got = {s[2] for s in st["sources"]}
            self.assertEqual(got, want)
        # the zigzag alone: sparks start in the air at the gable apex above it
        st = {}
        rp._embers_reset(st, L, L.indices("Peak"), EFFECTS["embers"].resolve({}))
        self.assertEqual(st["sources"][0][2], None)
        self.assertGreater(st["sources"][0][1], 5)

    def test_every_target_extremes_and_dt(self):
        targets = ["all", "Eaves", "Peak", "Accent", "Porch", "Garage peak", "Both peaks", "nope", ["Eaves", "Accent"]]
        for name in ("coaster", "embers"):
            eff = EFFECTS[name]
            lo = {p.name: p.min for p in eff.params if p.min is not None}
            hi = {p.name: p.max for p in eff.params if p.max is not None}
            for params in ({}, lo, hi, {"ends": "wrap", "bump": True}):
                for tg in targets:
                    for dt in (0.0, 0.05, 5.0):
                        frame, _ = run_frames(name, self.L, tg, params, frames=25, dt=dt)
                        self.check(frame)

    def test_empty_and_tiny_layouts(self):
        tiny = Layout([Run("m", "One", 1)])
        two = Layout([Run("m", "Two", 2)])
        empty = Layout([])
        for name in ("coaster", "embers"):
            for L in (tiny, two, empty):
                frame, _ = run_frames(name, L, "all", frames=30)
                self.check(frame, L)

    def test_layout_change_resets(self):
        for name in ("coaster", "embers"):
            _, st = run_frames(name, self.L, "all", frames=30)
            other = Layout([Run("m", "Line", 40, geometry=[[0, 0, 0], [20, 20, 6], [39, 39, 0]])])
            frame, st = run_frames(name, other, "all", frames=30, state=st)
            self.check(frame, other)

    def test_deterministic(self):
        for name in ("coaster", "embers"):
            a, _ = run_frames(name, self.L, "Whole front", frames=80)
            b, _ = run_frames(name, self.L, "Whole front", frames=80)
            self.assertEqual(a, b)

    def test_presets_load_and_render(self):
        for f in ("roller-coaster", "twin-coasters", "ember-falls", "sparkler-tree"):
            with open(os.path.join(ROOT, "presets", f + ".json")) as fh:
                sc = Scene.from_dict(json.load(fh))
            for k in range(40):
                cols = sc.render(k * 0.05, self.L)
                self.check(cols)


if __name__ == "__main__":
    unittest.main()
