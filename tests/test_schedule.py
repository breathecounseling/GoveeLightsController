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


class PolishTests(unittest.TestCase):
    def test_holidays(self):
        from goveelights.schedule import easter, resolve_date
        self.assertEqual(resolve_date("thanksgiving", 2026), date(2026, 11, 26))
        self.assertEqual(resolve_date("thanksgiving+1", 2026), date(2026, 11, 27))
        self.assertEqual(easter(2027), date(2027, 3, 28))
        self.assertEqual(resolve_date("mothers-day", 2027), date(2027, 5, 9))
        self.assertEqual(resolve_date("memorial-day", 2027), date(2027, 5, 31))
        self.assertEqual(resolve_date("12-24", 2026), date(2026, 12, 24))

    def test_weekday_rule_overrides_off_time(self):
        sch = {"on": "time", "on_time": "17:00", "off_time": "22:00", "default": {"preset": "a"},
               "rules": [{"name": "Weekend", "days": [4, 5], "off_time": "00:30"}]}
        on, off = window(date(2026, 11, 27), sch)          # Friday
        self.assertEqual((off - on), timedelta(hours=7, minutes=30))
        on, off = window(date(2026, 11, 25), sch)          # Wednesday
        self.assertEqual((off - on), timedelta(hours=5))

    def test_holiday_range_rule(self):
        sch = {"default": None, "rules": [{"name": "Xmas", "from": "thanksgiving+1", "to": "01-06", "program": {"preset": "x"}}]}
        self.assertEqual(program_for(date(2026, 11, 27), sch)[1], "Xmas")
        self.assertEqual(program_for(date(2026, 11, 26), sch)[1], "default")
        self.assertEqual(program_for(date(2027, 1, 3), sch)[1], "Xmas")

    def test_dim(self):
        from goveelights.schedule import dim_factor
        sch = {"dim": {"enabled": True, "from": "22:00", "level": 0.4, "fade_min": 30}}
        at = lambda h, m: datetime(2026, 11, 27, h, m).astimezone()
        self.assertEqual(dim_factor(at(21, 0), sch), 1.0)
        self.assertAlmostEqual(dim_factor(at(22, 15), sch), 0.7, places=2)
        self.assertAlmostEqual(dim_factor(at(23, 0), sch), 0.4, places=2)
        self.assertAlmostEqual(dim_factor(datetime(2026, 11, 28, 0, 30).astimezone(), sch, at(17, 0)), 0.4, places=2)

    def test_morning_window(self):
        sch = {"on": "time", "on_time": "17:00", "off_time": "22:00", "default": {"preset": "a"},
               "morning": {"enabled": True, "on_time": "06:00", "off": "time", "off_time": "07:30"}}
        e = evaluate(datetime(2026, 11, 27, 6, 30).astimezone(), sch)
        self.assertTrue(e["active"]); self.assertEqual(e["rule"], "morning")
        e = evaluate(datetime(2026, 11, 27, 3, 0).astimezone(), sch)
        self.assertFalse(e["active"]); self.assertIn("T06:00", e["next_change"])


class ReviewFixTests(unittest.TestCase):
    def test_lights_off_rule(self):
        sch = {"default": {"preset": "a"}, "rules": [{"name": "Off Mondays", "days": [0], "program": None},
                                                    {"name": "Times only", "days": [1], "off_time": "01:00"}]}
        self.assertEqual(program_for(date(2026, 11, 30), sch), (None, "Off Mondays"))     # Monday: explicitly off
        self.assertEqual(program_for(date(2026, 12, 1), sch), ({"preset": "a"}, "default"))  # times-only rule keeps default

    def test_leap_day_rule(self):
        sch = {"rules": [{"name": "Leap", "from": "02-28", "to": "02-29", "program": {"preset": "x"}}]}
        self.assertEqual(program_for(date(2028, 2, 29), sch)[1], "Leap")
        self.assertEqual(program_for(date(2027, 2, 28), sch)[1], "default")

    def test_dim_before_on_time(self):
        from goveelights.schedule import dim_factor
        sch = {"dim": {"enabled": True, "from": "16:00", "level": 0.5, "fade_min": 1}}
        on = datetime(2026, 12, 1, 16, 45).astimezone()
        self.assertAlmostEqual(dim_factor(datetime(2026, 12, 1, 17, 0).astimezone(), sch, on), 0.5)

    def test_morning_after_manual_override(self):
        sch = {"on": "time", "on_time": "17:00", "off_time": "22:00", "default": {"preset": "a"},
               "morning": {"enabled": True, "on_time": "06:00", "off": "time", "off_time": "07:30"}}
        e = evaluate(datetime(2026, 11, 27, 23, 0).astimezone(), sch)     # after the evening ends
        self.assertIn("T06:00", e["next_change"])
