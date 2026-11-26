"""Round 11: a full-season rehearsal on the owner-like setup (this house's three runs,
sunset on/off at 11 pm in Denver, DEN followed, the holiday calendar applied as the app
applies it, visitor page and game day on), Thanksgiving 2026 to 2 January 2027 on a fake
clock. Each test here failed before its fix."""
import shutil
import sys
import tempfile
import types
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, "tests")
from test_integration import Rig  # noqa: E402
from test_tonight import _Denver  # noqa: E402

from goveelights import controller as C  # noqa: E402
from goveelights.config import Config  # noqa: E402
from goveelights.legacy_defaults import LEGACY_DEFAULTS  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
NYE = "New Year's Eve countdown"
ROOFLINE = "Christmas on the roofline"


def owner_rig(test, when: datetime, tmp: str | None = None) -> Rig:
    """The owner's setup at `when`, with the holiday calendar applied (factory rules
    switched off, as the preview pre-ticks them)."""
    first = tmp is None
    if first:
        tmp = tempfile.mkdtemp()
        test.addCleanup(shutil.rmtree, tmp, True)
        cfg = Config(Path(tmp) / "cfg.json")
        cfg.import_layout(ROOT / "layouts/house.json", create_missing=True)
        for k, d in enumerate(cfg.devices.values()):
            d["ip"] = f"10.0.0.{k + 10}"
        cfg.data["schedule"].update(enabled=True, lat=LEGACY_DEFAULTS["schedule.lat"],
                                    lon=LEGACY_DEFAULTS["schedule.lon"], on="sunset", off_time="23:00")
        cfg.data["game"].update(enabled=True, teams=[{"league": "nfl", "team": "DEN"}])
        cfg.data["visitor"]["enabled"] = True
        cfg.save()
    r = Rig(test, when.astimezone(), {}, tmp=tmp)
    if first:
        dis = [s["index"] for s in r.ctl.calendar_preview()["shadows"] if s["factory"]]
        r.ctl.calendar_apply(dis, enable=True)
    return r


def label(ctl):
    return (ctl.interrupt or {}).get("label")


class ReminderAfterShowTests(_Denver, unittest.TestCase):
    def test_no_days_to_go_reminder_the_moment_a_show_ends(self):
        """From 1 December the '24 days to Christmas' reminder comes every 15 minutes.
        One that fell due during the 6 pm show (or a celebration, a scoreboard, a visitor
        pick) used to play the second it ended: show, one second of the program, then the
        reminder. It now waits a few minutes after anything else on the house."""
        r = owner_rig(self, datetime(2026, 12, 1, 17, 50))
        ctl = r.ctl
        r.tick(60)
        self.assertTrue(ctl.engine.live)
        r.tick(datetime(2026, 12, 1, 18, 0, 30).timestamp() - r.clock.t)
        self.assertEqual(label(ctl), f"Show: {ROOFLINE}")
        ctl._last_reminder = r.clock.t - 14.5 * 60                # due at 18:01, mid-show
        while label(ctl) == f"Show: {ROOFLINE}":
            r.tick(1)
        ended = r.clock.t
        seen = []
        while r.clock.t < ended + 10 * 60:
            r.tick(1)
            if (label(ctl) or "").endswith("days to Christmas") and not seen:
                seen.append(r.clock.t - ended)
        self.assertTrue(seen, "the reminder still comes")
        self.assertGreaterEqual(seen[0], 170)                     # not seconds after the show


class NewYearRestartTests(_Denver, unittest.TestCase):
    def test_a_restart_during_the_new_year_show_starts_it_again_in_time_for_midnight(self):
        """The PC restarted at 11:57 pm on New Year's Eve: the 11:55 showtime's minute had
        passed, so the five-minute show was gone and only the plain event countdown ran.
        A show whose countdown is pinned to a moment still ahead is started late (its
        build-up repeats until the countdown), so midnight still gets the show."""
        r = owner_rig(self, datetime(2026, 12, 31, 23, 50))
        r.tick(60)
        self.assertTrue(r.ctl.engine.live)
        r2 = owner_rig(self, datetime(2026, 12, 31, 23, 57), tmp=r.tmp)    # the restart
        ctl = r2.ctl
        r2.tick(15)
        self.assertEqual(ctl.status()["show"], NYE)
        midnight = datetime(2027, 1, 1).astimezone().timestamp()
        r2.tick(midnight - 2 - r2.clock.t)
        self.assertEqual(ctl.status()["show"], NYE)
        r2.tick(4)
        self.assertEqual(ctl.status()["show"], NYE)
        self.assertNotEqual(label(ctl), "New Year")                   # the show owns midnight
        self.assertIn("grand", (ctl.engine.scene.name or "").lower())

    def test_not_after_midnight_or_before_its_time(self):
        r = owner_rig(self, datetime(2027, 1, 1, 0, 5))
        r.tick(30)
        self.assertIsNone(r.ctl.status()["show"])
        r = owner_rig(self, datetime(2026, 12, 31, 23, 40))
        r.tick(60)
        self.assertIsNone(r.ctl.status()["show"])


class ClockBackTests(_Denver, unittest.TestCase):
    def test_a_clock_set_back_does_not_freeze_a_celebration_or_the_rotation(self):
        """Windows corrected a fast clock by an hour mid-evening: every 'until' and 'next'
        time stayed an hour ahead, so a 90-second celebration held the house for an hour
        and a half and the rotation stopped changing."""
        r = owner_rig(self, datetime(2026, 11, 28, 19, 20))
        ctl = r.ctl
        r.tick(30)
        self.assertEqual(ctl.playing["kind"], "playlist")
        ctl._pending.append((r.clock.t, "win", ["#0a2343", "#fc4c02"]))
        ctl._pending_tick(r.clock.t)
        self.assertEqual(label(ctl), "Victory!")
        r.clock.t -= 3600                                       # the clock is set back an hour
        r.tick(5 * 60)
        self.assertIsNone(ctl.interrupt)
        item = ctl.playing.get("item")
        r.tick(9 * 60)
        self.assertNotEqual(ctl.playing.get("item"), item)

    def test_the_tick_loop_keeps_checking_the_schedule_after_a_clock_set_back(self):
        """The 10-second schedule check compared with the last check's clock time: set
        back an hour, it did not run for an hour (the lights stayed on past 11 pm)."""
        r = owner_rig(self, datetime(2026, 11, 28, 22, 50))
        ctl = r.ctl
        calls, clock = [], r.clock
        steps = iter([0] * 30 + [-3600] + [0] * 30)

        class Stop:
            def wait(self, s):
                try:
                    clock.t += s + next(steps)
                except StopIteration:
                    return True
                return False
        ctl._stop = Stop()
        ctl._schedule_tick = lambda: calls.append(clock.t)
        ctl._tick_once = lambda now: None
        ctl.diag = types.SimpleNamespace(scan=lambda: None)
        ctl._tick_loop()
        after = [t for t in calls if t < datetime(2026, 11, 28, 22, 0).timestamp()]
        self.assertGreaterEqual(len(after), 2)

    def test_game_polls_continue_after_a_clock_set_back(self):
        r = owner_rig(self, datetime(2026, 11, 27, 13, 30))
        ctl = r.ctl
        clock, polls = r.clock, []
        steps = iter([0] * 20 + [-3600] + [0] * 400)

        class Stop:
            def wait(self, s):
                try:
                    clock.t += s + next(steps)
                except StopIteration:
                    return True
                return False
        ctl._stop = Stop()
        ctl._poll_game = lambda t, key: polls.append(clock.t) or 15
        ctl._live_loop()
        back = datetime(2026, 11, 27, 13, 0).timestamp()
        self.assertGreaterEqual(len([t for t in polls if t < back]), 10)


    def test_a_clock_set_back_during_the_midnight_countdown_keeps_the_finale_on_midnight(self):
        """A time sync 30 s before midnight moved the pinned countdown's end back with the
        other timers: the grand finale started 29 s early, digits still reading 30."""
        r = owner_rig(self, datetime(2026, 12, 31, 23, 54, 50))
        ctl = r.ctl
        midnight = datetime(2027, 1, 1).astimezone().timestamp()
        r.tick(midnight - 30 - r.clock.t)
        st = ctl.interrupt["show"]
        self.assertEqual(st["idx"], st["anchor_idx"])
        r.clock.t -= 30
        fin = None
        while r.clock.t < midnight + 5 and fin is None:
            r.tick(1)
            if "grand" in (ctl.engine.scene.name or "").lower():
                fin = r.clock.t - midnight
        self.assertIsNotNone(fin)
        self.assertGreaterEqual(fin, -1)

    def test_a_clock_set_back_keeps_an_event_countdown_on_its_moment(self):
        r = owner_rig(self, datetime(2026, 12, 31, 23, 50))
        ctl = r.ctl
        ctl.cfg.data["showtimes"] = []
        midnight = datetime(2027, 1, 1).astimezone().timestamp()
        r.tick(midnight - 20 - r.clock.t)
        self.assertTrue(ctl.interrupt.get("event"))
        r.clock.t -= 10
        r.tick(1)
        self.assertAlmostEqual(ctl.interrupt["until"] - midnight, 0.5, delta=0.01)

    def test_a_clock_set_back_during_a_celebration_does_not_freeze_the_paused_show(self):
        """The show a touchdown paused resumed with its cue time still an hour ahead and
        sat on one cue for the rest of the evening."""
        r = owner_rig(self, datetime(2026, 11, 28, 17, 59, 30))
        ctl = r.ctl
        r.tick(60)
        self.assertEqual(label(ctl), f"Show: {ROOFLINE}")
        ctl._pending.append((r.clock.t, "td", ["#0a2343", "#fc4c02"]))
        ctl._pending_tick(r.clock.t)
        self.assertTrue(ctl.interrupt.get("resume"))
        r.tick(5)
        r.clock.t -= 3600
        r.tick(20)
        self.assertEqual(label(ctl), f"Show: {ROOFLINE}")      # back in the show
        scenes = set()
        for _ in range(120):
            r.tick(1)
            scenes.add(ctl.engine.scene.name)
        self.assertGreater(len(scenes), 1)                    # its cues keep changing
        r.tick(10 * 60)
        self.assertIsNone(ctl.interrupt)                      # and it ends, handing the house back


class RebuildKeepsDimTests(_Denver, unittest.TestCase):
    def test_adding_a_run_late_in_the_evening_keeps_the_dim_and_the_blackout(self):
        """Adding the fourth run (or any engine rebuild) put the house back to full
        brightness until the next schedule check, and dropped a Blackout."""
        r = owner_rig(self, datetime(2026, 12, 5, 22, 30))
        ctl = r.ctl
        r.tick(20)
        ctl.engine.dim = 0.5
        ctl.engine.blackout = True
        ctl.build_engine()
        self.assertEqual(ctl.engine.dim, 0.5)
        self.assertTrue(ctl.engine.blackout)


class ChristmasEvePlanTests(_Denver, unittest.TestCase):
    def test_the_plan_says_reminders_stop_when_the_loop_show_starts(self):
        """Christmas Eve's Tonight card said '1 day to Christmas' every 15 minutes while
        the lights are on, but Santa's coming takes the house from 5:15 pm until lights-off
        and no reminder plays over it."""
        r = owner_rig(self, datetime(2026, 12, 24, 12, 0))
        p = r.ctl.tonight(password_set=True)
        rem = [x for x in p["timeline"] if x["label"].startswith("Reminders")]
        self.assertEqual(len(rem), 1)
        self.assertIn("until 5:15 pm", rem[0]["detail"])
        self.assertIn("Santa's coming", rem[0]["detail"])
        # once Santa's coming is playing, no reminder line comes back
        for hh, mm in ((17, 20), (19, 0)):
            r = owner_rig(self, datetime(2026, 12, 24, 16, 0))
            r.tick(datetime(2026, 12, 24, hh, mm).astimezone().timestamp() - r.clock.t)
            self.assertTrue(r.ctl.interrupt["show"]["loop"])
            rem = [x for x in r.ctl.tonight(password_set=True)["timeline"] if x["label"].startswith("Reminders")]
            self.assertEqual(rem, [], (hh, mm))
        # at 5 pm the one reminder still due before 5:15 is listed
        r = owner_rig(self, datetime(2026, 12, 24, 16, 0))
        r.tick(datetime(2026, 12, 24, 17, 0).astimezone().timestamp() - r.clock.t)
        rem = [x for x in r.ctl.tonight(password_set=True)["timeline"] if x["label"].startswith("Reminders")]
        self.assertEqual(len(rem), 1)
        self.assertIn("until 5:15 pm", rem[0]["detail"])
        # an ordinary December night keeps the plain wording
        r = owner_rig(self, datetime(2026, 12, 10, 12, 0))
        rem = [x for x in r.ctl.tonight(password_set=True)["timeline"] if x["label"].startswith("Reminders")]
        self.assertNotIn("until", rem[0]["detail"])


if __name__ == "__main__":
    unittest.main()
