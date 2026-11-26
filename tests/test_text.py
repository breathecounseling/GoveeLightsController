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
        self.assertEqual(msg, "DEN 17 - PIT 10")
        self.assertEqual(extra["accent"][:2], (0, 6))
        self.assertEqual(extra["dim"], (6, 9))
        P2 = EFFECTS["text"].resolve({"source": "score", "score_detail": True})
        self.assertEqual(scrolltext.build_message(P2, 0)[0], "DEN 17 - PIT 10  10:32 3RD")
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


def drive(steps, L=None, params=None, target="Peak", st=None, t0=0.0, tr0=0.0, fps=20.0, cache=None):
    """Run the effect directly with the engine's travel integration; steps is a list of
    (seconds, param overrides). Returns (frames, state, t, travel)."""
    L = L or house()
    fx = EFFECTS["text"]
    idx = L.indices(target) if target else []
    st = {} if st is None else st
    cache = {} if cache is None else cache
    t, tr, frames = t0, tr0, []
    base = {"sparkle": 0, "border": "off", **(params or {})}
    for secs, over in steps:
        P = fx.resolve({**base, **over})
        for _ in range(int(round(secs * fps))):
            dt = 1.0 / fps
            t += dt
            tr += P["speed"] * dt
            P["_travel"] = tr
            frames.append(fx.fn(t, Ctx(L, idx, t, dt, 120, cache), P, st))
    return frames, st, t, tr


def change(a, b, idx):
    return sum(abs(x - y) for i in idx for x, y in zip(a[i], b[i]))


class ScrollRobustnessTests(unittest.TestCase):
    def setUp(self):
        self._now = scrolltext._now
        self.L = house()
        self.peak = self.L.indices("Peak")

    def tearDown(self):
        scrolltext._now = self._now

    def test_speed_reversal_keeps_text_on_screen(self):
        for a, b in ((5, -5), (-5, 5), (3.5, -3.5)):
            frames, st, _, _ = drive([(60, {"speed": a}), (20, {"speed": b})], L=self.L)
            after = frames[60 * 20:]
            # the display is never dark for longer than one pass of the message
            W = font.layout("MERRY CHRISTMAS").width * 1.2 * 0.7
            limit = int((19 + W) / (abs(b) * 0.7) * 20)
            run_dark = worst = 0
            for f in after:
                run_dark = 0 if lit(f, self.peak) else run_dark + 1
                worst = max(worst, run_dark)
            self.assertLess(worst, limit, (a, b))
            self.assertGreater(sum(1 for f in after if lit(f, self.peak)), len(after) // 2, (a, b))
            # and the text turns round smoothly: no jump at the reversal
            k = 60 * 20
            typical = sorted(change(frames[j], frames[j + 1], self.peak) for j in range(k - 200, k - 1))[150]
            self.assertLess(change(frames[k - 1], frames[k], self.peak), 3 * typical + 1, (a, b))

    def test_stop_and_start(self):
        frames, st, _, _ = drive([(10, {}), (3, {"speed": 0}), (6, {})], L=self.L)
        k = 10 * 20
        self.assertTrue(all(lit(f, self.peak) for f in frames[k + 10:k + 60]))     # still: centred text
        # fading between scroll and still: no single-frame jump
        diffs = [change(frames[j], frames[j + 1], self.peak) for j in range(k - 5, k + 15)]
        self.assertLess(max(diffs), 40)
        self.assertTrue(any(lit(f, self.peak) for f in frames[k + 61:]))          # starts scrolling again

    def test_live_message_change_waits_for_next_pass(self):
        # the clock goes 9:59 -> 10:00 mid-scroll: the copy on screen keeps its text
        now = [time.mktime(datetime.datetime(2026, 12, 1, 9, 59, 50).timetuple())]
        scrolltext._now = lambda: now[0]
        frames, st, t, tr = drive([(4, {})], L=self.L, params={"source": "clock"})
        before = [c["msg"] for c in st["copies"]]
        self.assertIn("9:59", before)
        now[0] += 15
        more, st, t2, tr2 = drive([(0.05, {})], L=self.L, params={"source": "clock"}, st=st, t0=t, tr0=tr)
        self.assertEqual([c["msg"] for c in st["copies"]][:len(before)], before)
        typical = sorted(change(frames[j], frames[j + 1], self.peak) for j in range(40, 79))[30]
        self.assertLess(change(frames[-1], more[0], self.peak), 2 * typical + 1)
        drive([(30, {})], L=self.L, params={"source": "clock"}, st=st, t0=t2, tr0=tr2)
        self.assertIn("10:00", [c["msg"] for c in st["copies"]])

    def test_still_message_cross_fades(self):
        now = [time.mktime(datetime.datetime(2026, 12, 1, 9, 59, 50).timetuple())]
        scrolltext._now = lambda: now[0]
        frames, st, t, tr = drive([(1, {"speed": 0})], L=self.L, params={"source": "clock"})
        now[0] += 15
        more, st, _, _ = drive([(1, {"speed": 0})], L=self.L, params={"source": "clock"}, st=st, t0=t, tr0=tr)
        diffs = [change(a, b, self.peak) for a, b in zip([frames[-1]] + more, more)]
        self.assertGreater(sum(diffs), 10)                       # it did change
        self.assertLess(max(diffs), 0.35 * sum(diffs))           # ... over several frames
        self.assertEqual(st["copies"][-1]["msg"], "10:00")

    def test_editing_text_restarts_cleanly(self):
        frames, st, t, tr = drive([(5, {})], L=self.L)
        drive([(10, {"text": "HO HO HO"})], L=self.L, st=st, t0=t, tr0=tr)
        self.assertEqual(st["copies"][-1]["msg"], "HO HO HO")
        self.assertFalse(any(c["msg"] == "MERRY CHRISTMAS" for c in st["copies"]))

    def test_speed_and_size_are_in_zigzag_bulbs(self):
        _, st, t, tr = drive([(2, {"speed": 4})], L=self.L)
        c = st["copies"][0]
        x0 = scrolltext._copy_x(c, {"xc": 0}, tr * 0.7, 0)
        _, st, _, tr2 = drive([(1, {"speed": 4})], L=self.L, st=st, t0=t, tr0=tr)
        x1 = scrolltext._copy_x(c, {"xc": 0}, tr2 * 0.7, 0)
        self.assertAlmostEqual(x0 - x1, 4 * 0.7, delta=0.05)     # 4 zigzag bulbs (0.7 apart) a second
        for prm in EFFECTS["text"].params:
            if prm.name in ("speed", "size"):
                self.assertIn("zigzag bulbs", prm.help)

    def test_window_fade_dims_the_flanks(self):
        p = {"text": "MMMMMMMMMMMMMMMM", "speed": 5, "color_mode": "solid", "colors": ["#ffffff"]}
        full, _, _, _ = drive([(8, {})], L=self.L, params={**p, "window": "full"})
        fade, _, _, _ = drive([(8, {})], L=self.L, params={**p, "window": "fade"})
        rows = geometry.rows(self.L, "Peak")
        top = rows[4]
        xl, xr = min(self.L.pixels[i].x for i in top), max(self.L.pixels[i].x for i in top)
        flank = [i for i in rows[0] if self.L.pixels[i].x < xl - 3 or self.L.pixels[i].x > xr + 3]
        centre = [i for r in rows[:5] for i in r if xl <= self.L.pixels[i].x <= xr]
        self.assertTrue(flank)
        a, b = full[-1], fade[-1]
        self.assertEqual([b[i] for i in centre], [a[i] for i in centre])
        self.assertGreater(sum(max(a[i]) for i in flank), 0)
        self.assertLess(sum(max(b[i]) for i in flank), 0.3 * sum(max(a[i]) for i in flank))

    def test_huge_travel_jump_and_tiny_text(self):
        L = self.L
        fx = EFFECTS["text"]
        st, cache = {}, {}
        for tr in (0.0, 1e7, -1e7, 3.0):
            P = fx.resolve({"text": ".", "gap": 0, "size": 0.8})
            P["_travel"] = tr
            valid(self, fx.fn(1.0, Ctx(L, self.peak, 1.0, 0.05, 120, cache), P, st), L.n)
            self.assertLessEqual(len(st["copies"]), 80)

    def test_days_until_leap_day(self):
        for y in (2025, 2026, 2027, 2029):
            now = time.mktime(datetime.datetime(y, 3, 1, 12).timetuple())
            target = next(datetime.date(yy, 2, 29) for yy in range(y, y + 5) if yy % 4 == 0)
            self.assertEqual(scrolltext.days_until("02-29", now), (target - datetime.date(y, 3, 1)).days, y)

    def test_team_colour_skips_navy(self):
        c = scrolltext._team_colour({"colors": ["#0a2343", "#fc4c02"]})
        self.assertGreater(c[0], 0.9)
        self.assertLess(c[2], 0.1)                               # Broncos orange, not sky blue
        self.assertIsNone(scrolltext._team_colour({"colors": ["#0a2343", "#102010"]}))
        scrolltext._team_colour({"colors": ["bad", None, 3]})           # never raises
        self.assertGreater(scrolltext._team_colour({"colors": ["#c8102e", "#000000"]})[0], 0.9)


class ScoreTests(unittest.TestCase):
    def setUp(self):
        self._games = livedata.LIVE["games"]
        livedata.set_games({})

    def tearDown(self):
        livedata.LIVE["games"] = self._games
        livedata._queue.clear()

    def test_flash_only_when_we_score(self):
        L = house()
        livedata.set_games({"nfl:DEN": GAME})
        frames, st, t, tr = drive([(1, {})], L=L, params={"source": "score"})
        livedata.set_games({"nfl:DEN": {**GAME, "opp_score": 17}})
        frames, st, t, tr = drive([(0.1, {})], L=L, params={"source": "score"}, st=st, t0=t, tr0=tr)
        self.assertEqual(st["flash"], 0)
        self.assertGreater(st["dip"], 0.9)
        livedata.set_games({"nfl:DEN": {**GAME, "opp_score": 17, "score": 20}})
        frames, st, t, tr = drive([(0.1, {})], L=L, params={"source": "score"}, st=st, t0=t, tr0=tr)
        self.assertGreater(st["flash"], 0.9)
        self.assertEqual(st["flash_rgb"][0], 1.0)                # the team's orange
        # the flash is visible even with white letters: background and top row light up
        P = {"source": "score", "colors": ["#ffffff"], "color_mode": "solid", "border": "twinkle"}
        _, st2, t, tr = drive([(1, {})], L=L, params=P)
        livedata.set_games({"nfl:DEN": {**GAME, "opp_score": 17, "score": 27}})
        fl, st2, _, _ = drive([(1, {})], L=L, params=P, st=st2, t0=t, tr0=tr)
        rows = geometry.rows(L, "Peak")
        self.assertTrue(any(max(f[i]) > 0.2 and f[i][2] < 0.1 for f in fl[:20] for i in rows[0]))
        self.assertTrue(any(f[i][0] > 0.6 and f[i][2] < 0.2 for f in fl[:20] for i in rows[-1]))

    def test_delay_holds_scores_back(self):
        livedata.update_game("nfl:DEN", GAME, delay=30)
        self.assertIsNone(livedata.current_game())
        self.assertIsNone(livedata.current_game(time.time() + 29))
        self.assertEqual(livedata.current_game(time.time() + 31)["score"], 17)
        livedata.update_game("nfl:DEN", {**GAME, "score": 24}, delay=30)
        self.assertEqual(livedata.current_game(time.time() + 1)["score"], 17)
        self.assertEqual(livedata.current_game(time.time() + 31)["score"], 24)
        livedata.update_game("nfl:DEN", None, delay=30)            # ignored
        livedata.update_game("nfl:DEN", {**GAME, "score": 31}, delay="junk")   # no delay
        self.assertEqual(livedata.current_game(time.time() + 32)["score"], 31)

    def test_stale_and_failed_data_is_not_shown_as_live(self):
        livedata.update_game("nfl:DEN", GAME)
        now = time.time()
        self.assertIsNotNone(livedata.current_game(now + 60))
        livedata.update_game("nfl:DEN", {**GAME, "error": "timeout"})
        self.assertIsNotNone(livedata.current_game(now + 60))      # one failure: still recent data
        self.assertIsNone(livedata.current_game(now + livedata.STALE_IN + 5))
        # a team removed from the config is never refreshed, so it goes stale too
        livedata.set_games({})
        livedata.update_game("nfl:KC", {**GAME, "team": "KC", "id": "9"})
        self.assertIsNone(livedata.current_game(time.time() + livedata.STALE_IN + 5))

    def test_final_score_stays_for_a_while(self):
        post = {**GAME, "state": "post", "score": 24, "detail": "Final", "id": "402"}
        livedata.update_game("nfl:DEN", post)
        self.assertEqual(livedata.current_game()["state"], "post")
        P = EFFECTS["text"].resolve({"source": "score", "score_detail": True})
        msg, extra = scrolltext.build_message(P, time.time())
        self.assertEqual(msg, "FINAL DEN 24 - PIT 10")
        self.assertEqual(extra["accent"][:2], (6, 12))
        self.assertIsNone(livedata.current_game(time.time() + livedata.FINAL_FOR + 60))
        livedata.set_games({"nfl:DEN": {**post, "id": "77", "date": "2020-01-01T18:00Z"}})
        self.assertIsNone(livedata.current_game())


class ControllerHookTests(unittest.TestCase):
    def test_controller_publishes_games(self):
        import inspect
        from goveelights import controller
        self.assertIn("livedata.update_game", inspect.getsource(controller.Controller._live_loop))


class WordByWordTests(unittest.TestCase):
    def test_hold_shows_one_still_word_at_a_time(self):
        """hold > 0: each word of the message is shown on its own, centred and still, in
        turn (cross-fading between words); the default (0) scrolls as before."""
        p = {"text": "HI YO", "hold": 2, "border": "off", "sparkle": 0,
             "colors": ["#ff0000", "#00ff00"], "color_mode": "words"}
        a, L, sc = run(p, frames=30)                          # t = 1.45: the first word
        b = sc.render(1.5, L)
        idx = [i for i, q in enumerate(L.pixels) if q.run == "Peak"]
        self.assertEqual([a[i] for i in idx], [b[i] for i in idx])   # still within a word
        self.assertTrue(any(a[i][0] > 0.5 for i in idx))     # word 0 in the first colour
        self.assertFalse(any(a[i][1] > 0.5 for i in idx))
        for k in range(31, 61):                               # well into the second word
            c = sc.render(k / 20, L)
        self.assertTrue(any(c[i][1] > 0.5 for i in idx))     # word 1 in the second colour
        self.assertFalse(any(c[i][0] > 0.5 for i in idx))
        s, L2, sc2 = run({"text": "HI YO", "border": "off", "sparkle": 0}, frames=30)
        s2 = sc2.render(1.6, L2)
        self.assertNotEqual([s[i] for i in idx], [s2[i] for i in idx])   # default still scrolls


if __name__ == "__main__":
    unittest.main()
