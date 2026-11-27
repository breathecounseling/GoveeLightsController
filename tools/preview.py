"""Render an effect/preset on the house layout to an image, to check how it looks.

    python3 tools/preview.py <preset-id | preset.json | effect-name> [--set key=value ...]
        [--seconds 6] [--frames 8] [--fps 20] [--out <temp dir>/preview] [--layer N]
        [--audio BPM] [--layout layouts/examples/gable-house.json]

--audio BPM feeds the scene a made-up song at that tempo (a kick on every beat, see
goveelights.audio.synthetic), as if a phone were listening (Play -> Listen).

Renders the scene at the real frame rate from t=0 (so effects with memory evolve as
they would live) and saves --frames snapshots spread over --seconds as:
  <out>.html  house view per snapshot + a filmstrip (one row per frame, every bulb)
  <out>.png   screenshot of that page (needs node + playwright; skipped if missing)
--layout picks the layout file (default: layouts/house.json if present, else the example
layouts/examples/gable-house.json). A govee_config.json works too (its devices and zones).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from goveelights.cli import load_scene  # noqa: E402
from goveelights.layout import ROLES, Layout, Run  # noqa: E402


HOUSE = ROOT / "layouts" / "house.json"
EXAMPLE = ROOT / "layouts" / "examples" / "gable-house.json"


def default_layout_file() -> Path:
    return HOUSE if HOUSE.exists() else EXAMPLE


def house_layout(path=None) -> Layout:
    """The layout in a layout file (or a govee_config.json)."""
    data = json.loads(Path(path or default_layout_file()).read_text(encoding="utf-8-sig"))
    devs = data["devices"]
    runs = [Run(mac=m, name=d.get("name") or m, count=d["pixel_count"], reverse=d.get("reversed", False),
                hidden=set(d.get("hidden", [])), geometry=d.get("geometry", []),
                role=d.get("role") if d.get("role") in ROLES else None, follows=d.get("follows") or None,
                extends=d.get("extends") or None)
            for m, d in devs.items() if d.get("pixel_count") and d.get("enabled", True) is not False]
    runs.sort(key=lambda r: devs[r.mac].get("order", 0))
    return Layout(runs, data.get("zones"), data.get("target_aliases"))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("what")
    ap.add_argument("--set", action="append", default=[])
    ap.add_argument("--seconds", type=float, default=6.0)
    ap.add_argument("--frames", type=int, default=8)
    ap.add_argument("--fps", type=float, default=20.0)
    ap.add_argument("--out", default=os.path.join(tempfile.gettempdir(), "preview"))
    ap.add_argument("--audio", type=float, default=0.0, metavar="BPM", help="simulate music at this tempo")
    ap.add_argument("--layout", help="layout file or govee_config.json (default: %(default)s)",
                    default=str(default_layout_file().relative_to(ROOT)))
    a = ap.parse_args(argv)
    from goveelights.audio import synthetic
    lay = Path(a.layout).expanduser()
    if not lay.is_absolute():                      # relative to where you are, else to the project
        lay = lay.resolve() if lay.exists() else ROOT / lay
    os.chdir(ROOT)
    L = house_layout(lay)
    scene = load_scene(a.what, a.set)
    total = max(1, int(a.seconds * a.fps))
    keep = {int(round(k * (total - 1) / max(1, a.frames - 1))) for k in range(a.frames)}
    snaps, strip, ms = [], [], []
    import time
    for f in range(total):
        t0 = time.perf_counter()
        t = f / a.fps
        if a.audio:
            cols = scene.render(t, L, {"bpm": a.audio}, synthetic(t, a.audio))
        else:
            cols = scene.render(t, L)
        ms.append((time.perf_counter() - t0) * 1000)
        if f % max(1, total // 60) == 0:
            strip.append(cols)
        if f in keep:
            snaps.append((f / a.fps, cols))
    W, H, pad = 980, 200, 12
    # every bulb, an unmapped run's line below the house too (the layout's own bounds leave it out)
    xs, ys = [p.x for p in L.pixels], [p.y for p in L.pixels]
    minx, maxx = min(xs or [L.minx]), max(xs or [L.maxx])
    miny, maxy = min(ys or [L.miny]), max(ys or [L.maxy])
    sx = (W - 2 * pad) / max(1e-6, maxx - minx)
    yex = 1.6
    H = int((maxy - miny) * sx * yex + 2 * pad + 10)

    def rgb(c):
        return f"rgb({int(min(1, c[0]) * 255)},{int(min(1, c[1]) * 255)},{int(min(1, c[2]) * 255)})"

    def house(cols):
        dots = "".join(f'<circle cx="{pad + (p.x - minx) * sx:.1f}" cy="{H - pad - (p.y - miny) * sx * yex:.1f}" '
                       f'r="3.6" fill="{rgb(c) if max(c) > 0.01 else "#1a1f27"}"/>' for p, c in zip(L.pixels, cols))
        return f'<svg width="{W}" height="{H}" style="background:#05070a;display:block">{dots}</svg>'

    rows = "".join(f'<div style="display:flex;height:4px">' + "".join(
        f'<span style="flex:1;background:{rgb(c)}"></span>' for c in cols) + "</div>" for cols in strip)
    perf = f"render: avg {sum(ms) / len(ms):.1f} ms, worst {max(ms):.1f} ms per frame ({L.n} bulbs)"
    html = (f'<meta charset="utf-8"><body style="background:#000;color:#bbb;font:12px sans-serif;margin:8px">'
            f'<div>{a.what} {" ".join(a.set)}{f" · music at {a.audio:g} BPM" if a.audio else ""} · {perf}</div>'
            + "".join(f'<div style="margin-top:6px">t = {t:.2f} s</div>{house(c)}' for t, c in snaps)
            + f'<div style="margin-top:8px">filmstrip (time runs down, every bulb in strip order)</div>{rows}</body>')
    page = Path(a.out + ".html").resolve()
    page.write_text(html, encoding="utf-8")
    png = json.dumps(str(page.with_suffix(".png")))
    print(perf)
    js = (f"const {{chromium}}=require('playwright');(async()=>{{const b=await chromium.launch();"
          f"const p=await b.newPage({{viewport:{{width:{W + 20},height:800}}}});await p.goto({json.dumps(page.as_uri())});"
          f"await p.screenshot({{path:{png},fullPage:true}});await b.close()}})()")
    try:
        subprocess.run(["node", "-e", js], check=True, timeout=60, capture_output=True)
        print(f"wrote {a.out}.html and {a.out}.png")
    except (OSError, subprocess.SubprocessError) as e:
        print(f"wrote {a.out}.html (no screenshot: {e})")


if __name__ == "__main__":
    main()
