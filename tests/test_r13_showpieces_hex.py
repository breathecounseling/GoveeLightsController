"""Round 13: the showpieces redrawn for the Peak's honeycomb (the triangle of offset rows):
the turkey fan, harvest sunset and sunburst, the falling star, the sleigh's grand tour and
candlelight. Every picture renders with and without the Tip, stays in range, keeps dark
space round its shapes, and does not move when a bulb on the Peak dies."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from goveelights import addrun  # noqa: E402
from goveelights.effects_ext import hexcanvas as hx  # noqa: E402
from goveelights.effects_ext import showpieces as sp  # noqa: E402
from goveelights.layout import Layout  # noqa: E402
from goveelights.scene import Scene  # noqa: E402

OWNER = ROOT / "layouts" / "house.json"
HAS_OWNER = OWNER.exists()
PRESETS = ["turkey-fan", "harvest-sunset", "candy-cane-chase", "orange-crush", "falling-star", "hail-mary",
           "sleigh-grand-tour", "gratitude-candlelight"]
DEAD = {"Peak": {0, 26, 27, 60, 101}}


def owner(dead=None, tip=False):
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


def preset(pid):
    return json.loads((ROOT / "presets" / f"{pid}.json").read_text(encoding="utf-8"))


def frames(d, L, n, fps=20.0):
    sc = Scene.from_dict(d)
    return [sc.render(k / fps, L) for k in range(n)]


def peak_view(L, cols):
    """{cell key: colour} over the Peak (and Tip) screen's working bulbs."""
    hc = hx.get(L, list(range(L.n)))
    return {c.key: tuple(round(v, 9) for v in cols[c.i]) for c in hc.cells if c.i is not None}


@unittest.skipUnless(HAS_OWNER, "the owner's layout is not in this copy")
class HoneycombShowpieces(unittest.TestCase):
    def test_presets_render_in_range_with_and_without_the_tip(self):
        for tip in (False, True):
            L = owner(tip=tip)
            for pid in PRESETS:
                with self.subTest(preset=pid, tip=tip):
                    for cols in frames(preset(pid), L, 70)[::7]:
                        self.assertEqual(len(cols), L.n)
                        self.assertTrue(all(0.0 <= c <= 1.0 for px in cols for c in px))

    def test_pictures_keep_dark_space_and_few_colours(self):
        L = owner()
        peak = L.indices("Peak")
        for pid, t in (("turkey-fan", 120), ("orange-crush", 80), ("candy-cane-chase", 80),
                       ("gratitude-candlelight", 60)):
            with self.subTest(preset=pid):
                cols = frames(preset(pid), L, t)[-1]
                dark = sum(1 for i in peak if max(cols[i]) < 0.03)
                lit = sum(1 for i in peak if max(cols[i]) > 0.3)
                self.assertGreater(dark, 12, "dark lines / space between the shapes")
                self.assertGreater(lit, 15)
                hues = {tuple(round(v / max(cols[i]) * 4) for v in cols[i]) for i in peak if max(cols[i]) > 0.3}
                self.assertLessEqual(len(hues), 9, hues)

    def test_turkey_bird_and_feather_gaps(self):
        L = owner()
        hc = hx.get(L, L.indices("Whole front gable"))
        plan = sp._turkey_plan(hc, 7)
        kinds = [p[0] for p in plan.values()]
        self.assertGreaterEqual(kinds.count("head"), 4)
        self.assertGreaterEqual(kinds.count("wattle"), 2)
        self.assertGreaterEqual(kinds.count("wattle"), 1)
        self.assertGreaterEqual(kinds.count("body"), 10)
        self.assertGreaterEqual(kinds.count("gap"), 12)
        # the feathers reach the edge on both sides, mirrored
        f = {p[1] for p in plan.values() if p[0] in ("f", "tip")}
        self.assertGreaterEqual(len(f), 6)
        cols = frames({"layers": [{"effect": "fan", "target": "Peak",
                                   "params": {"style": "turkey", "glints": 0, "speed": 0}}]}, L, 120)[-1]
        for c in hc.cells:
            if plan[c.key][0] in ("gap", "halo"):
                self.assertEqual(cols[c.i], (0.0, 0.0, 0.0))

    def test_sunburst_wedges_reach_the_edge_with_dark_lines(self):
        L = owner()
        hc = hx.get(L, L.indices("Peak"))
        for n in (6, 7):
            gaps = set()
            for a in sp._rim_angles(hc, (0.0, -1.0), n):
                g = sp._ray(hc, 0.0, -1.0, a, 1.0)
                self.assertGreaterEqual(len(g), 4)
                gaps |= g
            self.assertTrue(gaps & sp._rim(hc), "the dark lines run out to the edge")

    def test_sixty_degree_ray_is_a_straight_line(self):
        hc = hx.get(owner(), None)
        cells = sorted(sp._ray(hc, 0.0, 0.0, 60.0, 0.5))
        self.assertEqual([d - r for r, d in cells], [0] * len(cells))

    def test_falling_star_is_a_bold_streak_on_a_lattice_diagonal(self):
        for tip in (False, True):
            L = owner(tip=tip)
            hc = hx.get(L, list(range(L.n)))
            sc = Scene.from_dict(preset("falling-star"))
            best = []
            for k in range(60):
                cols = sc.render(k / 20, L)
                lit = [c for c in hc.cells if c.i is not None and max(cols[c.i]) > 0.5]
                if len(lit) > len(best):
                    best = lit
            with self.subTest(tip=tip):
                self.assertGreaterEqual(len(best), 6)
                rows = {}
                for c in best:
                    rows.setdefault(c.r, []).append(c.d)
                self.assertTrue(all(len(v) <= 2 for v in rows.values()), rows)        # two bulbs wide
                lead = sorted((r, min(v)) for r, v in rows.items())
                steps = {(d1 - d0) for (r0, d0), (r1, d1) in zip(lead, lead[1:]) if r1 == r0 + 1}
                self.assertEqual(len(steps), 1)                                     # one straight line
                self.assertIn(abs(steps.pop()), (1,))                               # at 60 degrees
                dark = sum(1 for c in hc.cells if c.i is not None and max(cols[c.i]) < 0.03)
                self.assertGreater(dark, len(hc.cells) // 2, "over black")

    def test_sleigh_flies_across_the_peak(self):
        for tip in (False, True):
            L = owner(tip=tip)
            hc = hx.get(L, list(range(L.n)))
            sc = Scene.from_dict(preset("sleigh-grand-tour"))
            xs = []
            for k in range(0, 200, 2):
                cols = sc.render(k / 20, L)
                red = [c for c in hc.cells if c.i is not None and cols[c.i][0] > 0.7 and cols[c.i][1] < 0.15]
                gold = [c for c in hc.cells if c.r == 0 and c.i is not None and cols[c.i][0] > 0.7
                        and 0.4 < cols[c.i][1] < 0.85 and cols[c.i][2] < 0.3]
                if len(red) >= 4 and len(gold) >= 4:
                    xs.append(sum(c.x for c in gold) / len(gold))
            with self.subTest(tip=tip):
                self.assertGreater(len(xs), 5, "the sleigh shows on the Peak")
                self.assertGreater(max(xs) - min(xs), 8, "and crosses it")

    def test_candles_three_flames_off_peak_unchanged(self):
        L = owner()
        hc = hx.get(L, list(range(L.n)))
        plan = sp._candle_plan(hc)
        self.assertEqual(len({k for part, k in plan.values() if part == "C"}), 3)
        # off the zigzag every bulb glows exactly as it did before the candles were drawn
        # (text-give-thanks and turkey-fan use candlelight as their base layer)
        d = {"layers": [{"effect": "candlelight", "params": {"swell_every": 12}}]}
        off = [i for i in range(L.n) if hc.of(i) is None]
        a = frames(d, L, 30)[-1]
        real = sp.hx.get
        sp.hx.get = lambda *a_, **k_: None
        try:
            b = frames(d, L, 30)[-1]
        finally:
            sp.hx.get = real
        for i in off:
            self.assertEqual(a[i], b[i])
        self.assertNotEqual([a[c.i] for c in hc.cells if c.i is not None], [b[c.i] for c in hc.cells if c.i is not None])
        self.assertTrue(all(max(a[i]) > 0.1 for i in off))

    def test_sunset_comes_back_after_it_sets(self):
        # an Ambient preset runs for hours: long after the first sunset the Peak must still
        # carry a bright, shaped picture most of the time (not a dim wash for good)
        d = preset("harvest-sunset")
        sink = d["layers"][-1]["params"]["sink"]
        for tip in (False, True):
            L = owner(tip=tip)
            hc = hx.get(L, list(range(L.n)))
            sc = Scene.from_dict(d)
            sc.render(0.0, L)
            good = total = 0
            for k in range(int(2 * sink * 4), int(3.5 * sink * 4)):
                cols = sc.render(k / 4, L)
                total += 1
                bright = sum(1 for c in hc.cells if c.i is not None and max(cols[c.i]) > 0.5)
                dark = sum(1 for c in hc.cells if c.i is not None and max(cols[c.i]) < 0.05)
                if bright >= 10 and dark >= 4:
                    good += 1
            with self.subTest(tip=tip):
                self.assertGreater(good / total, 0.7, (good, total))
            at = Scene.from_dict(d)
            at.render(0.0, L)
            cols = at.render(2 * sink, L)
            with self.subTest(tip=tip, t=2 * sink):
                self.assertGreaterEqual(sum(1 for c in hc.cells if c.i is not None and max(cols[c.i]) > 0.5), 10)

    def test_falling_star_peak_keeps_calm_stars_between_falls(self):
        for pid in ("falling-star", "hail-mary"):
            for tip in (False, True):
                L = owner(tip=tip)
                hc = hx.get(L, list(range(L.n)))
                sc = Scene.from_dict(preset(pid))
                lit = 0
                n = 400
                for k in range(n):
                    cols = sc.render(k / 20, L)
                    if sum(1 for c in hc.cells if c.i is not None and max(cols[c.i]) > 0.1) >= 4:
                        lit += 1
                with self.subTest(preset=pid, tip=tip):
                    self.assertGreater(lit / n, 0.95)
        L = owner()
        hc = hx.get(L, list(range(L.n)))
        calm = sp._calm_stars(hc, 3, 1.0)
        self.assertGreaterEqual(len(calm), 5)
        self.assertTrue(all(sp._hex_dist(a, b) >= 3 for a in calm for b in calm if a is not b))
        cols = frames({"layers": [{"effect": "falling_star", "params": {"every": 120}}]}, L, 10)[-1]
        on = [c for c in hc.cells if c.i is not None and max(cols[c.i]) > 0.02]
        self.assertTrue(on and all(max(cols[c.i]) <= 0.26 for c in on), "dim, calm stars")
        cols = frames({"layers": [{"effect": "falling_star", "params": {"every": 120, "stars": 0}}]}, L, 10)[-1]
        self.assertTrue(all(max(cols[c.i]) < 0.02 for c in hc.cells if c.i is not None), "stars 0: black")

    def test_candy_cane_hub_stands_apart_from_the_red(self):
        p = [ly for ly in preset("candy-cane-chase")["layers"] if ly["effect"] == "fan"][0]["params"]
        self.assertNotEqual(p["hub"].lower(), p["colors"][0].lower())
        self.assertGreater(int(p["hub"][3:5], 16), 0xc0, "a pale hub, not another red")

    def test_dead_bulbs_do_not_shift_the_pictures(self):
        L, Ld = owner(), owner(dead=DEAD)
        cases = [("fan", {"style": "turkey"}, 100), ("fan", {"style": "sunset"}, 60),
                 ("fan", {"style": "sunburst", "intro": 0}, 40), ("candlelight", {}, 40),
                 ("falling_star", {"stars": 0}, 28), ("sleigh_tour", {"stars": 0, "dust": 0, "sparkle": 0}, 90)]
        for eff, params, n in cases:
            with self.subTest(effect=eff, **{k: str(v) for k, v in params.items()}):
                d = {"layers": [{"effect": eff, "target": "all", "params": params}]}
                a = peak_view(L, frames(d, L, n)[-1])
                b = peak_view(Ld, frames(d, Ld, n)[-1])
                self.assertLess(len(b), len(a))
                self.assertTrue(any(max(v) > 0.3 for v in b.values()))
                self.assertEqual({k: v for k, v in b.items() if a.get(k) != v}, {})


if __name__ == "__main__":
    unittest.main()
