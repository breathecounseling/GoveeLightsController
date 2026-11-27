"""The Peak's TRUE proportions (goveelights/effects_ext/hexcanvas.py HexCanvas.aspect): on the
house the zigzag's rows hang about 0.66 of a bulb spacing apart (the owner's night photos),
not the ideal honeycomb's 0.87 nor the hand-drawn layout's 1.2. One source of truth:
camera-mapped geometry, else the run's "aspect" in the layout, else PEAK_ASPECT; helpers
convert design units (columns, rows) to bulb spacings as hung, and tools/contact_sheet.py
--street-aspect draws with it."""
from __future__ import annotations

import json
import math
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from goveelights import addrun  # noqa: E402
from goveelights.config import Config  # noqa: E402
from goveelights.effects_ext import hexcanvas as hx  # noqa: E402
from goveelights.layout import Layout, Run, run_aspect  # noqa: E402

HOUSE = ROOT / "layouts" / "house.json"
DIAGRAM = [[0, 0, 0], [5, 5, 0], [6, 4.5, 0.9], [10, 0.5, 0.9], [11, 1, 1.8], [14, 4, 1.8]]


def diagram(aspect=None, geometry=DIAGRAM, guess=False):
    return Layout([Run(mac="P", name="Peak", count=15, geometry=geometry, aspect=aspect, guess=guess)])


def mapped(gap: float, pitch: float = 1.0, aspect=None, guess=False):
    """The diagram's zigzag as camera mapping writes it: a knot per bulb, rows `gap` apart."""
    rows = [(0, 6, 1), (6, 5, -1), (11, 4, 1)]
    knots = []
    for r, (first, n, way) in enumerate(rows):
        x0 = -(n - 1) / 2 * pitch
        for k in range(n):
            col = k if way > 0 else n - 1 - k
            knots.append([first + k, round(x0 + col * pitch, 4), round(r * gap, 4)])
    return diagram(aspect, knots, guess)


def house():
    from preview import house_layout
    return house_layout(HOUSE)


class Resolution(unittest.TestCase):
    def test_house_layout_carries_the_photo_value(self):
        data = json.loads(HOUSE.read_text(encoding="utf-8-sig"))
        peak = next(d for d in data["devices"].values() if d.get("name") == "Peak")
        self.assertEqual(peak["aspect"], hx.PEAK_ASPECT)       # the fallback matches the layout file
        L = house()
        hc = hx.get(L, L.indices("Peak"))
        self.assertEqual((hc.aspect, hc.aspect_source), (hx.PEAK_ASPECT, "layout"))
        # the hand-drawn geometry says 1.2 (rows 0.84 apart, bulbs 0.7): not the truth
        self.assertAlmostEqual(hc.geometry_aspect, 1.2, places=6)
        self.assertLess(hx.PEAK_ASPECT, hx.ROW_H)              # flatter than the ideal honeycomb

    def test_the_tip_keeps_the_peaks_aspect(self):
        L = addrun.layout_with_tip(house())
        hc = hx.get(L, L.indices("Peak"))
        self.assertTrue(hc.has_tip)
        self.assertEqual((hc.aspect, hc.aspect_source), (hx.PEAK_ASPECT, "layout"))
        self.assertAlmostEqual(hc.geometry_aspect, 1.2, places=6)  # the Tip's placeholder rows ignored

    def test_no_aspect_given_falls_back_to_the_default(self):
        hc = hx.get(diagram())
        self.assertEqual((hc.aspect, hc.aspect_source), (hx.PEAK_ASPECT, "default"))
        self.assertAlmostEqual(hc.geometry_aspect, 0.9)

    def test_layout_override(self):
        hc = hx.get(diagram(aspect=0.7))
        self.assertEqual((hc.aspect, hc.aspect_source), (0.7, "layout"))

    def test_camera_mapped_geometry_wins(self):
        hc = hx.get(mapped(0.6, pitch=1.3, aspect=0.9))
        self.assertEqual(hc.aspect_source, "camera")
        self.assertAlmostEqual(hc.aspect, 0.6 / 1.3, places=3)

    def test_a_placeholder_guess_is_not_a_measurement(self):
        hc = hx.get(mapped(0.6, guess=True, aspect=0.7))
        self.assertEqual((hc.aspect, hc.aspect_source), (0.7, "layout"))

    def test_an_unbelievable_measurement_is_ignored(self):
        hc = hx.get(mapped(5.0))
        self.assertEqual(hc.aspect_source, "default")

    def test_run_aspect_reads_only_believable_numbers(self):
        self.assertEqual(run_aspect({"aspect": 0.66}), 0.66)
        self.assertEqual(run_aspect({"aspect": "0.7"}), 0.7)
        for bad in (None, True, "wide", 0, 5, [0.6]):
            self.assertIsNone(run_aspect({"aspect": bad}), bad)
        self.assertIsNone(run_aspect({}))

    def test_the_config_carries_it(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        c = Config(d / "govee_config.json")
        c.import_layout(HOUSE, create_missing=True)
        peak = next(v for v in c.devices.values() if v.get("name") == "Peak")
        self.assertEqual(peak.get("aspect"), hx.PEAK_ASPECT)
        L = Layout.from_config(c)
        self.assertEqual(next(r for r in L.runs if r.name == "Peak").aspect, hx.PEAK_ASPECT)
        self.assertEqual(next(r for r in addrun.copy_runs(L) if r.name == "Peak").aspect, hx.PEAK_ASPECT)


class TrueProportions(unittest.TestCase):
    def setUp(self):
        L = house()
        self.ii = L.indices("Peak")
        self.L = L
        self.hc = hx.get(L, self.ii)

    def test_conversions(self):
        hc, a = self.hc, hx.PEAK_ASPECT
        self.assertEqual(hc.to_physical(2.5, 3), (2.5, 3 * a))
        x, y = hc.from_physical(*hc.to_physical(-4.5, 5))
        self.assertAlmostEqual(x, -4.5)
        self.assertAlmostEqual(y, 5)
        self.assertAlmostEqual(hc.columns_for(3), 3 * a)       # 3 rows tall is ~2 bulbs wide
        self.assertAlmostEqual(hc.rows_for(2), 2 / a)          # 2 bulbs tall is ~3 rows
        w, h = hc.physical_size
        self.assertEqual(w, 26.0)                              # 27 bulbs across
        self.assertAlmostEqual(h, 5 * a)                       # 6 rows: about 3.3 bulbs tall
        self.assertGreater(w / h, 7)                           # the gable is very flat

    def test_true_canvas_draws_round_circles(self):
        t = hx.true(self.L, self.ii)
        self.assertTrue(t.is_true)
        self.assertFalse(self.hc.is_true)
        self.assertEqual(t.row_h, t.aspect)
        self.assertEqual(t.widths, self.hc.widths)             # the same cells, measured truly
        cx, cy, r = 0.0, 2.0, 1.6
        cells = t.cells_of(hx.Circle(cx, cy, r))
        wide = max(c.x for c in cells) - min(c.x for c in cells)
        tall = (max(c.y for c in cells) - min(c.y for c in cells)) * t.aspect
        self.assertLess(abs(wide - tall), 1.01)                # as tall as wide, on the house
        # the ideal honeycomb canvas's circle is fewer rows tall: wider than tall as hung
        old = self.hc.cells_of(hx.Circle(cx, cy, r))
        old_tall = (max(c.y for c in old) - min(c.y for c in old)) * t.aspect
        self.assertLess(old_tall, tall)
        for c in cells:
            X, Y = t.to_physical(c.x, c.y)
            self.assertLessEqual(math.hypot(X - cx, Y - cy * t.aspect), r + 1e-9)

    def test_get_is_unchanged(self):
        self.assertEqual(self.hc.row_h, hx.ROW_H)              # pictures keep their look until redrawn
        self.assertIs(hx.true(self.L, self.ii), hx.true(self.L, self.ii))
        self.assertIsNone(hx.true(Layout([Run(mac="E", name="Eaves", count=10)])))


class ContactSheet(unittest.TestCase):
    def test_street_aspect_uses_the_canvas_aspect(self):
        import contact_sheet as cs
        for L in (house(), addrun.layout_with_tip(house())):
            ii = L.indices("Peak")
            hc = hx.get(L, ii)
            yex = cs.street_yex(L, ii)
            pos = cs.honeycomb_positions(L, even=True)
            ys = sorted({round(y, 6) for _, y in pos.values()})
            gaps = {round(b - a, 6) for a, b in zip(ys, ys[1:])}
            self.assertEqual(len(gaps), 1)                     # evenly spaced rows
            self.assertAlmostEqual(gaps.pop() * yex / hc.pitch, hc.aspect, places=5)

    def test_without_even_rows_keep_the_layouts_heights(self):
        import contact_sheet as cs
        L = house()
        ys = sorted({round(y, 6) for _, y in cs.honeycomb_positions(L).values()})
        self.assertEqual(ys, [0.18, 1.02, 1.65, 2.49, 3.33, 3.75])


if __name__ == "__main__":
    unittest.main()
