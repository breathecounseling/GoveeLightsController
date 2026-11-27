"""Round 15: the marquee on the Peak (owner, round 3: "they all just flash the letter
without a marquee"). Every built-in word preset now scrolls (its one-big-letter version
stays as a "(big letters)" twin); the marquee stops on each
letter in the middle of the Peak (dwell) with the letter alone on a dark Peak, then moves
on. Letters measured in TRUE proportions (rows hexcanvas.aspect bulb spacings apart),
with and without the Tip, and a dead bulb never shifts a letter."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).parent))

from goveelights import addrun, hexfont  # noqa: E402
from goveelights.effects_ext import hexcanvas  # noqa: E402
from goveelights.effects_ext import peak_text as PT  # noqa: E402
from goveelights.layout import Layout  # noqa: E402
from goveelights.scene import Scene  # noqa: E402

from test_r10_showpieces import EXAMPLE, HOUSE, layout  # noqa: E402

FPS = 20
SCROLL = ["text-give-thanks", "text-go-broncos", "text-happy-new-year",
          "text-merry-christmas", "text-scoreboard", "text-touchdown"]


def house(tip=False, dead=()):
    L = layout(HOUSE if HOUSE.exists() else EXAMPLE)
    if tip:
        L = addrun.layout_with_tip(L, visible=12, flip=False, rows=addrun.parse_rows(""))
    if dead:
        runs = addrun.copy_runs(L)
        for r in runs:
            if r.name == "Peak":
                r.dead = set(dead)
        L = Layout(runs, L.zone_specs, L.aliases)
    return L


def scene(text, **kw):
    p = {"text": text, "mode": "marquee", "colors": ["#ffffff"], "color_mode": "solid", "eaves": "off",
         "wall": "off", "speed": 4.0, "dwell": 0.6, **kw}
    return {"name": "t", "layers": [{"effect": "peak_text", "target": "Peak", "params": p}]}


def stop_times(lat, text, speed, dwell):
    """(glyph, stop step, the middle of its dwell in seconds) for each letter."""
    out, t, s0 = [], 0.0, 0
    letters = [g for g in hexfont.line_layout(text) if g[0] != " "]
    for (ch, gx, w), k in zip(letters, PT.marquee_stops(lat, text)):
        t += (k - s0) / speed
        out.append((ch, gx, k, t + dwell / 2))
        t += dwell
        s0 = k
    return out


def lit(frame, idx, th=0.3):
    return {i for i in idx if max(frame[i]) > th}


class Stops(unittest.TestCase):
    def setUp(self):
        self.L = house()
        self.lat = hexfont.lattice(self.L, self.L.indices("Peak"))

    def test_one_stop_per_letter_each_whole_and_centred(self):
        lt, rt = self.lat.width_at(self.lat.peak_rows - 1)
        for text in ("GO BRONCOS!", "MERRY XMAS", "HAPPY NEW YEAR!", "TOUCHDOWN!", "GIVE THANKS", "MW 2026 <3"):
            letters = [g for g in hexfont.line_layout(text) if g[0] != " "]
            stops = PT.marquee_stops(self.lat, text)
            self.assertEqual(len(stops), len(letters), text)
            self.assertEqual(stops, sorted(set(stops)), text)
            for (ch, gx, w), k in zip(letters, stops):
                x = PT._marquee_x(self.lat, k) + gx
                self.assertEqual(hexfont.off_peak(self.lat, ch, x), 0, (text, ch))
                self.assertLessEqual(abs(x + w / 2 - (lt + rt) / 2), 1.5, (text, ch))

    def test_position_stands_still_at_each_stop(self):
        self.assertEqual(PT.marquee_position(0.0, 4, [8], 1.0), (0.0, 0.0))
        self.assertEqual(PT.marquee_position(1.0, 4, [8], 1.0)[0], 4.0)
        s, still = PT.marquee_position(2.5, 4, [8], 1.0)
        self.assertEqual((s, still), (8.0, 1.0))
        self.assertEqual(PT.marquee_position(3.25, 4, [8], 1.0), (9.0, 0.0))
        self.assertEqual(PT.marquee_position(3.0, 4, [8], 0.0), (12.0, 0.0))     # dwell 0: a steady scroll


class ScrollingPresets(unittest.TestCase):
    def test_every_word_preset_scrolls(self):
        L = house()
        lat = hexfont.lattice(L, L.indices("Peak"))
        peak = [i for i, _, _ in lat.cells]
        for pid in SCROLL:
            d = json.loads((ROOT / "presets" / f"{pid}.json").read_text())
            self.assertNotIn("(", d["name"], pid)                 # the plain name is the marquee
            P = [ly for ly in d["layers"] if ly["effect"] == "peak_text"][0]["params"]
            self.assertEqual(P["mode"], "marquee", pid)
            self.assertGreater(P["dwell"], 0.3, pid)          # stops on each letter
            self.assertLessEqual(P["speed"], 5, pid)           # slow enough to read
            sc = Scene.from_dict(d)
            frames = [sc.render(k / FPS, L) for k in range(0, 240)]
            shapes = {frozenset(lit(f, peak)) for f in frames}
            self.assertGreater(len(shapes), 12, pid)           # the letters move across, not flash in place
            # it really scrolls: a letter's lit bulbs slide left a bulb at a time
            xs = {i: X for i, X, _ in lat.cells}
            centres = []
            for f in frames:
                on = lit(f, peak)
                if on:
                    centres.append(sum(xs[i] for i in on) / len(on))
            self.assertTrue(any(b < a - 0.5 for a, b in zip(centres, centres[1:])), pid)


class TrueProportions(unittest.TestCase):
    """A letter at its stop, measured in bulb spacings as the gable hangs."""

    def test_letters_are_alone_and_in_sensible_proportions(self):
        for tip in (False, True):
            L = house(tip=tip)
            lat = hexfont.lattice(L, L.indices("Peak"))
            peak = [i for i, _, r in lat.cells if r < lat.peak_rows]       # (the Tip has its chase)
            pos = {i: (X, r) for i, X, r in lat.cells}
            hc = hexcanvas.get(L, L.indices("Peak"))
            aspect = hc.aspect
            self.assertAlmostEqual(aspect, 0.66, delta=0.1)
            text = "GO BRONCOS!"
            sc = Scene.from_dict(scene(text))
            todo = stop_times(lat, text, 4.0, 0.6)
            k, n = 0, int((todo[-1][3] + 0.1) * FPS)
            for fi in range(n + 1):
                f = sc.render(fi / FPS, L)
                if k < len(todo) and abs(fi / FPS - todo[k][3]) < 0.5 / FPS:
                    ch, gx, step, _ = todo[k]
                    want = set(hexfont.render_line(lat, ch, PT._marquee_x(lat, step) + gx))
                    on = lit(f, peak)
                    self.assertLessEqual(want, on, (tip, ch))                # the whole letter
                    self.assertEqual(lit(f, peak, 0.02), on, (tip, ch))      # no dim neighbour
                    if ch not in "S!":                                       # (S! both fit at once)
                        self.assertEqual(on, want, (tip, ch))                # the letter, alone
                    if ch.isalpha():
                        X = [pos[i][0] for i in want]
                        Y = [pos[i][1] * aspect for i in want]
                        w, h = max(X) - min(X) + 1, max(Y) - min(Y) + aspect
                        # about 3.3-4 bulb spacings tall, 3-7 wide: never the wide smear of
                        # a letter drawn a bulb per unit across and a row per unit up
                        self.assertTrue(3.0 <= h <= 4.5, (ch, h))
                        self.assertTrue(2.5 <= w <= 7.5, (ch, w))
                        self.assertLessEqual(w / h, 2.0, ch)
                    k += 1
            self.assertEqual(k, len(todo), tip)

    def test_tip_leaves_the_peak_letters_alone(self):
        a, b = house(), house(tip=True)
        la, lb = (hexfont.lattice(L, L.indices("Peak")) for L in (a, b))
        self.assertEqual(PT.marquee_stops(la, "TOUCHDOWN!"), PT.marquee_stops(lb, "TOUCHDOWN!"))
        sa, sb = Scene.from_dict(scene("TOUCHDOWN!")), Scene.from_dict(scene("TOUCHDOWN!"))
        pa = [i for i, _, _ in la.cells]
        for k in range(0, 200, 7):
            fa, fb = sa.render(k / FPS, a), sb.render(k / FPS, b)
            self.assertEqual(lit(fa, pa), lit(fb, pa), k)

    def test_a_dead_bulb_never_shifts_a_letter(self):
        # a bulb marked dead keeps its place: every other bulb lights exactly as before
        L0 = house()
        lat0 = hexfont.lattice(L0, L0.indices("Peak"))
        dead_i, dX = lat0.rows[2][len(lat0.rows[2]) // 2]       # a bulb in the middle of the window
        L1 = house(dead={L0.pixels[dead_i].local})
        lat1 = hexfont.lattice(L1, L1.indices("Peak"))
        self.assertEqual(len(lat1.cells), len(lat0.cells) - 1)
        self.assertEqual(PT.marquee_stops(lat0, "GO BRONCOS!"), PT.marquee_stops(lat1, "GO BRONCOS!"))
        where0 = {i: (X, r) for i, X, r in lat0.cells}
        where1 = {i: (X, r) for i, X, r in lat1.cells}
        s0, s1 = Scene.from_dict(scene("GO BRONCOS!")), Scene.from_dict(scene("GO BRONCOS!"))
        moved = 0
        for k in range(0, 300, 5):
            f0, f1 = s0.render(k / FPS, L0), s1.render(k / FPS, L1)
            a = {where0[i] for i in lit(f0, where0)} - {(dX, 2)}
            b = {where1[i] for i in lit(f1, where1)}
            self.assertEqual(a, b, k)
            moved += bool(a)
        self.assertGreater(moved, 20)

if __name__ == "__main__":
    unittest.main()
