"""Round 11: the Christmas-season presets polished for the street (warm whites from RGB,
no muddy mixes, the Peak used as a tree/sky) render deterministically, stay lit and extend
to a fourth run."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from goveelights.layout import Layout, Run
from goveelights.scene import Scene

from test_r10_showpieces import EXAMPLE, HOUSE, digest, layout, render

ROOT = Path(__file__).resolve().parent.parent
CHANGED = ["porch-fireworks", "grand-finale", "christmas-twinkle", "snowy-evening", "champagne-fizz",
           "advent-calendar", "sleigh-grand-tour", "falling-star", "snow-piling-up", "christmas-tree"]


def preset(pid: str) -> dict:
    return json.loads((ROOT / "presets" / f"{pid}.json").read_text(encoding="utf-8"))


def layouts():
    out = [("example", layout(EXAMPLE)), ("example + 4th run", layout(EXAMPLE, 100))]
    if HOUSE.exists():
        out += [("house", layout(HOUSE)), ("house + 4th run", layout(HOUSE, 100))]
    return out


class PolishedPresets(unittest.TestCase):
    def test_deterministic_and_lit_everywhere(self):
        for name, L in layouts():
            for pid in CHANGED:
                with self.subTest(layout=name, preset=pid):
                    a = render(preset(pid), L, 120)
                    self.assertEqual(digest(a), digest(render(preset(pid), L, 120)))
                    for cols in a:
                        self.assertEqual(len(cols), L.n)
                        self.assertTrue(all(0.0 <= c <= 1.0 for px in cols for c in px))
                    lit = sum(1 for px in a[-1] if max(px) > 0.02)
                    self.assertGreater(lit, L.n // 3, "most of the house should be lit")

    def test_no_dim_rainbow_on_the_porch_fireworks_eaves(self):
        g = [l for l in preset("porch-fireworks")["layers"] if l["effect"] == "glitter"][0]["params"]
        self.assertEqual(g["level"], 0)
        self.assertLessEqual(len(g["colors"]), 3)

    def test_warm_white_rooflines_match_the_tree_lighting(self):
        ww = preset("warm-white")["layers"][0]["params"]["color"]
        for pid in ("christmas-tree", "advent-calendar"):
            self.assertEqual(preset(pid)["layers"][0]["params"]["background"], ww, pid)
        self.assertEqual(preset("snowy-evening")["layers"][0]["params"]["colors"][0], ww)

    def test_whole_house_looks_reach_a_new_run_by_themselves(self):
        # these used to leave a fourth run dark (or, worse, with only a few twinkles, which
        # also stopped "copy a run" from filling it in)
        L = layout(EXAMPLE, 100)
        four = [p.i for p in L.pixels if p.run == "Run 4"]
        for pid in ("christmas-twinkle", "snowy-evening", "porch-fireworks", "grand-finale", "snow-piling-up",
                    "sleigh-grand-tour", "falling-star"):
            with self.subTest(preset=pid):
                cols = render(preset(pid), L, 60)[-1]
                self.assertGreater(sum(1 for i in four if max(cols[i]) > 0.02), 90)

    def test_copy_a_run_fills_the_rest(self):
        base = layout(EXAMPLE)
        runs = [Run(mac=r.mac, name=r.name, count=r.count, reverse=r.reverse, hidden=set(r.hidden),
                    geometry=r.geometry) for r in base.runs]
        extra = Run(mac="AA:BB:CC:DD:EE:FF:00:11", name="Run 4", count=80)
        extra.follows = "Lower roofline"
        L = Layout(runs + [extra], base.zone_specs, base.aliases)
        four = [p.i for p in L.pixels if p.run == "Run 4"]
        for pid in ("christmas-tree", "advent-calendar", "champagne-fizz"):
            with self.subTest(preset=pid):
                sc = Scene.from_dict(preset(pid))
                cols = [sc.render(k / 20, L) for k in range(60)][-1]
                self.assertGreater(sum(1 for i in four if max(cols[i]) > 0.02), 70)


def _brown_share(cols_frames, idx):
    import colorsys
    n = b = 0
    for cols in cols_frames:
        for i in idx:
            h, s, v = colorsys.rgb_to_hsv(*cols[i])
            n += 1
            if 0.03 <= v < 0.45 and 15 / 360 <= h <= 50 / 360 and s > 0.35:
                b += 1
    return b / n


class ReviewFixes(unittest.TestCase):
    def test_champagne_fizz_reads_pale_gold_not_brown(self):
        L = layout(HOUSE if HOUSE.exists() else EXAMPLE)
        fr = render(preset("champagne-fizz"), L, 300)
        frames = [fr[k] for k in range(60, 300, 6)]
        runs = {}
        for p in L.pixels:
            runs.setdefault(p.run, []).append(p.i)
        for run, idx in runs.items():
            with self.subTest(run=run):
                self.assertLess(_brown_share(frames, idx), 0.15)

    def test_grand_finale_roofline_is_white_gold_red(self):
        allowed = {"#ffffff", "#ff0010", "#ffc040", "#ffb020", "#ff1020", "#fff0c0"}
        for l in preset("grand-finale")["layers"]:
            t = l.get("target")
            t = t if isinstance(t, list) else [t]
            if l["effect"] in ("fireworks", "bursts") and "Whole front gable" not in t:
                with self.subTest(target=t):
                    cols = {c.lower() for c in l["params"]["colors"]}
                    self.assertLessEqual(len(cols - allowed), 1, cols - allowed)

    def test_advent_closed_doors_are_not_read_as_off(self):
        # early December (few doors open) the closed-door tree must still read as lit
        d = preset("advent-calendar")
        L = layout(HOUSE if HOUSE.exists() else EXAMPLE)
        for l in d["layers"]:
            if l["effect"] == "advent":
                l["params"]["day"] = 3
        cols = render(d, L, 200)[-1]
        peak = [p.i for p in L.pixels if p.run == "Peak"] or list(range(L.n))
        mean = sum(max(cols[i]) for i in peak) / len(peak)
        self.assertGreaterEqual(mean, 0.22)
        self.assertGreaterEqual(d["layers"][1]["params"]["closed_level"], 0.45)


if __name__ == "__main__":
    unittest.main()
