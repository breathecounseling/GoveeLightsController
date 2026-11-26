"""Round-10 bug hunt (outside game day, see test_r10_gameday.py): each test here failed
before its fix."""
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, "tests")

ROOT = Path(__file__).resolve().parent.parent


class Ctl:
    presets = None
    games: dict = {}


class PreviewQueueTests(unittest.TestCase):
    def test_a_newer_long_preview_skips_the_queued_ones(self):
        """Dragging a slider on the 24 s story sends a preview per pause. Long previews
        are made one at a time, so each waited for every earlier one (four drags: the
        last answer took four times as long). A preview still waiting when a newer one
        arrives is dropped; the newest is made next."""
        from goveelights.hologram import studio as st
        with tempfile.TemporaryDirectory() as td:
            s = st.Studio(td)
            go, started, made = threading.Event(), threading.Event(), []

            def proc(spec):
                made.append(spec["seconds"])
                started.set()
                go.wait(10)
                return {"tiles": [], "seconds": spec["seconds"], "year": 2026}
            out = {}

            def ask(secs):
                out[secs] = s.preview(Ctl(), {"scene": "santa-landing", "layout": "1", "seconds": secs})
            with mock.patch.object(s, "_preview_proc", side_effect=proc):
                first = threading.Thread(target=ask, args=(20,))
                first.start()
                self.assertTrue(started.wait(10))                  # 20 s is being made
                waiting = [threading.Thread(target=ask, args=(n,)) for n in (21, 22, 23)]
                for t in waiting:
                    t.start()
                    time.sleep(0.15)                               # they queue in this order
                go.set()
                for t in [first] + waiting:
                    t.join(10)
            self.assertEqual(made, [20, 23])
            self.assertTrue(out[21].get("superseded") and out[22].get("superseded"))
            self.assertFalse(out[23].get("superseded"))
            self.assertEqual(out[23]["seconds"], 23)

    def test_superseded_answer_is_not_cached(self):
        from goveelights.hologram import studio as st
        with tempfile.TemporaryDirectory() as td:
            s = st.Studio(td)
            with mock.patch.object(s, "_preview_proc", return_value={"tiles": [], "seconds": 20, "year": 2026}):
                s._preview_gen = 5                                 # as if newer ones had arrived
                with mock.patch.object(s, "_newest", side_effect=lambda g: False):
                    r = s.preview(Ctl(), {"scene": "santa-landing", "layout": "1", "seconds": 20})
                self.assertTrue(r.get("superseded"))
                r = s.preview(Ctl(), {"scene": "santa-landing", "layout": "1", "seconds": 20})
                self.assertFalse(r.get("superseded"))

    def test_page_ignores_a_superseded_answer(self):
        html = (ROOT / "goveelights" / "web" / "app.html").read_text(encoding="utf-8")
        body = html[html.index("function hoPreview()"):html.index("function hoStage(")]
        self.assertIn("superseded", body)


class FourthRunTests(unittest.TestCase):
    """The fourth box gets a bulb count (the ladder trip) before it is camera-mapped: it
    sits on a line below the house in the preview, but it must not squash the mapped
    house's 2D effects (tree, text, dusk sky) into the top of their range."""

    def layouts(self):
        from test_rack import LAYOUT_FILE

        from goveelights.config import Config
        from goveelights.layout import Layout
        td = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, td, True)
        cfg = Config(Path(td) / "cfg.json")
        cfg.import_layout(LAYOUT_FILE, create_missing=True)
        three = Layout.from_config(cfg)
        cfg.devices["AA:BB:CC:DD:EE:FF:00:09"] = {**cfg.devices[next(iter(cfg.devices))], "name": "Run 4",
                                                  "pixel_count": 72, "hidden": [], "geometry": [], "order": 9}
        return three, Layout.from_config(cfg)

    def test_mapped_runs_keep_their_coordinates(self):
        three, four = self.layouts()
        self.assertEqual(four.n, three.n + 72)
        a = {(p.run, p.local): p for p in three.pixels}
        for p in four.pixels:
            if p.run == "Run 4":
                continue
            q = a[(p.run, p.local)]
            self.assertEqual((p.nx, p.ny, p.sx, p.sy, p.spanx, p.spany), (q.nx, q.ny, q.sx, q.sy, q.spanx, q.spany))
            self.assertEqual((p.rnx, p.rny), (q.rnx, q.rny))

    def test_unmapped_run_stays_in_range(self):
        _, four = self.layouts()
        for p in four.pixels:
            self.assertTrue(0.0 <= p.nx <= 1.0 and 0.0 <= p.ny <= 1.0, (p.run, p.nx, p.ny))
            self.assertTrue(0.0 <= p.sx <= p.spanx and 0.0 <= p.sy <= p.spany, (p.run, p.sx, p.sy))

    def test_long_unmapped_strip_spreads_under_the_house(self):
        from goveelights.layout import Layout, Run
        three, _ = self.layouts()
        runs = [Run(mac=r.mac, name=r.name, count=r.count, reverse=r.reverse, hidden=set(r.hidden),
                    geometry=r.geometry) for r in three.runs]
        L = Layout(runs + [Run(mac="W", name="WLED", count=300, geometry=[])])
        xs = [p.nx for p in L.pixels if p.run == "WLED"]
        self.assertEqual(len(set(xs)), 300)                                # no bulbs piled on the edge
        self.assertTrue(xs[0] == 0.0 and 0.99 <= xs[-1] <= 1.0)

    def test_preview_bounds_still_show_the_placeholder_line(self):
        from types import SimpleNamespace

        from goveelights.controller import Controller
        _, four = self.layouts()
        info = Controller.layout_info(SimpleNamespace(engine=SimpleNamespace(layout=four)))
        ys = [y for _, y, _ in info["pixels"]]
        self.assertLessEqual(info["bounds"][1], min(ys))
        self.assertGreaterEqual(info["bounds"][3], max(ys))

    def test_tree_on_the_peak_renders_the_same(self):
        from goveelights.scene import Scene
        three, four = self.layouts()
        d = __import__("json").loads((ROOT / "presets" / "christmas-tree.json").read_text(encoding="utf-8"))
        a, b = Scene.from_dict(d), Scene.from_dict(d)
        ca, cb = a.render(3.0, three), b.render(3.0, four)
        pa = {(p.run, p.local): ca[p.i] for p in three.pixels if p.run == "Peak"}
        pb = {(p.run, p.local): cb[p.i] for p in four.pixels if p.run == "Peak"}
        diff = sum(1 for k in pa if max(abs(x - y) for x, y in zip(pa[k], pb[k])) > 0.02)
        self.assertLess(diff, len(pa) * 0.05)


BOM = b"\xef\xbb\xbf"


class ByteOrderMarkTests(unittest.TestCase):
    """Windows Notepad ('UTF-8 with BOM') and Windows PowerShell's Set-Content -Encoding
    UTF8 start a file with a byte-order mark. A hand-edited govee_config.json saved that
    way stopped the app starting ('Unexpected UTF-8 BOM'): no lights that night."""

    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, self.td, True)

    def write(self, name, obj) -> Path:
        p = self.td / name
        p.write_bytes(BOM + __import__("json").dumps(obj).encode("utf-8"))
        return p

    def test_config_with_bom_loads(self):
        from goveelights.config import Config
        p = self.write("govee_config.json", {"schedule": {"enabled": True, "lat": 40.0, "lon": -105.0},
                                             "game": {"teams": []}})
        cfg = Config(p)
        self.assertTrue(cfg.data["schedule"]["enabled"])
        self.assertEqual(cfg.data["schedule"]["lat"], 40.0)

    def test_legacy_config_with_bom_is_migrated(self):
        from goveelights.config import Config
        p = self.write("govee_config.json", {"schedule": {"enabled": True}})
        cfg = Config(p)
        self.assertIn("schedule.lat", cfg.migrated)
        self.assertEqual(Config(p).data["schedule"]["enabled"], True)      # and the rewrite reads back

    def test_layout_file_with_bom_imports(self):
        from test_rack import LAYOUT_FILE

        from goveelights.config import Config
        lay = __import__("json").loads(Path(LAYOUT_FILE).read_text(encoding="utf-8"))
        p = self.write("house.json", lay)
        cfg = Config(self.td / "cfg.json")
        self.assertTrue(cfg.import_layout(p, create_missing=True))
        self.assertEqual(len(cfg.devices), len(lay["devices"]))
        cfg.data["zones"] = []
        self.assertTrue(cfg.merge_house_layout(p))                         # its zones come across

    def test_preset_with_bom_is_listed_and_plays(self):
        from goveelights.controller import PresetStore
        from goveelights.scene import Scene
        d = {"name": "Hand edited", "layers": [{"effect": "solid", "params": {"color": "#ff0000"}}]}
        self.write("hand-edited.json", d)
        store = PresetStore(self.td)
        self.assertIn("hand-edited", [p["id"] for p in store.list()])
        self.assertEqual(store.get("hand-edited")["name"], "Hand edited")
        self.assertEqual(Scene.load(self.td / "hand-edited.json").name, "Hand edited")

    def test_auth_file_with_bom_loads(self):
        from goveelights.auth import Auth
        p = self.write("govee_auth.json", {"password": None, "sessions": {}, "lan_without_login": False})
        a = Auth(p)
        self.assertFalse(a.data["lan_without_login"])


if __name__ == "__main__":
    unittest.main()
