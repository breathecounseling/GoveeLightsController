"""Write a clip out as files a fan's app or SD card takes: animated GIF, H.264 MP4 when
ffmpeg is installed (skipped otherwise) and, if asked, the PNG frames (a folder or a zip
per fan). The default is GIF + MP4, the same as the app.

The same goes for a projector / screen frame (one picture, any aspect) and an LED matrix
(targets.py), which can also have:
    rgb   the raw frames, W x H x 3 bytes each, back to back, in wiring order, plus a
          .json note of the size and frame rate (stream it to WLED over DDP, or feed any
          pixel-pushing tool)
    view  a blown-up GIF that looks like the LEDs, to check the art by eye

One pass renders each frame once and feeds every fan's GIF, PNGs and ffmpeg pipe, so a
four-fan set costs little more than one fan."""
from __future__ import annotations

import io
import os
import shutil
import struct
import subprocess
import time
import zipfile
import zlib
from pathlib import Path

from . import gif
from .canvas import Canvas
from .render import Clip

FORMATS = ("gif", "mp4", "png", "rgb", "view")
MATRIX_ONLY = ("rgb", "view")
DEFAULT_FORMATS = ("gif", "mp4")
FFMPEG_WAIT = 300           # seconds an encoder may take to finish after the last frame
_H264 = ("libx264", "libopenh264", "h264_mf", "h264_videotoolbox", "h264_nvenc", "h264_qsv")
_ffmpeg_cache: dict = {}


# ---------------------------------------------------------------- PNG
def png_bytes(w: int, h: int, rgb: bytes, level: int = 6) -> bytes:
    stride = w * 3
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        raw += rgb[y * stride:(y + 1) * stride]

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), level)) + chunk(b"IEND", b""))


def contact_sheet(frames: list[Canvas], cols: int = 4, pad: int = 6) -> bytes:
    """Frames side by side on a grey board (for checking the art), as PNG bytes."""
    if not frames:
        raise ValueError("no frames")
    w, h = frames[0].w, frames[0].h
    rows = (len(frames) + cols - 1) // cols
    W, H = cols * (w + pad) + pad, rows * (h + pad) + pad
    board = Canvas(W, H)
    board.rect(0, 0, W, H, (40, 40, 46))
    for k, f in enumerate(frames):
        ox, oy = pad + (k % cols) * (w + pad), pad + (k // cols) * (h + pad)
        for c in range(3):
            src, dst = f.p[c], board.p[c]
            for y in range(h):
                dst[(oy + y) * W + ox:(oy + y) * W + ox + w] = src[y * w:(y + 1) * w]
    return png_bytes(W, H, board.rgb())


# ---------------------------------------------------------------- ffmpeg
def ffmpeg_info() -> dict:
    """{"path": ..., "encoder": ...} if ffmpeg with an H.264 encoder is on PATH, else {}."""
    if "v" in _ffmpeg_cache:
        return _ffmpeg_cache["v"]
    res: dict = {}
    path = shutil.which("ffmpeg")
    if path:
        try:
            out = subprocess.run([path, "-hide_banner", "-encoders"], capture_output=True, text=True,
                                 timeout=20, creationflags=_NO_WINDOW).stdout
            have = {ln.split()[1] for ln in out.splitlines() if len(ln.split()) > 1 and ln.startswith(" V")}
            enc = next((e for e in _H264 if e in have), None)
            if enc:
                res = {"path": path, "encoder": enc}
        except (OSError, subprocess.SubprocessError):
            res = {}
    _ffmpeg_cache["v"] = res
    return res


_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _ffmpeg_proc(info: dict, size, fps: float, out: Path):
    w, h = size if isinstance(size, tuple) else (size, size)
    enc = info["encoder"]
    args = [info["path"], "-hide_banner", "-loglevel", "error", "-y",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", f"{fps:g}", "-i", "-",
            "-an", "-c:v", enc, "-pix_fmt", "yuv420p"]
    if enc == "libx264":
        args += ["-preset", "medium", "-crf", "16", "-profile:v", "high", "-tune", "animation"]
    else:
        args += ["-b:v", "6M"]
    args += ["-movflags", "+faststart", str(out)]
    return subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            creationflags=_NO_WINDOW)


# ---------------------------------------------------------------- the export
def names(clip: Clip) -> list[str]:
    """Base file name of each fan's clip."""
    lay = clip.layout
    if hasattr(lay, "file_stem"):                   # a frame or a matrix: one picture
        return [lay.file_stem(clip.scene_id)]
    stem = f"{clip.scene_id}-{lay.size}"
    if lay.count == 1:
        return [stem]
    return [f"{stem}-{lay.name}-fan{k + 1}" for k in range(lay.count)]


def export(clip: Clip, out_dir, formats=DEFAULT_FORMATS, local_palette: bool = False,
           progress=None, zip_png: bool = False) -> dict:
    """Write the clip. Returns {"files": [...], "skipped": {format: reason}, "seconds": t}.
    progress(done_frames, total_frames) is called as it goes."""
    t0 = time.time()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    formats = [f for f in formats if f in FORMATS]
    lay = clip.layout
    base = names(clip)
    files: list[str] = []
    skipped: dict = {}
    n = clip.count
    ow, oh = getattr(lay, "out_size", (lay.size, lay.size))
    matrix = getattr(lay, "kind", "") == "matrix"
    for f in MATRIX_ONLY:
        if f in formats and not matrix:
            formats.remove(f)
            skipped[f] = "only for an LED matrix"

    procs = []
    if "mp4" in formats:
        info = ffmpeg_info()
        if not info:
            skipped["mp4"] = "ffmpeg with an H.264 encoder was not found on PATH"
        elif ow % 2 or oh % 2:
            skipped["mp4"] = "MP4 (yuv420p) needs an even width and height"
        else:
            for b in base:
                p = out / f"{b}.mp4"
                procs.append((p, _ffmpeg_proc(info, (ow, oh), clip.fps, p)))

    writers = []
    gfiles = []
    if "gif" in formats:
        pals = None
        if not local_palette:          # one palette per fan from a sample of its frames
            step = max(1, n // 16)
            samples = [[] for _ in base]
            for i in range(0, n, step):
                for k, tile in enumerate(clip.frame(i)):
                    samples[k].append(gif.keys(tile))
            pals = [gif.build_palette(s) for s in samples]
        for k, b in enumerate(base):
            fh = open(out / f"{b}.gif", "wb")
            w = gif.GifWriter(fh, ow, oh, clip.fps, local=local_palette)
            if pals:
                w.set_palette(pals[k])
            writers.append(w)
            gfiles.append(fh)

    pngdirs = []
    zips = []
    if "png" in formats:
        for b in base:
            if zip_png:
                zp = out / f"{b}-png.zip"
                zips.append((zp, zipfile.ZipFile(zp, "w", zipfile.ZIP_STORED)))
            else:
                d = out / f"{b}-png"
                d.mkdir(exist_ok=True)
                for old in d.glob("frame_*.png"):
                    old.unlink()
                pngdirs.append(d)

    rawf = view = vfh = None
    vscale = 1
    if "rgb" in formats:
        rawf = open(out / f"{base[0]}.rgb", "wb")
    if "view" in formats:
        from .targets import led_view
        vscale = max(4, min(16, 720 // max(ow, oh)))
        vfh = open(out / f"{base[0]}-view.gif", "wb")
        view = gif.GifWriter(vfh, ow * vscale, oh * vscale, clip.fps)

    ok = False
    try:
        for i in range(n):
            tiles = clip.frame(i)
            for k, tile in enumerate(tiles):
                if rawf:
                    rawf.write(lay.wire_order(tile))
                if view:
                    view.add(led_view(tile, vscale, max(1, vscale // 5)))
                if writers:
                    writers[k].add(tile)
                if procs or pngdirs or zips:
                    rgb = tile.rgb()
                    if procs:
                        try:
                            procs[k][1].stdin.write(rgb)
                        except (BrokenPipeError, OSError):
                            pass
                    if pngdirs or zips:
                        data = png_bytes(ow, oh, rgb)
                        nm = f"frame_{i + 1:04d}.png"
                        if pngdirs:
                            (pngdirs[k] / nm).write_bytes(data)
                        else:
                            zips[k][1].writestr(nm, data)
            if progress:
                progress(i + 1, n)
        ok = True
    finally:
        for w, fh in zip(writers, gfiles):
            try:
                w.close()
            finally:
                fh.close()
        for _, z in zips:
            z.close()
        if rawf:
            rawf.close()
        if view:
            try:
                view.close()
            finally:
                vfh.close()
        for p, pr in procs:
            try:
                pr.stdin.close()
            except OSError:
                pass
            pr.stdin = None                  # closed: communicate() must not flush it
            if not ok:                       # the frames failed: no half-written MP4s left behind
                _stop(pr, p)
    for k, b in enumerate(base):
        if writers:
            files.append(f"{b}.gif")
    for p, pr in procs:
        try:
            _, err = pr.communicate(timeout=FFMPEG_WAIT)
        except subprocess.TimeoutExpired:
            _stop(pr, p)
            skipped["mp4"] = f"ffmpeg did not finish within {FFMPEG_WAIT} s"
            continue
        err = (err or b"").decode("utf-8", "replace").strip()
        if pr.returncode == 0 and p.exists():
            files.append(p.name)
        else:
            skipped["mp4"] = f"ffmpeg failed: {err[:200] or 'exit ' + str(pr.returncode)}"
            try:
                p.unlink()
            except OSError:
                pass
    files += [d.name + "/" for d in pngdirs] + [zp.name for zp, _ in zips]
    if rawf:
        import json
        note = {"width": ow, "height": oh, "fps": clip.fps, "frames": n, "bytes_per_frame": ow * oh * 3,
                "order": "serpentine rows (every other row right to left)" if lay.serpentine else "rows, top left first",
                "format": "RGB, 8 bits a channel, frames back to back", "gamma": lay.gamma, "scene": clip.scene_id}
        (out / f"{base[0]}.json").write_text(json.dumps(note, indent=1))
        files += [f"{base[0]}.rgb", f"{base[0]}.json"]
    if view:
        files.append(f"{base[0]}-view.gif")
    return {"files": files, "skipped": skipped, "seconds": round(time.time() - t0, 2), "frames": n,
            "info": clip.info()}


def _stop(pr, path: Path):
    """Kill an ffmpeg encoder, reap it and remove its partial file."""
    try:
        pr.kill()
    except OSError:
        pass
    try:
        pr.communicate(timeout=10)
    except (subprocess.TimeoutExpired, OSError, ValueError):
        pass
    try:
        path.unlink()
    except OSError:
        pass


def preview_gifs(clip: Clip, led_scale: int = 0) -> list[bytes]:
    """Small looping GIFs, one per fan (or one for a frame / matrix), for the phone's live
    preview. led_scale > 1 blows a matrix up to look like its LEDs."""
    ow, oh = getattr(clip.layout, "out_size", (clip.layout.size, clip.layout.size))
    look = (lambda t: t)
    if led_scale > 1:
        from .targets import led_view
        ow, oh = ow * led_scale, oh * led_scale
        look = (lambda t: led_view(t, led_scale, max(1, led_scale // 5)))
    bufs = [io.BytesIO() for _ in range(clip.layout.count)]
    ws = [gif.GifWriter(b, ow, oh, clip.fps) for b in bufs]
    step = max(1, clip.count // 8)
    samples = [[] for _ in bufs]
    for i in range(0, clip.count, step):
        for k, tile in enumerate(clip.frame(i)):
            samples[k].append(gif.keys(look(tile)))
    for w, s in zip(ws, samples):
        w.set_palette(gif.build_palette(s))
    for tiles in clip.frames():
        for w, tile in zip(ws, tiles):
            w.add(look(tile))
    for w in ws:
        w.close()
    return [b.getvalue() for b in bufs]


def safe_name(name: str) -> bool:
    """A file name this module could have written (no paths)."""
    return (bool(name) and len(name) < 120 and os.path.basename(name) == name and not name.startswith(".")
            and all(ch.isalnum() or ch in "-_." for ch in name))
