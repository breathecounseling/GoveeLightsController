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
        with mock.patch("goveelights.config.write_json", side_effect=PermissionError("locked")), \
                self.assertLogs("goveelights.config", "WARNING") as lg:
            c = Config(p)
        self.assertIn("could not save", lg.output[0])
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
        self.assertEqual(rules["KC Sundays"]["program"], {"evolve": "game-day", "minutes": 6})
        self.assertFalse([s for s in skipped if "Sundays" in s])

    def test_every_nfl_team_gets_its_sundays(self):
        for team in ("ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN", "DET", "GB",
                     "HOU", "IND", "JAX", "KC", "LV", "LAC", "LAR", "MIA", "MIN", "NE", "NO", "NYG",
                     "NYJ", "PHI", "PIT", "SF", "SEA", "TB", "TEN", "WSH"):
            rules, skipped = self.names([{"league": "nfl", "team": team}])
            hit = [r for n, r in rules.items() if n.endswith("Sundays")]
            self.assertEqual(len(hit), 1, (team, skipped))
            self.assertIn(hit[0]["program"]["evolve"], ALL_IDS)

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
    "advent-calendar": "07995c84a9e9205c", "apex-ripples": "45c811f2ec4b9762", "ball-drop-at-midnight": "b35d50bf09ab7fdb",
    "ball-drop-preview-loop": "e2029bc03a39544b", "breathing-comets": "55c866657d25dcaf", "broncos-game-day": "4464c19356fec30b",
    "candy-cane": "12065aab5f091df5", "champagne-fizz": "5c0539e4fdac60a4", "christmas-tree": "6b6361a2ba0a8fcd",
    "christmas-twinkle": "be037db674f1bdc7", "comets": "dc5e84a72ec270f5", "diamond-glitter": "def4c8ac75428c33",
    "dusk-demo": "8092ba8f36d0972a", "dusk-to-night": "71ac91fec4beeaf4", "easter-pastels": "9e87da358f989d2a",
    "ember-falls": "f3986d59d17a3ebf", "falling-waves": "1f955cb3b44aaeba", "festive-glitter": "d4c0b3aba48408a7",
    "fill-the-peak": "e39100c20bb25c8b", "fireworks": "83aa641b1b7982aa", "gift-wrap": "0d97a53998f902d4",
    "glitter-ball": "d1379fd3e4f61c4d", "golden-shimmer": "866c978f5f241539", "golden-tree": "cbe12e013d0af857",
    "grand-finale": "fd8767520e2af06c", "halloween": "81aa637816c21e25", "halloween-haunted": "c6e71445e2205812",
    "halloween-pumpkin": "aa200cadf7f4f9e6", "heartbeat-from-the-apex": "b72b08c7b14826e6", "icicles": "acc10149538988ca",
    "icy-tree": "ebfb0b7cc4baceab", "july-4th-fireworks": "d6443d53c2049cb8", "july-4th-flag": "352f9ab71d7bd641",
    "lighthouse": "7dbcb66c74344131", "mirror-comets": "84d46c6e87664aa2", "music-pulse": "2aeed425ae2e8ee6",
    "northern-lights": "86546e9f6dd272b1", "paparazzi": "8eb42517655dba1a", "party-beat": "7d00ef1c888c3f86",
    "peak-fire": "452a63d53f9893ec", "plasma-party": "171cb64225791e3d", "porch-fireworks": "ad9a571d6fa334be",
    "rainbow-roofline": "8c62fb6176b74e04", "roller-coaster": "15c050995c56f2b6", "santas-sleigh": "f805c8ec7fbe9e43",
    "scanner-trails": "e3e9d3c9829f1cfa", "sequin-wave": "15d1bd30b8ddacb3", "snow-piling-up": "52652126f5b507d7",
    "snowy-evening": "333df0b24dd242f7", "sparkler-tree": "9a11fd77e84b3c8c", "spooky-tree": "bfbbe865cf3774bd",
    "spring-garden": "0846728047a61b59", "st-patricks": "73dca68fed9217b8", "starry-night": "483540bdeb7b3f4a",
    "starry-night-sleigh": "bd044c94a3adc6e1", "summer-evening": "4575d9be84675000", "summer-fireflies": "92dfe1ab07328710",
    "text-go-broncos": "89ea5df916923cce", "text-happy-new-year": "766614aa409ffc9d", "text-merry-christmas": "9af456bed0e80bca",
    "text-scoreboard": "27498bdeb9bbb032", "thanksgiving-harvest": "5f48e3f08efddd0d", "tree-lighting-ceremony": "2e58b83472fbf20f",
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
        L = Layout.from_config(c)
        self.assertTrue(L.zones["Zigzag top half"])                       # Peak's zones fit (same count)
        self.assertNotIn("Whole front gable", L.zones)                    # Eaves' don't: 90 bulbs, not 96
        self.assertEqual(c.devices["AA:BB:CC:DD:EE:FF:00:00"]["geometry"], [])
        self.assertTrue(c.devices["AA:BB:CC:DD:EE:FF:00:01"]["geometry"])
        self.assertTrue(any("kept your box's shape" in x for x in out), out)

    def test_a_different_house_keeps_its_own_shape(self):
        """The example imported onto a two-run ranch (150 + 80 bulbs): no stranger's
        geometry or hidden bulbs, no zones for runs it hasn't got or named like its runs."""
        c = self.discovered(2)
        firstrun.straight_layout(c, [{"mac": "AA:BB:CC:DD:EE:FF:00:00", "name": "Front roof", "pixel_count": 150},
                                     {"mac": "AA:BB:CC:DD:EE:FF:00:01", "name": "Garage", "pixel_count": 80}])
        before = copy.deepcopy(c.devices)
        out = c.import_layout(self.EX)
        for m, d in c.devices.items():
            self.assertEqual((d["name"], d["pixel_count"], d["hidden"], d["geometry"]),
                             (before[m]["name"], before[m]["pixel_count"], before[m]["hidden"], before[m]["geometry"]))
        self.assertEqual(c.data["zones"], [])
        self.assertIn("skipped zone 'Garage' (one of your boxes has that name)", out)
        self.assertTrue(any(x.startswith("skipped zone 'Porch' (needs Accent") for x in out), out)

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

    def test_running_setup_again_keeps_what_it_was_not_given(self):
        c = Config(_tmp(self) / "c.json")
        for k in range(2):
            c.upsert_discovered({"device": f"AA:BB:CC:DD:EE:FF:00:{k:02X}", "sku": "H805A", "ip": f"10.0.0.{k + 1}"})
        a, b = "AA:BB:CC:DD:EE:FF:00:00", "AA:BB:CC:DD:EE:FF:00:01"
        c.devices[a].update(reversed=True, pixel_count=50)
        c.devices[b].update(enabled=False, pixel_count=30)
        firstrun.straight_layout(c, [{"mac": a, "name": "Roof", "pixel_count": 50},     # what the app sends
                                     {"mac": b, "name": "Tree", "pixel_count": 30}])
        self.assertIs(c.devices[a]["reversed"], True)
        self.assertIs(c.devices[b]["enabled"], False)
        firstrun.straight_layout(c, [{"mac": a, "name": "Roof", "pixel_count": 50, "reversed": False},
                                     {"mac": b, "name": "Tree", "pixel_count": 30, "enabled": True}])
        self.assertIs(c.devices[a]["reversed"], False)
        self.assertIs(c.devices[b]["enabled"], True)

    def test_the_straight_line_counts_as_not_placed(self):
        c = Config(_tmp(self) / "c.json")
        c.upsert_discovered({"device": "AA:BB:CC:DD:EE:FF:00:00", "sku": "H805A", "ip": "10.0.0.1"})
        c.upsert_discovered({"device": "AA:BB:CC:DD:EE:FF:00:01", "sku": "H805A", "ip": "10.0.0.2"})
        firstrun.straight_layout(c, [{"mac": "AA:BB:CC:DD:EE:FF:00:00", "name": "A", "pixel_count": 40},
                                     {"mac": "AA:BB:CC:DD:EE:FF:00:01", "name": "B", "pixel_count": 20}])
        for d in c.devices.values():
            self.assertTrue(firstrun.is_placeholder(d["geometry"], d["pixel_count"]))
        for m, d in json.loads((ROOT / LAYOUT_FILE).read_text())["devices"].items():
            self.assertFalse(firstrun.is_placeholder(d["geometry"], d["pixel_count"]), d["name"])
        for junk in (None, [], [[0, 0, 0]], [[0, 0, 0], [9, 9, 1]], [[0, 0, 0], [9, 5, 0]], "x", [[0, "a", 0], [1, 1, 0]]):
            self.assertFalse(firstrun.is_placeholder(junk), junk)

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


# ------------------------------------------------------------- review fixes
def _preset_targets():
    out = []
    for pid in ALL_IDS:
        d = json.loads((ROOT / "presets" / f"{pid}.json").read_text())
        for x in d.get("layers") or [] if isinstance(d.get("layers"), list) else []:
            t = x.get("target", "all")
            if t not in out:
                out.append(t)
    return out


class KnownNamesStayDark(unittest.TestCase):
    """A box switched off (or renamed away from the names its zones use) lights nothing,
    as before: only names the house has never heard of play on a stand-in."""
    def cfg(self):
        c = Config(_tmp(self) / "c.json")
        c.import_layout(ROOT / LAYOUT_FILE, create_missing=True)
        return c

    def test_switching_a_box_off_keeps_its_layers_dark(self):
        targets = _preset_targets()
        for off in (None, "Eaves", "Peak", "Accent"):
            c = self.cfg()
            for d in c.devices.values():
                if d["name"] == off:
                    d["enabled"] = False
            L = Layout.from_config(c)
            self.assertEqual([t for t in targets if L.layer_indices(t) != L.indices(t)], [], off)
            if off:
                self.assertEqual(L.layer_indices(off), [])
                self.assertIn("nothing", L.plays_on(off))
                self.assertEqual(L.missing(off), [off])          # Tonight still reports it

    def test_no_bulb_count_and_renamed_box_stay_dark(self):
        c = self.cfg()
        acc = next(m for m, d in c.devices.items() if d["name"] == "Accent")
        c.devices[acc]["pixel_count"] = None
        L = Layout.from_config(c)
        self.assertEqual(L.layer_indices("Accent"), [])
        self.assertEqual(L.layer_indices("Porch"), [])
        c = self.cfg()
        c.devices[acc]["name"] = "H805A 1A2B"                    # renamed: the zones lose their run
        L = Layout.from_config(c)
        self.assertEqual(L.layer_indices("Porch"), [])           # a zone the config knows
        self.assertEqual(L.layer_indices("Accent"), [])          # a run the zones name
        self.assertEqual(L.layer_indices(["Porch", "Peak"]), L.indices("Peak"))

    def test_unknown_names_still_find_a_stand_in(self):
        c = self.cfg()
        L = Layout.from_config(c)
        self.assertEqual(L.layer_indices("Tree"), L.indices("Peak"))
        self.assertEqual(L.layer_indices("Nonsense"), list(range(L.n)))


class TextOnAFlatHouse(unittest.TestCase):
    def ranch(self):
        return Layout([Run("a", "Front roof", 150, geometry=[[0, 0, 0], [149, 149, 0]]),
                       Run("b", "Garage", 80, geometry=[[0, 152, 0], [79, 231, 0]])])

    def test_text_presets_show_something(self):
        L = self.ranch()
        for pid in ("text-go-broncos", "text-happy-new-year", "text-merry-christmas", "text-scoreboard"):
            d = json.loads((ROOT / "presets" / f"{pid}.json").read_text())
            frames = _scene_frames(d, L, 30)
            mean = sum(max(c) for f in frames for c in f) / (len(frames) * L.n)
            self.assertGreater(mean, 0.1, pid)
            self.assertGreater(len({tuple(f) for f in frames}), 1, pid)     # it moves

    def test_a_single_text_layer_marquees_along_the_run(self):
        L = self.ranch()
        sc = Scene.from_dict({"layers": [{"effect": "text", "params": {"text": "HI", "speed": 4, "sparkle": 0}}]})
        f = sc.render(0.0, L)
        lit = [i for i, c in enumerate(f) if max(c) > 0.05]
        self.assertTrue(lit)
        self.assertLess(len(lit), L.n)                           # letters and gaps, not a flood


class TonightOnANewHouse(_Denver, unittest.TestCase):
    NOW = _dt.datetime(2026, 12, 10, 15, 0)

    def rig(self):
        from test_integration import SCH, Rig
        return Rig(self, self.NOW.astimezone(), {"schedule": SCH})

    def checks(self, r):
        return {c["id"]: c for c in tonight.build(r.ctl, self.NOW.astimezone())["checks"]}

    def test_setup_straight_line_is_not_mapped(self):
        r = self.rig()
        self.assertEqual(self.checks(r)["layout"]["level"], tonight.OK)
        boxes = [{"mac": m, "name": d["name"], "pixel_count": d["pixel_count"], "straight": True}
                 for m, d in sorted(r.ctl.cfg.devices.items(), key=lambda kv: kv[1].get("order", 0))]
        firstrun.straight_layout(r.ctl.cfg, boxes)
        r.ctl.build_engine()
        c = self.checks(r)["layout"]
        self.assertEqual((c["level"], c["jump"]), (tonight.WARN, "map"))

    def test_stand_ins_on_another_house_are_fine(self):
        r = self.rig()
        r.ctl.cfg.data["zones"] = []
        r.ctl.cfg.data["target_aliases"] = {"*": "Eaves", "porch": "Accent"}
        r.ctl.build_engine()
        with mock.patch.object(tonight, "_house_layout_file", lambda: False), \
                mock.patch("goveelights.layout._house_names", lambda cfg: []):
            r.ctl.build_engine()
            r.ctl._dark_cache = None
            c = self.checks(r)["targets"]
            self.assertEqual(c["level"], tonight.OK, c)
            self.assertEqual(c["jump"], "")
            r.ctl.cfg.data["target_aliases"] = {}
            r.ctl.build_engine()
            c = self.checks(r)["targets"]
            self.assertEqual(c["level"], tonight.WARN, c)        # some play on the whole house
            listed = c["detail"].split("They aim at ")[1].split(", which")[0].rstrip("…").split(", ")
            for t in listed:
                self.assertIn(f"{t} plays on", c["detail"])       # every name listed is explained


class CommandLine(unittest.TestCase):
    def test_config_before_or_after_the_command(self):
        from goveelights import cli
        for argv in (["--config", "X", "layout"], ["-v", "--config=X", "effects"], ["--config", "X", "play", "p"]):
            self.assertTrue(cli._leading_options_then_subcommand(argv), argv)
        for argv in (["--config", "X"], ["--port", "8080"], ["--config", "X", "--port", "1"]):
            self.assertFalse(cli._leading_options_then_subcommand(argv), argv)
        d = _tmp(self)
        ex = str(ROOT / LAYOUT_FILE)
        for k, argv in enumerate((["--config", str(d / "a.json"), "layout", "import", ex],
                                  ["layout", "--config", str(d / "b.json"), "import", ex])):
            with mock.patch("sys.stdout", new=__import__("io").StringIO()), mock.patch("logging.basicConfig"):
                cli.main(argv)
            self.assertTrue((d / ("ab"[k] + ".json")).exists(), argv)


class ExportBuiltinChecks(unittest.TestCase):
    def test_builtin_checks_come_from_the_owner_files(self):
        ex = _load_export()
        d = _tmp(self)
        (d / "goveelights").mkdir()
        (d / "layouts").mkdir()
        (d / "goveelights" / "legacy_defaults.py").write_text(
            "LEGACY_DEFAULTS = {'schedule.lat': 12.3456, 'schedule.lon': -98.7654, 'game.teams': None}\n")
        (d / "layouts" / "house.json").write_text(json.dumps({"devices": {"AA:BB:CC:DD:EE:FF:A1:B2": {}}}))
        pats = ex.builtin_checks(d)
        (d / "layouts" / "house.json").unlink()
        (d / "goveelights" / "legacy_defaults.py").unlink()
        for name, text, bad in (("a.txt", "near 12.34 and 98.76", True), ("b.txt", "at 12.35, -98.77", True),
                                ("c.txt", "12.3 and 98.7 and 112.345", False), ("d.txt", "box H805A FFA1B2", True),
                                ("e.txt", "id ends A1B2!", True), ("f.txt", "ends FF:A1:B2", True),
                                ("g.txt", "A1B2C3 is longer", False), ("h.txt", "net " + "192.168." + "14.7", True),
                                ("i.txt", "see " + "claude" + ".ai/code/x", True), ("j.txt", "192.168.1.4", False)):
            (d / name).write_text(text)
            self.assertEqual(bool(ex.scan(d, [], pats)), bad, text)
            (d / name).unlink()
        self.assertEqual(len(ex.builtin_checks(_tmp(self))), len(ex._FIXED))   # a public copy: fixed ones only

    def test_a_missing_denylist_stops_the_export(self):
        ex = _load_export()
        with self.assertRaises(SystemExit) as e:
            ex.build(_tmp(self) / "out", _tmp(self) / "nope.txt")
        self.assertIn("--no-denylist", str(e.exception))


if __name__ == "__main__":
    unittest.main()
