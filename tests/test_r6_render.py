"""Round-6 render fixes: a new fade started mid-fade starts from what is on screen, a live
edit keeps a running crossfade, fx caches survive a reused layout id, echo does not pile
up frames while the clock is stopped, screen blending of values above 1, bad user palette
entries, negative fade progress and a hue sweep restarted while one ends."""
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from test_integration import SCH, Rig  # noqa: E402

from goveelights.app_server import Server, make_handler  # noqa: E402
from goveelights.auth import Auth  # noqa: E402
from goveelights.engine import Engine  # noqa: E402
from goveelights.layout import Layout, Run  # noqa: E402
from goveelights.output import MemoryOutput  # noqa: E402
from goveelights.scene import Scene, blend  # noqa: E402


def solid(h):
    return Scene.from_dict({"layers": [{"effect": "solid", "params": {"color": h}}]})


def close(test, a, b):
    for x, y in zip(a, b):
        test.assertAlmostEqual(x, y, places=3)


class FadeTests(unittest.TestCase):
    def setUp(self):
        self.e = Engine(Layout([Run("a", "A", 10), Run("b", "B", 10)]), MemoryOutput())
        self.e.set_scene(solid("#ff0000"))
        self.e.render(0.0)
        self.e.set_scene(solid("#00ff00"), 2.0)
        self.e._fade_start = 0.0
        close(self, self.e.render(1.0)[0], (0.5, 0.5, 0.0))

    def test_new_fade_mid_fade_starts_from_the_screen(self):
        self.e.set_scene(solid("#0000ff"), 2.0)
        self.e._fade_start = 1.0
        close(self, self.e.render(1.0)[0], (0.5, 0.5, 0.0))        # was a flash to pure green
        close(self, self.e.render(2.0)[0], (0.25, 0.25, 0.5))
        close(self, self.e.render(3.0)[0], (0.0, 0.0, 1.0))

    def test_live_edit_keeps_the_crossfade(self):
        self.e.set_scene(solid("#00ff10"), 0.0, continuous=True)
        c = self.e.render(1.0)[0]
        self.assertAlmostEqual(c[0], 0.5, places=3)                # was cancelled: (0, 1, 0.06)
        close(self, self.e.render(2.0)[0], (0.0, 1.0, 16 / 255))

    def test_fade_progress_never_negative(self):
        self.e._fade_start = 1.5                                  # set_scene after tick read t
        c = self.e.render(1.0)[0]
        self.assertTrue(all(v >= 0 for v in c), c)
        close(self, c, (1.0, 0.0, 0.0))


class HueSweepTests(unittest.TestCase):
    def test_restart_during_the_last_frame_is_kept(self):
        e = Engine(Layout([Run("a", "A", 4)]), MemoryOutput())
        e.set_scene(solid("#ff0000"))
        old, new = (e.wall() - 10, 4.0), (e.wall(), 4.0)          # old has finished

        class Racy:                                                # pad('hue') restarts it mid-read
            def __bool__(self):
                return True

            def __iter__(self):
                e.hue_sweep = new
                return iter(old)
        racy = Racy()
        e.hue_sweep = racy
        e.render(0.0)
        self.assertIs(e.hue_sweep, new)


class FxCacheTests(unittest.TestCase):
    def _reuse(self, fx):
        sc = Scene.from_dict({"layers": [{"effect": "chase", "fx": fx}]})
        l1 = Layout([Run("a", "A", 10)])
        sc.render(0.0, l1)
        l2 = Layout([Run("a", "A", 30)])
        cache = sc._cache["ctx"]
        for k in list(cache):                                     # l2 got l1's freed address
            if isinstance(k, tuple) and id(l1) in k:
                cache[tuple(id(l2) if v == id(l1) else v for v in k)] = cache[k]
        out = sc.render(0.1, l2)
        self.assertEqual(len(out), 30)

    def test_blur(self):
        self._reuse([{"type": "blur"}])

    def test_mirror(self):
        self._reuse([{"type": "mirror", "params": {"axis": "strip"}}])


class EchoTests(unittest.TestCase):
    def test_no_pile_up_at_speed_zero(self):
        L = Layout([Run("a", "A", 10)])
        sc = Scene.from_dict({"layers": [{"effect": "chase", "fx": [{"type": "echo"}]}]})
        for k in range(200):
            sc.render(k / 20, L, {"speed": 1.0})
        n = len(sc.layers[0].units[0].state["hist"])
        for k in range(200, 1200):
            sc.render(k / 20, L, {"speed": 0.0})
        self.assertLessEqual(len(sc.layers[0].units[0].state["hist"]), n + 1)


class ScreenTests(unittest.TestCase):
    def test_bright_inputs_stay_bright(self):
        for v in (1.5, 3.0):
            close(self, blend("screen", (v,) * 3, (v,) * 3, 1.0), (1.0, 1.0, 1.0))
        close(self, blend("screen", (0.5,) * 3, (0.5,) * 3, 1.0), (0.75, 0.75, 0.75))

    def test_boosted_orange(self):
        L = Layout([Run("a", "A", 4)])
        lay = {"effect": "solid", "params": {"color": "#ff8000"}, "fx": [{"type": "adjust", "params": {"brightness": 2}}]}
        sc = Scene.from_dict({"layers": [lay, {**lay, "blend": "screen"}]})
        close(self, sc.render(0, L)[0], (1.0, 1.0, 0.0))


class PaletteTests(unittest.TestCase):
    def test_bad_entries_do_not_stop_rendering(self):
        L = Layout([Run("a", "A", 4)])
        sc = Scene.from_dict({"layers": [{"effect": "chase", "palette": "Mine"}]}, {"Mine": ["#ff00", "#00ff00"]})
        self.assertEqual(len(sc.render(0, L)), 4)
        sc = Scene.from_dict({"layers": [{"effect": "chase"}]}, {"Mine": ["red", 7]})
        self.assertEqual(len(sc.render(0, L, {"palette": "Mine"})), 4)

    def test_api_refuses_bad_colours(self):
        r = Rig(self, datetime(2026, 11, 26, 12, 0).astimezone(), {"schedule": {**SCH, "enabled": False}})
        auth = Auth(Path(tempfile.mkdtemp()) / "auth.json")
        srv = Server(("127.0.0.1", 0), make_handler(r.ctl, lambda: None, auth))
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)

        def post(pals):
            req = urllib.request.Request(f"http://127.0.0.1:{srv.server_address[1]}/api/palettes",
                                         json.dumps({"palettes": pals}).encode(), method="POST",
                                         headers={"Content-Type": "application/json", "X-Requested-With": "lights"})
            try:
                return json.load(urllib.request.urlopen(req, timeout=5)).get("ok", False)
            except urllib.error.HTTPError:
                return False
        for bad in (["#ff00"], ["red"], [7]):
            self.assertFalse(post({"Mine": bad}), bad)
        self.assertNotEqual(r.ctl.cfg.data.get("palettes", {}).get("Mine"), ["red"])
        self.assertTrue(post({"Mine": ["#ff0000", "#0f0"]}))


if __name__ == "__main__":
    unittest.main()
