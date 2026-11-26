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


if __name__ == "__main__":
    unittest.main()
