"""python -m goveelights hologram <scene> [options]: make hologram-fan clips.

    python -m goveelights hologram --list
    python -m goveelights hologram sleigh --layout 1x3 --size 720 --seconds 8 --out holo/
    python -m goveelights hologram fireworks --palette "Patriotic"
    python -m goveelights hologram football --team "#c8102e,#ffffff" --formats gif,mp4,png
    python -m goveelights hologram santa-landing --layout 2x3 --size 480
    python -m goveelights hologram santa-landing --frame 1920x1080          # projector / screen
    python -m goveelights hologram santa-landing --frame 16:9 --background night
    python -m goveelights hologram santa-landing --matrix 64x32 --gamma 2.2 --preview
    python -m goveelights hologram santa-landing --matrix 64x32 --live 192.168.1.60   (experimental)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import LAYOUT_NAMES, LAYOUTS, SCENES, Clip, FanLayout, export, targets
from .render import max_seconds


def user_palettes(config) -> dict:
    """The palettes made in the app (the config's "palettes"), read only; {} if none."""
    if not config:
        from ..config import DEFAULT_PATH
        config = DEFAULT_PATH
    try:
        with open(config, encoding="utf-8-sig") as f:
            pals = json.load(f).get("palettes")
    except (OSError, ValueError, AttributeError):
        return {}
    return pals if isinstance(pals, dict) else {}


def colours(v: str | None, what: str, config=None) -> list[str] | None:
    """A palette name (built-in, or made in the app) or comma-separated #hex colours."""
    if not v:
        return None
    if "#" in v or all(len(x.strip()) in (3, 6) and all(ch in "0123456789abcdefABCDEF" for ch in x.strip())
                       for x in v.split(",")):
        out = [x.strip() for x in v.split(",") if x.strip()]
        from .scenes import hex_rgb
        for c in out:
            hex_rgb(c)
        return out
    from ..palettes import all_palettes
    pals = all_palettes()
    key = next((k for k in pals if k.lower() == v.strip().lower()), None)
    if key is None:
        pals = all_palettes(user_palettes(config))
        key = next((k for k in pals if k.lower() == v.strip().lower()), None)
    if key is None:
        raise SystemExit(f"{what}: '{v}' is not a palette (built-in or yours) or a list of #hex colours")
    return list(pals[key])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m goveelights hologram",
                                 description="Make looping clips for LED hologram (POV) fans.")
    ap.add_argument("scene", nargs="?", help="scene id (see --list)")
    ap.add_argument("--list", action="store_true", help="list the scenes and layouts")
    ap.add_argument("--layout", default="1", choices=list(LAYOUTS),
                    help="1 = single fan, 1xN = a row of N, 2x2 = a square (default 1)")
    ap.add_argument("--size", type=int, default=720, help="pixels across each fan's clip (default 720)")
    ap.add_argument("--seconds", type=float, help="loop length (2-12, a story up to 40; default per scene)")
    ap.add_argument("--fps", type=float, help="frames a second (10-30, default per scene)")
    ap.add_argument("--diameter", type=float, default=42.0, help="fan diameter in cm (default 42)")
    ap.add_argument("--spacing", type=float, default=0.0, help="fan centre spacing in cm (default diameter + 10%%)")
    ap.add_argument("--palette", help="recolour: a palette name (built-in or made in the app) or #hex,#hex,...")
    ap.add_argument("--config", help="config file to read your own palettes from (default: the app's)")
    ap.add_argument("--team", help="team colours for the football scene: #hex,#hex")
    ap.add_argument("--year", type=int, help="year for the New Year scene (default: the coming one)")
    ap.add_argument("--frame", metavar="WxH|A:B",
                    help="a projector / screen picture instead of fans: 1920x1080, or an aspect like 16:9 "
                         "(its height is --size)")
    ap.add_argument("--background", default="black", choices=list(targets.BACKGROUNDS),
                    help="frame / matrix: black (= no light: projecting on the house wall) or night "
                         "(a deep-blue sky, for a TV or screen)")
    ap.add_argument("--matrix", metavar="WxH", help="an LED pixel matrix instead of fans, e.g. 64x32 (WLED, P10 panels)")
    ap.add_argument("--gamma", type=float, default=1.0, help="matrix: LED gamma (1 = none, 2.2 typical; "
                    "leave 1 if WLED's own gamma correction is on)")
    ap.add_argument("--serpentine", action="store_true", help="matrix: rows zig-zag (raw file / live order)")
    ap.add_argument("--preview", action="store_true", help="print a few frames in the terminal (24-bit colour) and stop")
    ap.add_argument("--live", metavar="IP[:PORT]", help="EXPERIMENTAL matrix: stream to WLED over DDP instead of writing files")
    ap.add_argument("--loops", type=int, default=0, help="--live: times through the loop (default: until Ctrl-C)")
    ap.add_argument("--formats", default=None,
                    help="any of gif,png,mp4 (default gif,mp4; mp4 needs ffmpeg; png = every frame as a PNG); "
                         "a matrix also takes rgb (raw frames + .json) and view (an LED-look GIF), default gif,rgb,view")
    ap.add_argument("--local-palette", action="store_true", help="GIF: a palette per frame instead of one per clip")
    ap.add_argument("--zip-png", action="store_true", help="put each fan's PNG frames in a zip")
    ap.add_argument("--sheet", action="store_true", help="also write a contact sheet PNG of 8 frames")
    ap.add_argument("--out", default="hologram_clips", help="output folder (default hologram_clips)")
    ap.add_argument("--progress-file", help=argparse.SUPPRESS)       # the app's background jobs
    ap.add_argument("--json", action="store_true", help="print the result as JSON")
    a = ap.parse_args(argv)
    if a.list or not a.scene:
        print("Scenes:")
        for sid, cls in SCENES.items():
            print(f"  {sid:<10} {cls.name}: {cls.blurb}")
        print("Layouts: " + ", ".join(f"{k} ({v})" for k, v in LAYOUT_NAMES.items()))
        print("Other displays: --frame WxH (projector / screen), --matrix WxH (LED matrix)")
        return 0 if a.list else 2
    if a.scene not in SCENES:
        raise SystemExit(f"unknown scene '{a.scene}' (one of {', '.join(SCENES)})")
    if a.frame and a.matrix:
        raise SystemExit("pick --frame or --matrix, not both")
    if a.seconds and not 2 <= a.seconds <= max_seconds(a.scene):
        raise SystemExit(f"seconds must be 2 to {max_seconds(a.scene):g} for this scene")
    try:
        if a.frame:
            w, h = targets.parse_frame(a.frame, a.size)
            lay = targets.FrameLayout(w, h, a.background)
        elif a.matrix:
            w, h = targets.parse_wh(a.matrix, "--matrix")
            lay = targets.MatrixLayout(w, h, a.gamma, a.serpentine, a.background)
        else:
            lay = FanLayout(a.layout, size=a.size, diameter_cm=a.diameter, spacing_cm=a.spacing)
        clip = Clip(a.scene, lay, a.seconds, a.fps, colours(a.palette, "--palette", a.config), colours(a.team, "--team"), a.year)
    except ValueError as e:
        raise SystemExit(str(e)) from None
    if a.preview:
        return _preview(clip)
    if a.live:
        return _live(clip, a)
    kind = getattr(lay, "kind", "fans")
    fdef = "gif,rgb,view" if kind == "matrix" else ",".join(export.DEFAULT_FORMATS)
    fmts = [f.strip().lower() for f in (a.formats or fdef).split(",") if f.strip()]
    bad = [f for f in fmts if f not in export.FORMATS]
    if bad:
        raise SystemExit(f"unknown format(s) {', '.join(bad)} (gif, png, mp4; a matrix also rgb, view)")
    if kind != "matrix" and any(f in export.MATRIX_ONLY for f in fmts):
        raise SystemExit("rgb and view are for an LED matrix (--matrix WxH)")
    prog = Path(a.progress_file) if a.progress_file else None
    last = [0.0]

    def progress(done, total):
        now = time.time()
        if prog and (now - last[0] > 0.4 or done == total):
            last[0] = now
            tmp = prog.with_suffix(".tmp")
            tmp.write_text(json.dumps({"done": done, "total": total}))
            tmp.replace(prog)
        if not a.json and sys.stdout.isatty():
            print(f"\r  frame {done}/{total}", end="", flush=True)

    res = export.export(clip, a.out, fmts, local_palette=a.local_palette, progress=progress, zip_png=a.zip_png)
    if a.sheet:
        frames = []
        for k in range(8):
            frames += clip.frame(k * clip.count // 8)
        name = f"{export.names(clip)[0].split('-fan')[0]}-sheet.png"
        (Path(a.out) / name).write_bytes(export.contact_sheet(frames, cols=4 if lay.count == 1 else lay.count))
        res["files"].append(name)
    if a.json:
        print(json.dumps(res))
    else:
        if sys.stdout.isatty():
            print()
        what = {"frame": f"a {lay.out_size[0]}x{lay.out_size[1]} frame",
                "matrix": f"a {lay.out_size[0]}x{lay.out_size[1]} LED matrix"}.get(kind, f"{lay.count} fan(s)")
        print(f"{SCENES[a.scene].name}: {clip.count} frames at {clip.fps:g} fps, {what}, "
              f"{res['seconds']} s -> {Path(a.out).resolve()}")
        for f in res["files"]:
            print("  " + f)
        for k, why in res["skipped"].items():
            print(f"  (skipped {k}: {why})")
    return 0


def _preview(clip) -> int:
    """A few key frames in the terminal: an LED matrix pixel for pixel, anything else
    averaged down to fit."""
    from .targets import ansi
    for k in range(6):
        t = (k + 0.5) / 6
        print(f"t = {t * clip.seconds:4.1f} s")
        for tile in clip.tiles_at(t, mask=False):
            print(ansi(tile, 64 if clip.layout.count > 1 else 96))
    return 0


def _live(clip, a) -> int:
    from . import live
    try:
        host, port = live.parse_host(a.live)
        print(f"Rendering {clip.count} frames...", flush=True)
        frames = live.render(clip)
    except ValueError as e:
        raise SystemExit(str(e)) from None
    print(f"EXPERIMENTAL: streaming {SCENES[clip.scene_id].name} to {host}:{port} over DDP at {clip.fps:g} fps"
          + (f", {a.loops} loop(s)" if a.loops > 0 else " (Ctrl-C stops)"), flush=True)
    n = live.stream(frames, host, port, clip.fps, a.loops)
    print(f"sent {n} frames")
    return 0
