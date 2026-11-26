"""Browser check of More -> Fourth run at 390 px (tests/ui_fourth_run.js): no JS errors, no
horizontal scroll, 44 px touch targets, and the check / add / key flows against a real app
server on a controller with fake boxes. Skipped when node or Playwright is missing."""
import json
import os
import shutil
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
import test_r10_fourth_run as T  # noqa: E402

from goveelights import addrun  # noqa: E402
from goveelights import controller as C  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def _node_path():
    if not shutil.which("node") or not shutil.which("npm"):
        return None
    try:
        root = subprocess.run(["npm", "root", "-g"], capture_output=True, text=True, timeout=20).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return root if root and (Path(root) / "playwright").exists() else None


NODE_PATH = _node_path()


@unittest.skipUnless(NODE_PATH, "node + Playwright not installed")
class FourthRunInTheBrowser(unittest.TestCase):
    def test_phone_width_flow(self):
        from goveelights.app_server import Server, make_handler
        from goveelights.auth import Auth
        for p in (mock.patch.object(C.Controller, "CLOUD_THREAD", False), mock.patch.object(addrun, "_spawn")):
            p.start()
            self.addCleanup(p.stop)
        case = T.ControllerFourthRun("test_server_endpoints")
        case.addCleanup = self.addCleanup
        r, ctl = case.make()
        ctl.cloud_api.transport = T.FakeHTTP(T.DEVS)
        srv = Server(("127.0.0.1", 0), make_handler(ctl, lambda: None, Auth(Path(r.tmp) / "auth.json")))
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        shot = os.environ.get("FOURTH_RUN_SHOT", "")
        res = subprocess.run(["node", str(ROOT / "tests" / "ui_fourth_run.js"),
                              f"http://127.0.0.1:{srv.server_address[1]}/", shot,
                              os.environ.get("FOURTH_RUN_SHEET_SHOT", "")],
                             capture_output=True, text=True, timeout=120, env={**os.environ, "NODE_PATH": NODE_PATH})
        self.assertEqual(res.returncode, 0, res.stderr)
        out = json.loads(res.stdout.strip().splitlines()[-1])
        self.assertEqual(out["errors"], [], out)
        self.assertEqual(out["overflow"], [])
        self.assertEqual(out["small"], [])
        self.assertEqual(out["steps"], ["card", "check", "add sheet", "added", "key"])
        self.assertIn("ignores commands", out["verdict"])
        self.assertEqual((out["name"], out["join"]), ("Second roofline", "Lower roofline"))
        # the check found it ignoring commands: the sheet starts on the cloud, warns on Wi-Fi
        self.assertEqual((out["defaultOut"], out["lanWarn"], out["findDisabled"]), ("cloud", True, True))
        self.assertEqual(out["follow"], "Lower roofline")     # suggested; the owner can pick "Leave it dark"
        self.assertGreaterEqual(out["followH"], 44)
        self.assertIn("Second roofline", out["devInfo"])
        self.assertIn("7890", out["cloudText"])
        self.assertNotIn("abcd1234-ef56", out["cloudText"])
        self.assertEqual(ctl.cfg.device(T.NEW)["pixel_count"], 64)


if __name__ == "__main__":
    unittest.main()
