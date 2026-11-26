"""Render an effect/preset on the house layout to an image, to check how it looks.

    python3 tools/preview.py <preset-id | preset.json | effect-name> [--set key=value ...]
        [--seconds 6] [--frames 8] [--fps 20] [--out <temp dir>/preview] [--layer N]

Renders the scene at the real frame rate from t=0 (so effects with memory evolve as
they would live) and saves --frames snapshots spread over --seconds as:
  <out>.html  house view per snapshot + a filmstrip (one row per frame, every bulb)
  <out>.png   screenshot of that page (needs node + playwright; skipped if missing)
Uses layouts/house.json (the real house: Eaves, Peak, Accent, zones).
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
from goveelights.layout import Layout, Run  # noqa: E402


def house_layout() -> Layout:
    data = json.loads((ROOT / "layouts" / "house.json").read_text(encoding="utf-8"))
    devs = data["devices"]
    runs = [Run(mac=m, name=d["name"], count=d["pixel_count"], reverse=d.get("reversed", False),
                hidden=set(d.get("hidden", [])), geometry=d.get("geometry", []))
            for m, d in devs.items() if d.get("pixel_count")]
    runs.sort(key=lambda r: devs[r.mac].get("order", 0))
    return Layout(runs, data.get("zones"))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("what")
    ap.add_argument("--set", action="append", default=[])
    ap.add_argument("--seconds", type=float, default=6.0)
    ap.add_argument("--frames", type=int, default=8)
    ap.add_argument("--fps", type=float, default=20.0)
    ap.add_argument("--out", default=os.path.join(tempfile.gettempdir(), "preview"))
    a = ap.parse_args(argv)
    os.chdir(ROOT)
    L = house_layout()
    scene = load_scene(a.what, a.set)
    total = max(1, int(a.seconds * a.fps))
    keep = {int(round(k * (total - 1) / max(1, a.frames - 1))) for k in range(a.frames)}
    snaps, strip, ms = [], [], []
    import time
    for f in range(total):
        t0 = time.perf_counter()
        cols = scene.render(f / a.fps, L)
        ms.append((time.perf_counter() - t0) * 1000)
        if f % max(1, total // 60) == 0:
            strip.append(cols)
        if f in keep:
            snaps.append((f / a.fps, cols))
    W, H, pad = 980, 200, 12
    sx = (W - 2 * pad) / max(1e-6, L.maxx - L.minx)
    yex = 1.6
    H = int((L.maxy - L.miny) * sx * yex + 2 * pad + 10)

    def rgb(c):
        return f"rgb({int(min(1, c[0]) * 255)},{int(min(1, c[1]) * 255)},{int(min(1, c[2]) * 255)})"

    def house(cols):
        dots = "".join(f'<circle cx="{pad + (p.x - L.minx) * sx:.1f}" cy="{H - pad - (p.y - L.miny) * sx * yex:.1f}" '
                       f'r="3.6" fill="{rgb(c) if max(c) > 0.01 else "#1a1f27"}"/>' for p, c in zip(L.pixels, cols))
        return f'<svg width="{W}" height="{H}" style="background:#05070a;display:block">{dots}</svg>'

    rows = "".join(f'<div style="display:flex;height:4px">' + "".join(
        f'<span style="flex:1;background:{rgb(c)}"></span>' for c in cols) + "</div>" for cols in strip)
    perf = f"render: avg {sum(ms) / len(ms):.1f} ms, worst {max(ms):.1f} ms per frame ({L.n} bulbs)"
    html = (f'<meta charset="utf-8"><body style="background:#000;color:#bbb;font:12px sans-serif;margin:8px">'
            f'<div>{a.what} {" ".join(a.set)} · {perf}</div>'
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
