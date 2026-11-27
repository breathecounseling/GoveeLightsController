"""Round 13 (ui lens): the bulb check and Match the runs never share the lights; a phone that
only looks at the app never ends another phone's colour test; Open on the bulb-check banner
shows the walk on a second phone; a fourth-run check survives a peek at another tab."""
import json
import os
import subprocess
import sys
import threading
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from test_integration import Rig  # noqa: E402
from test_r12_calibration import NODE_PATH  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


class BulbCheckAndMatchDontShare(unittest.TestCase):
    def rig(self):
        r = Rig(self, datetime(2026, 11, 26, 14, 0).astimezone(), {"schedule": {"enabled": False}})
        return r, r.ctl

    def test_match_refused_while_the_bulb_check_runs(self):
        r, ctl = self.rig()
        e = ctl.engine
        self.assertFalse(e.live)
        ctl.bulbcheck.start(e.layout.runs[0].mac)
        self.assertTrue(e.output.started)
        with self.assertRaises(ValueError) as cm:
            ctl.match("start", "red")
        self.assertIn("bulb check", str(cm.exception))
        self.assertIsNone(ctl.match_state)
        ctl.match("stop")                                   # a stray stop (e.g. leaving More)
        self.assertTrue(e.output.started)                   # the boxes are still the check's
        self.assertTrue(ctl.bulbcheck.active)
        ctl.bulbcheck.stop()
        self.assertFalse(e.output.started)
        self.assertFalse(e.live)

    def test_bulb_check_ends_a_running_colour_test_first(self):
        r, ctl = self.rig()
        e = ctl.engine
        self.assertTrue(ctl.match("start", "red")["took_lights"])
        self.assertTrue(e.live)
        ctl.bulbcheck.start(e.layout.runs[1].mac)
        self.assertIsNone(ctl.match_state)
        self.assertNotIn("match", e.overlays)
        self.assertTrue(e.output.started)
        ctl.match("stop")
        self.assertTrue(ctl.bulbcheck.active)               # not silently ended
        self.assertTrue(e.output.started)                   # and Peak not released
        ctl.bulbcheck.stop()
        self.assertFalse(e.output.started)
        self.assertFalse(e.live)                            # off before the colour test: off again

    def test_lit_house_keeps_playing_through_both(self):
        r, ctl = self.rig()
        ctl.play_preset("warm-white")
        ctl.set_live(True)
        ctl.match("start", "red")
        ctl.bulbcheck.start(ctl.engine.layout.runs[0].mac)
        ctl.bulbcheck.stop()
        self.assertTrue(ctl.engine.live)
        self.assertTrue(ctl.engine.output.started)
        self.assertEqual(ctl.playing["id"], "warm-white")


@unittest.skipUnless(NODE_PATH, "node + Playwright not installed")
class TwoPhonesInTheBrowser(unittest.TestCase):
    def test_second_phone(self):
        from goveelights.app_server import Server, make_handler
        from goveelights.auth import Auth
        r = Rig(self, datetime(2026, 11, 26, 14, 0).astimezone(), {"schedule": {"enabled": False}})
        ctl = r.ctl
        ctl.play_preset("warm-white")
        ctl.set_live(True)
        srv = Server(("127.0.0.1", 0), make_handler(ctl, lambda: None, Auth(Path(r.tmp) / "auth.json")))
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        res = subprocess.run(["node", str(ROOT / "tests" / "ui_r13_fixes.js"), f"http://127.0.0.1:{srv.server_address[1]}/"],
                             capture_output=True, text=True, timeout=180, env={**os.environ, "NODE_PATH": NODE_PATH})
        self.assertEqual(res.returncode, 0, res.stderr)
        o = json.loads(res.stdout.strip().splitlines()[-1])
        self.assertEqual(o["errors"], [], o)
        # a phone that only looked never ends another phone's test
        self.assertEqual(o["matchAfterA"], {"color": "red"}, o)
        self.assertEqual(o["matchAfterBTab"], {"color": "red"}, o)
        self.assertEqual(o["matchAfterBLeave"], {"color": "red"}, o)
        self.assertNotEqual(o["aNote"], "", o)
        self.assertIsNone(o["matchAfterAStop"], o)
        # banner Open on a phone that never opened More shows the walk, not "Done"
        self.assertNotIn("has ended", o["bOpen"], o)
        self.assertIn("bulbs 11", o["bOpen"], o)
        # while the bulb check runs, the colour test swatches are off
        self.assertTrue(o["swDisabled"], o)
        self.assertFalse(o["swDisabledAfter"], o)
        # fourth run: the check's answer survives a peek at another tab
        self.assertEqual(o["frBtnBack"], "Checking… (about 8 s)", o)
        self.assertIn("Yes, it turned green", o["frOut"], o)
        self.assertEqual(o["frAnswered"], "Good: it answers.", o)
        self.assertEqual(o["frReleased"], 1, o)


if __name__ == "__main__":
    unittest.main()
