"""Hologram fan studio review fixes: splice paths, New Year legibility, colour sources,
job lifetime (restart, cancel, hung ffmpeg), downloads and the CLI defaults."""
from __future__ import annotations

import io
import json
import math
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from goveelights.hologram import SCENES, Clip, FanLayout, export
from goveelights.hologram import studio as studio_mod
from goveelights.hologram.scenes import hex_rgb, vivid
from goveelights.hologram.studio import Studio, resolve_colours, team_colours


def inside_a_fan(ctx, x, y) -> bool:
    return any(math.hypot(x - fx, y - fy) < ctx.R for fx, fy in ctx.fans)


class SplicePathTests(unittest.TestCase):
    def test_sleigh_flies_through_the_fans_on_a_square(self):
        cl = Clip("sleigh", FanLayout("2x2", size=200))
        sc, c = cl.scene, cl.ctx
        n = 400
        pts = [sc.path(i / n) for i in range(n)]
        frac = sum(inside_a_fan(c, x, y) for x, y, _ in pts) / n
        self.assertGreater(frac, 0.6)                    # was 0.34 on the old diagonal
        row_ys = sorted({round(y) for _, y in c.fans})
        for ry in row_ys:                                # it passes along both rows
            self.assertTrue(any(abs(y - ry) < c.R * 0.5 and inside_a_fan(c, x, y) for x, y, _ in pts), ry)
        # and it crosses the moon's fan
        mx, my, _ = sc.moon
        self.assertTrue(any(math.hypot(x - mx, y - my) < c.R * 0.6 for x, y, _ in pts))

    def test_sleigh_single_row_path_still_reads(self):
        for lay in ("1", "1x3"):
            cl = Clip("sleigh", FanLayout(lay, size=200))
            n = 200
            frac = sum(inside_a_fan(cl.ctx, *cl.scene.path(i / n)[:2]) for i in range(n)) / n
            self.assertGreater(frac, 0.55, lay)

    def test_football_ball_stays_in_the_fans_on_a_square(self):
        cl = Clip("football", FanLayout("2x2", size=200))
        sc, c = cl.scene, cl.ctx
        self.assertEqual(sc.post, 1)
        self.assertEqual(sc.kick, 3)                     # straight below the posts
        n = 400
        frac = sum(inside_a_fan(c, *sc.ball_at(i / n)[:2]) for i in range(n)) / n
        self.assertGreater(frac, 0.85)                   # was 0.68 on the diagonal

    def test_football_ball_visible_early_in_the_kick(self):
        cl = Clip("football", FanLayout("2x2", size=120))
        tiles = cl.tiles_at(0.2)
        brown = sum(1 for t in tiles for y in range(t.h) for x in range(t.w)
                    if t.pixel(x, y) == (196, 98, 36))
        self.assertGreater(brown, 10)

    def test_scene_info_says_which_scenes_span_the_fans(self):
        info = {cls.id: cls.info() for cls in SCENES.values()}
        for sid in ("ghosts", "snowglobe", "wreath"):
            self.assertFalse(info[sid]["splice"], sid)
        for sid in ("sleigh", "pumpkin", "football", "newyear"):
            self.assertTrue(info[sid]["splice"], sid)


class NewYearTests(unittest.TestCase):
    def test_burst_never_runs_through_the_letters(self):
        from goveelights import font
        cl = Clip("newyear", FanLayout("1", size=240))
        sc, c = cl.scene, cl.ctx
        cx, cy = c.fans[0]
        for t in (0.0, 0.02, 0.05):                      # the start of a burst: rays near the middle
            cv = cl.canvas_at(t)
            for wi, (txt, hgt, dy) in enumerate(sc.words[0]):
                col = sc.cols[wi % len(sc.cols)]
                hh = hgt * c.R * 0.95
                w = min(font.layout(txt).width * hh / font.ROWS, 1.6 * c.R)
                hh = min(hh, w * font.ROWS / max(1, font.layout(txt).width))
                seen = set()
                for y in range(int(cy + dy * c.R - hh / 2) + 1, int(cy + dy * c.R + hh / 2)):
                    for x in range(int(cx - w / 2) + 1, int(cx + w / 2)):
                        seen.add(cv.pixel(x, y))
                self.assertLessEqual(seen, {(0, 0, 0), tuple(col)}, (t, txt))


class _Presets:
    def __init__(self, data):
        self.data = data

    def list(self):
        return [{"id": k, "name": k} for k in self.data]

    def get(self, pid):
        if pid not in self.data:
            raise FileNotFoundError(f"[Errno 2] No such file or directory: '/secret/place/presets/{pid}.json'")
        return self.data[pid]


class _Engine:
    master = {"palette": ""}


class _Ctl:
    def __init__(self, presets=None, scene=None):
        self.presets = _Presets(presets or {})
        self.user_palettes = {}
        self.engine = _Engine()
        self.games = {}
        self.scene_dict = scene


class ColourSourceTests(unittest.TestCase):
    FIRE = {"layers": [{"effect": "solid", "params": {"color": "#3a0400"}},
                       {"effect": "solid", "params": {"color": "#300400"}},
                       {"effect": "fire", "params": {"intensity": 0.85}, "blend": "lighten"}]}

    def test_dim_base_washes_give_way_to_the_effects_own_colours(self):
        cols = resolve_colours(_Ctl({"fire": self.FIRE}), "preset:fire")
        self.assertNotIn("#3a0400", cols)
        self.assertNotIn("#300400", cols)
        self.assertIn("#ff4000", cols)                   # the fire effect's default palette

    def test_overlay_colours_come_first(self):
        p = {"layers": [{"effect": "solid", "params": {"color": "#10a020"}},
                        {"effect": "twinkle", "params": {"colors": ["#ffd000"]}, "blend": "add"}]}
        self.assertEqual(resolve_colours(_Ctl({"p": p}), "preset:p")[:2], ["#ffd000", "#10a020"])

    def test_nothing_to_match_is_none(self):
        self.assertIsNone(resolve_colours(_Ctl(scene=None), "house"))
        off = {"layers": [{"effect": "solid", "params": {"color": "#101010"}}]}
        self.assertIsNone(resolve_colours(_Ctl(scene=off), "house"))

    def test_unknown_preset_does_not_echo_a_path(self):
        with self.assertRaises(ValueError) as e:
            resolve_colours(_Ctl(), "preset:does-not-exist")
        self.assertNotIn("/", str(e.exception))
        self.assertIn("does-not-exist", str(e.exception))

    def test_default_team_colours_are_the_scenes_neutral_ones(self):
        self.assertEqual(team_colours(_Ctl()), list(SCENES["football"].default_colors))
        ctl = _Ctl()
        ctl.games = {"g": {"colors": ["#112233", "#445566"]}}
        self.assertEqual(team_colours(ctl), ["#112233", "#445566"])
        self.assertEqual(Clip("football", FanLayout("1", size=64)).scene.c1, vivid(hex_rgb("#ffd23a"), 200))


class StudioLifetimeTests(unittest.TestCase):
    def test_relative_folder_is_made_absolute(self):
        with tempfile.TemporaryDirectory() as td:
            old = os.getcwd()
            os.chdir(td)
            try:
                st = Studio("hologram_clips")
            finally:
                os.chdir(old)
            self.assertTrue(st.out.is_absolute())
            self.assertEqual(st.out, (Path(td) / "hologram_clips").resolve())

    def test_finished_files_survive_a_restart(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            (out / "wreath-240.gif").write_bytes(b"GIF89a")
            st = Studio(out)
            job = {"state": "done", "scene": "wreath", "layout": "1", "size": 240, "total": 144,
                   "files": ["wreath-240.gif", "wreath-240.mp4", "../govee_config.json"], "skipped": {}}
            st._save_result(job)
            st2 = Studio(out)
            j = st2.job()
            self.assertEqual(j["state"], "done")
            self.assertEqual(j["files"], ["wreath-240.gif"])      # only safe names still on disk
            self.assertEqual(st2.file("wreath-240.gif"), (out / "wreath-240.gif").resolve())
            st2._clean()                                          # a new job clears the list too
            self.assertFalse((out / studio_mod.RESULT).exists())
            self.assertEqual(Studio(out).job()["state"], "idle")

    def test_corrupt_result_is_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / studio_mod.RESULT).write_text("{nope")
            self.assertEqual(Studio(td).job()["state"], "idle")

    @unittest.skipIf(os.name == "nt", "process groups are POSIX; Windows uses taskkill /T")
    def test_kill_tree_stops_grandchildren(self):
        with tempfile.TemporaryDirectory() as td:
            pidf = Path(td) / "child.pid"
            code = ("import subprocess, sys, time\n"
                    "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                    f"open({str(pidf)!r}, 'w').write(str(p.pid))\n"
                    "time.sleep(60)\n")
            proc = subprocess.Popen([sys.executable, "-c", code], start_new_session=True)
            for _ in range(100):
                if pidf.exists() and pidf.read_text():
                    break
                time.sleep(0.05)
            child = int(pidf.read_text())
            studio_mod._kill_tree(proc)
            self.assertIsNotNone(proc.poll())
            for _ in range(100):
                try:
                    os.kill(child, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.05)
            else:
                os.kill(child, 9)
                self.fail("the grandchild (an ffmpeg, in real life) was left running")


class _FakeFfmpegTests(unittest.TestCase):
    def setUp(self):
        self.saved = dict(export._ffmpeg_cache)
        export._ffmpeg_cache["v"] = {"path": sys.executable, "encoder": "fake"}

    def tearDown(self):
        export._ffmpeg_cache.clear()
        export._ffmpeg_cache.update(self.saved)


class ExportRobustnessTests(_FakeFfmpegTests):
    def fake(self, script):
        procs = []

        def make(info, size, fps, out):
            p = subprocess.Popen([sys.executable, "-c", script, str(out)], stdin=subprocess.PIPE,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            procs.append(p)
            return p
        return make, procs

    def test_hung_encoder_is_skipped_not_fatal(self):
        make, procs = self.fake("import sys, time; open(sys.argv[1], 'wb').write(b'x'); sys.stdin.buffer.read(); time.sleep(60)")
        with tempfile.TemporaryDirectory() as td, mock.patch.object(export, "_ffmpeg_proc", make), \
                mock.patch.object(export, "FFMPEG_WAIT", 1):
            res = export.export(Clip("wreath", FanLayout("1", size=32), seconds=2, fps=10), td, ["gif", "mp4"])
            self.assertEqual(res["files"], ["wreath-32.gif"])
            self.assertIn("did not finish", res["skipped"]["mp4"])
            self.assertFalse((Path(td) / "wreath-32.mp4").exists())
        self.assertTrue(all(p.poll() is not None for p in procs))

    def test_failed_frames_stop_the_encoders(self):
        make, procs = self.fake("import sys, time; sys.stdin.buffer.read(); open(sys.argv[1], 'wb').write(b'x'); time.sleep(60)")
        clip = Clip("wreath", FanLayout("1x2", size=32), seconds=2, fps=10)
        calls = [0]
        real = clip.frame

        def boom(i, mask=True):
            calls[0] += 1
            if calls[0] > 3:
                raise RuntimeError("disk full")
            return real(i, mask)
        clip.frame = boom
        with tempfile.TemporaryDirectory() as td, mock.patch.object(export, "_ffmpeg_proc", make):
            with self.assertRaises(RuntimeError):
                export.export(clip, td, ["mp4"])
            self.assertEqual(len(procs), 2)
            self.assertTrue(all(p.poll() is not None for p in procs))
            self.assertEqual([f for f in os.listdir(td) if f.endswith(".mp4")], [])

    def test_default_formats_are_gif_and_mp4(self):
        self.assertEqual(export.DEFAULT_FORMATS, ("gif", "mp4"))


class CliDefaultTests(unittest.TestCase):
    def test_no_png_unless_asked_and_user_palettes(self):
        from goveelights.cli import main
        saved = dict(export._ffmpeg_cache)
        export._ffmpeg_cache["v"] = {}
        try:
            with tempfile.TemporaryDirectory() as td:
                cfg = Path(td) / "cfg.json"
                cfg.write_text(json.dumps({"palettes": {"Porch Mix": ["#ff0000", "#00ff00"]}}))
                out = Path(td) / "out"
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = main(["hologram", "wreath", "--size", "32", "--seconds", "2", "--fps", "10",
                               "--palette", "porch mix", "--config", str(cfg), "--out", str(out), "--json"])
                self.assertEqual(rc, 0)
                self.assertEqual(sorted(os.listdir(out)), ["wreath-32.gif"])
                with self.assertRaises(SystemExit):
                    main(["hologram", "wreath", "--palette", "Nope", "--config", str(cfg)])
        finally:
            export._ffmpeg_cache.clear()
            export._ffmpeg_cache.update(saved)


class ServerTests(unittest.TestCase):
    def test_send_file_survives_a_dropped_download(self):
        from goveelights.app_server import make_handler
        H = make_handler(_Ctl(), lambda: None, None)
        h = H.__new__(H)
        for name in ("send_response", "send_header", "end_headers", "_sec_headers"):
            setattr(h, name, lambda *a, **k: None)

        class Gone:
            def write(self, b):
                raise BrokenPipeError(32, "Broken pipe")
        h.wfile = Gone()
        h.close_connection = False
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.gif"
            p.write_bytes(b"0" * 1000)
            h._send_file(p)                              # no exception
        self.assertTrue(h.close_connection)

    def test_one_studio_for_simultaneous_first_requests(self):
        from goveelights import app_server
        real = app_server._studio_for

        def slow(ctl):
            time.sleep(0.2)
            return real(ctl)
        with tempfile.TemporaryDirectory() as td:
            ctl = _Ctl()
            ctl.cfg = type("C", (), {"path": str(Path(td) / "c.json")})()
            with mock.patch.object(app_server, "_studio_for", slow):
                H = app_server.make_handler(ctl, lambda: None, None)
                studio = _closure(H, "studio")           # what every /api/hologram* route calls
                got = []
                ts = [threading.Thread(target=lambda: got.append(studio())) for _ in range(4)]
                for t in ts:
                    t.start()
                for t in ts:
                    t.join()
            self.assertEqual(len(H.studio_box), 1)
            self.assertEqual(len({id(s) for s in got}), 1)


def _closure(H, name):
    """The handler factory's inner function `name` (kept in the methods' closures)."""
    for attr in vars(H).values():
        fn = getattr(attr, "__code__", None) and attr
        if not fn or not fn.__closure__:
            continue
        for var, cell in zip(fn.__code__.co_freevars, fn.__closure__):
            if var == name:
                return cell.cell_contents
    raise LookupError(name)


if __name__ == "__main__":
    unittest.main()
