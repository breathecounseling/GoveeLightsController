"""Round 11: the upgrade from an install made before rounds 8-10 (pre-flight for a holiday
evening). A holiday calendar installed by an older version has no showtimes for the new
shows and the old Thanksgiving program: the Tonight checklist says so, names what is new
tonight, and one tap (the calendar sheet's Update) brings it up to date. Nothing changes
on its own."""
import copy
import os
import sys
import time
import unittest
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from test_integration import SCH, Rig  # noqa: E402

from goveelights import calendar_builtin as cal  # noqa: E402
from goveelights import shows_builtin, tonight  # noqa: E402
from goveelights.config import DEFAULTS  # noqa: E402
from goveelights.controller import PresetStore  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
IDS = [p["id"] for p in PresetStore(ROOT / "presets").list()]
SHOWS = list(shows_builtin.BUILTIN_SHOWS)
TEAMS = [{"league": "nfl", "team": "DEN"}]
NEW_SHOWS = ("Thanksgiving tonight", "Christmas on the roofline")


def old_install(today=date(2026, 11, 20)):
    """Schedule, playlists and showtimes as the calendar of an older version left them:
    no season showtimes, Thanksgiving an evolving look, the older Christmas rotation."""
    c = cal.build(IDS, SHOWS, TEAMS, today=today)
    c["showtimes"] = [s for s in c["showtimes"] if s["show"] not in NEW_SHOWS]
    for r in c["rules"]:
        if r["name"] == "Thanksgiving":
            r["program"] = {"evolve": "thanksgiving-harvest"}
    c["playlists"].pop("Holiday: Thanksgiving", None)
    xm = c["playlists"]["Holiday: Christmas"]
    xm["items"] = [i for i in xm["items"] if i["preset"] not in ("sleigh-grand-tour", "candy-cane-chase", "falling-star")]
    rules = copy.deepcopy(DEFAULTS["schedule"]["rules"])
    sch = {**SCH, "dusk_intro_minutes": 10, "on_offset_min": -10, "rules": rules}
    sch, pls, sts = cal.merge(c, sch, copy.deepcopy(DEFAULTS["playlists"]),
                              [{"show": "Family show", "from": "19:30", "to": "19:30", "every": 60}], IDS, [0, 1])
    sch.pop(cal.STAMP, None)              # older versions kept no record of what they installed
    return sch, pls, sts


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


class CalendarChanges(_Denver, unittest.TestCase):
    def test_an_older_calendar_is_out_of_date(self):
        sch, pls, sts = old_install()
        fresh = cal.build(IDS, SHOWS, TEAMS, today=date(2026, 11, 26))
        ch = cal.changes(fresh, sch, pls, sts, IDS)
        self.assertEqual(sorted(s["show"] for s in ch["showtimes"]), sorted(NEW_SHOWS))
        self.assertIn("Thanksgiving", ch["rules"])
        self.assertIn("Holiday: Christmas", ch["playlists"])
        self.assertTrue(ch["outdated"])
        # after the update there is nothing left to do
        sch2, pls2, sts2 = cal.merge(fresh, sch, pls, sts, IDS)
        ch2 = cal.changes(fresh, sch2, pls2, sts2, IDS)
        self.assertFalse(ch2["outdated"], ch2)

    def test_the_owners_own_choices_are_not_an_update(self):
        """Switching a calendar showtime or rule off, or the season's dates moving on,
        must not nag: only what the calendar would add or change counts."""
        fresh = cal.build(IDS, SHOWS, TEAMS, today=date(2026, 11, 26))
        sch, pls, sts = cal.merge(fresh, {**SCH, "rules": []}, {}, [], IDS)
        for st in sts:
            if st.get("show") == "Christmas on the roofline":
                st["enabled"] = False
                st["dates"] = st["dates"][3:]
        for r in sch["rules"]:
            if r.get("name") == "Advent":
                r["enabled"] = False
        self.assertFalse(cal.changes(fresh, sch, pls, sts, IDS)["outdated"])

    def test_the_owners_edits_after_an_update_are_not_an_update(self):
        """Once this version has installed the calendar, deleting one of its showtimes or
        rules, trimming a rotation or giving a holiday another look is the owner's choice:
        only what a newer calendar adds or changes since then counts."""
        fresh = cal.build(IDS, SHOWS, TEAMS, today=date(2026, 11, 26))
        sch, pls, sts = old_install()
        sch, pls, sts = cal.merge(fresh, sch, pls, sts, IDS)          # Update tapped
        sts = [s for s in sts if s.get("show") != "Christmas on the roofline"]
        sch["rules"] = [r for r in sch["rules"] if r.get("name") != "Valentine's week"]
        for r in sch["rules"]:
            if r.get("name") == "Thanksgiving":
                r["program"] = {"preset": "warm-white"}
        pls["Holiday: Christmas"]["items"] = pls["Holiday: Christmas"]["items"][1:]
        ch = cal.changes(fresh, sch, pls, sts, IDS)
        self.assertFalse(ch["outdated"], ch)
        # a calendar that really changed since then is still offered
        newer = copy.deepcopy(fresh)
        newer["showtimes"].append({**newer["showtimes"][0], "show": "Brand new show"})
        ch = cal.changes(newer, sch, pls, sts, IDS)
        self.assertEqual([s["show"] for s in ch["showtimes"]], ["Brand new show"])
        self.assertEqual(ch["rules"], [])

    def test_no_calendar_no_changes(self):
        fresh = cal.build(IDS, SHOWS, TEAMS, today=date(2026, 11, 26))
        self.assertFalse(cal.changes(fresh, {**SCH, "rules": []}, {}, [], IDS)["outdated"])


class TonightAfterTheUpgrade(_Denver, unittest.TestCase):
    def rig(self, when=datetime(2026, 11, 26, 14, 0)):
        sch, pls, sts = old_install()
        return Rig(self, when.astimezone(), {"schedule": sch, "playlists": pls, "showtimes": sts})

    def test_tonight_says_the_calendar_has_tonights_show(self):
        r = self.rig()
        plan = r.ctl.tonight(password_set=True)
        self.assertEqual(plan["program_why"], "holiday calendar: Thanksgiving")
        self.assertNotIn("Thanksgiving tonight", [s["show"] for s in plan["showtimes"]])
        ck = next(c for c in plan["checks"] if c["id"] == "calendar")
        self.assertEqual(ck["level"], tonight.WARN)
        self.assertEqual(ck["jump"], "calendar")
        self.assertIn("Thanksgiving tonight", ck["detail"])
        self.assertIn("6:00 pm", ck["detail"])
        self.assertIn("9:00 pm", ck["detail"])
        self.assertIn("Thanksgiving rotation", ck["detail"])          # tonight's program changes too
        # one tap on Update (the sheet sends the factory rules it would switch off: none left)
        pv = r.ctl.calendar_preview()
        self.assertEqual([x for x in pv["shadows"] if x["factory"]], [])
        r.ctl.calendar_apply([], False)
        plan = r.ctl.tonight(password_set=True)
        self.assertNotIn("calendar", [c["id"] for c in plan["checks"]])
        st = next(s for s in plan["showtimes"] if s["show"] == "Thanksgiving tonight")
        self.assertEqual([datetime.fromisoformat(t).hour for t in st["times"]], [18, 19, 20, 21])
        self.assertEqual(plan["program_name"], "Thanksgiving rotation")
        # the owner's own showtime, show-free factory rules and playlist survive
        self.assertIn("Family show", [s["show"] for s in r.ctl.cfg.data["showtimes"]])
        self.assertEqual([x.get("enabled") for x in r.ctl.cfg.data["schedule"]["rules"][:2]], [False, False])
        self.assertIn("Christmas", r.ctl.cfg.data["playlists"])

    def test_an_update_that_misses_tonight_is_still_offered(self):
        r = self.rig(datetime(2026, 11, 28, 14, 0))
        ck = next(c for c in r.ctl.tonight(password_set=True)["checks"] if c["id"] == "calendar")
        self.assertEqual(ck["level"], tonight.WARN)
        self.assertIn("Christmas on the roofline", ck["detail"])


class AFourthBoxOnTheNetwork(_Denver, unittest.TestCase):
    """The H705E is on the LAN: discovery lists it with no bulb count. That is not a
    problem for tonight's show (the three runs light up); it points at More -> Fourth run."""

    def test_an_uncounted_extra_box_is_a_warning_not_a_failure(self):
        r = TonightAfterTheUpgrade.rig(self)
        r.ctl.cfg.devices["AA:BB:CC:DD:EE:FF:00:33"] = {"name": "H705E 0033", "sku": "H705E", "ip": "10.0.0.40",
                                                       "pixel_count": None, "enabled": True}
        ck = [c for c in r.ctl.tonight(password_set=True)["checks"] if c["id"] == "counts"]
        self.assertEqual(len(ck), 1)
        self.assertEqual(ck[0]["level"], tonight.WARN)
        self.assertIn("H705E 0033", ck[0]["detail"])
        self.assertIn("Eaves", ck[0]["detail"])                  # the runs that do light up
        self.assertEqual(ck[0]["jump"], "fourth")

    def test_a_placed_run_losing_its_count_is_still_a_failure(self):
        """Peak (mapped, a house-layout run) with no bulb count is not a new box: pointing
        at Fourth run would offer to overwrite its gable map with a guessed line."""
        r = TonightAfterTheUpgrade.rig(self)
        peak = next(d for d in r.ctl.cfg.devices.values() if d.get("name") == "Peak")
        peak["pixel_count"] = None
        r.ctl.build_engine()
        ck = next(c for c in r.ctl.tonight(password_set=True)["checks"] if c["id"] == "counts")
        self.assertEqual(ck["level"], tonight.FAIL, ck)
        self.assertIn("Peak", ck["detail"])
        self.assertEqual(ck["jump"], "devices")

    def test_no_counted_box_at_all_is_still_a_failure(self):
        r = TonightAfterTheUpgrade.rig(self)
        for d in r.ctl.cfg.devices.values():
            d["pixel_count"] = None
        r.ctl.build_engine()                                    # nothing left to light
        ck = next(c for c in r.ctl.tonight(password_set=True)["checks"] if c["id"] == "counts")
        self.assertEqual(ck["level"], tonight.FAIL)


class AppJumps(unittest.TestCase):
    def test_the_fourth_run_check_opens_its_card(self):
        html = (ROOT / "goveelights" / "web" / "app.html").read_text(encoding="utf-8")
        self.assertIn("fourth:['more','','fourthCard']", html)

    def test_the_calendar_check_opens_the_calendar_sheet(self):
        html = (ROOT / "goveelights" / "web" / "app.html").read_text(encoding="utf-8")
        self.assertIn("j === 'calendar'", html)


if __name__ == "__main__":
    unittest.main()
