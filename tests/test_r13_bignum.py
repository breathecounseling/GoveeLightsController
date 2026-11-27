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


if __name__ == "__main__":
    unittest.main()
