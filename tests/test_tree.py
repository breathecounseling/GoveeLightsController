import json
import time
import unittest

from goveelights.effects import EFFECTS
from goveelights.effects_ext import tree as T
from goveelights.fx import Ctx
from goveelights.layout import Layout, Run
from goveelights.scene import Scene
from test_rack import house, valid


def scene(params=None, target="Whole front gable"):
    return Scene.from_dict({"layers": [{"effect": "tree", "params": params or {}, "target": target}]})


def run(sc, L, t0=0.0, t1=1.0, fps=20):
    cols = None
    for k in range(int(round((t1 - t0) * fps)) + 1):
        cols = sc.render(t0 + k / fps, L)
    return cols


def call(L, idx, P=None, state=None, clock=1.0, dt=0.05):
    fx = EFFECTS["tree"]
    return fx.fn(clock, Ctx(L, idx, clock, dt, 120, {}), fx.resolve(P or {}), {} if state is None else state)


class TreeGeometryTests(unittest.TestCase):
    def test_parts_on_whole_gable(self):
        L = house()
        g = T.tree_geometry(L, L.indices("Whole front gable"))
        self.assertEqual(g["rows"], 6)
        self.assertEqual(sorted(L.pixels[i].local for i in g["star"]), [42, 43])
        self.assertTrue(all(L.pixels[i].run == "Peak" for i in g["body"]))
        self.assertEqual(len(g["body"]), 108)
        self.assertEqual(len(g["rest"]), 22)            # the rest of the gable outline glows

    def test_star_falls_back_to_top_of_target(self):
        L = house()
        g = T.tree_geometry(L, L.indices("Peak"))
        self.assertEqual(len(g["star"]), 2)
        top = max(L.pixels[i].y for i in L.indices("Peak"))
        for i in g["star"]:
            self.assertEqual(L.pixels[i].run, "Peak")
            self.assertAlmostEqual(L.pixels[i].y, top, places=3)

    def test_ornaments_deterministic_and_apart(self):
        L = house()
        g = T.tree_geometry(L, L.indices("Whole front gable"))
        a = T.ornaments(L, g, 0.5, 7, 5)
        b = T.ornaments(L, g, 0.5, 7, 5)
        self.assertEqual(a, b)
        self.assertGreater(len(a), 10)
        for i in a:
            for j in a:
                if i < j:
                    p, q = L.pixels[i], L.pixels[j]
                    self.assertGreater((p.x - q.x) ** 2 + ((p.y - q.y) * 1.25) ** 2, 1.59)
        self.assertEqual(T.ornaments(L, g, 0.0, 7, 5), {})
        self.assertNotEqual(set(T.ornaments(L, g, 0.2, 7, 5)), set(T.ornaments(L, g, 0.2, 8, 5)))


class TreeRenderTests(unittest.TestCase):
    def test_registered(self):
        self.assertTrue(EFFECTS["tree"].frame)
        self.assertEqual(EFFECTS["tree"].group, "Scenes")
        self.assertEqual(EFFECTS["presents"].group, "Scenes")

    def test_intro_lights_trunk_first_then_star(self):
        L = house()
        sc = scene({"intro": 8})
        star = [i for i in T.tree_geometry(L, L.indices("Whole front gable"))["star"]]
        rows = __import__("goveelights.geometry", fromlist=["rows"]).rows(L, "Peak")
        first = sc.render(0.0, L)
        self.assertTrue(all(max(first[i]) == 0 for r in rows for i in r))   # dark at the start
        cols = run(sc, L, 0.05, 2.5)                                        # partway
        bottom = sum(max(cols[i]) for i in rows[0]) / len(rows[0])
        top = sum(max(cols[i]) for i in rows[-1]) / len(rows[-1])
        self.assertGreater(bottom, 0.2)
        self.assertLess(top, 0.05)
        self.assertTrue(all(max(cols[i]) == 0 for i in star))              # star not yet lit
        cols = run(sc, L, 2.55, 10.0)
        self.assertTrue(all(max(cols[i]) > 0.5 for i in star))             # star lit after the intro
        for r in rows:
            self.assertGreater(sum(max(cols[i]) for i in r) / len(r), 0.2)

    def test_no_intro_and_body_is_green(self):
        L = house()
        cols = scene({"intro": 0, "density": 0, "garland_style": "none"}).render(5.0, L)
        for i in L.indices("Peak"):
            r, g, b = cols[i]
            if i in T.tree_geometry(L, L.indices("Whole front gable"))["near_star"]:
                continue
            self.assertGreater(g, r)
            self.assertGreater(g, b)
            self.assertGreater(g, 0.15)

    def test_peak_target_lights_its_own_star(self):
        L = house()
        cols = run(scene({"intro": 0}, "Peak"), L, 3.0, 4.0)
        eaves = L.indices("Eaves")
        self.assertTrue(all(max(cols[i]) == 0 for i in eaves))
        g = T.tree_geometry(L, L.indices("Peak"))
        self.assertTrue(all(max(cols[i]) > 0.4 for i in g["star"]))

    def test_values_and_length_over_time(self):
        L = house()
        for target in ("Whole front gable", "all", "Peak", "Eaves", "Porch", "Garage peak", ["Peak", "Porch"]):
            sc = scene({"intro": 3, "burst_every": 2}, target)
            for k in range(120):
                valid(self, sc.render(k / 20, L), L.n)

    def test_deterministic(self):
        L = house()
        a, b = scene(), scene()
        for k in range(200):
            self.assertEqual(a.render(k / 20, L), b.render(k / 20, L))

    def test_extreme_params(self):
        L = house()
        fx = EFFECTS["tree"]
        idx = L.indices("Whole front gable")
        for prm in fx.params:
            vals = []
            if prm.kind in ("number", "int"):
                vals = [prm.min, prm.max]
            elif prm.kind == "choice":
                vals = prm.options
            elif prm.kind == "colors":
                vals = [[], ["#000000"], ["#ffffff"] * 9]
            elif prm.kind == "color":
                vals = ["#000000", "#ffffff"]
            for v in vals:
                state = {}
                for k, clock in enumerate((0.0, 0.0, 0.05, 1.0, 7.9, 30.0, 400.0)):
                    cols = call(L, idx, {prm.name: v}, state, clock, 0 if k == 0 else 0.05)
                    valid(self, cols, L.n)

    def test_edge_targets(self):
        L = house()
        self.assertEqual(call(L, []), [(0.0, 0.0, 0.0)] * L.n)
        for idx in ([5], [93], [42, 43], [0, 1, 2], L.indices("all"), [999, -1, 5]):
            state = {}
            for clock in (0.0, 0.0, 0.1, 5.0, 50.0):
                valid(self, call(L, idx, {"intro": 2}, state, clock, 0.0), L.n)

    def test_large_dt_and_clock_going_back(self):
        L = house()
        idx = L.indices("Whole front gable")
        state = {}
        for clock in (0.0, 100.0, 0.5, 2000.0, 1999.0):
            valid(self, call(L, idx, {"relight": 0.5, "intro": 2}, state, clock, 5.0), L.n)
        self.assertLessEqual(state["start"], 1999.0)

    def test_relight_cycle(self):
        L = house()
        sc = scene({"intro": 2, "relight": 0.5})
        body = L.indices("Peak")
        run(sc, L, 0.0, 29.0)
        lit = sc.render(29.05, L)
        self.assertGreater(sum(max(lit[i]) for i in body) / len(body), 0.2)
        cols = run(sc, L, 29.1, 32.4)               # faded out (relight is due at 30 s)
        self.assertLess(sum(max(cols[i]) for i in body) / len(body), 0.02)
        cols = run(sc, L, 32.45, 32.9)              # relighting from the trunk: top still dark
        top = __import__("goveelights.geometry", fromlist=["rows"]).rows(L, "Peak")[-1]
        self.assertLess(sum(max(cols[i]) for i in top) / len(top), 0.05)
        cols = run(sc, L, 33.85, 38.0)
        self.assertGreater(sum(max(cols[i]) for i in body) / len(body), 0.2)

    def test_layout_change(self):
        L1 = Layout([Run("a", "A", 30)])
        L2 = house()
        sc = scene({"intro": 1}, "all")
        for k in range(10):
            valid(self, sc.render(k / 20, L1), L1.n)
        for k in range(10, 30):
            valid(self, sc.render(k / 20, L2), L2.n)

    def test_garland_moves_smoothly(self):
        L = house()
        sc = scene({"intro": 0, "density": 0, "garland_sparkle": 0, "shimmer": 0})
        a = sc.render(1.0, L)
        b = sc.render(1.05, L)
        diff = max(abs(x - y) for i in L.indices("Peak") for x, y in zip(a[i], b[i]))
        self.assertGreater(diff, 0.0)          # it moves
        self.assertLess(diff, 0.35)            # but glides (anti-aliased), no hard hops

    def test_performance(self):
        L = house()
        for f in ("christmas-tree", "tree-lighting-ceremony", "golden-tree"):
            sc = Scene.load(f"presets/{f}.json")
            t0 = time.perf_counter()
            for k in range(200):
                sc.render(k / 20, L)
            self.assertLess((time.perf_counter() - t0) / 200 * 1000, 8.0)


class PresentsTests(unittest.TestCase):
    def test_presents(self):
        L = house()
        sc = Scene.from_dict({"layers": [{"effect": "presents", "target": "Porch"}]})
        for k in range(40):
            cols = sc.render(k / 20, L)
            valid(self, cols, L.n)
        lit = [i for i in L.indices("Porch") if max(cols[i]) > 0.3]
        self.assertGreater(len(lit), 12)
        fx = EFFECTS["presents"]
        for prm in fx.params:
            if prm.kind in ("number", "int"):
                for v in (prm.min, prm.max):
                    sc = Scene.from_dict({"layers": [{"effect": "presents", "params": {prm.name: v}}]})
                    valid(self, sc.render(1.3, L), L.n)


class TreePresetTests(unittest.TestCase):
    def test_tree_presets(self):
        L = house()
        names = set()
        for f in ("christmas-tree", "golden-tree", "icy-tree", "tree-lighting-ceremony", "spooky-tree"):
            with open(f"presets/{f}.json") as fh:
                d = json.load(fh)
            names.add(d["name"])
            self.assertEqual(d["layers"][0]["effect"], "tree")
            self.assertEqual(d["layers"][0]["target"], "Whole front gable")
            sc = Scene.from_dict(d)
            for k in range(0, 300, 7):
                valid(self, sc.render(k / 20, L), L.n)
        self.assertEqual(len(names), 5)


if __name__ == "__main__":
    unittest.main()
