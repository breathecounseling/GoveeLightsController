"""Round 13: a hex-native font for the Peak (goveelights/hexfont.py) and 'Text on the
Peak' (peak_text): type a phrase, show it one big letter at a time or as a marquee."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from goveelights import addrun, hexfont  # noqa: E402

from test_r10_showpieces import EXAMPLE, HOUSE, layout, render  # noqa: E402

ALNUM = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


def house():
    return layout(HOUSE if HOUSE.exists() else EXAMPLE)


class Lattice(unittest.TestCase):
    def test_owner_diagram(self):
        # 1..6 on the bottom row, 7..11 above, each between two below
        lat = hexfont.Lattice.ideal((6, 5))
        xs0 = [x for _, x in lat.rows[0]]
        xs1 = [x for _, x in lat.rows[1]]
        for x in xs1:
            self.assertTrue(any(abs(x - a - 0.5) < 1e-9 for a in xs0), x)

    def test_house_peak_is_a_honeycomb(self):
        L = house()
        lat = hexfont.lattice(L, L.indices("Peak"))
        self.assertEqual(lat.peak_rows, 6)
        self.assertEqual([len(r) for r in lat.rows], [27, 23, 20, 16, 12, 10])
        par = [r[0][1] % 1.0 for r in lat.rows]
        for a, b in zip(par, par[1:]):              # every row half a bulb across from the one below
            self.assertAlmostEqual(abs(a - b), 0.5)
        for row in lat.rows:                        # one bulb apart along a row
            xs = [x for _, x in row]
            self.assertTrue(all(abs(b - a - 1) < 1e-9 for a, b in zip(xs, xs[1:])))

    def test_tip_rows_continue_the_lattice(self):
        L = addrun.layout_with_tip(house(), visible=12, flip=False, rows=addrun.parse_rows(""))
        lat = hexfont.lattice(L, L.indices("Peak"))
        self.assertEqual(lat.peak_rows, 6)
        self.assertEqual([len(r) for r in lat.rows[6:]], [7, 4, 1])
        self.assertEqual(len(lat.tip), 12)


class Glyphs(unittest.TestCase):
    def setUp(self):
        L = house()
        self.lat = hexfont.lattice(L, L.indices("Peak"))

    def test_every_letter_and_digit_big_fills_the_peak_and_fits(self):
        seen = {}
        for ch in ALNUM:
            cov = hexfont.render_big(self.lat, ch)
            rows = {r for i, _, r in self.lat.cells if i in cov}
            self.assertEqual(rows, set(range(6)), ch)          # all six rows (no letter is short)
            self.assertTrue(all(v == 1.0 for v in cov.values()), ch)   # on or off, never half
            key = frozenset(cov)
            self.assertNotIn(key, seen, f"{ch} looks the same as {seen.get(key)}")
            seen[key] = ch
            x0 = -(-hexfont.width(ch, "big") // 2)
            self.assertEqual(hexfont.off_peak(self.lat, ch, -x0, "big"), 0, ch)

    def test_every_letter_and_digit_marquee(self):
        seen = {}
        for ch in ALNUM:
            cov = hexfont.render_line(self.lat, ch, -2)
            self.assertTrue(cov, ch)
            key = frozenset(cov)
            self.assertNotIn(key, seen, f"{ch} looks the same as {seen.get(key)}")
            seen[key] = ch

    def test_vertical_strokes_are_one_bulb_a_row_in_the_marquee(self):
        for x in (-3, -2, 0, 1):                    # whichever way the rows are offset
            cov = hexfont.render_line(self.lat, "L", x)
            per_row = {}
            for i, _, r in self.lat.cells:
                if i in cov:
                    per_row[r] = per_row.get(r, 0) + 1
            for r in range(1, 6):
                self.assertEqual(per_row.get(r), 1, (x, r))

    def test_big_stems_are_two_bulbs(self):
        cov = hexfont.render_big(self.lat, "H")
        for r in (0, 1, 2, 4, 5):                   # (the bar is on row 3)
            n = sum(1 for i, _ in self.lat.rows[r] if i in cov)
            self.assertEqual(n, 4, r)

    def test_text_is_normalised(self):
        self.assertEqual(hexfont.normalise("Go Broncos! <3 ~"), "GO BRONCOS! ♥ ")

    def test_big_glyphs_differ_by_five_bulbs_or_more(self):
        # one letter at a time, so any two must differ clearly; the weak pairs of the first
        # draft (O/0, S/5, S/8, S/9, F/P, O/Q) are well apart now
        R = {ch: set(hexfont.render_big(self.lat, ch)) for ch in ALNUM}
        worst = min((len(R[a] ^ R[b]), a, b) for k, a in enumerate(ALNUM) for b in ALNUM[k + 1:])
        self.assertGreaterEqual(worst[0], 5, worst)
        self.assertGreaterEqual(len(R["O"] ^ R["0"]), 3)
        self.assertGreaterEqual(len(R["S"] ^ R["5"]), 6)

    def test_x_legs_and_k_arm_are_full_weight(self):
        for ch in "XK":
            cov = hexfont.render_big(self.lat, ch)
            for r in (0, 1, 4, 5):                  # two bulbs a stroke on the outer rows
                n = sum(1 for i, _ in self.lat.rows[r] if i in cov)
                self.assertGreaterEqual(n, 4, (ch, r))

    def test_four_has_a_full_height_right_stem(self):
        cov = hexfont.render_big(self.lat, "4")
        top = [x for i, x in self.lat.rows[5] if i in cov]
        bottom = [x for i, x in self.lat.rows[0] if i in cov]
        self.assertTrue(top and bottom)
        self.assertGreater(max(top), min(top) + 2)            # the open top: both stems reach it


class PeakText(unittest.TestCase):
    def scene(self, **params):
        return {"name": "t", "layers": [{"effect": "peak_text", "target": "Whole front",
                                         "params": {"text": "GO", "colors": ["#ff4000"], **params}}]}

    def test_letter_mode_shows_one_letter_at_a_time(self):
        L = house()
        lat = hexfont.lattice(L, L.indices("Peak"))
        frames = render(self.scene(mode="single", hold=1.0, pause=1.0), L, frames=60, fps=20)
        peak = [i for i, _, _ in lat.cells]
        g = hexfont.render_big(lat, "G")
        lit = {i for i in peak if max(frames[10][i]) > 0.5}
        self.assertEqual(lit, set(g))
        o = hexfont.render_big(lat, "O")
        lit = {i for i in peak if max(frames[35][i]) > 0.5}       # 1.75 s: the O
        self.assertEqual(lit, set(o))
        # the beat between letters is dark
        self.assertFalse(any(max(frames[22][i]) > 0.05 for i in peak))
        # the eaves keep time: brighter as a letter comes than a moment later
        rest = [i for i in L.indices("Whole front") if i not in set(peak)]
        self.assertGreater(sum(max(frames[1][i]) for i in rest), sum(max(frames[15][i]) for i in rest))

    def test_marquee_moves_and_stays_on_the_peak(self):
        L = house()
        frames = render(self.scene(mode="marquee", text="TOUCHDOWN", speed=4), L, frames=100, fps=20)
        lat = hexfont.lattice(L, L.indices("Peak"))
        peak = [i for i, _, _ in lat.cells]
        lit = [frozenset(i for i in peak if max(frames[k][i]) > 0.5) for k in (40, 60, 80)]
        self.assertTrue(all(lit))
        self.assertEqual(len(set(lit)), 3)

    def test_no_zigzag_is_all_trim(self):
        L = house()
        frames = render({"name": "t", "layers": [{"effect": "peak_text", "target": "Porch",
                                                  "params": {"text": "HI", "trim": "glow"}}]}, L, frames=3)
        self.assertTrue(any(max(c) > 0 for c in frames[-1]))


if __name__ == "__main__":
    unittest.main()
