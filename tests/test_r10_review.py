"""Round-10 review fixes: pregame polling that stops racing for a game stuck in "pre",
the Tonight card for a game that runs into lights-on, the preview tool with an
unmapped run, and the last JSON readers that tripped on a byte-order mark."""
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, "tests")
from test_r10_gameday import KEY, TEAM, GameDay, board, local  # noqa: E402

from goveelights import tonight  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BOM = b"\xef\xbb\xbf"


def _tmp(test) -> Path:
    d = Path(tempfile.mkdtemp())
    test.addCleanup(shutil.rmtree, d, True)
    return d


class StuckPregamePollTests(unittest.TestCase):
    def test_a_game_left_in_pre_goes_back_to_relaxed_polls(self):
        """A delayed or postponed game ESPN leaves in "pre": every minute for three hours
        after kickoff, then every five minutes, not every minute all evening."""
        gd = GameDay(self, local(12, 0))
        gd.r.clock.t = local(15, 30).timestamp()
        self.assertLessEqual(gd.ctl._poll_game(TEAM, KEY), 60)           # 2.5 h late: still watching
        for h, m in ((17, 0), (20, 0), (23, 59)):
            gd.r.clock.t = local(h, m).timestamp()
            self.assertEqual(gd.ctl._poll_game(TEAM, KEY), 300, f"{h}:{m:02d}")


class TonightRunsIntoLightsOnTests(unittest.TestCase):
    def test_afternoon_game_running_into_lights_on(self):
        """A 2:25 pm kickoff is still on when the lights come on at 4:36: its later
        scores are celebrated, so the card must not say there are none."""
        gd = GameDay(self, local(12, 0), on_time="16:36")
        gd.feed = board("pre", 0, 0, "2:25 PM", kickoff=local(14, 25))
        gd.run(4)
        kick = next(x for x in tonight.build(gd.ctl, local(12, 1))["timeline"] if x["kind"] == "game")
        self.assertIn("celebrations from lights-on (4:36 pm)", kick["detail"])
        self.assertNotIn("lights are off", kick["detail"])

    def test_schedule_off_does_not_claim_the_lights_are_off(self):
        gd = GameDay(self, local(12, 0))
        gd.ctl.cfg.data["schedule"]["enabled"] = False
        gd.run(4)
        kick = next(x for x in tonight.build(gd.ctl, local(12, 1))["timeline"] if x["kind"] == "game")
        self.assertNotIn("lights are off", kick["detail"])
        self.assertIn("if the lights are on", kick["detail"])


class PreviewUnmappedRunTests(unittest.TestCase):
    def test_unmapped_run_is_drawn_on_the_canvas(self):
        """On ladder day run 4 may be counted before it is mapped: the preview tool must
        still draw its bulbs (a line under the house), not below the picture."""
        import os
        sys.path.insert(0, str(ROOT / "tools"))
        self.addCleanup(sys.path.remove, str(ROOT / "tools"))
        self.addCleanup(os.chdir, os.getcwd())
        import preview
        d = _tmp(self)
        lay = json.loads((ROOT / "layouts" / "house.json").read_text(encoding="utf-8"))
        lay["devices"]["AA:BB:CC:DD:EE:" + "04"] = {"name": "Run 4", "pixel_count": 50, "order": 99}
        p = d / "lay.json"
        p.write_bytes(BOM + json.dumps(lay).encode("utf-8"))              # (and saved with a BOM)
        n = len(preview.house_layout(p).pixels)
        with mock.patch.object(preview.subprocess, "run", side_effect=OSError("no screenshot in tests")):
            preview.main(["warm-white", "--frames", "1", "--seconds", "0.1", "--layout", str(p),
                          "--out", str(d / "out")])
        html = (d / "out.html").read_text(encoding="utf-8")
        svg = re.search(r'<svg width="(\d+)" height="(\d+)"(.*?)</svg>', html, re.S)
        h = int(svg.group(2))
        cys = [float(c) for c in re.findall(r'cy="([\d.-]+)"', svg.group(3))]
        self.assertEqual(len(cys), n)
        self.assertLessEqual(max(cys), h)
        self.assertGreaterEqual(min(cys), 0)


class MoreByteOrderMarkTests(unittest.TestCase):
    def test_hologram_cli_reads_palettes_from_a_config_with_bom(self):
        from goveelights.hologram.cli import user_palettes
        p = _tmp(self) / "govee_config.json"
        p.write_bytes(BOM + json.dumps({"palettes": {"mine": ["#ff0000", "#00ff00"]}}).encode("utf-8"))
        self.assertEqual(user_palettes(p), {"mine": ["#ff0000", "#00ff00"]})

    def test_preview_tool_reads_a_layout_with_bom(self):
        sys.path.insert(0, str(ROOT / "tools"))
        self.addCleanup(sys.path.remove, str(ROOT / "tools"))
        import preview
        p = _tmp(self) / "lay.json"
        p.write_bytes(BOM + json.dumps({"devices": {"A": {"pixel_count": 5}}}).encode("utf-8"))
        self.assertEqual(len(preview.house_layout(p).pixels), 5)

    @unittest.skipUnless(shutil.which("git"), "needs git")
    def test_preset_migration_keeps_an_edit_saved_with_bom(self):
        from goveelights.controller import PresetStore, migrate_presets
        root = _tmp(self)
        g = lambda *a: subprocess.run(["git", "-C", str(root), *a], check=True, capture_output=True)  # noqa: E731
        g("init", "-q")
        g("config", "user.email", "t@example.com")
        g("config", "user.name", "t")
        (root / "presets").mkdir()
        shutil.copy(ROOT / "presets" / "fireworks.json", root / "presets")
        g("add", "presets")
        g("commit", "-qm", "built-ins")
        f = root / "presets" / "fireworks.json"
        fw = json.loads(f.read_text(encoding="utf-8"))
        f.write_bytes(BOM + json.dumps({**fw, "brightness": 0.25}, indent=2).encode("utf-8"))
        st = PresetStore(root / "presets", root / "user_presets")
        self.assertIn("fireworks", migrate_presets(st, root))
        self.assertEqual(st.get("fireworks")["brightness"], 0.25)


if __name__ == "__main__":
    unittest.main()
