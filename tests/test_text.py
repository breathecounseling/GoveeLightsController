import datetime
import glob
import json
import time
import unittest

from goveelights import font, geometry, livedata
from goveelights.effects import EFFECTS
from goveelights.effects_ext import scrolltext
from goveelights.fx import Ctx
from goveelights.scene import Scene

from test_rack import house, valid

GAME = {"id": "401", "state": "in", "detail": "10:32 - 3rd", "team": "DEN", "opponent": "PIT",
        "score": 17, "opp_score": 10, "colors": ["#fc4c02", "#0a2343"]}


def run(effect_params, target="Peak", frames=40, fps=20.0, L=None, t0=0.0):
    L = L or house()
    sc = Scene.from_dict({"layers": [{"effect": "text", "params": effect_params, "target": target}]})
    out = None
    for k in range(frames):
        out = sc.render(t0 + k / fps, L)
    return out, L, sc


def lit(cols, idx):
    return sum(1 for i in idx if max(cols[i]) > 0.3)


class FontTests(unittest.TestCase):
    def test_glyph_coverage(self):
        for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 !?-:.'+&#/" + font.HEART:
            self.assertIn(ch, font.GLYPHS, ch)

    def test_every_glyph_fits_five_rows(self):
        for ch, g in font.GLYPHS.items():
            self.assertEqual(len(g), 5, ch)
            self.assertEqual(len({len(r) for r in g}), 1, ch)
            self.assertTrue(1 <= len(g[0]) <= 5, ch)
            self.assertTrue(set("".join(g)) <= {"#", "."}, ch)
            if ch != " ":
                self.assertIn("#", "".join(g), ch)

    def test_distinct_glyphs(self):
        seen = {}
        for ch, g in font.GLYPHS.items():
            self.assertNotIn(g, seen, f"{ch} looks like {seen.get(g)}")
            seen[g] = ch

    def test_layout(self):
        rows = font.render("HI")
        self.assertEqual(rows[0], "#.#.###")
        self.assertEqual(font.layout("HI").width, 7)
        self.assertEqual(font.normalise("i <3 u~"), "I " + font.HEART + " U")
        bm = font.layout("A B")
        self.assertEqual(bm.width, 3 + 3 + 3)            # word gap is the space's 3 columns
        self.assertEqual(bm.letter[:3], [0, 0, 0])
        self.assertEqual(font.layout("").width, 0)
        self.assertEqual(font.layout(None).width, 0)


class TextEffectTests(unittest.TestCase):
    def setUp(self):
        self._now = scrolltext._now
        self._games = livedata.LIVE["games"]

    def tearDown(self):
        scrolltext._now = self._now
        livedata.LIVE["games"] = self._games

    def test_registered(self):
        fx = EFFECTS["text"]
        self.assertTrue(fx.frame)
        self.assertEqual(fx.group, "Text, pictures & numbers")
        self.assertEqual(fx.resolve({})["text"], "MERRY CHRISTMAS")

    def test_text_param_resolves_to_string(self):
        fx = EFFECTS["text"]
        self.assertEqual(fx.resolve({"text": 42})["text"], "42")
        self.assertEqual(fx.resolve({"text": None})["text"], "")
        self.assertEqual(len(fx.resolve({"text": "x" * 1000})["text"]), 200)

    def test_scrolls_on_the_peak_only(self):
        cols, L, _ = run({"sparkle": 0, "border": "off"}, frames=60)
        valid(self, cols, L.n)
        peak = set(L.indices("Peak"))
        self.assertGreater(lit(cols, peak), 10)
        self.assertEqual(lit(cols, [i for i in range(L.n) if i not in peak]), 0)

    def test_text_moves_and_glides(self):
        L = house()
        sc = Scene.from_dict({"layers": [{"effect": "text", "params": {"sparkle": 0, "border": "off"},
                                          "target": "Peak"}]})
        frames = [sc.render(k / 20, L) for k in range(120)]
        a, b = frames[80], frames[81]
        self.assertNotEqual(a, b)
        # anti-aliasing: some bulbs are partly lit while the text slides
        partial = [c for c in b if 0.05 < max(c) < 0.9]
        self.assertTrue(partial)

    def test_text_enters_from_the_right(self):
        L = house()
        sc = Scene.from_dict({"layers": [{"effect": "text", "params": {"sparkle": 0, "border": "off"},
                                          "target": "Peak"}]})
        first = sc.render(0, L)
        self.assertEqual(lit(first, L.indices("Peak")), 0)      # starts blank
        for k in range(1, 21):
            cols = sc.render(k / 20, L)
        xs = [L.pixels[i].x for i in L.indices("Peak") if max(cols[i]) > 0.3]
        mid = sum(L.pixels[i].x for i in L.indices("Peak")) / len(L.indices("Peak"))
        self.assertTrue(xs and min(xs) > mid)

    def test_letters_upright_columns_line_up(self):
        # a still "I": the top and bottom bars are 3 columns, the stem one column, centred
        cols, L, _ = run({"text": "I", "speed": 0, "sparkle": 0, "border": "off", "color_mode": "solid"},
                         frames=2)
        rows = geometry.rows(L, "Peak")
        stems = [[L.pixels[i].x for i in r if max(cols[i]) > 0.5] for r in rows[1:4]]
        centre = [sum(s) / len(s) for s in stems if s]
        self.assertEqual(len(centre), 3)
        self.assertLess(max(centre) - min(centre), 0.5)
        bottom = [i for i in rows[0] if max(cols[i]) > 0.5]
        self.assertGreater(len(bottom), len([i for i in rows[2] if max(cols[i]) > 0.5]))

    def test_deterministic(self):
        p = {"sparkle": 0.8, "color_mode": "rainbow"}
        a, _, _ = run(p, frames=30)
        b, _, _ = run(p, frames=30)
        self.assertEqual(a, b)

    def test_all_params_and_targets_are_safe(self):
        L = house()
        fx = EFFECTS["text"]
        variants = [{}]
        for prm in fx.params:
            if prm.kind in ("number", "int"):
                variants += [{prm.name: prm.min}, {prm.name: prm.max}]
            elif prm.kind == "choice":
                variants += [{prm.name: o} for o in prm.options]
        variants += [{"text": ""}, {"text": "~~~"}, {"text": "<3 " * 70}, {"colors": []},
                     {"date": "nonsense"}, {"date": "02-29"}, {"label": ""}, {"speed": 0, "text": ""}]
        for target in ("Peak", "all", "Eaves", "Accent", "Front gable outline", "Zigzag top half",
                       ["Peak", "Porch"], "nonexistent"):
            for v in variants:
                sc = Scene.from_dict({"layers": [{"effect": "text", "params": v, "target": target}]})
                for k in range(3):
                    valid(self, sc.render(5 + k * 0.05, L), L.n)

    def test_empty_target_and_dt(self):
        L = house()
        fn = EFFECTS["text"].fn
        P = EFFECTS["text"].resolve({})
        st = {}
        out = fn(0.0, Ctx(L, [], 0.0, 0.0, 120, {}), P, st)
        self.assertEqual(len(out), L.n)
        idx = L.indices("Peak")
        for dt in (0.0, 5.0, 1e6, -1.0):
            out = fn(1.0, Ctx(L, idx, 1.0, dt, 120, {}), P, st)
            valid(self, out, L.n)

    def test_layout_change(self):
        L1, L2 = house(), house()
        sc = Scene.from_dict({"layers": [{"effect": "text", "target": "Peak"}]})
        valid(self, sc.render(1.0, L1), L1.n)
        valid(self, sc.render(1.05, L2), L2.n)

    def test_border_on_top_row(self):
        L = house()
        cols, _, _ = run({"text": "", "border": "glow"}, frames=3, L=L)
        top = geometry.rows(L, "Peak")[-1]
        self.assertTrue(all(max(cols[i]) > 0.2 for i in top))
        rest = [i for i in L.indices("Peak") if i not in top]
        self.assertEqual(lit(cols, rest), 0)

    def test_sources(self):
        now = time.mktime(datetime.datetime(2026, 12, 1, 19, 42).timetuple())
        scrolltext._now = lambda: now
        P = EFFECTS["text"].resolve({"source": "clock"})
        self.assertEqual(scrolltext.build_message(P, now)[0], "7:42")
        P = EFFECTS["text"].resolve({"source": "clock", "clock": "24h"})
        self.assertEqual(scrolltext.build_message(P, now)[0], "19:42")
        P = EFFECTS["text"].resolve({"source": "clock", "clock": "12h am/pm"})
        self.assertEqual(scrolltext.build_message(P, now)[0], "7:42 PM")
        P = EFFECTS["text"].resolve({"source": "days"})
        self.assertEqual(scrolltext.build_message(P, now)[0], "24 DAYS TO XMAS")
        P = EFFECTS["text"].resolve({"source": "days", "date": "12-02", "label": "PARTY"})
        self.assertEqual(scrolltext.build_message(P, now)[0], "1 DAY TO PARTY")
        P = EFFECTS["text"].resolve({"source": "days", "date": "12-01", "text": "TODAY!"})
        self.assertEqual(scrolltext.build_message(P, now)[0], "TODAY!")
        P = EFFECTS["text"].resolve({"source": "days", "date": "01-01", "label": "2027"})
        self.assertEqual(scrolltext.build_message(P, now)[0], "31 DAYS TO 2027")
        P = EFFECTS["text"].resolve({"text": "{days} TO GO {time}"})
        self.assertEqual(scrolltext.build_message(P, now)[0], "24 TO GO 7:42")

    def test_score_source(self):
        livedata.LIVE["games"] = {}
        P = EFFECTS["text"].resolve({"source": "score", "text": "GO BRONCOS"})
        self.assertEqual(scrolltext.build_message(P, 0)[0], "GO BRONCOS")
        livedata.set_games({"nfl:DEN": {**GAME, "state": "pre"}})
        self.assertEqual(scrolltext.build_message(P, 0)[0], "GO BRONCOS")
        livedata.set_games({"nfl:DEN": GAME, "junk": "x"})
        msg, extra = scrolltext.build_message(P, 0)
        self.assertEqual(msg, "DEN 17  PIT 10")
        self.assertEqual(extra["accent"][:2], (0, 6))
        P2 = EFFECTS["text"].resolve({"source": "score", "score_detail": True})
        self.assertEqual(scrolltext.build_message(P2, 0)[0], "DEN 17  PIT 10  10:32 3RD")
        livedata.set_games({"nfl:DEN": {**GAME, "score": "bad"}})
        self.assertEqual(scrolltext.build_message(P, 0)[0], "GO BRONCOS")

    def test_score_change_flashes(self):
        L = house()
        livedata.set_games({"nfl:DEN": GAME})
        sc = Scene.from_dict({"layers": [{"effect": "text", "target": "Peak",
                                          "params": {"source": "score", "sparkle": 0}}]})
        for k in range(10):
            sc.render(k / 20, L)
        st = sc.layers[0].state
        self.assertEqual(st.get("flash", 0), 0)
        livedata.set_games({"nfl:DEN": {**GAME, "score": 24}})
        sc.render(0.55, L)
        self.assertGreater(st["flash"], 0.9)
        for k in range(12, 200):
            valid(self, sc.render(k / 20, L), L.n)
        self.assertEqual(st["flash"], 0)

    def test_negative_and_still(self):
        for p in ({"speed": -4}, {"speed": 0, "text": "24"}):
            cols, L, _ = run({**p, "sparkle": 0}, frames=60)
            valid(self, cols, L.n)
            self.assertGreater(lit(cols, L.indices("Peak")), 5)

    def test_days_until(self):
        now = time.mktime(datetime.datetime(2026, 12, 26, 12).timetuple())
        self.assertEqual(scrolltext.days_until("12-25", now), 364)
        self.assertEqual(scrolltext.parse_date("13-40"), (12, 25))
        self.assertEqual(scrolltext.parse_date("1/1"), (1, 1))
        self.assertGreaterEqual(scrolltext.days_until("02-29", now), 0)

    def test_presets_load_and_render(self):
        L = house()
        files = glob.glob("presets/text-*.json")
        self.assertGreaterEqual(len(files), 4)
        for f in files:
            with open(f, encoding="utf-8") as fh:
                sc = Scene.from_dict(json.load(fh))
            for k in range(5):
                valid(self, sc.render(k / 20, L), L.n)


class ControllerHookTests(unittest.TestCase):
    def test_controller_publishes_games(self):
        import inspect
        from goveelights import controller
        self.assertIn("livedata.set_games", inspect.getsource(controller.Controller._live_loop))


if __name__ == "__main__":
    unittest.main()
