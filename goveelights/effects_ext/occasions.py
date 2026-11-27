"""Year-round occasion effects that use the front gable's zigzag as a tiny 6-row display.

    floaters  little pixel-art sprites on the zigzag: hearts that float up the gable and
              POP at the apex in a ring of sparks (Valentine's), ghosts that drift
              sideways across it with dark eyes, bobbing and flickering (Halloween),
              striped eggs rising like balloons (Easter).
    flag      a waving flag on the zigzag: stripes on the rows, a blue field of
              twinkling stars in the top-left of the triangle, ripples rolling across
              it like cloth in a breeze, unfurling from the left when it starts.

Both are @frame_effect generators. They find the zigzag in their target (rows that
reverse direction, as the tree does); anything else in the target (e.g. the gable
outline) is "the rest": floaters give it a soft glow and the pop sparks, the apex
bulbs above the zigzag flash when a heart pops there. Sprites are sampled from small
bitmaps with smoothed bilinear filtering in (bulb, row) space, so they glide between
bulbs and rows instead of hopping. State is bounded (at most 6 sprites, 8 bursts) and
randomness comes from a random.Random kept in the layer state (deterministic for a
given seed and frame sequence).
"""
from __future__ import annotations

import math
import random

from .. import geometry
from ..color import BLACK, hash01, noise2
from ..effects import Param, frame_effect, travel
from ..sparkle import glint
from . import hexcanvas as hx

MAX_DT = 0.6           # only a real stall is capped (Scene caps raw dt at 0.5; master speed scales it)
MAX_SPRITES = 6
MAX_BURSTS = 8
POP_S = 0.3            # how long a popping sprite takes to flash and vanish
BURST_S = 1.3          # life of a pop's spark ring

# '1' = the sprite's colour, '2' = its detail (ghost eyes: the detail colour; egg band:
# the next palette colour). Top row first.
SPRITES: dict[str, list[str]] = {
    "heart": [
        "11.11",
        "11111",
        ".111.",
        "..1..",
    ],
    "ghost": [
        ".111.",
        "12121",
        "11111",
        "1.1.1",
    ],
    "egg": [
        ".11.",
        "2222",
        "1111",
        ".11.",
    ],
    "big egg": [
        ".111.",
        "11111",
        "22222",
        "11111",
        "22222",
        ".111.",
    ],
}
SHAPES = list(SPRITES)

# The same sprites drawn for the Peak as it hangs: a honeycomb, each row offset by half a
# bulb (hexcanvas.Sprite format: one line per row, one character per HALF bulb). Bold and
# solid so they read from the street: hearts with two lobes, a notch and a one-bulb point,
# solid ghosts with two dark eye holes and a flared, ragged hem, striped eggs.
# Drawn in TRUE proportions: on the house a row is only hexcanvas.aspect (about 0.66) of a
# bulb spacing tall, so a shape n rows tall is n * 0.66 spacings tall and its width in
# bulbs must match that (the earlier sprites, 6-7 bulbs wide and 4-5 rows tall, came out
# about twice as wide as tall: flat hearts, squat ghosts, eggs lying on their side).
#   heart  4 bulbs x 5 rows (3.3 spacings): a little wider than tall, as a heart is
#   ghost  5 bulbs x 6 rows (4.0): a domed head, the hem flaring to points
#   egg    3 bulbs x 5 rows (3.3), big egg 3 bulbs x 6 rows (4.0): taller than wide, upright
# The bitmaps above stay for targets without a zigzag.
HEX_SPRITES: dict[str, str] = {
    "heart": """
 1 . 1
1 1 1 1
 1 1 1
  1 1
   1
""",
    "ghost": """
   1 1
  1 1 1
 1 1 1 1
1 2 1 2 1
 1 1 1 1
1 . 1 . 1
""",
    "egg": """
 1 1
2 2 2
 1 1
1 1 1
 1 1
""",
    "big egg": """
  1
 1 1
2 2 2
 1 1
2 2 2
 1 1
""",
}
_HEX_CACHE: dict = {}


def _hex_sprite(shape: str):
    sp = _HEX_CACHE.get(shape)
    if sp is None:
        sp = _HEX_CACHE[shape] = hx.Sprite(HEX_SPRITES[shape], {"1": "1", "2": "2"})
    return sp


def _glide(f: float) -> float:
    """Rest on whole bulbs and glide quickly between them (so 1-bulb details stay crisp)."""
    return _smooth((f - 0.375) / 0.25)


def _hex_stamp(hc, sp, bottom: float, x: float, acc: dict, k: float = 1.0):
    """Add a sprite's coverage {bulb: [cover of '1', cover of '2']} with its bottom line at
    the (fractional) row `bottom` and its middle at column x, cross-fading between the
    lattice places either side so it moves smoothly."""
    r0 = math.floor(bottom)
    fr = _glide(bottom - r0)
    for dr, wr in ((0, 1 - fr), (1, fr)):
        if wr <= 0.001:
            continue
        row = r0 + dr
        # doubled-column offset of the sprite's left edge; valid ones share a parity
        tgt = 2 * x - (sp.width - 1) / 2
        par = (row + hc.p0 + sp.parity) % 2
        lo = math.floor(tgt)
        if (lo + par) % 2:
            lo -= 1
        fc = _glide((tgt - lo) / 2 + 0.13)        # half way (a row where it cannot centre): snap right
        for off, wc in ((lo, 1 - fc), (lo + 2, fc)):
            w = wr * wc * k
            if w <= 0.001:
                continue
            for kk, j, ch in sp.cells:
                if ch == ".":
                    continue
                c = hc.cell(row + j, (kk + off) / 2)
                if c is None or c.i is None or c.i not in hc.allowed:
                    continue
                a = acc.setdefault(c.i, [0.0, 0.0])
                a[0 if ch == "1" else 1] += w


def _smooth(x: float) -> float:
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    return x * x * (3 - 2 * x)


def _clamp(c):
    r, g, b = c
    return (0.0 if r < 0 else 1.0 if r > 1 else r,
            0.0 if g < 0 else 1.0 if g > 1 else g,
            0.0 if b < 0 else 1.0 if b > 1 else b)


def _dt(ctx) -> float:
    d = ctx.dt or 0.0
    if not (d > 0) or d != d:
        return 0.0
    return min(MAX_DT, d)


# ------------------------------------------------------------------ geometry
def field(layout, idx: list[int]) -> dict:
    """The zigzag inside a target as a little display: rows (bottom first, each left to
    right), each bulb's row number, bulb and row pitch, the apex bulbs crowning it and
    the rest of the target. Cached per layout object and target."""
    key = ("occasions-field", tuple(idx))
    c = geometry._cache(layout)
    if key in c:
        return c[key]
    px = layout.pixels
    groups: dict[str, list[int]] = {}
    for i in idx:
        groups.setdefault(px[i].run, []).append(i)
    rows = None
    for g in sorted(groups.values(), key=len, reverse=True):
        r = geometry._rows_by_reversal(px, g)
        if r:
            rows = r
            break
    if rows is None:                                 # no zigzag: rows by height
        rows = []
        for i in sorted(idx, key=lambda i: px[i].y):
            if rows and abs(px[i].y - px[rows[-1][0]].y) <= 0.45:
                rows[-1].append(i)
            else:
                rows.append([i])
    rows = [sorted(r, key=lambda i: px[i].x) for r in rows if r]
    rows.sort(key=lambda r: sum(px[i].y for i in r) / len(r))
    row_of = {i: k for k, r in enumerate(rows) for i in r}
    gaps = sorted(px[b].x - px[a].x for r in rows for a, b in zip(r, r[1:]) if px[b].x - px[a].x > 0.05)
    cp = gaps[len(gaps) // 2] if gaps else 1.0
    spans = [(px[r[0]].x, px[r[-1]].x) for r in rows]
    hc = hx.get(layout, idx)
    if hc is not None and hc.peak_rows == len(rows):
        # the rows' ends from the lattice, which keeps a dead bulb's place: nothing shifts
        spans = [(rw.cells[0].pos[0], rw.cells[-1].pos[0]) for rw in hc.rows[:len(rows)]]
    row_y = [sum(px[i].y for i in r) / len(r) for r in rows]
    cx = (spans[0][0] + spans[0][1]) / 2 if spans else 0.0
    left = min((s[0] for s in spans), default=0.0)
    right = max((s[1] for s in spans), default=0.0)
    fset = set(row_of)
    rest = [i for i in idx if i not in fset]
    # apex: the bulbs crowning the zigzag (on the house the gable apex, Eaves 42/43)
    apex = []
    if rows:
        top = max(px[i].y for i in fset)
        above = sorted((i for i in rest if px[i].y > top + 0.3 and left - 1 <= px[i].x <= right + 1),
                       key=lambda i: (-px[i].y, abs(px[i].x - cx)))
        apex = above[:2]
    out = {"rows": rows, "row_of": row_of, "cp": cp, "spans": spans, "row_y": row_y, "cx": cx,
           "left": left, "right": right, "rest": rest, "apex": apex, "n": len(rows),
           "col_in_row": {i: k for r in rows for k, i in enumerate(r)}}
    c[key] = out
    return out


def _halfspan(F: dict, row: float) -> float:
    """Half the zigzag's width at a (fractional) row number."""
    spans = F["spans"]
    if not spans:
        return 0.0
    k = min(len(spans) - 1, max(0, int(math.floor(row))))
    k2 = min(len(spans) - 1, k + 1)
    f = min(1.0, max(0.0, row - k))
    a = (spans[k][1] - spans[k][0]) / 2
    b = (spans[k2][1] - spans[k2][0]) / 2
    return a + (b - a) * f


def _row_y(F: dict, row: float) -> float:
    ys = F["row_y"]
    if not ys:
        return 0.0
    if row <= 0:
        return ys[0] + row * (ys[1] - ys[0] if len(ys) > 1 else 0.8)
    if row >= len(ys) - 1:
        return ys[-1] + (row - len(ys) + 1) * (ys[-1] - ys[-2] if len(ys) > 1 else 0.8)
    k = int(row)
    return ys[k] + (ys[k + 1] - ys[k]) * (row - k)


def sample(bitmap: list[str], u: float, v: float) -> tuple[float, float]:
    """Smoothed bilinear sample of a sprite at column u, row v (cell centres on whole
    numbers, row 0 = top) -> (coverage of '1', coverage of '2')."""
    h, w = len(bitmap), len(bitmap[0])
    c0, r0 = math.floor(u), math.floor(v)
    fu, fv = _smooth(u - c0), _smooth(v - r0)
    a1 = a2 = 0.0
    for dr, wr in ((0, 1 - fv), (1, fv)):
        r = r0 + dr
        if wr <= 0 or r < 0 or r >= h:
            continue
        line = bitmap[r]
        for dc, wc in ((0, 1 - fu), (1, fu)):
            c = c0 + dc
            if wc <= 0 or c < 0 or c >= w:
                continue
            ch = line[c]
            if ch == "1":
                a1 += wr * wc
            elif ch == "2":
                a2 += wr * wc
    return a1, a2


# ------------------------------------------------------------------ floaters
FLOATER_PARAMS = [
    Param("shape", "choice", "heart", "Shape", options=SHAPES,
          help="heart, ghost (with dark eyes) or a striped egg"),
    Param("motion", "choice", "rise", "Motion", options=["rise", "drift"],
          help="rise: float up the gable one after another and pop at the apex; "
               "drift: wander sideways across the gable, bobbing"),
    Param("colors", "colors", ["#ff0020", "#ff0040", "#ff2a2a", "#ff0030"], "Colours",
          help="each sprite takes the next colour; saturated colours read best"),
    Param("detail", "color", "#000000", "Detail colour",
          help="ghost eyes (dark reads as a hole); eggs use the next colour for their band"),
    Param("level", "number", 0.9, "Brightness", 0.1, 1, 0.01),
    Param("every", "number", 2.2, "New one every (s)", 0.3, 30, 0.1,
          help="average time between sprites (they never overlap)"),
    Param("count", "int", 3, "At most at once", 1, MAX_SPRITES, 1),
    Param("speed", "number", 1.1, "Speed", 0.1, 6, 0.05,
          help="rise: rows per second; drift: bulbs per second"),
    Param("sway", "number", 0.4, "Sway (bulbs)", 0, 2, 0.05, help="side-to-side sway while rising"),
    Param("flicker", "number", 0.15, "Flicker", 0, 1, 0.05, help="ghostly brightness flicker"),
    Param("pop", "number", 1.0, "Pop", 0, 1, 0.05,
          help="rise: how big the burst of sparks is when a sprite reaches the apex; 0 = it just fades"),
    Param("pop_color", "color", "#ffc8dc", "Spark colour", help="the sparks thrown out by a pop"),
    Param("background", "color", "#ff1a8c", "Glow colour",
          help="soft glow behind the sprites; a different hue from them makes the shapes read"),
    Param("glow", "number", 0.35, "Glow", 0, 0.6, 0.01, help="behind the sprites; 0 = dark (for overlays)"),
    Param("outline", "number", 0.7, "Outline glow", 0, 1, 0.01,
          help="the rest of the target (e.g. the gable outline) glows brighter, framing the sprites"),
    Param("seed", "int", 14, "Seed", 0, 9999, 1),
]


def _spawn(F: dict, P: dict, st: dict, rng: random.Random, clock: float):
    sprites = st["sprites"]
    bmp = SPRITES[P["shape"]]
    # w must be the hex sprite's width in bulb pitches (it is wider than the square bitmap):
    # with the bitmap's 5 the spacing rule left ~1 bulb of gap and lattice snapping closed it
    h = max(len(bmp), _hex_sprite(P["shape"]).height)
    w = max(len(bmp[0]), (_hex_sprite(P["shape"]).width + 1) // 2)
    n = F["n"]
    ncol = len(P["colors"]) or 1
    k = st.get("ci", rng.randrange(ncol))
    st["ci"] = (k + 1 + (rng.randrange(ncol - 1) if ncol > 2 and rng.random() < 0.3 else 0)) % ncol
    s = {"col": k, "born": clock, "ph": rng.random() * 6.283, "pop": None, "seed": rng.randrange(1 << 16)}
    if P["motion"] == "rise":
        if any(o["pop"] is None and o["top"] < h + 0.4 for o in sprites):
            return False                             # the last one has not cleared the bottom yet
        s.update(top=-0.6, off=rng.uniform(-1.0, 1.0))
    else:
        if n < h:
            bands = [n - 1]
        else:
            bands = list(range(h - 1, n))
        live = [o for o in sprites if "band" in o]
        # all ghosts drift the same way at the same speed, spaced apart, so they never overlap
        d = live[0]["dir"] if live else rng.choice((-1, 1))
        used = {o["band"] for o in live}
        free = [b for b in bands if b not in used] or bands
        band = rng.choice(free)
        reach = max(_halfspan(F, b - (h - 1) / 2) for b in bands) + (w / 2 + 0.6) * F["cp"]
        x0 = F["cx"] - d * reach
        if any(abs(o["x"] - x0) < (w + 3) * F["cp"] for o in live):
            return False
        s.update(band=band, dir=d, x=x0, end=F["cx"] + d * reach)
    sprites.append(s)
    return True


@frame_effect("floaters", "Floating hearts & ghosts", FLOATER_PARAMS,
              "Little pictures on the front gable's zigzag: hearts that float up one after another "
              "and pop at the apex in a ring of sparks, ghosts with dark eyes drifting across and "
              "bobbing, or striped eggs rising like balloons. Target 'Whole front gable' so the "
              "outline glows and the apex flashes when a heart pops; on 'Peak' alone just the "
              "zigzag is used.",
              group="Scenes")
def floaters(t, ctx, P, st):
    layout = ctx.layout
    px = layout.pixels
    n = len(px)
    out = [BLACK] * n
    idx = [i for i in ctx.idx if 0 <= i < n]
    if not idx:
        return out
    F = field(layout, idx)
    if not F["rows"]:
        return out
    clock = ctx.clock
    dt = _dt(ctx)
    if st.get("seed") != P["seed"] or "rng" not in st or st.get("shape") != (P["shape"], P["motion"]) \
            or clock < st.get("clock", clock) - 1e-6:
        st.clear()
        st["seed"], st["shape"] = P["seed"], (P["shape"], P["motion"])
        st["rng"] = random.Random(P["seed"] * 7349 + 3)
        st["sprites"], st["bursts"] = [], []
        st["next"] = clock + 0.4
    st["clock"] = clock
    rng = st["rng"]
    sprites, bursts = st["sprites"], st["bursts"]
    bmp = SPRITES[P["shape"]]
    h, w = len(bmp), len(bmp[0])
    nrows, cp, cx = F["n"], F["cp"], F["cx"]
    rise = P["motion"] == "rise"
    speed = P["speed"]

    # ---- spawn
    if clock >= st["next"] and len(sprites) < min(MAX_SPRITES, P["count"]):
        if _spawn(F, P, st, rng, clock):
            st["next"] = clock + P["every"] * rng.uniform(0.7, 1.3)
        else:
            st["next"] = clock + 0.1

    # ---- move
    top_row = nrows - 1
    keep = []
    for s in sprites:
        if s["pop"] is not None:
            if clock - s["pop"] < POP_S:
                keep.append(s)
            continue
        if rise:
            # slow a little near the top, so each one is seen whole before it pops
            prog = max(0.0, min(1.0, (s["top"] + 0.6) / (top_row + 0.6)))
            s["top"] += speed * dt * (1 - 0.45 * _smooth(prog))
            if s["top"] >= top_row + 0.12:
                s["top"] = top_row + 0.12
                s["pop"] = clock
                if P["pop"] > 0:
                    x, y = _sprite_xy(F, s, w, h, clock, P)
                    bursts.append({"x": x, "y": _row_y(F, s["top"] - (h - 1) * 0.35), "t": clock,
                                   "col": s["col"], "seed": s["seed"]})
                    del bursts[:-MAX_BURSTS]
            keep.append(s)
        else:
            s["x"] += s["dir"] * speed * cp * dt
            if (s["x"] - s["end"]) * s["dir"] < 0:
                keep.append(s)
    sprites[:] = keep[-MAX_SPRITES:]
    bursts[:] = [b for b in bursts if clock - b["t"] < BURST_S]

    # ---- draw
    cols = P["colors"] or [(1.0, 0.0, 0.3)]
    detail = P["detail"]
    lvl = P["level"]
    bg, gl = P["background"], P["glow"]
    flick = P["flicker"]
    seed = P["seed"]
    row_of, col_in = F["row_of"], F["col_in_row"]
    drawn = []                                         # (sprite, cx, top, alpha, white)
    for s in sprites:
        x, _ = _sprite_xy(F, s, w, h, clock, P)
        if rise:
            top = s["top"]
        else:
            top = s["band"] + 0.12 * math.sin(clock * 1.9 + s["ph"])
        a, white = 1.0, 0.0
        if s["pop"] is not None:
            f = (clock - s["pop"]) / POP_S
            a, white = max(0.0, 1 - f) ** 1.5, min(1.0, 2.5 * (1 - f)) * min(1.0, P["pop"] + 0.3)
        else:
            a = _smooth((clock - s["born"]) / 0.5)
        if flick > 0:
            a *= 1 - flick * noise2(clock * 2.3, s["seed"] * 0.37, 5)
        c1 = cols[s["col"] % len(cols)]
        c2 = cols[(s["col"] + 1) % len(cols)] if P["shape"] in ("egg", "big egg") else detail
        drawn.append((x, top, a, white, c1, c2))

    # on the Peak's honeycomb: lattice sprites, stamped where they are (cross-faded between
    # neighbouring places so they glide)
    hc = hx.get(layout, idx)
    stamps = None
    if hc is not None:
        hsp = _hex_sprite(P["shape"])
        stamps = []
        for (x, top, a, white, c1, c2) in drawn:
            acc: dict = {}
            _hex_stamp(hc, hsp, top - (hsp.height - 1), (x - cx) / cp, acc)
            stamps.append(acc)
    for r_i, row in enumerate(F["rows"]):
        for i in row:
            p = px[i]
            v = gl * (0.8 + 0.2 * noise2(clock * 0.4, i * 0.7, seed))
            c = (bg[0] * v, bg[1] * v, bg[2] * v)
            for si, (x, top, a, white, c1, c2) in enumerate(drawn):
                if stamps is not None:
                    a1, a2 = stamps[si].get(i, (0.0, 0.0))
                    if a1 <= 0 and a2 <= 0:
                        continue
                    a1, a2 = min(1.0, a1), min(1.0, a2)
                    sh = 0.9 + 0.1 * math.sin(clock * 3.1 + col_in.get(i, 0) * 0.9)
                    k1, k2 = a1 * a * lvl * sh, a2 * a * lvl
                    cover = min(1.0, (a1 + a2) * a)
                    col = (c1[0] * k1 + c2[0] * k2, c1[1] * k1 + c2[1] * k2, c1[2] * k1 + c2[2] * k2)
                    if white > 0:
                        col = (col[0] + (1 - col[0]) * white * a1 * 0.8, col[1] + (1 - col[1]) * white * a1 * 0.8,
                               col[2] + (1 - col[2]) * white * a1 * 0.8)
                    c = (c[0] * (1 - cover) + col[0], c[1] * (1 - cover) + col[1], c[2] * (1 - cover) + col[2])
                    continue
                vrow = top - r_i                       # sprite row under this bulb (0 = its top)
                if vrow < -1 or vrow > h:
                    continue
                u = (p.x - x) / cp + (w - 1) / 2
                if u < -1 or u > w:
                    continue
                if not rise:
                    # drifting: rest on whole bulbs and glide quickly between them, so
                    # single-bulb details (ghost eyes) are not smeared away
                    fl = math.floor(u)
                    u = fl + _smooth((u - fl - 0.375) / 0.25)
                a1, a2 = sample(bmp, u, vrow)
                if a1 <= 0 and a2 <= 0:
                    continue
                sh = 0.88 + 0.12 * math.sin(clock * 3.1 + col_in.get(i, 0) * 0.9)
                k1, k2 = a1 * a * lvl * sh, a2 * a * lvl
                cover = min(1.0, (a1 + a2) * a)
                col = (c1[0] * k1 + c2[0] * k2, c1[1] * k1 + c2[1] * k2, c1[2] * k1 + c2[2] * k2)
                if white > 0:
                    col = (col[0] + (1 - col[0]) * white * a1 * 0.8, col[1] + (1 - col[1]) * white * a1 * 0.8,
                           col[2] + (1 - col[2]) * white * a1 * 0.8)
                c = (c[0] * (1 - cover) + col[0], c[1] * (1 - cover) + col[1], c[2] * (1 - cover) + col[2])
            out[i] = c
    apex = set(F["apex"])
    ol = P["outline"]
    for i in F["rest"]:
        v = ol * (0.8 + 0.2 * noise2(clock * 0.4, i * 0.7, seed))
        if i in apex:
            v = min(1.0, v * 1.6)
        out[i] = (bg[0] * v, bg[1] * v, bg[2] * v)

    # ---- pops: the apex flashes, a ring of glittering sparks races out
    if bursts:
        pc = P["pop_color"]
        tick = int(clock * 18)
        for b in bursts:
            age = clock - b["t"]
            ring = 1.2 + age * 7.0
            fall = math.exp(-age / 0.45) * P["pop"]
            bc = cols[b["col"] % len(cols)]
            for i in idx:
                p = px[i]
                d = math.hypot(p.x - b["x"], (p.y - b["y"]) * 1.3)
                if d > ring + 1.6:
                    continue
                e = math.exp(-((d - ring) / 0.9) ** 2)
                if d < ring:
                    hs = hash01(i, tick, b["seed"])
                    if hs > 0.72:
                        e = max(e, (hs - 0.72) / 0.28 * math.exp(-(ring - d) / 2.5))
                e *= fall * (0.5 + 0.5 * hash01(i, tick, b["seed"], 2))
                if e <= 0.01:
                    continue
                sc = pc if hash01(i, b["seed"], 3) < 0.6 else bc
                o = out[i]
                out[i] = (o[0] + (sc[0] - o[0] * 0.5) * e, o[1] + (sc[1] - o[1] * 0.5) * e,
                          o[2] + (sc[2] - o[2] * 0.5) * e)
            if age < 0.6:
                f = math.exp(-age / 0.18)
                for i in apex:
                    o = out[i]
                    out[i] = (o[0] + (1 - o[0]) * f, o[1] + (pc[1] - o[1]) * f, o[2] + (pc[2] - o[2]) * f)
    for i in idx:
        out[i] = _clamp(out[i])
    return out


def _sprite_xy(F: dict, s: dict, w: int, h: int, clock: float, P: dict) -> tuple[float, float]:
    cp = F["cp"]
    if "band" in s:
        return s["x"], 0.0
    # rising: keep the whole sprite inside the triangle at its top row, swaying gently
    room = max(0.0, _halfspan(F, max(0.0, s["top"])) - (w - 1) / 2 * cp - 0.25 * cp)
    sway = P["sway"] * cp * math.sin(clock * 1.3 + s["ph"])
    x = F["cx"] + s["off"] * room + sway * min(1.0, room / max(cp, 1e-6))
    return x, 0.0


# ------------------------------------------------------------------ flag
FLAG_PARAMS = [
    Param("colors", "colors", ["#ff0010", "#c8c8d2"], "Stripes",
          help="the row colours, bottom row first, repeating"),
    Param("canton", "color", "#1030ff", "Star field"),
    Param("stars", "color", "#ffffff", "Stars"),
    Param("canton_rows", "int", 3, "Star field rows", 0, 6, 1, help="how many top rows the star field covers"),
    Param("canton_edge", "number", 0.5, "Star field width", 0.1, 1, 0.01,
          help="how far across the gable (from the left) the star field may reach; on the Peak it "
               "keeps a flag's proportions, so it is usually narrower"),
    Param("twinkle", "number", 0.5, "Star twinkle", 0, 3, 0.05, help="glints per star per second"),
    Param("wave", "number", 0.32, "Wave depth", 0, 0.9, 0.01, help="how much the ripples shade the flag"),
    Param("wavelength", "number", 9, "Ripple length (bulbs)", 2, 40, 0.5),
    Param("speed", "number", 4, "Ripple speed (bulbs/s)", -20, 20, 0.1),
    Param("level", "number", 0.9, "Brightness", 0.1, 1, 0.01),
    Param("intro", "number", 3, "Unfurl (s)", 0, 20, 0.5,
          help="when the scene starts the flag unfurls from the left; 0 = no intro"),
    Param("seed", "int", 4, "Seed", 0, 9999, 1),
]


CANTON_RATIO = 1.9      # the star field's width / height as seen on the house (a flag's own shape)


def canton_cells(hc, canton_rows: int, canton_edge: float) -> dict:
    """The flag's star field on the Peak's honeycomb: {bulb: is a star}.

    The top `canton_rows` rows of the Peak (and the Tip above them when it is hung), each
    from its left end (against the roof's slope) for as many bulbs as make the block
    CANTON_RATIO times as wide as it is tall in TRUE proportions: a row is only
    hc.aspect (about 0.66) of a bulb spacing tall, so 3 rows (2 spacings) take 4 bulbs, not
    the 10-11 that stretched it into a long blue band. It never reaches past `canton_edge`
    (the fraction of the bottom row from its left end). Stars sit on bulbs ringed by blue on
    every side (a star touching a stripe reads as a white bar), inside the field before its
    edge, never two side by side, so they read as separate points. Chosen by lattice place,
    so a dead bulb shifts nothing."""
    if canton_rows <= 0:
        return {}
    crow = hc.peak_rows - canton_rows
    ex = hc.rows[0].x0 + canton_edge * (hc.rows[0].x1 - hc.rows[0].x0)
    n = sum(1 for rw in hc.rows if rw.r >= crow)
    per_row = max(3, int(round(CANTON_RATIO * n * hc.aspect)))
    blue = set()
    for rw in hc.rows:
        if rw.r < crow:
            continue
        for c in rw.cells:
            if c.x <= min(ex, rw.x0 + per_row - 1) + 1e-6:
                blue.add(c.key)
    stars: set = set()
    # inside the field first (most neighbours), then bottom-left first
    for key in sorted(blue, key=lambda k: (-len(hc.neighbours(hc._by_key[k])), k[0], k[1])):
        c = hc._by_key[key]
        ring = hc.neighbours(c)
        if all(n.key in blue for n in ring) and not any(n.key in stars for n in ring):
            stars.add(key)
    return {hc._by_key[k].i: k in stars for k in blue if hc._by_key[k].i is not None}


@frame_effect("flag", "Waving flag", FLAG_PARAMS,
              "A flag on the front gable's zigzag: red and white stripes on the rows, a blue "
              "field of twinkling white stars in the top-left of the triangle, ripples rolling "
              "across it like cloth in a breeze. It unfurls from the left when it starts. "
              "Target the Peak.",
              group="Scenes")
def flag(t, ctx, P, st):
    layout = ctx.layout
    px = layout.pixels
    n = len(px)
    out = [BLACK] * n
    idx = [i for i in ctx.idx if 0 <= i < n]
    if not idx:
        return out
    F = field(layout, idx)
    rows = F["rows"]
    if not rows:
        return out
    clock = ctx.clock
    if "start" not in st or clock < st["start"] - 1e-6:
        st["start"] = clock
    age = clock - st["start"]
    nrows = len(rows)
    left, right = F["left"], F["right"]
    width = max(1e-6, right - left)
    edge = left + P["canton_edge"] * width
    crow = nrows - P["canton_rows"]
    cols = P["colors"] or [(1.0, 0.0, 0.0)]
    can, star = P["canton"], P["stars"]
    depth, lam, lvl = P["wave"], P["wavelength"], P["level"]
    tr = travel(t, P)
    D = P["intro"]
    front = (age / D) * (width + 4.0) - 2.0 if D > 0 and age < D else 1e9
    rate, seed = P["twinkle"], P["seed"]
    # on the Peak's honeycomb (and up the Tip when it is hung): the star field is a block in
    # the top-left corner, against the roof's slope, in TRUE proportions (canton_cells)
    hc = hx.get(layout, idx)
    is_star = {}
    if hc is not None:
        rows = [[c.i for c in rw.cells if c.i is not None and c.i in hc.allowed] for rw in hc.rows]
        is_star = canton_cells(hc, P["canton_rows"], P["canton_edge"])
    for r, row in enumerate(rows):
        base = cols[r % len(cols)]
        for k, i in enumerate(row):
            p = px[i]
            if hc is not None:
                xr = p.x - left
                ph = (xr - tr + r * 0.6) / lam * 2 * math.pi
                crest = 0.5 + 0.5 * math.cos(ph)
                amp = depth * min(1.0, 0.35 + xr / 6.0)
                v = lvl * (1 - amp * (1 - crest))
                if i in is_star:
                    if is_star[i]:
                        g = glint(i, clock, rate, 0.04, 0.35, seed, 1)[0] if rate > 0 else 0.0
                        s = 0.85 + 0.15 * g
                        c = (star[0] * s * v, star[1] * s * v, star[2] * s * v)
                    else:
                        c = (can[0] * v, can[1] * v, can[2] * v)
                else:
                    c = (base[0] * v, base[1] * v, base[2] * v)
                sheen = 0.14 * crest ** 6 * depth
                c = (c[0] + (1 - c[0]) * sheen, c[1] + (1 - c[1]) * sheen, c[2] + (1 - c[2]) * sheen)
                if front < 1e8:
                    f = _smooth((front - xr) / 2.0)
                    c = (c[0] * f, c[1] * f, c[2] * f)
                out[i] = _clamp(c)
                continue
            xr = p.x - left
            # the ripple: a crest rolling across, a little diagonal like cloth
            ph = (xr - tr + r * 0.6) / lam * 2 * math.pi
            crest = 0.5 + 0.5 * math.cos(ph)
            # the cloth hangs from the pole on the left: less movement there
            amp = depth * min(1.0, 0.35 + xr / 6.0)
            v = lvl * (1 - amp * (1 - crest))
            if r >= crow and p.x <= edge:
                if (k + 2 * r) % 3 == 0:              # a star on every third bulb
                    g = glint(i, clock, rate, 0.04, 0.35, seed, 1)[0] if rate > 0 else 0.0
                    s = 0.85 + 0.15 * g
                    c = (star[0] * s * v, star[1] * s * v, star[2] * s * v)
                else:
                    c = (can[0] * v, can[1] * v, can[2] * v)
            else:
                c = (base[0] * v, base[1] * v, base[2] * v)
            sheen = 0.14 * crest ** 6 * depth
            c = (c[0] + (1 - c[0]) * sheen, c[1] + (1 - c[1]) * sheen, c[2] + (1 - c[2]) * sheen)
            if front < 1e8:
                f = _smooth((front - xr) / 2.0)
                c = (c[0] * f, c[1] * f, c[2] * f)
            out[i] = _clamp(c)
    return out
