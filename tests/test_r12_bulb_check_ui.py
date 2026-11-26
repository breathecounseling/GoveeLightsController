"""Browser check of More -> Bulb check at 390 px (tests/ui_bulb_check.js): no JS errors, no
horizontal scroll, 44 px touch targets, and the walk / mark / colour test / re-hung / gap
check flows against a real app server. Skipped when node or Playwright is missing.
BULB_CHECK_SHOTS=<dir> also saves screenshots there."""
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
from test_r10_ui_playwright import NODE_PATH  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


@unittest.skipUnless(NODE_PATH, "node + Playwright not installed")
class BulbCheckInTheBrowser(unittest.TestCase):
    def test_phone_width_flow(self):
        from goveelights.app_server import Server, make_handler
        from goveelights.auth import Auth
        r = Rig(self, datetime(2026, 11, 26, 17, 0), {"schedule": {"enabled": False}})
        ctl = r.ctl
        ctl.play_preset("warm-white", fade=0)
        ctl.set_live(True)
        srv = Server(("127.0.0.1", 0), make_handler(ctl, lambda: None, Auth(Path(r.tmp) / "auth.json")))
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        res = subprocess.run(["node", str(ROOT / "tests" / "ui_bulb_check.js"),
                              f"http://127.0.0.1:{srv.server_address[1]}/", os.environ.get("BULB_CHECK_SHOTS", "")],
                             capture_output=True, text=True, timeout=180, env={**os.environ, "NODE_PATH": NODE_PATH})
        self.assertEqual(res.returncode, 0, res.stderr)
        out = json.loads(res.stdout.strip().splitlines()[-1])
        self.assertEqual(out["errors"], [], out)
        self.assertEqual(out["overflow"], [])
        self.assertEqual(out["small"], [])
        self.assertEqual(out["steps"], ["card", "walk", "marked", "back", "sweep", "gap", "remap", "banner", "ended"])
        self.assertIn("bulb check is on the lights", out["texts"]["banner"])
        self.assertIn("Bulb 11", out["texts"]["dead"])
        self.assertIn("bulbs 11–20", out["texts"]["rehung"])
        self.assertIn("alternate white and amber", out["texts"]["gap"])
        self.assertIsNone(out["texts"]["status"])                 # the camera took over: the check ended
        eaves = ctl.engine.layout.run("Eaves")
        self.assertEqual(ctl.cfg.devices[eaves.mac]["dead"], [10])
        self.assertEqual(ctl.cfg.devices[eaves.mac]["rehung"], [[10, 19]])
        self.assertNotIn(10, [p.local for p in eaves.pixels])    # rebuilt once the check ended
        self.assertIsNone(ctl.engine.takeover)
        self.assertFalse(ctl.bulbcheck.active)


if __name__ == "__main__":
    unittest.main()
