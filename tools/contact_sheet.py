"""Contact sheet: key frames of several presets (or a show's cues) on the house, one PNG.

    python3 tools/contact_sheet.py turkey-fan harvest-sunset --times 1,4,10,30 --out /tmp/sheet
    python3 tools/contact_sheet.py --show "Thanksgiving tonight" --out /tmp/thanksgiving
        [--layout layouts/examples/gable-house.json] [--extra-run 100] [--fps 20] [--cols 2]

Each preset is rendered at the real frame rate from t = 0 (effects with memory evolve as
they would live) and snapshotted at --times seconds. --show renders every cue's preset at
a few moments of its hold. --extra-run N adds a run of N bulbs with no mapped shape (as a
newly added box shows up before camera mapping) to check a fourth run is used.
Writes <out>.html and, with node + playwright, <out>.png.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from preview import default_layout_file, house_layout  # noqa: E402
from goveelights.cli import load_scene  # noqa: E402
from goveelights.layout import Layout, Run  # noqa: E402


def with_extra_run(L: Layout, count: int) -> Layout:
    runs = [Run(mac=r.mac, name=r.name, count=r.count, reverse=r.reverse, hidden=set(r.hidden),
                geometry=r.geometry) for r in L.runs]
    runs.append(Run(mac="EXTRA", name="Run 4", count=count))
    return Layout(runs, L.zone_specs, L.aliases)


def frames(what: str, L: Layout, times: list[float], fps: float):
    scene = load_scene(what, [])
    want = sorted(times)
    out, k = {}, 0
    total = int(max(want) * fps) + 1
    for f in range(total + 1):
        t = f / fps
        cols = scene.render(t, L)
        while k < len(want) and t >= want[k] - 1e-9:
            out[want[k]] = cols
            k += 1
        if k >= len(want):
            break
    return [(t, out[t]) for t in times if t in out]


def svg(L: Layout, cols, W=470, box=None, r=2.6):
    """The house (or the part of it inside box = (x0, x1, y0, y1)) as dots."""
    pad = 8
    x0, x1, y0, y1 = box or (L.minx, L.maxx, L.miny, L.maxy)
    sx = (W - 2 * pad) / max(1e-6, x1 - x0)
    yex = 1.6
    H = int((y1 - y0) * sx * yex + 2 * pad)

    def rgb(c):
        return f"rgb({int(min(1, c[0]) * 255)},{int(min(1, c[1]) * 255)},{int(min(1, c[2]) * 255)})"
    dots = []
    for p, c in zip(L.pixels, cols):
        if not (x0 - 0.01 <= p.x <= x1 + 0.01 and y0 - 0.01 <= p.y <= y1 + 0.01):
            continue
        x, y = pad + (p.x - x0) * sx, H - pad - (p.y - y0) * sx * yex
        if max(c) > 0.01:      # a soft halo, roughly how a bulb blooms in a photo from the street
            dots.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r * 2.1:.1f}" fill="{rgb(c)}" opacity="0.28"/>')
        dots.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="{rgb(c) if max(c) > 0.01 else "#1a1f27"}"/>')
    return f'<svg width="{W}" height="{H}" style="background:#04060a;display:block">{"".join(dots)}</svg>'


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("presets", nargs="*")
    ap.add_argument("--show")
    ap.add_argument("--times", default="1,4,10,25")
    ap.add_argument("--fps", type=float, default=20.0)
    ap.add_argument("--cols", type=int, default=2)
    ap.add_argument("--extra-run", type=int, default=0)
    ap.add_argument("--layout", default=str(default_layout_file().relative_to(ROOT)))
    ap.add_argument("--focus", help="also draw this target (e.g. 'Whole front gable') close up")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    lay = Path(a.layout)
    if not lay.is_absolute():
        lay = lay.resolve() if lay.exists() else ROOT / lay
    os.chdir(ROOT)
    L = house_layout(lay)
    if a.extra_run:
        L = with_extra_run(L, a.extra_run)
    jobs = []
    if a.show:
        from goveelights.shows_builtin import BUILTIN_SHOWS
        sh = BUILTIN_SHOWS[a.show]
        for n, cue in enumerate(sh["cues"], 1):
            if cue.get("preset"):
                s = float(cue.get("seconds", 30))
                ts = [round(x, 2) for x in (1.0, s * 0.3, s * 0.65, s - 1)]
                jobs.append((f'{n}. {cue["preset"]} ({s:g} s)', cue["preset"], ts))
    times = [float(x) for x in a.times.split(",")]
    jobs += [(p, p, times) for p in a.presets]
    box = None
    if a.focus:
        ii = L.indices(a.focus)
        if ii:
            px = L.pixels
            box = (min(px[i].x for i in ii) - 1, max(px[i].x for i in ii) + 1,
                   min(px[i].y for i in ii) - 0.5, max(px[i].y for i in ii) + 0.5)
    cells = []
    for label, pid, ts in jobs:
        for t, cols in frames(pid, L, ts, a.fps):
            zoom = svg(L, cols, box=box, r=7) if box else ""
            cells.append(f'<div><div class="l">{label} · t={t:g}s</div>{svg(L, cols)}{zoom}</div>')
    title = a.show or ", ".join(a.presets)
    html = (f'<meta charset="utf-8"><style>body{{background:#000;color:#bbb;font:12px sans-serif;margin:8px}}'
            f'.g{{display:grid;grid-template-columns:repeat({a.cols},480px);gap:6px}}.l{{margin:3px 0}}</style>'
            f'<div style="margin-bottom:6px">{title}{" · + run of " + str(a.extra_run) if a.extra_run else ""}</div>'
            f'<div class="g">{"".join(cells)}</div>')
    page = Path(a.out + ".html").resolve()
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(html, encoding="utf-8")
    js = (f"const {{chromium}}=require('playwright');(async()=>{{const b=await chromium.launch();"
          f"const p=await b.newPage({{viewport:{{width:{a.cols * 486 + 16},height:600}}}});"
          f"await p.goto({json.dumps(page.as_uri())});"
          f"await p.screenshot({{path:{json.dumps(str(page.with_suffix('.png')))},fullPage:true}});await b.close()}})()")
    try:
        subprocess.run(["node", "-e", js], check=True, timeout=120, capture_output=True)
        print(f"wrote {a.out}.html and {a.out}.png")
    except (OSError, subprocess.SubprocessError) as e:
        print(f"wrote {a.out}.html (no screenshot: {e})")


if __name__ == "__main__":
    main()
