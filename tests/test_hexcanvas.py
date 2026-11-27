"""The honeycomb canvas (goveelights/effects_ext/hexcanvas.py): the Peak's zigzag (and the
Tip) as a triangle of offset rows, one lattice cell per bulb, with shapes, strokes and
sprites drawn on that lattice. Dead bulbs keep their cell so nothing shifts."""
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from goveelights import addrun  # noqa: E402
from goveelights.effects_ext import hexcanvas as hx  # noqa: E402
from goveelights.layout import Layout, Run  # noqa: E402

OWNER = ROOT / "layouts" / "house.json"
HAS_OWNER = OWNER.exists()

# the owner's diagram: row 1 = bulbs 1..6 left to right, row 2 = 7..11 right to left, each
# between two of row 1; and a third row of 4 back left to right
DIAGRAM = [[0, 0, 0], [5, 5, 0], [6, 4.5, 0.9], [10, 0.5, 0.9], [11, 1, 1.8], [14, 4, 1.8]]


def diagram(dead=(), extra_runs=()):
    return Layout([Run(mac="P", name="Peak", count=15, geometry=DIAGRAM, dead=set(dead))] + list(extra_runs))


def owner(dead=(), tip=False):
    from preview import house_layout
    L = house_layout(OWNER)
    if tip:
        L = addrun.layout_with_tip(L)
    if dead:
        runs = addrun.copy_runs(L)
        for r in runs:
            r.dead = set(dead.get(r.name, ()))
        L = Layout(runs, L.zone_specs, L.aliases)
    return L


class Diagram(unittest.TestCase):
    def test_rows_are_offset_half_a_bulb(self):
        hc = hx.get(diagram())
        self.assertEqual(hc.widths, [6, 5, 4])
        self.assertEqual(hc.ascii(lambda c: "abcdefghijklmno"[c.local]),
                         "  l m n o\n"
                         " k j i h g\n"
                         "a b c d e f")
        # every bulb of row 2 midway between two of row 1
        r0 = [c.x for c in hc.row(0)]
        for c in hc.row(1):
            self.assertIn(c.x - 0.5, r0)
            self.assertIn(c.x + 0.5, r0)
        self.assertEqual([c.x for c in hc.row(0)], [-2.5, -1.5, -0.5, 0.5, 1.5, 2.5])

    def test_lattice_parity_and_axial(self):
        hc = hx.get(diagram())
        for c in hc.cells:
            self.assertEqual((c.d + c.r + hc.p0) % 2, 0)
            self.assertEqual(hc.at_axial(c.q, c.r), c.i)
            self.assertIs(hc.cell(c.r, c.x), c)
            self.assertEqual(hc.at(c.r, c.x), c.i)
            self.assertIs(hc.of(c.i), c)
        self.assertIsNone(hc.at(0, 0.0))          # between bulbs: no cell
        self.assertIsNone(hc.at(7, 0.5))

    def test_neighbours_and_walk(self):
        hc = hx.get(diagram())
        c = hc.cell(1, 0.0)
        self.assertEqual(len(hc.neighbours(c)), 6)
        line = hc.walk(0, -2.5, "ne", 5)
        self.assertEqual([(k.r, k.x) for k in line], [(0, -2.5), (1, -2.0), (2, -1.5)])   # stops at the edge
        self.assertEqual([k.x for k in hc.walk(0, -2.5, "e", 9)], [-2.5, -1.5, -0.5, 0.5, 1.5, 2.5])

    def test_dead_bulbs_shift_nothing(self):
        a, b = hx.get(diagram()), hx.get(diagram(dead={3, 10}))
        self.assertEqual([(c.key, c.local) for c in a.cells], [(c.key, c.local) for c in b.cells])
        self.assertEqual(b.widths, [6, 5, 4])
        self.assertTrue(b.cell(0, 0.5).dead)
        self.assertEqual(b.ascii(), "  # # # #\n x # # # #\n# # # x # #")
        disc = hx.Circle(0, 0.5, 1.6)
        ra, rb = a.raster(disc), b.raster(disc)
        # same cells covered; the dead one simply isn't drawn
        self.assertEqual({a.of(i).key for i in ra} - {a.cell(0, 0.5).key}, {b.of(i).key for i in rb})

    def test_no_zigzag(self):
        flat = Layout([Run(mac="E", name="Eaves", count=30, geometry=[[0, 0, 0], [29, 29, 0]])])
        self.assertIsNone(hx.get(flat))
        L = diagram(extra_runs=[Run(mac="E", name="Eaves", count=30, geometry=[[0, 20, 0], [29, 49, 0]])])
        self.assertIsNone(hx.get(L, L.indices("Eaves")))
        hc = hx.get(L, L.indices("all"))
        self.assertEqual(hc.host.name, "Peak")
        self.assertIs(hx.get(L, L.indices("all")), hc)               # cached

    def test_target_limits_drawing(self):
        L = diagram()
        some = [p.i for p in L.pixels if p.local < 6]                 # the bottom row only
        hc = hx.get(L, some)
        self.assertEqual(hc.widths, [6, 5, 4])                        # the lattice is the whole zigzag
        self.assertEqual(set(hc.fill_triangle()), set(some))


class Sprites(unittest.TestCase):
    def test_parse_and_place(self):
        sp = hx.Sprite("""
             R
            R R
           . G .
        """, {"R": (1, 0, 0), "G": (0, 1, 0)})
        self.assertEqual(sp.height, 3)
        self.assertEqual(sp.width, 5)
        hc = hx.get(diagram())
        out = hc.sprite(sp, row=0, col=0.0)
        got = {(hc.of(i).r, hc.of(i).x): c for i, c in out.items()}
        self.assertEqual(got, {(0, 0.5): (0.0, 1.0, 0.0), (1, 0.0): (1.0, 0.0, 0.0), (1, 1.0): (1.0, 0.0, 0.0),
                               (2, 0.5): (1.0, 0.0, 0.0)})
        dark = hc.sprite(sp, row=0, col=0.0, dark=True)
        self.assertEqual(len(dark), 6)
        self.assertEqual(sum(1 for c in dark.values() if c == (0.0, 0.0, 0.0)), 2)

    def test_sprite_matches_ascii(self):
        hc = hx.get(diagram())
        art = hc.ascii(hc.fill_triangle(), on="W")
        sp = hx.Sprite(art, {"W": (1, 1, 1)})
        self.assertEqual(len(hc.sprite(sp)), 15)

    def test_errors(self):
        with self.assertRaises(ValueError):
            hx.Sprite("R R\nR R", {"R": (1, 0, 0)})                 # stacked straight up: off the lattice
        with self.assertRaises(KeyError):
            hx.Sprite(" R\nQ Q", {"R": (1, 0, 0)})
        with self.assertRaises(ValueError):
            hx.Sprite("   \n  ")


class Shapes(unittest.TestCase):
    def test_line_at_60_degrees_is_straight(self):
        hc = hx.get(diagram())
        rh = hc.row_h
        stroke = hc.raster(hx.Line([(-2.5, 0), (-1.5, 2)], width=0.5))
        self.assertEqual({hc.of(i).key for i in stroke}, {c.key for c in hc.walk(0, -2.5, "ne", 2)})
        self.assertAlmostEqual(math.degrees(math.atan2(rh, 0.5)), 60.0)

    def test_soft_edges_and_combinations(self):
        hc = hx.get(diagram())
        crisp = hc.raster(hx.Circle(0, 1, 1.2))
        self.assertTrue(all(v == 1.0 for v in crisp.values()))
        soft = hc.raster(hx.Circle(0, 1, 1.2), soft=0.6)
        self.assertTrue(any(0 < v < 1 for v in soft.values()))
        ring = hc.raster(hx.Circle(0, 1, 1.0) - hx.Circle(0, 1, 0.5))
        self.assertNotIn(hc.at(1, 0.0), ring)
        self.assertIn(hc.at(1, 1.0), ring)
        both = hc.raster(hx.Circle(-2.5, 0, 0.1) | hx.Circle(2.5, 0, 0.1))
        self.assertEqual({hc.of(i).x for i in both}, {-2.5, 2.5})
        fn = hc.raster(lambda x, y: y - 0.5)                          # any function of design units
        self.assertEqual({hc.of(i).r for i in fn}, {0})

    def test_arc(self):
        hc = hx.get(diagram())
        # round bulb (0, 0.5): its six neighbours are 1 away; 30..150 degrees takes the two above
        top = hc.raster(hx.Arc(0.5, 0, 1.0, 30, 150, width=0.4))
        self.assertEqual({hc.of(i).key for i in top}, {(1, 0), (1, 2)})
        whole = hc.raster(hx.Arc(0.5, 0, 1.0, 0, 360, width=0.4))
        self.assertEqual(len(whole), 4)                               # (the two below are off the screen)

    def test_paint(self):
        hc = hx.get(diagram())
        f = [(0.0, 0.0, 0.0)] * 15
        i = hc.at(0, 0.5)
        hc.paint(f, {i: 0.5}, (1.0, 0.0, 0.0))
        self.assertEqual(f[i], (0.5, 0.0, 0.0))
        hc.paint(f, {i: 1.0}, (0.2, 0.2, 0.2), mode="max")
        self.assertEqual(f[i], (0.5, 0.2, 0.2))
        hc.paint(f, {i: 1.0}, (0.1, 0.1, 0.1), mode="add")
        self.assertAlmostEqual(f[i][2], 0.3)
        hc.paint(f, {i: (0.0, 0.0, 1.0)})                             # a sprite's colours
        self.assertEqual(f[i], (0.0, 0.0, 1.0))


@unittest.skipUnless(HAS_OWNER, "the owner's layout is not in this copy")
class OwnerPeak(unittest.TestCase):
    def test_rows_and_widths(self):
        hc = hx.get(owner())
        self.assertEqual(hc.widths, [27, 23, 20, 16, 12, 10])
        self.assertFalse(hc.has_tip)
        self.assertEqual((hc.row(0)[0].x, hc.row(0)[-1].x), (-13.0, 13.0))   # centred on the widest row
        self.assertEqual(len(hc.live()), 108)
        self.assertEqual(len(hc.apex_cells), 2)                      # an even top row: two centre bulbs
        self.assertEqual(hc.apex, (0.0, 5.0))
        self.assertGreater(hc.tip_y, 5.0)                            # the sides meet above the top row
        self.assertLess(hc.centroid[1], 2.5)                         # pictures sit low, on the widest rows
        for a, b in zip(hc.rows, hc.rows[1:]):                       # every row offset half a bulb
            self.assertEqual((a.cells[0].d - b.cells[0].d) % 2, 1)
            self.assertGreaterEqual(b.x0, a.x0)
            self.assertLessEqual(b.x1, a.x1)

    def test_with_the_tip(self):
        hc = hx.get(owner(tip=True))
        self.assertEqual(hc.widths, [27, 23, 20, 16, 12, 10, 7, 4, 1])
        self.assertTrue(hc.has_tip)
        self.assertEqual(hc.peak_rows, 6)
        self.assertEqual([(c.r, c.x, c.run) for c in hc.apex_cells], [(8, 0.0, "Tip")])
        self.assertEqual(len(hc.live()), 120)
        # the Tip's bulbs keep the Peak's lattice: the same cells below
        base = hx.get(owner())
        self.assertEqual([c.key for c in base.cells], [c.key for c in hc.cells if not c.tip])

    def test_fill_outline_column(self):
        hc = hx.get(owner(tip=True))
        self.assertEqual(len(hc.fill_triangle()), 120)
        self.assertEqual(len(hc.outline_triangle()), 27 + 2 * 7 + 1)
        self.assertEqual(len(hc.fill_triangle(inset=1)), sum(max(0, n - 2) for n in hc.widths[1:-1]))
        col = hc.column(0.0, width=2)
        self.assertEqual(len(col), 2 * 8 + 1)                        # two bulbs a row; the apex has one
        tri = hc.raster(hx.Polygon(hc.triangle()))
        self.assertEqual(len(tri), 120)                              # the outline polygon holds every bulb

    def test_dead_bulbs_on_the_house(self):
        L = owner(tip=True)
        Ld = owner(tip=True, dead={"Peak": {0, 26, 27, 60}, "Tip": {11}})
        a, b = hx.get(L), hx.get(Ld)
        self.assertEqual([(c.key, c.local, c.run) for c in a.cells], [(c.key, c.local, c.run) for c in b.cells])
        self.assertEqual(b.widths, a.widths)
        self.assertEqual(len(b.live()), 115)
        self.assertTrue(b.apex_cells[0].dead)
        star = hx.Circle(0, 7, 1.5)
        self.assertEqual({b.of(i).key for i in b.raster(star)},
                         {a.of(i).key for i in a.raster(star)} - {(8, 0)})


if __name__ == "__main__":
    unittest.main()
