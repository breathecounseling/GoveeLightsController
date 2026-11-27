"""Round 13: the December pictures redrawn for the honeycomb triangle (hexcanvas): each
design sits on the lattice, the pictures render with and without the Tip, keep dark
space round them, use the width of the gable, don't shift when bulbs die, and the trim
follows the hardware (the Eaves sparkle, the Accent washes the wall in one colour)."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from goveelights import addrun  # noqa: E402
from goveelights.effects_ext import hexcanvas as hx  # noqa: E402
from goveelights.effects_ext import peak_pictures as pp  # noqa: E402
from goveelights.layout import Layout  # noqa: E402
from goveelights.scene import Scene  # noqa: E402

OWNER = ROOT / "layouts" / "house.json"
EXAMPLE = ROOT / "layouts" / "examples" / "gable-house.json"


def house(tip=False, dead=None, path=OWNER):
    from preview import house_layout
    L = house_layout(path)
    if tip:
        L = addrun.layout_with_tip(L)
    if dead:
        runs = addrun.copy_runs(L)
        for r in runs:
            r.dead = set(dead.get(r.name, ()))
        L = Layout(runs, L.zone_specs, L.aliases)
    return L


def scene(style, **params):
    d = json.loads((ROOT / "presets" / f"peak-{style}.json").read_text(encoding="utf-8"))
    d["layers"][0]["params"].update(params)
    return Scene.from_dict(d)


def frames(style, L, times, **params):
    sc = scene(style, **params)
    want, out, k = sorted(times), {}, 0
    while len(out) < len(want):
        t = k / 20
        cols = sc.render(t, L)
        for w in want:
            if w not in out and t >= w - 1e-9:
                out[w] = cols
        k += 1
    return [out[t] for t in times]


TIMES = [0.5, 2.0, 3.4, 5.6, 7.2, 9.95, 11.5]
DESIGNS = [pp.STAR, pp.STAR_TIP, pp.WREATH, pp.WREATH_TIP, pp.SNOWMAN, pp.SNOWMAN_TIP, pp.GINGER,
           pp.GINGER_TIP, pp.BOX, pp.BOW, pp.BOW_TIP, pp.RUDOLPH, pp.RUDOLPH_TIP, pp.JOY_STAR,
           pp.JOY_STAR_TIP, pp.STAR_FAR, {pp.LID_ROW: pp.LID}] + list(pp.LETTERS.values())


@unittest.skipUnless(OWNER.exists(), "the owner's layout is not in this copy")
class HoneycombPictures(unittest.TestCase):
    def test_designs_sit_on_the_lattice(self):
        # every bulb a design names is a bulb of the house's screen (with the Tip), so
        # nothing is silently dropped or rounded to a neighbour
        hc = hx.get(house(tip=True))
        for k, d in enumerate(DESIGNS):
            for r, spans in d.items():
                for a, b, ch in spans:
                    for j in range(int(round(b - a)) + 1):
                        with self.subTest(design=k, row=r, x=a + j):
                            self.assertIsNotNone(hc.cell(r, a + j))

    def test_designs_are_symmetric(self):
        # the pictures are mirror-symmetric about the centre line (J is a letter, the
        # snowman's carrot points one way)
        for k, d in enumerate([pp.STAR, pp.STAR_TIP, pp.WREATH, pp.WREATH_TIP, pp.GINGER_TIP, pp.BOX,
                               pp.BOW, pp.BOW_TIP, pp.RUDOLPH, pp.RUDOLPH_TIP, pp.LETTERS["O"],
                               pp.LETTERS["Y"], pp.JOY_STAR, pp.JOY_STAR_TIP]):
            for r, spans in d.items():
                xs = sorted(a + j for a, b, _ in spans for j in range(int(round(b - a)) + 1))
                self.assertEqual(xs, sorted(-x for x in xs), (k, r))

    def test_render_with_and_without_the_tip(self):
        for tip in (False, True):
            L = house(tip=tip)
            hc = hx.get(L)
            screen = set(hc.live())
            for style in pp.STYLES:
                with self.subTest(tip=tip, style=style):
                    for cols in frames(style, L, TIMES):
                        self.assertEqual(len(cols), L.n)
                        self.assertTrue(all(0.0 <= v <= 1.0 for c in cols for v in c))
                        dark = sum(1 for i in screen if max(cols[i]) < 0.05)
                        self.assertGreater(dark, len(screen) * 0.2, "dark space round the picture")

    def test_pictures_are_bold_and_in_true_proportion(self):
        # Round 15: on the house the rows hang hc.aspect (about 0.66, hexcanvas's one source
        # of truth) of a bulb spacing apart, so a picture's width in bulbs over its height
        # in rows * aspect is its real shape from the street. The first pictures ran 3 to
        # 3.6 times as wide as tall (the owner's wreath read as a flat oval); now each is
        # between upright and twice as wide (a gingerbread house, antlers), and the round
        # ones are near round. The picture's own bulbs are fully lit (no dim half-lit
        # edges); context round it (far-off stars, flakes, glitter) stays faint or small.
        for tip in (False, True):
            L = house(tip=tip)
            hc = hx.get(L)
            self.assertAlmostEqual(hc.aspect, hx.PEAK_ASPECT, places=2)
            for style in pp.STYLES:
                cols = frames(style, L, [1.2])[0]
                lit = [c for c in hc.cells if c.i is not None and max(cols[c.i]) > 0.05]
                fig = [c for c in lit if abs(c.x) <= 5]
                with self.subTest(tip=tip, style=style):
                    strong = [c for c in fig if max(cols[c.i]) >= 0.45]
                    self.assertGreater(len(strong), 0.8 * len(fig))
                    self.assertGreaterEqual(len({c.r for c in strong}), 5)
                    # nothing bright far out on the wide rows but the ornament's little baubles
                    far = [c for c in lit if abs(c.x) > 5 and max(cols[c.i]) >= 0.45]
                    self.assertLessEqual(len(far), 8 if style == "ornament" else 0)

    def test_designs_in_true_proportion(self):
        # the designs' full spans (not clipped to the middle of the gable, so a design that
        # grew wider is caught), with and without their Tip parts, at the layout's aspect
        a = hx.get(house()).aspect
        rud = {**pp.RUDOLPH, **pp.RUDOLPH_TIP}
        cases = {
            "wreath": (pp.WREATH, 1.5), "wreath tip": ({**pp.WREATH, **pp.WREATH_TIP}, 1.5),
            "star": (pp.STAR, 1.5), "star tip": (pp.STAR_TIP, 1.5),
            "J": (pp.LETTERS["J"], 1.5), "O": (pp.LETTERS["O"], 1.5), "Y": (pp.LETTERS["Y"], 1.5),
            "snowman": (pp.SNOWMAN, 2.0), "snowman tip": ({**pp.SNOWMAN, **pp.SNOWMAN_TIP}, 2.0),
            "gingerbread": (pp.GINGER, 2.0), "gingerbread tip": ({**pp.GINGER, **pp.GINGER_TIP}, 2.0),
            "present": ({**pp.BOX, pp.LID_ROW: pp.LID, **pp.BOW}, 2.0),
            "rudolph": (pp.RUDOLPH, 2.0), "rudolph tip": (rud, 2.0),
        }
        for name, (d, most) in cases.items():
            xs = [a0 + j for sp in d.values() for a0, b, _ in sp for j in range(int(round(b - a0)) + 1)]
            w = max(xs) - min(xs) + 1
            h = (max(d) - min(d)) * a + 1
            with self.subTest(design=name):
                self.assertGreaterEqual(w / h, 0.75)
                self.assertLessEqual(w / h, most)

    def test_round_designs_measure_round(self):
        # the designs themselves (no animation): the wreath's ring and the O
        a = hx.get(house()).aspect
        for d in (pp.WREATH, pp.LETTERS["O"]):
            xs = [a0 + j for sp in d.values() for a0, b, _ in sp for j in range(int(round(b - a0)) + 1)]
            w = max(xs) - min(xs) + 1
            h = (max(d) - min(d)) * a + 1               # rows apart, plus a bulb's own size
            self.assertLessEqual(abs(w / h - 1), 0.5)

    def test_pictures_use_the_canvas_aspect(self):
        # no private copy of the row gap: hexcanvas's aspect is the one source of truth
        self.assertFalse(hasattr(pp, "STREET_ROW"))

    def test_rudolphs_face_is_one_bright_fur_at_rest(self):
        # his face must read between the antlers and the nose at night: every face bulb
        # (the cheeks framing the nose too) is the bright fur while the nose rests, never
        # the old dark brown that barely showed on the house
        for tip in (False, True):
            L = house(tip=tip)
            hc = hx.get(L)
            face = [c for c, ch in pp._cells(hc, pp.RUDOLPH) if ch == "F" and c.i is not None]
            g = {"nose": 0.6, "blink": 0.0}
            out = pp._rudolph(hc, 2.0, g)
            with self.subTest(tip=tip):
                self.assertEqual(len(face), 11)
                for c in face:
                    col = out[c.i]
                    for v, f in zip(col, pp.FUR):
                        self.assertAlmostEqual(v, f, places=3)
                self.assertGreaterEqual(pp.FUR[0], 0.9)
                self.assertGreaterEqual(pp.FUR[1], 0.38)

    def test_the_tip_adds_to_the_picture(self):
        L = house(tip=True)
        hc = hx.get(L)
        tip = [c.i for c in hc.cells if c.tip and c.i is not None]
        for style in pp.STYLES:
            lit = set()
            for cols in frames(style, L, TIMES):
                lit |= {i for i in tip if max(cols[i]) > 0.3}
            with self.subTest(style=style):
                self.assertGreaterEqual(len(lit), 4)

    def test_dead_bulbs_shift_nothing(self):
        # the frame on every working bulb of the screen is the same when some die
        dead = {"Peak": {0, 26, 27, 60, 100}}
        for tip in (False, True):
            a_L, b_L = house(tip=tip), house(tip=tip, dead=dead)
            a_hc, b_hc = hx.get(a_L), hx.get(b_L)
            for style in pp.STYLES:
                fa = frames(style, a_L, [1.0, 6.0])
                fb = frames(style, b_L, [1.0, 6.0])
                for ca, cb in zip(fa, fb):
                    ka = {c.key: ca[c.i] for c in a_hc.cells if c.i is not None}
                    kb = {c.key: cb[c.i] for c in b_hc.cells if c.i is not None}
                    with self.subTest(tip=tip, style=style):
                        self.assertEqual(len(kb), len(ka) - 5)
                        self.assertEqual({k: v for k, v in ka.items() if k in kb}, kb)

    def test_accent_washes_and_eaves_sparkle(self):
        L = house()
        acc = [p.i for p in L.pixels if p.run == "Accent"]
        eav = [p.i for p in L.pixels if p.run == "Eaves"]
        for style in pp.STYLES:
            cols = frames(style, L, [4.0])[0]
            with self.subTest(style=style):
                # the Accent: one colour, evenly (it washes the siding)
                hues = {tuple(round(v / max(1e-6, max(cols[i])), 1) for v in cols[i]) for i in acc}
                self.assertLessEqual(len(hues), 2)
                self.assertTrue(all(max(cols[i]) > 0.2 for i in acc))
                # the Eaves: lit, with some variety along them
                self.assertTrue(all(max(cols[i]) > 0.02 for i in eav))
                self.assertGreaterEqual(len({tuple(round(v, 2) for v in cols[i]) for i in eav}), 2)

    def test_other_targets(self):
        # the Peak alone, the porch alone (no zigzag: it gets the trim), a dead-bulb-free
        # example house
        for target in ("Peak", "Accent", ["Peak", "Accent"]):
            for style in pp.STYLES:
                d = {"layers": [{"effect": "peak_picture", "target": target, "params": {"style": style}}]}
                cols = Scene.from_dict(d).render(2.0, house())
                self.assertTrue(all(0.0 <= v <= 1.0 for c in cols for v in c))
        L = house(path=EXAMPLE)
        for style in pp.STYLES:
            cols = frames(style, L, [2.0])[0]
            self.assertTrue(any(max(c) > 0.5 for c in cols), style)


@unittest.skipUnless(OWNER.exists(), "the owner's layout is not in this copy")
class ReviewFixes(unittest.TestCase):
    """Round 13 review: Rudolph's nose stays bright, the wreath's hole stays dark, the
    JOY burst leaves room round its star."""

    def test_rudolph_nose_is_bright_all_cycle_and_redder_than_his_muzzle(self):
        for tip in (False, True):
            L = house(tip=tip)
            hc = hx.get(L)
            nose = [c.i for c, ch in pp._cells(hc, pp.RUDOLPH) if ch == "N"]
            muzzle = [c.i for c, ch in pp._cells(hc, pp.RUDOLPH)
                      if ch == "F" and c.r <= 1 and abs(c.x) < 2.6]
            ts = [k * 0.1 for k in range(25)]
            fr = frames("rudolph", L, ts)
            peak = max(cols[i][0] for cols in fr for i in nose)
            for t, cols in zip(ts, fr):
                with self.subTest(tip=tip, t=t):
                    n = min(cols[i][0] for i in nose)
                    self.assertGreaterEqual(n, 0.75 * peak)
                    # Round 15: the nose stands out by its HUE, not by a dark muzzle (on the
                    # house a dark brown face was invisible): the nose is pure red, the
                    # cheeks framing it stay caramel fur, never a second red nose
                    for i in nose:
                        self.assertLessEqual(cols[i][1], 0.1 * cols[i][0])
                    for i in muzzle:
                        self.assertGreaterEqual(cols[i][1], 0.2 * cols[i][0])

    def test_wreath_hole_is_dark_and_the_bow_sits_on_the_ring(self):
        for r, spans in pp.WREATH.items():
            for a, b, ch in spans:
                if ch in "RK":
                    self.assertLessEqual(r, 1, "the bow sits on the bottom of the ring")
        for tip in (False, True):
            L = house(tip=tip)
            hc = hx.get(L)
            # (round 15: the ring is true-size, six rows by six bulbs round a hole of 7)
            hole = [c.i for c in hc.cells if c.i is not None and 1 <= c.r <= 3 and abs(c.x) <= 1]
            self.assertGreaterEqual(len(hole), 7)
            for cols in frames("wreath", L, TIMES):
                self.assertTrue(all(max(cols[i]) < 0.02 for i in hole))

    def test_joy_burst_is_sparse_and_its_star_stays_bright(self):
        L = house()
        hc = hx.get(L)
        scr = set(hc.live())
        star = {c.i for c, _ in pp._cells(hc, pp.JOY_STAR)}
        b0 = 3 * pp.JOY_STEP
        ts = [b0 + pp.JOY_BURST * u for u in (0.15, 0.3, 0.45, 0.6, 0.7)]
        ref = max(max(c) for c in frames("star", L, [1.2])[0])
        for t, cols in zip(ts, frames("joy", L, ts)):
            with self.subTest(t=t):
                lit = [i for i in scr - star if max(cols[i]) > 0.05]
                self.assertLessEqual(len(lit), 0.4 * len(scr - star))
                self.assertTrue(all(max(cols[i]) >= 0.8 * ref for i in star))


if __name__ == "__main__":
    unittest.main()
