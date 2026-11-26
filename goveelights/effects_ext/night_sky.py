"""Christmas-Eve night scenes for the house.

    starry_night  A calm deep-blue / indigo night over the whole house: a sparse, fixed set
                  of bulbs are stars that twinkle softly, each at its own pace, and every N
                  seconds a shooting star streaks across the top of the house (along the
                  gable outlines, over an apex and down the slope) with a bright,
                  anti-aliased head and a fading tail. Now and then two come together.
    gift_wrap     The front of the house wrapped like a present: wrapping paper with a
                  subtle pattern (diagonal stripes or polka dots), a ribbon running down
                  through the apex to the porch and one band across the zigzag, and a big
                  shimmering bow on the apex. Starts by tying the ribbon (bands draw in from
                  the edges, then the bow pops); optionally unwraps every few minutes (the
                  ribbon unties, the zigzag bursts into sparkles) and wraps itself again.
    advent        Advent calendar: 24 "doors" (groups of bulbs along the zigzag rows, or
                  along the roofline if the target has no zigzag). On day D of December the
                  first D glow gold, today's pulses and sparkles, the rest wait as dim warm
                  placeholders. On the 24th (and outside December) the whole calendar is
                  open and shimmers.

All three are @frame_effect generators: what they remember (timelines, shooting stars,
cached geometry) lives in the layer's state dict, randomness is hashed or comes from a
random.Random kept there (deterministic for a seed and a frame sequence), lists are
bounded, and every clock jump (backwards, a stall, dt 0) is handled.
"""
from __future__ import annotations

import bisect
import datetime as _dt
import math
import random

from .. import geometry as geo
from ..color import BLACK, hash01, lerp, noise2
from ..effects import Param, frame_effect
from ..sparkle import glint

TAU = 2 * math.pi
WHITE = (1.0, 1.0, 1.0)

_today = _dt.date.today        # the advent calendar's date (patched by tests)


# ====================================================================== shared helpers
def _smooth(x: float) -> float:
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    return x * x * (3 - 2 * x)


def _target(ctx) -> tuple[list, list[int]]:
    n = len(ctx.layout.pixels)
    return [BLACK] * n, [i for i in ctx.idx if 0 <= i < n]


def _clamp(frame: list, idx) -> list:
    for i in idx:
        r, g, b = frame[i]
        if not (0.0 <= r <= 1.0 and 0.0 <= g <= 1.0 and 0.0 <= b <= 1.0):
            frame[i] = (min(1.0, max(0.0, r)) if r == r else 0.0,
                        min(1.0, max(0.0, g)) if g == g else 0.0,
                        min(1.0, max(0.0, b)) if b == b else 0.0)
    return frame


def _sig(layout, idx) -> tuple:
    return (id(layout), len(layout.pixels), len(idx), hash(tuple(idx)))


def _zigzag(layout, idx: list[int]) -> list[list[int]]:
    """Rows (bottom first, each left to right) of the part of the target that is wired as a
    zigzag (the Peak), or [] if nothing in the target is."""
    px = layout.pixels
    groups: dict[str, list[int]] = {}
    for i in idx:
        groups.setdefault(px[i].run, []).append(i)
    for g in sorted(groups.values(), key=len, reverse=True):
        r = geo._rows_by_reversal(px, g)
        if r:
            r = [sorted(row, key=lambda i: px[i].x) for row in r]
            r.sort(key=lambda row: sum(px[i].y for i in row) / len(row))
            return r
    return []


def _rows_by_height(px, idx, tol=0.45) -> list[list[int]]:
    """Group bulbs into horizontal rows by height (bottom first, each left to right)."""
    out: list[list[int]] = []
    for i in sorted(idx, key=lambda i: px[i].y):
        if out and abs(px[i].y - px[out[-1][0]].y) <= tol:
            out[-1].append(i)
        else:
            out.append([i])
    return [sorted(r, key=lambda i: px[i].x) for r in out]


def _crown(layout, idx: list[int], zig: list[list[int]], count: int = 2) -> list[int]:
    """The bulbs crowning the zigzag (the gable apex) if they are in the target; else the
    middle of the zigzag's top row; with no zigzag, the target's highest bulbs."""
    px = layout.pixels
    iset = set(idx)
    if zig:
        body = [i for r in zig for i in r]
        zset = set(body)
        xs = [px[i].x for i in body]
        lo, hi, top = min(xs), max(xs), max(px[i].y for i in body)
        mid = (lo + hi) / 2
        above = sorted((p.i for p in px if p.i not in zset and p.y > top + 0.3 and lo - 1 <= p.x <= hi + 1),
                       key=lambda i: (-px[i].y, abs(px[i].x - mid)))[:count]
        if above and all(i in iset for i in above):
            return above
        row = zig[-1]
        cx = sum(px[i].x for i in row) / len(row)
        return sorted(row, key=lambda i: abs(px[i].x - cx))[:count if len(row) >= 4 else 1]
    if not idx:
        return []
    xs = [px[i].x for i in idx]
    mid = (min(xs) + max(xs)) / 2
    top = max(px[i].y for i in idx)
    cand = [i for i in idx if px[i].y >= top - 0.35]
    return sorted(cand, key=lambda i: abs(px[i].x - mid))[:count]


def _tracks(layout, idx) -> list:
    """The target split into physically continuous chains of bulbs (strip order, split
    where the next bulb is on another run or not a neighbour), as geometry.Paths."""
    px = layout.pixels
    groups, cur = [], []
    for i in idx:
        if cur:
            a, b = px[cur[-1]], px[i]
            if a.run != b.run or math.hypot(b.x - a.x, b.y - a.y) > 2.5:
                groups.append(cur)
                cur = []
        cur.append(i)
    if cur:
        groups.append(cur)
    return [geo.path(layout, g) for g in groups if len(g) >= 2]


# ====================================================================== starry night
SN_MAX_METEORS = 6
SN_HEAD_W = 1.25       # half-width of the meteor's anti-aliased leading edge (bulbs)


def _sn_setup(ctx, st, P):
    L = ctx.layout
    idx = [i for i in ctx.idx if 0 <= i < len(L.pixels)]
    px = L.pixels
    seed = P["seed"]
    sig = _sig(L, idx)
    # ---- the stars: a sparse, fixed set; never two neighbours on the same strand. Keyed on
    # the share and the seed only, so modulating 'Stars' never disturbs the shooting stars.
    skey = (sig, round(P["stars"], 3), seed)
    if st.get("skey") != skey:
        st["skey"] = skey
        _sn_stars(st, idx, P["stars"], seed)
    if st.get("key") == sig:
        return
    st["key"] = sig
    # ---- the sky: per-bulb noise coordinates (the sky drifts slowly between blue and indigo)
    st["sky"] = [(i, px[i].x * 0.085, px[i].y * 0.22) for i in idx]
    # ---- where shooting stars fly: the rooflines (highest first), else the top row
    tracks = []
    for pth in _tracks(L, idx):
        if geo._rows_by_reversal(px, pth.order) is not None:
            continue                                   # a zigzag fill is not a roofline
        tracks.append(pth)
    if not tracks:
        rows = _zigzag(L, idx) or _rows_by_height(px, idx)
        if rows and len(rows[-1]) >= 2:
            tracks = [geo.path(L, rows[-1])]
    sky = []
    for pth in tracks:
        if pth.length < 3:
            continue
        ys = pth.y
        apexes = []
        for k, y in enumerate(ys):
            lo, hi = max(0, k - 4), min(len(ys), k + 5)
            if all(y >= ys[j] - 1e-6 for j in range(lo, hi)) and y - min(ys[max(0, k - 12):k + 13]) >= 1.5:
                if not apexes or pth.s[k] - apexes[-1] > 3:
                    apexes.append(pth.s[k])
        sky.append((pth, apexes, max(ys), pth.length * (1.0 + max(0.0, max(ys) - min(ys)))))
    # the highest rooflines get nearly all the shooting stars; lower ones the odd one
    hi = max((s[2] for s in sky), default=0.0)
    st["tracks"] = [(pth, ap, w * math.exp(-(hi - top) / 2.0)) for pth, ap, top, w in sky]
    st["meteors"] = []                                 # the tracks changed: old meteors are void


def _sn_stars(st, idx, share, seed):
    stars = {}
    for i in idx:
        h = hash01(i, seed, 501)
        if h < share and (i - 1) not in stars:
            a, b, c = hash01(i, seed, 502), hash01(i, seed, 503), hash01(i, seed, 504)
            stars[i] = (0.12 + 0.5 * a,                  # twinkle rate (Hz): each star its own pace
                        b * 97.0,                        # phase
                        0.5 + 0.5 * c * c,               # resting brightness: a few big stars
                        hash01(i, seed, 505))            # colour pick
    st["stars"] = stars


def _sn_spawn(st, rng, P, clock, delay=0.0, avoid=None):
    tracks = st["tracks"]
    if not tracks or len(st["meteors"]) >= SN_MAX_METEORS:
        return None
    tot = sum(w for _, _, w in tracks)
    r, k = rng.random() * tot, 0
    while k < len(tracks) - 1 and r > tracks[k][2]:
        r -= tracks[k][2]
        k += 1
    pth, apexes, _ = tracks[k]
    Lp = pth.length
    length = min(Lp * 0.9, 16.0 + rng.random() * 14.0)
    choices = [a for a in apexes if a != avoid] or apexes
    if choices:
        a = choices[int(rng.random() * len(choices)) % len(choices)]
        d = 1 if rng.random() < 0.5 else -1
        room_fwd = (Lp - a) if d > 0 else a
        if room_fwd < 5:                               # nothing beyond the apex: fly the other way
            d = -d
        s0 = a - d * rng.random() * 5.0                # start just before (or at) the apex: over and down
        start_key = a
    else:
        d = 1 if rng.random() < 0.5 else -1
        s0 = rng.random() * Lp
        if (Lp - s0 if d > 0 else s0) < length * 0.6:
            d = -d
        start_key = None
    s0 = min(Lp, max(0.0, s0))
    m = {"k": k, "s0": s0, "dir": d, "len": max(3.0, length),
         "v": P["shooting_speed"] * (0.85 + 0.3 * rng.random()),
         "b": 0.85 + 0.15 * rng.random(), "t0": clock + delay, "apex": start_key}
    st["meteors"].append(m)
    return m


def _sn_env(x: float, length: float) -> float:
    """A meteor's brightness when its head has flown x bulbs: flares in, burns out."""
    if x <= 0:
        return 0.0
    return _smooth(x / 1.5) * (1.0 - _smooth((x - 0.55 * length) / (0.45 * length)))


@frame_effect("starry_night", "Starry night", [
    Param("sky", "color", "#0a1eff", "Sky colour", help="deep, saturated blue reads best on LEDs"),
    Param("sky2", "color", "#3a10d0", "Second sky colour",
          help="the sky drifts slowly between the two (indigo by default)"),
    Param("sky_level", "number", 0.6, "Sky brightness", 0, 1, 0.01,
          help="low enough for the stars to stand out, high enough to survive the late-night dim "
               "(very dark reads as off)"),
    Param("drift", "number", 1.0, "Sky drift", 0, 5, 0.1, help="how fast the sky's colours wander; 0 = still"),
    Param("stars", "number", 0.14, "Stars", 0, 0.5, 0.01, help="share of bulbs that are stars"),
    Param("star_colors", "colors", ["#ffffff", "#cfdcff", "#ffe2a8"], "Star colours",
          help="the first colour is the most common"),
    Param("star_level", "number", 0.85, "Star brightness", 0.05, 1, 0.01),
    Param("twinkle", "number", 0.6, "Twinkle depth", 0, 1, 0.05,
          help="how far each star dims as it twinkles; 0 = steady"),
    Param("twinkle_speed", "number", 1.0, "Twinkle speed", 0.1, 5, 0.1),
    Param("shooting_every", "number", 10, "Shooting star every (s)", 0, 300, 1,
          help="average seconds between shooting stars (a little irregular); 0 = none"),
    Param("shooting_speed", "number", 22, "Shooting star speed (bulbs/s)", 10, 120, 1),
    Param("tail", "number", 8, "Tail length (bulbs)", 1, 30, 0.5),
    Param("shooting_color", "color", "#ffffff", "Shooting star colour"),
    Param("tail_color", "color", "#c8d8ff", "Tail colour",
          help="the tail turns from the head colour to this as it fades; keep it pale so it reads"),
    Param("doubles", "number", 0.25, "Doubles", 0, 1, 0.05,
          help="chance that a second shooting star follows the first moments later"),
    Param("seed", "int", 3, "Seed", 0, 9999, 1, help="which bulbs are stars, and the shooting stars' paths"),
], "A calm deep-blue night over the house: sparse stars twinkle softly, each at its own pace, "
   "while the sky drifts slowly between blue and indigo. Every few seconds a shooting star "
   "streaks across the top of the house, over a gable apex and down the slope, with a bright "
   "head and a fading tail; now and then two come together. Target 'all' (the whole house).",
   group="Scenes")
def starry_night(t, ctx, P, st):
    frame, idx = _target(ctx)
    if not idx:
        return frame
    _sn_setup(ctx, st, P)
    clock = ctx.clock
    seed = P["seed"]
    if st.get("seed") != seed or "rng" not in st:
        st["seed"], st["rng"] = seed, random.Random(seed * 7919 + 17)
    rng = st["rng"]

    # ---- sky
    sky, sky2, lv = P["sky"], P["sky2"], P["sky_level"]
    dr = P["drift"] * clock
    for i, nx, ny in st["sky"]:
        m = noise2(nx + dr * 0.045, ny + dr * 0.011, seed + 7)
        w = lv * (0.78 + 0.22 * m)
        frame[i] = ((sky[0] + (sky2[0] - sky[0]) * m) * w, (sky[1] + (sky2[1] - sky[1]) * m) * w,
                    (sky[2] + (sky2[2] - sky[2]) * m) * w)

    # ---- stars
    cols = P["star_colors"] or [WHITE]
    nc = len(cols)
    depth, sp, slv = P["twinkle"], P["twinkle_speed"], P["star_level"]
    for i, (rate, ph, base, pick) in st["stars"].items():
        w = noise2(clock * rate * sp + ph, i * 1.618, seed + 11)
        w = _smooth(w * 1.25 - 0.1)
        b = base * (1.0 - depth + depth * w)
        g = glint(i, clock, 0.025, 0.06, 0.45, seed + 3, 1)[0]      # a rare sharp glitter
        b = min(1.0, b + g * 0.6) * slv
        ci = 0 if pick < 0.6 or nc == 1 else 1 + int((pick - 0.6) / 0.4 * (nc - 1)) % (nc - 1)
        c = cols[ci]
        s = frame[i]
        k = 1.0 - min(1.0, b * 1.3)                                 # the star outshines the sky behind it
        frame[i] = (s[0] * k + c[0] * b, s[1] * k + c[1] * b, s[2] * k + c[2] * b)

    # ---- shooting stars: scheduling
    every = P["shooting_every"]
    last = st.get("last")
    if last is None or clock < last - 1e-3 or clock > last + 30.0:   # start, rewind or a long stall
        st["meteors"] = []
        st["next"] = clock + (min(3.0, every * 0.35) if every > 0 else 0.0)
    st["last"] = clock
    if every > 0:
        nxt = st.get("next")
        if nxt is None or nxt > clock + every * 1.5 + 3.0:          # the interval was shortened
            nxt = st["next"] = clock + min(3.0, every * 0.35)
        if clock >= nxt:
            m = _sn_spawn(st, rng, P, clock)
            if m is not None and rng.random() < P["doubles"]:
                _sn_spawn(st, rng, P, clock, 0.25 + rng.random() * 0.45, avoid=m["apex"])
            nxt += every * (0.6 + 0.8 * rng.random())
            if nxt <= clock:
                nxt = clock + every * (0.6 + 0.8 * rng.random())
            st["next"] = nxt

    # ---- shooting stars: render
    if st["meteors"]:
        hc, tc, tail = P["shooting_color"], P["tail_color"], P["tail"]
        keep = []
        for m in st["meteors"]:
            if m["k"] >= len(st["tracks"]):
                continue
            pth = st["tracks"][m["k"]][0]
            age = clock - m["t0"]
            x = age * m["v"]                            # how far the head has flown
            if x > m["len"] + tail + 2.0:
                continue
            keep.append(m)
            if age <= 0:
                continue
            d, ln, mb = m["dir"], m["len"], m["b"]
            sh = m["s0"] + d * x
            e_head = _sn_env(x, ln) * mb
            lo, hi = (sh - tail, sh + SN_HEAD_W) if d > 0 else (sh - SN_HEAD_W, sh + tail)
            S, order = pth.s, pth.order
            j0 = max(0, bisect.bisect_left(S, lo))
            j1 = min(len(S), bisect.bisect_right(S, hi))
            for j in range(j0, j1):
                dd = d * (sh - S[j])                    # distance behind the head (negative: ahead)
                if dd > x:                              # before the meteor's starting point: never lit
                    continue
                if dd < 0:
                    w = e_head * (1.0 + dd / SN_HEAD_W) ** 2
                    c = (hc[0] * w, hc[1] * w, hc[2] * w)
                else:
                    wh = e_head * math.exp(-dd / 0.8)
                    u = dd / tail
                    wt = _sn_env(x - dd, ln) * mb * (1.0 - u) * 0.9
                    tcol = lerp(hc, tc, min(1.0, u))
                    c = (hc[0] * wh + tcol[0] * wt, hc[1] * wh + tcol[1] * wt, hc[2] * wh + tcol[2] * wt)
                i = order[j]
                a = frame[i]
                k = 1.0 - 0.5 * min(1.0, max(c) * 1.5)    # the streak cuts through the sky behind it
                frame[i] = (max(a[0] * k, c[0]), max(a[1] * k, c[1]), max(a[2] * k, c[2]))
        st["meteors"] = keep[-SN_MAX_METEORS:]
    return _clamp(frame, idx)


# ====================================================================== gift wrap
GW_PALETTES = {
    #                        paper               pattern             ribbon              bow
    "classic red & gold": ((1.0, 0.0, 0.0), (0.7, 0.0, 0.05), (1.0, 0.68, 0.1), (1.0, 0.86, 0.45)),
    "green & silver": ((0.0, 0.75, 0.06), (0.35, 1.0, 0.0), (0.72, 0.8, 1.0), (0.9, 0.94, 1.0)),
    "blue & white": ((0.0, 0.1, 1.0), (0.0, 0.55, 1.0), (0.92, 0.94, 1.0), (1.0, 1.0, 1.0)),
}
GW_UNWRAP_S = 5.0      # how long an unwrap (untie, burst, settle) takes before rewrapping
GW_RIBBON_HW = 1.4     # half-width of the vertical ribbon (bulbs): ~4 bulbs per zigzag row
GW_PAPER_FLOOR = 0.3   # the zigzag keeps this much paper under the burst (dark reads as a glitch)


def _gw_geometry(layout, idx: list[int]) -> dict:
    key = ("gift_wrap", _sig(layout, idx))
    c = geo._cache(layout)
    if key in c:
        return c[key]
    px = layout.pixels
    zig = _zigzag(layout, idx)
    zset = {i for r in zig for i in r}
    crown = _crown(layout, idx, zig)
    xs = [px[i].x for i in idx]
    ys = [px[i].y for i in idx]
    if crown:
        ax = sum(px[i].x for i in crown) / len(crown)
        ay = sum(px[i].y for i in crown) / len(crown)
    else:
        ax, ay = (min(xs) + max(xs)) / 2, max(ys)
    # the horizontal band: zigzag rows 2-3 (and every target bulb at those heights)
    if len(zig) >= 3:
        y1 = sum(px[i].y for i in zig[1]) / len(zig[1])
        y2 = sum(px[i].y for i in zig[2]) / len(zig[2])
        yc, hh = (y1 + y2) / 2, abs(y2 - y1) / 2 + 0.3
    else:
        yc, hh = (min(ys) + max(ys)) / 2, 0.6
    cset = set(crown)
    # the bow: the crown is the knot. Each loop is the outline running down from the knot on
    # one side plus the matching stretch of the zigzag's top row below it (a loop's two edges);
    # the top-row bulb either side of the centre stays paper so knot, loops and the ribbon's
    # tails (the centre of the top row) read apart.
    bow, gaps = {}, set()
    for i in idx:
        d = math.hypot(px[i].x - ax, px[i].y - ay)
        if i in cset:
            bow[i] = 0.0
        elif i not in zset and d <= (3.3 if zig else 1.6):
            bow[i] = d
    if zig and crown and len(zig[-1]) >= 5:
        for i in zig[-1]:
            dx = abs(px[i].x - ax)
            if 1.4 <= dx <= 3.5:
                bow[i] = math.hypot(px[i].x - ax, px[i].y - ay)
            elif 0.75 <= dx < 1.4:
                gaps.add(i)
    bow_r = max(bow.values(), default=0.0) or 1.0
    vcov, hcov = {}, {}
    for i in idx:
        if i in bow or i in gaps:
            continue
        p = px[i]
        v = 1.0 - _smooth((abs(p.x - ax) - GW_RIBBON_HW) / 0.6 + 0.5)
        h = 1.0 - _smooth((abs(p.y - yc) - hh) / 0.5 + 0.5)
        if v > 0.02:
            vcov[i] = v
        if h > 0.02:
            hcov[i] = h
    # vertical ribbon draws in from both ends toward the crossing, bulb by bulb
    vtop = sorted((i for i in vcov if px[i].y >= yc), key=lambda i: -px[i].y)
    vbot = sorted((i for i in vcov if px[i].y < yc), key=lambda i: px[i].y)
    vrank = {}
    for side in (vtop, vbot):
        # bulbs at about the same height share a rank, so a row's ribbon bulbs light together
        r, last = -1, None
        for i in side:
            if last is None or abs(px[i].y - last) > 0.3:
                r += 1
                last = px[i].y
            vrank[i] = (r, len(side))
        nr = r + 1
        for i in side:
            vrank[i] = (vrank[i][0], max(1, nr))
    # pattern: diagonal stripes phase, polka dots staggered along each row
    dots = set()
    for r, row in enumerate(zig):
        for k, i in enumerate(row):
            if (k + 2 * r) % 3 == 0:
                dots.add(i)
    other = [i for i in idx if i not in zset]
    k = 0
    for a, b in zip([None] + other, other):
        if a is not None and (px[a].run != px[b].run or abs(px[a].x - px[b].x) + abs(px[a].y - px[b].y) > 2.5):
            k = 0
        if k % 3 == 1:
            dots.add(b)
        k += 1
    out = {"zig": zset, "crown": crown, "gaps": gaps, "ax": ax, "ay": ay, "yc": yc, "bow": bow, "bow_r": bow_r,
           "vcov": vcov, "hcov": hcov, "vrank": vrank, "dots": dots,
           "xl": min(xs), "xr": max(xs),
           "pos": {i: (px[i].x, px[i].y) for i in idx},
           "dist": {i: math.hypot(px[i].x - ax, (px[i].y - yc) * 1.6) for i in idx}}
    c[key] = out
    return out


def _gw_timeline(clock: float, P: dict, st: dict) -> tuple[str, float]:
    """(phase, seconds into it): tie -> wrapped -> unwrap -> tie ..."""
    T, E = P["tie"], P["unwrap_every"]
    if "start" not in st or clock < st["start"] - 1e-6 or clock > st.get("last", clock) + 120:
        st["phase"], st["start"], st["rewrap"] = "tie", clock, False
    st["last"] = clock
    for _ in range(4):                     # a long stall may cross several phases
        ph, age = st["phase"], clock - st["start"]
        if ph == "tie" and age >= T:
            st["phase"], st["start"] = "wrapped", st["start"] + max(0.0, T)
        elif ph == "wrapped" and E > 0 and age >= max(E, 5.0):
            st["phase"], st["start"] = "unwrap", st["start"] + max(E, 5.0)
        elif ph == "unwrap" and age >= GW_UNWRAP_S:
            st["phase"], st["start"], st["rewrap"] = "tie", st["start"] + GW_UNWRAP_S, True
        else:
            break
    if clock - st["start"] > 1e5:          # keep the numbers small on very long runs
        st["start"] = clock - (clock - st["start"]) % 1000.0
    return st["phase"], max(0.0, clock - st["start"])


@frame_effect("gift_wrap", "Gift wrap", [
    Param("palette", "choice", "classic red & gold", "Wrapping",
          options=list(GW_PALETTES) + ["custom"],
          help="paper and ribbon colours; 'custom' uses the four colours below"),
    Param("paper", "color", "#ff0000", "Paper colour (custom)"),
    Param("pattern_color", "color", "#ff4800", "Pattern colour (custom)"),
    Param("ribbon", "color", "#ff9400", "Ribbon colour (custom)"),
    Param("bow", "color", "#ffc038", "Bow colour (custom)"),
    Param("pattern", "choice", "stripes", "Paper pattern", options=["stripes", "dots", "plain"],
          help="diagonal stripes or polka dots on the paper, by position on the house"),
    Param("pattern_depth", "number", 0.7, "Pattern strength", 0, 1, 0.05, help="0 = plain paper"),
    Param("stripe_width", "number", 5.5, "Stripe spacing (bulbs)", 1.5, 12, 0.5,
          help="distance from one stripe to the next; about 5 gives stripes 2 bulbs wide"),
    Param("level", "number", 0.85, "Brightness", 0.05, 1, 0.01),
    Param("sheen", "number", 0.35, "Paper sheen", 0, 1, 0.05,
          help="a soft glint of light sweeping across the paper now and then"),
    Param("shimmer", "number", 0.6, "Bow shimmer", 0, 1, 0.05, help="glints on the bow and the ribbon"),
    Param("tie", "number", 7, "Tie the ribbon intro (s)", 0, 30, 0.5,
          help="when the scene starts the ribbon draws in from the edges, then the bow pops; 0 = no intro"),
    Param("unwrap_every", "number", 0, "Unwrap every (s)", 0, 900, 1,
          help="the ribbon unties, the zigzag bursts into sparkles, then it wraps itself again; 0 = never"),
    Param("seed", "int", 9, "Seed", 0, 9999, 1),
], "The front of the house wrapped like a present: wrapping paper with subtle diagonal stripes "
   "or polka dots, a ribbon running down through the apex to the porch and a band across the "
   "zigzag, and a big shimmering bow on the gable apex. Starts by tying the ribbon (the bands "
   "draw in from the edges, then the bow pops); can unwrap now and then (the ribbon unties, the "
   "zigzag bursts into sparkles) and wrap itself again. Target the zigzag, the gable outline and "
   "the porch together.", group="Scenes")
def gift_wrap(t, ctx, P, st):
    frame, idx = _target(ctx)
    if not idx:
        return frame
    L = ctx.layout
    g = _gw_geometry(L, idx)
    clock = ctx.clock
    seed = P["seed"]
    if P["palette"] in GW_PALETTES:
        paper, patc, rib, bowc = GW_PALETTES[P["palette"]]
    else:
        paper, patc, rib, bowc = P["paper"], P["pattern_color"], P["ribbon"], P["bow"]
    lv = P["level"]
    phase, age = _gw_timeline(clock, P, st)
    T = P["tie"]

    # ---- where in the story we are
    paper_on, paper_zig, prog, bow_on, pop, burst_t = 1.0, 1.0, 1.0, 1.0, -1.0, -1.0
    if phase == "tie" and T > 0:
        x = age / T
        paper_on = _smooth(x / 0.18)
        if st.get("rewrap"):                    # rewrapping: the porch and outline kept their paper
            paper_zig, paper_on = GW_PAPER_FLOOR + (1 - GW_PAPER_FLOOR) * paper_on, 0.5 + 0.5 * paper_on
        else:
            paper_zig = paper_on
        prog = _smooth((x - 0.15) / 0.55)
        pop = age - 0.74 * T                    # the bow pops when the ribbon is tied
        bow_on = 0.0 if pop < 0 else 1.0
    elif phase == "unwrap":
        prog = 1.0 - _smooth(age / 1.4)         # the ribbon unties back to the edges
        bow_on = 1.0 - _smooth(age / 0.8)
        paper_on = 1.0 - 0.5 * _smooth((age - 0.6) / 1.2)
        burst_t = age - 1.2                     # then the zigzag bursts into sparkles
        paper_zig = 1.0
    # pop: the bow overshoots, flashes white and settles
    pop_scale, flash = 1.0, 0.0
    if 0 <= pop < 1.2:
        pop_scale = min(1.0, pop / 0.18) * (1.0 + 0.45 * math.exp(-pop / 0.3) * math.cos(pop * 13.0))
        flash = math.exp(-pop / 0.28)

    # ---- paper and pattern
    pat, depth = P["pattern"], P["pattern_depth"]
    sw = P["stripe_width"]
    sheen = P["sheen"]
    sh_pos = None
    if sheen > 0:
        cyc = (clock / 11.0) % 1.0               # a soft diagonal glint, every 11 s
        span = g["xr"] - g["xl"] + 14.0
        sh_pos = g["xl"] - 7.0 + cyc * span
    zig, dots, dist, pos = g["zig"], g["dots"], g["dist"], g["pos"]
    flick = int(clock * 16)
    ring = burst_t * 10.0 if burst_t >= 0 else -1.0
    settle = 1.0 - _smooth((age - 3.6) / 1.3) if phase == "unwrap" else 1.0
    for i in idx:
        x, y = pos[i]
        if pat == "stripes":
            q = ((x + y * 1.1) / sw) % 1.0
            m = 1.0 - _smooth((abs(q - 0.5) - 0.2) / 0.16 + 0.5)         # soft-edged stripe
        elif pat == "dots":
            m = 1.0 if i in dots else 0.0
        else:
            m = 0.0
        m *= depth
        c = (paper[0] + (patc[0] - paper[0]) * m, paper[1] + (patc[1] - paper[1]) * m,
             paper[2] + (patc[2] - paper[2]) * m)
        v = lv * (paper_zig if i in zig else paper_on)
        if burst_t >= 0 and i in zig:
            dd = dist[i]
            v *= 1.0 - (1 - GW_PAPER_FLOOR) * _smooth((ring - dd) / 1.2 + 0.5)   # the ring eats the paper...
        c = (c[0] * v, c[1] * v, c[2] * v)
        if sh_pos is not None and v > 0:
            u = (x - y * 0.6) - sh_pos
            if -3.0 < u < 3.0:
                e = sheen * 0.35 * (1.0 - abs(u) / 3.0) ** 2 * v
                c = (c[0] + e, c[1] + e * 0.9, c[2] + e * 0.8)
        if burst_t >= 0 and i in zig:                                   # ...and leaves sparkles
            since = burst_t - dist[i] / 10.0
            if since >= 0:
                hsh = hash01(i, flick, seed, 7)
                e = min(1.0, 1.25 * math.exp(-since / 1.5) * (0.3 + 0.7 * hsh * hsh)) * settle
                if since < 0.15:
                    e = max(e, 1.0 - since / 0.15)                    # the ring's bright front
                pick = hash01(i, seed, 8)
                sc = WHITE if pick < 0.35 else rib if pick < 0.7 else bowc if pick < 0.85 else patc
                c = (c[0] + sc[0] * e, c[1] + sc[1] * e, c[2] + sc[2] * e)
        frame[i] = c

    # ---- ribbon
    vcov, hcov, vrank = g["vcov"], g["hcov"], g["vrank"]
    shim = P["shimmer"]
    xl, xr, ax = g["xl"], g["xr"], g["ax"]
    if prog > 0:
        for i in set(vcov) | set(hcov):
            x, y = pos[i]
            cov = 0.0
            if i in vcov:
                r, nr = vrank[i]
                cov = vcov[i] * _smooth(prog * nr * 1.05 - r)
            if i in hcov:
                fx = xl + prog * (ax - xl) + 0.6 if x <= ax else xr - prog * (xr - ax) - 0.6
                h = _smooth((fx - x) / 0.8 + 0.5) if x <= ax else _smooth((x - fx) / 0.8 + 0.5)
                cov = max(cov, hcov[i] * h)
            if cov <= 0:
                continue
            gl = glint(i, clock, 0.5 * shim, 0.03, 0.3, seed + 1, 1)[0] if shim > 0 else 0.0
            rl = min(1.0, lv * 1.15)                               # the ribbon outshines the paper
            rc = (rib[0] * rl, rib[1] * rl, rib[2] * rl)
            rc = (rc[0] + (1 - rc[0]) * gl * 0.8, rc[1] + (1 - rc[1]) * gl * 0.8, rc[2] + (1 - rc[2]) * gl * 0.6)
            frame[i] = lerp(frame[i], rc, cov)

    # ---- bow
    bow, br = g["bow"], g["bow_r"]
    knotc = lerp(bowc, WHITE, 0.45)
    if bow_on > 0:
        rise = (age * 2.5 if phase == "unwrap" else 0.0)          # unwrapping: the bow lifts away
        for i, d in bow.items():
            if pop_scale < 1.0 and d > br * pop_scale + 0.3:
                continue
            if d == 0.0:                                           # the knot: whiter, breathing
                v = 0.92 + 0.08 * math.sin(clock * 2.3 + i)
                bc = knotc
            else:                                                  # the loops: full and shimmering
                v = 0.95 - 0.1 * d / br + 0.05 * math.sin(clock * 1.7 + d * 2.0)
                bc = bowc
            gl = glint(i, clock, 1.6 * shim, 0.02, 0.22, seed + 2, 2)[0] if shim > 0 else 0.0
            v *= bow_on * min(1.0, lv * 1.15)
            c = (bc[0] * v, bc[1] * v, bc[2] * v)
            hot = min(1.0, gl * 0.85 + flash + rise * hash01(i, flick, 9) * 0.3)
            frame[i] = (c[0] + (1 - c[0]) * hot, c[1] + (1 - c[1]) * hot, c[2] + (1 - c[2]) * hot * 0.85)
    elif phase == "tie" and T > 0:
        for i in bow:                                              # no paper under the bow yet
            frame[i] = (frame[i][0] * 0.5, frame[i][1] * 0.5, frame[i][2] * 0.5)
    if flash > 0.02 and bow:                                       # the pop sends a glow down the gable
        for i in idx:
            if i in bow:
                continue
            d = math.hypot(pos[i][0] - g["ax"], pos[i][1] - g["ay"])
            if d < 4.5:
                e = flash * 0.6 * (1.0 - d / 4.5) ** 1.5
                a = frame[i]
                frame[i] = (a[0] + (1 - a[0]) * e, a[1] + (1 - a[1]) * e * 0.9, a[2] + (1 - a[2]) * e * 0.7)
    return _clamp(frame, idx)


# ====================================================================== advent calendar
AD_ORDERS = ["bottom up", "top down", "scattered"]
TODAY_PEAK = (1.0, 0.95, 0.8)    # today's door at the top of its pulse


def _ad_doors(layout, idx: list[int]) -> dict:
    """24 doors (or as many as the target has room for): lists of bulbs with a dark gap
    between neighbours. On a zigzag they are laid out row by row like a calendar's grid."""
    key = ("advent", _sig(layout, idx))
    c = geo._cache(layout)
    if key in c:
        return c[key]
    px = layout.pixels
    zig = _zigzag(layout, idx)
    doors: list[list[int]] = []          # bottom-left first, then along each row, rows upward

    def cut(seq: list[int], k: int):
        n = len(seq)
        slot = n / k
        m = max(1, min(int(slot) - 1 if slot >= 2 else 1, int(slot - 1.2 + 0.999)))
        for j in range(k):
            a = int(round(j * slot + (slot - m) / 2))
            door = seq[a:a + m]
            if door:
                doors.append(door)

    if zig:
        tot = sum(len(r) for r in zig)
        want = min(24, sum(max(1, len(r) // 2) for r in zig))
        exact = [len(r) * want / tot for r in zig]
        ks = [max(1, int(e)) for e in exact]
        while sum(ks) > want:
            j = max(range(len(ks)), key=lambda j: (ks[j] - exact[j], ks[j]))
            ks[j] -= 1
        while sum(ks) < want:
            j = max(range(len(ks)), key=lambda j: (exact[j] - ks[j], -j))
            ks[j] += 1
        for r, k in zip(zig, ks):
            if k > 0:
                cut(r, min(k, max(1, len(r) // 2)))
    else:
        seq = list(idx)
        k = min(24, max(1, len(seq) // 2)) if len(seq) >= 2 else len(seq)
        if k:
            cut(seq, k)
    zset = {i for r in zig for i in r}
    dset = {i for d in doors for i in d}
    crown = _crown(layout, idx, zig) if zig else []
    crown = [i for i in crown if i not in zset]
    cset = set(crown)
    frame_b = [i for i in idx if i not in zset and i not in dset and i not in cset]
    gaps = [i for i in zset if i not in dset]
    if crown:
        ax = sum(px[i].x for i in crown) / len(crown)
        ay = sum(px[i].y for i in crown) / len(crown)
        halo = {i: math.hypot(px[i].x - ax, px[i].y - ay) for i in frame_b}
    else:
        halo = {}
    out = {"doors": doors, "crown": crown, "frame": frame_b, "gaps": gaps, "halo": halo}
    c[key] = out
    return out


def advent_day(P: dict, today=None) -> int:
    """Today's door, 1..24: the 'day' param if set, else December's date (the 24th from
    Christmas Eve on, and outside December the whole calendar)."""
    d = int(P.get("day", 0) or 0)
    if 1 <= d <= 24:
        return d
    try:
        today = today or _today()
        if today.month == 12:
            return max(1, min(24, today.day))
    except Exception:                    # noqa: BLE001  (a broken clock never breaks the lights)
        pass
    return 24


def _ad_order(nd: int, mode: str, seed: int) -> list[int]:
    """Door indices in opening order."""
    order = list(range(nd))
    if mode == "top down":
        return order[::-1]
    if mode == "scattered":
        rnd = random.Random(seed * 131 + 7)
        rnd.shuffle(order)
    return order


@frame_effect("advent", "Advent calendar", [
    Param("day", "int", 0, "Day (0 = today)", 0, 24, 1,
          help="which door is today's: 0 follows the date (1-24 December; the 24th from Christmas "
               "Eve on and outside December, so the full calendar shows)"),
    Param("order", "choice", "bottom up", "Door order", options=AD_ORDERS,
          help="bottom up: the calendar fills toward the star, like the tree; scattered: like a real "
               "advent calendar"),
    Param("open_color", "color", "#ffa018", "Open doors", help="warm gold"),
    Param("open_colors", "colors", [], "Open doors (alternating)",
          help="optional: open doors take these colours in turn (e.g. red and green) instead of the one above"),
    Param("level", "number", 0.8, "Open door brightness", 0.05, 1, 0.01),
    Param("today_color", "color", "#ffd070", "Today's door", help="pulses and sparkles"),
    Param("pulse", "number", 2.6, "Pulse period (s)", 0.5, 10, 0.1),
    Param("sparkle", "number", 1.0, "Sparkle", 0, 3, 0.05, help="glitter on today's door (and on the 24th, everywhere)"),
    Param("closed_color", "color", "#ffc48a", "Closed doors", help="dim warm white placeholders"),
    Param("closed_level", "number", 0.25, "Closed door brightness", 0, 0.6, 0.01,
          help="keep it low but not too low: very dark reads as off"),
    Param("frame_color", "color", "#d00000", "Frame colour",
          help="the rest of the target (e.g. the gable outline and porch) frames the calendar"),
    Param("frame_level", "number", 0.45, "Frame brightness", 0, 1, 0.01, help="0 = frame dark"),
    Param("star_color", "color", "#ffe090", "Star colour", help="the gable apex above the calendar"),
    Param("intro", "number", 6, "Opening intro (s)", 0, 30, 0.5,
          help="when the scene starts the doors open one by one up to today, then today's pops; 0 = no intro"),
    Param("seed", "int", 24, "Seed", 0, 9999, 1, help="door order when scattered, and the sparkle pattern"),
], "An advent calendar on the zigzag: 24 doors in rows like a real calendar. On day D of "
   "December the first D doors glow warm gold, today's pulses and sparkles, and the rest wait as "
   "dim warm placeholders; the outline frames it and the apex is its star. On the 24th everything "
   "shimmers. Follows the date (or set the day). Without the zigzag in the target the doors run "
   "along the roofline. Target 'Whole front gable' (plus the porch for more frame).", group="Scenes")
def advent(t, ctx, P, st):
    frame, idx = _target(ctx)
    if not idx:
        return frame
    L = ctx.layout
    g = _ad_doors(L, idx)
    doors = g["doors"]
    nd = len(doors)
    clock = ctx.clock
    seed = P["seed"]
    day = advent_day(P)
    full = day >= 24
    if "start" not in st or clock < st["start"] - 1e-6:
        st["start"] = clock
    since = clock - st["start"]
    D = P["intro"]
    today_k = max(1, min(nd, int(math.ceil(day * nd / 24.0)))) if nd else 0   # doors open, incl. today
    order = _ad_order(nd, P["order"], seed)
    oc, lv, tc = P["open_color"], P["level"], P["today_color"]
    ocs = P.get("open_colors") or [oc]
    cc, cl = P["closed_color"], P["closed_level"]
    spk = P["sparkle"]
    per = P["pulse"]
    pulse = 0.5 + 0.5 * math.sin(clock * TAU / per - math.pi / 2)
    # intro: doors open one after another, then today's door pops
    intro = since < D if D > 0 else False
    step = (0.7 * D) / max(1, today_k - 1) if D > 0 else 0.0
    pop_at = 0.8 * D if D > 0 else -1.0
    pop = since - pop_at
    for rank, k in enumerate(order[:nd]):
        door = doors[k]
        n_open = rank + 1
        if n_open < today_k:                                   # an open door
            oc = ocs[k % len(ocs)]
            if intro:
                a = since - rank * step
                o = _smooth(a / 0.35)
                flash = math.exp(-a / 0.3) if a >= 0 else 0.0
            else:
                o, flash = 1.0, 0.0
            for j, i in enumerate(door):
                w = 0.9 + 0.1 * math.sin(clock * (0.7 + 0.3 * hash01(i, seed)) + i)
                c = (oc[0] * lv * w, oc[1] * lv * w, oc[2] * lv * w)
                if full and spk > 0:
                    gl = glint(i, clock, 0.35 * spk, 0.03, 0.4, seed + 1, 2)[0]
                    wave = 0.5 + 0.5 * math.sin(clock * 2.2 - rank * 0.45)
                    c = lerp(c, (oc[0] * lv, oc[1] * lv, oc[2] * lv), wave * 0.3)
                    c = (c[0] + (1 - c[0]) * gl * 0.8, c[1] + (1 - c[1]) * gl * 0.8, c[2] + (1 - c[2]) * gl * 0.6)
                closed = (cc[0] * cl, cc[1] * cl, cc[2] * cl)
                c = lerp(closed, c, o)
                if flash > 0:
                    c = (c[0] + (1 - c[0]) * flash * 0.6, c[1] + (1 - c[1]) * flash * 0.55, c[2] + (1 - c[2]) * flash * 0.4)
                frame[i] = c
        elif n_open == today_k:                                # today's door
            if intro and pop < 0:
                for i in door:
                    frame[i] = (cc[0] * cl, cc[1] * cl, cc[2] * cl)
                continue
            fl = math.exp(-pop / 0.35) if 0 <= pop < 3 else 0.0
            v = 0.35 + 0.65 * pulse                            # a deep, slow breath...
            top = lerp(tc, TODAY_PEAK, _smooth((pulse - 0.6) / 0.4))   # ...glowing near white at its peak
            for i in door:
                gl = glint(i, clock, 1.4 * spk, 0.02, 0.2, seed + 2, 2)[0] if spk > 0 else 0.0
                c = (top[0] * v, top[1] * v, top[2] * v)
                hot = min(1.0, gl * 0.9 + fl)
                frame[i] = (c[0] + (1 - c[0]) * hot, c[1] + (1 - c[1]) * hot, c[2] + (1 - c[2]) * hot * 0.8)
        else:                                                  # still closed
            for i in door:
                w = cl * (0.9 + 0.1 * math.sin(clock * 0.5 + i * 1.7))
                frame[i] = (cc[0] * w, cc[1] * w, cc[2] * w)
    # the frame (outline, porch...) and the star on top
    fc, fl_ = P["frame_color"], P["frame_level"]
    sc = P["star_color"]
    halo = g["halo"]
    for i in g["frame"]:
        w = fl_ * (0.85 + 0.15 * math.sin(clock * 0.6 + i * 0.9))
        c = (fc[0] * w, fc[1] * w, fc[2] * w)
        if full and spk > 0:
            gl = glint(i, clock, 0.25 * spk, 0.03, 0.35, seed + 3, 1)[0]
            c = (c[0] + (1 - c[0]) * gl * 0.7, c[1] + (0.8 - c[1]) * gl * 0.7, c[2] + (0.5 - c[2]) * gl * 0.5)
        d = halo.get(i, 99.0)
        if d < 2.6:                                            # the outline next to the star catches its glow
            c = lerp(c, (sc[0] * 0.5, sc[1] * 0.5, sc[2] * 0.5), (1 - d / 2.6) * (0.9 if full else 0.5))
        frame[i] = c
    for i in g["crown"]:
        if full:
            v = 0.85 + 0.15 * pulse
            gl = glint(i, clock, 1.0 * max(spk, 0.3), 0.02, 0.25, seed + 4, 2)[0]
        else:
            v = 0.35 + 0.1 * pulse * (day / 24.0)              # the star brightens as Christmas nears
            gl = 0.0
        if intro and since < D:
            v *= _smooth(since / max(0.5, D * 0.3))
        c = (sc[0] * v, sc[1] * v, sc[2] * v)
        frame[i] = (c[0] + (1 - c[0]) * gl, c[1] + (1 - c[1]) * gl, c[2] + (1 - c[2]) * gl * 0.8)
    return _clamp(frame, idx)
