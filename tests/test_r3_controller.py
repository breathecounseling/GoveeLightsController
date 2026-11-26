"""Round-3 controller regressions: playlist vs interrupts, New Year's ownership, loop
showtimes, event parties at lights-off, missing show presets, game polling blips and
stale weather. Driven with the fake-clock rig from test_integration."""
import sys
import unittest
from datetime import datetime
from unittest import mock

sys.path.insert(0, "tests")
from test_integration import Rig  # noqa: E402

from goveelights import controller as C  # noqa: E402
from goveelights import shows  # noqa: E402

NYE = "New Year's Eve countdown"
OFF = {"enabled": False}


def midnight():
    return datetime(2027, 1, 1, 0, 0).astimezone().timestamp()


class PlaylistTests(unittest.TestCase):
    def test_manual_playlist_ends_the_interrupt(self):
        r = Rig(self, datetime(2026, 12, 10, 19, 0).astimezone(),
                {"schedule": OFF, "playlists": {"Xmas": {"items": [{"preset": "christmas-tree", "minutes": 5},
                                                                    {"preset": "candy-cane", "minutes": 5}]}}})
        ctl = r.ctl
        ctl.play_preset("warm-white")
        ctl.set_live(True)
        self.assertTrue(ctl.start_interrupt(ctl.presets.get("candy-cane"), 60, "Visitor pick: Candy cane"))
        ctl.play_playlist("Xmas")
        self.assertIsNone(ctl.interrupt)
        r.tick(70, schedule=False)
        self.assertEqual(ctl.playing["kind"], "playlist")
        self.assertEqual(ctl.playlist["name"], "Xmas")


class NewYearCutInTests(unittest.TestCase):
    def rig(self, h, m, **extra):
        r = Rig(self, datetime(2026, 12, 31, h, m).astimezone(), {"schedule": OFF, **extra})
        r.ctl.play_preset("warm-white")
        r.ctl.set_live(True)
        return r

    def test_celebration_during_the_show_still_gets_midnight(self):
        r = self.rig(23, 55)
        ctl = r.ctl
        self.assertTrue(ctl.play_show(NYE, manual=False))
        r.tick(120, schedule=False)                              # 23:57
        d, secs = shows.celebration("touchdown", ["#fc4c02", "#0a2343"])
        self.assertTrue(ctl.start_interrupt(d, secs, d["name"]))
        labels = set()
        while r.clock.t < midnight() + 5:
            r.tick(1, schedule=False)
            labels.add((ctl.interrupt or {}).get("label"))
        self.assertIn("New Year countdown", labels)
        self.assertIn("New Year", labels)

    def test_show_reaching_midnight_still_owns_it(self):
        r = self.rig(23, 55)
        ctl = r.ctl
        ctl.play_show(NYE, manual=False)
        labels = set()
        while r.clock.t < midnight() + 30:
            r.tick(1, schedule=False)
            labels.add((ctl.interrupt or {}).get("label"))
        self.assertEqual(labels, {f"Show: {NYE}"})


class EventAtLightsOffTests(unittest.TestCase):
    def test_new_year_party_survives_midnight_off_time(self):
        sch = {"enabled": True, "on": "time", "on_time": "17:00", "off_time": "00:00",
               "default": {"preset": "warm-white"}, "rules": []}
        r = Rig(self, datetime(2026, 12, 31, 23, 58).astimezone(), {"schedule": sch})
        ctl = r.ctl
        r.tick(30)
        self.assertTrue(ctl.engine.live)
        r.tick(midnight() + 120 - r.clock.t)
        self.assertEqual((ctl.interrupt or {}).get("label"), "New Year")
        self.assertTrue(ctl.engine.live)
        r.tick(9 * 60)                                            # 00:11: the party is over, lights off
        self.assertIsNone(ctl.interrupt)
        self.assertFalse(ctl.engine.live)


class LoopShowtimeTests(unittest.TestCase):
    def test_forced_loop_showtime_ends(self):
        sch = {"enabled": True, "on": "time", "on_time": "17:00", "off_time": "23:00",
               "default": {"preset": "warm-white"}, "rules": []}
        stt = [{"show": "Winter wonderland loop", "from": "23:30", "to": "23:30", "every": 60,
                "only_when_on": False, "enabled": True}]
        r = Rig(self, datetime(2026, 12, 10, 22, 50).astimezone(), {"schedule": sch, "showtimes": stt})
        ctl = r.ctl
        r.tick(20 * 60, step=2)                                  # 23:10: off
        self.assertFalse(ctl.engine.live)
        r.tick(25 * 60, step=2)                                  # 23:35: the show
        self.assertTrue(ctl.engine.live)
        self.assertIn("show", ctl.interrupt)
        r.tick(3 * 3600, step=5)                                 # 02:35: long finished, lights off
        self.assertIsNone(ctl.interrupt)
        self.assertFalse(ctl.engine.live)


class MissingPresetShowtimeTests(unittest.TestCase):
    def test_missing_first_cue_is_skipped_once(self):
        stt = [{"show": "Quick", "from": "17:00", "to": "23:00", "every": 15, "only_when_on": True}]
        r = Rig(self, datetime(2026, 12, 10, 19, 14).astimezone(),
                {"schedule": OFF, "showtimes": stt,
                 "shows": {"Quick": {"end": "resume", "cues": [{"preset": "gone-preset", "seconds": 5}]}}})
        ctl = r.ctl
        ctl.play_preset("warm-white")
        ctl.set_live(True)
        with self.assertLogs(C.log, "WARNING"):
            r.tick(130, schedule=False)                          # raises out of _tick_once before the fix
        self.assertEqual(len(ctl._showtimes_done), 1)


class GamePollTests(unittest.TestCase):
    def test_error_mid_game_retries_soon_and_keeps_cadence(self):
        r = Rig(self, datetime(2026, 12, 10, 19, 0).astimezone(),
                {"schedule": OFF, "game": {"enabled": True, "teams": [{"league": "nfl", "team": "DEN"}],
                                            "scoreboard_every": 10, "delay": 30}})
        ctl = r.ctl
        key = "nfl:DEN"
        ctl.games[key] = {"id": "1", "state": "in", "score": 7, "opp_score": 0, "team": "DEN",
                          "colors": ["#fc4c02"], "polled": r.clock.t}
        ctl._scoreboard_tick(r.clock.t)
        at = ctl._scoreboard_at
        self.assertIsNotNone(at)
        with mock.patch.object(C.live, "fetch_json", side_effect=OSError("timeout")):
            self.assertEqual(ctl._poll_game({"league": "nfl", "team": "DEN"}, key), 30)
        ctl._scoreboard_tick(r.clock.t + 1)
        self.assertEqual(ctl._scoreboard_at, at)
        ctl.games[key] = {"state": "post", "id": "1", "score": 7, "opp_score": 0}
        ctl._scoreboard_tick(r.clock.t + 2)
        self.assertIsNone(ctl._scoreboard_at)


class StaleWeatherTests(unittest.TestCase):
    def test_old_conditions_are_dropped(self):
        r = Rig(self, datetime(2026, 12, 10, 19, 0).astimezone(), {"schedule": OFF, "weather": {"enabled": True}})
        ctl = r.ctl
        ctl.weather = {"snow": True, "storm": True, "at": r.clock.t - 60}
        ctl._apply_weather()
        self.assertIn("snow", ctl.engine.overlays)
        ctl.weather = {"snow": True, "storm": True, "at": r.clock.t - 5 * 3600, "error": "timeout"}
        ctl._apply_weather()
        self.assertNotIn("snow", ctl.engine.overlays)
        self.assertNotIn("storm", ctl.engine.overlays)


if __name__ == "__main__":
    unittest.main()
