import unittest
from datetime import date, datetime, timedelta, timezone

from goveelights.schedule import evaluate, program_for, sun_times, window

EVANS = (40.3764, -104.6922)
MST, MDT = timezone(timedelta(hours=-7)), timezone(timedelta(hours=-6))


class SunTests(unittest.TestCase):
    def check(self, d, tz, hh, mm):
        _, s = sun_times(d, *EVANS)
        local = s.astimezone(tz)
        got = local.hour * 60 + local.minute
        self.assertLess(abs(got - (hh * 60 + mm)), 4, f"{d}: sunset {local:%H:%M}, expected ~{hh}:{mm:02d}")

    def test_evans_sunsets(self):
        # reference values for Evans/Greeley, CO (timeanddate.com, within ~2 min)
        self.check(date(2026, 12, 21), MST, 16, 38)
        self.check(date(2026, 6, 21), MDT, 20, 32)
        self.check(date(2026, 3, 20), MDT, 19, 13)


class ScheduleTests(unittest.TestCase):
    SCH = {"on": "time", "on_time": "17:00", "off_time": "01:00",
           "default": {"preset": "warm-white"},
           "rules": [{"name": "Xmas", "from": "11-25", "to": "01-06", "program": {"playlist": "Christmas"}},
                     {"name": "Halloween", "from": "10-01", "to": "10-31", "program": {"preset": "halloween"}}]}

    def at(self, *a):
        return datetime(*a).astimezone()

    def test_rules_wrap_year(self):
        self.assertEqual(program_for(date(2026, 12, 31), self.SCH)[1], "Xmas")
        self.assertEqual(program_for(date(2027, 1, 3), self.SCH)[1], "Xmas")
        self.assertEqual(program_for(date(2026, 10, 15), self.SCH)[1], "Halloween")
        self.assertEqual(program_for(date(2026, 7, 4), self.SCH)[1], "default")

    def test_window_past_midnight(self):
        on, off = window(date(2026, 7, 4), self.SCH)
        self.assertEqual(off - on, timedelta(hours=8))

    def test_evaluate(self):
        self.assertFalse(evaluate(self.at(2026, 7, 4, 12, 0), self.SCH)["active"])
        e = evaluate(self.at(2026, 7, 4, 18, 0), self.SCH)
        self.assertTrue(e["active"])
        self.assertEqual(e["program"], {"preset": "warm-white"})
        e = evaluate(self.at(2026, 7, 5, 0, 30), self.SCH)          # after midnight, still on
        self.assertTrue(e["active"])
        e = evaluate(self.at(2026, 12, 24, 20, 0), self.SCH)
        self.assertEqual(e["program"], {"playlist": "Christmas"})
        e = evaluate(self.at(2026, 7, 5, 2, 0), self.SCH)
        self.assertFalse(e["active"])
        self.assertTrue(e["next_change"].startswith("2026-07-05T17:00"))

    def test_sunset_mode(self):
        sch = {"on": "sunset", "on_offset_min": -15, "off_time": "23:00", "lat": EVANS[0], "lon": EVANS[1]}
        on, off = window(date(2026, 12, 21), sch)
        self.assertLess(on, off)


if __name__ == "__main__":
    unittest.main()
