"""python -m goveelights hologram <scene> [options]: make hologram-fan clips.

    python -m goveelights hologram --list
    python -m goveelights hologram sleigh --layout 1x3 --size 720 --seconds 8 --out holo/
    python -m goveelights hologram fireworks --palette "Patriotic"
    python -m goveelights hologram football --team "#fc4c02,#0a2343" --formats gif,mp4
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import LAYOUT_NAMES, LAYOUTS, SCENES, Clip, FanLayout, export


def colours(v: str | None, what: str) -> list[str] | None:
    """A palette name (built-in) or comma-separated #hex colours."""
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
        raise SystemExit(f"{what}: '{v}' is not a built-in palette or a list of #hex colours")
    return list(pals[key])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m goveelights hologram",
                                 description="Make looping clips for LED hologram (POV) fans.")
    ap.add_argument("scene", nargs="?", help="scene id (see --list)")
    ap.add_argument("--list", action="store_true", help="list the scenes and layouts")
    ap.add_argument("--layout", default="1", choices=list(LAYOUTS),
                    help="1 = single fan, 1xN = a row of N, 2x2 = a square (default 1)")
    ap.add_argument("--size", type=int, default=720, help="pixels across each fan's clip (default 720)")
    ap.add_argument("--seconds", type=float, help="loop length (2-12, default per scene)")
    ap.add_argument("--fps", type=float, help="frames a second (10-30, default per scene)")
    ap.add_argument("--diameter", type=float, default=42.0, help="fan diameter in cm (default 42)")
    ap.add_argument("--spacing", type=float, default=0.0, help="fan centre spacing in cm (default diameter + 10%%)")
    ap.add_argument("--palette", help="recolour: a built-in palette name or #hex,#hex,...")
    ap.add_argument("--team", help="team colours for the football scene: #hex,#hex")
    ap.add_argument("--year", type=int, help="year for the New Year scene (default: the coming one)")
    ap.add_argument("--formats", default="gif,png,mp4", help="any of gif,png,mp4 (default all; mp4 needs ffmpeg)")
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
        return 0 if a.list else 2
    if a.scene not in SCENES:
        raise SystemExit(f"unknown scene '{a.scene}' (one of {', '.join(SCENES)})")
    try:
        lay = FanLayout(a.layout, size=a.size, diameter_cm=a.diameter, spacing_cm=a.spacing)
        clip = Clip(a.scene, lay, a.seconds, a.fps, colours(a.palette, "--palette"), colours(a.team, "--team"), a.year)
    except ValueError as e:
        raise SystemExit(str(e)) from None
    fmts = [f.strip().lower() for f in a.formats.split(",") if f.strip()]
    bad = [f for f in fmts if f not in export.FORMATS]
    if bad:
        raise SystemExit(f"unknown format(s) {', '.join(bad)} (gif, png, mp4)")
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
        print(f"{SCENES[a.scene].name}: {clip.count} frames at {clip.fps:g} fps, {lay.count} fan(s), "
              f"{res['seconds']} s -> {Path(a.out).resolve()}")
        for f in res["files"]:
            print("  " + f)
        for k, why in res["skipped"].items():
            print(f"  (skipped {k}: {why})")
    return 0
