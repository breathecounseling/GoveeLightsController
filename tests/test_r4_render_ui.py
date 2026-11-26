"""Round-4 regressions: dated showtimes (hidden weekdays, midnight-crossing windows,
impossible dates, the phone keypad), the countdown cue's hold range, and evolve
leaving the advent calendar's day and gift wrap's timers alone."""
from __future__ import annotations

import json
import shutil
import sys
import unittest
from datetime import datetime
from unittest import mock

sys.path.insert(0, "tests")
from test_integration import Rig  # noqa: E402
from test_r3_web import APP, extract, run_node  # noqa: E402

from goveelights import evolve  # noqa: E402
from goveelights.app_server import validate_showtimes  # noqa: E402
from goveelights.controller import Controller  # noqa: E402

OFF = {"enabled": False}
QUICK = {"Quick": {"end": "resume", "cues": [{"preset": "candy-cane", "seconds": 5}]}}


def fired(test, start, stt, seconds, step=5):
    """The local times (MM-DD HH:MM) at which the showtimes started a show."""
    r = Rig(test, start.astimezone(), {"schedule": OFF, "showtimes": stt, "shows": QUICK})
    ctl = r.ctl
    ctl.play_preset("warm-white")
    ctl.set_live(True)
    times = []
    real = ctl.play_show

    def spy(*a, **k):
        times.append(datetime.fromtimestamp(r.clock.t).strftime("%m-%d %H:%M"))
        return real(*a, **k)
    with mock.patch.object(ctl, "play_show", spy):
        r.tick(seconds, step=step, schedule=False)
    return times


class DatedShowtimeTests(unittest.TestCase):
    def test_date_wins_over_hidden_weekdays(self):
        # 2026-12-31 is a Thursday; Fr/Sa were ticked before the date was typed
        stt = [{"show": "Quick", "from": "19:00", "to": "19:00", "every": 60,
                "days": [4, 5], "date": "12-31", "only_when_on": True}]
        self.assertEqual(fired(self, datetime(2026, 12, 31, 18, 59), stt, 120), ["12-31 19:00"])

    def test_weekdays_still_apply_without_a_date(self):
        stt = [{"show": "Quick", "from": "19:00", "to": "19:00", "every": 60, "days": [4, 5]}]
        self.assertEqual(fired(self, datetime(2026, 12, 31, 18, 59), stt, 120), [])

    def test_midnight_window_belongs_to_the_evening_it_started(self):
        # (12-24 rather than 12-31: at New Year's midnight the built-in celebration has the lights)
        stt = [{"show": "Quick", "from": "23:45", "to": "00:15", "every": 15, "date": "12-24",
                "only_when_on": True}]
        self.assertEqual(fired(self, datetime(2026, 12, 23, 23, 40), stt, 40 * 60), [])
        self.assertEqual(fired(self, datetime(2026, 12, 24, 23, 40), stt, 40 * 60),
                         ["12-24 23:45", "12-25 00:00", "12-25 00:15"])

    def test_midnight_window_weekday_is_the_evening_one(self):
        # Friday 2026-12-04, 23:45 -> 00:15 Saturday morning: all three slots are Friday's
        stt = [{"show": "Quick", "from": "23:45", "to": "00:15", "every": 15, "days": [4]}]
        self.assertEqual(fired(self, datetime(2026, 12, 4, 23, 40), stt, 40 * 60),
                         ["12-04 23:45", "12-05 00:00", "12-05 00:15"])

    def test_server_rejects_impossible_dates(self):
        for bad in ("11-31", "02-30", "04-31", "00-10", "12-32"):
            with self.assertRaises(ValueError, msg=bad):
                validate_showtimes([{"show": "Quick", "date": bad}])
        validate_showtimes([{"show": "Quick", "dates": ["02-29", "12-31", "01-01"]}])


class CountdownCueTests(unittest.TestCase):
    def test_countdown_cue_secs_are_capped_like_the_scene(self):
        self.assertEqual(Controller._cue_secs({"countdown_to": "00:00", "seconds": 300}), 99)
        self.assertEqual(Controller._cue_secs({"countdown_to": "00:00", "seconds": 1}), 3)
        self.assertEqual(Controller._cue_secs({"preset": "x", "seconds": 300}), 300)

    def test_long_countdown_hold_jumps_only_99s_before_the_time(self):
        shows = {"NYE": {"end": "resume", "cues": [{"preset": "candy-cane", "seconds": 600},
                                                    {"countdown_to": "00:00", "seconds": 300}]}}
        r = Rig(self, datetime(2026, 12, 31, 23, 55).astimezone(), {"schedule": OFF, "shows": shows})
        ctl = r.ctl
        ctl.play_preset("warm-white")
        ctl.set_live(True)
        self.assertTrue(ctl.play_show("NYE", manual=False))
        midnight = datetime(2027, 1, 1, 0, 0).astimezone().timestamp()
        while ctl.interrupt["show"]["idx"] == 0 and r.clock.t < midnight:
            r.tick(1, schedule=False)
        self.assertAlmostEqual(midnight - r.clock.t, 99, delta=2)


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class AppTests(unittest.TestCase):
    def defs(self, *names):
        return "\n".join(extract(APP, n) for n in names)

    def test_parse_dates_month_lengths_and_phone_input(self):
        out = run_node(self.defs("parseStDates") + """
console.log(JSON.stringify([parseStDates('11-31'), parseStDates('02-30'), parseStDates('02-29'), parseStDates('1231'),
  parseStDates('12/31 1.1'), parseStDates('12-31'), parseStDates('123'), parseStDates('0431')]))""")
        self.assertEqual(out, [None, None, ["02-29"], ["12-31"], ["12-31", "01-01"], ["12-31"], None, None])

    def test_date_box_is_not_a_number_pad_and_clears_days(self):
        src = extract(APP, "drawShowtimes")
        self.assertNotIn('inputmode="numeric"', src)
        self.assertIn("delete st.days", src)

    def test_cue_secs_matches_the_server_for_countdowns(self):
        cases = [{"countdown_to": "00:00", "seconds": 300}, {"countdown_to": "00:00", "seconds": 1},
                 {"countdown_to": "00:00"}, {"countdown_to": "00:00", "seconds": 45.7}, {"seconds": 300}]
        out = run_node(self.defs("cueSecs") + f"\nconsole.log(JSON.stringify({json.dumps(cases)}.map(cueSecs)))")
        self.assertEqual(out, [Controller._cue_secs(c) for c in cases])

    def test_countdown_hold_input_range(self):
        self.assertIn('max="${c.countdown_to?99:600}"', extract(APP, "shEditor"))


class EvolveSkipTests(unittest.TestCase):
    def test_date_and_timer_params_are_kept(self):
        for name in ("day", "tie", "unwrap_every"):
            self.assertIn(name, evolve.SKIP)

    def test_advent_day_stays_put(self):
        scene = {"layers": [{"effect": "advent", "params": {"day": 12}}]}
        days = [evolve.variation(scene, s)["layers"][0]["params"]["day"] for s in range(1, 10)]
        self.assertEqual(days, [12] * 9)


if __name__ == "__main__":
    unittest.main()
