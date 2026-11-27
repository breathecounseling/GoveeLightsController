"""Round 13: the Tip (the fourth box, an H705E) as the top of the Peak's gable.

"Add it to the show" -> "Top of the Peak (completes the gable)" places the Tip's visible
bulbs on rows above the Peak's top row (from the Peak's own geometry), hides the rest,
joins it into the gable zones and makes it continue the Peak (layout 'extends'): Peak
layers, the zigzag pictures, the tree's star, text and the sleigh use it. Without the Tip
every frame is byte-identical to before (tests/test_r8_opensource.py); with it, the frames
are recorded here. "Take it out" restores everything."""
from __future__ import annotations

import copy
import datetime as _dt
import hashlib
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).parent))

from goveelights import addrun, geometry, livedata  # noqa: E402
from goveelights.effects_ext import dusk_depth, night_sky, peak_pictures, roofline_physics, scrolltext  # noqa: E402
from goveelights.effects_ext import showpieces, tree  # noqa: E402
from goveelights.layout import Layout  # noqa: E402
from goveelights.scene import Scene  # noqa: E402

from preview import house_layout  # noqa: E402

OWNER = ROOT / "layouts" / "house.json"
HAS_OWNER = OWNER.exists()


def owner():
    return house_layout(OWNER)


def tipped(**k):
    return addrun.layout_with_tip(owner(), **k)


def render(pid, L, n=40):
    sc = Scene.from_dict(json.loads((ROOT / "presets" / f"{pid}.json").read_text()))
    hh = hashlib.sha256()
    for k in range(n):
        cols = sc.render(k / 10, L)
        hh.update(bytes(min(255, max(0, int(c * 255 + .5))) for px in cols for c in px))
    return hh.hexdigest()[:16]


@unittest.skipUnless(HAS_OWNER, "the owner's layout is not in this copy")
class TipGeometry(unittest.TestCase):
    def test_rows_above_the_peak_from_its_own_geometry(self):
        host = addrun.zigzag_host(owner())
        self.assertEqual(host.name, "Peak")
        # the Peak: 0.7 between bulbs, centre line x = -40, sides with slope 0.6, its top row
        # (98-107) ends at the south corner: row 7 runs back south -> north, 0.63 apart
        self.assertEqual(addrun.tip_geometry(host),
                         [[0, -42.1, 4.38], [6, -37.9, 4.38], [7, -38.95, 5.01], [10, -41.05, 5.01],
                          [11, -40.0, 5.64]])
        self.assertEqual(addrun.tip_geometry(host, flip=True),
                         [[0, -37.9, 4.38], [6, -42.1, 4.38], [7, -41.05, 5.01], [10, -38.95, 5.01],
                          [11, -40.0, 5.64]])
        # fewer visible bulbs: the rows fill in order and stop
        self.assertEqual(addrun.tip_geometry(host, visible=9), [[0, -42.1, 4.38], [6, -37.9, 4.38],
                                                                 [7, -38.95, 5.01], [8, -39.65, 5.01]])
        # other rows (if the '6 = 2nd row' landmark means something else): still narrowing upward
        g = addrun.tip_geometry(host, 12, (6, 5, 1))
        self.assertEqual([k[0] for k in g], [0, 5, 6, 10, 11])
        self.assertLess(g[0][2], g[2][2])
        self.assertLess(g[2][2], g[4][2])

    def test_parse_rows(self):
        self.assertEqual(addrun.parse_rows("7, 4, 1"), (7, 4, 1))
        self.assertEqual(addrun.parse_rows("7+4+1"), (7, 4, 1))
        self.assertEqual(addrun.parse_rows(None), addrun.TIP_ROWS)
        for bad in ("7, x", "0, 3", "-1"):
            with self.assertRaises(ValueError):
                addrun.parse_rows(bad)

    def test_the_house_frame_does_not_move(self):
        L, T = owner(), tipped()
        self.assertEqual((L.minx, L.maxx, L.miny, L.maxy), (T.minx, T.maxx, T.miny, T.maxy))
        self.assertEqual(T.n, L.n + 12)
        # the other runs keep their virtual indexes and positions
        for a, b in zip(L.pixels, T.pixels):
            self.assertEqual((a.i, a.mac, a.local, a.x, a.y, a.nx, a.ny), (b.i, b.mac, b.local, b.x, b.y, b.nx, b.ny))

    def test_the_tip_continues_the_peak(self):
        T = tipped()
        tip = T.run("Tip")
        self.assertEqual(len(tip.pixels), 12)
        self.assertTrue(all(p.run == "Peak" and p.ext for p in tip.pixels))
        self.assertEqual(T.extensions("Peak"), [tip])
        self.assertEqual(len(T.indices("Peak")), 120)
        self.assertEqual(T.indices("Tip"), [p.i for p in tip.pixels])
        for z in ("Zigzag top half", "Whole front gable", "Whole front"):
            self.assertTrue(set(T.indices("Tip")) <= set(T.indices(z)), z)
        for z in ("Zigzag bottom half", "Front gable outline", "Porch", "Lower roofline"):
            self.assertFalse(set(T.indices("Tip")) & set(T.indices(z)), z)
        # the Peak's own per-run heights now reach the apex
        self.assertAlmostEqual(max(p.rny for p in tip.pixels), 1.0)
        self.assertLess(max(p.rny for p in T.run("Peak").pixels), 0.7)
        # one zigzag of nine rows, the apex a row of its own
        rows = geometry._rows_by_reversal(T.pixels, T.indices("Peak"))
        self.assertEqual([len(r) for r in rows], [27, 23, 20, 16, 12, 10, 7, 4, 1])
        # a role target for the gable plays on it too
        self.assertIn("Tip", T._by_role("apex"))

    def test_bulb_order_runs_on_from_the_peak_into_the_tip(self):
        T = tipped()
        peak, tip = T.run("Peak"), T.run("Tip")
        hn = len(peak.pixels)
        self.assertEqual([p.ri for p in peak.pixels], list(range(hn)))
        self.assertEqual([p.ri for p in tip.pixels], list(range(hn, hn + 12)))
        self.assertTrue(all(p.rn == hn + 12 for p in peak.pixels + tip.pixels))
        self.assertAlmostEqual(tip.pixels[-1].ru, 1.0)
        self.assertAlmostEqual(peak.pixels[0].ru, 0.0)
        # neighbours along the run step from Peak bulb 107 straight onto Tip bulb 0
        seq = peak.pixels[0].seq
        self.assertEqual(seq[hn - 1:hn + 1], (peak.pixels[-1].i, tip.pixels[0].i))
        self.assertTrue(all(p.seq is seq for p in peak.pixels + tip.pixels))
        # without the Tip the Peak's own numbering is untouched
        L = owner()
        self.assertTrue(all(p.rn == hn and p.ru == p.ri / (hn - 1) for p in L.run("Peak").pixels))

    def test_disabled_or_missing_host_changes_nothing(self):
        L = owner()
        runs = addrun.copy_runs(L)
        from goveelights.layout import Run
        runs.append(Run(mac="TIP", name="Tip", count=108, extends="NO SUCH RUN", hidden=set(range(12, 108)),
                        geometry=[[0, -42, 4.4], [11, -40, 5.6]]))
        X = Layout(runs, L.zone_specs)
        self.assertEqual(len(X.indices("Peak")), 108)
        self.assertTrue(all(p.run == "Tip" for p in X.run("Tip").pixels))


@unittest.skipUnless(HAS_OWNER, "the owner's layout is not in this copy")
class TipInTheEffects(unittest.TestCase):
    def test_tree_star_moves_to_the_apex(self):
        T, L = tipped(), owner()
        apex = T.run("Tip").pixels[11].i
        g = tree.tree_geometry(T, T.indices("Whole front gable"))
        self.assertEqual(g["star"], [apex])
        self.assertEqual(g["rows"], 9)
        self.assertEqual(tree.tree_geometry(L, L.indices("Whole front gable"))["star"], [42, 43])   # as before
        # on the Peak alone too
        self.assertEqual(tree.tree_geometry(T, T.indices("Peak"))["star"], [apex])

    def test_presets_light_the_apex_with_the_star(self):
        T = tipped()
        apex = T.run("Tip").pixels[11].i
        for pid in ("christmas-tree", "tree-lighting-ceremony"):
            sc = Scene.from_dict(json.loads((ROOT / "presets" / f"{pid}.json").read_text()))
            cols = [sc.render(k / 4, T) for k in range(0, 160)]
            c = cols[-1][apex]
            self.assertGreater(min(c[0], c[1]), 0.5, (pid, c))          # warm white / gold star
            # the tree runs up into the Tip: its boughs and star there are lit; the Tip's
            # outer bulbs are the dark sky beside the tree (round 13 honeycomb tree)
            plan = tree.tree_plan(T, T.indices("Whole front gable"))
            tip = {p.i for p in T.run("Tip").pixels}
            drawn = tip & (set(plan["leaf"]) | set(plan["star"]))
            self.assertEqual(len(drawn), 6, pid)              # the star (3) and the top boughs (3)
            self.assertTrue(all(max(cols[-1][i]) > 0.2 for i in drawn), pid)

    def test_canvas_keeps_the_peaks_scale(self):
        T, L = tipped(), owner()
        a = showpieces.canvas(L, L.indices("Whole front gable"))
        b = showpieces.canvas(T, T.indices("Whole front gable"))
        self.assertEqual({i: a["uv"][i] for i in a["zig"]}, {i: b["uv"][i] for i in a["zig"]})
        self.assertEqual(len(b["tip"]), 12)
        self.assertGreater(min(b["uv"][i][1] for i in b["tip"]), 1.0)
        self.assertAlmostEqual(b["apex"][1], 5.64)

    def test_every_picture_uses_the_tip_and_the_star_crowns_the_apex(self):
        T = tipped()
        tip = [p.i for p in T.run("Tip").pixels]
        apex = tip[-1]
        for style in peak_pictures.STYLES:
            sc = Scene.from_dict(json.loads((ROOT / "presets" / f"peak-{style}.json").read_text()))
            lit = [0] * len(tip)
            for k in range(0, 200, 5):
                cols = sc.render(k / 10, T)
                for j, i in enumerate(tip):
                    lit[j] = max(lit[j], max(cols[i]))
            self.assertGreater(sum(v > 0.1 for v in lit), 3, style)
            if style in ("star", "ornament", "snowman", "wreath", "joy", "gingerbread"):
                self.assertGreater(lit[-1], 0.4, style)
        cols = Scene.from_dict(json.loads((ROOT / "presets" / "peak-star.json").read_text())).render(1.0, T)
        self.assertGreater(min(cols[apex]), 0.6)
        # the Eaves bulbs that used to crown it are trim again
        self.assertLess(min(cols[42]), 0.6)

    def test_text_uses_it_as_border(self):
        T = tipped()
        d = scrolltext._display(T, "Peak", T.indices("Peak"), {})
        border = {i for i, _ in d["border"]}
        self.assertTrue({p.i for p in T.run("Tip").pixels} <= border)
        self.assertEqual(d["rows"], 5)

    def test_sleigh_and_coasters_stay_on_the_rooflines(self):
        T, L = tipped(), owner()
        tip = {p.i for p in T.run("Tip").pixels}
        chains = showpieces._roof_chains(T, list(range(T.n)))
        self.assertFalse(any(set(c.order) & tip for c in chains))
        self.assertEqual(len(chains), len(showpieces._roof_chains(L, list(range(L.n)))))
        tracks = roofline_physics._roof_tracks(T, list(range(T.n)))
        self.assertFalse(any(set(t.order) & tip for t in tracks))


FROZEN = 1796940000.0


class _FixedDT(_dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return _dt.datetime(2026, 12, 10, 18, 0, tzinfo=tz) if tz else _dt.datetime(2026, 12, 10, 18, 0)


# Frame hashes with the Tip (12 bulbs, as added by "Top of the Peak"), recorded the same way
# as tests/test_r8_opensource.py GOLDEN (40 frames at t = k/10, clock frozen at FROZEN, the
# date 2026-12-10, TZ America/Denver). TIP_SAME: the presets whose frames on the original
# bulbs (Eaves, Peak, Accent) are unchanged by the Tip; it only adds the Tip's own bulbs.
# The others change for a reason: the Peak's heights now reach the apex, the tree's star and
# the pictures' crown move up, or the strip is 12 bulbs longer (effects that run along it).
TIP_GOLDEN = {
    "advent-calendar": "ce04fd20c5c18fd1", "apex-ripples": "880eff34785c59c9", "ball-drop-at-midnight": "8a22628a34bcd00e",
    "ball-drop-preview-loop": "1a9163b0c12dfd22", "breathing-comets": "a3a8bef3c3e3ac6a", "broncos-game-day": "909e7e3613cb8481",
    "candy-cane": "5e2363004f4e511d", "champagne-fizz": "4859f951a5cf63c6", "christmas-tree": "2d9ff0fefd721b57",
    "christmas-twinkle": "c9e9b8b992164b14", "comets": "c7cf23e55a210d3b", "diamond-glitter": "d7d587adcdf79946",
    "dusk-demo": "67b9c39628124cbd", "dusk-to-night": "8d77cfd04e66bcd4", "easter-pastels": "9116479118315288",
    "ember-falls": "e1ec65843cc41dc0", "falling-waves": "51a5f0182aa98bc5", "festive-glitter": "45682c91800ecb0e",
    "fill-the-peak": "294ecbe6f4b1e82c", "fireworks": "350c00e20ed3d0b6", "gift-wrap": "8001f342e8845cf7",
    "glitter-ball": "655ef72718b2deb4", "golden-shimmer": "f10b106a30cce3c1", "golden-tree": "bcbcfa5fe9a97a4c",
    "grand-finale": "43c110aeebcdf19e", "halloween": "b186e0ad86f1c62c", "halloween-haunted": "af0594033b8001b1",
    "halloween-pumpkin": "c284e80289ebf78e", "heartbeat-from-the-apex": "e64381aa0982055b", "icicles": "7fd33f2a5986c30a",
    "icy-tree": "b521e21b7a9b3d9c", "july-4th-fireworks": "2991e7fc6198fa36", "july-4th-flag": "ca932b00191d622f",
    "lighthouse": "7e9ae3d1d3a57b77", "mirror-comets": "08ee50dc017b2871", "music-pulse": "96522b8b2cc0b0f9",
    "northern-lights": "ecdbafd936dfdd69", "paparazzi": "7fad842081535208", "party-beat": "0f4db2764cbd30e3",
    "peak-fire": "c84c2ee7bf0344c3", "peak-gingerbread": "8c1db2025fe8b8fd", "peak-joy": "c4d70869c8dc340b",
    "peak-ornament": "74274a6023a07e30", "peak-present": "7602b4ccd828be2a", "peak-rudolph": "5746de945b43f37e",
    "peak-snowman": "6ccf9ce88f2e1f03", "peak-star": "c48065424fcc0a2e", "peak-wreath": "5e9dca1141f7c3d5",
    "plasma-party": "e5de57956ccac482", "porch-fireworks": "f96ba105277205aa", "rainbow-roofline": "98041943a35979a5",
    "roller-coaster": "e41e629d412706fb", "santas-sleigh": "dd17a10bf7c33cba", "scanner-trails": "e332fd04aedcc631",
    "sequin-wave": "d54313ad9d577eef", "sleigh-grand-tour": "f9150ffd5ea89da4", "snow-piling-up": "f661b8653667bbc4",
    "snowy-evening": "ba2267fdd9303da1", "sparkler-tree": "2a74b898a3911656", "spooky-tree": "9c5b17296d2ee4db",
    "spring-garden": "dafde51e69ffae29", "st-patricks": "7bbd6fdd892eb173", "starry-night": "5aeffa5a346fc93e",
    "starry-night-sleigh": "64c07353762dfa83", "summer-evening": "304103320ea1ff8b", "summer-fireflies": "c12c960aadfe9c5b",
    "text-give-thanks-letters": "f0ac6a9aebce6e72", "text-go-broncos-letters": "91a8b8fb62e73515", "text-happy-new-year-letters": "1862872b5c5bdb24",
    "text-merry-christmas-letters": "b6fcc6d61b94f0b3", "text-scoreboard-letters": "7ef7c055069da063", "thanksgiving-harvest": "1954ca90a1e28ffc",
    "text-give-thanks": "ffc18422853331d9", "text-go-broncos": "eac86ecc4c09a0a2", "text-happy-new-year": "8610c3b8c6491846",
    "text-merry-christmas": "239bbf4fd7823459", "text-scoreboard": "0fc622415646d622", "text-touchdown": "5a13a84405d9d9cf",
    "tree-lighting-ceremony": "1b38620ed1d6b148", "twin-coasters": "60157576150ea074", "valentines-glow": "f8ee23465f1c6886",
    "valentines-hearts": "00fe0db6867c034b", "warm-white": "e253164cd9245bb6", "winter-elegance": "4255ee8d325042c5",
    "winter-sparkle": "4256ee9c4b9c1025",
}
TIP_SAME = {"apex-ripples", "candy-cane", "champagne-fizz", "christmas-twinkle", "comets",
    "falling-waves", "festive-glitter", "fill-the-peak", "glitter-ball", "golden-shimmer",
    "icicles", "lighthouse", "northern-lights", "paparazzi", "party-beat",
    "peak-joy", "peak-rudolph", "peak-snowman", "peak-wreath", "plasma-party",
    "porch-fireworks", "rainbow-roofline", "santas-sleigh", "scanner-trails", "sequin-wave", "sleigh-grand-tour",
    "snow-piling-up", "snowy-evening", "spring-garden", "st-patricks", "starry-night", "starry-night-sleigh",
    "summer-evening", "summer-fireflies", "text-give-thanks-letters", "text-go-broncos-letters", "text-happy-new-year-letters",
    "text-merry-christmas-letters", "text-scoreboard-letters", "text-give-thanks", "text-go-broncos",
    "text-happy-new-year", "text-merry-christmas", "text-scoreboard", "text-touchdown",
    "warm-white", "winter-elegance", "winter-sparkle"}


@unittest.skipUnless(HAS_OWNER, "the owner's layout is not in this copy")
@unittest.skipUnless(sys.platform.startswith("linux"), "golden frames were recorded on Linux")
class WithTipFrames(unittest.TestCase):
    def setUp(self):
        import time
        if time.strftime("%Z") not in ("MST", "MDT"):
            self.skipTest("run with TZ=America/Denver")
        try:
            from goveelights.legacy_defaults import LEGACY_DEFAULTS
        except ImportError:
            self.skipTest("no legacy defaults")
        self.home = (LEGACY_DEFAULTS["schedule.lat"], LEGACY_DEFAULTS["schedule.lon"])

    def hashes(self, L, pids):
        home, games = livedata.LIVE.get("home"), livedata.LIVE.get("games")
        self.addCleanup(livedata.LIVE.update, {"home": home, "games": games})
        livedata.LIVE["games"] = {}
        livedata.set_home(self.home)
        dusk_depth._memo.__dict__.clear()
        with mock.patch("time.time", return_value=FROZEN), \
                mock.patch.object(night_sky, "_today", lambda: _dt.date(2026, 12, 10)), \
                mock.patch.object(dusk_depth, "datetime", _FixedDT):
            return {pid: render(pid, L) for pid in pids}

    def test_with_tip_frames_match_the_recorded_ones(self):
        got = self.hashes(tipped(), sorted(TIP_GOLDEN))
        self.assertEqual({k: v for k, v in got.items() if v != TIP_GOLDEN[k]}, {})

    def test_presets_that_dont_reach_the_gable_look_the_same_elsewhere(self):
        L, T = owner(), tipped()
        same = set()
        for pid in sorted(TIP_GOLDEN):
            a = Scene.from_dict(json.loads((ROOT / "presets" / f"{pid}.json").read_text()))
            b = Scene.from_dict(json.loads((ROOT / "presets" / f"{pid}.json").read_text()))
            rnd = lambda cols: [tuple(round(v, 6) for v in c) for c in cols]  # noqa: E731
            with mock.patch("time.time", return_value=FROZEN):
                if all(rnd(a.render(k / 10, L)) == rnd(b.render(k / 10, T)[:L.n]) for k in range(12)):
                    same.add(pid)
        self.assertEqual(same, TIP_SAME)


# ---------------------------------------------------------------- the add-run flow
class AddTheTip(unittest.TestCase):
    def setUp(self):
        if not HAS_OWNER:
            self.skipTest("the owner's layout is not in this copy")
        from test_r10_fourth_run import ControllerFourthRun, house_cfg
        self.house_cfg = house_cfg
        case = ControllerFourthRun("test_server_endpoints")
        case.addCleanup = self.addCleanup
        case.setUp()
        self.case = case

    def cfg(self):
        from test_r10_fourth_run import NEW
        c = self.house_cfg(self, "layouts/house.json")
        return c, NEW

    def test_add_places_hides_joins_and_take_it_out_restores(self):
        c, mac = self.cfg()
        before = copy.deepcopy(c.data)
        L = Layout.from_config(c)
        res = addrun.add_run(c, L, mac, 108, "peak top")
        self.assertEqual(res["name"], "Tip")
        self.assertEqual(res["tip"], {"visible": 12, "flip": False, "rows": [7, 4, 1], "host": "Peak"})
        self.assertEqual(res["joined"], "Zigzag top half, Whole front gable, Whole front")
        d = c.devices[mac]
        self.assertEqual(d["role"], "peak")
        self.assertEqual(d["hidden"], list(range(12, 108)))
        self.assertEqual(d["extends"], L.run("Peak").mac)
        self.assertEqual(d["geometry"][-1], [11, -40.0, 5.64])
        self.assertIsNone(d.get("follows"))
        T = Layout.from_config(c)
        self.assertEqual(len(T.indices("Peak")), 120)
        self.assertEqual(len(T.indices("Whole front gable")), 144)
        parts = [pt for z in c.data["zones"] for pt in z["parts"] if pt["run"] == "Tip"]
        self.assertEqual(parts, [{"run": "Tip", "from": 0, "to": 11}] * 3)
        # change it: flipped, nine bulbs
        addrun.add_run(c, T, mac, 108, "peak top", visible=9, flip=True)
        self.assertEqual(c.devices[mac]["hidden"], list(range(9, 108)))
        self.assertEqual(c.devices[mac]["geometry"][0], [0, -37.9, 4.38])
        self.assertEqual([pt["to"] for z in c.data["zones"] for pt in z["parts"] if pt["run"] == "Tip"], [8] * 3)
        # take it out: the config is exactly as before
        addrun.remove_run(c, mac)
        self.assertEqual(c.data, before)
        L2 = Layout.from_config(c)
        for pid in ("christmas-tree", "peak-star", "text-merry-christmas"):
            self.assertEqual(render(pid, L, 6), render(pid, L2, 6), pid)

    def test_refusals(self):
        c, mac = self.cfg()
        L = Layout.from_config(c)
        with self.assertRaises(ValueError):
            addrun.add_run(c, L, mac, 108, "peak top", visible=13)          # the rows hold 12
        with self.assertRaises(ValueError):
            addrun.add_run(c, L, mac, 10, "peak top", visible=12)           # more than the box has
        with self.assertRaises(ValueError):
            addrun.add_run(c, L, mac, 108, "peak top", rows="a, b")
        addrun.add_run(c, L, mac, 108, "peak top", visible=13, rows="7, 4, 2")
        self.assertEqual(c.devices[mac]["hidden"][0], 13)
        # a house without a zigzag can't have a Tip
        c2 = self.house_cfg(self)
        from goveelights.layout import Run
        flat = Layout([Run(mac="A", name="Eaves", count=20, geometry=[[0, 0, 0], [19, 19, 0]])])
        with self.assertRaises(ValueError):
            addrun.add_run(c2, flat, mac, 108, "peak top")

    def test_a_refused_change_leaves_the_config_alone(self):
        c, mac = self.cfg()
        addrun.add_run(c, Layout.from_config(c), mac, 108, "peak top")
        peak = Layout.from_config(c).run("Peak").mac
        c.devices[peak]["enabled"] = False
        before = copy.deepcopy(c.data)
        with self.assertRaises(ValueError):
            addrun.add_run(c, Layout.from_config(c), mac, 108, "peak top", visible=10)
        self.assertEqual(c.data, before)

    def test_controller_and_state(self):
        r, ctl = self.case.make()
        st = ctl.fourth_state()
        self.assertIn("peak top", [x["id"] for x in st["locations"]])
        self.assertTrue(st["tip"]["host"])
        res = ctl.add_run({"mac": self.case_new(), "count": 108, "where": "peak top", "visible": 12, "flip": False,
                           "rows": "7, 4, 1"})
        self.assertIn("tip", res)
        L = ctl.engine.layout
        self.assertEqual(len(L.run(res["name"]).pixels), 12)
        self.assertEqual(L.extensions(res["tip"]["host"]), [L.run(res["name"])])
        st2 = ctl.fourth_state()
        self.assertEqual(st2["tip"]["zones"], st["tip"]["zones"])      # what it joins, without itself
        box = [b for b in st2["boxes"] if b["mac"] == self.case_new()][0]
        self.assertEqual(box["tip"]["visible"], 12)
        ctl.remove_run(self.case_new())
        self.assertIsNone(ctl.engine.layout.run(res["name"]))

    def case_new(self):
        from test_r10_fourth_run import NEW
        return NEW


if __name__ == "__main__":
    unittest.main()
