"""'Tonight' plan + readiness checks, and the one-tap holiday calendar."""
import copy
import json
import logging
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from test_integration import SCH, Rig  # noqa: E402

from goveelights import calendar_builtin as cal  # noqa: E402
from goveelights import schedule as sched  # noqa: E402
from goveelights import shows_builtin, tonight  # noqa: E402
from goveelights.app_server import Server, make_handler, validate_schedule, validate_showtimes  # noqa: E402
from goveelights.auth import Auth  # noqa: E402
from goveelights.config import DEFAULTS  # noqa: E402
from goveelights.controller import PresetStore  # noqa: E402

ALL_IDS = [p["id"] for p in PresetStore(Path("presets")).list()]
HOLIDAY = ("valentines", "st-patricks", "easter", "july-4th", "thanksgiving", "halloween-", "broncos-game-day",
           "winter-elegance", "spring-", "summer-")
IDS = [i for i in ALL_IDS if not i.startswith(HOLIDAY)]     # the core looks, without the holiday track
SHOWS = list(shows_builtin.BUILTIN_SHOWS)
# what the holiday track adds (and a couple of halloween-* looks)
EXTRA = ["valentines", "st-patricks", "easter", "july-4th", "thanksgiving", "halloween-pumpkins", "halloween-ghosts",
         "broncos-game-day"]


class _Denver:
    """Run in the house's time zone (sunset, on/off times and showtimes are PC-local)."""

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


def _prog_presets(prog, playlists) -> list[str]:
    if prog.get("playlist"):
        return [i["preset"] for i in playlists[prog["playlist"]]["items"]]
    return [prog.get("preset") or prog.get("evolve")]


# ------------------------------------------------------------------ calendar
class CalendarTests(_Denver, unittest.TestCase):
    def merged(self, ids, user_rules=(), disable=None, default=None):
        c = cal.build(ids, SHOWS)
        sch = {**SCH, "rules": copy.deepcopy(list(user_rules)), "default": default}
        return c, cal.merge(c, sch, copy.deepcopy(DEFAULTS["playlists"]), [], ids, disable)

    def test_every_day_of_the_year_resolves_to_an_installed_program(self):
        for ids in (IDS, IDS + EXTRA):
            for user, disable in (((), None), (DEFAULTS["schedule"]["rules"], None), (DEFAULTS["schedule"]["rules"], [0, 1])):
                c, (sch, pls, sts) = self.merged(ids, user, disable)
                validate_schedule(sch)
                validate_showtimes(sts)
                start = date(2027, 1, 1)
                for k in range(366 + 365):                     # 2027 and leap-year 2028
                    d = start + timedelta(days=k)
                    now = datetime(d.year, d.month, d.day, 21, 45).astimezone()
                    ev = sched.evaluate(now, sch)
                    self.assertTrue(ev["active"], (d, ev))
                    for pid in _prog_presets(ev["program"], pls):
                        self.assertIn(pid, ids, (d, ev["program"]))

    def test_the_year_with_the_holiday_presets(self):
        _, (sch, pls, _) = self.merged(IDS + EXTRA)
        want = {date(2027, 12, 24): "Christmas Eve", date(2027, 12, 25): "Christmas Day",
                date(2027, 12, 31): "New Year's Eve", date(2028, 1, 1): "New Year's Day",
                date(2027, 12, 10): "Advent", date(2027, 11, 27): "Christmas season",
                date(2027, 12, 28): "Christmas season", date(2027, 11, 25): "Thanksgiving",
                date(2028, 1, 15): "Winter", date(2027, 2, 14): "Valentine's week",
                date(2027, 3, 17): "St Patrick's", date(2027, 3, 28): "Easter week",    # Easter Sunday 2027
                date(2027, 3, 22): "Easter week", date(2027, 7, 4): "July 4th week",
                date(2027, 10, 20): "Halloween fortnight", date(2027, 10, 31): "Halloween fortnight",
                date(2027, 9, 19): "Broncos Sundays", date(2027, 10, 24): "Halloween fortnight",
                date(2027, 9, 22): "default", date(2027, 5, 5): "default", date(2027, 8, 15): "default"}
        for d, name in want.items():
            self.assertEqual(sched.program_for(d, sch)[1], name, d)
        self.assertEqual(sched.program_for(date(2027, 3, 17), sch)[0], {"evolve": "st-patricks", "minutes": 4})
        self.assertEqual(sched.program_for(date(2027, 5, 5), sch)[0], {"preset": "warm-white"})
        hall = [i["preset"] for i in pls["Holiday: Halloween"]["items"]]
        self.assertEqual(hall[:2], ["halloween-ghosts", "halloween-pumpkins"])
        self.assertIn("spooky-tree", hall)
        advent = [i["preset"] for i in pls["Holiday: Advent"]["items"]]
        xmas = [i["preset"] for i in pls["Holiday: Christmas"]["items"]]
        self.assertIn("advent-calendar", advent)
        self.assertNotIn("advent-calendar", xmas)        # only in December
        for pid in ("christmas-tree", "gift-wrap", "starry-night-sleigh", "snowy-evening"):
            self.assertIn(pid, xmas)
        # New Year's Eve stays on past midnight; the countdown show is scheduled for it
        on, off = sched.window(date(2027, 12, 31), sch)
        self.assertEqual((off.date(), off.hour, off.minute), (date(2028, 1, 1), 0, 30))

    def test_the_shipped_presets_cover_every_holiday(self):
        c = cal.build(ALL_IDS, SHOWS)
        self.assertEqual(c["skipped"], [])
        progs = {r["name"]: r["program"] for r in c["rules"]}
        self.assertEqual(progs["Valentine's week"], {"playlist": "Holiday: Valentine's"})
        self.assertEqual(progs["July 4th week"], {"playlist": "Holiday: July 4th"})
        self.assertEqual(progs["Thanksgiving"]["evolve"], "thanksgiving-harvest")
        self.assertEqual(progs["Easter week"]["evolve"], "easter-pastels")
        self.assertEqual(progs["Broncos Sundays"]["evolve"], "broncos-game-day")
        hall = [i["preset"] for i in c["playlists"]["Holiday: Halloween"]["items"]]
        self.assertIn("halloween-pumpkin", hall)
        self.assertIn("halloween-haunted", hall)
        # one Valentine's look alone still gets the week, as an evolving scene
        one = cal.build(IDS + ["valentines-glow"], SHOWS)
        self.assertIn({"evolve": "valentines-glow", "minutes": 4},
                      [r["program"] for r in one["rules"] if r["name"] == "Valentine's week"])

    def test_missing_presets_are_skipped_with_a_note(self):
        c = cal.build(IDS, SHOWS)
        names = {r["name"] for r in c["rules"]}
        skipped = {s["name"]: s["note"] for s in c["skipped"]}
        for n in ("Valentine's week", "St Patrick's", "Easter week", "July 4th week", "Thanksgiving"):
            self.assertNotIn(n, names)
            self.assertIn("not installed", skipped[n])
        self.assertIn("Christmas season", names)
        self.assertIn("Broncos Sundays", names)            # falls back to text-go-broncos
        for r in c["rules"]:
            self.assertTrue(r["calendar"])
            self.assertTrue(cal.program_ok(r["program"], set(IDS), c["playlists"]), r)
        # nothing at all installed: nothing scheduled, everything explained
        empty = cal.build([], [])
        self.assertEqual(empty["rules"], [])
        self.assertEqual(empty["showtimes"], [])
        self.assertEqual(len(empty["skipped"]), len(cal._RULES) + len(cal._SHOWTIMES))
        # a one-item rotation is not a rotation
        self.assertIsNone(cal._playlist("Holiday: Winter", {"icicles"}))

    def test_merge_keeps_user_rules_first_and_replaces_only_its_own(self):
        user = [{"name": "Birthday", "from": "05-05", "to": "05-05", "program": {"preset": "fireworks"}},
                {"name": "Weekend late", "days": [4, 5], "off_time": "00:30"}]
        plist = {"Mine": {"items": [{"preset": "comets", "minutes": 3}]}}
        sts = [{"show": "Christmas show", "from": "18:00", "to": "21:00", "every": 60}]
        c = cal.build(IDS, SHOWS)
        sch, pls, st2 = cal.merge(c, {**SCH, "rules": user}, plist, sts, IDS)
        self.assertEqual(sch["rules"][:2], user)
        self.assertEqual(sched.program_for(date(2027, 5, 5), sch)[1], "Birthday")
        n = len(sch["rules"])
        sch2, pls2, st3 = cal.merge(c, sch, pls, st2, IDS)         # a second tap: same result
        self.assertEqual(sch2, sch)
        self.assertEqual(st3, st2)
        self.assertEqual(len(sch2["rules"]), n)
        self.assertEqual(st3[0], sts[0])
        self.assertEqual(sum(1 for s in st3 if s.get("calendar")), 2)
        self.assertIn("Mine", pls2)
        stripped = cal.strip(sch2, pls2, st3)
        self.assertEqual(stripped[0]["rules"], user)
        self.assertEqual(stripped[1], plist)
        self.assertEqual(stripped[2], sts)

    def test_shadows_and_disable(self):
        c = cal.build(IDS, SHOWS)
        sch = {**SCH, "rules": copy.deepcopy(DEFAULTS["schedule"]["rules"])}
        sh = cal.shadows(sch, c, date(2026, 11, 1))
        self.assertEqual([x["name"] for x in sh], ["Christmas", "Halloween"])
        self.assertTrue(all(x["factory"] for x in sh))
        self.assertGreater(sh[0]["nights"], 30)
        merged, _, _ = cal.merge(c, sch, {}, [], IDS, disable=[0])
        self.assertFalse(merged["rules"][0]["enabled"])
        self.assertEqual(sched.program_for(date(2026, 12, 24), merged)[1], "Christmas Eve")
        self.assertEqual(sched.program_for(date(2026, 10, 25), merged)[1], "Halloween")    # still first
        back = cal.reenable(cal.strip(merged, {}, [])[0])
        self.assertEqual(back, ["Christmas"])

    def test_default_becomes_warm_white_only_when_it_cannot_play(self):
        c = cal.build(IDS, SHOWS)
        for default, want in ((None, {"preset": "warm-white"}), ({"preset": "nope"}, {"preset": "warm-white"}),
                              ({"playlist": "Gone"}, {"preset": "warm-white"}),
                              ({"preset": "comets"}, {"preset": "comets"})):
            sch, _, _ = cal.merge(c, {**SCH, "default": default}, {}, [], IDS)
            self.assertEqual(sch["default"], want, default)

    def test_preview_ranges(self):
        r = {"from": "thanksgiving", "to": "12-30"}
        self.assertEqual(cal.next_range(r, date(2026, 11, 26)), (date(2026, 11, 26), date(2026, 12, 30)))
        self.assertEqual(cal.fmt_range(cal.next_range({"from": "12-31", "to": "01-01"}, date(2027, 1, 1))),
                         "Dec 31 – Jan 1")
        self.assertEqual(cal.next_range({"from": "07-01", "to": "07-04"}, date(2026, 11, 26))[0], date(2027, 7, 1))
        self.assertEqual(cal.fmt_range((date(2027, 9, 12), date(2027, 11, 24)), [6]), "Suns, Sep 12 – Nov 24")


# ------------------------------------------------------------------ tonight
class TonightTests(_Denver, unittest.TestCase):
    def rig(self, now, **extra):
        cfg = {"schedule": {**SCH, "rules": []}, **extra}
        r = Rig(self, now.astimezone(), cfg)
        r.ctl.started -= 3600                       # the app has been up a while
        return r

    def see_all(self, r):
        r.ctl.diag.seen = {m: r.clock.t - 30 for m in r.ctl.cfg.devices}

    def test_headline_timeline_and_checks_for_the_coming_evening(self):
        now = datetime(2026, 12, 24, 14, 0)
        st = [{"show": "Santa's coming", "from": "17:15", "to": "17:15", "every": 60, "date": "12-24", "only_when_on": True},
              {"show": "Christmas show", "from": "17:00", "to": "22:00", "every": 60, "days": [3]},     # Thursday
              {"show": "New Year's Eve countdown", "from": "23:55", "to": "23:55", "date": "12-31", "only_when_on": False}]
        r = self.rig(now, showtimes=st, schedule={
            **SCH, "dusk_intro_minutes": 20, "dim": {"enabled": True, "from": "22:00", "level": 0.4},
            "rules": [{"name": "Eve", "from": "12-24", "to": "12-24", "program": {"preset": "christmas-tree"}, "calendar": True}]})
        self.see_all(r)
        p = tonight.build(r.ctl, datetime.fromtimestamp(r.clock.t).astimezone(), password_set=True)
        on = sched.window(date(2026, 12, 24), r.ctl.cfg.data["schedule"])[0]
        self.assertEqual(p["headline"], f"On at {tonight.fmt_time(on)} (sunset) · Christmas tree · off at 11:00 pm")
        self.assertFalse(p["current"])
        self.assertEqual(p["program_why"], "holiday calendar: Eve")
        self.assertEqual(p["dusk_intro_min"], 20)
        labels = [x["label"] for x in p["timeline"]]
        self.assertEqual(labels[0], "Lights on: the dusk sky")
        self.assertEqual(labels[1], "Christmas tree")
        self.assertIn("Show: Santa's coming", labels)
        self.assertIn("Show: Christmas show", labels)         # Dec 24 2026 is a Thursday
        self.assertNotIn("Show: New Year's Eve countdown", labels)
        self.assertEqual(labels[-1], "Lights off")
        self.assertIn("Dim to 40%", labels)
        santa = next(s for s in p["showtimes"] if s["show"] == "Santa's coming")
        self.assertEqual(santa["text"], "at 5:15 pm")
        xs = next(s for s in p["showtimes"] if s["show"] == "Christmas show")
        self.assertEqual(len(xs["times"]), 6)
        self.assertEqual(xs["text"], "every hour, 5:00 pm – 10:00 pm (6×)")
        self.assertEqual([x["at"] for x in p["timeline"]], sorted(x["at"] for x in p["timeline"]))
        lv = {c["id"]: c["level"] for c in p["checks"]}
        for k in ("boxes", "counts", "layout", "schedule", "program", "showtimes", "builtin", "visitor"):
            self.assertEqual(lv[k], "ok", (k, p["checks"]))
        self.assertEqual(lv["clock"], "ok")                    # Denver PC, Evans CO
        self.assertEqual(p["checks"][-1]["level"], "ok")
        json.dumps(p)

    def test_on_now_and_forced_showtime(self):
        r = self.rig(datetime(2026, 12, 31, 21, 0), showtimes=[
            {"show": "New Year's Eve countdown", "from": "23:55", "to": "23:55", "date": "12-31", "only_when_on": False}],
            schedule={**SCH, "rules": [{"name": "NYE", "from": "12-31", "to": "12-31", "off_time": "00:30",
                                        "program": {"preset": "champagne-fizz"}}]})
        self.see_all(r)
        p = r.ctl.tonight(password_set=True)
        self.assertTrue(p["current"])
        self.assertEqual(p["headline"], "On now · Champagne fizz · off at 12:30 am")
        self.assertEqual(p["off_why"], "rule: NYE")
        nye = p["showtimes"][0]
        self.assertTrue(nye["forced"])
        self.assertEqual(nye["text"], "at 11:55 pm")
        # the New Year countdown event (built in) is within 24 h
        self.assertEqual([e["name"] for e in p["events"]], ["New Year"])
        self.assertEqual(p["events"][0]["time"], "midnight")
        self.assertIn("New Year", [x["label"] for x in p["timeline"]])
        self.assertTrue(all(x["at"] >= "2026-12-31T21:00" for x in p["timeline"] if x["kind"] != "on"))

    def test_problems_are_reported_with_a_fix_and_somewhere_to_go(self):
        r = self.rig(datetime(2026, 12, 2, 12, 0), showtimes=[
            {"show": "Nope", "from": "18:00", "to": "18:00"},
            {"show": "Christmas show", "from": "18:00", "to": "20:00"}],
            playlists={"Broken": {"items": [{"preset": "gone-1"}, {"preset": "candy-cane"}]}},
            shows={"Christmas show": {"cues": [{"preset": "missing-preset", "seconds": 10}]}},
            visitor={"enabled": True, "token": "abc", "presets": ["festive-glitter"]},
            schedule={**SCH, "rules": [{"name": "December", "from": "12-01", "to": "12-31", "program": {"preset": "gone"}},
                                       {"name": "Later", "from": "01-01", "to": "01-31", "program": {"playlist": "Broken"}}]})
        macs = list(r.ctl.cfg.devices)
        r.ctl.diag.seen = {macs[0]: r.clock.t - 30, macs[1]: r.clock.t - 3600}
        r.ctl.cfg.devices[macs[2]]["ip"] = ""
        r.ctl.engine.layout.runs[1].geometry = []
        p = r.ctl.tonight(password_set=False)
        by = {}
        for c in p["checks"]:
            by.setdefault(c["id"], []).append(c)
        self.assertEqual(by["boxes"][0]["level"], "fail")
        self.assertIn("2 of 3", by["boxes"][0]["title"])
        self.assertIn("last seen 60 min ago", by["boxes"][0]["detail"])
        self.assertIn("no address", by["boxes"][0]["detail"])
        self.assertEqual(by["boxes"][0]["jump"], "devices")
        self.assertEqual(by["layout"][0]["level"], "warn")
        self.assertEqual(by["layout"][0]["jump"], "map")
        self.assertEqual(by["program"][0]["level"], "fail")
        self.assertIn("'gone'", by["program"][0]["detail"])
        self.assertEqual(by["programs"][0]["level"], "warn")
        self.assertIn("gone-1", by["programs"][0]["detail"])
        self.assertEqual({c["level"] for c in by["showtimes"]}, {"fail"})
        self.assertIn("Nope", by["showtimes"][0]["detail"])
        self.assertIn("missing-preset", by["showtimes"][0]["detail"])
        self.assertEqual(by["remote"][0]["level"], "warn")
        self.assertEqual(by["remote"][0]["jump"], "security")
        self.assertEqual([c["level"] for c in p["checks"]], sorted([c["level"] for c in p["checks"]],
                         key=["fail", "warn", "ok"].index))
        self.assertGreaterEqual(p["summary"]["fail"], 3)
        for c in p["checks"]:
            if c["level"] != "ok":
                self.assertTrue(c["fix"], c)

    def test_schedule_off_paused_and_lights_off_rule(self):
        r = self.rig(datetime(2026, 7, 10, 12, 0), schedule={**SCH, "enabled": False})
        p = r.ctl.tonight()
        self.assertEqual(p["headline"], "Schedule is off · the lights won't come on by themselves")
        self.assertEqual(next(c for c in p["checks"] if c["id"] == "schedule")["level"], "fail")
        r2 = self.rig(datetime(2026, 7, 10, 12, 0), schedule={
            **SCH, "rules": [{"name": "Away", "from": "07-01", "to": "07-31", "program": None}]})
        p2 = r2.ctl.tonight()
        self.assertEqual(p2["headline"], "Tonight: lights stay off (rule: Away)")
        self.assertEqual(p2["timeline"], [])
        r2.ctl.override_until = datetime.fromtimestamp(r2.clock.t).astimezone() + timedelta(hours=3)
        self.assertEqual(next(c for c in r2.ctl.tonight()["checks"] if c["id"] == "schedule")["level"], "warn")

    def test_after_lights_off_plans_tomorrow(self):
        r = self.rig(datetime(2026, 7, 10, 23, 30))
        p = r.ctl.tonight()
        self.assertEqual(p["day"], "Tomorrow")
        self.assertTrue(p["headline"].startswith("Tomorrow: on at 8:"), p["headline"])

    def test_followed_game_today(self):
        now = datetime(2026, 11, 29, 12, 0)
        r = self.rig(now, game={"enabled": True, "teams": [{"league": "nfl", "team": "DEN"}, {"league": "nba", "team": "DEN"}]})
        kick = datetime(2026, 11, 29, 14, 25).astimezone()
        espn = kick.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")          # ESPN's format
        r.ctl.games["nfl:DEN"] = {"id": "1", "state": "pre", "team": "DEN", "opponent": "KC", "score": 0, "opp_score": 0,
                                  "date": espn}
        p = r.ctl.tonight()
        self.assertEqual(p["games"][0]["text"], "DEN vs KC, kickoff 2:25 pm")
        self.assertEqual(p["games"][1]["text"], "no game data yet")
        self.assertIn("Kickoff: DEN vs KC", [x["label"] for x in p["timeline"]])

    def test_broken_bits_never_break_the_plan(self):
        r = self.rig(datetime(2026, 12, 5, 12, 0), showtimes=[{"show": "Quick", "from": "xx"}, "junk", {"show": 5}],
                     events=[{"name": "bad", "month": "x"}, None], game={"enabled": True, "teams": ["junk"]},
                     schedule={**SCH, "dim": {"enabled": True, "from": "bad"}})
        r.ctl.games["nfl:DEN"] = "junk"
        p = r.ctl.tonight()
        json.dumps(p)
        self.assertTrue(p["headline"])

    def test_time_zone_check(self):
        lat, lon = 40.3764, -104.6922
        winter, summer = datetime(2026, 12, 1, 18, 0), datetime(2026, 7, 1, 20, 0)
        tz = lambda h: timezone(timedelta(hours=h))           # noqa: E731
        self.assertEqual(tonight.tz_check(lat, lon, winter.replace(tzinfo=tz(-7)))[0], "ok")
        self.assertEqual(tonight.tz_check(lat, lon, summer.replace(tzinfo=tz(-6)))[0], "ok")
        lvl, _, msg = tonight.tz_check(lat, lon, winter.replace(tzinfo=tz(-6)))
        self.assertEqual(lvl, "warn")
        self.assertIn("Mountain", msg)
        self.assertIn("UTC-7", msg)
        self.assertEqual(tonight.tz_check(lat, lon, winter.replace(tzinfo=tz(0)))[0], "warn")
        self.assertEqual(tonight.tz_check(lat, lon, summer.replace(tzinfo=tz(-7)))[0], "warn")     # no DST on the PC
        self.assertEqual(tonight.tz_check(33.45, -112.07, summer.replace(tzinfo=tz(-7)))[0], "ok")   # Phoenix
        self.assertEqual(tonight.tz_check(40.71, -74.0, winter.replace(tzinfo=tz(-5)))[0], "ok")     # New York
        self.assertEqual(tonight.tz_check(51.5, -0.12, summer.replace(tzinfo=tz(1)))[0], "ok")       # London BST
        self.assertEqual(tonight.tz_check(51.5, -0.12, summer.replace(tzinfo=tz(-7)))[0], "warn")

    def test_huge_log_is_flagged(self):
        r = self.rig(datetime(2026, 12, 5, 12, 0))
        with tempfile.TemporaryDirectory() as tmp:
            lf = Path(tmp) / "goveelights.log"
            with open(lf, "wb") as f:
                f.truncate(int(tonight.LOG_WARN_MB * 1e6) + 10)      # sparse: no real disk used
            h = logging.FileHandler(lf)
            root = logging.getLogger()
            root.addHandler(h)
            try:
                c = next(c for c in r.ctl.tonight()["checks"] if c["id"] == "log")
            finally:
                root.removeHandler(h)
                h.close()
        self.assertEqual(c["level"], "warn")

    def test_fast(self):
        r = self.rig(datetime(2026, 12, 24, 14, 0), showtimes=[{"show": "Quick", "from": "00:00", "to": "23:55", "every": 5}])
        t = time.perf_counter()
        p = r.ctl.tonight()
        self.assertLess(time.perf_counter() - t, 0.5)
        self.assertLessEqual(len(p["showtimes"][0]["times"]), 48)
        self.assertLess(len(p["timeline"]), 10)


# ------------------------------------------------------------------ the API
class ApiTests(_Denver, unittest.TestCase):
    def test_tonight_and_calendar_endpoints(self):
        r = Rig(self, datetime(2026, 11, 26, 12, 0).astimezone(),
                {"schedule": {**SCH, "enabled": False, "rules": copy.deepcopy(DEFAULTS["schedule"]["rules"])},
                 "showtimes": [{"show": "Christmas show", "from": "18:00", "to": "18:00"}]})
        tmp = tempfile.mkdtemp()
        auth = Auth(Path(tmp) / "auth.json")
        srv = Server(("127.0.0.1", 0), make_handler(r.ctl, lambda: None, auth))
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        base = f"http://127.0.0.1:{srv.server_address[1]}/api/"

        def get(p):
            return json.load(urllib.request.urlopen(base + p, timeout=5))

        def post(p, body):
            req = urllib.request.Request(base + p, json.dumps(body).encode(), method="POST",
                                         headers={"Content-Type": "application/json", "X-Requested-With": "lights"})
            return json.load(urllib.request.urlopen(req, timeout=5))

        t = get("tonight")
        self.assertIn("headline", t)
        self.assertEqual(next(c for c in t["checks"] if c["id"] == "schedule")["level"], "fail")
        pv = get("calendar")
        self.assertFalse(pv["installed"])
        self.assertEqual([s["name"] for s in pv["shadows"]], ["Christmas", "Halloween"])
        self.assertEqual(pv["rules"][0]["name"], "Thanksgiving")              # date order: on now (26 Nov 2026)
        self.assertTrue(pv["rules"][0]["now"])
        self.assertEqual({s["show"] for s in pv["showtimes"]}, {"Santa's coming", "New Year's Eve countdown"})
        res = post("calendar/apply", {"disable": [0], "enable": True})
        self.assertTrue(res["ok"])
        saved = json.loads(Path(r.tmp, "cfg.json").read_text())
        sch = saved["schedule"]
        self.assertTrue(sch["enabled"])
        self.assertEqual(sch["rules"][0]["name"], "Christmas")
        self.assertFalse(sch["rules"][0]["enabled"])
        self.assertEqual(sch["rules"][1]["name"], "Halloween")
        self.assertEqual(sum(1 for x in sch["rules"] if x.get("calendar")), res["rules"])
        self.assertIn("Holiday: Christmas", saved["playlists"])
        self.assertIn("Christmas", saved["playlists"])                       # the user's playlist stays
        self.assertEqual(saved["showtimes"][0], {"show": "Christmas show", "from": "18:00", "to": "18:00"})
        self.assertEqual(len(saved["showtimes"]), 3)
        t = get("tonight")
        self.assertEqual(t["program_why"], "holiday calendar: Thanksgiving")
        self.assertIn("thanksgiving", t["headline"].lower())
        self.assertTrue(get("calendar")["installed"])
        res2 = post("calendar/apply", {})                                     # a second tap
        saved2 = json.loads(Path(r.tmp, "cfg.json").read_text())
        self.assertEqual(saved2["schedule"]["rules"], sch["rules"])
        self.assertEqual(len(saved2["showtimes"]), 3)
        self.assertEqual(res2["rules"], res["rules"])
        self.assertEqual(post("calendar/remove", {})["reenabled"], ["Christmas"])
        saved3 = json.loads(Path(r.tmp, "cfg.json").read_text())
        self.assertEqual(saved3["schedule"]["rules"], DEFAULTS["schedule"]["rules"])
        self.assertNotIn("Holiday: Christmas", saved3["playlists"])
        self.assertEqual(len(saved3["showtimes"]), 1)
        with self.assertRaises(urllib.error.HTTPError):
            post("calendar/apply", {"disable": "x"})


# ------------------------------------------------------------------ review fixes
class ReviewFixTests(_Denver, unittest.TestCase):
    def rig(self, now, **extra):
        cfg = {"schedule": {**SCH, "rules": []}, **extra}
        r = Rig(self, now.astimezone(), cfg)
        r.ctl.started -= 3600
        r.ctl.diag.seen = {m: r.clock.t - 30 for m in r.ctl.cfg.devices}
        return r

    def by(self, p, cid):
        return [c for c in p["checks"] if c["id"] == cid]

    def test_old_missed_polls_dont_outlive_fresh_discovery(self):
        r = self.rig(datetime(2026, 12, 2, 12, 0))
        macs = list(r.ctl.cfg.devices)
        r.ctl.health[macs[2]] = {"miss": 3, "seen": r.clock.t - 13 * 3600}     # left over from last night
        self.assertEqual(self.by(r.ctl.tonight(), "boxes")[0]["level"], "ok")
        r.ctl.diag.seen[macs[2]] = r.clock.t - 13 * 3600                      # and nothing heard since
        c = self.by(r.ctl.tonight(), "boxes")[0]
        self.assertEqual(c["level"], "fail")
        self.assertIn("not answering", c["detail"])

    def test_wled_is_not_held_to_govee_discovery(self):
        r = self.rig(datetime(2026, 12, 2, 12, 0))
        r.ctl.cfg.devices["wled:aa"] = {"kind": "wled", "ip": "10.0.0.9", "pixel_count": 50, "name": "Tree WLED"}
        p = r.ctl.tonight()
        c = self.by(p, "boxes")[0]
        self.assertEqual((c["level"], c["title"]), ("ok", "All 3 boxes responding"))
        self.assertIn("Tree WLED", self.by(p, "wled")[0]["detail"])

    def test_bulb_counts_are_visible_bulbs_by_run(self):
        p = self.rig(datetime(2026, 12, 2, 12, 0)).ctl.tonight()
        self.assertEqual(self.by(p, "counts")[0]["detail"], "Eaves 93 · Peak 108 · Accent 60 bulbs")

    def test_nye_off_time_never_cuts_a_later_usual_off(self):
        c = cal.build(IDS, SHOWS)
        for off, want in (("23:00", (0, 30)), ("00:15", (0, 30)), ("01:00", (1, 0)), ("02:00", (2, 0))):
            sch, _, _ = cal.merge(c, {**SCH, "off_time": off}, {}, [], IDS)
            o = sched.window(date(2027, 12, 31), sch)[1]
            self.assertEqual((o.hour, o.minute), want, off)

    def test_santa_moves_after_a_late_lights_on_and_tonight_warns_if_missed(self):
        c = cal.build(IDS, SHOWS)
        _, _, sts = cal.merge(c, {**SCH, "on": "time", "on_time": "17:30"}, {}, [], IDS)
        santa = next(s for s in sts if s["show"] == "Santa's coming")
        self.assertEqual((santa["from"], santa["to"]), ("17:31", "17:31"))
        _, _, sts = cal.merge(c, dict(SCH), {}, [], IDS)                       # sunset ~4:40: unchanged
        self.assertEqual(next(s for s in sts if s["show"] == "Santa's coming")["from"], "17:15")
        r = self.rig(datetime(2026, 12, 24, 12, 0), schedule={**SCH, "on": "time", "on_time": "17:30"}, showtimes=[
            {"show": "Santa's coming", "from": "17:15", "to": "17:15", "date": "12-24", "only_when_on": True}])
        p = r.ctl.tonight()
        w = [x for x in self.by(p, "showtimes") if x["level"] == "warn"]
        self.assertTrue(w, p["checks"])
        self.assertIn("Santa's coming at 5:15 pm is before the lights come on at 5:30 pm", w[0]["detail"])
        self.assertEqual(w[0]["jump"], "showtimes")

    def test_looping_show_says_it_takes_over(self):
        r = self.rig(datetime(2026, 12, 24, 12, 0), showtimes=[
            {"show": "Santa's coming", "from": "17:15", "to": "17:15", "date": "12-24", "only_when_on": True}])
        p = r.ctl.tonight()
        x = next(x for x in p["timeline"] if x["label"] == "Show: Santa's coming")
        self.assertIn("loops until lights-off", x["detail"])
        self.assertTrue(p["showtimes"][0]["loops"])

    def test_shadow_counts_assume_factory_rules_switched_off(self):
        c = cal.build(IDS, SHOWS)
        rules = copy.deepcopy(DEFAULTS["schedule"]["rules"]) + [
            {"name": "Fridays", "days": [4], "program": {"preset": "comets"}}]
        sh = {x["name"]: x for x in cal.shadows({**SCH, "rules": rules}, c, date(2026, 11, 1))}
        merged, _, _ = cal.merge(c, {**SCH, "rules": rules}, {}, [], IDS,
                                 disable=[i for i, r in enumerate(rules) if r in DEFAULTS["schedule"]["rules"]])
        hidden = 0
        for k in range(366):
            d = date(2026, 11, 1) + timedelta(days=k)
            cr = next((r for r in c["rules"] if sched.rule_matches(r, d)), None)
            if cr and sched.program_for(d, merged)[1] == "Fridays":
                hidden += 1
        self.assertEqual(sh["Fridays"]["nights"], hidden)
        self.assertGreater(sh["Christmas"]["nights"], 30)

    def test_users_playlist_of_the_same_name_survives(self):
        c = cal.build(IDS, SHOWS)
        mine = {"Holiday: Christmas": {"items": [{"preset": "comets", "minutes": 3}]}}
        sch, pls, sts = cal.merge(c, dict(SCH), copy.deepcopy(mine), [], IDS)
        self.assertEqual(pls["Holiday: Christmas"], mine["Holiday: Christmas"])
        self.assertTrue(pls["Holiday: Christmas (calendar)"]["calendar"])
        self.assertEqual(sched.program_for(date(2026, 12, 28), sch)[0], {"playlist": "Holiday: Christmas (calendar)"})
        self.assertEqual(cal.merge(c, sch, pls, sts, IDS)[1], pls)             # a second tap: the same
        self.assertEqual(cal.strip(sch, pls, sts)[1], mine)

    def test_broken_schedule_is_one_clear_fail(self):
        r = self.rig(datetime(2026, 12, 2, 12, 0), schedule={**SCH, "off_time": "bad"})
        p = r.ctl.tonight()
        self.assertEqual(p["headline"], "Schedule has an error · open the schedule to fix it")
        self.assertEqual([c["level"] for c in self.by(p, "schedule")], ["fail"])
        r2 = self.rig(datetime(2026, 12, 2, 12, 0), schedule={**SCH, "rules": [
            {"name": "Odd", "from": 5, "to": 7, "program": {"preset": "comets"}}]})
        p2 = r2.ctl.tonight()
        json.dumps(p2)
        self.assertIn("fail", [c["level"] for c in p2["checks"]])

    def test_morning_window_is_on_now(self):
        r = self.rig(datetime(2026, 12, 2, 6, 30), schedule={
            **SCH, "morning": {"enabled": True, "on_time": "06:00", "off": "time", "off_time": "07:30"}})
        p = r.ctl.tonight()
        self.assertTrue(p["morning_now"])
        self.assertTrue(p["headline"].startswith("On now (morning) · off at 7:30 am · Tonight: on at 4:"), p["headline"])


if __name__ == "__main__":
    unittest.main()
