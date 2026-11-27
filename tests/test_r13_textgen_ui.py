"""Browser check of Play -> "Words on the Peak" (tests/ui_textgen.js) on an iPhone-sized
screen, against a real controller driving fake boxes (tools/fake_govee.py --counts
93,108,60: Eaves, Peak and Accent): type a phrase, the preview, Single letter / Marquee,
a colour chip, Play now, Save as preset, and the "Colours shifted - Reset" chip. No JS
errors, no horizontal scroll, 44 px touch targets. Skipped when node or Playwright is
missing, or not on Linux (the fakes bind 127.0.0.N). TEXT_SHOTS=<dir> saves the card."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from test_r10_ui_playwright import NODE_PATH  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OWNER = ROOT / "layouts" / "house.json"


@unittest.skipUnless(NODE_PATH, "node + Playwright not installed")
@unittest.skipUnless(sys.platform.startswith("linux"), "the fake boxes bind 127.0.0.N")
@unittest.skipUnless(OWNER.exists(), "the owner's layout is not in this copy")
class WordsOnThePeakInTheBrowser(unittest.TestCase):
    def test_type_preview_play_save_on_a_phone(self):
        from goveelights import controller as C
        from goveelights.app_server import Server, make_handler
        from goveelights.auth import Auth
        from goveelights.config import Config
        pre = set(threading.enumerate())
        self.addCleanup(lambda: [t.join(6) for t in set(threading.enumerate()) - pre])
        base = 20 + (os.getpid() * 7) % 200                # a range of its own (other runs may be going)
        house = sorted(json.loads(OWNER.read_text())["devices"].items(), key=lambda kv: kv[1].get("order", 0))
        macs = ",".join(m for m, _ in house)
        fake = subprocess.Popen([sys.executable, str(ROOT / "tools" / "fake_govee.py"), "--counts", "93,108,60",
                                 "--quiet", "--base", str(base), "--macs", macs],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.addCleanup(fake.stdout.close)
        self.addCleanup(fake.wait, 5)
        self.addCleanup(fake.terminate)
        self.assertIn("fake devices", fake.stdout.readline())
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        cfg = Config(tmp / "govee_config.json")
        cfg.import_layout(OWNER, create_missing=True)
        for k, d in enumerate(sorted(cfg.devices.values(), key=lambda d: d.get("order", 0))):
            d["ip"] = f"127.0.0.{base + k}"
            d["sku"] = "H805A"
        cfg.data["schedule"]["enabled"] = False
        cfg.save()
        ctl = C.Controller(cfg, presets=C.PresetStore(ROOT / "presets", user=tmp / "user"))
        self.addCleanup(ctl.shutdown)
        srv = Server(("127.0.0.1", 0), make_handler(ctl, lambda: None, Auth(tmp / "auth.json")))
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        shots = os.environ.get("TEXT_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = [str(Path(shots) / "words-card.png"), str(Path(shots) / "words-card-marquee.png")] if shots else []
        res = subprocess.run(["node", str(ROOT / "tests" / "ui_textgen.js"), f"http://127.0.0.1:{srv.server_address[1]}/",
                              *args], capture_output=True, text=True, timeout=150,
                             env={**os.environ, "NODE_PATH": NODE_PATH})
        self.assertEqual(res.returncode, 0, res.stderr)
        out = json.loads(res.stdout.strip().splitlines()[-1])
        self.assertEqual(out["errors"], [], out)
        self.assertEqual(out["overflow"], [], out)
        self.assertEqual(out["small"], [], out)
        self.assertEqual(out["steps"], ["card", "preview", "marquee", "played", "saved", "hue reset"], out)
        self.assertGreater(out["lit"], 50, out)                         # the preview shows the letters
        self.assertIn("~", out["skip"])                                 # and says what it skipped
        self.assertIn("Single letter", out["modeOn"])                   # the default
        self.assertEqual((out["label1"], out["label2"]), ("Letter time", "Speed"))
        self.assertIn("Broncos", out["chipOn"])
        self.assertGreaterEqual(out["inputH"], 44)
        self.assertEqual(out["playing"], "Go Broncos~")
        self.assertEqual(out["dialogDefault"], "Go Broncos~")
        self.assertIn("go-broncos-text", out["presets"])
        saved = ctl.presets.get("go-broncos-text")
        P = saved["layers"][0]["params"]
        self.assertEqual((saved["name"], P["mode"], P["colors"]), ("Go Broncos Text", "marquee", ["#ff4f00", "#1040ff"]))
        self.assertTrue(out["chipHidden0"])
        self.assertIn("Colours shifted", out["chipText"])
        self.assertEqual(out["hue"], 0)


if __name__ == "__main__":
    unittest.main()
