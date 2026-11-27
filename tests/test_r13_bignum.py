"""Round 13: countdown and days-to-go numbers readable on the Peak.

Owner: "the new years countdown numbers aren't readable on the roof peak". The numbers
were a 3x5 bitmap squeezed into the zigzag's triangle by proportion: three-bulb strokes,
one-row holes (3, 8, 9 and 0 alike) and two-digit numbers cut off by the narrow top
rows. They are now drawn bulb for bulb on all six rows (goveelights/bignum.py)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from goveelights import addrun, bignum  # noqa: E402
from goveelights.effects import EFFECTS  # noqa: E402
from goveelights.effects_ext import winter_night as wn  # noqa: E402
from goveelights.fx import Ctx  # noqa: E402
from goveelights.scene import Scene  # noqa: E402

from preview import default_layout_file, house_layout  # noqa: E402


def peak_rows(L):
    rows, _ = bignum.zigzag(L, L.indices("Peak"))
    return rows


class BigNumberTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.L = house_layout(default_layout_file())
        cls.T = addrun.layout_with_tip(house_layout(default_layout_file()), visible=12)
        cls.idx = cls.L.indices("Peak")

    def pattern(self, L, text):
        """The lit bulbs as (row, local index) pairs, so layouts can be compared."""
        rows = peak_rows(L)
        on = bignum.cells(L, L.indices("Peak"), text)
        return frozenset((k, n) for k, r in enumerate(rows) for n, i in enumerate(r) if i in on)

    def test_every_number_is_distinct_and_uses_all_six_rows(self):
        self.assertEqual(len(peak_rows(self.L)), 6)
        seen = {}
        for v in range(100):
            pat = self.pattern(self.L, str(v))
            self.assertTrue(pat, v)
            self.assertNotIn(pat, seen, f"{v} looks like {seen.get(pat)}")
            seen[pat] = v
            self.assertEqual({k for k, _ in pat}, set(range(6)), v)    # top to bottom

    def test_single_digits_keep_their_holes(self):
        # 0 has a hole two bulbs wide on the four middle rows; 8 and 9 do not look like it
        rows = peak_rows(self.L)
        on0 = bignum.cells(self.L, self.idx, "0")
        for k in (1, 2, 3, 4):
            lit = [n for n, i in enumerate(rows[k]) if i in on0]
            self.assertEqual(len(lit), 4, k)                            # two strokes, two bulbs each
            self.assertGreaterEqual(lit[2] - lit[1] - 1, 2, k)
        self.assertNotEqual(self.pattern(self.L, "8"), self.pattern(self.L, "0"))
        self.assertNotEqual(self.pattern(self.L, "9"), self.pattern(self.L, "3"))

    def test_two_digits_fit_and_stay_apart(self):
        rows = peak_rows(self.L)
        px = self.L.pixels
        cx = sum(px[i].x for i in rows[-1]) / len(rows[-1])
        for v in (10, 24, 59, 88):
            on = bignum.cells(self.L, self.idx, str(v))
            for k, r in enumerate(rows):
                xs = [px[i].x - cx for i in r if i in on]
                left, right = [x for x in xs if x < 0], [x for x in xs if x > 0]
                if left and right:      # a clear gap of at least one bulb between the digits
                    self.assertGreater(min(right) - max(left), 1.9 * 0.7, (v, k))
            self.assertTrue(any(i in on for i in rows[-1]), v)          # the top row is used

    def test_the_tip_does_not_move_the_number_and_marks_the_apex(self):
        for v in ("7", "10", "24"):
            self.assertEqual(self.pattern(self.L, v), self.pattern(self.T, v), v)
        self.assertEqual(bignum.crown(self.L, self.idx), [])
        crown = bignum.crown(self.T, self.T.indices("Peak"))
        self.assertEqual(len(crown), 1)
        px = self.T.pixels
        self.assertEqual(px[crown[0]].y, max(px[i].y for i in self.T.indices("Peak")))

    def render(self, L, effect, params, t):
        sc = Scene.from_dict({"layers": [{"effect": effect, "params": params, "target": "Peak"}]})
        return sc.render(t, L)

    def test_countdown_digits_on_the_peak(self):
        P = {"target": 0, "seconds": 10, "part": "digits"}
        for t, n in ((0.5, 10), (1.5, 9), (9.5, 1)):
            cols = self.render(self.L, "countdown", P, t)
            on = bignum.cells(self.L, self.idx, str(n))
            lit = {i for i in self.idx if max(cols[i]) > 0.5}
            self.assertEqual(lit, set(on), t)
        cols = self.render(self.L, "countdown", P, 10.2)                 # zero: the flash
        self.assertTrue(all(max(cols[i]) > 0.5 for i in self.idx))
        # with the Tip its apex ticks each second
        crown = bignum.crown(self.T, self.T.indices("Peak"))[0]
        a = self.render(self.T, "countdown", P, 3.02)
        b = self.render(self.T, "countdown", P, 3.9)
        self.assertGreater(max(a[crown]), 0.5)
        self.assertLess(max(b[crown]), max(a[crown]))

    def test_digits_effect_params_unchanged(self):
        self.assertEqual([p.name for p in EFFECTS["digits"].params], ["value", "colors", "pulse"])
        self.assertEqual([p.name for p in EFFECTS["countdown"].params], ["target", "colors", "seconds", "part"])
        cols = self.render(self.L, "digits", {"value": 24, "pulse": 0}, 1.0)
        on = bignum.cells(self.L, self.idx, "24")
        self.assertEqual({i for i in self.idx if max(cols[i]) > 0.5}, set(on))

    def test_digits_differ_by_whole_strokes(self):
        # each single digit differs from every other by at least three bulbs (1/7, 3/8/9/0,
        # 5/6, 2), and in a pair by at least two, in either place
        import itertools
        for L in (self.L, self.T):
            idx = L.indices("Peak")
            for texts, need in (([str(d) for d in range(10)], 3),
                                (["1" + str(d) for d in range(10)], 2),
                                ([str(d) + "0" for d in range(1, 10)], 2)):
                on = {t: set(bignum.cells(L, idx, t)) for t in texts}
                for a, b in itertools.combinations(texts, 2):
                    self.assertGreaterEqual(len(on[a] ^ on[b]), need, (a, b))

    def test_pair_seven_is_a_clean_diagonal(self):
        # the pair 7 used to curl back into a vertical stem and read as "?": its stroke under
        # the bar only ever steps left
        cols = [r.rindex("#") for r in bignum.PAIR["7"][1:]]
        self.assertEqual(cols, sorted(cols, reverse=True))
        self.assertGreater(cols[0], cols[-1] + 2)

    def test_every_number_fits_on_the_peak_and_never_the_tip(self):
        for L in (self.L, self.T):
            idx = L.indices("Peak")
            rows, tip = bignum.zigzag(L, idx)
            own = {i for r in rows for i in r}
            self.assertEqual(len(rows), 6)
            for v in range(100):
                on = set(bignum.cells(L, idx, str(v)))
                self.assertTrue(on <= own, v)
                if v >= 10:     # both digits whole: each half of the gable has lit bulbs on 5+ rows
                    px = L.pixels
                    cx = sum(px[i].x for i in rows[-1]) / len(rows[-1])
                    for side in (-1, 1):
                        lit_rows = {k for k, r in enumerate(rows) for i in r
                                    if i in on and (px[i].x - cx) * side > 0}
                        self.assertGreaterEqual(len(lit_rows), 5, (v, side))
            self.assertEqual(len(tip), 3 if L is self.T else 0)

    def test_countdown_ten_to_zero_with_and_without_the_tip(self):
        P = {"target": 0, "seconds": 10, "part": "digits"}
        for L in (self.L, self.T):
            idx = L.indices("Peak")
            own = {i for r in bignum.zigzag(L, idx)[0] for i in r}
            seen = []
            for n in range(10, 0, -1):
                cols = self.render(L, "countdown", P, 10 - n + 0.5)
                on = set(bignum.cells(L, idx, str(n)))
                lit = {i for i in own if max(cols[i]) > 0.5}
                self.assertEqual(lit, on, (n, L is self.T))
                seen.append(frozenset(on))
            self.assertEqual(len(set(seen)), 10)
            cols = self.render(L, "countdown", P, 10.2)                  # zero: the flash
            self.assertTrue(all(max(cols[i]) > 0.5 for i in own))

    def ball_drop_frames(self, L):
        """(state, frame, seconds shown) through the ball drop's countdown on the Peak."""
        idx = L.indices("Peak")
        P = EFFECTS["ball_drop"].resolve({"mode": "loop", "seconds": 10, "pause": 1,
                                         "sky_color": "#1030ff"})
        st = {}
        for k in range(int(11.0 / 0.05)):
            t = k * 0.05
            cols = EFFECTS["ball_drop"].fn(t, Ctx(L, idx, t, 0.05 if k else 0.0), P, st)
            if st["phase"] == "drop" and st["remaining"] > 0.6:
                yield P, st, cols, max(1, -int(-st["remaining"] // 1))

    def test_ball_drop_digits_sit_on_a_dark_margin(self):
        for L in (self.L, self.T):
            px = L.pixels
            idx = L.indices("Peak")
            own = {i for r in bignum.zigzag(L, idx)[0] for i in r}
            checked = 0
            for P, st, cols, n in self.ball_drop_frames(L):
                on = set(bignum.cells(L, idx, str(n)))
                bx, by = st["ball"]
                far = {i for i in own if (px[i].x - bx) ** 2 + (px[i].y - by) ** 2 > 2.8 ** 2}
                margin = (wn._bd_digit_clear(st, L, n) - on) & far
                sky = far - wn._bd_digit_clear(st, L, n)
                if not margin or not sky:
                    continue
                m = sum(sum(cols[i]) for i in margin) / len(margin)
                s = sum(sum(cols[i]) for i in sky) / len(sky)
                self.assertLess(m, 0.25 * s, (n, L is self.T))
                checked += 1
            self.assertGreater(checked, 50)

    def test_the_ball_never_dims_a_digit(self):
        for L in (self.L, self.T):
            px = L.pixels
            idx = L.indices("Peak")
            checked = 0
            for P, st, cols, n in self.ball_drop_frames(L):
                bx, by = st["ball"]
                floor = 0.6 * max(P["digit_color"]) - 1e-6
                for i in bignum.cells(L, idx, str(n)):
                    d = ((px[i].x - bx) ** 2 + (px[i].y - by) ** 2) ** 0.5
                    if 1.2 < d < 2.6:                   # in the ball's dark ring, not its core
                        self.assertGreaterEqual(max(cols[i]), floor, (n, i, round(d, 2)))
                        checked += 1
            self.assertGreater(checked, 20)

    def test_the_ball_core_stays_off_the_margin(self):
        """The ball crossing the number may light its own digit bulbs, but only dimly the
        dark margin around them, so no stray blob reads as part of a stroke."""
        for L in (self.L, self.T):
            px = L.pixels
            idx = L.indices("Peak")
            own = {i for r in bignum.zigzag(L, idx)[0] for i in r}
            seen = 0
            for P, st, cols, n in self.ball_drop_frames(L):
                on = set(bignum.cells(L, idx, str(n)))
                margin = (wn._bd_digit_clear(st, L, n) - on) & own
                stray = [i for i in margin if max(cols[i]) > 0.5]
                self.assertEqual(stray, [], (n, L is self.T))
                bx, by = st["ball"]
                core = [i for i in own if (px[i].x - bx) ** 2 + (px[i].y - by) ** 2 < 0.6]
                if core:                                    # the ball is still seen
                    self.assertGreater(max(max(cols[i]) for i in core), 0.3, n)
                    seen += 1
            self.assertGreater(seen, 20)

    def test_digit_bulbs_are_worked_out_once_per_number(self):
        """A 60 s drop: each number's bulbs, margin and keep set are computed once and
        reused (never rebuilt per frame, never wiped partway through the drop)."""
        calls = []
        real = bignum.cells

        def counting(layout, idx, text, *a, **k):
            calls.append(text)
            return real(layout, idx, text, *a, **k)
        L = self.L
        idx = L.indices("Peak")
        P = EFFECTS["ball_drop"].resolve({"mode": "loop", "seconds": 60, "pause": 1})
        st = {}
        bignum.cells = counting
        try:
            keeps = {}
            for k in range(int(61.0 / 0.1)):
                t = k * 0.1
                EFFECTS["ball_drop"].fn(t, Ctx(L, idx, t, 0.1 if k else 0.0), P, st)
                if st["phase"] == "drop" and st["remaining"] > 0.6:
                    n = max(1, -int(-st["remaining"] // 1))
                    keep = wn._bd_digit_keep(st, L, n)
                    self.assertIsInstance(keep, frozenset)
                    self.assertIs(keeps.setdefault(n, keep), keep)
        finally:
            bignum.cells = real
        self.assertGreater(len(set(calls)), 50)
        self.assertEqual(len(calls), len(set(calls)), "a number's bulbs were recomputed")


if __name__ == "__main__":
    unittest.main()
