"""Round 13 review fixes: hand-typed "dead" / "rehung" / "calibration" values that aren't
the expected shape, leaving Match the runs while its start is still on the way, Undo after
re-mapping a re-hung section, the calendar update wording on the Tonight card, taking out
an added run's calibration, and the public export's checks."""
import copy
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from test_integration import Rig  # noqa: E402

from goveelights import addrun, bulbcheck, calibrate, shows_builtin  # noqa: E402
from goveelights import calendar_builtin as cal  # noqa: E402
from goveelights.config import DEFAULTS, Config  # noqa: E402
from goveelights.controller import PresetStore  # noqa: E402
from goveelights.layout import Layout, dead_bulbs  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


class _Denver:
    def setUp(self):
        self._tz = os.environ.get("TZ")
        os.environ["TZ"] = "America/Denver"
        if hasattr(time, "tzset"):
            time.tzset()

    def tearDown(self):
        if self._tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self._tz
        if hasattr(time, "tzset"):
            time.tzset()


def _cfg(test) -> Config:
    d = tempfile.mkdtemp()
    test.addCleanup(shutil.rmtree, d, True)
    c = Config(Path(d) / "govee_config.json")
    c.import_layout(ROOT / "layouts" / "examples" / "gable-house.json", create_missing=True)
    return c


# ================================================================ hand edits
class HandTypedMarks(unittest.TestCase):
    def test_dead_not_a_list(self):
        self.assertEqual(dead_bulbs({"pixel_count": 93, "dead": 5}), {5})         # one number: that bulb
        for v in (True, False, 2.5, None, {"a": 1}, 500):
            self.assertEqual(dead_bulbs({"pixel_count": 93, "dead": v}), set(), v)

    def test_dead_quoted_number_is_one_bulb(self):
        self.assertEqual(dead_bulbs({"pixel_count": 93, "dead": "12"}), {12})
        self.assertEqual(dead_bulbs({"pixel_count": 93, "dead": "x"}), set())
        self.assertEqual(dead_bulbs({"pixel_count": 93, "dead": [11, "56", True, "q"]}), {11, 56})

    def test_rehung_not_a_list(self):
        for v in (3, True, "3", "12-14", {"a": 1}, [3, 4]):
            self.assertEqual(bulbcheck.rehung_ranges({"pixel_count": 93, "rehung": v}), [], v)
        self.assertEqual(bulbcheck.rehung_ranges({"pixel_count": 93, "rehung": [[3, 4], "ab", 7]}), [[3, 4]])

    def test_layout_and_bulb_check_survive(self):
        c = _cfg(self)
        for d in c.devices.values():
            d["dead"], d["rehung"] = 5, 3
        L = Layout.from_config(c)
        self.assertTrue(L.pixels)
        for d in c.devices.values():
            d["dead"] = True
        Layout.from_config(c)

    def test_one_bad_gain_keeps_the_rest(self):
        c = calibrate.read({"calibration": {"brightness": 0.8, "gain": [1, 0.9, "x"], "fade": 0.2}})
        self.assertEqual(c["brightness"], 0.8)
        self.assertEqual(c["fade"], 0.2)
        self.assertEqual(c["gain"], [1.0, 1.0, 1.0])
        c = calibrate.read({"calibration": {"brightness": "x", "gain": [1, 0.9, 0.8]}})
        self.assertEqual((c["brightness"], c["gain"]), (1.0, [1.0, 0.9, 0.8]))


# ================================================================ undo a re-map
class UndoBringsBackTheRehungMark(_Denver, unittest.TestCase):
    def test_undo(self):
        r = Rig(self, datetime(2026, 11, 26, 12, 0).astimezone(), {"schedule": {"enabled": False}})
        ctl = r.ctl
        L = ctl.engine.layout
        m = L.run("Eaves").mac
        ctl.bulbcheck.mark_rehung(m, 40, 49, True)
        geo0 = copy.deepcopy(ctl.cfg.devices[m].get("geometry") or [])
        pts = [[p.i, 100 + p.x * 12.0 + (37 if p.run == "Eaves" and 40 <= p.local <= 49 else 0), 500 - p.y * 12.0, 1.0]
               for p in L.pixels]
        ctl.map_apply(pts, section={"mac": m, "from": 40, "to": 49})
        self.assertEqual(bulbcheck.rehung_ranges(ctl.cfg.devices[m]), [])
        ctl.map_undo()
        self.assertEqual(ctl.cfg.devices[m].get("geometry"), geo0)
        self.assertEqual(bulbcheck.rehung_ranges(ctl.cfg.devices[m]), [[40, 49]])
        self.assertNotIn("rehung_prev", ctl.cfg.devices[m])
        # a later apply doesn't bring back an old undo's mark
        ctl.map_apply(pts, section={"mac": m, "from": 40, "to": 49})
        ctl.map_apply(pts)
        ctl.map_undo()
        self.assertEqual(bulbcheck.rehung_ranges(ctl.cfg.devices[m]), [])


# ================================================================ take out a run
class TakeOutRemovesItsCalibration(unittest.TestCase):
    def test_take_out(self):
        c = _cfg(self)
        new = "AA:BB:CC:DD:EE:FF:01:04"
        c.upsert_discovered({"device": new, "sku": "H705E", "ip": "10.0.0.44", "source_ip": "10.0.0.44"})
        before = copy.deepcopy(c.data)
        addrun.add_run(c, Layout.from_config(c), new, 60, "garage")
        calibrate.write(c.device(new), {"brightness": 0.7, "gain": [1, 0.9, 0.8], "fade": 0.3})
        c.device(new)["trim"] = [1, 1, 0.9]
        addrun.remove_run(c, new)
        self.assertEqual(c.data, before)
        addrun.add_run(c, Layout.from_config(c), new, 30, "other")
        self.assertEqual(calibrate.read(c.device(new))["brightness"], 1.0)


# ================================================================ calendar wording
def _older_install(today=date(2026, 11, 26)):
    """The calendar as round 11 installed it (no Peak pictures), stamped."""
    ids = [p["id"] for p in PresetStore(ROOT / "presets").list()]
    old = [i for i in ids if not i.startswith("peak-")]
    c = cal.build(old, [s for s in shows_builtin.BUILTIN_SHOWS if s != "Peak pictures"],
                  [{"league": "nfl", "team": "DEN"}], today=today)
    sch = {"enabled": True, "rules": copy.deepcopy(DEFAULTS["schedule"]["rules"]), "dusk_intro_minutes": 10,
           "on_offset_min": -10}
    sch, pls, sts = cal.merge(c, sch, copy.deepcopy(DEFAULTS["playlists"]), [], old, [0, 1])
    return {"schedule": sch, "playlists": pls, "showtimes": sts}


class CalendarUpdateWording(_Denver, unittest.TestCase):
    def check(self, when):
        r = Rig(self, when.astimezone(), _older_install())
        plan = r.ctl.tonight(password_set=True)
        return plan, next(c for c in plan["checks"] if c["id"] == "calendar")

    def test_thanksgiving_says_tonight_is_unchanged(self):
        plan, ck = self.check(datetime(2026, 11, 26, 14, 0))
        self.assertEqual(plan["program_name"], "Thanksgiving rotation")
        d = ck["detail"]
        self.assertNotIn("Holiday:", d)
        self.assertNotIn("New tonight", d)
        self.assertIn("Tonight's show is unchanged", d)
        self.assertIn("Christmas rotation from Fri Nov 27", d)
        i = [d.index(x) for x in ("Christmas rotation", "Advent rotation", "Christmas Day rotation")]
        self.assertEqual(i, sorted(i), d)                     # calendar order

    def test_advent_night_says_tonights_rotation_changes(self):
        plan, ck = self.check(datetime(2026, 12, 10, 14, 0))
        self.assertEqual(plan["program_name"], "Advent rotation")
        d = ck["detail"]
        self.assertIn("New tonight", d)
        self.assertIn("Advent rotation", d.split("New tonight", 1)[1].split(".")[0])
        self.assertNotIn("Tonight's show is unchanged", d)
        self.assertIn("Also updated: Christmas Day rotation from Fri Dec 25", d)
        self.assertNotIn("Holiday:", d)


# ================================================================ public export
def _export():
    spec = importlib.util.spec_from_file_location("export_public_r13", ROOT / "tools" / "export_public.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class PublicExport(unittest.TestCase):
    def test_bom_house_json_keeps_the_fragment_checks(self):
        ex = _export()
        mac = ":".join(["12", "34", "56", "78", "9A", "BC", "DE", "F0"])         # built here: no id in this file
        house = {"devices": {mac: {"name": "Eaves"}}}
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        (d / "layouts").mkdir()
        (d / "layouts" / "house.json").write_text(json.dumps(house), encoding="utf-8")
        plain = ex.builtin_checks(d)
        (d / "layouts" / "house.json").write_bytes(b"\xef\xbb\xbf" + json.dumps(house).encode("utf-8"))
        self.assertEqual(ex.builtin_checks(d), plain)
        self.assertTrue(any("DEF0" in p for p in plain))
        (d / "layouts" / "house.json").write_text("{not json", encoding="utf-8")
        with self.assertRaises(SystemExit):
            ex.builtin_checks(d)

    def test_tracked_tests_have_no_device_ids(self):
        ex = _export()
        hits = [h for h in ex.scan(ROOT / "tests", []) if h[2].startswith("device id")
                and "__pycache__" not in h[0]]                  # (the export copies tracked files only)
        self.assertEqual(hits, [])


# ================================================================ Match the runs: leaving early
def _node_path():
    import subprocess
    if not shutil.which("node") or not shutil.which("npm"):
        return None
    try:
        root = subprocess.run(["npm", "root", "-g"], capture_output=True, text=True, timeout=20).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return root if root and (Path(root) / "playwright").exists() else None


NODE_PATH = _node_path()


@unittest.skipUnless(NODE_PATH, "node + Playwright not installed")
class LeavingMoreWhileTheTestStarts(_Denver, unittest.TestCase):
    def test_leave_before_the_reply(self):
        import subprocess
        from goveelights.app_server import Server, make_handler
        from goveelights.auth import Auth
        r = Rig(self, datetime(2026, 11, 26, 14, 0).astimezone(), {"schedule": {"enabled": False}})
        r.ctl.play_preset("warm-white")
        r.ctl.set_live(True)
        srv = Server(("127.0.0.1", 0), make_handler(r.ctl, lambda: None, Auth(Path(r.tmp) / "auth.json")))
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        res = subprocess.run(["node", str(ROOT / "tests" / "ui_match_leave.js"), f"http://127.0.0.1:{srv.server_address[1]}/"],
                             capture_output=True, text=True, timeout=120, env={**os.environ, "NODE_PATH": NODE_PATH})
        self.assertEqual(res.returncode, 0, res.stderr)
        out = json.loads(res.stdout.strip().splitlines()[-1])
        self.assertEqual(out["errors"], [], out)
        self.assertEqual(out["during"], {"color": "red"}, out)
        self.assertIsNone(out["after"], out)


if __name__ == "__main__":
    unittest.main()
