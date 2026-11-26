import glob
import json
import unittest

from goveelights.effects import EFFECTS
from goveelights.engine import Engine, to_device_frames
from goveelights.layout import Layout, Run
from goveelights.output import MemoryOutput
from goveelights.scene import BLENDS, Layer, Scene, blend


def house():
    data = json.load(open("layouts/examples/gable-house.json"))["devices"]
    runs = [Run(mac=m, name=d["name"], count=d["pixel_count"], reverse=d["reversed"],
                hidden=set(d["hidden"]), geometry=d["geometry"]) for m, d in data.items()]
    return Layout(sorted(runs, key=lambda r: data[r.mac]["order"]))


class LayoutTests(unittest.TestCase):
    def test_virtual_strip(self):
        L = house()
        self.assertEqual(L.n, 93 + 108 + 60)       # Eaves, Peak, Accent (hidden bulbs excluded)
        self.assertEqual([p.local for p in L.runs[0].pixels][-1], 92)   # hidden tail skipped
        self.assertEqual(L.pixels[93].run, "Peak")
        self.assertEqual(L.pixels[93].local, 0)

    def test_reverse_and_hidden(self):
        L = Layout([Run("a", "A", 5, reverse=True, hidden={0})])
        self.assertEqual([p.local for p in L.pixels], [4, 3, 2, 1])
        frames = to_device_frames(L, [(1, 0, 0), (0, 1, 0), (0, 0, 1), (1, 1, 1)])
        self.assertEqual(frames["a"], [(0, 0, 0), (255, 255, 255), (0, 0, 255), (0, 255, 0), (255, 0, 0)])

    def test_geometry_interpolates(self):
        r = Run("a", "A", 5, geometry=[[0, 0, 0], [4, 8, 4]])
        self.assertEqual(r.positions()[2], (4.0, 2.0))


class EffectTests(unittest.TestCase):
    def test_every_effect_every_axis_valid(self):
        L = house()
        for name, fx in EFFECTS.items():
            axes = next((p.options for p in fx.params if p.name == "axis"), [None])
            for axis in axes:
                sc = Scene.single(name, {"axis": axis} if axis else {})
                for t in (0, 0.37, 5.2, 123.4):
                    cols = sc.render(t, L)
                    self.assertEqual(len(cols), L.n)
                    for c in cols:
                        self.assertTrue(all(0.0 <= v <= 1.0 for v in c), (name, axis, t, c))

    def test_pure(self):
        L = house()
        sc = Scene.single("twinkle")
        self.assertEqual(sc.render(3.3, L), sc.render(3.3, L))

    def test_bad_param_values_fall_back(self):
        r = EFFECTS["chase"].resolve({"spacing": "abc", "speed": 1e9, "axis": "nope"})
        self.assertEqual(r["axis"], "strip")
        self.assertEqual(r["speed"], 60)

    def test_presets_load_and_render(self):
        L = house()
        for f in glob.glob("presets/*.json"):
            Scene.load(f).render(1.0, L)


class BlendTests(unittest.TestCase):
    def test_modes(self):
        b, c = (0.5, 0.2, 0.0), (1.0, 0.5, 0.5)
        self.assertEqual(blend("normal", b, c, 1), c)
        self.assertEqual(blend("add", b, c, 1), (1.5, 0.7, 0.5))
        self.assertEqual(blend("multiply", b, c, 1), (0.5, 0.1, 0.0))
        self.assertEqual(blend("mask", b, (0, 0, 0), 1), (0, 0, 0))
        self.assertEqual(set(BLENDS), {"normal", "add", "multiply", "screen", "lighten", "mask"})

    def test_target(self):
        L = house()
        sc = Scene([Layer("solid", {"color": "#ff0000"}), Layer("solid", {"color": "#0000ff"}, target="Peak")])
        cols = sc.render(0, L)
        peak = L.run("Peak").pixels[0].i
        self.assertEqual(cols[0], (1.0, 0.0, 0.0))
        self.assertEqual(cols[peak], (0.0, 0.0, 1.0))
        self.assertEqual(cols[-1], (1.0, 0.0, 0.0))


class EngineTests(unittest.TestCase):
    def test_tick_sends_full_device_frames(self):
        L = house()
        out = MemoryOutput()
        e = Engine(L, out)
        e.set_scene(Scene.single("solid", {"color": "#00ff00"}))
        e.tick()
        self.assertEqual(out.frames, {})                 # preview only until live
        e.set_live(True)
        e.tick()
        self.assertEqual(len(out.frames[L.runs[0].mac]), 96)
        self.assertEqual(out.frames[L.runs[0].mac][95], (0, 0, 0))     # hidden
        self.assertEqual(out.frames[L.runs[1].mac][0], (0, 255, 0))

    def test_crossfade(self):
        L = Layout([Run("a", "A", 2)])
        e = Engine(L, MemoryOutput())
        e.set_scene(Scene.single("solid", {"color": "#000000"}))
        e.set_scene(Scene.single("solid", {"color": "#ffffff"}), fade_s=2)
        mid = e.render(e._fade_start + 1)[0][0]
        self.assertAlmostEqual(mid, 0.5, places=2)


if __name__ == "__main__":
    unittest.main()
