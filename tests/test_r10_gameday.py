"""Round-10 bug hunt: game day on the house's own three runs, driven through the real
ESPN parsing (live.find_game) with a mocked scoreboard feed and a fake clock: pregame,
kickoff, scores by both teams, the extra point after a touchdown, overtime, the final
and feed outages, for an afternoon game that ends just before the lights come on."""
import sys
import unittest
from datetime import datetime, timezone
from unittest import mock

sys.path.insert(0, "tests")
from test_integration import Rig  # noqa: E402

from goveelights import controller as C  # noqa: E402
from goveelights import tonight  # noqa: E402

KEY = "nfl:DEN"
TEAM = {"league": "nfl", "team": "DEN"}


def local(h, m=0, day=27):
    return datetime(2026, 11, day, h, m).astimezone()


def board(state, den, pit, detail="", kickoff=None):
    """An ESPN scoreboard (the fields live.find_game reads) for DEN at PIT."""
    ko = (kickoff or local(13)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    return {"events": [{"id": "401772", "date": ko, "competitions": [{
        "status": {"type": {"state": state, "shortDetail": detail}},
        "competitors": [
            {"homeAway": "home", "score": str(pit),
             "team": {"abbreviation": "PIT", "color": "000000", "alternateColor": "ffb612"}},
            {"homeAway": "away", "score": str(den),
             "team": {"abbreviation": "DEN", "color": "0a2343", "alternateColor": "fc4c02"}}]}]}]}


class GameDay:
    """The controller plus a feed that serves whatever `feed` holds (an Exception = outage)."""

    def __init__(self, test, start, on_time="16:36", **game):
        sch = {"enabled": True, "on": "time", "on_time": on_time, "off_time": "23:00",
               "default": {"preset": "warm-white"}, "rules": []}
        g = {"enabled": True, "teams": [TEAM], "delay": 30, "scoreboard_every": 0, "when_lights_off": False, **game}
        self.r = Rig(test, start, {"schedule": sch, "game": g})
        self.ctl = self.r.ctl
        self.feed = board("pre", 0, 0, "11/27 - 1:00 PM MST")
        self.next_poll = 0.0
        self.labels: list = []
        self._seen = None
        p = mock.patch.object(C.live, "fetch_json", side_effect=self._fetch)
        p.start()
        test.addCleanup(p.stop)

    def _fetch(self, url):
        if isinstance(self.feed, Exception):
            raise self.feed
        return self.feed

    def run(self, seconds, step=2.0):
        """The live-data loop (poll when due, celebrations), the once-a-second work and
        the schedule, on the fake clock."""
        end = self.r.clock.t + seconds
        while self.r.clock.t < end:
            t = self.r.clock.t
            if t >= self.next_poll:
                self.next_poll = t + self.ctl._poll_game(TEAM, KEY)
            self.ctl._pending_tick(t, force_live=bool(self.ctl.cfg.data["game"].get("when_lights_off")))
            it = self.ctl.interrupt or {}
            if it.get("label") and (it["label"], it.get("until")) != self._seen:
                self.labels.append(it["label"])               # each celebration started
            self._seen = (it.get("label"), it.get("until"))
            self.r.tick(step, step=step)


class ExtraPointTests(unittest.TestCase):
    def test_touchdown_then_extra_point_is_one_celebration(self):
        """ESPN shows the touchdown (6) and the extra point (+1, or +2 for a two-point
        try) as separate updates a minute or so apart: one celebration, not two."""
        gd = GameDay(self, local(18, 0), on_time="17:00")
        gd.feed = board("in", 0, 0, "1st 15:00", kickoff=local(18))
        gd.run(60)
        gd.feed = board("in", 6, 0, "1st 9:12", kickoff=local(18))
        gd.run(60)
        gd.feed = board("in", 7, 0, "1st 9:12", kickoff=local(18))
        gd.run(120)
        gd.feed = board("in", 13, 0, "2nd 3:00", kickoff=local(18))
        gd.run(60)
        gd.feed = board("in", 15, 0, "2nd 3:00", kickoff=local(18))
        gd.run(120)
        self.assertEqual(gd.labels, ["Touchdown!", "Touchdown!"])

    def test_field_goal_and_safety_still_celebrate(self):
        gd = GameDay(self, local(18, 0), on_time="17:00")
        gd.feed = board("in", 0, 0, "1st", kickoff=local(18))
        gd.run(60)
        gd.feed = board("in", 3, 0, "1st", kickoff=local(18))        # field goal
        gd.run(120)
        gd.feed = board("in", 5, 0, "2nd", kickoff=local(18))        # a safety, long after any touchdown
        gd.run(120)
        self.assertEqual(gd.labels, ["Score!", "Score!"])


class KickoffTests(unittest.TestCase):
    def test_score_in_the_first_poll_after_kickoff_is_celebrated(self):
        """The game flips from pre to in between two polls and we have already scored
        (a kick-off return, an opening drive): that score is still celebrated."""
        gd = GameDay(self, local(17, 50), on_time="17:00")
        gd.feed = board("pre", 0, 0, "6:00 PM", kickoff=local(18))
        gd.run(60)
        self.assertEqual(gd.ctl.games[KEY]["state"], "pre")
        gd.feed = board("in", 7, 0, "1st 11:40", kickoff=local(18))
        gd.run(16 * 60)
        self.assertIn("Touchdown!", gd.labels)

    def test_polls_often_around_kickoff(self):
        """Five-minute pregame polls would miss the start; near kickoff it checks every minute."""
        gd = GameDay(self, local(12, 0))
        self.assertGreaterEqual(gd.ctl._poll_game(TEAM, KEY), 300)       # an hour out: relaxed
        gd.r.clock.t = local(12, 55).timestamp()
        self.assertLessEqual(gd.ctl._poll_game(TEAM, KEY), 60)           # five minutes out
        gd.r.clock.t = local(13, 4).timestamp()
        self.assertLessEqual(gd.ctl._poll_game(TEAM, KEY), 60)           # late start: still pre


class AfternoonFinalTests(unittest.TestCase):
    """Tomorrow's game (1 pm MT) ends around 4:15 pm; the lights come on at sunset (~4:36)."""

    def play_the_game(self, gd, outage_at_final=False):
        gd.run(60)                                                       # 12:00 pregame
        gd.r.clock.t = local(13, 5).timestamp()
        gd.feed = board("in", 7, 0, "1st 4:00")
        gd.run(60)
        gd.feed = board("in", 7, 3, "2nd 12:00")                         # theirs: nothing
        gd.run(60)
        gd.feed = OSError("timed out")                                   # the feed drops out
        gd.run(10 * 60)
        gd.feed = board("in", 17, 17, "OT 8:00")
        gd.run(60)
        gd.r.clock.t = local(16, 10).timestamp()
        gd.feed = board("in", 23, 17, "OT 0:00")
        gd.run(60)
        if outage_at_final:
            gd.feed = OSError("timed out")
            gd.run(120)
        gd.feed = board("post", 23, 17, "Final/OT")
        gd.run(120)

    def test_overtime_win_before_lights_on_is_celebrated_at_lights_on(self):
        gd = GameDay(self, local(12, 0))
        self.play_the_game(gd)
        self.assertFalse(gd.ctl.engine.live)                             # 4:14 pm: still daylight
        self.assertEqual(gd.labels, [])
        gd.run(25 * 60)                                                  # past 4:36: lights on
        self.assertTrue(gd.ctl.engine.live)
        self.assertIn("Victory!", gd.labels)
        self.assertNotIn("Touchdown!", gd.labels)                        # daytime scores stay daytime
        gd.run(3 * 60)
        self.assertIsNone(gd.ctl.interrupt)
        self.assertEqual(gd.ctl.playing.get("id"), "warm-white")         # back to the evening's program

    def test_outage_across_the_final_still_wins(self):
        gd = GameDay(self, local(12, 0))
        self.play_the_game(gd, outage_at_final=True)
        gd.run(25 * 60)
        self.assertIn("Victory!", gd.labels)

    def test_a_win_long_before_lights_on_is_not_held(self):
        gd = GameDay(self, local(12, 0), on_time="20:00")
        self.play_the_game(gd)
        gd.r.clock.t = local(19, 59).timestamp()
        gd.run(5 * 60)
        self.assertTrue(gd.ctl.engine.live)
        self.assertEqual(gd.labels, [])
        self.assertEqual(gd.ctl._pending, [])

    def test_a_loss_is_not_celebrated(self):
        gd = GameDay(self, local(12, 0))
        gd.feed = board("in", 7, 10, "4th")
        gd.r.clock.t = local(16, 0).timestamp()
        gd.run(60)
        gd.feed = board("post", 7, 10, "Final")
        gd.run(60 * 60)
        self.assertTrue(gd.ctl.engine.live)
        self.assertEqual(gd.labels, [])

    def test_waking_from_sleep_a_day_later_does_not_celebrate(self):
        """The PC slept mid-game; the scoreboard still lists the final the next evening."""
        gd = GameDay(self, local(12, 0))
        gd.r.clock.t = local(14, 0).timestamp()
        gd.feed = board("in", 14, 3, "Halftime")
        gd.run(60)
        gd.r.clock.t = local(17, 0, day=28).timestamp()                  # Saturday evening
        gd.feed = board("post", 23, 17, "Final/OT")
        gd.run(10 * 60)
        self.assertTrue(gd.ctl.engine.live)
        self.assertEqual(gd.labels, [])

    def test_when_lights_off_setting_still_celebrates_at_once(self):
        gd = GameDay(self, local(12, 0), when_lights_off=True)
        self.play_the_game(gd)
        self.assertIn("Victory!", gd.labels)
        self.assertIn("Touchdown!", gd.labels)


class TonightKickoffTests(unittest.TestCase):
    def test_daytime_kickoff_says_the_lights_are_off(self):
        """The Tonight card must not promise celebrations at a 1 pm kickoff while the
        lights are off (and the owner has not asked for them with the lights off)."""
        gd = GameDay(self, local(12, 0))
        gd.run(4)
        p = tonight.build(gd.ctl, local(12, 1))
        kick = next(x for x in p["timeline"] if x["kind"] == "game")
        self.assertEqual(kick["time"], "1:00 pm")
        self.assertNotIn("celebrations when they score", kick["detail"])
        self.assertIn("lights are off", kick["detail"])

    def test_evening_kickoff_keeps_the_promise(self):
        gd = GameDay(self, local(17, 0), on_time="16:36")
        gd.feed = board("pre", 0, 0, "6:15 PM", kickoff=local(18, 15))
        gd.run(4)
        p = tonight.build(gd.ctl, local(17, 1))
        kick = next(x for x in p["timeline"] if x["kind"] == "game")
        self.assertEqual(kick["detail"], "celebrations when they score")


if __name__ == "__main__":
    unittest.main()
