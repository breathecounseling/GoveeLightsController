"""Round 10: showpieces for a three-run house (a fourth run joins in when it is there).
The fan (turkey, harvest sunset, sunburst) on the zigzag, the falling star across all
runs, the sleigh's grand tour via the lower roofline, candlelight, their presets, the
Thanksgiving / Christmas / Broncos touchdown shows and their holiday-calendar showtimes."""
from __future__ import annotations

import hashlib
import json
import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from goveelights import calendar_builtin as cal  # noqa: E402
from goveelights import schedule as sched  # noqa: E402
from goveelights import shows_builtin  # noqa: E402
from goveelights.app_server import validate_showtimes  # noqa: E402
from goveelights.effects import EFFECTS  # noqa: E402
from goveelights.effects_ext import showpieces  # noqa: E402
from goveelights.layout import Layout, Run  # noqa: E402
from goveelights.scene import Scene  # noqa: E402

NEW_PRESETS = ["turkey-fan", "harvest-sunset", "gratitude-candlelight", "text-give-thanks", "sleigh-grand-tour",
               "falling-star", "candy-cane-chase", "text-touchdown", "orange-crush", "hail-mary"]
NEW_SHOWS = ["Thanksgiving tonight", "Christmas on the roofline", "Broncos touchdown"]
HOUSE = ROOT / "layouts" / "house.json"
EXAMPLE = ROOT / "layouts" / "examples" / "gable-house.json"


def layout(path: Path, extra: int = 0) -> Layout:
    data = json.loads(path.read_text(encoding="utf-8"))
    devs = data["devices"]
    runs = sorted([Run(mac=m, name=d["name"], count=d["pixel_count"], reverse=d.get("reversed", False),
                       hidden=set(d.get("hidden", [])), geometry=d.get("geometry", [])) for m, d in devs.items()],
                  key=lambda r: devs[r.mac].get("order", 0))
    if extra:                    # a fourth box that has just been counted: no mapped shape yet
        runs.append(Run(mac="AA:BB:CC:DD:EE:FF:00:11", name="Run 4", count=extra))
    return Layout(runs, data.get("zones"))


def layouts():
    out = [("example", layout(EXAMPLE)), ("example + 4th run", layout(EXAMPLE, 100))]
    if HOUSE.exists():
        out += [("house", layout(HOUSE)), ("house + 4th run", layout(HOUSE, 100))]
    return out


def render(scene_dict: dict, L: Layout, frames: int = 160, fps: float = 20.0):
    sc = Scene.from_dict(scene_dict)
    return [sc.render(k / fps, L) for k in range(frames)]


def digest(frames) -> str:
    h = hashlib.sha256()
    for cols in frames:
        h.update(bytes(min(255, max(0, int(c * 255 + .5))) for px in cols for c in px))
    return h.hexdigest()


def preset(pid: str) -> dict:
    return json.loads((ROOT / "presets" / f"{pid}.json").read_text(encoding="utf-8"))


class NewPresetsRender(unittest.TestCase):
    def test_every_new_preset_renders_deterministically_everywhere(self):
        for name, L in layouts():
            for pid in NEW_PRESETS:
                with self.subTest(layout=name, preset=pid):
                    a = render(preset(pid), L)
                    b = render(preset(pid), L)
                    self.assertEqual(digest(a), digest(b))
                    for cols in a:
                        self.assertEqual(len(cols), L.n)
                        self.assertTrue(all(0.0 <= c <= 1.0 for px in cols for c in px))
                    lit = sum(1 for px in a[-1] if max(px) > 0.02)
                    self.assertGreater(lit, L.n // 3, "most of the house should be lit")

    def test_the_fourth_run_is_used_by_whole_house_looks(self):
        L = layout(EXAMPLE, 100)
        four = [p.i for p in L.pixels if p.run == "Run 4"]
        for pid in ("turkey-fan", "harvest-sunset", "gratitude-candlelight", "sleigh-grand-tour", "falling-star",
                    "candy-cane-chase", "orange-crush", "hail-mary"):
            with self.subTest(preset=pid):
                cols = render(preset(pid), L, 40)[-1]
                self.assertGreater(sum(1 for i in four if max(cols[i]) > 0.02), 50)

    def test_effects_survive_odd_targets(self):
        L1 = Layout([Run(mac="A", name="Only", count=12)])            # a short straight strip
        L0 = layout(EXAMPLE)
        for eff in ("fan", "falling_star", "sleigh_tour", "candlelight"):
            for style in ("turkey", "sunset", "sunburst"):
                if eff != "fan" and style != "turkey":
                    continue
                for L, tgt in ((L1, "all"), (L0, "Peak"), (L0, "Eaves"), (L0, "Porch"), (L0, ["North wall"])):
                    with self.subTest(effect=eff, style=style, target=tgt):
                        d = {"layers": [{"effect": eff, "target": tgt,
                                         "params": {"style": style} if eff == "fan" else {}}]}
                        fr = render(d, L, 60)
                        self.assertTrue(all(0.0 <= c <= 1.0 for px in fr[-1] for c in px))

    def test_clock_jumps_and_stalls(self):
        L = layout(EXAMPLE)
        for pid in ("sleigh-grand-tour", "falling-star", "turkey-fan", "harvest-sunset"):
            sc = Scene.from_dict(preset(pid))
            for t in (0, 0.05, 0.1, 5.0, 4.0, 4.05, 300.0, 300.0, 300.05):
                cols = sc.render(t, L)
                self.assertTrue(all(0.0 <= c <= 1.0 for px in cols for c in px), pid)


class Pictures(unittest.TestCase):
    def setUp(self):
        self.L = layout(EXAMPLE)
        self.peak = set(self.L.indices("Peak"))

    def test_canvas_finds_the_zigzag_and_its_trapezoid(self):
        cv = showpieces.canvas(self.L, self.L.indices("Whole front gable"))
        self.assertEqual(set(cv["zig"]), self.peak)
        self.assertTrue(cv["rest"])
        us = [u for u, _ in cv["uv"].values()]
        vs = [v for _, v in cv["uv"].values()]
        self.assertAlmostEqual(min(us), -1.0, places=6)
        self.assertAlmostEqual(max(vs), 1.0, places=6)
        self.assertGreater(cv["k"], 1.2)                         # the top row is still wide
        for i, (u, v) in cv["uv"].items():
            a, r = showpieces._polar(u, v, cv["k"])
            self.assertTrue(0 <= a <= 1 and r <= 1.08, (i, a, r))

    def test_turkey_has_a_head_a_wattle_and_symmetric_feathers(self):
        d = {"layers": [{"effect": "fan", "target": "Peak", "params": {"style": "turkey", "glints": 0, "speed": 0}}]}
        cols = render(d, self.L, 140)[-1]
        cv = showpieces.canvas(self.L, self.L.indices("Peak"))
        red = [i for i, (u, v) in cv["uv"].items() if cols[i][0] > 0.7 and cols[i][1] < 0.1 and 0.3 < v < 0.7]
        self.assertTrue(red, "the wattle")
        pale = [i for i in self.peak if min(cols[i]) > 0.2 and cols[i][1] > 0.5]
        self.assertTrue(pale, "the head and the feather tips")
        head = [i for i, (u, v) in cv["uv"].items() if abs(u) < 0.2 and v > 0.5 and min(cols[i]) > 0.2]
        self.assertGreaterEqual(len(head), 4, "a head big enough to read on six rows")
        body = [max(cols[i]) for i, (u, v) in cv["uv"].items() if abs(u) < 0.15 and v < 0.2]
        fan = [max(cols[i]) for i, (u, v) in cv["uv"].items() if abs(u) > 0.5 and v < 0.2]
        self.assertLess(sum(body) / len(body), 0.6 * sum(fan) / len(fan), "a dark body against bright feathers")
        # mirrored: a feather's colour on the left matches the right (same row, same distance out)
        rows = {}
        for i, (u, v) in cv["uv"].items():
            rows.setdefault(round(v, 2), []).append((u, i))
        same = total = 0
        for row in rows.values():
            row.sort()
            for (ul, il), (ur, ir) in zip(row, reversed(row)):
                if ul < -0.45 and abs(ul + ur) < 0.08:
                    total += 1
                    same += max(abs(a - b) for a, b in zip(cols[il], cols[ir])) < 0.2
        self.assertGreater(total, 10)
        self.assertGreater(same / total, 0.8)

    def test_the_sun_sets(self):
        d = {"layers": [{"effect": "fan", "target": "Peak", "params": {"style": "sunset", "sink": 20}}]}
        fr = render(d, self.L, 600)
        cv = showpieces.canvas(self.L, self.L.indices("Peak"))
        hot = lambda cols: [v for i, (u, v) in cv["uv"].items() if min(cols[i][0], cols[i][1]) > 0.6]  # noqa: E731
        early, late = hot(fr[20]), hot(fr[-1])
        self.assertTrue(early)
        self.assertGreater(sum(early) / len(early), sum(late) / len(late) if late else -1)
        top = [i for i, (u, v) in cv["uv"].items() if v > 0.9]
        self.assertTrue(all(fr[-1][i][2] > fr[-1][i][1] for i in top), "violet at the top of the sky")

    def test_smooth_enough_for_real_bulbs(self):
        """Ambient looks change gently from frame to frame at 20 fps (no flicker)."""
        for d in ({"layers": [{"effect": "candlelight", "params": {"swell_every": 0}}]},
                  {"layers": [{"effect": "fan", "target": "Peak", "params": {"style": "sunset"}}]},
                  {"layers": [{"effect": "fan", "target": "Peak", "params": {"style": "sunburst", "intro": 0}}]}):
            fr = render(d, self.L, 200)
            worst = max(abs(a - b) for f0, f1 in zip(fr[40:], fr[41:]) for p0, p1 in zip(f0, f1)
                        for a, b in zip(p0, p1))
            self.assertLess(worst, 0.1, d)

    def test_candle_white_is_warm_and_never_too_dark(self):
        """Every candle bulb round the house is warm and never too dark; on the zigzag the
        candles burn in the dark (a picture, not a wash), every lit bulb still warm."""
        fr = render(preset("gratitude-candlelight"), self.L, 100)
        for cols in fr[::10]:
            for i, (r, g, b) in enumerate(cols):
                if i in self.peak:
                    if max(r, g, b) > 0.02:
                        self.assertTrue(r >= g >= b, (r, g, b))
                    continue
                self.assertGreater(r, 0.25)
                self.assertTrue(r >= g >= b, (r, g, b))
            lit = [i for i in self.peak if max(cols[i]) > 0.3]
            dark = [i for i in self.peak if max(cols[i]) < 0.02]
            self.assertTrue(15 <= len(lit) <= 50, len(lit))
            self.assertGreater(len(dark), len(self.peak) // 2, "dark space round the candles")

    def test_falling_star_crosses_the_runs(self):
        L = self.L
        d = {"layers": [{"effect": "falling_star", "params": {"stars": 0, "sparks": 0, "burst": 1, "every": 30}}]}
        fr = render(d, L, 100)
        sky = fr[0][0]
        runs = set()
        for cols in fr:
            for p, c in zip(L.pixels, cols):
                if sum(c) > sum(sky) + 0.3:
                    runs.add(p.run)
        self.assertEqual(runs, {"Eaves", "Peak", "Accent"})

    def test_falling_star_keeps_its_pace_when_slow(self):
        """A long 'every' must not collapse into back-to-back stars."""
        L = layout(HOUSE if HOUSE.exists() else EXAMPLE)
        for every in (4.5, 30, 60):
            with self.subTest(every=every):
                sc = Scene.from_dict({"layers": [{"effect": "falling_star", "params": {"every": every}}]})
                starts, prev = [], 0
                for f in range(20 * 400):
                    sc.render(f / 20, L)
                    cnt = sc.layers[0].state.get("count", 0)
                    if cnt != prev:
                        starts.append(f / 20)
                        prev = cnt
                gaps = [b - a for a, b in zip(starts, starts[1:])]
                self.assertGreater(len(gaps), 2)
                self.assertGreater(min(gaps), every * 0.6, gaps)

    def test_hail_mary_is_orange_and_busy(self):
        """Broncos orange on a dark sky (not pink from orange added onto navy), with a star
        in flight most of the time."""
        L = layout(HOUSE if HOUSE.exists() else EXAMPLE)
        sc = Scene.from_dict(preset("hail-mary"))
        orange = pink = lit = 0
        dark = longest = 0
        for f in range(500):
            cols = sc.render(f / 20, L)
            hot = [c for c in cols if c[0] > 0.6]
            warm = [c for c in hot if c[0] - c[1] > 0.4]              # saturated reds and oranges
            orange += sum(1 for c in warm if c[1] > 0.2 and c[2] < 0.15)
            pink += sum(1 for c in warm if c[2] >= 0.15 and c[2] > 0.7 * c[1])
            if hot:
                lit += 1
                dark = 0
            else:
                dark += 1
                longest = max(longest, dark)
        self.assertGreater(orange, 4 * pink)
        self.assertGreater(lit, 250)                                  # was 195 of 500
        self.assertLess(longest / 20, 2.5)

    def test_the_sleigh_tours_every_roofline_run(self):
        for extra in (0, 100):
            L = layout(EXAMPLE, extra)
            route = showpieces._route(L, list(range(L.n)))
            paths = [d[0] for k, d, a, b in route if k == "path"]
            names = {L.pixels[p.order[0]].run for p in paths}
            self.assertEqual(names, {"Eaves", "Accent"} | ({"Run 4"} if extra else set()))
            self.assertNotIn("Peak", names)                  # the zigzag is not a roofline
            self.assertEqual(L.pixels[paths[0].order[0]].run, "Eaves")   # the top roofline first
            total = route[-1][3]
            self.assertAlmostEqual(route[0][2], 0.0)
            for (_, _, _, b), (_, _, a, _) in zip(route, route[1:]):
                self.assertAlmostEqual(a, b)                 # one continuous loop
            # the sleigh visits each roofline run during a lap
            sc = Scene.from_dict({"layers": [{"effect": "sleigh_tour", "params": {"stars": 0, "sparkle": 0,
                                                                                  "dust": 0, "pause": 0}}]})
            seen = set()
            fps = 20
            for k in range(int(total / 12 * fps) + 40):
                cols = sc.render(k / fps, L)
                for p, c in zip(L.pixels, cols):
                    if c[0] > 0.6 and c[1] < 0.15:           # red: the sleigh or Rudolph's nose
                        seen.add(p.run)
            self.assertTrue(names <= seen, (names, seen))


class Shows(unittest.TestCase):
    def test_new_shows_use_shipped_presets_and_last_three_to_six_minutes(self):
        ids = {p.stem for p in (ROOT / "presets").glob("*.json")}
        for name in NEW_SHOWS:
            sh = shows_builtin.BUILTIN_SHOWS[name]
            total = sum(c["seconds"] for c in sh["cues"])
            self.assertTrue(170 <= total <= 360, (name, total))
            self.assertEqual(sh["end"], "resume")
            for c in sh["cues"]:
                self.assertIn(c["preset"], ids)
                self.assertTrue(shows_builtin.cue_ok(c))

    def test_a_team_show_says_whose_it_is(self):
        """Every install gets the built-in shows, whichever team it follows: one built on a
        team's looks carries the team's name, and none borrows the name of the generic
        score celebration ("Touchdown!")."""
        for name, sh in shows_builtin.BUILTIN_SHOWS.items():
            with self.subTest(show=name):
                self.assertNotEqual(name, "Touchdown!")
                if any("broncos" in c.get("preset", "") for c in sh["cues"]):
                    self.assertIn("Broncos", name)
                    self.assertIn("Broncos", sh["description"])

    def test_every_cue_renders_on_the_house_with_a_fourth_run(self):
        L = layout(HOUSE if HOUSE.exists() else EXAMPLE, 100)
        for name in NEW_SHOWS:
            for c in shows_builtin.BUILTIN_SHOWS[name]["cues"]:
                with self.subTest(show=name, cue=c["preset"]):
                    fr = render(preset(c["preset"]), L, 30)
                    self.assertTrue(any(max(px) > 0.05 for px in fr[-1]))

    def test_calendar_schedules_them_at_the_top_of_the_hour(self):
        ids = [p.stem for p in (ROOT / "presets").glob("*.json")]
        c = cal.build(ids, list(shows_builtin.BUILTIN_SHOWS), [], today=date(2026, 11, 26))
        validate_showtimes(c["showtimes"])
        by = {s["show"]: s for s in c["showtimes"]}
        tg, xm = by["Thanksgiving tonight"], by["Christmas on the roofline"]
        self.assertEqual(tg["dates"], ["11-26"])
        self.assertEqual((tg["from"], tg["to"], tg["every"]), ("18:00", "21:00", 60))
        self.assertTrue(sched.showtime_on_night(tg, date(2026, 11, 26)))
        self.assertFalse(sched.showtime_on_night(tg, date(2026, 11, 27)))
        self.assertTrue(sched.showtime_on_night(xm, date(2026, 11, 27)))
        self.assertTrue(sched.showtime_on_night(xm, date(2026, 12, 23)))
        self.assertFalse(sched.showtime_on_night(xm, date(2026, 12, 24)))      # Santa's coming has the evening
        self.assertTrue(sched.showtime_on_night(xm, date(2026, 12, 25)))
        self.assertFalse(sched.showtime_on_night(xm, date(2026, 12, 31)))
        # the season that is coming next: Thanksgiving 2027 is 25 November
        c2 = cal.build(ids, list(shows_builtin.BUILTIN_SHOWS), [], today=date(2027, 1, 5))
        by2 = {s["show"]: s for s in c2["showtimes"]}
        self.assertEqual(by2["Thanksgiving tonight"]["dates"], ["11-25"])
        self.assertEqual(by2["Christmas on the roofline"]["dates"][0], "11-26")
        self.assertEqual(cal.fmt_dates(xm["dates"], date(2026, 11, 26)), "Nov 27 – Dec 23, Dec 25 – Dec 30")
        # Thanksgiving gets its own rotation with the turkey
        rule = next(r for r in c["rules"] if r["name"] == "Thanksgiving")
        self.assertEqual(rule["program"], {"playlist": "Holiday: Thanksgiving"})
        self.assertIn("turkey-fan", [i["preset"] for i in c["playlists"]["Holiday: Thanksgiving"]["items"]])

    def test_calendar_showtimes_follow_the_holiday_every_year(self):
        """Installed on Thanksgiving 2026, the shows still land on the right nights in later
        years (Thanksgiving moves: 25 Nov 2027, 23 Nov 2028), with no re-tap."""
        ids = [p.stem for p in (ROOT / "presets").glob("*.json")]
        c = cal.build(ids, list(shows_builtin.BUILTIN_SHOWS), [], today=date(2026, 11, 26))
        by = {s["show"]: s for s in c["showtimes"]}
        tg, xm = by["Thanksgiving tonight"], by["Christmas on the roofline"]
        for d, tg_on, xm_on in ((date(2027, 11, 25), True, False), (date(2027, 11, 26), False, True),
                                (date(2028, 11, 23), True, False), (date(2028, 11, 24), False, True),
                                (date(2027, 12, 24), False, False), (date(2027, 12, 30), False, True)):
            with self.subTest(night=d):
                self.assertEqual(sched.showtime_on_night(tg, d), tg_on)
                self.assertEqual(sched.showtime_on_night(xm, d), xm_on)
        # survives a round trip through the saved config and the API's checks
        validate_showtimes(json.loads(json.dumps(c["showtimes"])))
        with self.assertRaises(ValueError):
            validate_showtimes([{"show": "x", "season": [["thanksgiving", "nope"]]}])
        with self.assertRaises(ValueError):
            validate_showtimes([{"show": "x", "season": ["thanksgiving"]}])

    def test_tonight_plan_lists_the_thanksgiving_show(self):
        from datetime import datetime
        from goveelights import tonight
        ids = [p.stem for p in (ROOT / "presets").glob("*.json")]
        c = cal.build(ids, list(shows_builtin.BUILTIN_SHOWS), [], today=date(2026, 11, 26))
        st = next(s for s in c["showtimes"] if s["show"] == "Thanksgiving tonight")
        tz = datetime.now().astimezone().tzinfo
        ts = tonight.showtime_times(st, datetime(2026, 11, 26, 16, 0, tzinfo=tz), datetime(2026, 11, 26, 23, 0, tzinfo=tz))
        self.assertEqual([t.hour for t in ts], [18, 19, 20, 21])

    def test_owner_settings_are_untouched_by_an_update(self):
        """Nothing schedules the new shows until the owner taps 'Use the holiday calendar'."""
        from goveelights.config import DEFAULTS
        self.assertEqual(DEFAULTS.get("showtimes", []), [])
        for r in DEFAULTS["schedule"]["rules"]:
            self.assertNotIn("Holiday: Thanksgiving", json.dumps(r))


if __name__ == "__main__":
    unittest.main()
