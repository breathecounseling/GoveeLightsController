"""Round 12: December pictures on the Peak (wreath, snowman, gingerbread house, present,
winter star, ornament, Rudolph, JOY), each with a matching trim on the other runs; their
presets, the 'Peak pictures' show and their place in the Christmas-season calendar
(from the day after Thanksgiving: Thanksgiving itself is unchanged)."""
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
from goveelights.effects import EFFECTS  # noqa: E402
from goveelights.effects_ext import peak_pictures  # noqa: E402
from goveelights.scene import Scene  # noqa: E402

from test_r10_showpieces import EXAMPLE, HOUSE, digest, layout, render  # noqa: E402

STYLES = ["wreath", "snowman", "gingerbread", "present", "star", "ornament", "rudolph", "joy"]
PRESETS = [f"peak-{s}" for s in STYLES]

# 40 frames at t = k/10 on the owner's layout (the pictures don't read the clock or date)
GOLDEN = {
    "peak-wreath": "8e65ed7e84364442", "peak-snowman": "2fc5f79bc0fc00b1", "peak-gingerbread": "777a778cde8a9000",
    "peak-present": "2394214a0a1348e9", "peak-star": "b82ff744a0717143", "peak-ornament": "2ae9eb0db050c9a3",
    "peak-rudolph": "7840e75375353b42", "peak-joy": "bd3f2e5825c02ecd",
}


def preset(pid: str) -> dict:
    return json.loads((ROOT / "presets" / f"{pid}.json").read_text(encoding="utf-8"))


def layouts():
    out = [("example", layout(EXAMPLE)), ("example + 4th run", layout(EXAMPLE, 100))]
    if HOUSE.exists():
        out += [("house", layout(HOUSE)), ("house + 4th run", layout(HOUSE, 100))]
    return out


def owner_hash(pid: str) -> str:
    L = layout(HOUSE)
    sc = Scene.from_dict(preset(pid))
    hh = hashlib.sha256()
    for k in range(40):
        cols = sc.render(k / 10, L)
        hh.update(bytes(min(255, max(0, int(c * 255 + .5))) for px in cols for c in px))
    return hh.hexdigest()[:16]


class PeakPicturePresets(unittest.TestCase):
    def test_every_style_has_a_preset(self):
        self.assertEqual(peak_pictures.STYLES, STYLES)
        for s, pid in zip(STYLES, PRESETS):
            d = preset(pid)
            self.assertEqual(d["layers"][0]["params"]["style"], s)
            self.assertIn("Christmas", d["tags"])

    def test_params_are_in_range(self):
        for pid in PRESETS:
            for layer in preset(pid)["layers"]:
                eff = EFFECTS[layer["effect"]]
                spec = {p.name: p for p in eff.params}
                for k, v in layer.get("params", {}).items():
                    with self.subTest(preset=pid, param=k):
                        self.assertIn(k, spec)
                        p = spec[k]
                        if p.options:
                            self.assertIn(v, p.options)
                        if isinstance(v, (int, float)) and p.min is not None:
                            self.assertGreaterEqual(v, p.min)
                            self.assertLessEqual(v, p.max)

    def test_render_deterministic_in_range_and_lit_everywhere(self):
        for name, L in layouts():
            four = [p.i for p in L.pixels if p.run == "Run 4"]
            for pid in PRESETS:
                with self.subTest(layout=name, preset=pid):
                    a = render(preset(pid), L, 120)
                    self.assertEqual(digest(a), digest(render(preset(pid), L, 120)))
                    for cols in a:
                        self.assertEqual(len(cols), L.n)
                        self.assertTrue(all(0.0 <= c <= 1.0 for px in cols for c in px))
                    lit = sum(1 for px in a[-1] if max(px) > 0.02)
                    self.assertGreater(lit, L.n * 0.8, "the whole house takes part")
                    if four:                         # a fourth run gets the trim by itself
                        self.assertGreater(sum(1 for i in four if max(a[-1][i]) > 0.02), 90)

    def test_each_picture_is_a_picture(self):
        # the zigzag shows a shape (several distinct colours, some bright, some dark),
        # it moves, and no two pictures look alike
        L = layout(HOUSE if HOUSE.exists() else EXAMPLE)
        peak = [p.i for p in L.pixels if p.run == "Peak"]
        seen = {}
        for pid in PRESETS:
            with self.subTest(preset=pid):
                fr = render(preset(pid), L, 200)
                last = [tuple(round(c, 1) for c in fr[-1][i]) for i in peak]
                self.assertGreaterEqual(len(set(last)), 3)
                bright = sum(1 for c in last if max(c) >= 0.6)
                self.assertGreater(bright, 8)
                self.assertGreater(len(peak) - bright, 8)
                self.assertNotEqual([fr[60][i] for i in peak], [fr[199][i] for i in peak], "it animates")
                seen[pid] = tuple(last)
        self.assertEqual(len(set(seen.values())), len(PRESETS))

    def test_present_opens_and_the_trim_flashes(self):
        L = layout(HOUSE if HOUSE.exists() else EXAMPLE)
        eaves = [p.i for p in L.pixels if p.run == "Eaves"]
        d = preset("peak-present")
        fr = render(d, L, 20 * 18)
        shut = sum(max(fr[20 * 2][i]) for i in eaves)
        opened = sum(max(fr[int(20 * 18 * 0.65)][i]) for i in eaves)
        self.assertGreater(opened, shut * 1.15)

    def test_survives_odd_targets_and_clocks(self):
        for target in ("Peak", "Porch", ["Peak", "Porch"], "Whole front gable"):
            d = {"layers": [{"effect": "peak_picture", "target": target, "params": {"style": s}} for s in STYLES[:1]]}
            for s in STYLES:
                d["layers"][0]["params"]["style"] = s
                L = layout(EXAMPLE)
                sc = Scene.from_dict(d)
                for t in (0, 5, 3, 3, 100, 0):           # a clock that stalls or runs back
                    cols = sc.render(t, L)
                    self.assertTrue(all(0.0 <= c <= 1.0 for px in cols for c in px))

    def test_apex_follows_the_brightness_sliders(self):
        # the star's and the ornament's top bulb (over the zigzag) dims with Picture
        # brightness, and goes dark with the trim at 0 ("0 = dark")
        from goveelights.effects_ext.showpieces import _apex
        L = layout(HOUSE if HOUSE.exists() else EXAMPLE)
        idx = list(range(L.n))
        top = _apex(L, idx, 1)
        self.assertTrue(top)
        for s in ("star", "ornament"):
            with self.subTest(style=s):
                def at(**kw):
                    d = {"layers": [{"effect": "peak_picture", "params": dict(style=s, **kw)}]}
                    return render(d, L, 5)[-1][top[0]]
                full = at(level=1.0, trim_level=0.7)
                dim = at(level=0.2, trim_level=0.7)
                self.assertLessEqual(max(dim), max(full) * 0.2 + 1e-6)
                self.assertGreater(max(dim), 0.0)
                self.assertEqual(max(at(level=0.2, trim_level=0)), 0.0)

    def test_present_only_slider_says_so(self):
        spec = {p.name: p for p in EFFECTS["peak_picture"].params}
        self.assertIn("Present only", spec["cycle"].label)
        self.assertIn("Peak", EFFECTS["peak_picture"].description)
        self.assertIn("six rows", EFFECTS["peak_picture"].description)

    @unittest.skipUnless(HOUSE.exists(), "the owner's layout is not in this copy")
    @unittest.skipUnless(sys.platform.startswith("linux"), "frame hashes were recorded on Linux")
    def test_owner_frame_hashes(self):
        got = {pid: owner_hash(pid) for pid in PRESETS}
        self.assertEqual({k: v for k, v in got.items() if v != GOLDEN.get(k)}, {}, got)


class PeakPicturesShowAndCalendar(unittest.TestCase):
    def test_show(self):
        sh = shows_builtin.BUILTIN_SHOWS["Peak pictures"]
        self.assertEqual([c["preset"] for c in sh["cues"]], PRESETS)
        self.assertEqual(sh["end"], "resume")
        for c in sh["cues"]:
            self.assertTrue(shows_builtin.cue_ok(c))
            self.assertTrue((ROOT / "presets" / f"{c['preset']}.json").exists())

    def _calendar(self, today):
        ids = [p.stem for p in (ROOT / "presets").glob("*.json")]
        built = cal.build(ids, list(shows_builtin.BUILTIN_SHOWS), today=today)
        sch, pls, sts = cal.merge(built, {"rules": []}, {}, [], ids)
        return sch, pls, sts

    def test_thanksgiving_tonight_is_unchanged(self):
        sch, pls, sts = self._calendar(date(2026, 11, 26))
        prog, _ = sched.program_for(date(2026, 11, 26), sch)
        self.assertEqual(prog, {"playlist": "Holiday: Thanksgiving"})
        items = [i["preset"] for i in pls["Holiday: Thanksgiving"]["items"]]
        self.assertEqual(items, ["turkey-fan", "thanksgiving-harvest", "harvest-sunset", "gratitude-candlelight",
                                 "text-give-thanks"])
        self.assertFalse(any(p.startswith("peak-") for p in items))
        cues = [c["preset"] for c in shows_builtin.BUILTIN_SHOWS["Thanksgiving tonight"]["cues"]]
        self.assertEqual(cues, ["harvest-sunset", "turkey-fan", "thanksgiving-harvest", "text-give-thanks",
                                "gratitude-candlelight", "turkey-fan"])

    def test_christmas_season_from_the_day_after(self):
        sch, pls, sts = self._calendar(date(2026, 11, 26))
        for d, name in ((date(2026, 11, 27), "Holiday: Christmas"), (date(2026, 12, 10), "Holiday: Advent"),
                        (date(2026, 12, 25), "Holiday: Christmas Day")):
            prog, _ = sched.program_for(d, sch)
            self.assertEqual(prog, {"playlist": name})
            items = [i["preset"] for i in pls[name]["items"]]
            pics = [p for p in items if p.startswith("peak-")]
            self.assertGreaterEqual(len(pics), 2, name)
            self.assertLess(len(pics), len(items) / 2 + 1, "the pictures share the evening")
            self.assertTrue(all(a != b for a, b in zip(items, items[1:])))

    def test_joy_waits_until_it_has_been_seen_from_the_street(self):
        # JOY stays in the Peak pictures show (last cue) but not in the Christmas Day rotation
        sch, pls, sts = self._calendar(date(2026, 11, 26))
        items = [i["preset"] for i in pls["Holiday: Christmas Day"]["items"]]
        self.assertNotIn("peak-joy", items)
        self.assertIn("peak-snowman", items)
        cues = [c["preset"] for c in shows_builtin.BUILTIN_SHOWS["Peak pictures"]["cues"]]
        self.assertEqual(cues[-1], "peak-joy")


if __name__ == "__main__":
    unittest.main()
