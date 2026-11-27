"""Browser check of "Colours look wrong?" (tests/ui_colours.js) on an iPhone-sized screen,
against a real controller driving fake boxes (tools/fake_govee.py --counts 93,108,60,108:
Eaves, Peak, Accent and the Tip, added the old way as "Somewhere else"). The browser answers
as boxes wired R-B-G (the Peak and the Tip) would look; the test reads the frames the
controller really sends and checks the house shows true orange and blue afterwards, and
the old pink and cyan under "Before". Then the Fourth run card moves the Tip to the top of
the Peak in one tap. Skipped when node or Playwright is missing, or not on Linux (the fakes
bind 127.0.0.N). COLOUR_SHOTS=<dir> saves screenshots there."""
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

from goveelights import protocol as p  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OWNER = ROOT / "layouts" / "house.json"
TIP = "AA:BB:CC:DD:EE:FF:00:03"
ORANGE, BLUE = (255, 74, 0), (32, 80, 255)
WIRING = {"Peak": "RBG", "Tip": "RBG"}


def box_shows(order, sent):
    out = [0, 0, 0]
    for i, ch in enumerate(order):
        out["RGB".index(ch)] = sent[i]
    return tuple(out)


@unittest.skipUnless(NODE_PATH, "node + Playwright not installed")
@unittest.skipUnless(sys.platform.startswith("linux"), "the fake boxes bind 127.0.0.N")
@unittest.skipUnless(OWNER.exists(), "the owner's layout is not in this copy")
class ColoursInTheBrowser(unittest.TestCase):
    def test_colour_test_and_tip_move_on_a_phone(self):
        from goveelights import addrun
        from goveelights import controller as C
        from goveelights.app_server import Server, make_handler
        from goveelights.auth import Auth
        from goveelights.config import Config
        from goveelights.layout import Layout
        pre = set(threading.enumerate())
        self.addCleanup(lambda: [t.join(6) for t in set(threading.enumerate()) - pre])
        base = 20 + (os.getpid() * 7) % 200                    # a range of its own
        house = sorted(json.loads(OWNER.read_text())["devices"].items(), key=lambda kv: kv[1].get("order", 0))
        macs = ",".join([m for m, _ in house] + [TIP])
        fake = subprocess.Popen([sys.executable, str(ROOT / "tools" / "fake_govee.py"), "--counts", "93,108,60,108",
                                 "--quiet", "--base", str(base), "--macs", macs, "--skus", ",,,H705E"],
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
        cfg.upsert_discovered({"device": TIP, "sku": "H705E", "ip": f"127.0.0.{base + 3}",
                               "source_ip": f"127.0.0.{base + 3}"})
        cfg.data["schedule"]["enabled"] = False
        L = Layout.from_config(cfg)
        # the owner's pushed version: the Tip as "Somewhere else", copying the Peak
        addrun.add_run(cfg, L, TIP, 108, "other", name="Tip", follows=L.run("Peak").mac)
        cfg.save()
        ctl = C.Controller(cfg, presets=C.PresetStore(ROOT / "presets", user=tmp / "user"))
        self.addCleanup(ctl.shutdown)
        ips = {r.name: cfg.devices[r.mac]["ip"] for r in ctl.engine.layout.runs}
        last, lock = {}, threading.Lock()
        real = ctl.lan.send_razer

        def spy(ip, pkt):                                     # what reaches each box
            try:
                d = p.decode_razer(pkt)
                if "colors" in d:
                    with lock:
                        last[ip] = d["colors"]
            except ValueError:
                pass
            return real(ip, pkt)
        ctl.lan.send_razer = spy
        seen = {}

        def watch():                                          # the frames once each picture has settled
            t0, cur, since = time.time(), None, 0.0
            while time.time() - t0 < 150 and not seen.get("stop"):
                v = ctl.colourtest.view()
                now = (v["step"], v["showing"]) if v else None
                if now != cur:
                    cur, since = now, time.time()
                elif cur and cur[0] == "compare" and time.time() - since > 0.4:
                    with lock:
                        seen[cur[1]] = {k: list(x) for k, x in last.items()}
                time.sleep(0.05)
        th = threading.Thread(target=watch, daemon=True)
        th.start()
        srv = Server(("127.0.0.1", 0), make_handler(ctl, lambda: None, Auth(tmp / "auth.json")))
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        shots = os.environ.get("COLOUR_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        res = subprocess.run(["node", str(ROOT / "tests" / "ui_colours.js"), f"http://127.0.0.1:{srv.server_address[1]}/",
                              json.dumps(WIRING)] + ([shots] if shots else []),
                             capture_output=True, text=True, timeout=170, env={**os.environ, "NODE_PATH": NODE_PATH})
        seen["stop"] = True
        th.join(2)
        self.assertEqual(res.returncode, 0, res.stderr)
        out = json.loads(res.stdout.strip().splitlines()[-1])
        self.assertEqual(out["errors"], [], out)
        self.assertEqual(out["overflow"], [], out)
        self.assertEqual(out["small"], [], out)
        self.assertEqual(out["steps"], ["card", "question 2", "check", "result", "done", "manual", "peak top"], out)
        self.assertEqual(out["runs"], ["What colour is Eaves?", "What colour is Peak?", "What colour is Accent?",
                                       "What colour is Tip?"])
        self.assertEqual(out["ordersBefore"], ["RGB"] * 4)
        self.assertEqual(out["ordersAfter"], ["RGB", "RBG", "RGB", "RBG"])
        self.assertIn("R-B-G", out["orderText"])
        self.assertIn("green/blue swapped", out["orderText"])
        self.assertTrue(any("Peak: colour order R-B-G" in r for r in out["result"]), out["result"])
        self.assertTrue(any("Eaves: already right" in r for r in out["result"]), out["result"])
        self.assertEqual(out["manual"], ["RGB", "RBG", "RGB", "GRB"])
        self.assertTrue(out["linkLands"])
        self.assertIn("Make it the top of the Peak", out["offerText"])
        self.assertIn("Top of the Peak", out["movedText"])
        self.assertIn("12 on the gable", out["movedText"])
        # on the house: after = true orange and blue on every box, before = the old pink and cyan
        for name, ip in ips.items():
            o = WIRING.get(name, "RGB")
            after = {box_shows(o, c) for c in seen["after"][ip]}
            self.assertEqual(after - {(0, 0, 0)}, {ORANGE, BLUE}, name)
        before = {box_shows("RBG", c) for c in seen["before"][ips["Peak"]]} - {(0, 0, 0)}
        self.assertEqual(before, {(255, 0, 74), (32, 255, 80)})
        # the wiring stays with the box through the move; the Tip lights with the Peak
        d = ctl.cfg.devices[TIP]
        self.assertEqual((d["location"], d["color_order"]), ("peak top", "RBG"))
        self.assertIsNone(ctl.colourtest.view())
        self.assertEqual(len(ctl.engine.layout.run("Tip").pixels), 12)


if __name__ == "__main__":
    unittest.main()
