"""Round 15: trees and showpieces in TRUE proportions.

On the house the Peak's rows hang about 0.66 of a bulb spacing apart (hexcanvas.aspect,
from the owner's night photos), so a picture drawn one bulb per column and one row per
unit of height came out 2-3x too wide for its height (the owner: "distended"). The trees,
the turkey, the sun disc and the sunburst's hub, the falling star's head and the candles
are now measured in bulb spacings as they hang: a tree is taller than it is wide (narrow
on this flat gable: that is right), the sun and the hub are round, candles and flames
stand up. Every check runs with and without the Tip, and a dead bulb moves nothing."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from goveelights import addrun  # noqa: E402
from goveelights.effects import EFFECTS  # noqa: E402
from goveelights.effects_ext import hexcanvas as hx  # noqa: E402
from goveelights.effects_ext import showpieces as sp  # noqa: E402
from goveelights.effects_ext import tree as T  # noqa: E402
from goveelights.fx import Ctx  # noqa: E402
from goveelights.layout import Layout  # noqa: E402
from goveelights.scene import Scene  # noqa: E402

HOUSE = ROOT / "layouts" / "house.json"
TREES = ["christmas-tree", "golden-tree", "icy-tree", "spooky-tree", "sparkler-tree", "tree-lighting-ceremony"]
SHOWPIECES = ["turkey-fan", "harvest-sunset", "orange-crush", "candy-cane-chase", "falling-star",
              "sleigh-grand-tour", "gratitude-candlelight"]
DEAD = {"Peak": {0, 26, 27, 50, 60, 101}}


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


def gable(L):
    return L.indices("Whole front gable")


def size(hc, cells) -> tuple[float, float]:
    """(width, height) of a group of cells in bulb spacings as hung: each bulb is one
    spacing wide and one row (hc.aspect spacings) tall."""
    xs = [c.x for c in cells]
    rs = [c.r for c in cells]
    return (max(xs) - min(xs) + 1.0, (max(rs) - min(rs) + 1) * hc.aspect)


def preset(pid):
    return json.loads((ROOT / "presets" / f"{pid}.json").read_text(encoding="utf-8"))


class TrueCanvas(unittest.TestCase):
    def test_pictures_are_drawn_on_the_true_canvas(self):
        for tip in (False, True):
            L = house(tip)
            hc = hx.true(L, gable(L))
            self.assertTrue(hc.is_true)
            self.assertTrue(0.55 <= hc.aspect <= 0.75, hc.aspect)       # the photos: rows ~0.66 apart
            self.assertIs(T.tree_plan(L, gable(L))["hc"], hc)

    def test_street_aspect_in_the_contact_sheet_is_the_true_one(self):
        """The contact sheet's --street-aspect draws the rows hc.aspect bulb spacings apart, so
        the pictures are judged at the proportions the owner photographs."""
        import contact_sheet
        L = house()
        ii = L.indices("Peak")
        hc = hx.get(L, ii)
        ys = [sum(c.pos[1] for c in rw.cells) / rw.n for rw in hc.rows]
        dy = (ys[-1] - ys[0]) / (len(ys) - 1)
        self.assertAlmostEqual(contact_sheet.street_yex(L, ii) * dy / hc.pitch, hc.aspect, places=6)


class Trees(unittest.TestCase):
    def body(self, p):
        hc = p["hc"]
        keys = set(p["leaf_inner"]) | {hc.of(i).key for i in p["leaf"]} | {hc.of(i).key for i in p["trunk"]} \
            | {hc.of(i).key for i in p["star"]}
        return hc, [c for c in hc.cells if c.key in keys]

    def test_a_tree_is_taller_than_it_is_wide(self):
        # with the Tip: about 1.5x taller than wide (a fir); without it the Peak is only six
        # rows tall and the narrowest lattice slope makes it about square
        for tip, lo, hi in ((False, 0.9, 1.6), (True, 1.3, 2.2)):
            L = house(tip)
            hc, cells = self.body(T.tree_plan(L, gable(L)))
            w, h = size(hc, cells)
            self.assertTrue(lo <= h / w <= hi, (tip, w, h))
            self.assertLessEqual(w, 5.0, tip)                 # narrow on this flat gable: that is right
            # the boughs widen downwards, half a bulb a row at most (no row sprawls)
            rows = {}
            for c in cells:
                rows.setdefault(c.r, []).append(c.x)
            widths = [max(xs) - min(xs) + 1 for r, xs in sorted(rows.items()) if 0 < r < hc.top - (1 if tip else 0)]
            self.assertEqual(max(widths), widths[0], (tip, widths))
            for xs in rows.values():
                self.assertAlmostEqual(min(xs), -max(xs))         # centred

    def test_the_old_flat_mound_is_gone(self):
        """Before round 15 the no-Tip christmas tree was 10 bulbs wide and 4 rows of boughs:
        2.6 spacings tall, a mound ~4x wider than tall."""
        L = house()
        for pid in TREES:
            tl = [la for la in preset(pid)["layers"] if la["effect"] == "tree"][0]
            prm = EFFECTS["tree"].resolve(tl.get("params", {}))
            self.assertEqual(prm["spread"], 0.0, pid)
            hc, cells = self.body(T.tree_plan(L, gable(L), prm["spread"]))
            w, h = size(hc, cells)
            self.assertGreaterEqual(h / w, 0.9, pid)

    def test_tiers_step_back_in_with_the_tip(self):
        L = house(True)
        p = T.tree_plan(L, gable(L))
        hc = p["hc"]
        w = {}
        for i in p["leaf"]:
            w.setdefault(hc.of(i).r, []).append(hc.of(i).x)
        self.assertEqual([len(w[r]) for r in sorted(w, reverse=True)], [1, 2, 3, 2, 3, 4])
        self.assertEqual(p["tiers"], 2)
        self.assertEqual(sorted(hc.of(i).x for i in p["trunk"]), [0.0])     # a slender trunk
        self.assertEqual(p["rays"], [])                                     # no T-bar over a slender tree

    def test_spread_still_widens_the_tree(self):
        for tip in (False, True):
            L = house(tip)
            ws = []
            for s in (0.0, 1.0, 2.0):
                hc, cells = self.body(T.tree_plan(L, gable(L), s))
                ws.append(size(hc, cells)[0])
            self.assertLess(ws[0], ws[1], tip)
            self.assertLess(ws[1], ws[2], tip)
            wide = T.tree_plan(L, gable(L), 2.0)
            self.assertFalse(wide["slender"])
            self.assertTrue(wide["rays"])

    def test_slender_ornaments_are_single_bulbs_inside_the_silhouette(self):
        for tip in (False, True):
            L = house(tip)
            p = T.tree_plan(L, gable(L))
            self.assertTrue(p["slender"])
            orn = T.hex_ornaments(p, 0.3, 7, 3)
            self.assertTrue(orn, tip)
            hc = p["hc"]
            cells = [hc.of(i) for i in orn]
            for c in cells:
                self.assertFalse(p["leaf"][c.i][4], (tip, c.key))        # not on an edge
            for a in cells:
                for b in cells:
                    if a is not b:
                        self.assertGreater(T._hexdist(a, b), 1)           # green between them

    def test_dead_bulbs_move_nothing(self):
        for tip in (False, True):
            a, b = house(tip), house(tip, DEAD)
            pa, pb = T.tree_plan(a, gable(a)), T.tree_plan(b, gable(b))
            ka = {pa["hc"].of(i).key for i in pa["leaf"]}
            kb = {pb["hc"].of(i).key for i in pb["leaf"]}
            self.assertTrue(kb <= ka)
            self.assertEqual(pa["leaf_inner"], pb["leaf_inner"])
            oa = {pa["hc"].of(i).key for i in T.hex_ornaments(pa, 0.3, 7, 3)}
            ob = {pb["hc"].of(i).key for i in T.hex_ornaments(pb, 0.3, 7, 3)}
            self.assertTrue(ob <= oa, tip)

    def test_presets_render_in_range_with_and_without_the_tip(self):
        for tip in (False, True):
            L = house(tip)
            for pid in TREES:
                sc = Scene.from_dict(preset(pid))
                for k in (0, 40, 200):
                    for c in sc.render(k / 20, L):
                        self.assertTrue(all(0.0 <= v <= 1.0 for v in c), (pid, tip))


class Showpieces(unittest.TestCase):
    def test_turkey_stands_up(self):
        for tip in (False, True):
            L = house(tip)
            hc = hx.true(L, gable(L))
            plan = sp._turkey_plan(hc, 7)
            bird = [c for c in hc.cells if plan[c.key][0] in ("body", "head", "wattle")]
            body = [c for c in hc.cells if plan[c.key][0] == "body"]
            w, h = size(hc, bird)
            self.assertGreaterEqual(h / w, 1.0, (tip, w, h))           # taller than wide, head on top
            bw, bh = size(hc, body)
            self.assertTrue(0.6 <= bh / bw <= 1.5, (tip, bw, bh))      # a round body, not a flat slab
            heads = [c.r for c in hc.cells if plan[c.key][0] == "head"]
            self.assertGreater(min(heads), max(c.r for c in body))

    def test_sun_disc_is_round(self):
        for tip in (False, True):
            L = house(tip)
            hc = hx.true(L, gable(L))
            fx = EFFECTS["fan"]
            P = fx.resolve({"style": "sunset", "sink": 600, "intro": 0})
            st = {}
            cols = None
            for k in range(3):
                cols = fx.fn(k * 0.05, Ctx(L, gable(L), k * 0.05, 0.05, 120, {}), P, st)
            core = [c for c in hc.cells if c.i is not None and cols[c.i][0] > 0.8 and cols[c.i][1] > 0.6]
            self.assertTrue(core, tip)
            # the sun stands on the bottom row: its half above the centre is as tall as it is
            # half wide (round), measured in bulb spacings as hung
            w = max(c.x for c in core) - min(c.x for c in core) + 1
            h = (max(c.r for c in core) + 1) * hc.aspect
            self.assertTrue(0.4 <= h / w <= 0.9, (tip, w, h))

    def test_sunburst_hub_is_round(self):
        for tip in (False, True):
            L = house(tip)
            hc = hx.true(L, gable(L))
            st = {}
            frame = [(0.0, 0.0, 0.0)] * L.n
            P = EFFECTS["fan"].resolve({"style": "sunburst"})
            sp._sunburst_hex(frame, hc, st, P, 1.0, 1.0, P["colors"], 7, 1.0, 5)
            hub = [c for c in hc.cells if st["plan"][c.key][0] == "hub"]
            w = max(c.x for c in hub) - min(c.x for c in hub) + 1
            h = (max(c.r for c in hub) + 1) * hc.aspect
            self.assertTrue(0.3 <= h / w <= 0.8, (tip, w, h))

    def test_falling_star_head_is_round(self):
        """The head: a pair of bulbs and the one between them just behind (about as tall as
        wide), and one bulb of streak behind it, not a two-bulb flat dash."""
        for tip in (False, True):
            L = house(tip)
            hc = hx.true(L, gable(L))
            P = EFFECTS["falling_star"].resolve({})
            import random
            diag = sp._fs_diagonal(hc, 1, random.Random(3))
            fl = {"diag": [c.key for c in diag], "dir": "sw"}
            nd = len(diag)
            frame = [(0.0, 0.0, 0.0)] * L.n
            j = nd - 3
            sp._fs_streak(frame, hc, fl, float(j), 0.0, float(nd - 1), P)
            hot = [c for c in hc.cells if c.i is not None and max(frame[c.i]) > 0.8]
            w, h = size(hc, hot[:])
            head = [c for c in hot if c.r <= diag[j].r + 1]
            hw, hh = size(hc, head)
            self.assertEqual(len(head), 3, tip)
            self.assertTrue(0.5 <= hh / hw <= 1.0, (tip, hw, hh))
            # behind the head the streak is a single line of bulbs
            for r in range(diag[j].r + 2, hc.top + 1):
                lit = [c for c in hc.row(r) if c.i is not None and max(frame[c.i]) > 0.05]
                self.assertLessEqual(len(lit), 1, (tip, r))

    def test_candles_and_flames_stand_tall(self):
        for tip in (False, True):
            L = house(tip)
            hc = hx.true(L, gable(L))
            plan = sp._candle_plan(hc)
            mid = [c for c in hc.cells if c.key in plan and plan[c.key][1] == 0 and plan[c.key][0] != "glow"]
            w, h = size(hc, mid)
            self.assertGreaterEqual(h / w, 1.2, (tip, w, h))
            flame = [c for c in mid if plan[c.key][0] != "W"]
            fw, fh = size(hc, flame)
            self.assertGreaterEqual(fh / fw, 0.95 if not tip else 1.2, (tip, fw, fh))
            wax = [c for c in mid if plan[c.key][0] == "W"]
            ww, wh = size(hc, wax)
            self.assertLessEqual(ww, 3.0, tip)                       # a slim pillar
            for k in (1, 2):
                side = [c for c in hc.cells if c.key in plan and plan[c.key][1] == k and plan[c.key][0] != "glow"]
                sw, sh = size(hc, side)
                self.assertGreaterEqual(sh / sw, 0.85, (tip, k, sw, sh))

    def test_dead_bulbs_do_not_shift_the_pictures(self):
        for tip in (False, True):
            a, b = house(tip), house(tip, DEAD)
            ha, hb = hx.true(a, gable(a)), hx.true(b, gable(b))
            self.assertEqual(sp._turkey_plan(ha, 7), sp._turkey_plan(hb, 7))
            self.assertEqual(sp._candle_plan(ha), sp._candle_plan(hb))

    def test_presets_render_in_range_with_and_without_the_tip(self):
        for tip in (False, True):
            L = house(tip)
            for pid in SHOWPIECES:
                sc = Scene.from_dict(preset(pid))
                for k in range(0, 160, 8):
                    for c in sc.render(k / 20, L):
                        self.assertTrue(all(0.0 <= v <= 1.0 for v in c), (pid, tip))


if __name__ == "__main__":
    unittest.main()
