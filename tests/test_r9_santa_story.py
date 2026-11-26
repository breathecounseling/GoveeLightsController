"""Round 9: the "Santa lands" story scene, projector / screen frames, LED matrices (area-
averaged downsampling, raw RGB for WLED), the CLI for them, live DDP streaming and the
studio / app page."""
from __future__ import annotations

import hashlib
import io
import json
import os
import random
import re
import subprocess
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from goveelights.hologram import SCENES, Clip, FanLayout, export, gif
from goveelights.hologram import story
from goveelights.hologram.canvas import Canvas
from goveelights.hologram.targets import (FrameLayout, MatrixLayout, ansi, box_down, led_view, parse_frame,
                                          parse_wh)

ROOT = Path(__file__).resolve().parent.parent


def diff(a: Canvas, b: Canvas) -> float:
    n = 0
    for k in range(3):
        n += sum(1 for x, y in zip(a.p[k], b.p[k]) if abs(x - y) > 6)
    return n / (a.n * 3)


def lit(c: Canvas, x0=0, y0=0, x1=None, y1=None) -> int:
    x1, y1 = x1 or c.w, y1 or c.h
    return sum(1 for y in range(y0, y1) for x in range(x0, x1) if max(c.pixel(x, y)) > 80)


def digest(c: Canvas) -> str:
    return hashlib.sha256(c.rgb()).hexdigest()


class StoryTests(unittest.TestCase):
    def test_registered_with_its_own_length(self):
        cls = SCENES["santa-landing"]
        self.assertEqual(list(SCENES)[:2], ["sleigh", "santa-landing"])       # the Christmas scenes together
        self.assertTrue(cls.story)
        self.assertGreaterEqual(cls.seconds, 20)
        self.assertLessEqual(cls.seconds, 30)
        info = cls.info()
        self.assertEqual(info["name"], "Santa lands")
        self.assertEqual(info["max_seconds"], 40)
        Clip("santa-landing", FanLayout("1", size=64), seconds=40)
        with self.assertRaises(ValueError):
            Clip("santa-landing", FanLayout("1", size=64), seconds=41)
        with self.assertRaises(ValueError):                                     # others keep their 12 s cap
            Clip("sleigh", seconds=20)

    def test_beats_are_in_order(self):
        b = [story.T_ENTER, story.T_TOUCH, story.T_SETTLE, story.T_CROUCH, story.T_JUMP, story.T_LAND,
             story.T_STAND, story.T_WAVE0, story.T_WAVE1, story.T_TURNB, story.T_JUMP2, story.T_SEAT,
             story.T_SEATED, story.T_LIFT, story.T_GONE]
        self.assertEqual(b, sorted(b))
        self.assertLess(story.T_GONE + story.TRAIL_LIFE, 1.0)                   # the trail is gone before the seam

    def test_deterministic(self):
        a = Clip("santa-landing", FanLayout("1x2", size=96), fps=12)
        b = Clip("santa-landing", FanLayout("1x2", size=96), fps=12)
        for i in (0, 40, 130, 200):
            self.assertEqual([digest(t) for t in a.frame(i)], [digest(t) for t in b.frame(i)])

    def test_loop_is_seamless(self):
        for name in ("1", "1x3", "2x3"):
            clip = Clip("santa-landing", FanLayout(name, size=80), fps=12)
            first, last, wrap = clip.frame(0), clip.frame(clip.count - 1), clip.tiles_at(1.0)
            for a, b in zip(first, wrap):
                self.assertLess(diff(a, b), 0.002, name)
            seam = max(diff(x, y) for x, y in zip(last, first))
            self.assertLess(seam, 0.03, f"{name}: the last frame should run into the first")

    def test_the_story_plays(self):
        sc = Clip("santa-landing", FanLayout("1", size=200)).scene
        self.assertIsNone(sc.group_at(0.0))                                     # empty sky at the seam
        self.assertIsNone(sc.group_at(0.99))
        x, y, ang, _, _ = sc.group_at(story.T_TOUCH + 0.001)                    # landed, level
        self.assertAlmostEqual(y, sc.G, delta=0.06 * sc.R)
        self.assertLess(abs(ang), 0.05)
        self.assertIsNone(sc.santa_at(0.3))                                     # in his seat
        pose = sc.santa_at((story.T_WAVE0 + story.T_WAVE1) / 2)
        self.assertEqual(pose[2], "wave")
        self.assertEqual((pose[0], pose[1]), sc.spot)
        hx, hy = sc.santa_at((story.T_JUMP + story.T_LAND) / 2)[:2]           # mid-jump: above the roof
        self.assertLess(hy, sc.G - 0.1 * sc.R)
        gx, gy, _, _, _ = sc.group_at(story.T_GONE - 0.01)                      # leaving up and to the right
        self.assertLess(gy, sc.G - 0.5 * sc.R)
        self.assertGreater(gx, sc.A[0])

    def test_santa_waves_his_arm(self):
        clip = Clip("santa-landing", FanLayout("1", size=240))
        v = [(story.T_WAVE0 + (story.T_WAVE1 - story.T_WAVE0) * f) for f in (0.3, 0.35, 0.4)]
        frames = [clip.tiles_at(t)[0] for t in v]
        sx = int(clip.scene.spot[0] - clip.layout.origins()[0][0])
        box = (max(0, sx - 60), 30, sx + 10, 160)
        changes = [diff(_cut(a, box), _cut(b, box)) for a, b in zip(frames, frames[1:])]
        self.assertGreater(max(changes), 0.01)

    def test_words_appear(self):
        clip = Clip("santa-landing", FanLayout("1", size=240))
        gold = lambda c: sum(1 for r, g, b in zip(*c.p) if r > 230 and 150 < g < 220 and b < 80)
        before = gold(clip.tiles_at(story.T_HO0 - 0.01)[0])
        during = gold(clip.tiles_at(story.T_HO0 + 0.05)[0])
        self.assertGreater(during, before + 100)

    def test_two_rows_draw_a_bigger_santa(self):
        one = Clip("santa-landing", FanLayout("1x3", size=120)).scene
        two = Clip("santa-landing", FanLayout("2x3", size=120)).scene
        self.assertGreater(two.u, one.u * 1.5)
        lay = FanLayout("2x3", size=120)
        bottom_mid = lay.centres()[4]
        self.assertLess(abs(two.spot[0] - bottom_mid[0]), 0.5 * two.R)          # Santa waves inside a fan

    def test_render_budget(self):
        import time
        clip = Clip("santa-landing", FanLayout("1", size=480))
        t0 = time.perf_counter()
        for t in (0.2, 0.5, 0.88):
            clip.canvas_at(t)
        self.assertLess((time.perf_counter() - t0) / 3, 0.25)


def _cut(c: Canvas, box) -> Canvas:
    x0, y0, x1, y1 = box
    out = Canvas(x1 - x0, y1 - y0)
    for k in range(3):
        for y in range(y0, y1):
            out.p[k][(y - y0) * out.w:(y - y0 + 1) * out.w] = c.p[k][y * c.w + x0:y * c.w + x1]
    return out


class TargetTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_wh("1920x1080"), (1920, 1080))
        self.assertEqual(parse_wh(" 64 × 32 "), (64, 32))
        self.assertEqual(parse_frame("16:9", 720), (1280, 720))
        self.assertEqual(parse_frame("4:3", 768), (1024, 768))
        for bad in ("", "1920", "axb", "10:0"):
            with self.assertRaises(ValueError):
                parse_frame(bad)

    def test_frame_sizes_and_backgrounds(self):
        for (w, h) in ((1280, 720), (720, 1280), (400, 400)):
            clip = Clip("santa-landing", FrameLayout(w, h), fps=10, seconds=4)
            tiles = clip.frame(5)
            self.assertEqual(len(tiles), 1)
            self.assertEqual((tiles[0].w, tiles[0].h), (w, h))
        black = Clip("sleigh", FrameLayout(320, 180), fps=10).tiles_at(0.3)[0]
        self.assertEqual(black.pixel(2, 2), (0, 0, 0))                          # black = no light
        night = Clip("sleigh", FrameLayout(320, 180, "night"), fps=10).tiles_at(0.3)[0]
        top, bottom = night.pixel(2, 0), night.pixel(2, 179)
        self.assertGreater(bottom[2], top[2])                                   # a blue gradient
        self.assertGreater(top[2], 0)
        for bad in ((32, 720), (5000, 100), (3840, 3840)):
            with self.assertRaises(ValueError):
                FrameLayout(*bad)
        with self.assertRaises(ValueError):
            FrameLayout(640, 360, "pink")

    def test_frame_fills_the_width(self):
        # every scene puts light across a wide frame, not only in the middle circle
        for sid in SCENES:
            clip = Clip(sid, FrameLayout(384, 128), fps=10, seconds=4)
            left = right = 0
            for t in (0.1, 0.3, 0.5, 0.7, 0.9):
                tl = clip.tiles_at(t)[0]
                left += lit(tl, 0, 0, 100, 128)
                right += lit(tl, 284, 0, 384, 128)
            self.assertGreater(left, 0, sid)
            self.assertGreater(right, 0, sid)

    def test_box_down_is_an_exact_area_average(self):
        r = random.Random(3)
        c = Canvas(48, 24)
        for k in range(3):
            c.p[k][:] = bytes(r.randrange(256) for _ in range(c.n))
        for f in (1, 2, 3, 4, 6, 8, 12):
            o = box_down(c, f)
            self.assertEqual((o.w, o.h), (48 // f, 24 // f))
            for ch in range(3):
                for y in range(o.h):
                    for x in range(o.w):
                        s = sum(c.p[ch][(y * f + j) * 48 + x * f + i] for j in range(f) for i in range(f))
                        self.assertEqual(o.p[ch][y * o.w + x], (s + f * f // 2) // (f * f))
        big = Canvas(32, 32)
        big.rect(0, 0, 32, 32, (255, 255, 255))
        self.assertEqual(box_down(big, 16).pixel(1, 1), (255, 255, 255))      # no 16-bit overflow at 16 x 16
        with self.assertRaises(ValueError):
            box_down(big, 17)

    def test_thin_line_becomes_dim_not_gone(self):
        c = Canvas(64, 64)
        c.rect(0, 30, 64, 32, (255, 255, 255))                                 # 2 px line, 1/4 of an 8 px cell
        o = box_down(c, 8)
        self.assertEqual(o.pixel(3, 3), (64, 64, 64))

    def test_matrix(self):
        m = MatrixLayout(64, 32)
        self.assertEqual(m.out_size, (64, 32))
        self.assertEqual((m.frame.w, m.frame.h), (64 * m.k, 32 * m.k))
        clip = Clip("santa-landing", m, fps=10, seconds=6)
        t = clip.tiles_at(0.5)[0]
        self.assertEqual((t.w, t.h), (64, 32))
        self.assertGreater(lit(t), 40)
        g = Clip("santa-landing", MatrixLayout(64, 32, gamma=2.2), fps=10, seconds=6).tiles_at(0.5)[0]
        self.assertLess(sum(g.p[1]), sum(t.p[1]))                               # gamma darkens mid-tones
        for bad in ((4, 4), (300, 8), (200, 200)):
            with self.assertRaises(ValueError):
                MatrixLayout(*bad)

    def test_wire_order(self):
        m = MatrixLayout(8, 8, serpentine=True)
        c = Canvas(8, 8)
        c.rect(0, 1, 1, 2, (255, 0, 0))                                          # row 1, first pixel
        data = m.wire_order(c)
        self.assertEqual(len(data), 8 * 8 * 3)
        self.assertEqual(data[(8 + 7) * 3:(8 + 7) * 3 + 3], b"\xff\x00\x00")     # reversed row
        self.assertEqual(MatrixLayout(8, 8).wire_order(c)[8 * 3:8 * 3 + 3], b"\xff\x00\x00")

    def test_led_view_and_ansi(self):
        c = Canvas(4, 2)
        c.rect(0, 0, 4, 2, (10, 200, 30))
        v = led_view(c, 5, 1)
        self.assertEqual((v.w, v.h), (20, 10))
        self.assertEqual(v.pixel(0, 0), (10, 200, 30))
        self.assertEqual(v.pixel(4, 0), (0, 0, 0))                              # the gap between LEDs
        txt = ansi(c)
        self.assertEqual(txt.count("▀"), 4)
        self.assertIn("38;2;10;200;30", txt)

    def test_fan_layouts_still_work(self):
        for name in ("1", "1x4", "2x2", "1x5", "1x6", "2x3", "2x4"):
            lay = FanLayout(name, size=64)
            clip = Clip("sleigh", lay, fps=10, seconds=2)
            self.assertEqual(len(clip.frame(3)), lay.count)
            self.assertEqual(lay.out_size, (64, 64))


class ExportTests(unittest.TestCase):
    def test_frame_export(self):
        with tempfile.TemporaryDirectory() as td:
            clip = Clip("santa-landing", FrameLayout(160, 90), fps=10, seconds=3)
            res = export.export(clip, td, ["gif", "png", "rgb"])
            self.assertIn("santa-landing-frame-160x90.gif", res["files"])
            self.assertIn("rgb", res["skipped"])                                # matrix only
            d = gif.decode((Path(td) / "santa-landing-frame-160x90.gif").read_bytes())
            self.assertEqual((d["w"], d["h"], len(d["frames"])), (160, 90, 30))
            self.assertEqual(len(list((Path(td) / "santa-landing-frame-160x90-png").glob("*.png"))), 30)

    def test_matrix_export(self):
        with tempfile.TemporaryDirectory() as td:
            clip = Clip("santa-landing", MatrixLayout(32, 16, serpentine=True), fps=10, seconds=3)
            res = export.export(clip, td, ["gif", "rgb", "view"])
            stem = "santa-landing-matrix-32x16"
            self.assertEqual(set(res["files"]), {f"{stem}.gif", f"{stem}.rgb", f"{stem}.json", f"{stem}-view.gif"})
            raw = (Path(td) / f"{stem}.rgb").read_bytes()
            self.assertEqual(len(raw), 32 * 16 * 3 * 30)
            note = json.loads((Path(td) / f"{stem}.json").read_text())
            self.assertEqual((note["width"], note["height"], note["frames"], note["fps"]), (32, 16, 30, 10))
            self.assertIn("serpentine", note["order"])
            self.assertEqual(gif.decode((Path(td) / f"{stem}.gif").read_bytes())["w"], 32)
            v = gif.decode((Path(td) / f"{stem}-view.gif").read_bytes())
            self.assertEqual(v["w"] % 32, 0)
            self.assertGreater(v["w"], 32)

    @unittest.skipUnless(export.ffmpeg_info(), "ffmpeg with H.264 not on PATH")
    def test_frame_mp4(self):
        with tempfile.TemporaryDirectory() as td:
            clip = Clip("santa-landing", FrameLayout(192, 108), fps=10, seconds=2)
            res = export.export(clip, td, ["mp4"])
            self.assertEqual(res["files"], ["santa-landing-frame-192x108.mp4"], res)


class CliTests(unittest.TestCase):
    def run_cli(self, *args):
        from goveelights.cli import main
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(["hologram", *args])
        return rc, buf.getvalue()

    def test_frame_and_matrix(self):
        with tempfile.TemporaryDirectory() as td:
            rc, out = self.run_cli("santa-landing", "--frame", "16:9", "--size", "90", "--seconds", "2", "--fps", "10",
                                   "--formats", "gif", "--json", "--out", td)
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(out.strip().splitlines()[-1])["files"], ["santa-landing-frame-160x90.gif"])
            rc, out = self.run_cli("santa-landing", "--matrix", "16x8", "--seconds", "2", "--fps", "10",
                                   "--json", "--out", td)
            self.assertEqual(rc, 0)
            files = json.loads(out.strip().splitlines()[-1])["files"]
            self.assertIn("santa-landing-matrix-16x8.rgb", files)                 # default gif,rgb,view
            self.assertIn("santa-landing-matrix-16x8-view.gif", files)

    def test_preview_and_errors(self):
        rc, out = self.run_cli("santa-landing", "--matrix", "16x8", "--seconds", "3", "--fps", "10", "--preview")
        self.assertEqual(rc, 0)
        self.assertEqual(out.count("t = "), 6)
        self.assertEqual(len(re.findall("▀", out)), 6 * 16 * 4)
        for bad in (["--frame", "1x1"], ["--matrix", "64x32", "--frame", "16:9"], ["--matrix", "nope"],
                    ["--seconds", "50"], ["--formats", "rgb"], ["--gamma", "9", "--matrix", "16x8"]):
            with self.assertRaises(SystemExit, msg=bad):
                self.run_cli("santa-landing", *bad)

    def test_list_shows_the_story(self):
        rc, out = self.run_cli("--list")
        self.assertIn("santa-landing", out)
        self.assertIn("--matrix", out)


class LiveTests(unittest.TestCase):
    def test_stream_sends_ddp_frames(self):
        from goveelights.hologram import live

        class Sock:
            def __init__(self):
                self.sent = []

            def sendto(self, pkt, addr):
                self.sent.append((pkt, addr))

        clip = Clip("santa-landing", MatrixLayout(32, 16), fps=10, seconds=2)
        frames = live.render(clip)
        self.assertEqual(len(frames), 20)
        self.assertEqual(len(frames[0]), 32 * 16 * 3)
        s = Sock()
        now = [0.0]
        n = live.stream(frames, "10.0.0.9", 4048, 10, loops=2, sock=s, clock=lambda: now[0],
                        sleep=lambda d: now.__setitem__(0, now[0] + d))
        self.assertEqual(n, 40)
        self.assertAlmostEqual(now[0], 4.0, places=3)                          # paced at the clip's fps
        self.assertEqual(len(s.sent), 40 * 2)                                   # 1536 bytes: two packets a frame
        pkt, addr = s.sent[0]
        self.assertEqual(addr, ("10.0.0.9", 4048))
        self.assertEqual(pkt[2:4], b"\x0b\x01")                                 # DDP RGB, display id 1
        self.assertEqual(s.sent[1][0][0] & 1, 1)                                # push flag on the last packet
        self.assertEqual(live.parse_host("1.2.3.4:5000"), ("1.2.3.4", 5000))
        with self.assertRaises(ValueError):
            live.render(Clip("sleigh", FanLayout("1", size=64), fps=10, seconds=2))
        with self.assertRaises(ValueError):
            live.parse_host("")


class StudioTests(unittest.TestCase):
    def test_parse_displays(self):
        from goveelights.hologram.studio import parse

        class Ctl:
            presets = None
        a = parse(Ctl(), {"scene": "santa-landing", "display": "frame", "frame": "1920x1080", "background": "night"}, False)
        self.assertEqual((a["layout"].w, a["layout"].h, a["layout"].background), (1920, 1080, "night"))
        p = parse(Ctl(), {"scene": "santa-landing", "display": "frame", "frame": "1920x1080"}, True)
        self.assertLessEqual(max(p["layout"].w, p["layout"].h), 320)          # previews are small
        self.assertEqual(p["seconds"], 24)
        m = parse(Ctl(), {"scene": "santa-landing", "display": "matrix", "matrix": "64x32", "gamma": 2.2,
                          "serpentine": True}, False)
        self.assertEqual((m["layout"].w, m["layout"].gamma, m["layout"].serpentine), (64, 2.2, True))
        self.assertEqual(parse(Ctl(), {"scene": "santa-landing", "seconds": 30}, False)["seconds"], 30)
        for bad in ({"display": "tv"}, {"display": "frame", "frame": "9x9"}, {"display": "matrix", "matrix": "999x2"},
                    {"display": "frame", "background": "red"}, {"seconds": 45}, {"layout": "3x3"}):
            with self.assertRaises(ValueError, msg=bad):
                parse(Ctl(), {"scene": "santa-landing", **bad}, False)
        with self.assertRaises(ValueError):
            parse(Ctl(), {"scene": "sleigh", "seconds": 20}, False)

    def test_preview_of_a_matrix_is_blown_up(self):
        from goveelights.hologram.studio import Studio

        class Ctl:
            presets = None
        with tempfile.TemporaryDirectory() as td:
            r = Studio(td).preview(Ctl(), {"scene": "snowglobe", "display": "matrix", "matrix": "32x16", "seconds": 2})
            self.assertEqual((r["display"], r["out"]), ("matrix", [32, 16]))
            import base64
            g = gif.decode(base64.b64decode(r["tiles"][0].split(",", 1)[1]))
            self.assertEqual(g["w"], 320)


class PageTests(unittest.TestCase):
    def setUp(self):
        self.html = (ROOT / "goveelights" / "web" / "app.html").read_text(encoding="utf-8")

    def test_display_selector(self):
        for k in ('id="hoDisp"', "data-v=\"fans\"", "data-v=\"frame\"", "data-v=\"matrix\"", 'id="hoFrame"',
                  'id="hoMatrix"', 'id="hoBg"', 'id="hoGamma"', 'id="hoSerp"', 'id="hoRgb"', 'id="hoView"'):
            self.assertIn(k, self.html)
        self.assertIn("#hoDisp button{min-height:44px", self.html)
        self.assertIn("#hoLayouts button{flex:1 0 17%;min-height:44px}", self.html)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_inline_script_parses(self):
        scripts = re.findall(r"<script>(.*?)</script>", self.html, re.S)
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write("\n;\n".join(scripts))
        try:
            r = subprocess.run(["node", "--check", f.name], capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
        finally:
            os.unlink(f.name)


if __name__ == "__main__":
    unittest.main()
