import sys
import unittest

sys.path.insert(0, "tests")
from test_rack import house  # noqa: E402

from goveelights import geometry as G  # noqa: E402
from goveelights.effects import EFFECTS, Param, frame_effect  # noqa: E402
from goveelights.engine import to_device_frames  # noqa: E402
from goveelights.scene import Scene  # noqa: E402


class FoundationTests(unittest.TestCase):
    def test_zigzag_rows_and_star(self):
        L = house()
        self.assertEqual([len(r) for r in G.rows(L, "Peak")], [27, 23, 20, 16, 12, 10])
        star = G.star(L, "Peak")
        self.assertEqual(sorted(L.pixels[i].local for i in star), [42, 43])
        self.assertEqual(L.pixels[star[0]].run, "Eaves")

    def test_frame_effect_has_memory(self):
        @frame_effect("_test_counter", "test", [Param("step", "number", 1.0, min=0, max=10)])
        def counter(t, ctx, P, state):
            state["n"] = state.get("n", 0) + 1
            v = min(1.0, state["n"] / 10)
            return [(v, v, v) if i in set(ctx.idx) else (0, 0, 0) for i in range(len(ctx.layout.pixels))]
        try:
            L = house()
            sc = Scene.from_dict({"layers": [{"effect": "_test_counter", "target": "Peak"}]})
            for k in range(5):
                cols = sc.render(k * 0.05, L)
            self.assertAlmostEqual(cols[L.run("Peak").pixels[0].i][0], 0.5)
            self.assertEqual(cols[0], (0.0, 0.0, 0.0))
            edited = Scene.from_dict({"layers": [{"effect": "_test_counter", "target": "Peak"}]})
            edited.adopt_state(sc)                      # editing keeps the memory
            cols = edited.render(0.3, L)
            self.assertAlmostEqual(cols[L.run("Peak").pixels[0].i][0], 0.6)
        finally:
            EFFECTS.pop("_test_counter", None)

    def test_dither_and_trim(self):
        L = house()
        buf = [[0.0] * 3 for _ in L.pixels]
        v = 2.5 / 255
        outs = [to_device_frames(L, [(v, v, v)] * L.n, 1.0, buf)[L.runs[0].mac][0][0] for _ in range(10)]
        self.assertAlmostEqual(sum(outs) / 10, 2.5)
        L.runs[0].trim = (1.0, 0.9, 0.5)
        f = to_device_frames(L, [(1.0, 1.0, 1.0)] * L.n)
        self.assertEqual(f[L.runs[0].mac][0], (255, 230, 128))

    def test_splat_glides(self):
        L = house()
        p = L.run("Eaves").pixels[5]
        a = [(0, 0, 0)] * L.n
        G.splat(a, L, p.x + 0.5, p.y, (1, 1, 1))     # halfway between two bulbs: both partly lit
        lit = sorted(round(c[0], 2) for c in a if c[0] > 0)
        self.assertGreaterEqual(len(lit), 2)

    def test_groups(self):
        self.assertEqual(EFFECTS["glitter"].group, "Sparkle & glitter")
        self.assertTrue(all(e.group for e in EFFECTS.values()), [n for n, e in EFFECTS.items() if not e.group])


if __name__ == "__main__":
    unittest.main()


class EvolveTests(unittest.TestCase):
    def test_variations_stay_in_character(self):
        import glob
        import json
        from goveelights import evolve
        L = house()
        for f in glob.glob("presets/*.json"):
            base = json.load(open(f))
            for step in range(1, 4):
                v = evolve.variation(base, step, 0.2, f)
                self.assertEqual(len(v["layers"]), len(base["layers"]))
                Scene.from_dict(v).render(1.0, L)        # always renders
        base = json.load(open("presets/festive-glitter.json"))
        v = evolve.variation(base, 1, 0.2, "x")
        self.assertLess(abs(v["layers"][0]["params"]["width"] - base["layers"][0]["params"]["width"]), 5)
        self.assertEqual(evolve.variation(base, 5, 0.2, "x"), evolve.variation(base, 5, 0.2, "x"))   # deterministic


class EngineFixTests(unittest.TestCase):
    def test_dark_stays_dark_with_dither(self):
        L = house()
        buf = [[0.0] * 3 for _ in L.pixels]
        v = 0.3 / 255
        outs = [to_device_frames(L, [(v, v, v)] * L.n, 1.0, buf)[L.runs[0].mac][0][0] for _ in range(20)]
        self.assertEqual(set(outs), {0})

    def test_mapcode_ids_cover_every_bulb(self):
        from goveelights.effects import MAP_BITS
        L = house()
        self.assertGreater(1 << (MAP_BITS - 1), L.n + 100)
