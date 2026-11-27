"""Round 13: the small pictures and occasion scenes redrawn for the Peak as it hangs, a
triangle on a honeycomb (rows offset by half a bulb). Pictures are lattice sprites (bold,
dark round them), the floaters are lattice sprites over a dim field, the flag's stars sit
on an even third of the lattice, the advent doors are 2-bulb bricks each ringed by dark
bulbs, and the gift wrap draws a present (box, ribbon, bow) on the Peak. Each renders with
and without the Tip, stays in range, and a dead bulb shifts nothing."""
import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from test_r12_bulb_check import OWNER_LAYOUT, by_phys, layout_with  # noqa: E402
from test_rack import LAYOUT_FILE, house, valid  # noqa: E402

from goveelights import addrun  # noqa: E402
from goveelights.effects import EFFECTS  # noqa: E402
from goveelights.effects_ext import hexcanvas as hx  # noqa: E402
from goveelights.effects_ext import night_sky as ns  # noqa: E402
from goveelights.effects_ext import occasions as O  # noqa: E402
from goveelights.fx import Ctx  # noqa: E402
from goveelights.pictures import HEX_PICTURES, PICTURES  # noqa: E402
from goveelights.scene import Scene  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PRESETS = ["halloween-pumpkin", "valentines-glow", "advent-calendar", "gift-wrap", "valentines-hearts",
           "halloween-haunted", "easter-pastels", "july-4th-flag"]
PATH = OWNER_LAYOUT if OWNER_LAYOUT.exists() else ROOT / LAYOUT_FILE


def _shape(c):
    m = max(c)
    return "dark" if m < 0.3 else max(range(3), key=lambda j: c[j])


def preset(pid):
    return json.loads((ROOT / "presets" / f"{pid}.json").read_text(encoding="utf-8"))


def call(name, L, idx, P=None, st=None, clock=1.0):
    fx = EFFECTS[name]
    return fx.fn(clock, Ctx(L, idx, clock, 0.05), fx.resolve(P or {}), {} if st is None else st)


def layouts():
    L = layout_with({}, PATH)
    return [("peak", L), ("tip", addrun.layout_with_tip(L, visible=12))]


class PictureSprites(unittest.TestCase):
    def test_every_picture_is_drawn_for_the_lattice(self):
        self.assertEqual(set(HEX_PICTURES), set(PICTURES))
        for _, L in layouts():
            hc = hx.get(L, L.indices("Peak"))
            for name, pic in HEX_PICTURES.items():
                text = pic["tip"] if hc.has_tip and "tip" in pic else pic["peak"]
                sp = hx.Sprite(text, {ch: ch for ch in "1234"})
                cells = hc.sprite_cells(sp, 0, 0.0)
                self.assertEqual(len(cells), len(sp.cells), name)          # every cell on the screen
                lit = [c for c, ch in cells if ch != "."]
                self.assertGreater(len(lit), 15, name)
                # dark space round it: the bottom row's ends and the rows' outer bulbs stay dark
                row0 = hc.rows[0].cells
                self.assertFalse({row0[0].key, row0[-1].key} & {c.key for c in lit}, name)

    def test_heart_has_a_notch_two_lobes_and_a_point(self):
        L = layout_with({}, PATH)
        hc = hx.get(L, L.indices("Peak"))
        cols = call("picture", L, L.indices("Peak"), {"image": "heart", "colors": ["#ff0000"]})
        on = lambda r, x: max(cols[hc.at(r, x)]) > 0.5           # noqa: E731
        self.assertFalse(on(5, -0.5) or on(5, 0.5) or on(4, 0))  # the notch, 2 bulbs then 1
        self.assertTrue(all(on(5, x) for x in (-3.5, -2.5, -1.5, 1.5, 2.5, 3.5)))
        self.assertEqual([x for x in range(-13, 14) if on(0, x)], [0])      # one-bulb point...
        self.assertTrue(all(on(1, x) for x in (-1.5, -0.5, 0.5, 1.5)))      # ...under a 4-bulb row

    def test_beat_swells_a_glow_round_the_heart(self):
        L = layout_with({}, PATH)
        idx = L.indices("Peak")
        P = {"image": "heart", "colors": ["#ff0000"], "background": "#000000", "beat": 1.0, "period": 1.0}
        rest = call("picture", L, idx, P, clock=0.5)
        beat = call("picture", L, idx, P, clock=0.06)
        lit = lambda cols: sum(1 for i in idx if max(cols[i]) > 0.05)     # noqa: E731
        self.assertGreater(lit(beat), lit(rest) + 10)

    def test_off_the_zigzag_the_bitmap_still_draws(self):
        L = house()
        cols = call("picture", L, L.indices("Eaves"), {"image": "star"})
        valid(self, cols, L.n)
        self.assertTrue(any(max(cols[i]) > 0.5 for i in L.indices("Eaves")))


class Floaters(unittest.TestCase):
    def test_sprites_are_lattice_sprites(self):
        for shape in O.SHAPES:
            sp = O._hex_sprite(shape)
            self.assertLessEqual(sp.height, 6)
        L = layout_with({}, PATH)
        hc = hx.get(L, L.indices("Peak"))
        acc = {}
        O._hex_stamp(hc, O._hex_sprite("heart"), 1.0, 0.0, acc)            # resting on a place
        self.assertEqual(sum(1 for a in acc.values() if a[0] >= 0.99), 20)
        self.assertTrue(all(a[0] in (0.0, 1.0) for a in acc.values()))
        acc = {}
        O._hex_stamp(hc, O._hex_sprite("heart"), 0.5, 0.25, acc)           # gliding: cross-faded
        self.assertAlmostEqual(sum(a[0] for a in acc.values()), 20, delta=0.05)


class Flag(unittest.TestCase):
    def test_stars_are_spread_evenly_each_ringed_by_blue(self):
        for _, L in layouts():
            idx = L.indices("Peak")
            hc = hx.get(L, idx)
            cols = call("flag", L, idx, {"wave": 0, "twinkle": 0, "intro": 0, "canton_edge": 0.62})
            stars = [c for c in hc.cells if c.i is not None and min(cols[c.i]) > 0.6 and c.r >= 3
                     and c.x <= 3.2]
            self.assertGreater(len(stars), 6)
            for s in stars:
                for n in hc.neighbours(s):
                    if n.i is not None and n.r >= 3 and n.x <= 3.2:
                        c = cols[n.i]
                        self.assertTrue(c[2] > 0.6 and c[0] < 0.3, (s.key, n.key, c))


    def test_no_star_touches_a_white_stripe(self):
        """A star on the canton's right edge joined the white stripe beside it as a white
        bar; every star must have blue (or the edge of the screen) on every side."""
        for tag, L in layouts():
            idx = L.indices("Peak")
            hc = hx.get(L, idx)
            for P in ({"wave": 0, "twinkle": 0, "intro": 0, "canton_edge": 0.62}, {"intro": 0}):
                R = EFFECTS["flag"].resolve(P)
                ex = hc.rows[0].x0 + R["canton_edge"] * (hc.rows[0].x1 - hc.rows[0].x0)
                crow = hc.peak_rows - R["canton_rows"]
                st = {}
                for k in range(0, 200, 5):
                    cols = call("flag", L, idx, P, st, clock=k * 0.05)
                    white = {c.i for c in hc.cells if c.i is not None and min(cols[c.i]) > 0.6}
                    stars = [c for c in hc.cells if c.i in white and c.r >= crow and c.x <= ex + 1e-6]
                    if "wave" in P:                       # a still flag: the full star field
                        self.assertGreaterEqual(len(stars), 6)
                    for c in stars:                      # stripe neighbours count too
                        ns_ = [n for n in hc.neighbours(c) if n.i is not None]
                        self.assertFalse([n.key for n in ns_ if n.i in white], (tag, P, c.key))


class Advent(unittest.TestCase):
    def test_doors_stand_apart(self):
        for _, L in layouts():
            idx = L.indices("Whole front gable")
            g = ns._ad_doors(L, idx)
            hc = hx.get(L, idx)
            doors = g["doors"]
            self.assertEqual(len(doors), 24)
            self.assertGreaterEqual(sum(len(d) == 2 for d in doors), 21)
            owner = {i: k for k, d in enumerate(doors) for i in d}
            for k, d in enumerate(doors):
                if hc.of(d[0]) is None:                          # the star on the gable apex
                    self.assertEqual(k, len(doors) - 1)
                    continue
                self.assertIn(len(d), (1, 2))
                for i in d:
                    for n in hc.neighbours(hc.of(i)):
                        self.assertIn(owner.get(n.i, k), (k,), "doors touch")
                self.assertTrue(g["ring"][k])

    def test_todays_door_is_ringed(self):
        L = layout_with({}, PATH)
        idx = L.indices("Whole front gable")
        g = ns._ad_doors(L, idx)
        P = EFFECTS["advent"].resolve({"day": 10, "intro": 0, "sparkle": 0, "order": "bottom up"})
        st = {}
        for k in range(30):
            cols = EFFECTS["advent"].fn(k * 0.05, Ctx(L, idx, k * 0.05, 0.05), P, st)
        today = ns._ad_order(len(g["doors"]), "bottom up", P["seed"])[
            max(1, min(len(g["doors"]), -(-10 * len(g["doors"]) // 24))) - 1]
        ring = [max(cols[i]) for i in g["ring"][today]]
        other = [max(cols[i]) for i in g["gaps"] if i not in g["ring"][today]]
        self.assertGreater(min(ring), max(other) + 0.1)


class GiftWrap(unittest.TestCase):
    def test_a_present_on_dark(self):
        for _, L in layouts():
            idx = L.indices("Whole front gable") + L.indices("Porch")
            cols, _ = EFFECTS["gift_wrap"].fn(1.0, Ctx(L, idx, 1.0, 0.05),
                                              EFFECTS["gift_wrap"].resolve({"tie": 0, "shimmer": 0, "sheen": 0}),
                                              {}), None
            g = ns._gw_geometry(L, idx)
            hc = hx.get(L, idx)
            self.assertTrue(all(max(cols[i]) < 0.05 for i in g["dark"]))
            self.assertGreater(len(g["dark"]), 30)
            bow = [i for i in g["bow"] if i in g["zig"]]
            self.assertGreaterEqual(len(bow), 12)
            self.assertTrue(all(hc.of(i).r >= 4 for i in bow))
            rib = [i for i in g["vcov"] if i in g["zig"]]
            self.assertEqual(sorted({len([i for i in rib if hc.of(i).r == r]) for r in range(4)}), [2, 3])
            for i in rib:
                self.assertGreater(cols[i][1], 0.4)                       # gold ribbon


class Pumpkin(unittest.TestCase):
    def test_face_is_eyes_nose_and_grin_apart(self):
        """The carved holes (ember glow) must form separate features: two eyes, a nose and
        the two halves of a toothed grin, none more than 2 rows tall (eyes that ran into the
        mouth read as two dark gashes)."""
        for tag, L in layouts():
            hc = hx.get(L, L.indices("Peak"))
            sc = Scene.from_dict(preset("halloween-pumpkin"))
            for k in range(41):
                cols = sc.render(k * 0.05, L)
            holes = {c.i for c in hc.cells if c.i is not None and 0.15 < max(cols[c.i]) < 0.4
                     and cols[c.i][0] >= max(cols[c.i])}
            groups, seen = [], set()
            for i in holes:
                if i in seen:
                    continue
                g, todo = [], [i]
                seen.add(i)
                while todo:
                    j = todo.pop()
                    g.append(j)
                    for n in hc.neighbours(hc.of(j)):
                        if n.i in holes and n.i not in seen:
                            seen.add(n.i)
                            todo.append(n.i)
                groups.append(g)
            self.assertEqual(len(groups), 5, tag)
            for g in groups:
                rows = {hc.of(i).r for i in g}
                self.assertLessEqual(max(rows) - min(rows), 1, (tag, sorted(rows)))


class Haunted(unittest.TestCase):
    def test_ghosts_never_touch(self):
        """Ghosts drift spaced by the hex sprite's width; with the old square bitmap's
        width lattice snapping closed the gap and they merged into one white blob."""
        L = layout_with({}, PATH)
        hc = hx.get(L, L.indices("Peak"))
        stamps, real = [], O._hex_stamp

        def spy(hc_, sp, bottom, x, acc, k=1.0):
            mine = {}
            real(hc_, sp, bottom, x, mine, k)
            stamps.append({i for i, a in mine.items() if a[0] + a[1] > 0.5})
            for i, a in mine.items():
                b = acc.setdefault(i, [0.0, 0.0])
                b[0] += a[0]
                b[1] += a[1]
        O._hex_stamp = spy
        try:
            sc = Scene.from_dict(preset("halloween-haunted"))
            touching, most = 0, 0
            for k in range(1200):
                stamps.clear()
                sc.render(k * 0.05, L)
                most = max(most, len(stamps))
                near = [set().union(*({n.i for n in hc.neighbours(hc.of(i)) if n.i is not None} | {i}
                                      for i in s)) if s else set() for s in stamps]
                if any(near[a] & stamps[b] for a in range(len(stamps)) for b in range(a + 1, len(stamps))):
                    touching += 1
        finally:
            O._hex_stamp = real
        self.assertEqual(touching, 0)
        self.assertGreaterEqual(most, 3)


class Presets(unittest.TestCase):
    def test_render_with_and_without_the_tip_in_range(self):
        for tag, L in layouts():
            for pid in PRESETS:
                with self.subTest(preset=pid, layout=tag):
                    sc = Scene.from_dict(preset(pid))
                    for k in range(0, 200, 7):
                        cols = sc.render(k / 10, L)
                        valid(self, cols, L.n)
                    peak = L.indices("Peak")
                    self.assertTrue(any(max(cols[i]) > 0.3 for i in peak))

    def test_a_dead_bulb_shifts_nothing(self):
        L0 = layout_with({}, PATH)
        pk = L0.run("Peak").pixels
        cases = [{"Peak": {pk[0].local}}, {"Peak": {pk[40].local, pk[-1].local}}]
        for pid in PRESETS:
            for dead in cases:
                with self.subTest(preset=pid, dead=dead):
                    L = layout_with(dead, PATH)
                    sa, sb = Scene.from_dict(copy.deepcopy(preset(pid))), Scene.from_dict(copy.deepcopy(preset(pid)))
                    for step in range(6):
                        t = step * 0.9
                        ca, cb = by_phys(L0, sa.render(t, L0)), by_phys(L, sb.render(t, L))
                        ca = {k: v for k, v in ca.items() if k[0] == "Peak"}   # the screen
                        cb = {k: v for k, v in cb.items() if k[0] == "Peak"}
                        for run, ks in dead.items():
                            for k in ks:
                                ca.pop((run, k), None)
                                cb.pop((run, k), None)
                        # the picture's shape: which bulbs are lit, and in which colour family
                        # (sparkle and shimmer are seeded per bulb and may differ a little)
                        bad = [k for k in ca if _shape(ca[k]) != _shape(cb[k])
                               and abs(max(ca[k]) - max(cb[k])) > 0.12]
                        self.assertLessEqual(len(bad), 1, f"{pid} with {dead} at t={t}: {bad}")


if __name__ == "__main__":
    unittest.main()
