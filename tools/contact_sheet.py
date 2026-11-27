"""Contact sheet: key frames of several presets (or a show's cues) on the house, one PNG.

    python3 tools/contact_sheet.py turkey-fan harvest-sunset --times 1,4,10,30 --out /tmp/sheet
    python3 tools/contact_sheet.py --show "Thanksgiving tonight" --out /tmp/thanksgiving
        [--layout layouts/examples/gable-house.json] [--extra-run 100] [--fps 20] [--cols 2]
        [--tip 12] [--tip-flip] [--tip-rows 7,4,1]
        [--focus Peak --zoom-only --honeycomb --street-aspect] [--labels "a,b,c"]

Each preset is rendered at the real frame rate from t = 0 (effects with memory evolve as
they would live) and snapshotted at --times seconds. --show renders every cue's preset at
a few moments of its hold. --extra-run N adds a run of N bulbs with no mapped shape (as a
newly added box shows up before camera mapping) to check a fourth run is used.
--tip N adds a Tip on top of the layout's zigzag with N visible bulbs, placed exactly as
"Add it to the show" -> "Top of the Peak" places it (--tip-flip: bulb 0 at the other end,
--tip-rows: bulbs per row from bulb 0). A layout file whose Tip entry has "extends" works
with --layout too.
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
                geometry=r.geometry, role=r.role, extends=r.extends) for r in L.runs]
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


def honeycomb_positions(L: Layout) -> dict:
    """Where the Peak's (and the Tip's) bulbs really sit if the rows are offset half a bulb
    each (the honeycomb, see goveelights/hexfont.py), as {bulb: (x, y)} in house units."""
    from goveelights import hexfont
    try:
        idx = L.indices("Peak")
    except Exception:  # noqa: BLE001
        return {}
    lat = hexfont.lattice(L, idx)
    if not lat.rows:
        return {}
    px = L.pixels
    base = [i for i, _ in lat.rows[0]]
    dx = sorted(px[b].x - px[a].x for r in lat.rows for (a, _), (b, _) in zip(r, r[1:]) if px[b].x > px[a].x)
    pitch = dx[len(dx) // 2] if dx else 1.0
    c0 = (px[base[0]].x + px[base[-1]].x) / 2
    out = {}
    for row in lat.rows:
        y = sum(px[i].y for i, _ in row) / len(row)
        for i, X in row:
            out[i] = (c0 + X * pitch, y)
    return out


def svg(L: Layout, cols, W=470, box=None, r=2.6, pos=None, yex=1.6):
    """The house (or the part of it inside box = (x0, x1, y0, y1)) as dots. yex stretches
    heights (1.6 reads well for the whole house; the close-up can use --street-aspect)."""
    pad = 8
    x0, x1, y0, y1 = box or (L.minx, L.maxx, L.miny, L.maxy)
    sx = (W - 2 * pad) / max(1e-6, x1 - x0)
    H = int((y1 - y0) * sx * yex + 2 * pad)

    def rgb(c):
        return f"rgb({int(min(1, c[0]) * 255)},{int(min(1, c[1]) * 255)},{int(min(1, c[2]) * 255)})"
    dots = []
    pos = pos or {}
    for k, (p, c) in enumerate(zip(L.pixels, cols)):
        bx, by = pos.get(k, (p.x, p.y))
        if not (x0 - 0.01 <= bx <= x1 + 0.01 and y0 - 0.01 <= by <= y1 + 0.01):
            continue
        x, y = pad + (bx - x0) * sx, H - pad - (by - y0) * sx * yex
        if max(c) > 0.01:      # a soft halo, roughly how a bulb blooms in a photo from the street
            dots.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r * 2.1:.1f}" fill="{rgb(c)}" opacity="0.28"/>')
        dots.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="{rgb(c) if max(c) > 0.01 else "#1a1f27"}"/>')
    return f'<svg width="{W}" height="{H}" style="background:#04060a;display:block">{"".join(dots)}</svg>'


STREET_ROW = 0.69          # row spacing / bulb spacing on the Peak in the owner's night photos


def street_yex(L: Layout, ii) -> float:
    """The height stretch that draws the zigzag's rows STREET_ROW bulb spacings apart."""
    from goveelights import geometry as geo
    px = L.pixels
    rows = geo._rows_by_reversal(px, [i for i in ii if not getattr(px[i], "ext", False)]) or []
    rows = [sorted(r, key=lambda i: px[i].x) for r in rows if len(r) > 1]
    if len(rows) < 2:
        return 1.6
    ys = sorted(sum(px[i].y for i in r) / len(r) for r in rows)
    dy = (ys[-1] - ys[0]) / (len(ys) - 1)
    dx = sorted(abs(px[b].x - px[a].x) for r in rows for a, b in zip(r, r[1:]))
    dx = dx[len(dx) // 2]
    return max(0.2, STREET_ROW * dx / max(1e-6, dy))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("presets", nargs="*")
    ap.add_argument("--show")
    ap.add_argument("--times", default="1,4,10,25")
    ap.add_argument("--fps", type=float, default=20.0)
    ap.add_argument("--cols", type=int, default=2)
    ap.add_argument("--extra-run", type=int, default=0)
    ap.add_argument("--tip", type=int, default=0, help="add a Tip with this many visible bulbs (0 = none)")
    ap.add_argument("--tip-flip", action="store_true", help="the Tip's bulb 0 is at the other end")
    ap.add_argument("--tip-rows", default="", help="the Tip's bulbs per row from bulb 0 (default 7,4,1)")
    ap.add_argument("--layout", default=str(default_layout_file().relative_to(ROOT)))
    ap.add_argument("--focus", help="also draw this target (e.g. 'Whole front gable') close up")
    ap.add_argument("--honeycomb", action="store_true",
                    help="draw the Peak's rows offset half a bulb each, as they are on the house")
    ap.add_argument("--zoom-only", action="store_true", help="with --focus: only the close-up")
    ap.add_argument("--street-aspect", action="store_true",
                    help="draw the close-up with the Peak's proportions as seen from the street (the "
                         "owner's photos: rows about 0.7 of a bulb spacing apart), not stretched tall")
    ap.add_argument("--labels", default="", help="comma-separated captions, one per frame, instead of 'preset · t'")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    lay = Path(a.layout)
    if not lay.is_absolute():
        lay = lay.resolve() if lay.exists() else ROOT / lay
    os.chdir(ROOT)
    L = house_layout(lay)
    if a.tip:
        from goveelights import addrun
        L = addrun.layout_with_tip(L, visible=a.tip, flip=a.tip_flip, rows=addrun.parse_rows(a.tip_rows))
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
    pos = honeycomb_positions(L) if a.honeycomb else None
    yz = street_yex(L, L.indices(a.focus)) if box and a.street_aspect else 1.6
    caps = [c for c in a.labels.split(",")] if a.labels else []
    cells = []
    for label, pid, ts in jobs:
        for t, cols in frames(pid, L, ts, a.fps):
            zoom = svg(L, cols, box=box, r=4 if a.street_aspect else 7, pos=pos, yex=yz) if box else ""
            whole = "" if (a.zoom_only and box) else svg(L, cols, pos=pos)
            cap = caps[len(cells)] if len(cells) < len(caps) else f"{label} · t={t:g}s"
            cells.append(f'<div><div class="l">{cap}</div>{whole}{zoom}</div>')
    title = a.show or ", ".join(a.presets)
    html = (f'<meta charset="utf-8"><style>body{{background:#000;color:#bbb;font:12px sans-serif;margin:8px}}'
            f'.g{{display:grid;grid-template-columns:repeat({a.cols},480px);gap:6px}}.l{{margin:3px 0}}</style>'
            f'<div style="margin-bottom:6px">{title}{" · + run of " + str(a.extra_run) if a.extra_run else ""}'
            f'{" · with the Tip (" + str(a.tip) + " bulbs)" if a.tip else ""}</div>'
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
