"""Year-round occasion presets and their two effects (floaters, flag) on the real house."""
import glob
import json
import os
import time
import unittest

from goveelights.effects import EFFECTS
from goveelights.effects_ext import occasions as O
from goveelights.fx import Ctx
from goveelights.layout import Layout, Run
from goveelights.pictures import PICTURES, sample
from goveelights.scene import Scene
from test_rack import house, valid

OCCASIONS = {
    "valentines-hearts": "Valentine's", "valentines-glow": "Valentine's", "st-patricks": "St. Patrick's",
    "easter-pastels": "Easter", "july-4th-fireworks": "July 4th", "july-4th-flag": "July 4th",
    "halloween-haunted": "Halloween", "halloween-pumpkin": "Halloween", "thanksgiving-harvest": "Thanksgiving",
    "broncos-game-day": "Game day", "winter-elegance": "Winter", "spring-garden": "Spring",
    "summer-evening": "Summer",
}
BUDGET_MS = 8.0
FLOATER_SCREENS = {"valentines-hearts", "easter-pastels", "halloween-haunted"}


def load(pid):
    with open(f"presets/{pid}.json", encoding="utf-8") as f:
        return json.load(f)


def one(effect, params=None, target="Whole front gable"):
    return Scene.from_dict({"layers": [{"effect": effect, "params": params or {}, "target": target}]})


def run(sc, L, seconds, fps=20, t0=0.0):
    cols = None
    for k in range(int(seconds * fps)):
        cols = sc.render(t0 + k / fps, L)
    return cols


def call(name, L, idx, P=None, state=None, clock=1.0, dt=0.05):
    fx = EFFECTS[name]
    return fx.fn(clock, Ctx(L, idx, clock, dt, 120, {}), fx.resolve(P or {}), {} if state is None else state)


def lum(c):
    return 0.3 * c[0] + 0.59 * c[1] + 0.11 * c[2]


class EveryPresetTests(unittest.TestCase):
    def test_every_preset_loads_renders_and_stays_in_budget(self):
        """Every shipped preset renders valid frames on the house for a while, and on
        average well inside the frame budget (warm-up frames excluded)."""
        L = house()
        ids = sorted(os.path.basename(f)[:-5] for f in glob.glob("presets/*.json"))
        self.assertTrue(set(OCCASIONS) <= set(ids))
        for pid in ids:
            with self.subTest(preset=pid):
                sc = Scene.from_dict(load(pid))
                for k in range(10):                               # warm caches
                    sc.render(k / 20, L)
                best = None
                for _ in range(3):                                # best of three: ignore a busy machine
                    t0 = time.perf_counter()
                    for k in range(40):
                        cols = sc.render(0.5 + k / 20, L)
                    ms = (time.perf_counter() - t0) * 1000 / 40
                    best = ms if best is None else min(best, ms)
                valid(self, cols, L.n)
                self.assertLess(best, BUDGET_MS, f"{pid}: {best:.1f} ms/frame")

    def test_occasion_presets_are_tagged_and_light_every_run(self):
        L = house()
        names = set()
        for pid, tag in OCCASIONS.items():
            with self.subTest(preset=pid):
                d = load(pid)
                self.assertIn(tag, d["tags"])
                self.assertNotIn(d["name"], names)
                names.add(d["name"])
                sc = Scene.from_dict(d)
                lit = {r.name: 0.0 for r in L.runs}
                drive = {r.name: 0.0 for r in L.runs}
                frames = 0
                for k in range(20 * 20):
                    cols = sc.render(k / 20, L)
                    if k % 10 == 0 and k >= 100:          # after any intro
                        frames += 1
                        for p, c in zip(L.pixels, cols):
                            lit[p.run] += lum(c)
                            drive[p.run] += max(c)
                avg = {r.name: lit[r.name] / (frames * len(r.pixels)) for r in L.runs}
                # nothing "off": every run is visibly lit on average ...
                for run_name, v in avg.items():
                    self.assertGreater(v, 0.06, f"{pid}: {run_name} too dark ({v:.3f})")
                # ... and no run blows the others away (nor sits as a dark hole beside them).
                # Balance is how hard each run's LEDs are driven (brightest channel), so a
                # saturated purple run is not counted as "dim" beside an orange one. A
                # near-dark zigzag (floaters glow 0.18) measured 1.7-2.2 and must fail.
                lvl = {r.name: drive[r.name] / (frames * len(r.pixels)) for r in L.runs}
                hi, lo = max(lvl.values()), min(lvl.values())
                # Round 13: the floaters are bold sprites over a DIM field on the Peak (the
                # owner asked for pictures with dark space round them, readable from the
                # street), so on average the Peak is driven less than the rooflines; it must
                # still glow (field and sprites) and not drop out beside them.
                limit = 2.5 if pid in FLOATER_SCREENS else 1.6
                self.assertLessEqual(hi / lo, limit, f"{pid}: runs unbalanced {lvl}")
                self.assertGreater(lvl["Peak"], 0.22, f"{pid}: the Peak is too dark")


class MasterPaletteTests(unittest.TestCase):
    def test_master_palette_keeps_stencils_and_ghost_eyes(self):
        """A master palette restyles looks, not stencils: the pumpkin's mask layer and the
        ghosts' eye colour stay as they are."""
        from goveelights.scene import apply_palette
        L = house()
        F = O.field(L, L.indices("Peak"))
        body = F["rows"][0][0]                                # bottom-left bulb: solid pumpkin
        for master in (None, {"palette": "Halloween"}):
            sc = Scene.from_dict(load("halloween-pumpkin"))
            seen = []
            for k in range(60):
                cols = sc.render(k / 20, L, master)
                if k >= 20:
                    seen.append(cols[body])
            spread = max(max(c[j] for c in seen) - min(c[j] for c in seen) for j in range(3))
            self.assertLess(spread, 0.05, f"{master}: pumpkin body flickers {spread:.2f}")
        fx = EFFECTS["floaters"]
        P = fx.resolve({"shape": "ghost"})
        apply_palette(fx, P, [(1.0, 0.0, 1.0), (0.0, 1.0, 0.0)])
        self.assertEqual(tuple(P["detail"]), (0.0, 0.0, 0.0))
        self.assertEqual(P["colors"], [(1.0, 0.0, 1.0), (0.0, 1.0, 0.0)])


class FieldTests(unittest.TestCase):
    def test_zigzag_found_in_whole_gable(self):
        L = house()
        F = O.field(L, L.indices("Whole front gable"))
        self.assertEqual([len(r) for r in F["rows"]], [27, 23, 20, 16, 12, 10])
        self.assertEqual(sorted(L.pixels[i].local for i in F["apex"]), [42, 43])
        self.assertEqual(len(F["rest"]), 24)                     # the gable outline, Eaves 30..53
        self.assertAlmostEqual(F["cp"], 0.7, delta=0.05)

    def test_rows_by_height_without_a_zigzag(self):
        L = house()
        F = O.field(L, L.indices("Eaves"))
        self.assertTrue(F["rows"])
        cols = call("floaters", L, L.indices("Eaves"))
        valid(self, cols, L.n)

    def test_sample_glides(self):
        bmp = O.SPRITES["heart"]
        self.assertEqual(sample(PICTURES["heart"], 0.5, 0.5), sample(PICTURES["heart"], 0.5, 0.5))
        self.assertEqual(O.sample(bmp, 0, 0), (1.0, 0.0))
        self.assertEqual(O.sample(bmp, 2, 0), (0.0, 0.0))          # the notch between the lobes
        a, _ = O.sample(bmp, 1.5, 0)
        self.assertTrue(0 < a < 1)
        self.assertEqual(O.sample(bmp, -3, 0), (0.0, 0.0))
        self.assertEqual(O.sample(bmp, 0, 9), (0.0, 0.0))


class FloaterTests(unittest.TestCase):
    def test_hearts_rise_and_pop_at_the_apex(self):
        L = house()
        sc = one("floaters")
        apex = [p.i for p in L.pixels if p.run == "Eaves" and p.local in (42, 43)]
        peak = set(L.indices("Peak"))
        best_apex, rows_seen = 0.0, set()
        F = O.field(L, L.indices("Whole front gable"))
        for k in range(20 * 30):
            cols = sc.render(k / 20, L)
            best_apex = max(best_apex, max(lum(cols[i]) for i in apex))
            for r, row in enumerate(F["rows"]):
                if any(cols[i][0] > 0.5 for i in row):
                    rows_seen.add(r)
            self.assertTrue(all(i in peak or lum(cols[i]) <= 1.0 for i in range(L.n)))
        self.assertEqual(rows_seen, set(range(6)))           # hearts travel the whole gable
        self.assertGreater(best_apex, 0.8)                     # and the apex flashes when they pop
        st = sc.layers[0].state
        self.assertLessEqual(len(st["sprites"]), O.MAX_SPRITES)
        self.assertLessEqual(len(st["bursts"]), O.MAX_BURSTS)

    def test_rising_sprites_never_overlap_and_state_stays_bounded(self):
        L = house()
        idx = L.indices("Whole front gable")
        for shape in O.SHAPES:
            for motion in ("rise", "drift"):
                st = {}
                P = {"shape": shape, "motion": motion, "every": 0.3, "count": 6, "speed": 3}
                for k in range(3000):
                    call("floaters", L, idx, P, st, clock=k / 20)
                    sp = [s for s in st["sprites"] if s["pop"] is None]
                    if motion == "rise":
                        tops = sorted(s["top"] for s in sp)
                        h = len(O.SPRITES[shape])
                        self.assertTrue(all(b - a >= h + 0.3 for a, b in zip(tops, tops[1:])), tops)
                    else:
                        xs = sorted(s["x"] for s in sp)
                        self.assertTrue(all(b - a >= 3 for a, b in zip(xs, xs[1:])), xs)
                        self.assertEqual(len({s["dir"] for s in sp}), min(1, len(sp)))
                self.assertLessEqual(len(st["sprites"]), O.MAX_SPRITES)
                self.assertLessEqual(len(st["bursts"]), O.MAX_BURSTS)

    def test_ghosts_have_dark_eyes(self):
        L = house()
        sc = one("floaters", {"shape": "ghost", "motion": "drift", "colors": ["#ffffff"], "detail": "#000000",
                              "flicker": 0, "glow": 0, "outline": 0, "speed": 1.0})
        F = O.field(L, L.indices("Whole front gable"))
        found = False
        for k in range(20 * 40):
            cols = sc.render(k / 20, L)
            for row in F["rows"]:
                v = [lum(cols[i]) for i in row]
                # a bright, dark, bright, dark, bright run along one row: the eyes
                for a in range(len(v) - 4):
                    w = v[a:a + 5]
                    if min(w[0], w[2], w[4]) > 0.6 and max(w[1], w[3]) < 0.25:
                        found = True
        self.assertTrue(found)

    def test_robust_inputs(self):
        L = house()
        self.assertEqual(call("floaters", L, []), [(0.0, 0.0, 0.0)] * L.n)
        st = {}
        for clock in (5.0, 5.05, 1.0, 1.05, float("nan")):          # clock going back resets
            cols = call("floaters", L, L.indices("Peak"), {}, st, clock=clock if clock == clock else 2.0,
                        dt=float("nan") if clock != clock else 0.05)
            valid(self, cols, L.n)
        small = Layout([Run("a", "A", 3)])
        valid(self, call("floaters", small, [0, 1, 2]), 3)
        valid(self, call("flag", small, [0, 1, 2]), 3)
        valid(self, call("flag", L, []), L.n)


class FlagTests(unittest.TestCase):
    def test_reads_as_a_flag(self):
        L = house()
        sc = one("flag", {"wave": 0, "twinkle": 0}, target="Peak")
        cols = run(sc, L, 5)
        F = O.field(L, L.indices("Peak"))
        rows = F["rows"]
        bottom = [cols[i] for i in rows[0]]
        self.assertTrue(all(c[0] > 0.8 and c[1] < 0.1 for c in bottom))              # red bottom stripe
        self.assertTrue(all(min(c) > 0.6 for c in (cols[i] for i in rows[1])))       # white stripe
        top = rows[-1]
        left, right = top[: len(top) // 2], top[len(top) // 2 + 1:]
        self.assertTrue(all(c[2] > 0.6 for c in (cols[i] for i in left)))             # blue/white stars, top left
        self.assertTrue(any(c[0] < 0.2 for c in (cols[i] for i in left)))             # some pure blue
        self.assertTrue(all(min(c) > 0.6 for c in (cols[i] for i in right)))          # stripes beside it

    def test_unfurls_from_the_left(self):
        L = house()
        sc = one("flag", {"intro": 4}, target="Peak")
        cols = run(sc, L, 1.5)
        row = O.field(L, L.indices("Peak"))["rows"][0]
        self.assertGreater(lum(cols[row[0]]), 0.1)
        self.assertLess(lum(cols[row[-1]]), 0.01)
        cols = run(sc, L, 3, t0=1.5)
        self.assertGreater(lum(cols[row[-1]]), 0.1)


class PictureTests(unittest.TestCase):
    def test_every_bitmap_row_shows_on_the_zigzag(self):
        """The zigzag's top two rows are closer together than the others; each of the six
        bitmap rows must still land on its own row of bulbs."""
        L = house()
        F = O.field(L, L.indices("Peak"))
        bmp = [ch * 13 for ch in "abcdef"]                  # one letter per bitmap row, top first
        got = ""
        for row in reversed(F["rows"]):
            seen = {sample(bmp, L.pixels[i].rnx, L.pixels[i].rny) for i in row}
            self.assertEqual(len(seen), 1)
            got += seen.pop()
        self.assertEqual(got, "abcdef")

    def test_jack_o_lantern_face(self):
        L = house()
        cols = run(Scene.from_dict(load("halloween-pumpkin")), L, 3)
        F = O.field(L, L.indices("Peak"))
        eyes = F["rows"][4]                                  # second row from the top: the eyes
        v = [cols[i] for i in eyes]
        # Round 13: the face is carved, dark eye holes (an ember glow) in a bright orange
        # pumpkin, which reads from the street where yellow-on-orange smudged
        orange = [k for k, c in enumerate(v) if c[0] > 0.6 and c[1] < 0.35]
        self.assertTrue(orange)
        holes = [k for k, c in enumerate(v) if max(c) < 0.35 and orange[0] < k < orange[-1]]
        self.assertEqual(len(holes), 2)                       # two eyes inside the pumpkin


if __name__ == "__main__":
    unittest.main()
