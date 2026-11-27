"""Round 13 output fixes: zigzag pictures keep their shape round a dead bulb (also one at a
row end, and one on another run); far-end fade compensation reaches 'no change' at the
run's last lit bulb; the phone preview shows each run's calibration like the boxes get it."""
import copy
import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from test_integration import Rig  # noqa: E402
from test_r12_bulb_check import OWNER_LAYOUT, by_phys, layout_with, preset  # noqa: E402
from test_rack import LAYOUT_FILE, house  # noqa: E402

from goveelights import calibrate  # noqa: E402
from goveelights.effects_ext import showpieces  # noqa: E402
from goveelights.engine import to_device_frames  # noqa: E402
from goveelights.scene import Scene  # noqa: E402


def _fan_only(d):
    d = copy.deepcopy(d)
    d["layers"] = [ly for ly in d["layers"] if ly["effect"] == "fan"]
    return d


LOOKS = {"peak-joy": lambda: preset("peak-joy"), "peak-wreath": lambda: preset("peak-wreath"),
         "peak-gingerbread": lambda: preset("peak-gingerbread"),
         "turkey-fan": lambda: _fan_only(preset("turkey-fan"))}


def _row_ends(L):
    """The Peak's bottom-row end bulbs (physical)."""
    pk = L.run("Peak").pixels
    lo = min(p.y for p in pk)
    row = [p for p in pk if abs(p.y - lo) < 0.3]
    return [min(row, key=lambda p: p.x).local, max(row, key=lambda p: p.x).local]


class PicturesKeepShapeRoundADeadBulb(unittest.TestCase):
    def check(self, path):
        L0 = layout_with({}, path)
        a, b = _row_ends(L0)
        cases = [{"Peak": {a}}, {"Peak": {b}}, {"Peak": {a, b}}, {"Eaves": {5}}]
        for name, mk in LOOKS.items():
            for dead in cases:
                L = layout_with(dead, path)
                sa, sb = Scene.from_dict(mk()), Scene.from_dict(mk())
                for step in range(8):
                    t = step * 0.9
                    ca, cb = by_phys(L0, sa.render(t, L0)), by_phys(L, sb.render(t, L))
                    for run, ks in dead.items():
                        for k in ks:
                            ca.pop((run, k), None)
                    self.assertEqual(ca, cb, f"{name} with {dead} at t={t}")

    def test_example_house(self):
        self.check(LAYOUT_FILE)

    @unittest.skipUnless(OWNER_LAYOUT.exists(), "the owner's layout is not in this copy")
    def test_owner_house(self):
        self.check("layouts/house.json")

    def test_canvas_uv_does_not_move(self):
        L0 = layout_with({})
        a, _ = _row_ends(L0)
        L = layout_with({"Peak": {a}})
        c0 = showpieces.canvas(L0, [p.i for p in L0.run("Peak").pixels])
        c1 = showpieces.canvas(L, [p.i for p in L.run("Peak").pixels])
        uv0 = {L0.pixels[i].local: uv for i, uv in c0["uv"].items()}
        uv1 = {L.pixels[i].local: uv for i, uv in c1["uv"].items()}
        uv0.pop(a)
        self.assertEqual(uv0, uv1)
        self.assertEqual((c0["k"], c0["apex"], c0["y0"], c0["H"]), (c1["k"], c1["apex"], c1["y0"], c1["H"]))

    def test_stable_seed_is_the_index_without_marks(self):
        L0 = layout_with({})
        self.assertEqual([p.si for p in L0.pixels], [p.i for p in L0.pixels])
        L = layout_with({"Eaves": {5}})
        old = {(p.run, p.local): p.si for p in L0.pixels}
        self.assertEqual([p.si for p in L.pixels], [old[(p.run, p.local)] for p in L.pixels])


class FadeReachesTheLastLitBulb(unittest.TestCase):
    def setUp(self):
        self.L = layout_with({}, "layouts/house.json" if OWNER_LAYOUT.exists() else LAYOUT_FILE)

    def test_last_lit_bulb_is_unchanged_on_every_run(self):
        for r in self.L.runs:
            calibrate.apply_to_run(r, {"brightness": 1.0, "gain": [1, 1, 1], "gamma": 1.0, "fade": 0.4})
        f = to_device_frames(self.L, [(1.0, 1.0, 1.0)] * self.L.n)
        for r in self.L.runs:
            if not r.pixels:
                continue
            last = max(p.local for p in r.pixels)
            first = min(p.local for p in r.pixels)
            self.assertEqual(f[r.mac][last], (255, 255, 255), r.name)
            if first == 0:
                self.assertEqual(f[r.mac][0], (153, 153, 153), r.name)

    def test_a_dead_bulb_does_not_change_the_others(self):
        L0 = self.L
        r0 = L0.run("Eaves")
        far = max(p.local for p in r0.pixels)
        L = layout_with({"Eaves": {far}}, "layouts/house.json" if OWNER_LAYOUT.exists() else LAYOUT_FILE)
        for LL in (L0, L):
            calibrate.apply_to_run(LL.run("Eaves"), {"brightness": 1.0, "gain": [1, 1, 1], "gamma": 1.0, "fade": 0.4})
        a = to_device_frames(L0, [(1.0, 1.0, 1.0)] * L0.n)[r0.mac]
        b = to_device_frames(L, [(1.0, 1.0, 1.0)] * L.n)[r0.mac]
        a[far] = (0, 0, 0)
        self.assertEqual(a, b)


class PreviewShowsCalibration(unittest.TestCase):
    def test_preview_matches_what_is_sent(self):
        r = Rig(self, datetime(2026, 11, 26, 17, 0), {"schedule": {"enabled": False}})
        ctl, e = r.ctl, r.ctl.engine
        ctl.play_preset("warm-white", fade=0)
        e.tick()
        plain = ctl.frame_hex()
        run = e.layout.runs[0]
        calibrate.apply_to_run(run, {"brightness": 0.3, "gain": [1, 0.9, 0.6], "gamma": 1.0, "fade": 0.4})
        e.gamma = 1.0
        e.live = False
        e.tick()
        sent = to_device_frames(e.layout, e.last_colors)
        h = ctl.frame_hex()
        for k, p in enumerate(e.layout.pixels):
            got = tuple(int(h[6 * k + 2 * j:6 * k + 2 * j + 2], 16) for j in range(3))
            want = sent[p.mac][p.local]
            self.assertTrue(all(abs(x - y) <= 1 for x, y in zip(got, want)), (p, got, want))
        # the untouched runs are as before
        other = [k for k, p in enumerate(e.layout.pixels) if p.mac != run.mac]
        self.assertEqual([h[6 * k:6 * k + 6] for k in other], [plain[6 * k:6 * k + 6] for k in other])

    def test_uncalibrated_preview_is_the_render(self):
        r = Rig(self, datetime(2026, 11, 26, 17, 0), {"schedule": {"enabled": False}})
        e = r.ctl.engine
        r.ctl.play_preset("warm-white", fade=0)
        e.tick()
        self.assertIs(e.preview_colors, e.last_colors)


if __name__ == "__main__":
    unittest.main()
