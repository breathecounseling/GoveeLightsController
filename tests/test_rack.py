import json
import unittest

from goveelights import modulation
from goveelights.fx import FX
from goveelights.layout import Layout, Run
from goveelights.scene import Scene


def house():
    data = json.load(open("layouts/house.json"))
    devs = data["devices"]
    runs = [Run(mac=m, name=d["name"], count=d["pixel_count"], reverse=d["reversed"],
                hidden=set(d["hidden"]), geometry=d["geometry"]) for m, d in devs.items()]
    return Layout(sorted(runs, key=lambda r: devs[r.mac]["order"]), data.get("zones"))


def valid(test, cols, n):
    test.assertEqual(len(cols), n)
    for c in cols:
        test.assertTrue(all(0.0 <= v <= 1.0 for v in c), c)


class RackTests(unittest.TestCase):
    def test_every_fx_runs_over_time(self):
        L = house()
        for name in FX:
            sc = Scene.from_dict({"layers": [{"effect": "comet", "fx": [{"type": name}]}]})
            for k in range(6):
                valid(self, sc.render(1 + k * 0.05, L), L.n)

    def test_fx_chain_and_mod_on_fx(self):
        L = house()
        sc = Scene.from_dict({"layers": [{"effect": "rainbow", "fx": [
            {"type": "hue", "mod": {"shift": {"shape": "saw", "rate": 1, "depth": 1}}},
            {"type": "strobe", "params": {"rate": 2, "duty": 0.5}}]}]})
        on = sc.render(0.1, L)
        off = sc.render(0.3, L)            # strobe: second half of each cycle is dark
        self.assertTrue(any(sum(c) > 0 for c in on))
        self.assertTrue(all(sum(c) == 0 for c in off))

    def test_zones(self):
        L = house()
        self.assertEqual(len(L.zones["North wall"]), 31)
        self.assertEqual(len(L.indices("Whole front gable")), 24 + 108)
        sc = Scene.from_dict({"layers": [{"effect": "solid", "params": {"color": "#ff0000"}, "target": "Front gable outline"}]})
        cols = sc.render(0, L)
        self.assertEqual(sum(1 for c in cols if c[0] > 0), 24)

    def test_palette_restyles(self):
        L = house()
        sc = Scene.from_dict({"layers": [{"effect": "solid", "params": {"color": "#ff0000"}, "palette": "Icy"}]})
        self.assertEqual(sc.render(0, L)[0], (1.0, 1.0, 1.0))
        sc = Scene.from_dict({"layers": [{"effect": "solid", "params": {"color": "#ff0000"}}]})
        self.assertEqual(sc.render(0, L, {"palette": "Ocean"})[0][0], 0.0)

    def test_speed_change_does_not_jump(self):
        L = house()
        a = Scene.from_dict({"layers": [{"effect": "comet", "params": {"speed": 10}}]})
        for k in range(40):
            a.render(100 + k * 0.05, L)
        before = a.layers[0].travel
        b = Scene.from_dict({"layers": [{"effect": "comet", "params": {"speed": 50}}]})
        b.adopt_state(a)
        b.render(102.0, L)            # last frame was at 101.95
        self.assertAlmostEqual(b.layers[0].travel - before, 50 * 0.05, places=3)

    def test_modulation_bounds_and_shapes(self):
        for shape in modulation.SHAPES:
            for k in range(50):
                v = modulation.apply({"shape": shape, "rate": 0.7, "depth": 1}, 5, 0, 10, k * 0.13)
                self.assertTrue(0 <= v <= 10)
        self.assertEqual(modulation.apply({"sync": True, "beats": 4, "shape": "square", "depth": 1}, 5, 0, 10, 0.1, bpm=120), 10)

    def test_master_hue_and_speed(self):
        L = house()
        sc = Scene.from_dict({"layers": [{"effect": "solid", "params": {"color": "#ff0000"}}]})
        g = sc.render(0, L, {"hue": 1 / 3})[0]
        self.assertGreater(g[1], 0.99)

    def test_v1_presets_still_load(self):
        import glob
        L = house()
        for f in glob.glob("presets/*.json"):
            valid(self, Scene.load(f).render(3.0, L), L.n)


if __name__ == "__main__":
    unittest.main()
