import datetime as dt
import json
import time
import unittest
from unittest import mock

from goveelights.effects import EFFECTS
from goveelights.effects_ext import night_sky as ns
from goveelights.fx import Ctx
from goveelights.scene import Scene

from test_rack import house, valid

NAMES = ("starry_night", "gift_wrap", "advent")
PRESETS = ("starry-night", "gift-wrap", "advent-calendar", "starry-night-sleigh")


def step(name, L, idx, P, st, t, d):
    return EFFECTS[name].fn(t, Ctx(L, idx, t, d), P, st)


def run(name, L, target="all", params=None, frames=40, dt_=0.05, t0=0.0, state=None):
    P = EFFECTS[name].resolve(params or {})
    idx = L.indices(target) if isinstance(target, str) else target
    st = {} if state is None else state
    out = None
    for k in range(frames):
        out = step(name, L, idx, P, st, t0 + k * dt_, 0.0 if k == 0 else dt_)
    return out, st


def lum(c):
    return max(c)


class RegistrationTests(unittest.TestCase):
    def test_registered(self):
        labels = {"starry_night": "Starry night", "gift_wrap": "Gift wrap", "advent": "Advent calendar"}
        for n in NAMES:
            e = EFFECTS[n]
            self.assertTrue(e.frame)
            self.assertEqual(e.group, "Scenes")
            self.assertEqual(e.label, labels[n])
            self.assertTrue(e.description)
            for p in e.params:
                self.assertTrue(p.label, (n, p.name))
                self.assertTrue(p.help or p.kind in ("color", "number", "int", "colors", "choice"), (n, p.name))

    def test_presets_load_and_render(self):
        L = house()
        for pid in PRESETS:
            with open(f"presets/{pid}.json", encoding="utf-8") as f:
                sc = Scene.from_dict(json.load(f))
            for k in range(30):
                valid(self, sc.render(k * 0.1, L), L.n)


class RobustnessTests(unittest.TestCase):
    def setUp(self):
        self.L = house()

    def test_targets_and_outside_dark(self):
        L = self.L
        targets = ["all", "Eaves", "Peak", "Accent", "Porch", "Whole front", "Whole front gable",
                   "Front gable outline", "Flat eaves", "Zigzag top half", "Garage peak",
                   [5], [], [204, 205], [9999, -3]]
        for n in NAMES:
            for tg in targets:
                idx = L.indices(tg) if isinstance(tg, str) else tg
                cols, _ = run(n, L, idx, frames=30, dt_=0.3)
                valid(self, cols, L.n)
                s = set(idx)
                self.assertTrue(all(max(c) == 0 for i, c in enumerate(cols) if i not in s), (n, tg))

    def test_param_extremes_and_choices(self):
        L = self.L
        for n in NAMES:
            eff = EFFECTS[n]
            for pick in ("min", "max"):
                params = {}
                for p in eff.params:
                    if p.kind in ("number", "int"):
                        params[p.name] = getattr(p, pick)
                    elif p.kind == "choice":
                        params[p.name] = p.options[0 if pick == "min" else -1]
                    elif p.kind == "colors":
                        params[p.name] = [] if pick == "min" else ["#000000"]
                cols, _ = run(n, L, "all", params, frames=80, dt_=0.1)
                valid(self, cols, L.n)
            for p in eff.params:
                if p.kind == "choice":
                    for o in p.options:
                        cols, _ = run(n, L, L.indices(["Whole front gable", "Porch"]), {p.name: o}, frames=40, dt_=0.2)
                        valid(self, cols, L.n)

    def test_dt_zero_huge_negative_and_clock_jumps(self):
        L = self.L
        idx = L.indices("all")
        for n in NAMES:
            P = EFFECTS[n].resolve({"unwrap_every": 5} if n == "gift_wrap" else {})
            st = {}
            for t, d in [(0, 0), (0, 0), (1, 5.0), (1.1, -1.0), (1.2, float("nan")), (500, 0.05),
                         (3, 0.05), (3.05, 0.05), (1e6, 0.05), (1e6 + 0.05, 0.05), (1e6 + 400, 0.05)]:
                valid(self, step(n, L, idx, P, st, t, d), L.n)

    def test_deterministic(self):
        L = self.L
        for n in NAMES:
            a, _ = run(n, L, "all", frames=300, dt_=0.05)
            b, _ = run(n, L, "all", frames=300, dt_=0.05)
            self.assertEqual(a, b, n)

    def test_bounded_state(self):
        L = self.L
        idx = L.indices("all")
        P = EFFECTS["starry_night"].resolve({"shooting_every": 1, "doubles": 1, "tail": 30, "shooting_speed": 10})
        st = {}
        for k in range(3000):
            step("starry_night", L, idx, P, st, k * 0.1, 0.1 if k else 0.0)
            self.assertLessEqual(len(st["meteors"]), ns.SN_MAX_METEORS)
        P = EFFECTS["gift_wrap"].resolve({"unwrap_every": 5, "tie": 1})
        st = {}
        for k in range(3000):
            step("gift_wrap", L, idx, P, st, k * 0.1, 0.1 if k else 0.0)
        self.assertLess(len(st), 10)

    def test_speed_under_budget(self):
        L = self.L
        for n, tg, params in (("starry_night", "all", {"shooting_every": 2, "doubles": 1}),
                              ("gift_wrap", "all", {"unwrap_every": 10, "tie": 3}),
                              ("advent", "all", {"day": 24})):
            idx = L.indices(tg)
            P = EFFECTS[n].resolve(params)
            st, ms = {}, []
            for k in range(600):
                t0 = time.perf_counter()
                step(n, L, idx, P, st, k * 0.05, 0.05 if k else 0.0)
                ms.append((time.perf_counter() - t0) * 1000)
            self.assertLess(sum(ms) / len(ms), 8.0, n)


class StarryNightTests(unittest.TestCase):
    def setUp(self):
        self.L = house()

    def test_blue_sky_and_sparse_stars(self):
        L = self.L
        idx = L.indices("all")
        cols, st = run("starry_night", L, idx, {"shooting_every": 0}, frames=20)
        stars = st["stars"]
        self.assertTrue(0.05 * len(idx) < len(stars) < 0.25 * len(idx))
        self.assertFalse(any(i + 1 in stars for i in stars))          # never two neighbours
        sky = [cols[i] for i in idx if i not in stars]
        self.assertTrue(all(c[2] > c[0] and c[2] > c[1] and c[2] > 0.15 for c in sky))   # blue, not off
        self.assertTrue(any(min(cols[i]) > 0.3 for i in stars))      # stars read white-ish

    def test_stars_twinkle_at_different_rates(self):
        L = self.L
        idx = L.indices("all")
        P = EFFECTS["starry_night"].resolve({"shooting_every": 0})
        st = {}
        series = {}
        for k in range(200):
            cols = step("starry_night", L, idx, P, st, k * 0.05, 0.05 if k else 0.0)
            for i in st["stars"]:
                series.setdefault(i, []).append(lum(cols[i]))
        moving = [i for i, s in series.items() if max(s) - min(s) > 0.1]
        self.assertGreater(len(moving), len(series) // 3)
        self.assertGreater(len({round(r[0], 3) for r in st["stars"].values()}), 5)

    def test_shooting_star_streaks_along_the_top(self):
        L = self.L
        idx = L.indices("all")
        P = EFFECTS["starry_night"].resolve({"shooting_every": 4, "doubles": 0, "twinkle": 0, "stars": 0})
        st = {}
        base = step("starry_night", L, idx, P, st, 0.0, 0.0)
        heads, eaves = [], set(L.indices("Eaves"))
        for k in range(1, 400):
            t = k * 0.05
            cols = step("starry_night", L, idx, P, st, t, 0.05)
            hot = [i for i in idx if lum(cols[i]) > lum(base[i]) + 0.4]
            if hot:
                self.assertTrue(set(hot) <= eaves, hot)                # on the roofline, not the zigzag
                heads.append((t, max(hot, key=lambda i: lum(cols[i]))))
        self.assertTrue(heads)
        # several shooting stars in 20 s, each moving along a streak
        self.assertGreaterEqual(len({round(t) for t, _ in heads}), 3)
        # the first starts near a gable apex (the highest bulbs)
        first = heads[0][1]
        self.assertGreater(L.pixels[first].y, 2.0)

    def test_none_when_disabled(self):
        L = self.L
        _, st = run("starry_night", L, "all", {"shooting_every": 0}, frames=400, dt_=0.1)
        self.assertEqual(st["meteors"], [])

    def test_peak_only_falls_back_to_top_row(self):
        L = self.L
        idx = L.indices("Peak")
        _, st = run("starry_night", L, idx, {"shooting_every": 1}, frames=5)
        self.assertTrue(st["tracks"])
        self.assertTrue(set(st["tracks"][0][0].order) <= set(idx))


class GiftWrapTests(unittest.TestCase):
    def setUp(self):
        self.L = house()
        self.idx = self.L.indices("Whole front gable") + self.L.indices("Porch")

    def test_wrapped_ribbon_bow_and_paper(self):
        L, idx = self.L, self.idx
        cols, _ = run("gift_wrap", L, idx, {"tie": 0, "shimmer": 0, "sheen": 0}, frames=5)
        g = ns._gw_geometry(L, idx)
        self.assertEqual(sorted(g["crown"]), [42, 43])                # the bow sits on the apex
        rib = [i for i in idx if g["hcov"].get(i, 0) > 0.9 and i not in g["bow"]]
        self.assertTrue(rib)
        for i in rib:                                                  # gold ribbon: red + green
            self.assertGreater(cols[i][1], 0.3)
        paper = [i for i in idx if i not in g["hcov"] and i not in g["vcov"] and i not in g["bow"]]
        self.assertTrue(all(cols[i][0] > 0.5 and cols[i][2] < 0.2 for i in paper))   # red paper
        for i in g["crown"]:
            self.assertGreater(min(cols[i][:2]), 0.5)                  # bright bow
        # the vertical ribbon reaches the porch
        porch = set(L.indices("Porch"))
        self.assertTrue(any(i in porch for i in g["vcov"]))

    def test_tie_intro_draws_in_then_bow_pops(self):
        L, idx = self.L, self.idx
        P = EFFECTS["gift_wrap"].resolve({"tie": 8, "shimmer": 0, "sheen": 0})
        g = ns._gw_geometry(L, idx)
        st, snaps = {}, {}
        for k in range(int(10 / 0.05)):
            t = k * 0.05
            cols = step("gift_wrap", L, idx, P, st, t, 0.05 if k else 0.0)
            snaps[round(t, 2)] = cols
        knot = g["crown"][0]
        self.assertLess(snaps[3.0][knot][1], 0.15)                     # no (gold) bow yet
        self.assertGreater(lum(snaps[6.2][knot]), 0.8)                 # popped
        # ribbon: partly drawn at 3 s (edges first), complete by 6 s
        band = [i for i in g["hcov"] if g["hcov"][i] > 0.9 and i not in g["bow"]]
        golden = lambda c: c[1] > 0.3                                  # noqa: E731
        part = [i for i in band if golden(snaps[3.0][i])]
        self.assertTrue(0 < len(part) < len(band))
        xs = [L.pixels[i].x for i in part]
        mid = g["ax"]
        self.assertTrue(all(abs(x - mid) > 2 for x in xs))             # from the edges in
        self.assertTrue(all(golden(snaps[6.5][i]) for i in band))

    def test_unwrap_bursts_and_rewraps(self):
        L, idx = self.L, self.idx
        P = EFFECTS["gift_wrap"].resolve({"tie": 2, "unwrap_every": 10, "shimmer": 0, "sheen": 0})
        st, phases = {}, []
        for k in range(int(40 / 0.05)):
            step("gift_wrap", L, idx, P, st, k * 0.05, 0.05 if k else 0.0)
            if not phases or phases[-1] != st["phase"]:
                phases.append(st["phase"])
        self.assertEqual(phases[:5], ["tie", "wrapped", "unwrap", "tie", "wrapped"])

    def test_palettes_differ(self):
        L, idx = self.L, self.idx
        seen = set()
        for pal in ns.GW_PALETTES:
            cols, _ = run("gift_wrap", L, idx, {"tie": 0, "palette": pal}, frames=3)
            seen.add(tuple(round(v, 2) for v in cols[L.indices("Peak")[0]]))
        self.assertEqual(len(seen), len(ns.GW_PALETTES))


class AdventTests(unittest.TestCase):
    def setUp(self):
        self.L = house()
        self.idx = self.L.indices("Whole front gable")

    def test_24_doors_on_the_zigzag_with_gaps(self):
        g = ns._ad_doors(self.L, self.idx)
        self.assertEqual(len(g["doors"]), 24)
        peak = set(self.L.indices("Peak"))
        allb = [i for d in g["doors"] for i in d]
        self.assertEqual(len(allb), len(set(allb)))
        self.assertTrue(set(allb) <= peak)
        self.assertTrue(all(2 <= len(d) <= 4 for d in g["doors"]))
        self.assertTrue(g["gaps"])
        self.assertEqual(sorted(g["crown"]), [42, 43])

    def test_eaves_fallback(self):
        idx = self.L.indices("Eaves")
        g = ns._ad_doors(self.L, idx)
        self.assertEqual(len(g["doors"]), 24)

    def test_day_from_date(self):
        f = ns.advent_day
        self.assertEqual(f({"day": 0}, dt.date(2026, 12, 7)), 7)
        self.assertEqual(f({"day": 0}, dt.date(2026, 12, 1)), 1)
        self.assertEqual(f({"day": 0}, dt.date(2026, 12, 24)), 24)
        self.assertEqual(f({"day": 0}, dt.date(2026, 12, 28)), 24)
        self.assertEqual(f({"day": 0}, dt.date(2026, 11, 26)), 24)
        self.assertEqual(f({"day": 5}, dt.date(2026, 11, 26)), 5)

    def test_open_today_and_closed_doors(self):
        L, idx = self.L, self.idx
        with mock.patch.object(ns, "_today", lambda: dt.date(2026, 12, 10)):
            cols, _ = run("advent", L, idx, {"intro": 0, "sparkle": 0, "order": "bottom up"}, frames=3)
        doors = ns._ad_doors(L, idx)["doors"]
        for k, d in enumerate(doors):
            v = max(lum(cols[i]) for i in d)
            if k < 9:
                self.assertGreater(v, 0.5, k)                          # open: gold
                self.assertGreater(cols[d[0]][0], cols[d[0]][2] * 3)
            elif k == 9:
                self.assertGreater(v, 0.6)                             # today
            else:
                self.assertLess(v, 0.25, k)                            # closed placeholder
                self.assertGreater(v, 0.05, k)                         # ...but not off

    def test_today_pulses_and_intro_opens_in_order(self):
        L, idx = self.L, self.idx
        P = EFFECTS["advent"].resolve({"day": 12, "intro": 6, "sparkle": 0})
        doors = ns._ad_doors(L, idx)["doors"]
        st, today, first_lit = {}, [], {}
        for k in range(int(12 / 0.05)):
            t = k * 0.05
            cols = step("advent", L, idx, P, st, t, 0.05 if k else 0.0)
            for j in range(12):
                if j not in first_lit and max(lum(cols[i]) for i in doors[j]) > 0.5:
                    first_lit[j] = t
            if t > 8:
                today.append(lum(cols[doors[11][0]]))
        self.assertEqual(sorted(first_lit, key=first_lit.get), list(range(12)))
        self.assertGreater(max(today) - min(today), 0.15)

    def test_scattered_order_is_a_permutation(self):
        o = ns._ad_order(24, "scattered", 3)
        self.assertEqual(sorted(o), list(range(24)))
        self.assertNotEqual(o, list(range(24)))
        self.assertEqual(o, ns._ad_order(24, "scattered", 3))


if __name__ == "__main__":
    unittest.main()
