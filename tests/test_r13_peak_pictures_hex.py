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
           pp.JOY_STAR_TIP, {3: pp.LID}] + list(pp.LETTERS.values())


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

    def test_pictures_are_bold_and_use_the_width(self):
        # most of a picture's bulbs are fully lit (no dim half-lit edges), and the picture
        # spans a good part of the gable's wide rows
        L = house()
        hc = hx.get(L)
        for style in pp.STYLES:
            cols = frames(style, L, [1.2])[0]
            lit = [c for c in hc.cells if c.i is not None and max(cols[c.i]) > 0.05]
            with self.subTest(style=style):
                strong = [c for c in lit if max(cols[c.i]) >= 0.45]
                self.assertGreater(len(strong), 0.8 * len(lit))
                span = max(c.x for c in strong) - min(c.x for c in strong)
                self.assertGreaterEqual(span, 8)
                self.assertGreaterEqual(len({c.r for c in strong}), 5)

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


if __name__ == "__main__":
    unittest.main()
