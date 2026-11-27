"""Round 13: the text generator (Play -> Words on the Peak) and 'Text on the Peak' in both
modes: letter timing (gaps, word gaps, transitions, once), the marquee never showing a
dim cut-off letter, the Tip, the eaves and porch keeping time, the game score, the
fallback with no zigzag, the migrated text presets, and the /api/text endpoint."""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).parent))

from goveelights import addrun, hexfont, textgen  # noqa: E402
from goveelights.effects_ext import peak_text as PT  # noqa: E402

from test_r10_showpieces import EXAMPLE, HOUSE, layout, render  # noqa: E402

FPS = 20


def house():
    return layout(HOUSE if HOUSE.exists() else EXAMPLE)


def scene(target="Whole front", **params):
    p = {"text": "GO", "colors": ["#ff4000"], **params}
    return {"name": "t", "layers": [{"effect": "peak_text", "target": target, "params": p}]}


def peak_of(L):
    lat = hexfont.lattice(L, L.indices("Peak"))
    return lat, [i for i, _, _ in lat.cells]


def lit(frame, idx, th=0.3):
    return {i for i in idx if max(frame[i]) > th}


class Timeline(unittest.TestCase):
    def test_gaps(self):
        slots, cyc = PT.letter_timeline("AB C", 1.0, 2.0, 0.2, 0.6)
        self.assertEqual([(round(s, 3), h, k) for s, h, k in slots], [(0, 1.0, 0), (1.2, 1.0, 1), (2.8, 1.0, 3)])
        self.assertAlmostEqual(cyc, 5.8)

    def test_leading_and_double_spaces(self):
        slots, _ = PT.letter_timeline("  A  B", 1.0, 0.0, 0.2, 0.6)
        self.assertEqual([round(s, 3) for s, _, _ in slots], [0, 1.6])


class SingleLetter(unittest.TestCase):
    def setUp(self):
        self.L = house()
        self.lat, self.peak = peak_of(self.L)

    def test_letters_and_word_gap_in_time(self):
        # A at 0-1 s, dark 1-1.2, B 1.2-2.2, word gap 2.2-2.8, C 2.8-3.8
        fr = render(scene(text="AB C", hold=1.0, gap=0.2, word_gap=0.6, pause=2, transition="cut"),
                    self.L, frames=100, fps=FPS)
        want = {0.5: "A", 1.7: "B", 3.3: "C"}
        for t, ch in want.items():
            self.assertEqual(lit(fr[int(t * FPS)], self.peak), set(hexfont.render_big(self.lat, ch)), (t, ch))
        for t in (1.1, 2.5, 4.5):                   # the beat, the word gap, the pause
            self.assertEqual(lit(fr[int(t * FPS)], self.peak, 0.02), set(), t)

    def test_lower_case_and_unknown_characters(self):
        fr = render(scene(text="h~i", hold=1.0, gap=0.2, transition="cut"), self.L, frames=30, fps=FPS)
        self.assertEqual(lit(fr[10], self.peak), set(hexfont.render_big(self.lat, "H")))
        self.assertEqual(lit(fr[24], self.peak), set(hexfont.render_big(self.lat, "I")))

    def test_transitions(self):
        H = set(hexfont.render_big(self.lat, "H"))
        rowof = {i: r for i, _, r in self.lat.cells}
        cut = render(scene(text="H", hold=1.0, transition="cut", colors=["#ff0000"]), self.L, frames=3, fps=FPS)
        self.assertTrue(all(cut[1][i][0] > 0.99 for i in H))
        wipe = render(scene(text="H", hold=1.0, transition="wipe"), self.L, frames=4, fps=FPS)
        rows = {rowof[i] for i in lit(wipe[2], self.peak)}         # 0.1 s: the bottom rows only
        self.assertTrue(rows and max(rows) <= 3, rows)
        self.assertEqual(lit(render(scene(text="H", hold=1.0, transition="wipe"), self.L, frames=12, fps=FPS)[11],
                             self.peak), H)
        flash = render(scene(text="H", hold=1.0, transition="flash", colors=["#ff0000"]), self.L, frames=3, fps=FPS)
        self.assertTrue(all(min(flash[0][i]) > 0.9 for i in H))     # white first
        fade = render(scene(text="H", hold=1.0, transition="fade", colors=["#ff0000"]), self.L, frames=3, fps=FPS)
        self.assertTrue(all(fade[0][i][0] < 0.05 for i in H))       # from dark

    def test_once(self):
        fr = render(scene(text="HI", hold=0.5, gap=0.2, pause=0.5, repeat="once"), self.L, frames=60, fps=FPS)
        self.assertTrue(lit(fr[5], self.peak))
        for k in range(36, 60):                     # after one pass (1.7 s) it stays dark
            self.assertEqual(lit(fr[k], self.peak, 0.02), set(), k)
        loop = render(scene(text="HI", hold=0.5, gap=0.2, pause=0.5), self.L, frames=45, fps=FPS)
        self.assertTrue(lit(loop[40], self.peak))   # 2.0 s: the H again

    def test_with_and_without_the_tip(self):
        for tip in (False, True):
            L = addrun.layout_with_tip(house(), visible=12, flip=False, rows=addrun.parse_rows("")) if tip else house()
            lat = hexfont.lattice(L, L.indices("Peak"))
            self.assertEqual(lat.peak_rows, 6)
            fr = render(scene(target="Peak", text="E", hold=1.0, transition="cut"), L, frames=12, fps=FPS)
            on = {i for i in L.indices("Peak") if max(fr[10][i]) > 0.3}
            self.assertEqual(on - set(lat.tip), set(hexfont.render_big(lat, "E")))
            if tip:
                low = {i for i, _ in lat.rows[6]}
                self.assertFalse(on & low)               # the Tip's lowest row stays dark
                self.assertTrue(on & (set(lat.tip) - low))   # the crown


class Marquee(unittest.TestCase):
    def setUp(self):
        self.L = house()
        self.lat, self.peak = peak_of(self.L)

    def test_no_dim_preview_on_the_flanks(self):
        # white letters: at the start of each step (no cross-fade), a lit bulb is part of a
        # letter that is on the Peak (full), or cut by a bulb or two (0.7) - never a dim preview
        fr = render(scene(mode="marquee", text="MERRY CHRISTMAS", colors=["#ffffff"], color_mode="solid", speed=4),
                    self.L, frames=400, fps=FPS)
        seen = cut = 0
        for k in range(0, 400, 5):                  # every step (4 a second)
            for i in self.peak:
                v = max(fr[k][i])
                if v > 0.02:
                    seen += 1
                    if v < 0.69:                    # a clipped letter: clearly dimmer, never a ghost
                        cut += 1
                        self.assertAlmostEqual(v, PT.CUT, delta=0.03, msg=(k, i, v))
        self.assertGreater(seen, 200)
        self.assertGreater(cut, 0)

    def test_clipped_letters_are_clearly_dimmer(self):
        # review: at 0.7 a letter cut by the slopes looked full from the street; at about a
        # third it reads as "on its way in" and the whole letter stands out
        self.assertLessEqual(PT.CUT, 0.4)
        self.assertGreaterEqual(PT.CUT, 0.25)

    def test_moves_and_colours_letters_by_default(self):
        fr = render(scene(mode="marquee", text="HI HO", colors=["#ff0000", "#00ff00"], speed=6), self.L,
                    frames=200, fps=FPS)
        cols = set()
        for f in fr:
            for i in self.peak:
                if max(f[i]) > 0.5:
                    cols.add(0 if f[i][0] > f[i][1] else 1)
        self.assertEqual(cols, {0, 1})              # 'letters' colouring by default
        lits = [frozenset(lit(fr[k], self.peak)) for k in range(0, 200, 10)]
        self.assertGreater(len(set(lits)), 5)

    def test_tip_chase(self):
        L = addrun.layout_with_tip(house(), visible=12, flip=False, rows=addrun.parse_rows(""))
        lat = hexfont.lattice(L, L.indices("Peak"))
        fr = render(scene(target="Peak", mode="marquee", text="HI"), L, frames=40, fps=FPS)
        a = {i for i in lat.tip if max(fr[2][i]) > 0.3}
        b = {i for i in lat.tip if max(fr[7][i]) > 0.3}
        self.assertTrue(a and b and a != b)


class Trim(unittest.TestCase):
    def setUp(self):
        self.L = house()
        self.lat, self.peak = peak_of(self.L)
        tgt = self.L.indices("Whole front")
        rest = [i for i in tgt if i not in set(self.peak)]
        px = self.L.pixels
        self.eaves = [i for i in rest if px[i].run == "Eaves"]
        self.wall = [i for i in rest if px[i].run == "Accent"]
        self.assertTrue(self.eaves and self.wall)

    def test_eaves_pulse_and_wall_follows(self):
        fr = render(scene(text="AB", colors=["#ff0000", "#0000ff"], hold=1.0, gap=0.3, eaves="pulse", wall="follow"),
                    self.L, frames=60, fps=FPS)
        e = lambda k: sum(max(fr[k][i]) for i in self.eaves)    # noqa: E731
        self.assertGreater(e(2), e(18))             # flashes as the letter comes, then settles
        w = fr[18][self.wall[0]]
        self.assertGreater(w[0], 5 * w[2])          # the wall in the A's red
        w = fr[50][self.wall[0]]
        self.assertGreater(w[2], 5 * w[0])          # then the B's blue

    def test_fixed_wall_echo_and_off(self):
        fr = render(scene(text="A", colors=["#ff0000"], eaves="echo", wall="fixed", wall_color="#00ff00"),
                    self.L, frames=12, fps=FPS)
        self.assertGreater(fr[10][self.wall[0]][1], 0.2)
        self.assertGreater(fr[10][self.eaves[0]][0], 0.3)
        fr = render(scene(text="A", eaves="off", wall="off"), self.L, frames=12, fps=FPS)
        self.assertFalse(any(max(fr[10][i]) > 0 for i in self.eaves + self.wall))

    def test_chase_steps_with_letters(self):
        fr = render(scene(text="ABC", eaves="chase", hold=0.5, gap=0.2), self.L, frames=30, fps=FPS)
        a = {i for i in self.eaves if max(fr[5][i]) > 0.4}
        b = {i for i in self.eaves if max(fr[19][i]) > 0.4}
        self.assertTrue(a and b and a != b)


class Score(unittest.TestCase):
    GAME = {"team": "DEN", "score": 17, "opponent": "PIT", "opp_score": 10, "state": "in", "colors": ["#fc4c02"],
            "id": "g1"}

    def test_score_one_letter_at_a_time_in_the_team_colour(self):
        L = house()
        lat, peak = peak_of(L)
        with mock.patch("goveelights.livedata.current_game", return_value=dict(self.GAME)):
            fr = render(scene(source="score", text="GO", colors=["#ffffff"], color_mode="solid", hold=0.5, gap=0.2,
                              transition="cut"), L, frames=40, fps=FPS)
        self.assertEqual(lit(fr[5], peak), set(hexfont.render_big(lat, "D")))
        c = fr[5][next(iter(hexfont.render_big(lat, "D")))]
        self.assertGreater(c[0], 3 * c[2])                         # DEN in the team's orange

    def test_no_game_shows_the_message(self):
        L = house()
        lat, peak = peak_of(L)
        with mock.patch("goveelights.livedata.current_game", return_value=None):
            fr = render(scene(source="score", text="GO", hold=0.5, transition="cut"), L, frames=10, fps=FPS)
        self.assertEqual(lit(fr[5], peak), set(hexfont.render_big(lat, "G")))


class Fallback(unittest.TestCase):
    def test_a_straight_run_gets_the_flat_marquee(self):
        from goveelights.layout import Layout, Run
        L = Layout([Run("a", "Front roof", 150, geometry=[[0, 0, 0], [149, 149, 0]])])
        from goveelights.scene import Scene
        sc = Scene.from_dict(scene(target="all", text="HELLO", mode="single"))
        frames = [sc.render(k / 20, L) for k in range(40)]
        on = [sum(1 for c in f if max(c) > 0.05) for f in frames]
        self.assertTrue(all(0 < n < L.n for n in on))
        self.assertGreater(len({tuple(f) for f in frames}), 5)


class Presets(unittest.TestCase):
    def test_text_presets_use_the_honeycomb_font_single(self):
        for p in sorted((ROOT / "presets").glob("text-*.json")):
            d = json.loads(p.read_text())
            layers = [ly for ly in d["layers"] if ly["effect"] in ("text", "peak_text")]
            self.assertEqual([ly["effect"] for ly in layers], ["peak_text"], p.name)
            self.assertEqual(layers[0]["params"]["mode"], "single", p.name)

    def test_old_text_params_still_play(self):
        # a user's own preset from before keeps working with the old effect and its params
        L = house()
        lat, peak = peak_of(L)
        fr = render({"name": "old", "layers": [{"effect": "text", "target": "Peak", "params": {
            "text": "HI", "speed": 4.0, "size": 1.4, "border": "marquee", "sparkle": 0}}]}, L, frames=40, fps=FPS)
        self.assertTrue(any(lit(f, peak) for f in fr))


class Generator(unittest.TestCase):
    def test_scene(self):
        sc = textgen.scene({"text": "  go   broncos ", "mode": "marquee", "colors": ["#ff4f00", "bad"], "speed": 99})
        P = sc["layers"][0]["params"]
        self.assertEqual((P["text"], P["mode"], P["colors"], P["speed"]), ("go broncos", "marquee", ["#ff4f00"], 12))
        self.assertEqual(sc["name"], "Go Broncos")
        self.assertEqual(sc["layers"][0]["effect"], "peak_text")
        self.assertEqual(textgen.scene({"text": "hi", "mode": "?"})["layers"][0]["params"]["mode"], "single")
        with self.assertRaises(ValueError):
            textgen.scene({"text": " ~~ "})
        self.assertEqual(textgen.unsupported("Hi~ é <3"), ["~", "É"])

    def test_preview_starts_at_the_first_letter(self):
        L = house()
        lat, peak = peak_of(L)
        sc = textgen.scene({"text": "OK", "hold": 1.0})
        pv = textgen.preview(sc, L, seconds=textgen.length(sc), fps=5)
        self.assertEqual(len(pv["frames"]), int(textgen.length(sc) * 5))
        f = pv["frames"][3]                                      # 0.6 s: the O
        on = {i for i in peak if f[i * 6:i * 6 + 6] != "000000" and int(f[i * 6:i * 6 + 2], 16) > 100}
        self.assertEqual(on, set(hexfont.render_big(lat, "O")))

    def test_length_is_the_house_pass(self):
        # review: the phone looped on a 0.4 s end pause while the house waits 1.6 s
        sc = textgen.scene({"text": "GO BRONCOS", "hold": 0.8})
        want = PT.letter_timeline("GO BRONCOS", 0.8, 1.6, 0.22, 0.65)[1]
        self.assertAlmostEqual(textgen.length(sc), want, places=6)
        sc["layers"][0]["params"]["pause"] = 3.0
        self.assertAlmostEqual(textgen.length(sc), PT.letter_timeline("GO BRONCOS", 0.8, 3.0, 0.22, 0.65)[1])
        mq = textgen.scene({"text": "GO", "mode": "marquee", "speed": 4})
        self.assertAlmostEqual(textgen.length(mq), (hexfont.line_width("GO") + 16) / 4 + 1.6)

    def test_long_phrase_previews_thirty_seconds_and_says_so(self):
        # review: a 60-letter phrase previewed only its first 16 s (about 14 letters)
        L = house()
        long_ = textgen.scene({"text": "MERRY CHRISTMAS AND A HAPPY NEW YEAR FROM ALL OF US HERE 2026"})
        self.assertGreater(textgen.length(long_), 30)
        pv = textgen.preview(long_, L, seconds=textgen.length(long_), fps=6)
        self.assertEqual(len(pv["frames"]), 30 * 6)
        self.assertTrue(pv["partial"])
        short = textgen.scene({"text": "GO"})
        pv = textgen.preview(short, L, seconds=textgen.length(short), fps=6)
        self.assertFalse(pv["partial"])

    def test_chip_and_mode_taps_preview_at_once(self):
        # review: a chip tap waited out the typing debounce while the old colours played on
        html = (ROOT / "goveelights" / "web" / "app.html").read_text()
        for sel in ("$('txtCols').querySelectorAll('.chip').forEach(", "document.querySelectorAll('#txtMode button').forEach("):
            line = next(ln for ln in html.splitlines() if sel in ln and "onclick" in ln)
            self.assertIn("txtPreview.now()", line, sel)
        self.assertIn("r.partial", html)

    def test_api_preview_play_save(self):
        from goveelights import controller as C
        from goveelights.app_server import Server, make_handler
        from goveelights.auth import Auth
        from test_integration import Rig
        r = Rig(self, datetime(2026, 11, 26, 17, 0), {"schedule": {"enabled": False}})
        ctl = r.ctl
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        ctl.presets = C.PresetStore(ROOT / "presets", user=tmp / "user")
        srv = Server(("127.0.0.1", 0), make_handler(ctl, lambda: None, Auth(tmp / "auth.json")))
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        base = f"http://127.0.0.1:{srv.server_address[1]}/api/"

        def post(body):
            req = urllib.request.Request(base + "text", json.dumps(body).encode(),
                                         {"Content-Type": "application/json", "X-Requested-With": "lights"})
            try:
                with urllib.request.urlopen(req, timeout=10) as f:
                    return f.status, json.loads(f.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read())

        st, res = post({"text": "Joy~", "action": "preview"})
        self.assertEqual(st, 200)
        self.assertEqual(res["skipped"], ["~"])
        self.assertTrue(res["frames"])
        self.assertEqual(len(res["frames"][0]), 6 * len(ctl.engine.layout.pixels))
        st, res = post({"text": "Go Broncos", "mode": "marquee", "action": "play"})
        self.assertEqual(st, 200)
        self.assertEqual(ctl.playing["name"], "Go Broncos")
        self.assertEqual(ctl.engine.scene.layers[0].effect, "peak_text")
        self.assertEqual(ctl.engine.scene.layers[0].params["mode"], "marquee")
        st, res = post({"text": "Happy Birthday", "action": "save", "name": "Birthday"})
        self.assertEqual((st, res["id"]), (200, "birthday"))
        saved = ctl.presets.get("birthday")
        self.assertEqual((saved["name"], saved["layers"][0]["params"]["text"]), ("Birthday", "Happy Birthday"))
        st, res = post({"text": "  ", "action": "play"})
        self.assertNotEqual(st, 200)
        st, res = post({"text": "x", "action": "nope"})
        self.assertNotEqual(st, 200)


if __name__ == "__main__":
    unittest.main()
