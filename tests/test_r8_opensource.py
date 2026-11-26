"""Open-source readiness: neutral defaults for new installs with a one-time migration that
keeps an existing house exactly as it was, layer targets that work on any layout, the
first-run setup helpers, layout import onto other boxes, and the public export check."""
import copy
import datetime as _dt
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
from test_rack import HOME, LAYOUT_FILE, house  # noqa: E402

from goveelights import calendar_builtin as cal  # noqa: E402
from goveelights import firstrun, livedata, tonight  # noqa: E402
from goveelights import schedule as sched  # noqa: E402
from goveelights.app_server import validate_schedule  # noqa: E402
from goveelights.config import DEFAULTS, Config, migrate_legacy_defaults  # noqa: E402
from goveelights.controller import PresetStore  # noqa: E402
from goveelights.effects_ext import dusk_depth, night_sky  # noqa: E402
from goveelights.layout import Layout, Run  # noqa: E402
from goveelights.legacy_defaults import LEGACY_DEFAULTS  # noqa: E402
from goveelights.scene import Scene  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OWNER_LAYOUT = ROOT / "layouts" / "house.json"
HAS_LEGACY = all(v is not None for v in LEGACY_DEFAULTS.values())
SHOWS = ["Santa's coming", "New Year's Eve countdown"]
ALL_IDS = [p["id"] for p in PresetStore(ROOT / "presets").list()]


def _tmp(test) -> Path:
    d = Path(tempfile.mkdtemp())
    test.addCleanup(shutil.rmtree, d, True)
    return d


class _Denver:
    def setUp(self):
        self._tz = os.environ.get("TZ")
        os.environ["TZ"] = "America/Denver"
        if hasattr(__import__("time"), "tzset"):
            __import__("time").tzset()

    def tearDown(self):
        if self._tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self._tz
        if hasattr(__import__("time"), "tzset"):
            __import__("time").tzset()


# ------------------------------------------------------------- defaults + migration
class NeutralDefaults(unittest.TestCase):
    def test_new_install_has_no_location_and_no_team(self):
        c = Config(_tmp(self) / "new.json")
        self.assertIsNone(c.data["schedule"]["lat"])
        self.assertIsNone(c.data["schedule"]["lon"])
        self.assertEqual(c.data["game"]["teams"], [])
        self.assertEqual(c.migrated, [])
        self.assertIsNone(sched.location(c.data["schedule"]))
        c.save()                                     # the first save stores "not set"
        again = Config(c.path)
        self.assertEqual(again.migrated, [])         # ...so it is never mistaken for an old file
        self.assertIsNone(again.data["schedule"]["lat"])
        self.assertEqual(again.data["game"]["teams"], [])

    def test_defaults_hold_no_location_or_team(self):
        self.assertIsNone(DEFAULTS["schedule"]["lat"])
        self.assertEqual(DEFAULTS["game"]["teams"], [])
        self.assertEqual(DEFAULTS["target_aliases"], {})

    def test_sunset_without_a_location_uses_the_on_time(self):
        sch = {"on": "sunset", "on_time": "17:45", "off_time": "23:00"}
        on, off = sched.window(date(2026, 12, 10), sch)
        self.assertEqual((on.hour, on.minute, off.hour), (17, 45, 23))
        self.assertEqual(tonight.on_reason(date(2026, 12, 10), sch), "fixed time: set your location for sunset")
        m = sched.morning_window(date(2026, 12, 10), {**sch, "morning": {"enabled": True, "on_time": "06:00"}})
        self.assertEqual(m[1].hour, 7)               # "until sunrise" with no location: 7:00
        with_loc = sched.window(date(2026, 12, 10), {**sch, "lat": HOME[0], "lon": HOME[1]})[0]
        self.assertNotEqual((with_loc.hour, with_loc.minute), (17, 45))

    def test_location_parsing(self):
        self.assertEqual(sched.location({"lat": "40.5", "lon": -105}), (40.5, -105.0))
        for bad in ({}, {"lat": None, "lon": 1}, {"lat": "x", "lon": 1}, {"lat": 91, "lon": 0},
                    {"lat": True, "lon": 0}, {"lat": float("nan"), "lon": 0}, None):
            self.assertIsNone(sched.location(bad), bad)
        validate_schedule({"on": "sunset", "lat": None, "lon": None})
        with self.assertRaises(ValueError):
            validate_schedule({"on": "sunset", "lat": "abc", "lon": 5})
        with self.assertRaises(ValueError):
            validate_schedule({"on": "sunset", "lat": 40})

    def test_migration_fills_only_missing_keys(self):
        legacy = {"schedule.lat": 1.5, "schedule.lon": 2.5, "game.teams": [{"league": "nfl", "team": "X"}],
                  "nothing.here": None}
        raw = {"schedule": {"lat": 9.0}, "visitor": {}}
        self.assertEqual(migrate_legacy_defaults(raw, legacy), ["schedule.lon", "game.teams"])
        self.assertEqual(raw, {"schedule": {"lat": 9.0, "lon": 2.5}, "visitor": {},
                               "game": {"teams": [{"league": "nfl", "team": "X"}]}})
        self.assertEqual(migrate_legacy_defaults(raw, legacy), [])          # one time only
        raw2 = {"schedule": "junk"}
        migrate_legacy_defaults(raw2, legacy)                              # never crashes on odd files
        self.assertEqual(raw2["schedule"], "junk")
        legacy["game.teams"].append("changed")
        self.assertEqual(len(raw["game"]["teams"]), 1)                      # copied, not shared

    def test_neutral_legacy_module_migrates_nothing(self):
        neutral = {k: None for k in LEGACY_DEFAULTS}
        raw = {}
        self.assertEqual(migrate_legacy_defaults(raw, neutral), [])
        self.assertEqual(raw, {})


@unittest.skipUnless(HAS_LEGACY, "public copy: no earlier defaults to keep")
class ExistingConfigKeepsItsHouse(_Denver, unittest.TestCase):
    LAT, LON = LEGACY_DEFAULTS["schedule.lat"], LEGACY_DEFAULTS["schedule.lon"]

    def old_window(self, d, sch):
        """What the previous version computed: the lat/lon it fell back to."""
        return sched.window(d, {**sch, "lat": self.LAT, "lon": self.LON})

    def test_minimal_existing_config_keeps_the_old_schedule_times(self):
        p = _tmp(self) / "govee_config.json"
        p.write_text(json.dumps({"schedule": {"enabled": True, "on": "sunset", "on_offset_min": -10}}))
        c = Config(p)
        self.assertEqual(sorted(c.migrated), ["game.teams", "schedule.lat", "schedule.lon"])
        on_disk = json.loads(p.read_text())
        self.assertEqual((on_disk["schedule"]["lat"], on_disk["schedule"]["lon"]), (self.LAT, self.LON))
        self.assertEqual(on_disk["schedule"]["enabled"], True)            # nothing else rewritten
        self.assertEqual(set(on_disk), {"schedule", "game"})
        self.assertEqual(on_disk["game"], {"teams": LEGACY_DEFAULTS["game.teams"]})
        sch = c.data["schedule"]
        base = {k: v for k, v in sch.items() if k not in ("lat", "lon")}
        d = date(2026, 1, 1)
        for k in range(0, 366, 7):                  # a year of evenings, to the second
            day = d + timedelta(days=k)
            self.assertEqual([t.isoformat() for t in sched.window(day, sch)],
                             [t.isoformat() for t in self.old_window(day, base)], day)
        self.assertEqual(Config(p).migrated, [])    # written once

    def test_an_empty_existing_file_follows_the_old_team_too(self):
        p = _tmp(self) / "govee_config.json"
        p.write_text("{}")
        c = Config(p)
        self.assertEqual(c.data["game"]["teams"], LEGACY_DEFAULTS["game.teams"])
        ids = [r["name"] for r in cal.build(ALL_IDS, SHOWS, c.data["game"]["teams"])["rules"]]
        self.assertIn("Broncos Sundays", ids)

    def test_a_full_config_is_not_touched(self):
        p = _tmp(self) / "govee_config.json"
        c = Config(p)
        c.data["schedule"].update(lat=12.0, lon=34.0)
        c.data["game"]["teams"] = []
        c.save()
        before = p.read_bytes()
        again = Config(p)
        self.assertEqual(again.migrated, [])
        self.assertEqual(p.read_bytes(), before)
        self.assertEqual(again.data["game"]["teams"], [])        # "no team" stays no team

    def test_read_only_file_still_loads_with_the_old_values(self):
        p = _tmp(self) / "govee_config.json"
        p.write_text("{}")
        with mock.patch("goveelights.config.write_json", side_effect=PermissionError("locked")):
            c = Config(p)
        self.assertEqual(c.data["schedule"]["lat"], self.LAT)


# ------------------------------------------------------------- calendar team rule
class CalendarTeamRule(unittest.TestCase):
    def names(self, teams):
        c = cal.build(ALL_IDS, SHOWS, teams)
        return {r["name"]: r for r in c["rules"]}, {s["name"]: s["note"] for s in c["skipped"]}

    def test_no_team_no_game_day_rule(self):
        rules, skipped = self.names([])
        self.assertFalse([n for n in rules if "Sundays" in n])
        self.assertIn("follow an NFL team", skipped["Game-day Sundays"])

    def test_the_team_you_follow(self):
        rules, _ = self.names([{"league": "nba", "team": "LAL"}, {"league": "nfl", "team": "den"}])
        r = rules["Broncos Sundays"]
        self.assertEqual(r["program"], {"evolve": "broncos-game-day", "minutes": 6})
        self.assertEqual((r["from"], r["to"], r["days"]), ("labor-day+6", "thanksgiving-1", [6]))
        rules, skipped = self.names([{"league": "nfl", "team": "KC"}])
        self.assertNotIn("Broncos Sundays", rules)
        self.assertIn("game-day", skipped["KC Sundays"])          # no generic look installed
        rules, _ = cal.build(ALL_IDS + ["game-day"], SHOWS, [{"league": "nfl", "team": "KC"}])["rules"], None
        self.assertIn("KC Sundays", [r["name"] for r in rules])

    def test_junk_teams(self):
        rules, skipped = self.names(["junk", {"league": "nfl"}, {"league": "nfl", "team": "  "}, None])
        self.assertIn("Game-day Sundays", skipped)


# ------------------------------------------------------------- targets on any house
def _scene_frames(scene_dict, L, frames=12):
    sc = Scene.from_dict(copy.deepcopy(scene_dict))
    return [sc.render(k / 10, L) for k in range(frames)]


class TargetsWorkAnywhere(unittest.TestCase):
    def stranger(self, aliases=None):
        runs = [Run("a", "Roof", 60, geometry=[[0, 0, 0], [59, 59, 0]]),
                Run("b", "Gable", 40, geometry=[[0, 70, 0], [20, 80, 10], [39, 90, 0]])]
        return Layout(runs, [{"name": "Left half", "parts": [{"run": "Roof", "from": 0, "to": 29}]}], aliases)

    def test_present_targets_are_never_looked_up(self):
        L = house()
        for t in L.target_names() + ["all", ["Peak", "Eaves"], ["North wall", "Porch"]]:
            self.assertEqual(L.layer_indices(t), L.indices(t), t)
            self.assertEqual(L.missing(t), [])

    def test_roles_aliases_and_fallback(self):
        L = self.stranger()
        gable = L.indices("Gable")
        self.assertEqual(L.layer_indices("Peak"), gable)                  # peak -> a run named *gable*
        self.assertEqual(L.layer_indices("Whole front gable"), gable)
        self.assertEqual(L.layer_indices("Eaves"), L.indices("Roof"))     # roofline -> *roof*
        self.assertEqual(L.layer_indices("Porch"), list(range(L.n)))     # nothing porch-like: whole house
        self.assertEqual(L.layer_indices("Nonsense"), list(range(L.n)))
        self.assertEqual(L.layer_indices(["Peak", "Porch"]), gable)       # part of it found: just that
        self.assertEqual(L.indices("Peak"), [])                           # strict lookups unchanged
        self.assertEqual(L.missing(["Peak", "Gable", "all"]), ["Peak"])
        self.assertEqual(L.plays_on("Peak"), "Gable")
        self.assertEqual(L.plays_on("Porch"), "the whole house")

        L = self.stranger({"porch": "Left half", "Peak": ["Roof"], "*": "Gable", "loop": "loop2", "loop2": "loop"})
        self.assertEqual(L.layer_indices("Porch"), L.indices("Left half"))   # by role alias
        self.assertEqual(L.layer_indices("Garage"), L.indices("Left half"))  # garage is porch-like too
        self.assertEqual(L.layer_indices("Peak"), L.indices("Roof"))         # a name alias wins
        self.assertEqual(L.layer_indices("Nonsense"), gable)                 # "*"
        self.assertEqual(L.layer_indices("loop"), gable)                     # alias loops end
        self.assertEqual(L.plays_on("Nonsense"), "the Gable")

    def test_every_built_in_preset_lights_something_on_a_stranger_house(self):
        L = self.stranger()
        for pid in ("christmas-tree", "fill-the-peak", "broncos-game-day", "porch-fireworks", "gift-wrap"):
            d = json.loads((ROOT / "presets" / f"{pid}.json").read_text())
            frames = _scene_frames(d, L, 8)
            self.assertTrue(any(max(c) > 0.02 for f in frames for c in f), pid)

    def test_aliases_come_from_the_config(self):
        c = Config(_tmp(self) / "c.json")
        c.import_layout(ROOT / LAYOUT_FILE, create_missing=True)
        c.data["target_aliases"] = {"Tree": "Peak"}
        L = Layout.from_config(c)
        self.assertEqual(L.layer_indices("Tree"), L.indices("Peak"))

    def test_example_house_has_every_target_the_presets_use(self):
        """Before/after on the example layout (which has every target the presets use):
        layer_indices must give exactly what the old strict lookup gave."""
        L = house()
        for pid in ALL_IDS:
            d = json.loads((ROOT / "presets" / f"{pid}.json").read_text())
            if not isinstance(d.get("layers"), list):
                continue
            self.assertEqual([L.missing(x.get("target", "all")) for x in d["layers"]],
                             [[] for _ in d["layers"]], pid)

    def test_missing_targets_listing(self):
        class Ctl:
            pass
        ctl = Ctl()
        ctl.presets = PresetStore(ROOT / "presets")
        ctl.engine = Ctl()
        ctl.engine.layout = self.stranger()
        out = tonight.missing_targets(ctl)
        ids = {p["id"]: p["targets"] for p in out["presets"]}
        self.assertIn("Whole front gable", ids["christmas-tree"])
        self.assertEqual(out["targets"]["Whole front gable"], "Gable")
        self.assertEqual(out["targets"]["Porch"], "the whole house")
        self.assertIn("Gable", out["names"])
        json.dumps(out)
        ctl.engine.layout = house()
        self.assertEqual(tonight.missing_targets(ctl)["presets"], [])


FROZEN = 1796940000.0
# Frame hashes of every built-in preset on the owner's layout, recorded with the code
# before the open-source changes (40 frames at t = k/10, clock frozen at FROZEN and the
# date at 2026-12-10, TZ America/Denver, the house location published to the dusk sky).
GOLDEN = {
    "advent-calendar": "80c38134a1b3d63e", "apex-ripples": "45c811f2ec4b9762", "ball-drop-at-midnight": "b35d50bf09ab7fdb",
    "ball-drop-preview-loop": "4726ab3965e9a61d", "breathing-comets": "55c866657d25dcaf", "broncos-game-day": "4464c19356fec30b",
    "candy-cane": "12065aab5f091df5", "champagne-fizz": "c28cee59b6dec46d", "christmas-tree": "48bdb532ed9a9fe4",
    "christmas-twinkle": "2277a2031211d138", "comets": "dc5e84a72ec270f5", "diamond-glitter": "def4c8ac75428c33",
    "dusk-demo": "8092ba8f36d0972a", "dusk-to-night": "71ac91fec4beeaf4", "easter-pastels": "e908d0cd21767783",
    "ember-falls": "f3986d59d17a3ebf", "falling-waves": "1f955cb3b44aaeba", "festive-glitter": "d4c0b3aba48408a7",
    "fill-the-peak": "e39100c20bb25c8b", "fireworks": "83aa641b1b7982aa", "gift-wrap": "0d97a53998f902d4",
    "glitter-ball": "d1379fd3e4f61c4d", "golden-shimmer": "866c978f5f241539", "golden-tree": "0700c49c295cd069",
    "grand-finale": "b93a090cb44c008a", "halloween": "81aa637816c21e25", "halloween-haunted": "c6e71445e2205812",
    "halloween-pumpkin": "aa200cadf7f4f9e6", "heartbeat-from-the-apex": "b72b08c7b14826e6", "icicles": "acc10149538988ca",
    "icy-tree": "f82255c628936124", "july-4th-fireworks": "d6443d53c2049cb8", "july-4th-flag": "352f9ab71d7bd641",
    "lighthouse": "7dbcb66c74344131", "mirror-comets": "84d46c6e87664aa2", "music-pulse": "2aeed425ae2e8ee6",
    "northern-lights": "86546e9f6dd272b1", "paparazzi": "8eb42517655dba1a", "party-beat": "7d00ef1c888c3f86",
    "peak-fire": "452a63d53f9893ec", "plasma-party": "171cb64225791e3d", "porch-fireworks": "4a4f161eef8f437c",
    "rainbow-roofline": "8c62fb6176b74e04", "roller-coaster": "15c050995c56f2b6", "santas-sleigh": "f805c8ec7fbe9e43",
    "scanner-trails": "e3e9d3c9829f1cfa", "sequin-wave": "15d1bd30b8ddacb3", "snow-piling-up": "1e64a8f1252ffcc2",
    "snowy-evening": "4f017951bc58a372", "sparkler-tree": "aca2bfd4e73486ab", "spooky-tree": "b92a98a3645bd590",
    "spring-garden": "0846728047a61b59", "st-patricks": "73dca68fed9217b8", "starry-night": "483540bdeb7b3f4a",
    "starry-night-sleigh": "bd044c94a3adc6e1", "summer-evening": "4575d9be84675000", "summer-fireflies": "92dfe1ab07328710",
    "text-go-broncos": "27d73a56ecffbcb4", "text-happy-new-year": "1792a91a8f560a80", "text-merry-christmas": "89233f44b73906cf",
    "text-scoreboard": "f0e95fdf8e72fba1", "thanksgiving-harvest": "5f48e3f08efddd0d", "tree-lighting-ceremony": "2e58b83472fbf20f",
    "twin-coasters": "40681a32a8acca37", "valentines-glow": "91b7608c852602d3", "valentines-hearts": "331604c472d3ecb6",
    "warm-white": "e1ba6a0b3f40d0a7", "winter-elegance": "34c6cbd5c3efbb07", "winter-sparkle": "1f2989c4db1a7dd2",
}


class _FixedDT(_dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return _dt.datetime(2026, 12, 10, 18, 0, tzinfo=tz) if tz else _dt.datetime(2026, 12, 10, 18, 0)


@unittest.skipUnless(OWNER_LAYOUT.exists() and HAS_LEGACY, "the owner's layout is not in this copy")
@unittest.skipUnless(sys.platform.startswith("linux"), "golden frames were recorded on Linux")
class OwnerHouseRendersByteIdentical(_Denver, unittest.TestCase):
    def test_every_preset_matches_the_recorded_frames(self):
        data = json.loads(OWNER_LAYOUT.read_text(encoding="utf-8"))
        devs = data["devices"]
        runs = sorted([Run(mac=m, name=d["name"], count=d["pixel_count"], reverse=d.get("reversed", False),
                           hidden=set(d.get("hidden", [])), geometry=d.get("geometry", [])) for m, d in devs.items()],
                      key=lambda r: devs[r.mac].get("order", 0))
        L = Layout(runs, data.get("zones"))
        home, games = livedata.LIVE.get("home"), livedata.LIVE.get("games")
        self.addCleanup(livedata.LIVE.update, {"home": home, "games": games})
        livedata.LIVE["games"] = {}
        livedata.set_home((LEGACY_DEFAULTS["schedule.lat"], LEGACY_DEFAULTS["schedule.lon"]))
        dusk_depth._memo.__dict__.clear()
        got = {}
        with mock.patch("time.time", return_value=FROZEN), \
                mock.patch.object(night_sky, "_today", lambda: _dt.date(2026, 12, 10)), \
                mock.patch.object(dusk_depth, "datetime", _FixedDT):
            for pid in sorted(GOLDEN):
                sc = Scene.from_dict(json.loads((ROOT / "presets" / f"{pid}.json").read_text()))
                hh = hashlib.sha256()
                for k in range(40):
                    cols = sc.render(k / 10, L)
                    hh.update(bytes(min(255, max(0, int(c * 255 + .5))) for px in cols for c in px))
                got[pid] = hh.hexdigest()[:16]
        self.assertEqual({k: v for k, v in got.items() if v != GOLDEN[k]}, {})


class DuskFollowsTheHouse(unittest.TestCase):
    def setUp(self):
        home = livedata.LIVE.get("home")
        self.addCleanup(livedata.LIVE.__setitem__, "home", home)

    def test_home_and_guess(self):
        livedata.set_home(None)
        lat, lon = livedata.home()
        self.assertEqual(lat, 40.0)
        self.assertTrue(-180 <= lon <= 180)
        livedata.set_home((12.5, "33"))
        self.assertEqual(livedata.home(), (12.5, 33.0))
        livedata.set_home(("x", 1))
        self.assertIsNone(livedata.LIVE["home"])

    def test_default_params_use_the_house(self):
        from goveelights.effects import EFFECTS
        P = dict(EFFECTS["dusk"].resolve({"demo": 60}))
        self.assertEqual((P["lat"], P["lon"]), (0, 0))
        livedata.set_home((60.0, 20.0))
        a = dusk_depth.dusk_sky(5.0, P)["az"]
        b = dusk_depth.dusk_sky(5.0, {**P, "lat": 60.0, "lon": 20.0})["az"]
        self.assertEqual(a, b)
        livedata.set_home((-30.0, 150.0))
        self.assertNotEqual(dusk_depth.dusk_sky(5.0, P)["az"], a)


# ------------------------------------------------------------- layout import
class ImportOntoOtherBoxes(unittest.TestCase):
    EX = ROOT / LAYOUT_FILE

    def discovered(self, n):
        c = Config(_tmp(self) / "c.json")
        for k in range(n):
            c.upsert_discovered({"device": f"AA:BB:CC:DD:EE:FF:00:{k:02X}", "sku": "H805A", "ip": f"10.0.0.{k + 1}"})
        return c

    def test_example_maps_by_order_and_adds_no_devices(self):
        c = self.discovered(3)
        c.devices["AA:BB:CC:DD:EE:FF:00:00"]["pixel_count"] = 90          # your own count stays
        out = c.import_layout(self.EX)
        self.assertEqual(len(c.devices), 3)
        self.assertEqual([c.devices[m]["name"] for m in sorted(c.devices)], ["Eaves", "Peak", "Accent"])
        self.assertEqual(c.devices["AA:BB:CC:DD:EE:FF:00:00"]["pixel_count"], 90)
        self.assertEqual(c.devices["AA:BB:CC:DD:EE:FF:00:01"]["pixel_count"], 108)
        self.assertTrue(any("-> your box" in x for x in out), out)
        self.assertTrue(Layout.from_config(c).zones["Whole front gable"])

    def test_your_names_are_kept_and_zones_follow_them(self):
        c = self.discovered(3)
        c.devices["AA:BB:CC:DD:EE:FF:00:01"]["name"] = "Gable"
        c.import_layout(self.EX)
        self.assertEqual(c.devices["AA:BB:CC:DD:EE:FF:00:01"]["name"], "Gable")
        runs = {p["run"] for z in c.data["zones"] for p in z["parts"]}
        self.assertIn("Gable", runs)
        self.assertNotIn("Peak", runs)
        self.assertTrue(Layout.from_config(c).zones["Zigzag top half"])

    def test_same_names_match_first(self):
        c = self.discovered(3)
        c.devices["AA:BB:CC:DD:EE:FF:00:00"]["name"] = "Accent"
        c.import_layout(self.EX)
        self.assertEqual(c.devices["AA:BB:CC:DD:EE:FF:00:00"]["pixel_count"], 108)   # the Accent entry

    def test_fewer_boxes_skip_with_a_note(self):
        c = self.discovered(2)
        out = c.import_layout(self.EX)
        self.assertEqual(len(c.devices), 2)
        self.assertTrue(any(x.startswith("skipped layout run 'Accent'") for x in out), out)

    def test_nothing_discovered_adds_nothing_unless_asked(self):
        c = Config(_tmp(self) / "c.json")
        c.import_layout(self.EX)
        self.assertEqual(c.devices, {})
        c.import_layout(self.EX, create_missing=True)
        self.assertEqual(len(c.devices), 3)

    @unittest.skipUnless(OWNER_LAYOUT.exists(), "the owner's layout is not in this copy")
    def test_owner_layout_still_matches_its_own_boxes(self):
        lay = json.loads(OWNER_LAYOUT.read_text())["devices"]
        c = Config(_tmp(self) / "c.json")
        for k, mac in enumerate(lay):
            c.upsert_discovered({"device": mac, "sku": "H805A", "ip": f"10.0.0.{k + 1}"})
        out = c.import_layout(OWNER_LAYOUT)
        self.assertFalse([x for x in out if "->" in x or "skipped" in x])
        for mac, e in lay.items():
            self.assertEqual(c.devices[mac]["geometry"], e["geometry"])
            self.assertEqual(c.devices[mac]["name"], e["name"])


# ------------------------------------------------------------- first-run setup
class FirstRun(unittest.TestCase):
    def test_needed_until_boxes_and_location(self):
        c = Config(_tmp(self) / "c.json")
        st = firstrun.state(c)
        self.assertTrue(st["needed"])
        self.assertFalse(st["has_layout"])
        self.assertIsNone(st["location"])
        self.assertTrue(all(round(x["lat"], 2) == x["lat"] for x in st["cities"]))
        for k in range(2):
            c.upsert_discovered({"device": f"AA:BB:CC:DD:EE:FF:00:{k:02X}", "sku": "H805A", "ip": f"10.0.0.{k + 1}"})
        done = firstrun.straight_layout(c, [{"mac": "AA:BB:CC:DD:EE:FF:00:01", "name": "Roof", "pixel_count": 50},
                                            {"mac": "AA:BB:CC:DD:EE:FF:00:00", "name": "Tree", "pixel_count": 30}])
        self.assertEqual(done, ["Roof", "Tree"])
        L = Layout.from_config(Config(c.path))
        self.assertEqual([r.name for r in L.runs], ["Roof", "Tree"])
        self.assertEqual(L.n, 80)
        xs = [p.x for p in L.pixels]
        self.assertEqual(xs, sorted(xs))                         # side by side, no overlap
        self.assertTrue(firstrun.state(c)["needed"])             # still no location
        c.data["schedule"].update(lat=HOME[0], lon=HOME[1])
        self.assertFalse(firstrun.state(c)["needed"])
        self.assertEqual(firstrun.state(c)["location"], list(HOME))

    def test_a_mapped_box_keeps_its_map(self):
        c = Config(_tmp(self) / "c.json")
        c.upsert_discovered({"device": "AA:BB:CC:DD:EE:FF:00:00", "sku": "H805A", "ip": "10.0.0.1"})
        c.devices["AA:BB:CC:DD:EE:FF:00:00"]["geometry"] = [[0, 5, 5], [9, 9, 9]]
        firstrun.straight_layout(c, [{"mac": "AA:BB:CC:DD:EE:FF:00:00", "name": "A", "pixel_count": 10}])
        self.assertEqual(c.devices["AA:BB:CC:DD:EE:FF:00:00"]["geometry"], [[0, 5, 5], [9, 9, 9]])
        firstrun.straight_layout(c, [{"mac": "AA:BB:CC:DD:EE:FF:00:00", "name": "A", "pixel_count": 10,
                                      "straight": True}])
        self.assertEqual(c.devices["AA:BB:CC:DD:EE:FF:00:00"]["geometry"], [[0, 0.0, 0.0], [9, 9.0, 0.0]])

    def test_bad_input(self):
        c = Config(_tmp(self) / "c.json")
        c.upsert_discovered({"device": "AA:BB:CC:DD:EE:FF:00:00", "sku": "H805A", "ip": "10.0.0.1"})
        c.upsert_discovered({"device": "AA:BB:CC:DD:EE:FF:00:01", "sku": "H805A", "ip": "10.0.0.2"})
        for boxes in (None, [], [{"mac": "nope", "pixel_count": 5}], [{"mac": "AA:BB:CC:DD:EE:FF:00:00", "pixel_count": 0}],
                      [{"mac": "AA:BB:CC:DD:EE:FF:00:00", "pixel_count": 300}], ["x"],
                      [{"mac": "AA:BB:CC:DD:EE:FF:00:00", "name": "A", "pixel_count": 5},
                       {"mac": "AA:BB:CC:DD:EE:FF:00:01", "name": "a", "pixel_count": 5}]):
            with self.assertRaises((ValueError, TypeError), msg=boxes):
                firstrun.straight_layout(c, boxes)


# ------------------------------------------------------------- public export
def _load_export():
    spec = importlib.util.spec_from_file_location("export_public", ROOT / "tools" / "export_public.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ExportChecks(unittest.TestCase):
    def test_scan_finds_denylist_hits_and_mac_like_ids(self):
        ex = _load_export()
        d = _tmp(self)
        (d / "a.txt").write_text("hello Smallville and AA:BB:CC:DD:EE:FF:00:01 (fake)\n")
        (d / "b.html").write_text("<p>" + ":".join(["12", "34", "56", "78", "9A", "BC", "DE", "F0"]) + "</p>")   # built here: a literal id would trip the export check
        (d / "c.bin").write_bytes(b"\x00\x01smallville\x00")
        (d / "ok.py").write_text("x = 'H805A 12:30'  # a time\n")
        hits = ex.scan(d, ["Smallville"])
        where = sorted({h[0] for h in hits})
        self.assertEqual(where, ["a.txt", "b.html", "c.bin"])
        self.assertFalse([h for h in hits if h[0] == "a.txt" and "AA:BB" in h[2]])   # placeholders are fine

    def test_scrub_replaces_text(self):
        ex = _load_export()
        out = ex.scrub_text("near Smallville at 12.3456, -98.7654", [("Smallville", "Springfield"),
                                                                   ("12.3456", "40.71")])
        self.assertEqual(out, "near Springfield at 40.71, -98.7654")

    def test_neutral_legacy_module(self):
        ex = _load_export()
        ns = {}
        exec(ex.NEUTRAL_LEGACY, ns)                              # noqa: S102 - our own constant
        self.assertEqual(set(ns["LEGACY_DEFAULTS"]), set(LEGACY_DEFAULTS))
        self.assertTrue(all(v is None for v in ns["LEGACY_DEFAULTS"].values()))


if __name__ == "__main__":
    unittest.main()
