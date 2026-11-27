"""Round 13: features that borrow the lights (Match the runs, Bulb check, camera mapping,
diagnostics, forced celebrations, the Fourth-run blink) hand them over cleanly to each other:
nobody switches the house off under someone else, and nobody leaves it on for good."""
import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from test_integration import Rig  # noqa: E402

from goveelights import addrun  # noqa: E402
from goveelights import controller as C  # noqa: E402


def rig(t, when=datetime(2026, 11, 26, 14, 0), extra=None):
    return Rig(t, when.astimezone(), extra or {"schedule": {"enabled": False}})


class _Streamer:
    def __init__(self):
        self.realtime = False
        self.shown = 0

    def enable(self):
        self.realtime = True

    def disable(self):
        self.realtime = False

    def show(self, colors, gradient=True):
        self.realtime = True
        self.shown += 1


class Takeovers(unittest.TestCase):
    def off_rig(self, **kw):
        r = rig(self, **kw)
        r.ctl.play_preset("warm-white", manual=False)
        self.assertFalse(r.ctl.engine.live)
        return r

    # 1 -----------------------------------------------------------------
    def test_mapping_after_a_lights_off_match_keeps_the_house_on(self):
        r = self.off_rig()
        ctl = r.ctl
        self.assertTrue(ctl.match("start", "warm")["took_lights"])
        ctl.map_start()
        r.tick(2)
        self.assertIsNone(ctl.match_state)
        self.assertTrue(ctl.engine.live, "the camera must see the mapping pattern")
        self.assertTrue(ctl.engine.output.started)
        ctl.map_stop()
        self.assertFalse(ctl.engine.live, "the house was off before the colour test")

    # 2 -----------------------------------------------------------------
    def test_match_ending_does_not_release_a_lights_off_bulb_check(self):
        r = self.off_rig()
        ctl = r.ctl
        ctl.bulbcheck.start(ctl.engine.layout.runs[0].mac, "groups")
        self.assertTrue(ctl.engine.output.started)
        with self.assertRaises(ValueError):        # the two are kept apart: no colour test during a check
            ctl.match("start", "warm")
        ctl.match("stop")                          # a stray Stop must not release the check either
        r.tick(1)
        self.assertTrue(ctl.bulbcheck.active)
        self.assertTrue(ctl.engine.output.started, "the check still needs the boxes")
        ctl.bulbcheck.stop("done")
        self.assertFalse(ctl.engine.output.started)
        self.assertFalse(ctl.engine.live)

    def test_forced_celebration_under_a_lights_off_bulb_check(self):
        r = self.off_rig(when=datetime(2026, 11, 29, 14, 0))
        ctl = r.ctl
        ctl.bulbcheck.start(ctl.engine.layout.runs[1].mac, "groups")
        now = r.clock.t
        ctl._pending.append((now, "touchdown", ["#fc4c02", "#0a2343"]))
        ctl._pending_tick(now, force_live=True)
        for _ in range(12):
            r.tick(5)
            ctl.bulbcheck.act("next")
        self.assertIsNone(ctl.interrupt)
        self.assertTrue(ctl.bulbcheck.active)
        self.assertTrue(ctl.engine.output.started, "the check still needs the boxes")

    # 3 -----------------------------------------------------------------
    def test_fourth_run_tools_leave_a_box_the_bulb_check_drives(self):
        r = self.off_rig()
        ctl = r.ctl
        mac = ctl.engine.layout.runs[0].mac
        ctl.engine.output.devices = {m: {} for m in ctl.cfg.devices}
        s = _Streamer()
        ctl.diag.streamers[mac] = s
        ctl.diag.rt = lambda m: s
        ctl.bulbcheck.start(mac, "groups")
        s.realtime = True                       # the engine's streamer drives it
        with self.assertRaises(ValueError):
            ctl.blink_run(mac)
        with self.assertRaises(ValueError):
            addrun.solid(ctl, mac, 5, "#ff0000")
        addrun.release(ctl, mac)
        self.assertTrue(s.realtime, "release must not switch off a box the check drives")
        self.assertEqual(s.shown, 0)

    # 4 -----------------------------------------------------------------
    def test_rebuild_during_a_lights_off_match_still_ends_dark(self):
        r = self.off_rig()
        ctl = r.ctl
        ctl.match("start", "warm")
        ctl.bulbcheck.set_dead(ctl.engine.layout.runs[0].mac, [7])
        r.tick(2)
        ctl.match("stop")
        r.tick(2)
        self.assertFalse(ctl.engine.live)

    def test_rebuild_keeps_lit_lights_lit(self):
        r = rig(self)
        ctl = r.ctl
        ctl.play_preset("warm-white")
        ctl.set_live(True)
        ctl.build_engine()
        self.assertTrue(ctl.engine.live)
        self.assertTrue(ctl.engine.output.started)

    # 5 -----------------------------------------------------------------
    def test_diagnostics_after_a_lights_off_match_ends_dark(self):
        r = self.off_rig()
        ctl = r.ctl
        ctl.match("start", "warm")
        ctl.enter_diag()
        r.tick(2)
        ctl.exit_diag()
        r.tick(2)
        self.assertFalse(ctl.engine.live)

    # 6 -----------------------------------------------------------------
    def test_visitor_picks_wait_for_a_bulb_check(self):
        r = rig(self, when=datetime(2026, 11, 26, 17, 30),
                extra={"schedule": {"enabled": False},
                       "visitor": {"enabled": True, "presets": ["candy-cane-chase", "warm-white"]}})
        ctl = r.ctl
        ctl.play_preset("warm-white")
        ctl.set_live(True)
        ctl.bulbcheck.start(ctl.engine.layout.runs[0].mac, "groups")
        ctl.visitor_pick("candy-cane-chase", "phone-A")
        r.tick(5)
        self.assertIsNone(ctl.interrupt)
        self.assertEqual(len(ctl.vqueue), 1)

    # 7 -----------------------------------------------------------------
    def test_bulb_check_survives_the_end_of_a_match_that_lit_the_house(self):
        r = self.off_rig()
        ctl = r.ctl
        ctl.match("start", "warm")
        ctl.bulbcheck.start(ctl.engine.layout.runs[0].mac, "single")
        r.tick(1)
        ctl.match("stop")
        r.tick(2)
        self.assertTrue(ctl.bulbcheck.active)
        self.assertTrue(ctl.engine.output.started)
        ctl.bulbcheck.stop("done")
        self.assertFalse(ctl.engine.output.started)
        self.assertFalse(ctl.engine.live)

    # 8 -----------------------------------------------------------------
    def test_phone_only_survives_a_bulb_check_rebuild(self):
        r = rig(self)
        ctl = r.ctl
        ctl.play_preset("warm-white")
        ctl.set_live(True)
        ctl.bulbcheck.start(ctl.engine.layout.runs[0].mac, "single", at=5)
        ctl.bulbcheck.act("dark")
        ctl.set_live(False)
        self.assertFalse(ctl.engine.live)
        self.assertTrue(ctl.phone_only)


if __name__ == "__main__":
    unittest.main()
