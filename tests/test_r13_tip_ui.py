"""Browser check of "Top of the Peak" in the Add a run sheet (tests/ui_tip.js) on an iPhone-
sized screen, against a real controller driving fake boxes (tools/fake_govee.py --counts
93,108,60,108: Eaves, Peak, Accent and the Tip, which now answers LAN commands). No JS
errors, no horizontal scroll, 44 px touch targets; the Tip is added on top of the Peak and
taken out again. Skipped when node or Playwright is missing, or not on Linux (the fakes
bind 127.0.0.N). TIP_SHOTS=<dir> saves the sheet and the card there."""
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from test_r10_ui_playwright import NODE_PATH  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OWNER = ROOT / "layouts" / "house.json"
TIP = "AA:BB:CC:DD:EE:FF:00:03"            # tools/fake_govee.py names device 4 like this


@unittest.skipUnless(NODE_PATH, "node + Playwright not installed")
@unittest.skipUnless(sys.platform.startswith("linux"), "the fake boxes bind 127.0.0.N")
@unittest.skipUnless(OWNER.exists(), "the owner's layout is not in this copy")
class TipInTheBrowser(unittest.TestCase):
    def test_add_the_tip_on_a_phone(self):
        from goveelights import controller as C
        from goveelights.app_server import Server, make_handler
        from goveelights.auth import Auth
        from goveelights.config import Config
        pre = set(threading.enumerate())
        # (runs last) wait for the threads this test started, e.g. the 4 s blink, so none
        # is still running when the next test patches the controller's clock
        self.addCleanup(lambda: [t.join(6) for t in set(threading.enumerate()) - pre])
        base = 40 + os.getpid() % 180                     # a range of its own (other runs may be going)
        fake = subprocess.Popen([sys.executable, str(ROOT / "tools" / "fake_govee.py"), "--counts", "93,108,60,108",
                                 "--quiet", "--base", str(base)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True)
        self.addCleanup(fake.stdout.close)
        self.addCleanup(fake.wait, 5)
        self.addCleanup(fake.terminate)
        line = fake.stdout.readline()
        self.assertIn("fake devices", line)
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        cfg = Config(tmp / "govee_config.json")
        cfg.import_layout(OWNER, create_missing=True)
        for k, d in enumerate(sorted(cfg.devices.values(), key=lambda d: d.get("order", 0))):
            d["ip"] = f"127.0.0.{base + k}"
        cfg.upsert_discovered({"device": TIP, "sku": "H705E", "ip": f"127.0.0.{base + 3}",
                               "source_ip": f"127.0.0.{base + 3}", "wifiVersionSoft": "1.01.17"})
        cfg.devices[TIP]["name"] = "Tip"
        cfg.data["schedule"]["enabled"] = False
        cfg.save()
        before = copy.deepcopy(cfg.data)
        ctl = C.Controller(cfg, presets=C.PresetStore(ROOT / "presets", user=tmp / "user"))
        self.addCleanup(ctl.shutdown)
        srv = Server(("127.0.0.1", 0), make_handler(ctl, lambda: None, Auth(tmp / "auth.json")))
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        shots = os.environ.get("TIP_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = [str(Path(shots) / "tip-sheet.png"), str(Path(shots) / "tip-card.png")] if shots else []
        seen = {}

        def watch():                                     # what the engine had while the Tip was in
            t0 = time.time()
            while time.time() - t0 < 100 and not seen.get("stop"):
                L = ctl.engine.layout
                if L.run("Tip") is not None and "peak" not in seen:
                    seen["peak"] = len(L.indices("Peak"))
                    seen["tip"] = len(L.run("Tip").pixels)
                    seen["gable"] = len(L.indices("Whole front gable"))
                time.sleep(0.05)
        th = threading.Thread(target=watch, daemon=True)
        th.start()
        res = subprocess.run(["node", str(ROOT / "tests" / "ui_tip.js"), f"http://127.0.0.1:{srv.server_address[1]}/",
                              *args], capture_output=True, text=True, timeout=150,
                             env={**os.environ, "NODE_PATH": NODE_PATH})
        seen["stop"] = True
        th.join(2)
        self.assertEqual(res.returncode, 0, res.stderr)
        out = json.loads(res.stdout.strip().splitlines()[-1])
        self.assertEqual(out["errors"], [], out)
        self.assertEqual(out["overflow"], [], out)
        self.assertEqual(out["small"], [], out)
        self.assertEqual(out["steps"], ["card", "check", "top of the peak", "added", "taken out"], out)
        self.assertIn("answers commands", out["verdict"])                 # the fake Tip answers now
        self.assertTrue(out["tipHiddenFirst"])
        self.assertEqual((out["tipHidden"], out["joinHidden"]), (False, True))
        self.assertEqual((out["name"], out["visible"], out["rows"]), ("Tip", "12", "7, 4, 1"))
        self.assertIn("bulbs 0–6, then bulbs 7–10, then bulb 11 (the very top)", out["rowsHelp"])
        self.assertIn("bulbs 0–5, then bulbs 6–10, then bulb 11", out["rowsHelp2"])
        self.assertIn("part of the Peak", out["joins"])
        self.assertIn("Whole front gable", out["joins"])
        self.assertEqual(out["flip"], [["Where the Peak's top row ends", True], ["The other end", False]])
        self.assertEqual(out["flipped"], "The other end")
        self.assertTrue(all(h >= 44 for h in out["inputH"]), out["inputH"])
        self.assertIn("108 bulbs (12 on the gable)", out["cardText"])
        self.assertIn("Top of the Peak", out["cardText"])
        self.assertEqual(seen.get("peak"), 120, seen)
        self.assertEqual((seen.get("tip"), seen.get("gable")), (12, 144), seen)
        # taken out: the config is as it was (the check only noted what it found)
        after = copy.deepcopy(ctl.cfg.data)
        for d in (before, after):
            for dev in d["devices"].values():
                dev.pop("diag", None)
                dev.pop("firmware", None)
        self.assertEqual(after["zones"], before["zones"])
        self.assertIsNone(ctl.engine.layout.run("Tip"))
        self.assertEqual(ctl.cfg.devices[TIP].get("pixel_count"), None)


if __name__ == "__main__":
    unittest.main()
