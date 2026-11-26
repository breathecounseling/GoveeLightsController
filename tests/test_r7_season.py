"""Round 7: what a simulated holiday season (Dec 1 - Jan 6) turned up.

Celebrations no longer end a running show or cut into a pinned midnight countdown, a
loop show due earlier in the evening starts late after a restart, and the 'tonight'
plan says what really happens at New Year's and with the 'days to go' reminders."""
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, "tests")
from test_integration import GAME, SCH, Rig  # noqa: E402
from test_tonight import _Denver  # noqa: E402

from goveelights import livedata, shows, shows_builtin, tonight  # noqa: E402

NYE = "New Year's Eve countdown"
SANTA = "Santa's coming"


def _rig(test, when, **extra):
    r = Rig(test, when.astimezone(), {"schedule": {"enabled": False}, **extra})
    r.ctl.play_preset("warm-white")
    r.ctl.set_live(True)
    return r


class CelebrationDuringShowTests(_Denver, unittest.TestCase):
    def test_a_touchdown_pauses_a_loop_show_and_the_show_carries_on(self):
        r = _rig(self, datetime(2026, 12, 24, 18, 40))
        ctl = r.ctl
        self.assertTrue(ctl.play_show(SANTA, manual=False))
        r.tick(70, schedule=False)                              # into the second cue
        idx = ctl.interrupt["show"]["idx"]
        ctl._pending.append((r.clock.t, "touchdown", GAME["colors"]))
        ctl._pending_tick(r.clock.t)
        self.assertEqual(ctl.playing["name"], "Touchdown!")
        self.assertIsNone(ctl.status()["show"])
        _, secs = shows.celebration("touchdown", GAME["colors"])
        r.tick(secs + 2, schedule=False)
        self.assertEqual(ctl.status()["show"], SANTA)            # back in the show, not the program
        self.assertEqual(ctl.interrupt["show"]["idx"], idx)      # at the cue it had reached
        self.assertEqual(ctl.interrupt["label"], f"Show: {SANTA}")
        self.assertEqual(ctl.interrupt["playing"]["id"], "warm-white")
        r.tick(3600, step=2, schedule=False)
        self.assertEqual(ctl.status()["show"], SANTA)            # still looping an hour later

    def test_a_score_before_midnight_waits_for_the_new_year_show(self):
        r = _rig(self, datetime(2026, 12, 31, 23, 55))
        ctl = r.ctl
        midnight = datetime(2027, 1, 1).astimezone().timestamp()
        cues = shows_builtin.BUILTIN_SHOWS[NYE]["cues"]
        j = next(k for k, c in enumerate(cues) if c.get("countdown_to"))
        self.assertTrue(ctl.play_show(NYE, manual=False))
        r.tick(midnight - 30 - r.clock.t, schedule=False)          # 23:59:30: a score lands
        ctl._pending.append((r.clock.t, "score", GAME["colors"]))
        for _ in range(40):
            r.tick(1, schedule=False)
            ctl._pending_tick(r.clock.t)
            self.assertEqual(ctl.status()["show"], NYE)
        self.assertEqual(ctl.interrupt["show"]["idx"], j + 1)      # the grand finale at midnight
        self.assertEqual(len(ctl._pending), 1)                     # held, not lost
        rest = sum(c["seconds"] for c in cues[j + 1:])
        r.tick(rest + 5, schedule=False)
        self.assertIsNone(ctl.interrupt)                           # the show finished on its own
        r.tick(10 * 60, schedule=False)
        ctl._pending_tick(r.clock.t)
        self.assertEqual(ctl.playing["name"], "Score!")            # and then the celebration

    def test_an_event_countdown_holds_celebrations(self):
        r = _rig(self, datetime(2026, 12, 31, 23, 58, 50))
        ctl = r.ctl
        r.tick(15, schedule=False)
        self.assertEqual(ctl.interrupt["label"], "New Year countdown")
        ctl._pending.append((r.clock.t, "touchdown", GAME["colors"]))
        ctl._pending_tick(r.clock.t)
        self.assertEqual(ctl.interrupt["label"], "New Year countdown")
        self.assertEqual(len(ctl._pending), 1)


class LateLoopShowtimeTests(_Denver, unittest.TestCase):
    STT = [{"show": SANTA, "from": "17:15", "to": "17:15", "every": 60, "date": "12-24", "only_when_on": True}]

    def rig(self, h, m):
        r = Rig(self, datetime(2026, 12, 24, h, m).astimezone(),
                {"schedule": {**SCH, "rules": []}, "showtimes": self.STT})
        r.ctl._schedule_tick()
        return r

    def test_started_after_its_minute_the_loop_show_still_plays(self):
        r = self.rig(17, 20)                                   # restarted after 5:15 pm
        self.assertTrue(r.ctl.engine.live)
        r.tick(3, schedule=False)
        self.assertEqual(r.ctl.status()["show"], SANTA)
        r.ctl.play_preset("warm-white")                          # the owner changes it: no restart
        r.tick(120, schedule=False)
        self.assertIsNone(r.ctl.status()["show"])

    def test_not_another_day_or_once_it_has_played(self):
        r = Rig(self, datetime(2026, 12, 23, 17, 20).astimezone(),
                {"schedule": {**SCH, "rules": []}, "showtimes": self.STT})
        r.ctl._schedule_tick()
        r.tick(3, schedule=False)
        self.assertIsNone(r.ctl.status()["show"])
        r = self.rig(17, 14)
        r.tick(70, schedule=False)
        self.assertEqual(r.ctl.status()["show"], SANTA)
        r.ctl.play_preset("warm-white")
        r.tick(120, schedule=False)
        self.assertIsNone(r.ctl.status()["show"])               # played at 5:15: not caught up again


class SeasonPlanTests(_Denver, unittest.TestCase):
    def plan(self, when, sch_rules, showtimes):
        r = Rig(self, when.astimezone(), {"schedule": {**SCH, "rules": sch_rules}, "showtimes": showtimes})
        r.ctl.started -= 3600
        return r, r.ctl.tonight(password_set=True)

    def test_new_year_counted_down_by_the_show_and_lights_already_on(self):
        stt = [{"show": NYE, "from": "23:55", "to": "23:55", "date": "12-31", "only_when_on": False}]
        rule = [{"name": "NYE", "from": "12-31", "to": "12-31", "off_time": "00:30",
                 "program": {"preset": "champagne-fizz"}}]
        _, p = self.plan(datetime(2026, 12, 31, 12, 0), rule, stt)
        ev = next(e for e in p["events"] if e["name"] == "New Year")
        self.assertEqual(ev["what"], f"counted down by the {NYE} show")
        show = next(x for x in p["timeline"] if x["label"] == f"Show: {NYE}")
        self.assertNotIn("switches the lights on", show["detail"])
        # off at 11 pm: the showtime does switch them on, and the show still owns midnight
        _, p = self.plan(datetime(2026, 12, 31, 12, 0), [], stt)
        show = next(x for x in p["timeline"] if x["label"] == f"Show: {NYE}")
        self.assertIn("switches the lights on", show["detail"])
        # no show: the event's own countdown and celebration
        _, p = self.plan(datetime(2026, 12, 31, 12, 0), rule, [])
        ev = next(e for e in p["events"] if e["name"] == "New Year")
        self.assertEqual(ev["what"], "60 s countdown, 10 min celebration")

    def test_days_to_go_reminders_are_in_the_plan(self):
        _, p = self.plan(datetime(2026, 12, 20, 12, 0), [], [])
        rem = [x for x in p["timeline"] if x["label"].startswith("Reminders")]
        self.assertEqual([x["label"] for x in rem], ["Reminders: 5 days to Christmas"])
        _, p = self.plan(datetime(2026, 12, 10, 12, 0), [], [])
        self.assertTrue(any(x["label"].startswith("Reminders") for x in p["timeline"]))
        _, p = self.plan(datetime(2026, 11, 20, 12, 0), [], [])
        self.assertFalse(any(x["label"].startswith("Reminders") for x in p["timeline"]))


class ReminderTests(_Denver, unittest.TestCase):
    def test_one_day_and_not_the_moment_the_lights_come_on(self):
        r = Rig(self, datetime(2026, 12, 24, 16, 0).astimezone(), {"schedule": {"enabled": False}})
        ctl = r.ctl
        r.tick(20 * 60, schedule=False)                          # up a while with the lights off
        ctl.play_preset("warm-white")
        ctl.set_live(True)
        r.tick(5, schedule=False)
        self.assertIsNone(ctl.interrupt)                         # not 1 s after lights-on
        r.tick(15 * 60, schedule=False)
        self.assertIsNotNone(ctl.interrupt)
        self.assertEqual(ctl.interrupt["label"], "1 day to Christmas")
        self.assertEqual(shows.days_scene(1)["name"], "1 day to go")
        self.assertEqual(shows.days_scene(3)["name"], "3 days to go")


class EndedPruneTests(unittest.TestCase):
    def setUp(self):
        self.saved = dict(livedata._ended)
        livedata._ended.clear()
        self.addCleanup(lambda: (livedata._ended.clear(), livedata._ended.update(self.saved)))

    def test_old_finished_games_are_forgotten(self):
        t = 1_800_000_000.0
        for k in range(20):                                       # a season of weekly games
            livedata._stamp("nfl:DEN", {"id": f"g{k}", "state": "post", "team": "DEN"}, t + k * 7 * 86400)
        self.assertLessEqual(len(livedata._ended), 1)
        # the game still shown keeps its first-seen-final time however long it is polled
        g = livedata._stamp("nfl:DEN", {"id": "g19", "state": "post", "team": "DEN"}, t + 19 * 7 * 86400 + 3 * 86400)
        self.assertEqual(g["ended"], t + 19 * 7 * 86400)
        livedata._stamp("nhl:COL", {"id": "h1", "state": "post", "team": "COL"}, t + 19 * 7 * 86400 + 3 * 86400)
        self.assertIn(("nfl:DEN", "g19"), livedata._ended)       # another team's updates leave it be
