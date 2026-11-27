"""The Christmas tree drawn for the Peak's honeycomb (round 13 revamp): a tiered fir with
60-degree lattice sides, dark sky and a black margin round it, a trunk on the bottom row,
bold ornament pairs in two or three colours, a garland on a lattice diagonal and the star
on the top row (the Tip's apex when there is one). Works with and without the Tip; a dead
bulb never moves anything."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

from goveelights import addrun  # noqa: E402
from goveelights.effects import EFFECTS  # noqa: E402
from goveelights.effects_ext import tree as T  # noqa: E402
from goveelights.fx import Ctx  # noqa: E402
from goveelights.layout import Layout  # noqa: E402
from goveelights.scene import Scene  # noqa: E402

HOUSE = ROOT / "layouts" / "house.json"
PRESETS = ["christmas-tree", "golden-tree", "icy-tree", "tree-lighting-ceremony", "spooky-tree", "sparkler-tree"]
QUIET = {"intro": 0, "burst_every": 0, "sky_level": 0, "sky_twinkle": 0, "shimmer": 0, "garland_sparkle": 0,
         "glints": 0}


def house(tip=False, dead=None):
    from preview import house_layout
    L = house_layout(HOUSE)
    if tip:
        L = addrun.layout_with_tip(L)
    if dead:
        runs = addrun.copy_runs(L)
        for r in runs:
            r.dead = set(dead.get(r.name, ()))
        L = Layout(runs, L.zone_specs, L.aliases)
    return L


def call(L, idx, P=None, state=None, clock=20.0, dt=0.05):
    fx = EFFECTS["tree"]
    return fx.fn(clock, Ctx(L, idx, clock, dt, 120, {}), fx.resolve(P or {}), {} if state is None else state)


def preset(pid):
    return json.loads((ROOT / "presets" / f"{pid}.json").read_text(encoding="utf-8"))


def lum(c):
    return max(c)


class Plan(unittest.TestCase):
    def test_tree_on_the_peak(self):
        L = house()
        p = T.tree_plan(L, L.indices("Whole front gable"))
        hc = p["hc"]
        self.assertEqual(hc.widths, [27, 23, 20, 16, 12, 10])
        cells = {i: hc.of(i) for i in p["leaf"]}
        # the star: the top row's middle two; no rays over a slender tree (round 15)
        self.assertEqual(sorted(hc.of(i).x for i in p["star"]), [-0.5, 0.5])
        self.assertTrue(all(hc.of(i).r == hc.top for i in p["star"] + p["rays"]))
        self.assertEqual(p["rays"], [])
        # the trunk: the bottom row's middle bulb under a slender tree (three under a wide
        # one), nothing else on the bottom row is tree
        self.assertEqual(sorted(hc.of(i).x for i in p["trunk"]), [0.0])
        wide = T.tree_plan(L, L.indices("Whole front gable"), 2.0)
        self.assertEqual(sorted(hc.of(i).x for i in wide["trunk"]), [-1.0, 0.0, 1.0])
        self.assertEqual(len(wide["rays"]), 2)
        self.assertTrue(all(c.r > 0 for c in cells.values()))
        # every row of boughs is centred, 60-degree sides: in true proportions (round 15)
        # the narrowest slope the lattice has, half a bulb a row
        widths = {}
        for c in cells.values():
            widths.setdefault(c.r, []).append(c.x)
        self.assertEqual([len(widths[r]) for r in sorted(widths)], [4, 3, 2, 1])
        for xs in widths.values():
            self.assertAlmostEqual(min(xs), -max(xs))
        # dark sky all round: the tree never touches the gable's sides
        for r, xs in widths.items():
            row = hc.row(r)
            self.assertGreaterEqual(min(xs) - row[0].x, 3)
            self.assertGreaterEqual(row[-1].x - max(xs), 3)
        self.assertEqual(len(p["rest"]), 24)                 # the gable outline frames it

    def test_tree_with_the_tip(self):
        L = house(tip=True)
        p = T.tree_plan(L, L.indices("Whole front gable"))
        hc = p["hc"]
        apex = L.run("Tip").pixels[-1].i
        self.assertIn(apex, p["star"])
        self.assertEqual(len(p["star"]), 3)                   # the apex and the two under it
        self.assertEqual(p["tiers"], 2)                       # three rows each, the lower steps back in
        self.assertEqual(len(p["leaf"]), 15)
        # on the Peak alone too
        q = T.tree_plan(L, L.indices("Peak"))
        self.assertIn(apex, q["star"])
        self.assertEqual(q["rest"], [])
        self.assertEqual(set(q["leaf"]), set(p["leaf"]))
        self.assertEqual(hc.top, 8)

    def test_no_zigzag_falls_back(self):
        L = house()
        for target in ("Eaves", "Porch", "Front gable outline"):
            self.assertIsNone(T.tree_plan(L, L.indices(target)), target)

    def test_ornaments_are_bold_pairs_apart(self):
        # on a wide tree (spread): a slender one has single-bulb ornaments (test_r15_*)
        for tip in (False, True):
            L = house(tip=tip)
            p = T.tree_plan(L, L.indices("Whole front gable"), 2.0)
            hc = p["hc"]
            for seed in range(12):
                orn = T.hex_ornaments(p, 0.18, seed, 3)
                self.assertTrue(orn)
                self.assertLessEqual(len(set(orn.values())), 3)
                cells = [hc.of(i) for i in orn]
                for a in cells:
                    mates = [b for b in cells if b is not a and b.r == a.r and abs(b.x - a.x) == 1]
                    self.assertEqual(len(mates), 1)            # each ornament is a pair
                    self.assertEqual(orn[mates[0].i], orn[a.i])  # of one colour
                    for b in cells:
                        if b is not a and b is not mates[0]:
                            self.assertGreater(T._hexdist(a, b), 2)
                self.assertTrue(all(i in p["leaf"] for i in orn))


class Render(unittest.TestCase):
    def test_silhouette_against_a_dark_sky(self):
        for tip in (False, True):
            L = house(tip=tip)
            idx = L.indices("Whole front gable")
            p = T.tree_plan(L, idx)
            out = call(L, idx, QUIET)
            for i in p["sky"]:
                self.assertEqual(out[i], (0.0, 0.0, 0.0))
            out = call(L, idx, {**QUIET, "sky_level": 0.06})
            for i in p["margin"]:
                self.assertEqual(out[i], (0.0, 0.0, 0.0))       # a black margin hugs the tree
            self.assertTrue(any(lum(out[i]) > 0.01 for i in p["sky"]))
            # boughs green, bright; the star the brightest, whitest thing
            out = call(L, idx, {**QUIET, "density": 0, "garland_style": "none"})
            for i in p["leaf"]:
                r, g, b = out[i]
                self.assertGreater(g, 0.3)
                self.assertGreater(g, 2 * r)
                self.assertGreater(g, 2 * b)
            for i in p["star"]:
                self.assertGreater(min(out[i][:2]), 0.5)
                self.assertGreater(sum(out[i]), max(sum(out[j]) for j in p["leaf"]))
            for i in p["trunk"]:
                r, g, b = out[i]
                self.assertGreater(r, g)
                self.assertGreater(r, 0.2)
            # the bottom row is the trunk and dark ground: no square block of green
            hc = p["hc"]
            lit = [c for c in hc.row(0) if lum(out[c.i]) > 0.05]
            self.assertEqual(len(lit), len(p["trunk"]))

    def test_star_on_the_peak_alone(self):
        for tip in (False, True):
            L = house(tip=tip)
            idx = L.indices("Peak")
            p = T.tree_plan(L, idx)
            out = call(L, idx, {"intro": 0, "burst_every": 0})
            self.assertTrue(p["star"])
            self.assertTrue(all(min(out[i][:2]) > 0.5 for i in p["star"]))
            self.assertTrue(all(out[i] == (0.0, 0.0, 0.0) for i in L.indices("Eaves")))

    def test_garland_swag_runs_on_a_lattice_diagonal(self):
        L = house(tip=True)
        idx = L.indices("Peak")
        P = {**QUIET, "density": 0, "garland": "#ff0000", "body": "#00ff00", "body2": "#00ff00",
             "garland_spacing": 30, "speed": 0, "spread": 2.0}
        p = T.tree_plan(L, idx, 2.0)
        out = call(L, idx, P)
        g = [p["hc"].of(i) for i in p["leaf"] if out[i][0] > 0.5]
        self.assertGreaterEqual(len(g), 3)
        us = {c.x - 0.5 * c.r for c in g}
        self.assertEqual(len(us), 1, us)                     # one straight 60-degree line
        self.assertEqual(len({c.r for c in g}), len(g))      # one bulb per row

    def test_strand_garland_lies_along_the_tier_hems(self):
        L = house()
        idx = L.indices("Peak")
        for spread in (0.0, 2.0):
            p = T.tree_plan(L, idx, spread)
            out = call(L, idx, {**QUIET, "density": 0, "garland": "#ff0000", "garland_style": "strand",
                                "spread": spread})
            rows = {p["hc"].of(i).r for i in p["leaf"] if out[i][0] > 0.3}
            self.assertIn(1, rows, spread)                   # the hems: each tier's lowest row
            self.assertLessEqual(rows, {1, 3}, spread)

    def test_intro_trunk_then_boughs_upwards_then_star(self):
        L = house(tip=True)
        idx = L.indices("Whole front gable")
        p = T.tree_plan(L, idx)
        hc = p["hc"]
        st = {}
        P = {"intro": 8, "burst_every": 0, "sky_twinkle": 0}

        def mean(out, ii):
            return sum(lum(out[i]) for i in ii) / len(ii)

        low = [i for i in p["leaf"] if hc.of(i).r == 1]
        high = [i for i in p["leaf"] if hc.of(i).r >= 5]
        outs = {}
        for k in range(0, 201):
            c = 100 + k * 0.05
            out = call(L, idx, P, st, clock=c)
            if k in (0, 10, 50, 200):
                outs[k] = out
        self.assertTrue(all(lum(outs[0][i]) == 0 for i in list(p["leaf"]) + p["star"]))
        self.assertGreater(mean(outs[10], p["trunk"]), 0.3)      # 0.5 s: the trunk is on
        self.assertGreater(mean(outs[50], low), 0.2)             # 2.5 s: the low boughs
        self.assertLess(mean(outs[50], high), 0.05)              # but not the top
        self.assertTrue(all(lum(outs[50][i]) < 0.02 for i in p["star"] + p["rest"]))
        self.assertGreater(mean(outs[200], high), 0.2)           # 10 s: lit, star on
        self.assertTrue(all(lum(outs[200][i]) > 0.5 for i in p["star"]))

    def test_ceremony_shows_the_unlit_tree_waiting(self):
        L = house()
        sc = Scene.from_dict(preset("tree-lighting-ceremony"))
        idx = L.indices("Whole front gable")
        p = T.tree_plan(L, idx)
        for k in range(41):
            out = sc.render(k / 20, L)
        high = [i for i in p["leaf"] if p["hc"].of(i).r >= 3]
        for i in high:                                       # dim green, not dark, not grey
            r, g, b = out[i]
            self.assertGreater(g, 0.01)
            self.assertLess(g, 0.2)
            self.assertGreater(g, 2 * max(r, b))

    def test_dead_bulbs_move_nothing(self):
        for tip in (False, True):
            dead = {"Peak": {0, 26, 30, 60, 100}}
            if tip:
                dead["Tip"] = {3}
            A, B = house(tip), house(tip, dead)
            for pid in PRESETS:
                d = preset(pid)                      # the tree layer (other effects aren't ours)
                d["layers"] = [layer for layer in d["layers"] if layer["effect"] == "tree"]
                sa, sb = Scene.from_dict(d), Scene.from_dict(d)
                for k in range(0, 160):
                    ca, cb = sa.render(k / 20, A), sb.render(k / 20, B)
                    if k % 40:
                        continue
                    # every working bulb of the front gable shows exactly what it showed
                    # before the others died
                    a = {(p.mac, p.local): ca[p.i] for p in A.pixels}
                    for i in B.indices("Whole front gable"):
                        p = B.pixels[i]
                        for x, y in zip(cb[i], a[(p.mac, p.local)]):
                            self.assertAlmostEqual(x, y, 9, (pid, tip, k, p.run, p.local))

    def test_presets_render_in_range_with_and_without_the_tip(self):
        for tip in (False, True):
            L = house(tip)
            idx = L.indices("Whole front gable")
            p = T.tree_plan(L, idx)
            for pid in PRESETS:
                sc = Scene.from_dict(preset(pid))
                for k in range(0, 400, 3):
                    cols = sc.render(k / 20, L)
                    self.assertEqual(len(cols), L.n)
                    self.assertTrue(all(0.0 <= v <= 1.0 for c in cols for v in c), pid)
                # after the intro: a lit tree, a star, mostly dark sky
                lit_leaf = sum(1 for i in p["leaf"] if lum(cols[i]) > 0.15) / len(p["leaf"])
                self.assertGreater(lit_leaf, 0.8, (pid, tip))
                self.assertTrue(all(lum(cols[i]) > 0.4 for i in p["star"]), (pid, tip))
                sky = sorted(lum(cols[i]) for i in p["sky"])
                self.assertLess(sky[len(sky) // 2], 0.15, (pid, tip))

    def test_presets_keep_ids_names_and_few_ornament_colours(self):
        names = {"christmas-tree": "Christmas tree", "golden-tree": "Golden tree", "icy-tree": "Icy tree",
                 "tree-lighting-ceremony": "Tree lighting ceremony", "spooky-tree": "Spooky tree",
                 "sparkler-tree": "Sparkler tree"}
        for pid, name in names.items():
            d = preset(pid)
            self.assertEqual(d["name"], name)
            trees = [layer for layer in d["layers"] if layer["effect"] == "tree"]
            self.assertEqual(len(trees), 1, pid)
            self.assertEqual(trees[0]["target"], "Whole front gable")
            oc = trees[0].get("params", {}).get("ornament_colors")
            if oc is not None:
                self.assertLessEqual(len(oc), 3, pid)
            for layer in d["layers"]:
                for k, v in layer.get("params", {}).items():
                    self.assertIn(k, {p.name for p in EFFECTS[layer["effect"]].params}, (pid, k))

    def test_example_house_and_extreme_params(self):
        from test_r10_showpieces import EXAMPLE, layout
        L = layout(EXAMPLE)
        idx = L.indices("Whole front gable") or L.indices("all")
        fx = EFFECTS["tree"]
        for prm in fx.params:
            vals = []
            if prm.kind in ("number", "int"):
                vals = [prm.min, prm.max]
            elif prm.kind == "choice":
                vals = prm.options
            elif prm.kind == "colors":
                vals = [[], ["#000000"], ["#ffffff"] * 9]
            for v in vals:
                st = {}
                for k, clock in enumerate((0.0, 0.0, 0.05, 1.0, 7.9, 30.0, 400.0)):
                    cols = call(L, idx, {prm.name: v}, st, clock, 0 if k == 0 else 0.05)
                    self.assertEqual(len(cols), L.n)
                    self.assertTrue(all(0.0 <= x <= 1.0 for c in cols for x in c), (prm.name, v))


class StreetLegibility(unittest.TestCase):
    """Review follow-ups: one star, a quiet sky, a fuller tree without the Tip, no ember bar."""

    def test_one_star_without_the_tip(self):
        # the outline's apex sits right over the small star: its halo must stay well under
        # the star, or it reads as a second star floating above the tree
        L = house()
        idx = L.indices("Whole front gable")
        p = T.tree_plan(L, idx)
        for clock in (6.0, 12.0, 20.0):
            out = call(L, idx, {"intro": 0, "burst_every": 0}, clock=clock)
            star = min(lum(out[i]) for i in p["star"])
            near = [lum(out[i]) for i in p["rest"] if p["dist"][i] < 3.5]
            self.assertTrue(near)
            self.assertLess(max(near), 0.3, clock)
            self.assertLess(max(near), star * 0.4, clock)

    def test_tip_keeps_the_full_halo(self):
        L = house(tip=True)
        idx = L.indices("Whole front gable")
        p = T.tree_plan(L, idx)
        out = call(L, idx, {"intro": 0, "burst_every": 0}, clock=12.0)
        self.assertGreater(max(lum(out[i]) for i in p["rest"] if p["dist"][i] < 1.0), 0.45)

    def test_far_stars_are_faint_and_few(self):
        self.assertEqual(EFFECTS["tree"].resolve({})["sky_twinkle"], 0.02)
        L = house()
        idx = L.indices("Whole front gable")
        p = T.tree_plan(L, idx)
        st, peak, lit = {}, 0.0, 0
        for k in range(400):
            out = call(L, idx, {"intro": 0, "burst_every": 0}, st, clock=10 + k * 0.05)
            sky = [lum(out[i]) for i in p["sky"]]
            peak = max(peak, max(sky))
            lit += sum(1 for v in sky if v > 0.08)
        self.assertLess(peak, 0.2)
        self.assertLess(lit / 400, 2.0)

    def test_christmas_and_golden_keep_true_proportions(self):
        # round 15: the owner's photos show the rows hang ~0.66 of a bulb apart, so the
        # spread-2 tree (boughs 10, 9 / 4, 3 without the Tip) was a mound ~4x wider than
        # tall; the presets now draw the slender tree, and spread still widens it on demand
        for pid in ("christmas-tree", "golden-tree"):
            tl = [la for la in preset(pid)["layers"] if la["effect"] == "tree"][0]
            self.assertNotIn("spread", tl["params"], pid)
        L = house()
        p = T.tree_plan(L, L.indices("Whole front gable"), 2.0)
        hc = p["hc"]
        widths = {}
        for i in p["leaf"]:
            widths.setdefault(hc.of(i).r, []).append(hc.of(i).x)
        self.assertEqual([len(widths[r]) for r in sorted(widths)], [8, 7, 2, 1])
        for r, xs in widths.items():
            self.assertGreaterEqual(min(xs) - hc.row(r)[0].x, 3)

    def test_spread_help_owns_up_to_half_steps(self):
        prm = {q.name: q for q in EFFECTS["tree"].params}["spread"]
        self.assertIn("half", prm.help)
        L = house()
        idx = L.indices("Whole front gable")
        self.assertEqual(sorted(T.tree_plan(L, idx, 1.0)["leaf"]), sorted(T.tree_plan(L, idx, 1.5)["leaf"]))

    def test_sparkler_embers_do_not_pile_up_on_the_bottom_row(self):
        for tip in (False, True):
            L = house(tip)
            p = T.tree_plan(L, L.indices("Whole front gable"))
            row0 = [c.i for c in p["hc"].row(0) if c.i is not None and c.i not in p["trunk"]]
            sc = Scene.from_dict(preset("sparkler-tree"))
            bright = sky = n = 0
            for k in range(600):
                f = sc.render(k * 0.05, L)
                if k >= 200:
                    bright += sum(1 for i in row0 if lum(f[i]) > 0.4)
                    sky += sum(1 for i in p["sky"] if lum(f[i]) > 0.3)
                    n += 1
            self.assertLess(bright / n, 2.5, tip)
            # at most about one sky bulb in eight lit by embers at a time (the round 15
            # slender tree leaves more of the Peak as sky)
            self.assertLess(sky / n / len(p["sky"]), 0.13, tip)


if __name__ == "__main__":
    unittest.main()
