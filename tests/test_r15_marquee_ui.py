"""Browser check of the marquee end to end (tests/ui_marquee.js) on an iPhone-sized screen,
against a real controller driving fake boxes (tools/fake_govee.py --counts 93,108,60,108:
Eaves, Peak, Accent and a fourth box not yet in the show). The owner, round 3: "they all
just flash the letter without a marquee". Words on the Peak in Marquee scrolls on the
house, Save as preset keeps the mode, the preset's Edit screen has a Single letter /
Marquee switch at the top, and the built-in word presets scroll on the house.
Skipped when node or Playwright is missing, or not on Linux (the fakes bind 127.0.0.N).
MARQUEE_SHOTS=<dir> saves the card and the editor switch."""
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
class MarqueeInTheBrowser(unittest.TestCase):
    def test_marquee_scrolls_on_the_house_and_keeps_its_mode(self):
        from goveelights import controller as C
        from goveelights.app_server import Server, make_handler
        from goveelights.auth import Auth
        from goveelights.config import Config
        pre = set(threading.enumerate())
        self.addCleanup(lambda: [t.join(6) for t in set(threading.enumerate()) - pre])
        base = 30 + (os.getpid() * 11) % 190                # a range of its own (other runs may be going)
        house = sorted(json.loads(OWNER.read_text())["devices"].items(), key=lambda kv: kv[1].get("order", 0))
        macs = ",".join([m for m, _ in house] + ["AA:BB:CC:DD:EE:FF:00:31"])
        fake = subprocess.Popen([sys.executable, str(ROOT / "tools" / "fake_govee.py"), "--counts", "93,108,60,108",
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
        shots = os.environ.get("MARQUEE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        res = subprocess.run(["node", str(ROOT / "tests" / "ui_marquee.js"), f"http://127.0.0.1:{srv.server_address[1]}/",
                              *([shots] if shots else [])], capture_output=True, text=True, timeout=180,
                             env={**os.environ, "NODE_PATH": NODE_PATH})
        self.assertEqual(res.returncode, 0, res.stderr)
        out = json.loads(res.stdout.strip().splitlines()[-1])
        self.assertEqual(out["errors"], [], out)
        self.assertEqual(out["overflow"], [], out)
        self.assertEqual(out["small"], [], out)
        self.assertEqual(out["steps"], ["preview", "house", "saved", "editor"], out)
        self.assertIn("Marquee", out["modeOn"])
        # the preview and the house scroll: the Peak's letters slide left, many distinct frames
        self.assertGreater(out["preview"]["distinct"], 10, out)
        self.assertGreater(out["previewSlides"], 3, out)
        self.assertGreater(out["houseFrames"], 10, out)
        self.assertGreater(out["houseSlides"], 3, out)
        # saved with its mode; the editor's switch says Marquee and shows the marquee's settings
        self.assertEqual(out["saved"], "marquee")
        self.assertEqual(ctl.presets.get("marquee-test")["layers"][0]["params"]["mode"], "marquee")
        self.assertIn("Marquee", out["edit1"]["on"])
        self.assertIn("Marquee speed", out["edit1"]["labels"])
        self.assertIn("Stop on each letter (s)", out["edit1"]["labels"])
        self.assertNotIn("Letter time (s)", out["edit1"]["labels"])
        self.assertNotIn("Show it as", out["edit1"]["labels"])           # the switch, not a chip row
        # every built-in word preset scrolls on the house; its one-big-letter twin stays
        ids = {s.split("|")[0]: s.split("|")[1] for s in out["builtin"]}
        for w in ("give-thanks", "go-broncos", "happy-new-year", "merry-christmas", "scoreboard", "touchdown"):
            self.assertIn(f"text-{w}", ids)
            self.assertIn(f"text-{w}-letters", ids)
            self.assertNotIn(f"text-{w}-scroll", ids)
        self.assertEqual(ids["text-go-broncos"], "Go Broncos")
        self.assertEqual(ids["text-go-broncos-letters"], "Go Broncos (big letters)")
        self.assertEqual(out["playing"], "Go Broncos")
        self.assertGreater(out["builtinFrames"], 10, out)
        self.assertGreater(out["builtinSlides"], 3, out)
        # its editor opens on the words with Marquee on; one tap switches to Single letter
        self.assertIn("Marquee", out["edit2"]["on"])
        self.assertIn("Single letter", out["edit3"]["on"])
        self.assertIn("Letter time (s)", out["edit3"]["labels"])
        self.assertNotIn("Marquee speed", out["edit3"]["labels"])
        self.assertEqual(out["draftMode"], ["single"])
        self.assertTrue(json.loads((ROOT / "presets" / "text-go-broncos.json").read_text())
                        ["layers"][1]["params"]["mode"] == "marquee")             # the file is untouched


if __name__ == "__main__":
    unittest.main()
