"""Round-4 controller state-machine regressions: a showtime at the lights-on minute, a
looping copy of the New Year's show at lights-off, the visitor queue at lights-off, the
visitor wait during a show, a forced show spanning lights-on, and scenes that fail to
build inside an interrupt (showtime and visitor pick). Driven with the fake-clock rig."""
import sys
import unittest
from datetime import datetime

sys.path.insert(0, "tests")
from test_integration import Rig  # noqa: E402

from goveelights import controller as C  # noqa: E402

NYE = "New Year's Eve countdown"
OFF = {"enabled": False}
SCH = {"enabled": True, "on": "time", "on_time": "17:00", "off_time": "23:00",
       "default": {"preset": "warm-white"}, "rules": []}
BAD = {"name": "Broken", "layers": [{"effect": "gone"}]}


def break_preset(ctl, pid="broken"):
    """Make presets.get(pid) return a scene whose effect no longer exists."""
    real = ctl.presets.get

    def get(p):
        return dict(BAD) if p == pid else real(p)
    ctl.presets.get = get


class ShowtimeAtLightsOnTests(unittest.TestCase):
    def test_showtime_at_the_on_minute_plays(self):
        stt = [{"show": "Christmas Eve spectacular", "from": "17:00", "to": "17:00", "only_when_on": True}]
        r = Rig(self, datetime(2026, 12, 10, 16, 58).astimezone(), {"schedule": SCH, "showtimes": stt})
        r.tick(3 * 60)                                           # 17:01
        ctl = r.ctl
        self.assertTrue(ctl.engine.live)
        self.assertEqual((ctl.interrupt or {}).get("label"), "Show: Christmas Eve spectacular")


class LoopNewYearTests(unittest.TestCase):
    def test_loop_copy_ends_at_lights_off(self):
        sch = {**SCH, "on_time": "17:00", "off_time": "00:30"}
        stt = [{"show": "NYE party", "from": "23:55", "to": "23:55", "only_when_on": True}]
        r = Rig(self, datetime(2026, 12, 31, 23, 50).astimezone(), {"schedule": sch, "showtimes": stt})
        ctl = r.ctl
        ctl.copy_show(NYE, "NYE party")
        ctl.cfg.data["shows"]["NYE party"]["end"] = "loop"
        r.tick(10)
        self.assertTrue(ctl.engine.live)
        r.tick(6 * 60, step=2)                                   # 23:56: the show
        self.assertEqual((ctl.interrupt or {}).get("label"), "Show: NYE party")
        self.assertTrue(ctl.interrupt["show"]["owns"])
        r.tick(10 * 60, step=2)                                  # 00:06: past the countdown
        self.assertIn("show", ctl.interrupt)
        r.tick(30 * 60, step=5)                                  # 00:36: past lights-off
        self.assertIsNone(ctl.interrupt)
        self.assertFalse(ctl.engine.live)


class VisitorQueueTests(unittest.TestCase):
    def rig(self, when, sch=None, **extra):
        r = Rig(self, when, {"schedule": sch or SCH, "visitor": {"enabled": True, "token": "t",
                                                                   "presets": ["candy-cane", "warm-white"], "seconds": 300},
                             "events": [], **extra})
        r.tick(10)
        return r

    def test_queue_cleared_at_lights_off(self):
        r = self.rig(datetime(2026, 12, 10, 22, 58).astimezone())
        ctl = r.ctl
        self.assertTrue(ctl.engine.live)
        ctl.visitor_pick("warm-white", "a")
        r.tick(5)
        ctl.visitor_pick("candy-cane", "b")
        self.assertEqual(len(ctl.vqueue), 1)
        r.tick(120)                                              # 23:00: off
        self.assertFalse(ctl.engine.live)
        self.assertEqual(ctl.vqueue, [])

    def test_wait_counts_a_running_show(self):
        r = self.rig(datetime(2026, 12, 24, 18, 58).astimezone())
        ctl = r.ctl
        ctl.play_show("Christmas Eve spectacular", manual=False)
        ctl.visitor_pick("warm-white", "a")
        ctl.visitor_pick("candy-cane", "b")
        you = ctl.visitor_state("a")["you"]
        self.assertEqual(you["position"], 1)
        self.assertGreater(you["wait"], 400)                     # the ~9 minute show first
        self.assertTrue(you["after_show"])

    def test_loop_show_wait_is_not_a_number(self):
        r = self.rig(datetime(2026, 12, 24, 18, 58).astimezone())
        ctl = r.ctl
        ctl.play_show("Santa's coming", manual=False)
        ctl.vqueue.append({"id": "warm-white", "client": "a"})   # queued just before the show began
        you = ctl.visitor_state("a")["you"]
        self.assertIsNone(you["wait"])
        self.assertTrue(you["after_show"])


class ForcedShowAcrossLightsOnTests(unittest.TestCase):
    def test_lights_stay_on_after_the_show(self):
        stt = [{"show": "New Year's Eve countdown", "from": "16:55", "to": "16:55", "only_when_on": False}]
        r = Rig(self, datetime(2026, 12, 10, 16, 50).astimezone(), {"schedule": SCH, "showtimes": stt})
        ctl = r.ctl
        r.tick(10)
        self.assertFalse(ctl.engine.live)
        r.tick(6 * 60, step=2)                                   # 16:56: the show, lights forced on
        self.assertIn("show", ctl.interrupt or {})
        trace = []
        while ctl.interrupt:
            r.tick(1)
            trace.append(ctl.engine.live)
        self.assertTrue(all(trace))                              # the show ended after 17:00: still on
        self.assertTrue(ctl.engine.live)
        self.assertEqual((ctl.playing or {}).get("kind"), "preset")
        r.tick(30)
        self.assertTrue(ctl.engine.live)


class BrokenSceneTests(unittest.TestCase):
    def test_start_interrupt_leaves_no_half_interrupt(self):
        r = Rig(self, datetime(2026, 12, 10, 19, 0).astimezone(), {"schedule": OFF})
        ctl = r.ctl
        ctl.play_preset("warm-white")
        ctl.set_live(True)
        with self.assertRaises(ValueError):
            ctl.start_interrupt(BAD, 10 ** 12, "Show: Mine")
        self.assertIsNone(ctl.interrupt)

    def test_forced_showtime_with_broken_cue_keeps_the_schedule(self):
        stt = [{"show": "Mine", "from": "23:30", "to": "23:30", "only_when_on": False}]
        r = Rig(self, datetime(2026, 12, 4, 22, 58).astimezone(),
                {"schedule": SCH, "showtimes": stt, "events": [],
                 "shows": {"Mine": {"end": "resume", "cues": [{"preset": "broken", "seconds": 30}]}}})
        ctl = r.ctl
        break_preset(ctl)
        with self.assertLogs(C.log, "WARNING"):
            r.tick(40 * 60, step=5)                              # 23:38
        self.assertIsNone(ctl.interrupt)
        self.assertFalse(ctl.engine.live)
        r.clock.t = datetime(2026, 12, 5, 16, 59).astimezone().timestamp()
        r.tick(120, step=2)                                      # 17:01 next day
        self.assertTrue(ctl.engine.live)

    def test_broken_visitor_pick_is_dropped(self):
        stt = [{"show": "Christmas Eve spectacular", "from": "18:00", "to": "18:00", "only_when_on": True}]
        r = Rig(self, datetime(2026, 12, 10, 17, 30).astimezone(),
                {"schedule": SCH, "showtimes": stt, "events": [],
                 "visitor": {"enabled": True, "token": "t", "presets": ["broken", "warm-white"]}})
        ctl = r.ctl
        break_preset(ctl)
        r.tick(10)
        ctl.vqueue.append({"id": "broken", "client": "a"})
        with self.assertLogs(C.log, "WARNING"):
            r.tick(5)
        self.assertEqual(ctl.vqueue, [])
        self.assertIsNone(ctl.interrupt)
        r.tick(31 * 60, step=2)                                  # 18:01
        self.assertEqual((ctl.interrupt or {}).get("label"), "Show: Christmas Eve spectacular")


if __name__ == "__main__":
    unittest.main()
