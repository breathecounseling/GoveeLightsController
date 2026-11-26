"""Upgrading an older install: git pull must not trip over your presets, zones added to
the house layout later must reach old configs, and old leftovers must be handled."""
import copy
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
from test_integration import SCH, Rig  # noqa: E402

from goveelights import controller as C  # noqa: E402
from goveelights import tonight  # noqa: E402
from goveelights.config import Config  # noqa: E402
from goveelights.controller import PresetStore, migrate_presets  # noqa: E402
from goveelights.layout import Layout  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HOUSE = ROOT / "layouts" / "examples" / "gable-house.json"
SCENE = {"name": "Mine", "layers": [{"effect": "solid", "params": {"color": "#ff0000"}}]}
MACS = list(json.loads(HOUSE.read_text())["devices"])


def _tmp(test) -> Path:
    d = Path(tempfile.mkdtemp())
    test.addCleanup(shutil.rmtree, d, True)
    return d


def _split(test):
    d = _tmp(test)
    (d / "presets").mkdir()
    for pid in ("icicles", "candy-cane"):
        shutil.copy(ROOT / "presets" / f"{pid}.json", d / "presets")
    return d, PresetStore(d / "presets", d / "user_presets")


class SplitPresetStoreTests(unittest.TestCase):
    def test_favourite_on_a_builtin_leaves_the_tracked_file_alone(self):
        d, st = _split(self)
        before = (d / "presets" / "candy-cane.json").read_bytes()
        st.set_meta("candy-cane", favorite=True, tags=["Mine"])
        self.assertEqual((d / "presets" / "candy-cane.json").read_bytes(), before)
        row = next(p for p in st.list() if p["id"] == "candy-cane")
        self.assertTrue(row["favorite"])
        self.assertEqual(row["tags"], ["Mine"])
        self.assertTrue(st.get("candy-cane")["favorite"])

    def test_save_on_a_builtin_writes_your_copy(self):
        d, st = _split(self)
        before = (d / "presets" / "candy-cane.json").read_bytes()
        scene = st.get("candy-cane")
        scene["brightness"] = 0.5
        self.assertEqual(st.save(scene, "candy-cane"), "candy-cane")
        self.assertEqual((d / "presets" / "candy-cane.json").read_bytes(), before)
        self.assertEqual(st.get("candy-cane")["brightness"], 0.5)
        self.assertEqual([p["id"] for p in st.list()].count("candy-cane"), 1)
        st.delete("candy-cane")                      # back to the built-in
        self.assertNotEqual(st.get("candy-cane").get("brightness"), 0.5)
        with self.assertRaises(ValueError):
            st.delete("candy-cane")                  # a built-in itself can't be deleted
        self.assertTrue((d / "presets" / "candy-cane.json").exists())

    def test_a_new_name_clashing_with_a_builtin_gets_its_own_id(self):
        d, st = _split(self)
        pid = st.save({**SCENE, "name": "Icicles"})
        self.assertEqual(pid, "icicles-2")
        self.assertFalse((d / "presets" / "icicles-2.json").exists())
        self.assertTrue((d / "user_presets" / "icicles-2.json").exists())
        self.assertEqual(st.save(SCENE), "mine")
        self.assertEqual(sorted(f.name for f in (d / "presets").iterdir()), ["candy-cane.json", "icicles.json"])

    def test_default_store_keeps_user_presets_outside_the_tracked_folder(self):
        st = PresetStore(C.PRESETS_DIR)
        self.assertNotEqual(st.user.resolve(), C.PRESETS_DIR.resolve())
        self.assertEqual(st.user.name, "user_presets")
        r = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", "user_presets/x.json"])
        self.assertEqual(r.returncode, 0)

    def test_saves_are_atomic_and_unreadable_files_are_reported(self):
        d = _tmp(self)
        st = PresetStore(d)
        st.save(SCENE)
        good = (d / "mine.json").read_text()
        with mock.patch("goveelights.config.json.dump", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                st.save({**SCENE, "brightness": 0.3}, "mine")
            with self.assertRaises(OSError):
                st.set_meta("mine", favorite=True)
        self.assertEqual((d / "mine.json").read_text(), good)
        self.assertEqual([f.name for f in d.iterdir()], ["mine.json"])     # no temp file left
        (d / "half.json").write_text('{"name": "Half", "lay')
        with self.assertLogs("goveelights.controller", logging.WARNING):
            self.assertEqual([p["id"] for p in st.list()], ["mine"])
        self.assertEqual(st.broken, ["half.json"])


@unittest.skipUnless(shutil.which("git"), "needs git")
class MigrationTests(unittest.TestCase):
    def git(self, root, *a):
        return subprocess.run(["git", "-C", str(root), *a], check=True, capture_output=True, text=True).stdout

    def test_moves_your_presets_out_so_git_pull_can_update(self):
        root = _tmp(self)
        self.git(root, "init", "-q")
        self.git(root, "config", "user.email", "t@example.com")
        self.git(root, "config", "user.name", "t")
        (root / "presets").mkdir()
        for pid in ("icicles", "candy-cane", "fireworks", "comets"):
            shutil.copy(ROOT / "presets" / f"{pid}.json", root / "presets")
        self.git(root, "add", "presets")
        self.git(root, "commit", "-qm", "built-ins")
        P = root / "presets"
        # what older versions did: favourite written into the built-in, an edit saved over
        # one, a deleted built-in, and your own preset (one sharing a later built-in's name)
        cc = json.loads((P / "candy-cane.json").read_text())
        (P / "candy-cane.json").write_text(json.dumps({**cc, "favorite": True}, indent=2))
        fw = json.loads((P / "fireworks.json").read_text())
        (P / "fireworks.json").write_text(json.dumps({**fw, "brightness": 0.25}, indent=2))
        (P / "comets.json").unlink()
        (P / "my-show.json").write_text(json.dumps(SCENE))
        st = PresetStore(P, root / "user_presets")
        moved = migrate_presets(st, root)
        self.assertEqual(sorted(moved), ["candy-cane", "comets", "fireworks", "my-show"])
        self.assertEqual(self.git(root, "status", "--porcelain", "--", "presets"), "")    # pull won't refuse
        self.assertEqual(sorted(f.name for f in (root / "user_presets").glob("*.json")),
                         ["_meta.json", "fireworks.json", "my-show.json"])
        rows = {p["id"]: p for p in st.list()}
        self.assertTrue(rows["candy-cane"]["favorite"])
        self.assertEqual(st.get("fireworks")["brightness"], 0.25)
        self.assertEqual(st.get("my-show")["name"], "Mine")
        self.assertIn("comets", rows)
        self.assertEqual(migrate_presets(st, root), [])                                     # one time only

    def test_nothing_to_do_outside_git_or_with_one_folder(self):
        d, st = _split(self)
        (d / "presets" / "mine.json").write_text(json.dumps(SCENE))
        self.assertEqual(migrate_presets(st, d), [])
        self.assertTrue((d / "presets" / "mine.json").exists())
        self.assertEqual(migrate_presets(PresetStore(d / "presets"), d), [])


class HouseLayoutMergeTests(unittest.TestCase):
    def cfg(self, zones=None, names=None):
        c = Config(_tmp(self) / "c.json")
        c.import_layout(HOUSE, create_missing=True)
        if zones is not None:
            c.data["zones"] = zones
        for mac, nm in (names or {}).items():
            c.devices[mac]["name"] = nm
        c.save()
        return Config(c.path)

    def accent_lit(self, c, pid="christmas-tree"):
        L = Layout.from_config(c)
        scene = json.loads((ROOT / "presets" / f"{pid}.json").read_text())
        return [L.indices(l.get("target", "all")) for l in scene["layers"]]

    def test_zones_added_since_an_old_import_are_merged(self):
        house = json.loads(HOUSE.read_text())["zones"]
        old = [z for z in house if z["name"] not in ("Garage", "Garage peak", "Porch", "Lower roofline",
                                                     "Whole front", "Both peaks")]
        mine = {"name": "North wall", "parts": [{"run": "Eaves", "from": 0, "to": 3}]}
        c = self.cfg([mine] + old[1:])
        self.assertIn([], self.accent_lit(c))            # before: a layer lights nothing
        self.assertTrue(c.merge_house_layout(HOUSE))
        names = [z["name"] for z in c.data["zones"]]
        self.assertEqual(sorted(names), sorted(z["name"] for z in house))
        self.assertEqual(c.data["zones"][0], mine)       # yours kept, never overwritten
        self.assertNotIn([], self.accent_lit(c))
        self.assertNotIn([], self.accent_lit(Config(c.path)))      # saved
        self.assertEqual(c.merge_house_layout(HOUSE), [])          # idempotent

    def test_no_zones_at_all(self):
        c = self.cfg([])
        c.merge_house_layout(HOUSE)
        for pid in ("christmas-tree", "gift-wrap", "advent-calendar", "apex-ripples"):
            self.assertNotIn([], self.accent_lit(c, pid), pid)

    def test_renamed_boxes_get_the_zones_under_their_own_names(self):
        c = self.cfg([], {MACS[2]: "Garage box"})
        c.merge_house_layout(HOUSE)
        porch = next(z for z in c.data["zones"] if z["name"] == "Porch")
        self.assertEqual({p["run"] for p in porch["parts"]}, {"Garage box"})
        self.assertTrue(Layout.from_config(c).zones["Porch"])
        self.assertEqual(c.devices[MACS[2]]["name"], "Garage box")

    def test_other_boxes_are_left_alone(self):
        c = Config(_tmp(self) / "c.json")
        c.upsert_discovered({"device": "AA:BB:CC:DD:EE:FF:00:11", "sku": "H805A", "ip": "10.0.0.9"})
        self.assertEqual(c.merge_house_layout(HOUSE), [])
        self.assertEqual(c.data["zones"], [])

    def test_auto_named_boxes_take_the_layout_names(self):
        for how in ("import", "merge"):
            c = Config(_tmp(self) / "c.json")
            for k, mac in enumerate(MACS):              # Phase 1 discovery first
                c.upsert_discovered({"device": mac, "sku": "H805A", "ip": f"10.0.0.{k + 1}"})
            self.assertEqual(c.devices[MACS[0]]["name"], "H805A " + MACS[0].replace(":", "")[-4:])
            if how == "import":
                c.import_layout(HOUSE)
            else:
                c.data["zones"] = []
                c.merge_house_layout(HOUSE)
            self.assertEqual([c.devices[m]["name"] for m in MACS], ["Eaves", "Peak", "Accent"], how)
            c.devices[MACS[0]]["name"] = "Roof"           # a name you chose stays
            c.import_layout(HOUSE)
            self.assertEqual(c.devices[MACS[0]]["name"], "Roof")


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


class TonightUpgradeChecks(_Denver, unittest.TestCase):
    NOW = datetime(2026, 12, 10, 15, 0)

    def checks(self, r):
        return {c["id"]: c for c in tonight.build(r.ctl, self.NOW.astimezone())["checks"]}

    def test_presets_aimed_at_missing_zones_are_reported(self):
        sch = {**SCH, "default": {"preset": "christmas-tree"}}
        r = Rig(self, self.NOW.astimezone(), {"schedule": sch})
        self.assertNotIn("targets", self.checks(r))          # the shipped layout: every built-in lights
        r.ctl.cfg.data["zones"] = []
        r.ctl.build_engine()
        c = self.checks(r)["targets"]
        self.assertEqual(c["level"], tonight.FAIL)
        self.assertIn("Christmas tree", c["title"])
        self.assertIn("Garage", c["detail"])
        self.assertEqual(c["jump"], "house")
        self.assertTrue(r.ctl.cfg.merge_house_layout(HOUSE))  # what the one-tap fix does
        r.ctl.build_engine()
        self.assertNotIn("targets", self.checks(r))

    def test_zone_parts_naming_a_missing_box_are_reported(self):
        r = Rig(self, self.NOW.astimezone(), {"schedule": SCH})
        r.ctl.cfg.devices[MACS[2]]["name"] = "H805A " + MACS[2].replace(":", "")[-4:]
        r.ctl.build_engine()
        c = self.checks(r)
        self.assertIn("Accent", c["zones"]["detail"])
        self.assertEqual(c["targets"]["level"], tonight.WARN)

    def test_stretched_camera_map_is_flagged(self):
        r = Rig(self, self.NOW.astimezone(), {"schedule": SCH})
        self.assertNotIn("geometry", self.checks(r))
        d = r.ctl.cfg.devices[MACS[1]]
        d["geometry"] = [[i, x * 3, y * 3] for i, x, y in d["geometry"]]
        r.ctl.build_engine()
        c = self.checks(r)["geometry"]
        self.assertEqual(c["level"], tonight.WARN)
        self.assertIn("Peak", c["detail"])


class OldConfigLeftovers(unittest.TestCase):
    def test_visitor_only_when_on_is_dropped(self):
        p = _tmp(self) / "c.json"
        p.write_text(json.dumps({"visitor": {"enabled": True, "only_when_on": False, "seconds": 30}}))
        with self.assertLogs("goveelights.config", logging.INFO) as lg:
            c = Config(p)
        self.assertNotIn("only_when_on", c.data["visitor"])
        self.assertEqual(c.data["visitor"]["seconds"], 30)
        self.assertIn("only_when_on", lg.output[-1])
        p.write_text(json.dumps({"visitor": {"only_when_on": True}}))
        self.assertNotIn("only_when_on", Config(p).data["visitor"])

    def test_atomic_save_temp_files_are_gitignored(self):
        if not shutil.which("git"):
            self.skipTest("needs git")
        for name in ("govee_config.json.k3j_x9a.tmp", "govee_auth.json.ab12cd.tmp"):
            r = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", name])
            self.assertEqual(r.returncode, 0, name)


if __name__ == "__main__":
    unittest.main()
