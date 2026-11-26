"""Round-5 fixes: the Tonight plan's showtime dates match the controller's (a window that
crosses midnight belongs to its evening; a date wins over weekdays), the holiday calendar
preview shows the Santa time that will be installed and keeps it after lights-on when the
schedule changes, and visitor picks are refused while the lights are off."""
import json
import sys
import tempfile
import threading
import unittest
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from test_integration import SCH, Rig  # noqa: E402
from test_tonight import _Denver  # noqa: E402

from goveelights import schedule as sched  # noqa: E402
from goveelights import tonight  # noqa: E402
from goveelights.app_server import Server, make_handler  # noqa: E402
from goveelights.auth import Auth  # noqa: E402


def dt(*a):
    return datetime(*a).astimezone()


def hm(ts):
    return [t.strftime("%m-%d %H:%M") for t in ts]


class ShowtimeNightTests(_Denver, unittest.TestCase):
    def test_after_midnight_firings_belong_to_the_evening(self):
        st = {"show": "x", "from": "23:00", "to": "01:00", "every": 60, "date": "12-30"}
        self.assertEqual(hm(tonight.showtime_times(st, dt(2026, 12, 30, 12), dt(2026, 12, 31, 12))),
                         ["12-30 23:00", "12-31 00:00", "12-31 01:00"])
        st["date"] = "01-01"                           # Jan 1's evening starts at 11 pm on Jan 1
        self.assertEqual(tonight.showtime_times(st, dt(2026, 12, 31, 12), dt(2027, 1, 1, 12)), [])
        self.assertEqual(hm(tonight.showtime_times(st, dt(2027, 1, 1, 12), dt(2027, 1, 2, 12))),
                         ["01-01 23:00", "01-02 00:00", "01-02 01:00"])

    def test_weekdays_too(self):
        st = {"show": "x", "from": "23:30", "to": "00:30", "every": 60, "days": [4]}     # Friday nights
        self.assertEqual(hm(tonight.showtime_times(st, dt(2026, 12, 4, 12), dt(2026, 12, 5, 12))),
                         ["12-04 23:30", "12-05 00:30"])

    def test_a_date_wins_over_weekdays(self):
        st = {"show": "x", "from": "18:00", "to": "18:00", "date": "12-31", "days": [0]}  # 12-31 is a Thursday
        self.assertTrue(sched.showtime_on_night(st, dt(2026, 12, 31, 12).date()))
        self.assertEqual(hm(tonight.showtime_times(st, dt(2026, 12, 31, 12), dt(2027, 1, 1, 12))), ["12-31 18:00"])

    def test_plan_matches_what_the_controller_plays(self):
        stt = [{"show": "Christmas Eve spectacular", "from": "23:00", "to": "01:00", "every": 60,
                "date": "12-30", "only_when_on": True}]
        r = Rig(self, dt(2026, 12, 30, 22, 58), {"schedule": {**SCH, "off_time": "01:30"}, "showtimes": stt})
        ctl = r.ctl
        r.tick(10)
        self.assertTrue(ctl.engine.live)
        planned = [t[11:16] for t in ctl.tonight()["showtimes"][0]["times"]]
        self.assertEqual(planned, ["23:00", "00:00", "01:00"])
        starts, was = [], None
        for _ in range(130):                                    # to about 01:08, a minute at a time
            r.tick(60, step=2)
            lab = (ctl.interrupt or {}).get("label")
            if lab and lab != was:
                starts.append(datetime.fromtimestamp(r.clock.t).strftime("%H"))
            was = lab
        self.assertEqual(starts, ["23", "00", "01"])


class CalendarSantaTimeTests(_Denver, unittest.TestCase):
    LATE = {**SCH, "on": "time", "on_time": "17:30", "rules": []}

    def santa(self, sts):
        return next(s for s in sts if s["show"] == "Santa's coming")

    def test_preview_shows_the_time_that_is_installed(self):
        r = Rig(self, dt(2026, 11, 26, 12, 0), {"schedule": dict(self.LATE), "showtimes": []})
        pv = self.santa(r.ctl.calendar_preview()["showtimes"])
        self.assertEqual(pv["time"], "5:31 pm")
        self.assertIn("from 5:31 pm", pv["about"])
        self.assertIn("moved from 5:15 pm", pv["about"])
        r.ctl.calendar_apply()
        self.assertEqual(self.santa(r.ctl.cfg.data["showtimes"])["from"], "17:31")
        r2 = Rig(self, dt(2026, 11, 26, 12, 0), {"schedule": {**SCH, "rules": []}, "showtimes": []})
        pv2 = self.santa(r2.ctl.calendar_preview()["showtimes"])            # sunset: no move, no note
        self.assertEqual(pv2["time"], "5:15 pm")
        self.assertNotIn("moved", pv2["about"])

    def test_a_later_on_time_moves_santa_again(self):
        mine = {"show": "Christmas Eve spectacular", "from": "17:15", "to": "17:15", "date": "12-24"}
        r = Rig(self, dt(2026, 11, 26, 12, 0), {"schedule": {**SCH, "rules": []}, "showtimes": [mine]})
        r.ctl.calendar_apply()
        self.assertEqual(self.santa(r.ctl.cfg.data["showtimes"])["from"], "17:15")
        auth = Auth(Path(tempfile.mkdtemp()) / "auth.json")
        srv = Server(("127.0.0.1", 0), make_handler(r.ctl, lambda: None, auth))
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        req = urllib.request.Request(f"http://127.0.0.1:{srv.server_address[1]}/api/schedule",
                                     json.dumps({"schedule": {"on": "time", "on_time": "18:00"}}).encode(),
                                     method="POST", headers={"Content-Type": "application/json",
                                                             "X-Requested-With": "lights"})
        self.assertTrue(json.load(urllib.request.urlopen(req, timeout=5))["ok"])
        saved = json.loads(Path(r.tmp, "cfg.json").read_text())["showtimes"]
        self.assertEqual((self.santa(saved)["from"], self.santa(saved)["to"]), ("18:01", "18:01"))
        self.assertEqual(saved[0], mine)                     # the user's own showtime is theirs to fix


class VisitorWhileOffTests(_Denver, unittest.TestCase):
    def test_pick_while_off_is_refused_even_without_only_when_on(self):
        r = Rig(self, dt(2026, 12, 10, 12, 0), {"schedule": {**SCH, "rules": []}, "visitor": {
            "enabled": True, "token": "t", "presets": ["candy-cane"], "only_when_on": False}, "events": []})
        r.tick(10)
        ctl = r.ctl
        self.assertFalse(ctl.engine.live)
        self.assertFalse(ctl.visitor_state("a")["on"])
        with self.assertRaises(ValueError):
            ctl.visitor_pick("candy-cane", "a")
        self.assertEqual(ctl.vqueue, [])


if __name__ == "__main__":
    unittest.main()
