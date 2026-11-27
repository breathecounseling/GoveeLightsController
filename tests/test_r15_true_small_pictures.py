"""Round 15: the small pictures and occasion scenes in TRUE proportions.

On the house the Peak's rows are only hexcanvas.aspect (about 0.66) of a bulb spacing
apart, and the gable is 27 bulbs wide but 6 rows tall: pictures drawn one row per unit of
height came out two to three times too wide (the owner's night photos: flat wide ovals).
Each shape is measured here in physical units (bulb spacings as the gable hangs: a lit
shape n bulbs across is n wide, m rows tall is m * aspect tall) and must have the shape's
real proportions: a round pumpkin, heart-shaped hearts, upright ghosts, eggs taller than
wide, a box-shaped present and a flag-shaped star field. With and without the Tip, with
no exceptions, and a dead bulb shifts nothing."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from test_r12_bulb_check import OWNER_LAYOUT, by_phys, layout_with  # noqa: E402
from test_rack import LAYOUT_FILE, valid  # noqa: E402

from goveelights import addrun  # noqa: E402
from goveelights.effects import EFFECTS  # noqa: E402
from goveelights.effects_ext import hexcanvas as hx  # noqa: E402
from goveelights.effects_ext import night_sky as ns  # noqa: E402
from goveelights.effects_ext import occasions as O  # noqa: E402
from goveelights.fx import Ctx  # noqa: E402
from goveelights.pictures import HEX_PICTURES  # noqa: E402
from goveelights.scene import Scene  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PATH = OWNER_LAYOUT if OWNER_LAYOUT.exists() else ROOT / LAYOUT_FILE

# width / height in bulb spacings as hung: (lowest, highest)
PICTURE_RATIO = {
    "heart": (1.0, 1.45), "pumpkin": (1.0, 1.4), "jack-o-lantern": (1.1, 1.6), "star": (0.95, 1.35),
    "snowflake": (1.1, 1.6), "tree": (0.9, 1.35), "present": (1.0, 1.6), "smile": (0.95, 1.4),
}


def layouts():
    L = layout_with({}, PATH)
    return [("peak", L), ("tip", addrun.layout_with_tip(L, visible=12))]


def call(name, L, idx, P=None, st=None, clock=1.0):
    fx = EFFECTS[name]
    return fx.fn(clock, Ctx(L, idx, clock, 0.05), fx.resolve(P or {}), {} if st is None else st)


def size(hc, cells):
    """(width, height) of lattice cells in bulb spacings as the gable hangs."""
    xs = [c.x for c in cells]
    rs = [c.r for c in cells]
    return max(xs) - min(xs) + 1, (max(rs) - min(rs) + 1) * hc.aspect


def preset(pid):
    return json.loads((ROOT / "presets" / f"{pid}.json").read_text(encoding="utf-8"))


class Aspect(unittest.TestCase):
    def test_the_lattice_measures_the_house(self):
        for _, L in layouts():
            hc = hx.get(L, L.indices("Peak"))
            self.assertTrue(0.55 <= hc.aspect <= 0.75, hc.aspect)


class Pictures(unittest.TestCase):
    def lit(self, L, name):
        idx = L.indices("Peak")
        hc = hx.get(L, idx)
        cols = call("picture", L, idx, {"image": name, "background": "#000000"})
        valid(self, cols, L.n)
        return hc, [c for c in hc.cells if c.i is not None and max(cols[c.i]) > 0.05]

    def test_every_picture_in_true_proportion(self):
        self.assertEqual(set(PICTURE_RATIO), set(HEX_PICTURES))
        for tag, L in layouts():
            for name, (lo, hi) in PICTURE_RATIO.items():
                with self.subTest(picture=name, layout=tag):
                    hc, cells = self.lit(L, name)
                    w, h = size(hc, cells)
                    self.assertTrue(lo <= w / h <= hi, f"{name} {tag}: {w:g} x {h:.2f} = {w / h:.2f}")
                    self.assertGreaterEqual(len(cells), 10)
                    # bold enough to read and small enough to leave dark room on the gable
                    self.assertGreaterEqual(h, 3.0)
                    self.assertLessEqual(w, 8)

    def test_hearts_have_lobes_a_notch_and_a_point(self):
        for tag, L in layouts():
            hc, cells = self.lit(L, "heart")
            keys = {c.key for c in cells}
            top = max(c.r for c in cells)
            row = sorted(c.x for c in cells if c.r == top)
            self.assertEqual(len(row), 4 if hc.has_tip else 2, tag)        # two lobes...
            self.assertFalse(hc.cell(top, 0) and hc.cell(top, 0).key in keys)    # ...a notch
            self.assertFalse(any(hc.cell(top, x) and hc.cell(top, x).key in keys for x in (-0.5, 0.5)))
            bottom = [c.x for c in cells if c.r == 0]
            self.assertEqual(bottom, [0.0], tag)                                # one-bulb point
            widest = max(sum(1 for c in cells if c.r == r) for r in range(top + 1))
            self.assertGreaterEqual(widest, 5)

    def test_pumpkin_is_round_with_a_stem(self):
        for tag, L in layouts():
            idx = L.indices("Peak")
            hc = hx.get(L, idx)
            cols = call("picture", L, idx, {"image": "pumpkin"})
            green = [c for c in hc.cells if c.i is not None and cols[c.i][1] > cols[c.i][0] + 0.2]
            body = [c for c in hc.cells if c.i is not None and cols[c.i][0] > 0.5]
            self.assertEqual(len(green), 1, tag)
            self.assertGreater(green[0].r, max(c.r for c in body))              # on top
            w, h = size(hc, body)
            self.assertTrue(1.0 <= w / h <= 1.55, (tag, w, h))     # a pumpkin is a little squat
            # round: the middle rows are the widest, the top and bottom rows narrower
            n = {r: sum(1 for c in body if c.r == r) for r in {c.r for c in body}}
            self.assertLess(n[min(n)], max(n.values()))
            self.assertLess(n[max(n)], max(n.values()))

    def test_present_is_a_box_with_a_bow_on_top(self):
        for tag, L in layouts():
            idx = L.indices("Peak")
            hc = hx.get(L, idx)
            cols = call("picture", L, idx, {"image": "present"})
            red = [c for c in hc.cells if c.i is not None and cols[c.i][0] > 0.5 and cols[c.i][1] < 0.3]
            gold = [c for c in hc.cells if c.i is not None and cols[c.i][1] > 0.5]
            self.assertTrue(red and gold)
            top_box = max(c.r for c in red)
            self.assertTrue(any(c.r > top_box for c in gold), tag)               # the bow above
            box = [c for c in red + gold if c.r <= top_box]
            w, h = size(hc, box)
            self.assertTrue(1.0 <= w / h <= 2.0, (tag, w, h))              # a box, not a parcel
            w, h = size(hc, red + gold)
            self.assertTrue(1.0 <= w / h <= 1.6, (tag, w, h))              # with its bow


class Floaters(unittest.TestCase):
    RATIO = {"heart": (1.0, 1.4), "ghost": (1.0, 1.4), "egg": (0.6, 0.95), "big egg": (0.6, 0.95)}

    def test_sprites_in_true_proportion(self):
        L = layout_with({}, PATH)
        hc = hx.get(L, L.indices("Peak"))
        self.assertEqual(set(self.RATIO), set(O.SHAPES))
        for shape, (lo, hi) in self.RATIO.items():
            sp = O._hex_sprite(shape)
            cells = [c for c, ch in hc.sprite_cells(sp, 0, 0.0) if ch != "."]
            self.assertEqual(len(cells), sum(1 for *_, ch in sp.cells if ch != "."), shape)
            w, h = size(hc, cells)
            self.assertTrue(lo <= w / h <= hi, f"{shape}: {w:g} x {h:.2f} = {w / h:.2f}")
            self.assertLessEqual(sp.height, hc.peak_rows)

    def test_eggs_are_taller_than_wide_and_striped(self):
        for shape in ("egg", "big egg"):
            sp = O._hex_sprite(shape)
            self.assertIn("2", {ch for *_, ch in sp.cells})
            self.assertGreater(sp.height * hx.PEAK_ASPECT, (sp.width + 1) / 2)

    def test_ghost_has_two_eyes_and_a_ragged_hem(self):
        sp = O._hex_sprite("ghost")
        eyes = [(k, j) for k, j, ch in sp.cells if ch == "2"]
        self.assertEqual(len(eyes), 2)
        self.assertEqual(eyes[0][1], eyes[1][1])                                 # level
        self.assertGreaterEqual(abs(eyes[0][0] - eyes[1][0]), 4)                 # apart
        hem = sorted((k, ch) for k, j, ch in sp.cells if j == 0)
        self.assertIn(".", [ch for _, ch in hem])

    def test_scenes_render_with_and_without_the_tip(self):
        for tag, L in layouts():
            for pid in ("valentines-hearts", "halloween-haunted", "easter-pastels"):
                with self.subTest(preset=pid, layout=tag):
                    sc = Scene.from_dict(preset(pid))
                    lit = 0
                    for k in range(0, 300, 3):
                        cols = sc.render(k / 10, L)
                        valid(self, cols, L.n)
                        lit = max(lit, sum(1 for i in L.indices("Peak") if max(cols[i]) > 0.6))
                    self.assertGreaterEqual(lit, 10)


class Flag(unittest.TestCase):
    def test_star_field_is_flag_shaped(self):
        for tag, L in layouts():
            hc = hx.get(L, L.indices("Peak"))
            for edge in (0.5, 0.62, 1.0):
                field = O.canton_cells(hc, 3, edge)
                cells = [hc.of(i) for i in field]
                rows = {c.r for c in cells}
                h = len(rows) * hc.aspect
                w = max(sum(1 for c in cells if c.r == r) for r in rows)
                self.assertTrue(1.3 <= w / h <= 2.6, (tag, edge, w, h))
                # every row starts at the roof's slope (the screen's left end)
                for r in rows:
                    self.assertEqual(min(c.x for c in cells if c.r == r), hc.rows[r].x0)
                stars = [c for c in cells if field[c.i]]
                self.assertGreaterEqual(len(stars), 2, (tag, edge))
                keys = {c.key for c in cells}
                for s in stars:
                    self.assertTrue(all(n.key in keys for n in hc.neighbours(s)))
                    self.assertFalse(any(field.get(n.i) for n in hc.neighbours(s)))
        self.assertEqual(O.canton_cells(hc, 0, 0.5), {})

    def test_july_4th_canton_is_small_and_blue(self):
        for tag, L in layouts():
            idx = L.indices("Peak")
            hc = hx.get(L, idx)
            cols = call("flag", L, idx, {"wave": 0, "twinkle": 0, "intro": 0, "canton_edge": 0.62})
            valid(self, cols, L.n)
            blue = [c for c in hc.cells if c.i is not None and cols[c.i][2] > 0.6 and cols[c.i][0] < 0.3]
            n = sum(1 for rw in hc.rows if rw.r >= hc.peak_rows - 3)
            self.assertLessEqual(len(blue), (6 if not hc.has_tip else 9) * n, tag)
            # the stripes run full width below it
            self.assertTrue(all(max(cols[c.i]) > 0.5 for c in hc.rows[0].cells if c.i is not None))


class GiftWrap(unittest.TestCase):
    def test_the_present_is_box_shaped(self):
        for tag, L in layouts():
            idx = L.indices("Whole front gable") + L.indices("Porch")
            g = ns._gw_geometry(L, idx)
            hc = hx.get(L, idx)
            box = [hc.of(i) for i in g["su"]]
            w, h = size(hc, box)
            self.assertTrue(1.1 <= w / h <= 2.0, (tag, w, h))              # a box, not a parcel
            bow = [hc.of(i) for i in g["bow"] if i in g["zig"]]
            self.assertGreaterEqual(len(bow), 5)
            self.assertGreater(min(c.r for c in bow), max(c.r for c in box))
            bw, _ = size(hc, bow)
            self.assertLessEqual(bw, w + 0.5)                              # no wider than the box
            w, h = size(hc, box + bow)
            self.assertTrue(1.0 <= w / h <= 1.6, (tag, w, h))              # with its bow
            # a one-bulb ribbon each way: 1 or 2 bulbs a row down the middle, one row across
            for r in {c.r for c in box}:
                n = sum(1 for i in g["vcov"] if i in g["zig"] and hc.of(i).r == r)
                self.assertIn(n, (1, 2), (tag, r))
            across = {hc.of(i).r for i in g["hcov"] if i in g["zig"]}
            self.assertEqual(len(across), 1)


class DeadBulbs(unittest.TestCase):
    def test_a_dead_bulb_shifts_no_picture(self):
        L0 = layout_with({}, PATH)
        pk = L0.run("Peak").pixels
        for name in HEX_PICTURES:
            for dead in ({pk[0].local}, {pk[40].local, pk[60].local}):
                L = layout_with({"Peak": dead}, PATH)
                a = by_phys(L0, call("picture", L0, L0.indices("Peak"), {"image": name}))
                b = by_phys(L, call("picture", L, L.indices("Peak"), {"image": name}))
                for k in a:
                    if k[0] == "Peak" and k[1] not in dead:
                        self.assertEqual(a[k], b[k], (name, dead, k))

    def test_a_dead_bulb_moves_no_star(self):
        L0 = layout_with({}, PATH)
        pk = L0.run("Peak").pixels
        hc0 = hx.get(L0, L0.indices("Peak"))
        f0 = O.canton_cells(hc0, 3, 0.62)
        star = next(i for i, s in f0.items() if s)
        L = layout_with({"Peak": {L0.pixels[star].local}}, PATH)
        hc = hx.get(L, L.indices("Peak"))
        f = O.canton_cells(hc, 3, 0.62)
        s0 = {hc0.of(i).key for i, s in f0.items() if s}
        s1 = {hc.of(i).key for i, s in f.items() if s}
        self.assertEqual(s0 - {hc0.of(star).key}, s1)
        self.assertTrue(pk)


if __name__ == "__main__":
    unittest.main()


class RisingSpritesStayApart(unittest.TestCase):
    """Review of round 15: with the Tip hung the upper heart slows near the apex while the
    next one rises at full speed, and the pair met point-to-lobes (an hourglass). Through
    the whole climb there is at least one dark row between two rising sprites."""

    def check(self, pid, L, seconds=60.0, dt=0.05):
        layer = next(ly for ly in preset(pid)["layers"] if ly["effect"] == "floaters")
        idx = L.indices("Peak") + [i for i in L.indices("Whole front gable") if i not in L.indices("Peak")]
        hc = hx.get(L, L.indices("Peak"))
        fx = EFFECTS["floaters"]
        P = fx.resolve(layer["params"])
        hsp = O._hex_sprite(P["shape"])
        st: dict = {}
        worst = None
        for k in range(int(seconds / dt)):
            clock = 1.0 + k * dt
            fx.fn(clock, Ctx(L, idx, clock, dt), P, st)
            rows = []
            for s in st["sprites"]:
                if s["pop"] is not None:
                    continue
                acc: dict = {}
                x, _ = O._sprite_xy(O.field(L, idx), s, 1, 1, clock, P)
                O._hex_stamp(hc, hsp, s["top"] - (hsp.height - 1), 0.0, acc)
                lit = [hc.of(i).r for i, (a1, a2) in acc.items() if a1 + a2 > 0.05]
                if lit:
                    rows.append((min(lit), max(lit)))
            rows.sort()
            for (lo_a, hi_a), (lo_b, hi_b) in zip(rows, rows[1:]):
                gap = lo_b - hi_a - 1                       # dark rows between them
                worst = gap if worst is None else min(worst, gap)
                self.assertGreaterEqual(gap, 1, (pid, round(clock, 2), rows))
        return worst

    def test_valentines_hearts_peak_and_tip(self):
        for tag, L in layouts():
            with self.subTest(tag):
                self.check("valentines-hearts", L)

    def test_easter_eggs_peak_and_tip(self):
        for tag, L in layouts():
            with self.subTest(tag):
                self.check("easter-pastels", L)


class TestLayoutKeepsAspect(unittest.TestCase):
    """Review of round 15: tests.test_r12_bulb_check.layout_with dropped the layout file's
    "aspect", so every test ran on PEAK_ASPECT and a change to house.json alone was hidden."""

    def test_layout_with_passes_the_file_aspect_through(self):
        import tempfile
        data = json.loads((ROOT / LAYOUT_FILE).read_text(encoding="utf-8"))
        peak = next(d for d in data["devices"].values() if d["name"] == "Peak")
        peak["aspect"] = 0.8
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "house.json"
            p.write_text(json.dumps(data), encoding="utf-8")
            L = layout_with({}, p)
        run = next(r for r in L.runs if r.name == "Peak")
        self.assertAlmostEqual(run.aspect, 0.8)
        self.assertAlmostEqual(hx.get(L, L.indices("Peak")).aspect, 0.8)
