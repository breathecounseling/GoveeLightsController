"""New opt-in params added for the r7-2 preset pass: coaster 'cover' and dusk 'aurora'.
Their defaults must leave every other preset unchanged."""
import unittest

from goveelights.effects import EFFECTS
from goveelights.fx import Ctx

from test_rack import house


def run_coaster(L, params, frames=120, dt=0.05):
    eff = EFFECTS["coaster"]
    P = eff.resolve(params)
    idx = L.indices("Eaves")
    st, out, t = {}, None, 0.0
    for k in range(frames):
        d = 0.0 if k == 0 else dt
        t += d
        out = eff.fn(t, Ctx(L, idx, t, d, 120.0, {}), P, st)
    return out, idx


class CoasterCoverTests(unittest.TestCase):
    def setUp(self):
        self.L = house()

    def test_default_is_off_and_frames_match(self):
        self.assertIs(EFFECTS["coaster"].resolve({})["cover"], False)
        a, _ = run_coaster(self.L, {"background": "#3a14a0"})
        b, _ = run_coaster(self.L, {"background": "#3a14a0", "cover": False})
        self.assertEqual(a, b)

    def test_cover_keeps_car_colour_pure_and_track_lit(self):
        bg = (0x3a / 255, 0x14 / 255, 0xa0 / 255)
        out, idx = run_coaster(self.L, {"cars": 1, "colors": ["#ff0000"], "background": "#3a14a0",
                                        "cover": True, "sparkle": 0})
        for i in idx:
            c = out[i]
            self.assertTrue(all(0.0 <= v <= 1.0 for v in c), c)
        # the brightest bulb is the car: red with no blue from the track mixed in
        top = max((out[i] for i in idx), key=lambda c: c[0])
        self.assertGreater(top[0], 0.8)
        self.assertLess(top[2], bg[2] - 0.1)          # without cover it would keep the track blue
        # bulbs away from the car show the track colour, not black
        dark = [out[i] for i in idx if out[i][0] < 0.01 + bg[0]]
        self.assertTrue(dark)
        self.assertTrue(all(abs(c[2] - bg[2]) < 0.02 for c in dark))

    def test_cover_with_black_background_is_unchanged(self):
        a, _ = run_coaster(self.L, {})
        b, _ = run_coaster(self.L, {"cover": True})
        self.assertEqual(a, b)


class DuskAuroraTests(unittest.TestCase):
    def test_default_off_and_only_upper_bulbs_at_night(self):
        eff = EFFECTS["dusk"]
        self.assertEqual(eff.resolve({})["aurora"], 0)
        L = house()
        # demo mode holds full night for a while after the dusk has raced through
        base = eff.resolve({"demo": 20, "clouds": 0, "stars": 0})
        aur = eff.resolve({"demo": 20, "clouds": 0, "stars": 0, "aurora": 1})
        t = 24.0
        changed = [p for p in L.pixels if eff.fn(t, p, base) != eff.fn(t, p, aur)]
        self.assertTrue(changed)
        self.assertTrue(all(0.5 * (p.ny + p.rny) > 0.25 for p in changed))
        # at the start of the demo (sun still up) the aurora is invisible
        self.assertEqual([eff.fn(0.0, p, base) for p in L.pixels], [eff.fn(0.0, p, aur) for p in L.pixels])


if __name__ == "__main__":
    unittest.main()
