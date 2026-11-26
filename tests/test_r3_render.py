"""Regression tests for the round-3 render review fixes."""
import json
import time
import types
import unittest

from goveelights import evolve as evo
from goveelights import effects_ext  # noqa: F401  (registers plug-in effects)
from goveelights.effects import EFFECTS
from goveelights.effects_ext import dusk_depth
from goveelights.engine import Engine
from goveelights.output import MemoryOutput
from goveelights.layout import Layout, Run
from goveelights.scene import Scene
from test_rack import house


def preset(pid):
    with open(f"presets/{pid}.json") as f:
        return json.load(f)


class EvolveSentinelTests(unittest.TestCase):
    CASES = {"dusk-to-night": ("demo", "lat", "lon", "shift"), "christmas-tree": ("relight",),
             "porch-fireworks": ("finale",), "snow-piling-up": ("clear",)}

    def test_off_and_location_params_stay_put(self):
        for pid, names in self.CASES.items():
            base = preset(pid)
            for step in range(1, 13):
                v = evo.variation(base, step, 0.2, pid)
                for L0, L1 in zip(base["layers"], v["layers"]):
                    P0 = EFFECTS[L0["effect"]].resolve(L0.get("params", {}))
                    P1 = EFFECTS[L1["effect"]].resolve(L1.get("params", {}))
                    for n in names:
                        if n in P0:
                            self.assertEqual(P0[n], P1[n], f"{pid} step {step}: {n}")

    def test_zero_base_with_zero_min_is_kept(self):
        sch = EFFECTS["comet"].params
        for step in range(20):
            import random
            P = {"gap": 0}
            evo._vary(P, sch, random.Random(step), 0.3)
            self.assertEqual(P.get("gap"), 0)


class EvolveStateTests(unittest.TestCase):
    def test_tree_keeps_its_lit_state_across_a_variation(self):
        L = house()
        d = {"layers": [{"effect": "tree", "params": {}, "target": "Whole front gable"}]}
        eng = Engine(L, MemoryOutput())
        tclock = [0.0]
        eng.now = lambda: tclock[0]
        eng.set_scene(Scene.from_dict(d))
        for k in range(400):                       # 20 s: intro done, tree lit
            tclock[0] = k / 20
            eng.render(tclock[0])
        lit = sum(sum(c) for c in eng.render(20.0))
        old_state = eng.scene.layers[0].state
        eng.set_scene(Scene.from_dict(evo.variation(d, 1, 0.2, "x")), 8.0, continuous=True)
        new = eng.scene
        self.assertIsNot(new.layers[0].state, old_state)      # copied, not shared
        self.assertEqual(new.layers[0].state.get("start"), old_state.get("start"))
        tclock[0] = 20.05
        after = sum(sum(c) for c in new.render(20.05, L))
        self.assertGreater(after, lit * 0.4)


class SparkleCostTests(unittest.TestCase):
    def test_same_output_and_fast_at_slider_top(self):
        n = 261
        fx = EFFECTS["sparkle"]
        P = fx.resolve({"rate": 100, "decay": 3, "color": "#ffffff"})
        t0 = time.perf_counter()
        cols = [fx.fn(12.34, types.SimpleNamespace(i=i, n=n), P) for i in range(n)]
        self.assertLess(time.perf_counter() - t0, 0.1)
        # reference: the original per-bulb loop
        import math
        from goveelights.color import hash01
        slot = 1 / P["rate"]
        look = int(P["decay"] * 4 / slot) + 1
        k0 = math.floor(12.34 / slot)
        for i in (0, 17, 130, 260):
            out = 0.0
            for k in range(k0, k0 - look, -1):
                if int(hash01(k, P["seed"]) * n) == i:
                    out = max(out, math.exp(-(12.34 - k * slot) / P["decay"]))
            self.assertAlmostEqual(cols[i][0], out, places=9)


class UnmappedRunTests(unittest.TestCase):
    def test_hidden_bulbs_do_not_move_an_unmapped_strip(self):
        base = house()
        with open("layouts/house.json") as f:
            data = json.load(f)
        devs = data["devices"]
        runs = [Run(mac=m, name=d["name"], count=d["pixel_count"], reverse=d["reversed"],
                    hidden=set(d["hidden"]), geometry=d["geometry"]) for m, d in devs.items()]
        runs = sorted(runs, key=lambda r: devs[r.mac]["order"]) + [Run("w", "WLED", 30)]
        L = Layout(runs, data.get("zones"))
        self.assertAlmostEqual(L.minx, base.minx)
        self.assertAlmostEqual(L.maxx, base.maxx)


class DuskMemoTests(unittest.TestCase):
    def test_sun_glow_in_memo_key(self):
        fx = EFFECTS["dusk"] if "dusk" in EFFECTS else None
        P1 = dict(fx.resolve({"demo": 60, "sun_glow": 1.0})) if fx else None
        if fx is None:
            self.skipTest("no dusk effect")
        P0 = dict(P1, sun_glow=0.0)
        g1 = dusk_depth.dusk_sky(5.0, P1)["glow"]
        g0 = dusk_depth.dusk_sky(5.0, P0)["glow"]
        self.assertEqual(g0, 0.0)
        self.assertGreater(g1, 0.0)


if __name__ == "__main__":
    unittest.main()
