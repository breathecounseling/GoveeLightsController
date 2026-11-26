"""Hologram fan studio: GIF encoder, tile geometry, seamless loops, render speed, CLI, API."""
from __future__ import annotations

import io
import json
import os
import random
import shutil
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stdout
from pathlib import Path

from goveelights.hologram import SCENES, Clip, FanLayout, gif
from goveelights.hologram import export
from goveelights.hologram.canvas import Canvas
from goveelights.hologram.drivers import Fan, FanDriver, FanHub, LanProtocolStub, ManualDriver
from goveelights.hologram.studio import resolve_colours

try:
    from PIL import Image
except ImportError:                                      # pragma: no cover
    Image = None


def lzw_reference(data: bytes, mcs: int = 8) -> bytes:
    """Textbook greedy GIF LZW, one byte at a time (what the fast encoder must equal)."""
    clear, eoi = 1 << mcs, (1 << mcs) + 1
    cs, nxt, table, out, buf, nb = mcs + 1, eoi + 1, {}, bytearray(), clear, mcs + 1

    def emit(code):
        nonlocal buf, nb
        buf |= code << nb
        nb += cs
        while nb >= 8:
            out.append(buf & 255)
            buf >>= 8
            nb -= 8

    if not data:
        emit(eoi)
    else:
        w = data[0]
        for k in data[1:]:
            c = table.get((w << 8) | k)
            if c is not None:
                w = c
                continue
            emit(w)
            if nxt < 4095:
                table[(w << 8) | k] = nxt
                nxt += 1
                if nxt > (1 << cs) and cs < 12:
                    cs += 1
            else:
                emit(clear)
                table.clear()
                cs, nxt = mcs + 1, eoi + 1
            w = k
        emit(w)
        emit(eoi)
    while nb > 0:
        out.append(buf & 255)
        buf >>= 8
        nb -= 8
    return bytes(out)


def frames_for(seed=1, n=4, size=61):
    r = random.Random(seed)
    out = []
    for _ in range(n):
        c = Canvas(size, size)
        for _ in range(25):
            c.circle(r.random() * size, r.random() * size, r.random() * 14,
                     (r.randrange(256), r.randrange(256), r.randrange(256)), r.choice(("set", "add", "over", "max")), 0.7)
        out.append(c)
    return out


def write_gif(frames, local=False):
    b = io.BytesIO()
    w = gif.GifWriter(b, frames[0].w, frames[0].h, 24, local=local)
    if not local:
        w.set_palette(gif.build_palette([gif.keys(f) for f in frames]))
    for f in frames:
        w.add(f)
    w.close()
    return b.getvalue()


class LzwTests(unittest.TestCase):
    def test_matches_reference_and_decodes(self):
        r = random.Random(5)
        cases = [b"", b"\x07", b"\x00" * 9000, bytes(r.randrange(256) for _ in range(30000)),
                 bytes(r.randrange(3) for _ in range(20000))]
        for _ in range(25):
            d = bytearray()
            for _ in range(150):
                d += bytes((r.randrange(5),)) * r.choice((1, 1, 2, 7, 60, 400, 6000))
            cases.append(bytes(d))
        for d in cases:
            enc = gif.lzw_encode(d)
            self.assertEqual(enc, lzw_reference(d), len(d))
            self.assertEqual(gif.lzw_decode(enc, 8), d)


class GifTests(unittest.TestCase):
    def test_round_trip_own_decoder(self):
        for local in (False, True):
            frames = frames_for(2)
            d = gif.decode(write_gif(frames, local))
            self.assertEqual((d["w"], d["h"], d["loop"]), (61, 61, 0))
            self.assertEqual(len(d["frames"]), 4)
            for f, got in zip(frames, d["frames"]):
                ref = f.rgb()
                worst = max(abs(a - b) for a, b in zip(ref, got["rgb"]))
                self.assertLessEqual(worst, 40 if not local else 12)

    def test_black_stays_exact_and_few_colours_are_exact(self):
        c = Canvas(40, 40)
        c.circle(20, 20, 12, (255, 33, 66))
        c.rect(0, 0, 8, 8, (0, 255, 0))
        d = gif.decode(write_gif([c]))
        self.assertEqual(d["frames"][0]["palette"][0], (0, 0, 0))
        self.assertEqual(d["frames"][0]["rgb"], c.rgb())         # 5-bit-exact colours survive

    def test_delays_average_to_fps(self):
        b = io.BytesIO()
        w = gif.GifWriter(b, 8, 8, 24)
        for _ in range(24):
            w.add(Canvas(8, 8))
        w.close()
        self.assertEqual(sum(f["delay"] for f in gif.decode(b.getvalue())["frames"]), 100)

    @unittest.skipIf(Image is None, "Pillow not installed")
    def test_pillow_reads_it(self):
        for local in (False, True):
            frames = frames_for(3, n=3, size=97)
            data = write_gif(frames, local)
            ours = gif.decode(data)
            im = Image.open(io.BytesIO(data))
            self.assertEqual(im.n_frames, 3)
            for i in range(3):
                im.seek(i)
                self.assertEqual(im.convert("RGB").tobytes(), ours["frames"][i]["rgb"])

    def test_bad_frame_size(self):
        w = gif.GifWriter(io.BytesIO(), 10, 10, 20)
        with self.assertRaises(ValueError):
            w.add(Canvas(9, 10))


class LayoutTests(unittest.TestCase):
    def test_geometry(self):
        lay = FanLayout("1x3", size=200, diameter_cm=40, spacing_cm=50)
        self.assertEqual(lay.pitch, 250)
        self.assertEqual(lay.canvas_size, (700, 200))
        self.assertEqual(lay.origins(), [(0, 0), (250, 0), (500, 0)])
        self.assertEqual(lay.hero(), 1)
        sq = FanLayout("2x2", size=100, diameter_cm=50, spacing_cm=60)
        self.assertEqual(sq.canvas_size, (220, 220))
        self.assertEqual(sq.origins(), [(0, 0), (120, 0), (0, 120), (120, 120)])
        self.assertEqual(FanLayout("1", size=300).canvas_size, (300, 300))
        self.assertEqual(FanLayout("1x2", diameter_cm=50).spacing_cm, 55.0)

    def test_rejects_bad_settings(self):
        for kw in ({"name": "3x3"}, {"size": 10}, {"diameter_cm": 40, "spacing_cm": 30},
                   {"diameter_cm": 40, "spacing_cm": 400}):
            with self.assertRaises(ValueError):
                FanLayout(**{"name": "1x2", **kw})

    def test_tiles_cut_at_centres_and_masked(self):
        lay = FanLayout("1x3", size=100, diameter_cm=40, spacing_cm=60)
        cv = Canvas(*lay.canvas_size)
        cols = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
        for (cx, cy), c in zip(lay.centres(), cols):
            cv.rect(cx - 5, cy - 5, cx + 5, cy + 5, c)
        cv.rect(100, 0, 150, 100, (255, 255, 255))           # in the gap between fan 1 and 2
        cv.span(0, 0, cv.w, (200, 200, 200))                  # top row: outside every circle
        tiles = lay.tiles(cv)
        self.assertEqual(len(tiles), 3)
        for t, c in zip(tiles, cols):
            self.assertEqual((t.w, t.h), (100, 100))
            self.assertEqual(t.pixel(50, 50), c)
            for (x, y) in ((0, 0), (99, 0), (0, 99), (99, 99), (20, 3)):
                self.assertEqual(t.pixel(x, y), (0, 0, 0))
            self.assertNotIn((255, 255, 255), {t.pixel(x, 50) for x in range(100)})

    def test_crop_outside_reads_black(self):
        cv = Canvas(10, 10)
        cv.rect(0, 0, 10, 10, (9, 9, 9))
        t = cv.crop(-5, -5, 10)
        self.assertEqual(t.pixel(0, 0), (0, 0, 0))
        self.assertEqual(t.pixel(7, 7), (9, 9, 9))


def diff(a: Canvas, b: Canvas) -> float:
    """Share of pixels that differ by more than a few levels."""
    n = 0
    pa, pb = a.p, b.p
    for k in range(3):
        n += sum(1 for x, y in zip(pa[k], pb[k]) if abs(x - y) > 6)
    return n / (a.n * 3)


class SceneTests(unittest.TestCase):
    def test_loops_are_seamless(self):
        for sid in SCENES:
            for name in ("1", "1x3", "2x2"):
                clip = Clip(sid, FanLayout(name, size=96), seconds=6, fps=12)
                first = [t for t in clip.tiles_at(0.0)]
                wrap = clip.tiles_at(1.0)                    # "frame N": must equal frame 0
                for a, b in zip(first, wrap):
                    self.assertLess(diff(a, b), 0.002, f"{sid} {name}: t=1 differs from t=0")
                # and the jump from the last frame back to the first is an ordinary step
                last = clip.frame(clip.count - 1)
                steps = [diff(x, y) for k in (3, 20, 40)
                         for x, y in zip(clip.frame(k), clip.frame(k + 1))]
                seam = max(diff(x, y) for x, y in zip(last, first))
                self.assertLessEqual(seam, max(steps) * 2.5 + 0.004, f"{sid} {name}: visible seam")

    def test_every_scene_draws_something_bright_inside_the_circle(self):
        for sid in SCENES:
            clip = Clip(sid, FanLayout("1", size=120))
            lit = 0
            for k in range(0, clip.count, max(1, clip.count // 6)):
                t = clip.frame(k)[0]
                lit += sum(1 for v in t.p[0] if v > 60) + sum(1 for v in t.p[1] if v > 60)
            self.assertGreater(lit, 2000, sid)

    def test_render_time_budget(self):
        # a full-size single-fan frame should stay well under a 24 fps frame budget x 3
        for sid in SCENES:
            clip = Clip(sid, FanLayout("1", size=480))
            t0 = time.perf_counter()
            n = 0
            for k in range(0, clip.count, clip.count // 8):
                clip.canvas_at(k / clip.count)
                n += 1
            per = (time.perf_counter() - t0) / n
            self.assertLess(per, 0.25, f"{sid}: {per * 1000:.0f} ms a frame")

    def test_palette_and_team_colours_are_used(self):
        a = Clip("fireworks", FanLayout("1", size=96), colors=["#00ff00"]).frame(20)[0]
        reds = sum(1 for r, g in zip(a.p[0], a.p[1]) if r > 150 and g < 60)
        self.assertEqual(reds, 0)
        b = Clip("football", FanLayout("1", size=96), team=["#0000ff", "#ffffff"]).frame(1)[0]
        self.assertTrue(any(bl > 150 and r < 60 for r, bl in zip(b.p[0], b.p[2])))

    def test_bad_arguments(self):
        with self.assertRaises(ValueError):
            Clip("nope")
        with self.assertRaises(ValueError):
            Clip("sleigh", seconds=30)
        with self.assertRaises(ValueError):
            Clip("sleigh", fps=60)


class ExportCliTests(unittest.TestCase):
    def test_cli_writes_files(self):
        from goveelights.cli import main
        with tempfile.TemporaryDirectory() as td:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = main(["hologram", "ghosts", "--layout", "1x2", "--size", "64", "--seconds", "2", "--fps", "10",
                           "--formats", "gif,png", "--out", td, "--json", "--zip-png", "--sheet"])
            self.assertEqual(rc, 0)
            res = json.loads(buf.getvalue().strip().splitlines()[-1])
            names = set(os.listdir(td))
            for k in (1, 2):
                self.assertIn(f"ghosts-64-1x2-fan{k}.gif", names)
                self.assertIn(f"ghosts-64-1x2-fan{k}-png.zip", names)
            self.assertEqual(set(res["files"]) - names, set())
            d = gif.decode((Path(td) / "ghosts-64-1x2-fan1.gif").read_bytes())
            self.assertEqual((d["w"], len(d["frames"])), (64, 20))
            import zipfile
            with zipfile.ZipFile(Path(td) / "ghosts-64-1x2-fan2-png.zip") as z:
                self.assertEqual(len(z.namelist()), 20)
                self.assertTrue(z.read("frame_0001.png").startswith(b"\x89PNG"))

    def test_cli_list_and_errors(self):
        from goveelights.cli import main
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main(["hologram", "--list"]), 0)
        self.assertIn("sleigh", out.getvalue())
        with self.assertRaises(SystemExit):
            main(["hologram", "dragon"])
        with self.assertRaises(SystemExit):
            main(["hologram", "sleigh", "--palette", "No Such Palette"])
        with self.assertRaises(SystemExit):
            main(["hologram", "sleigh", "--formats", "avi"])

    @unittest.skipUnless(export.ffmpeg_info(), "ffmpeg with H.264 not on PATH")
    def test_mp4(self):
        with tempfile.TemporaryDirectory() as td:
            res = export.export(Clip("wreath", FanLayout("1", size=64), seconds=2, fps=10), td, ["mp4"])
            self.assertEqual(res["files"], ["wreath-64.mp4"])
            self.assertGreater((Path(td) / "wreath-64.mp4").stat().st_size, 500)

    def test_mp4_skipped_without_ffmpeg(self):
        saved = dict(export._ffmpeg_cache)
        export._ffmpeg_cache["v"] = {}
        try:
            with tempfile.TemporaryDirectory() as td:
                res = export.export(Clip("wreath", FanLayout("1", size=32), seconds=2, fps=10), td, ["mp4", "gif"])
            self.assertIn("mp4", res["skipped"])
            self.assertEqual(res["files"], ["wreath-32.gif"])
        finally:
            export._ffmpeg_cache.clear()
            export._ffmpeg_cache.update(saved)

    def test_png_writer(self):
        c = Canvas(3, 2)
        c.rect(0, 0, 1, 1, (10, 20, 30))
        data = export.png_bytes(3, 2, c.rgb())
        if Image is not None:
            im = Image.open(io.BytesIO(data))
            self.assertEqual(im.getpixel((0, 0)), (10, 20, 30))
            self.assertEqual(im.getpixel((2, 1)), (0, 0, 0))


class DriverTests(unittest.TestCase):
    def test_hub_without_drivers_is_a_noop(self):
        hub = FanHub()
        r = hub.switch("sleigh")
        self.assertFalse(r[0]["ok"])
        self.assertIsNone(hub.cue({"preset": "x"}))
        self.assertEqual(len(hub.cue({"fans": "sleigh"})), 1)

    def test_stub_never_pretends(self):
        for d in (LanProtocolStub(), ManualDriver()):
            with self.assertRaises(NotImplementedError):
                d.play(Fan("a"), "x")
        hub = FanHub()
        hub.add(LanProtocolStub(), [Fan("f1")])
        r = hub.switch("sleigh")
        self.assertEqual((r[0]["fan"], r[0]["ok"]), ("f1", False))

    def test_base_class(self):
        with self.assertRaises(NotImplementedError):
            FanDriver().discover()


class _Presets:
    def list(self):
        return [{"id": "halloween", "name": "Halloween", "mtime": 0}]

    def get(self, pid):
        if pid != "halloween":
            raise KeyError(pid)
        return {"layers": [{"params": {"colors": ["#ff5000", "#7000ff"], "background": "#ffffff"}},
                           {"params": {"color": "#000000"}}]}


class _Cfg:
    def __init__(self, path):
        self.path = path


class _Engine:
    master = {"palette": ""}


class _Ctl:
    def __init__(self, tmp):
        self.cfg = _Cfg(Path(tmp) / "govee_config.json")
        self.presets = _Presets()
        self.user_palettes = {"Mine": ["#123456", "#abcdef"]}
        self.engine = _Engine()
        self.games = {}
        self.scene_dict = {"layers": [{"params": {"colors": ["#00ff00"]}}]}
        self.vcfg = {}


class ColourTests(unittest.TestCase):
    def test_sources(self):
        with tempfile.TemporaryDirectory() as td:
            ctl = _Ctl(td)
            self.assertIsNone(resolve_colours(ctl, ""))
            self.assertEqual(resolve_colours(ctl, "house"), ["#00ff00"])
            ctl.engine.master = {"palette": "Mine"}
            self.assertEqual(resolve_colours(ctl, "house"), ["#123456", "#abcdef"])
            self.assertEqual(resolve_colours(ctl, "preset:halloween"), ["#ff5000", "#7000ff"])
            self.assertEqual(resolve_colours(ctl, "pal:Mine"), ["#123456", "#abcdef"])
            for bad in ("pal:Nope", "weird"):
                with self.assertRaises(ValueError):
                    resolve_colours(ctl, bad)


class ApiTests(unittest.TestCase):
    def setUp(self):
        from goveelights.app_server import Server, make_handler
        from goveelights.auth import Auth
        self.tmp = tempfile.TemporaryDirectory()
        self.auth = Auth(Path(self.tmp.name) / "auth.json")
        self.auth.set_password("a long enough password")
        self.auth.data["lan_without_login"] = False
        self.token, _ = self.auth.new_session()
        self.srv = Server(("127.0.0.1", 0), make_handler(_Ctl(self.tmp.name), lambda: None, self.auth))
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def tearDown(self):
        for st in self.srv.RequestHandlerClass.studio_box or []:
            st.stop()
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def req(self, path, body=None, cookie=True, csrf=True):
        from goveelights.auth import COOKIE
        h = {}
        if cookie:
            h["Cookie"] = f"{COOKIE}={self.token}"
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
            if csrf:
                h["X-Requested-With"] = "lights"
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, headers=h)
        try:
            with urllib.request.urlopen(r, timeout=60) as resp:
                return resp.status, resp.read(), dict(resp.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read(), dict(e.headers)

    def test_needs_login_and_csrf_header(self):
        self.assertEqual(self.req("/api/hologram", cookie=False)[0], 401)
        self.assertEqual(self.req("/api/hologram/preview", {"scene": "sleigh"}, cookie=False)[0], 401)
        self.assertEqual(self.req("/api/hologram/preview", {"scene": "sleigh"}, csrf=False)[0], 403)
        self.assertEqual(self.req("/api/hologram/make", {"scene": "sleigh"}, csrf=False)[0], 403)
        self.assertEqual(self.req("/api/hologram/file?name=x.gif", cookie=False)[0], 401)

    def test_info_and_preview(self):
        code, body, _ = self.req("/api/hologram")
        self.assertEqual(code, 200)
        info = json.loads(body)
        self.assertEqual({s["id"] for s in info["scenes"]}, set(SCENES))
        self.assertIn("Mine", info["palettes"])
        self.assertEqual(info["presets"][0]["id"], "halloween")
        code, body, _ = self.req("/api/hologram/preview", {"scene": "pumpkin", "layout": "1x2", "colors": "preset:halloween",
                                                           "diameter": 42, "spacing": 50})
        self.assertEqual(code, 200)
        r = json.loads(body)
        self.assertEqual(len(r["tiles"]), 2)
        self.assertEqual(r["colors"], ["#ff5000", "#7000ff"])
        import base64
        g = gif.decode(base64.b64decode(r["tiles"][0].split(",", 1)[1]))
        self.assertEqual(len(g["frames"]), round(r["seconds"] * 12))
        for bad in ({"scene": "x"}, {"scene": "sleigh", "layout": "9x9"}, {"scene": "sleigh", "spacing": 10, "layout": "1x2"},
                    {"scene": "sleigh", "colors": "pal:Nope"}, {"scene": "football", "team": ["red"]},
                    {"scene": "sleigh", "seconds": 99}):
            self.assertEqual(self.req("/api/hologram/preview", bad)[0], 400, bad)

    def test_make_and_download(self):
        self.assertEqual(self.req("/api/hologram/make", {"scene": "snowglobe", "size": 333})[0], 400)
        self.assertEqual(self.req("/api/hologram/make", {"scene": "snowglobe", "formats": ["avi"]})[0], 400)
        code, body, _ = self.req("/api/hologram/make", {"scene": "snowglobe", "layout": "1x2", "size": 240,
                                                        "seconds": 2, "fps": 10, "formats": ["gif", "png"]})
        self.assertEqual(code, 200, body)
        for _ in range(240):
            j = json.loads(self.req("/api/hologram/job")[1])
            if j["state"] != "running":
                break
            time.sleep(0.25)
        self.assertEqual(j["state"], "done", j)
        self.assertIn("snowglobe-240-1x2-fan1.gif", j["files"])
        self.assertIn("snowglobe-240-1x2-fan2-png.zip", j["files"])
        code, body, hdr = self.req("/api/hologram/file?name=snowglobe-240-1x2-fan1.gif")
        self.assertEqual(code, 200)
        self.assertEqual(hdr["Content-Type"], "image/gif")
        self.assertIn("attachment", hdr["Content-Disposition"])
        self.assertEqual(gif.decode(body)["w"], 240)
        for bad in ("../govee_config.json", "govee_config.json", "progress.json", ".x.gif", "nothere.gif"):
            self.assertEqual(self.req("/api/hologram/file?name=" + urllib.request.quote(bad))[0], 400, bad)
        out = Path(self.tmp.name) / "hologram_clips"
        self.assertTrue((out / "snowglobe-240-1x2-fan2.gif").is_file())


if __name__ == "__main__":
    unittest.main()
