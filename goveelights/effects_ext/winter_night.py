"""Winter-night show-stoppers for the house.

    sleigh     Santa's sleigh: a red-and-gold sleigh led by a line of reindeer (Rudolph's
               red nose in front) flies along the roofline, up and over both gables,
               leaving a glittering trail of gold and white sparkles that linger and
               fade, every N seconds, alternating direction. Quiet stars in between.
    icicles    Dripping icicles: ice-blue icicle glints hang along the eaves; drops of
               light form, slide down the gable slopes, drip off the low ends and fall
               (real 2D free fall) onto the roofline below, where they splash.
    ball_drop  New Year's ball drop: a glowing crystal ball waits at the gable apex,
               descends the centre of the zigzag over the last N seconds with the
               seconds counted down as big digits, then BOOM: the house bursts gold and
               white, sparks fly and "HAPPY NEW YEAR" scrolls across the zigzag.
               Loops for previews, or lands exactly at local midnight (or any HH:MM).

All three are @frame_effect generators: memory lives in the layer's state dict,
randomness comes from a random.Random kept there (deterministic for a given seed and
frame sequence), physics runs in capped sub-steps (a stalled frame never teleports
anything) and particle counts are bounded so they can run all night.
Coordinates are the layout's (bulb-spacing units, y up); moving lights are drawn
anti-aliased (geometry.splat / splat_path) so they glide between bulbs.
"""
from __future__ import annotations

import bisect
import datetime as _dt
import math
import random
import time as _time

from .. import font
from .. import geometry as geo
from ..color import BLACK, hash01, lerp
from ..effects import EFFECTS, Param, frame_effect
from ..fx import Ctx
from ..sparkle import glint

MAX_DT = 0.1          # largest frame step simulated (s): a stall never makes anything jump
STEP = 1 / 40         # physics sub-step (s)
WHITE = (1.0, 1.0, 1.0)
GOLD = (1.0, 0.62, 0.12)
WARM_WHITE = (1.0, 0.86, 0.6)

_now = _time.time      # wall clock for the ball drop's "clock" mode (patched by tests)


# ====================================================================== shared helpers
def _frame_dt(ctx) -> float:
    d = ctx.dt or 0.0
    if not (d > 0) or d != d:                       # 0, negative or NaN
        return 0.0
    return min(MAX_DT, d)


def _substeps(dt: float) -> tuple[float, int]:
    if dt <= 0:
        return 0.0, 0
    n = max(1, math.ceil(dt / STEP - 1e-9))
    return dt / n, n


def _sig(ctx) -> tuple:
    L, idx = ctx.layout, ctx.idx
    return (id(L), len(L.pixels), len(idx), hash(tuple(idx)))


def _clamp(frame: list, idx) -> list:
    for i in idx:
        r, g, b = frame[i]
        if r > 1.0 or g > 1.0 or b > 1.0 or r < 0.0 or g < 0.0 or b < 0.0:
            frame[i] = (min(1.0, max(0.0, r)), min(1.0, max(0.0, g)), min(1.0, max(0.0, b)))
    return frame


def _add(frame, i, c, w):
    a = frame[i]
    frame[i] = (a[0] + c[0] * w, a[1] + c[1] * w, a[2] + c[2] * w)


def _smooth(x: float) -> float:
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    return x * x * (3 - 2 * x)


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
    out = []
    for g in groups:
        if len(g) >= 2:
            pth = geo.path(layout, g)
            if pth.length > 1e-6:
                out.append(pth)
    return out


def _is_fill(layout, pth) -> bool:
    """True for a zigzag fill (the Peak's rows) rather than a roofline."""
    return geo._rows_by_reversal(layout.pixels, pth.order) is not None


def _roof_tracks(layout, idx) -> list:
    tracks = _tracks(layout, idx)
    roof = [t for t in tracks if not _is_fill(layout, t)]
    return roof or tracks


def _segment(frame, pth, s0: float, s1: float, col, gain: float = 1.0):
    """Light the stretch [s0, s1] of a path, each bulb by how much of its own footprint
    (half a bulb either side) the stretch covers: crisp, anti-aliased ends."""
    if s1 < s0:
        s0, s1 = s1, s0
    k, _ = pth.locate(s0)
    S, order = pth.s, pth.order
    j = max(0, k - 1)
    while j < len(order) and S[j] - 0.5 <= s1:
        cov = min(s1, S[j] + 0.5) - max(s0, S[j] - 0.5)
        if cov > 0:
            _add(frame, order[j], col, min(1.0, cov) * gain)
        j += 1


def _stars(frame, idx, clock, density, seed, colour=(0.75, 0.85, 1.0)):
    """A quiet starry sky: sparse, slow cool-white twinkles."""
    if density <= 0:
        return
    rate = 0.05 * density
    for i in idx:
        g, _ = glint(i, clock, rate, 0.6, 0.9, seed, 1)
        if g > 0.02:
            w = 0.38 * g
            _add(frame, i, colour, w)


# ====================================================================== sleigh
DIRECTIONS = ["alternate", "north to south", "south to north"]
SL_MAX_SPARKS = 420
SL_MAX_DUST = 160
SL_SPARK_MAX = 0.6    # trail sparkles never outshine the sleigh


def _sl_setup(ctx, st):
    key = _sig(ctx)
    if st.get("key") == key:
        return
    L, idx = ctx.layout, ctx.idx
    st["key"] = key
    st["allowed"] = set(idx)
    tracks = _roof_tracks(L, idx)
    st["path"] = max(tracks, key=lambda t: t.length) if tracks else None
    st["sparks"], st["dust"] = [], []
    st["pass"] = None
    # stardust only makes sense if some target bulb lies below the roofline to catch it
    pth = st["path"]
    st["dust_ok"] = False
    if pth is not None:
        px = L.pixels
        on = set(pth.order)
        others = [px[i] for i in idx if i not in on]
        pts = [pth.point(s) for s in pth.s]
        st["dust_ok"] = any(abs(p.x - x) <= 2.0 and p.y < y - 1.0 for p in others for x, y in pts)


def _sl_train(P) -> tuple[list, float, float]:
    """The reindeer as [(offset behind the head, kind)] front to back, and where the sleigh
    starts and ends behind the head, in bulbs."""
    out = []
    gap = 2.0                                       # a dark bulb between reindeer: each reads as one
    for k in range(int(P["reindeer"])):
        out.append((k * gap, "nose" if k == 0 else "deer"))
    sl0 = int(P["reindeer"]) * gap + 0.6
    return out, sl0, sl0 + P["sleigh_length"]


@frame_effect("sleigh", "Santa's sleigh", [
    Param("every", "number", 24, "Pass every (s)", 6, 600, 1,
          help="seconds from the start of one flight to the start of the next"),
    Param("speed", "number", 13, "Flying speed (bulbs/s)", 3, 40, 0.5,
          help="how fast the sleigh flies along the roofline (a bit slower climbing, faster diving)"),
    Param("direction", "choice", "alternate", "Direction", options=DIRECTIONS,
          help="alternate: back and forth across the house, each pass the other way"),
    Param("reindeer", "int", 5, "Reindeer", 0, 9, 1,
          help="dots leading the sleigh; the first one is Rudolph with his red nose"),
    Param("sleigh_length", "number", 3.5, "Sleigh length (bulbs)", 2, 6, 0.5),
    Param("sleigh_color", "color", "#ff0a0a", "Sleigh colour"),
    Param("trim_color", "color", "#ffb020", "Sleigh trim (gold)", help="the curled front and the back of the sleigh"),
    Param("deer_color", "color", "#ff8a30", "Reindeer colour", help="warm amber reads best"),
    Param("nose_color", "color", "#ff0000", "Rudolph's nose"),
    Param("sparkle_colors", "colors", ["#ffc030", "#ffe080", "#ffffff"], "Trail sparkle colours",
          help="white sparkles are scattered half as often as the others, keeping the trail golden"),
    Param("trail", "number", 2.6, "Trail lingers (s)", 0.3, 8, 0.1,
          help="how long the glittering sparkles behind the sleigh last"),
    Param("sparkle", "number", 1.0, "Trail density", 0, 3, 0.05,
          help="how many sparkles the sleigh scatters"),
    Param("dust", "number", 0.4, "Falling stardust", 0, 1, 0.05,
          help="some sparkles drift down off the roof (onto the zigzag / porch if they are in the target)"),
    Param("jingle", "number", 0.35, "Jingle shimmer", 0, 1, 0.05,
          help="a subtle golden shimmer on the bulbs near the sleigh as it passes"),
    Param("stars", "number", 0.5, "Background twinkle", 0, 2, 0.05,
          help="quiet starry twinkles between (and during) passes; 0 = dark"),
    Param("background", "color", "#000000", "Sky colour", help="behind everything (keep it dark so the sleigh reads)"),
    Param("seed", "int", 12, "Seed", 0, 9999, 1),
], "Santa's sleigh flies across the house: Rudolph's red nose leads a line of reindeer and a "
   "red-and-gold sleigh up and over both gables along the roofline, scattering a glittering "
   "trail of gold and white sparkles that linger and fade. Every N seconds, back and forth, "
   "with quiet stars twinkling in between. Target the Eaves (or the whole house).", group="Scenes")
def sleigh(t, ctx, P, st):
    L, idx = ctx.layout, ctx.idx
    n = len(L.pixels)
    sky = P["background"]
    frame = [BLACK] * n
    if not idx:
        return frame
    for i in idx:
        frame[i] = sky
    _sl_setup(ctx, st)
    if st.get("seed") != P["seed"] or "rng" not in st:
        st["seed"], st["rng"] = P["seed"], random.Random(P["seed"] * 7907 + 3)
    rng = st["rng"]
    clock = ctx.clock
    pth = st["path"]
    allowed = st["allowed"]
    _stars(frame, idx, clock, P["stars"], P["seed"])
    if pth is None:
        return _clamp(frame, idx)
    Lp = pth.length
    train, sl0, sl1 = _sl_train(P)
    tail_off = sl1

    # ---------------------------------------------------------- scheduling
    every = max(1.0, P["every"])
    nxt = st.get("next")
    if nxt is None or nxt > clock + every + 1 or nxt < clock - 3 * every:
        nxt = st["next"] = clock + 1.0              # first flight soon after starting
    ps = st["pass"]
    if ps is None and clock >= nxt:
        count = st.get("count", 0)
        mode = P["direction"]
        d = 1 if mode == "north to south" else -1 if mode == "south to north" else (1 if count % 2 == 0 else -1)
        ps = st["pass"] = {"u": -0.5, "dir": d, "t0": clock}
        st["count"] = count + 1
        st["next"] = nxt + every
        while st["next"] <= clock:                 # a long stall: don't queue up passes
            st["next"] += every

    # ---------------------------------------------------------- physics
    dt, steps = _substeps(_frame_dt(ctx))
    sc = P["sparkle_colors"] or [GOLD]
    # gold-weighted: near-white sparkles are drawn at half the rate, so the trail stays golden
    scw, acc = [], 0.0
    for c in sc:
        acc += 0.5 if min(c) > 0.9 else 1.0
        scw.append(acc)
    spark_rate = 26.0 * P["sparkle"] * (P["speed"] / 13.0) ** 0.5
    life0 = P["trail"]
    dust_p = P["dust"] * 0.35 if st["dust_ok"] else 0.0
    for _ in range(steps):
        if ps is not None:
            d = ps["dir"]
            s_head = ps["u"] if d > 0 else Lp - ps["u"]
            sl = max(-1.0, min(1.0, pth.slope(min(max(s_head, 0.0), Lp)) * d))
            ps["u"] += P["speed"] * (1.0 - 0.35 * sl) * dt
            # scatter sparkles just behind the sleigh
            lam = spark_rate * dt
            k = int(lam) + (1 if rng.random() < lam - int(lam) else 0)
            for _ in range(k):
                u = ps["u"] - tail_off - 1.5 - rng.random() * 1.8   # a dark bulb before the trail
                s = u if d > 0 else Lp - u
                if 0 <= s <= Lp:
                    col = sc[min(len(sc) - 1, bisect.bisect_left(scw, rng.random() * acc))]
                    if rng.random() < dust_p and len(st["dust"]) < SL_MAX_DUST:
                        x, y = pth.point(s)
                        st["dust"].append([x, y, (rng.random() - 0.5) * 0.8, 0.3 * rng.random(), 0.0,
                                           life0 * (0.8 + 0.8 * rng.random()), col, rng.randrange(1 << 20)])
                    else:
                        st["sparks"].append([s, 0.0, life0 * (0.45 + 0.75 * rng.random()), col,
                                             rng.randrange(1 << 20)])
            if ps["u"] - tail_off > Lp + 1.0:
                ps = st["pass"] = None
        for s in st["sparks"]:
            s[1] += dt
        st["sparks"] = [s for s in st["sparks"] if s[1] < s[2]]
        for p in st["dust"]:
            p[4] += dt
            p[3] -= 2.2 * dt                        # gentle gravity, heavy air drag
            p[3] *= math.exp(-1.6 * dt)
            p[2] *= math.exp(-1.0 * dt)
            p[0] += p[2] * dt
            p[1] += p[3] * dt
        st["dust"] = [p for p in st["dust"] if p[4] < p[5]]
    if len(st["sparks"]) > SL_MAX_SPARKS:
        st["sparks"] = st["sparks"][-SL_MAX_SPARKS:]

    # ---------------------------------------------------------- render
    tw = int(clock * 14)
    trail = [BLACK] * n if st["sparks"] else None
    for s in st["sparks"]:
        u = s[1] / s[2]
        # capped below the sleigh so the sleigh, not its tail, is the brightest thing up there
        b = (_smooth(s[1] / 0.3) * (1.0 - u) ** 1.4) * (0.45 + 0.55 * hash01(s[4], tw)) * SL_SPARK_MAX
        if hash01(s[4], tw, 3) < 0.08:              # an occasional bright glint
            b = min(SL_SPARK_MAX, b * 1.8 + 0.12)
        c = s[3]
        geo.splat_path(trail, pth, s[0], (c[0] * b, c[1] * b, c[2] * b), 0.9)
    if trail is not None:                           # overlapping sparkles: cap the sum per bulb
        for i in pth.order:
            c = trail[i]
            m = max(c)
            if m > 0:
                k = SL_SPARK_MAX / m if m > SL_SPARK_MAX else 1.0
                _add(frame, i, c, k)
    for p in st["dust"]:
        u = p[4] / p[5]
        b = 0.8 * (1.0 - u) ** 1.2 * (0.5 + 0.5 * hash01(p[7], tw))
        c = p[6]
        geo.splat(frame, L, p[0], p[1], (c[0] * b, c[1] * b, c[2] * b), 1.0, allowed)

    if ps is not None:
        d, uh = ps["dir"], ps["u"]

        def S(off):
            u = uh - off
            return u if d > 0 else Lp - u

        # jingle: a soft golden shimmer around the sleigh
        sx, sy = pth.point(min(max(S((sl0 + sl1) / 2), 0.0), Lp))
        jg = P["jingle"]
        if jg > 0:
            for i, dd in geo.near(L, sx, sy, 11.0):
                if i in allowed:
                    w = (1.0 - dd / 11.0) ** 2
                    g, _ = glint(i, clock, 2.5, 0.02, 0.18, P["seed"] + 5, 1)
                    _add(frame, i, WARM_WHITE, jg * w * (0.06 + 0.5 * g))
        # reins: a faint golden line from the lead reindeer to the sleigh
        if train:
            _segment(frame, pth, S(0.0), S(sl0), P["trim_color"], 0.10)
        # reindeer, Rudolph first (his nose glows and pulses)
        for off, kind in train:
            s = S(off)
            if kind == "nose":
                pulse = 0.8 + 0.2 * math.sin(clock * 9.0)
                c = P["nose_color"]
                geo.splat_path(frame, pth, s, (c[0] * pulse, c[1] * pulse, c[2] * pulse), 1.15)
            else:
                bob = 0.72 + 0.12 * math.sin(clock * 7.0 + off * 1.7)
                c = P["deer_color"]
                geo.splat_path(frame, pth, s, (c[0] * bob, c[1] * bob, c[2] * bob), 0.9)
        # the sleigh: gold curled front, red body, gold back edge
        tl = P["sleigh_length"]
        _segment(frame, pth, S(sl0), S(sl0 + 0.9), P["trim_color"], 1.0)
        _segment(frame, pth, S(sl0 + 0.9), S(sl1 - 0.45), P["sleigh_color"], 1.0)
        _segment(frame, pth, S(sl0 + 0.9), S(sl1 - 0.45), WARM_WHITE, 0.22)   # lift: reads brightest
        _segment(frame, pth, S(sl1 - 0.45), S(sl1), P["trim_color"], 0.8)
        # a white-hot glint riding on the sleigh (light catching the runners)
        gl = 0.35 + 0.25 * math.sin(clock * 11.0)
        geo.splat_path(frame, pth, S(sl0 + tl * 0.5), (gl, gl * 0.9, gl * 0.7), 0.8)
    return _clamp(frame, idx)


# ====================================================================== icicles
IC_MAX_DROPS = 60
FORM, SLIDE, FALL, GLINT = 0, 1, 2, 3
GLINT_T = 0.6         # a drop with nothing below to fall onto fades out as a glint (s)
FLAT = 0.18           # |slope| below this counts as a flat eave: drops drip straight off it
SLIDE_G = 10.0        # how strongly a drop is pulled down a slope (bulbs/s² per unit slope)
SLIDE_DRAG = 1.4      # wet friction on the roof (1/s): drops slide calmly


def _ic_setup(ctx, st):
    key = _sig(ctx)
    if st.get("key") == key:
        return
    L, idx = ctx.layout, ctx.idx
    px = L.pixels
    st["key"] = key
    st["allowed"] = set(idx)
    tracks = _roof_tracks(L, idx)
    tracks = [t for t in tracks if not _is_fill(L, t)]
    st["tracks"] = tracks
    on_track = {i for t in tracks for i in t.order}
    st["frost"] = [i for i in idx if i not in on_track]    # zigzag etc.: frost, no drops
    st["eave"] = [i for i in idx if i in on_track]
    # drop sites: (track, s) for every bulb on a roofline track
    st["sites"] = [(k, t.s[j]) for k, t in enumerate(tracks) for j in range(len(t.order))]
    # where falling drops land: target bulbs binned by column
    bins: dict[int, list] = {}
    for i in idx:
        bins.setdefault(math.floor(px[i].x), []).append(i)
    st["bins"] = bins
    st["floor"] = (min(px[i].y for i in idx) - 2.0) if idx else 0.0
    # where drops are most worth watching: slopes (they slide), then eaves with a roofline
    # below to splash on; the lowest line (nothing below) drips least
    acc, cum = 0.0, []
    for k, j in st["sites"]:
        pth = tracks[k]
        x, y = pth.point(j)
        if abs(pth.slope(j)) >= FLAT:
            w = 2.0
        elif _ic_land(st, px, x, y - 0.3) is not None:
            w = 1.4
        else:
            w = 0.1                                 # nothing below to splash on: rarely
        acc += w
        cum.append(acc)
    st["cum"] = cum
    st["drops"], st["splash"], st["wet"] = [], [], {}
    st["next"] = None


def _ic_land(st, px, x, y):
    """Highest target bulb under (x, y) within reach, or None."""
    best = None
    k = math.floor(x)
    for j in (k - 1, k, k + 1):
        for i in st["bins"].get(j, ()):
            p = px[i]
            if p.y < y and abs(p.x - x) <= 0.75 and (best is None or p.y > px[best].y):
                best = i
    return best


@frame_effect("icicles", "Dripping icicles", [
    Param("rate", "number", 36, "Drips per minute", 2, 240, 1,
          help="how often a drop of light forms somewhere on the eaves"),
    Param("colors", "colors", ["#40a8ff", "#1460ff", "#9ad8ff"], "Icicle colours",
          help="the cool tints the icicles hang in"),
    Param("drop_color", "color", "#e6f6ff", "Drop colour"),
    Param("ice", "number", 0.55, "Icicle glow", 0, 1, 0.01,
          help="how bright the icicles hang along the eaves between drops"),
    Param("frost", "number", 0.3, "Frost glow (zigzag)", 0, 1, 0.01,
          help="glow of target bulbs that are not eaves (e.g. the zigzag) — frosty, with glints"),
    Param("glint", "number", 0.6, "Moonlight glint", 0, 1, 0.05,
          help="a slow sheen of light sliding along the icicles, as if catching the moon"),
    Param("sparkle", "number", 0.5, "Sparkle", 0, 1, 0.05,
          help="random ice crystals flashing white"),
    Param("gravity", "number", 18, "Gravity", 4, 40, 0.5,
          help="how fast drops fall once they drip off the roof (bulbs/s²)"),
    Param("form_time", "number", 1.8, "Drop forms over (s)", 0.3, 6, 0.1,
          help="how long a drop takes to swell before it lets go"),
    Param("splash", "number", 0.8, "Splash", 0, 1, 0.05,
          help="how big the splash is when a drop lands on the roofline below"),
    Param("seed", "int", 21, "Seed", 0, 9999, 1),
], "Ice-blue icicles hang along the flat and sloped eaves, glinting in the moonlight. Drops of "
   "light swell at random bulbs, slide down the gable slopes, drip off the low ends and eave "
   "edges, fall under gravity onto the roofline below and splash. Calm and continuous. "
   "Target the whole front (or the whole house).", group="Weather")
def icicles(t, ctx, P, st):
    L, idx = ctx.layout, ctx.idx
    n = len(L.pixels)
    frame = [BLACK] * n
    if not idx:
        return frame
    _ic_setup(ctx, st)
    if st.get("seed") != P["seed"] or "rng" not in st:
        st["seed"], st["rng"] = P["seed"], random.Random(P["seed"] * 6007 + 11)
    rng = st["rng"]
    px = L.pixels
    clock = ctx.clock
    seed = P["seed"]
    tracks = st["tracks"]
    allowed = st["allowed"]
    cols = P["colors"] or [(0.5, 0.8, 1.0)]

    # ---------------------------------------------------------- icicles + frost (base)
    ice, gl, sp = P["ice"], P["glint"], P["sparkle"]
    # the moonlight sheen: a soft band sliding across the house every ~11 s
    xs0, xs1 = L.minx - 6, L.maxx + 6
    per = 11.0
    ph = (clock / per) % 1.0
    sheen_x = xs1 - (xs1 - xs0) * ph
    for i in st["eave"]:
        p = px[i]
        h = hash01(i, seed, 1)
        c = cols[int(h * 977) % len(cols)]
        lvl = ice * (0.45 + 0.55 * hash01(i, seed, 2))
        lvl *= 0.85 + 0.15 * math.sin(clock * (0.6 + 0.5 * h) + h * 40)
        if gl > 0:
            dx = (p.x - sheen_x) / 3.2
            if dx * dx < 9:
                lvl += gl * 0.55 * math.exp(-dx * dx)
        frame[i] = (c[0] * lvl, c[1] * lvl, c[2] * lvl)
        if sp > 0:
            g, _ = glint(i, clock, 0.05 * sp, 0.03, 0.35, seed, 1)
            if g > 0.01:
                _add(frame, i, WHITE, 0.8 * g)
    fr = P["frost"]
    for i in st["frost"]:
        p = px[i]
        h = hash01(i, seed, 4)
        c = cols[int(h * 613) % len(cols)]
        lvl = fr * (0.5 + 0.5 * math.sin(clock * (0.35 + 0.3 * h) + h * 50) ** 2)
        if gl > 0:
            dx = (p.x - sheen_x) / 3.2
            if dx * dx < 9:
                lvl += gl * 0.25 * math.exp(-dx * dx)
        frame[i] = (c[0] * lvl, c[1] * lvl, c[2] * lvl)
        if sp > 0:
            g, _ = glint(i, clock, 0.03 * sp, 0.03, 0.3, seed + 1, 1)
            if g > 0.01:
                _add(frame, i, WHITE, 0.65 * g)

    # ---------------------------------------------------------- spawn
    sites = st["sites"]
    if st["next"] is None or st["next"] > clock + 120 or st["next"] < clock - 10:
        st["next"] = clock + 0.3
    rate = max(0.01, P["rate"]) / 60.0
    while sites and clock >= st["next"]:
        if len(st["drops"]) < IC_MAX_DROPS:
            k, s = sites[min(len(sites) - 1, bisect.bisect_left(st["cum"], rng.random() * st["cum"][-1]))]
            st["drops"].append({"m": FORM, "k": k, "s": s, "v": 0.0, "age": 0.0,
                                "T": P["form_time"] * (0.75 + 0.5 * rng.random()),
                                "x": 0.0, "y": 0.0, "vx": 0.0, "vy": 0.0, "land": None, "id": rng.randrange(1 << 20)})
        st["next"] += -math.log(1.0 - rng.random() * 0.999) / rate

    # ---------------------------------------------------------- physics
    dt, steps = _substeps(_frame_dt(ctx))
    g = P["gravity"]
    wet = st["wet"]
    for _ in range(steps):
        alive = []
        for d in st["drops"]:
            d["age"] += dt
            if d["m"] == FORM:
                if d["age"] >= d["T"]:
                    pth = tracks[d["k"]]
                    sl = pth.slope(d["s"])
                    if abs(sl) < FLAT:
                        x, y = pth.point(d["s"])
                        if _ic_land(st, px, x, y - 0.3) is None:
                            d["m"], d["age"] = GLINT, 0.0    # nothing below: an invisible fall
                        else:
                            _ic_drip(st, d, pth, 0.0, px, g)
                    else:
                        d["m"], d["age"] = SLIDE, 0.0
                alive.append(d)
            elif d["m"] == SLIDE:
                pth = tracks[d["k"]]
                sl = pth.slope(min(max(d["s"], 0.0), pth.length))
                d["v"] += (-SLIDE_G * sl - SLIDE_DRAG * d["v"]) * dt
                d["s"] += d["v"] * dt
                k_, f_ = pth.locate(d["s"])
                j = pth.order[k_ + (1 if f_ > 0.5 and k_ + 1 < len(pth.order) else 0)]
                wet[j] = max(wet.get(j, 0.0), 0.55)
                off = d["s"] <= 0.0 or d["s"] >= pth.length
                d["s"] = min(max(d["s"], 0.0), pth.length)
                sl2 = pth.slope(d["s"])
                # leaves the roof at the low end: where it flattens out or the line ends
                if off or abs(sl2) < FLAT or d["age"] > 10.0 or (sl2 * d["v"] > 0 and abs(d["v"]) < 0.05):
                    _ic_drip(st, d, pth, d["v"], px, g)
                alive.append(d)
            elif d["m"] == GLINT:
                if d["age"] < GLINT_T:
                    alive.append(d)
            else:                                   # FALL
                d["vy"] -= g * dt
                d["x"] += d["vx"] * dt
                d["y"] += d["vy"] * dt
                land = d["land"]
                if land is not None and d["y"] <= px[land].y:
                    _ic_splash(st, rng, land, d, P)
                    continue
                if d["y"] < st["floor"]:
                    continue
                alive.append(d)
        st["drops"] = alive
        # splash droplets: tiny bounces that fall back down
        for s in st["splash"]:
            s["age"] += dt
            for b in s["bits"]:
                b[3] -= g * 0.8 * dt
                b[0] += b[2] * dt
                b[1] += b[3] * dt
        st["splash"] = [s for s in st["splash"] if s["age"] < 0.9]
    if dt > 0:
        kd = math.exp(-dt * steps / 0.45)
        for j in list(wet):
            v = wet[j] * kd
            if v < 0.01:
                del wet[j]
            else:
                wet[j] = v

    # ---------------------------------------------------------- render drops
    dc = P["drop_color"]
    for j, v in wet.items():
        if j in allowed:
            _add(frame, j, dc, v * 0.5)
    for d in st["drops"]:
        if d["m"] == FORM:
            u = min(1.0, d["age"] / d["T"])
            b = 0.15 + 0.85 * u * u
            if u > 0.85:                            # a little shiver just before it lets go
                b *= 0.9 + 0.1 * math.sin(d["age"] * 40)
            geo.splat_path(frame, tracks[d["k"]], d["s"], (dc[0] * b, dc[1] * b, dc[2] * b), 1.2)
        elif d["m"] == SLIDE:
            pth = tracks[d["k"]]
            geo.splat_path(frame, pth, d["s"], dc, 1.2)
            back = -1.0 if d["v"] > 0 else 1.0       # a glistening tail uphill
            for m, w in ((0.7, 0.45), (1.4, 0.25), (2.1, 0.12)):
                geo.splat_path(frame, pth, d["s"] + back * m, (dc[0] * w, dc[1] * w, dc[2] * w), 1.0)
        elif d["m"] == GLINT:                       # lets go as a flash of light, then fades
            u = min(1.0, d["age"] / GLINT_T)
            b = (1.0 - u) ** 1.5
            geo.splat_path(frame, tracks[d["k"]], d["s"], (dc[0] * b, dc[1] * b, dc[2] * b), 1.2)
        else:
            geo.splat(frame, L, d["x"], d["y"], dc, 1.2, allowed)
            geo.splat(frame, L, d["x"] - d["vx"] * 0.04, d["y"] - d["vy"] * 0.04,
                      (dc[0] * 0.4, dc[1] * 0.4, dc[2] * 0.4), 1.0, allowed)
    for s in st["splash"]:
        a = s["age"]
        p = px[s["i"]]
        e = s["e"]
        flash = math.exp(-a / 0.12) * e
        geo.splat(frame, L, p.x, p.y, (dc[0] * flash, dc[1] * flash, dc[2] * flash), 1.2, allowed)
        # a ring of light running out along the line either side
        r = 0.6 + 5.0 * a
        w = 0.6 * e * max(0.0, 1.0 - a / 0.6)
        if w > 0:
            for i, dd in geo.near(L, p.x, p.y, r + 0.9):
                if i in allowed:
                    q = 1.0 - abs(dd - r) / 0.9
                    if q > 0:
                        _add(frame, i, cols[0], w * q)
        for b in s["bits"]:
            if a < 0.8:
                bb = 0.55 * e * (1.0 - a / 0.8)
                geo.splat(frame, L, b[0], b[1], (dc[0] * bb, dc[1] * bb, dc[2] * bb), 0.9, allowed)
    return _clamp(frame, idx)


def _ic_drip(st, d, pth, v, px, g=18.0):
    """The drop lets go of the roof: 2D free fall from where it is, keeping its speed."""
    s = min(max(d["s"], 0.0), pth.length)
    x, y = pth.point(s)
    k, _ = pth.locate(s)
    ds = pth.s[k + 1] - pth.s[k] if k + 1 < len(pth.s) else 0.0
    tx, ty = ((pth.x[k + 1] - pth.x[k]) / ds, (pth.y[k + 1] - pth.y[k]) / ds) if ds > 0 else (0.0, 0.0)
    d["m"], d["age"] = FALL, 0.0
    d["x"], d["y"] = x, y
    d["vx"], d["vy"] = tx * v * 0.8, min(0.0, ty * v)
    # land on the first target bulb below; aim using where it will be when it gets there
    land = _ic_land(st, px, x, y - 0.3)
    if land is not None and abs(d["vx"]) > 0.05:
        fall = y - px[land].y
        tt = math.sqrt(2 * max(0.0, fall) / max(0.1, g))
        land = _ic_land(st, px, x + d["vx"] * tt, y - 0.3) or land
    d["land"] = land


def _ic_splash(st, rng, i, d, P):
    e = P["splash"]
    if e <= 0:
        return
    if len(st["splash"]) > 24:
        st["splash"].pop(0)
    p_y = d["y"]
    bits = []
    for sgn in (-1, 1):
        if rng.random() < 0.8:
            bits.append([d["x"], p_y + 0.1, sgn * (1.0 + 1.6 * rng.random()), 2.0 + 2.0 * rng.random()])
    st["splash"].append({"i": i, "age": 0.0, "e": e * (0.7 + 0.3 * rng.random()), "bits": bits})


# ====================================================================== ball drop
MODES = ["loop", "clock"]
COUNT_STYLES = ["digits", "rows", "off"]
BD_MAX_SPARKS = 700
MSG_START = 2.2       # seconds after the landing when the message starts to scroll in


def _text():
    eff = EFFECTS.get("text")
    return eff


def _bd_setup(ctx, st):
    key = _sig(ctx)
    if st.get("key") == key:
        return
    L, idx = ctx.layout, ctx.idx
    px = L.pixels
    st["key"] = key
    st["allowed"] = set(idx)
    # the zigzag: the target's bulbs on one run that is wired as rows
    groups: dict[str, list[int]] = {}
    for i in idx:
        groups.setdefault(px[i].run, []).append(i)
    zig, rows = [], None
    for g in sorted(groups.values(), key=len, reverse=True):
        r = geo._rows_by_reversal(px, g)
        if r:
            zig, rows = g, r
            break
    if rows is None:                                # no zigzag: the target's rows by height
        zig = list(idx)
        rows = []
        for i in sorted(zig, key=lambda i: px[i].y):
            if rows and abs(px[i].y - px[rows[-1][0]].y) <= 0.45:
                rows[-1].append(i)
            else:
                rows.append([i])
    rows = [sorted(r, key=lambda i: px[i].x) for r in rows if r]
    rows.sort(key=lambda r: sum(px[i].y for i in r) / len(r))
    st["zig"], st["rows"] = zig, rows
    zs = set(zig)
    if rows:
        top, bot = rows[-1], rows[0]
        cx = sum(px[i].x for i in top) / len(top)
        ytop = sum(px[i].y for i in top) / len(top)
        ybot = sum(px[i].y for i in bot) / len(bot)
        xs = [px[i].x for i in zig]
        lo, hi = min(xs), max(xs)
        # the star crowning the zigzag (the gable apex), if the target includes it
        above = sorted((i for i in idx if i not in zs and px[i].y > ytop + 0.3 and lo - 1 <= px[i].x <= hi + 1),
                       key=lambda i: (-px[i].y, abs(px[i].x - cx)))[:2]
        star = above if above and abs(sum(px[i].x for i in above) / len(above) - cx) < 3 else []
        if star:
            ystar = sum(px[i].y for i in star) / len(star)
        else:
            ystar = ytop
    else:
        cx = ytop = ybot = ystar = 0.0
        star = []
    st["cx"], st["ytop"], st["ybot"], st["ystar"], st["star"] = cx, ytop, ybot, ystar, star
    # the top row's centre bulbs: where the ball lands after leaving the star (bridges the
    # dark gap between the apex and the zigzag's top row)
    st["top_mid"] = sorted(rows[-1], key=lambda i: abs(px[i].x - cx))[:2] if rows else []
    st["rest"] = [i for i in idx if i not in zs and i not in set(star)]
    st["zig_idx"] = sorted(zig)
    if zig:
        zlo, zhi = min(px[i].x for i in zig) - 2.5, max(px[i].x for i in zig) + 2.5
        st["side"] = [i for i in idx if i not in zs and not zlo <= px[i].x <= zhi]
    else:
        st["side"] = []
    st["sparks"], st["glow"] = [], {}
    st["zset"], st["mw"] = zs, 0.0
    st["boom_id"] = None
    st["txt"], st["msg"] = {}, {}


def _parse_hhmm(s) -> tuple[int, int]:
    try:
        h, m = (int(v) for v in str(s).strip().replace(".", ":").split(":")[:2])
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h, m
    except (TypeError, ValueError):
        pass
    return 0, 0


def _bd_clock_remaining(P, now: float) -> tuple[float, int]:
    """(seconds until the drop lands, id of that drop) for clock mode: negative while
    celebrating the one that just landed."""
    h, m = _parse_hhmm(P["at"])
    try:
        here = _dt.datetime.fromtimestamp(now)
        today = here.replace(hour=h, minute=m, second=0, microsecond=0)
        last = today if today.timestamp() <= now else today - _dt.timedelta(days=1)
        nxt = last + _dt.timedelta(days=1)
        lt, nt = last.timestamp(), nxt.timestamp()
    except (OverflowError, OSError, ValueError):
        return 1e9, 0
    if now - lt < P["celebrate"]:
        return -(now - lt), int(lt)
    return nt - now, int(nt)


def _bd_burst(st, rng, x, y, n, speed, cols, up=False):
    sp = st["sparks"]
    for _ in range(n):
        a = rng.random() * math.tau
        if up:                                      # from the bottom row: mostly up and out
            a = math.pi * (0.04 + 0.92 * rng.random())
        v = speed * (0.35 + 0.65 * math.sqrt(rng.random()))
        c = cols[rng.randrange(len(cols))]
        sp.append([x, y, math.cos(a) * v, math.sin(a) * v * 0.85, 0.0, 1.3 + 1.1 * rng.random(), c,
                   rng.randrange(1 << 20)])
    if len(sp) > BD_MAX_SPARKS:
        del sp[:len(sp) - BD_MAX_SPARKS]


@frame_effect("ball_drop", "New Year's ball drop", [
    Param("mode", "choice", "loop", "Timing", options=MODES,
          help="loop: drop again and again (to preview it); clock: the ball lands exactly at the time "
               "below (midnight), waiting at the top until the last seconds"),
    Param("seconds", "int", 10, "Drop over the last (s)", 3, 60, 1,
          help="how long the ball takes to come down; the seconds are counted down on the zigzag"),
    Param("at", "text", "00:00", "Lands at (HH:MM)",
          help="clock mode: local time the ball lands, 00:00 = midnight (try 21:00 for an early kids' New Year)"),
    Param("celebrate", "number", 24, "Celebrate for (s)", 4, 900, 1,
          help="after the ball lands: the burst, sparks and the message"),
    Param("pause", "number", 4, "Wait between drops (s)", 1, 60, 0.5,
          help="loop mode: how long the ball waits at the top before the next countdown"),
    Param("countdown", "choice", "digits", "Countdown", options=COUNT_STYLES,
          help="digits: the seconds as big numbers on the zigzag; rows: zigzag rows go out as the ball "
               "passes; off: just the ball"),
    Param("ball_colors", "colors", ["#ffffff", "#ffd060", "#9fd0ff"], "Ball colours",
          help="the crystal ball's facets (white-hot core, gold and ice-blue glints)"),
    Param("digit_color", "color", "#2a78ff", "Digit colour",
          help="a strong colour that contrasts with the ball reads best"),
    Param("glow_color", "color", "#ffa000", "Glow colour",
          help="the rest of the target: drains with the ball and ticks each second"),
    Param("colors", "colors", ["#ffc020", "#ffffff", "#ff2040", "#3a8cff", "#30ff70", "#ff40d0"],
          "Firework colours", help="the bursts after the ball lands"),
    Param("sparks", "number", 1.0, "Sparks", 0, 2, 0.05, help="how many sparks fly when the ball lands"),
    Param("message", "text", "HAPPY NEW YEAR!", "Message",
          help="scrolls across the zigzag after the burst (empty = keep the glitter)"),
    Param("message_colors", "colors", ["#ffc000", "#ffffff", "#40a0ff"], "Message colours"),
    Param("wait_glow", "number", 0.14, "Waiting glow", 0, 0.8, 0.01,
          help="how bright the rest of the house glows (glow colour) while the ball waits"),
    Param("wait_chase", "number", 0.0, "Waiting chase", 0, 1, 0.05,
          help="slow bright bands of the glow colour travelling along the house while it waits"),
    Param("sky_color", "color", "#000000", "Gable sky",
          help="the zigzag's background behind the waiting ball (black = dark with a few stars); "
               "it shimmers slowly and dims while the ball comes down so the digits read"),
    Param("party_glow", "number", 0.12, "Celebration glow", 0, 0.8, 0.01,
          help="how bright the gold glitter stays on the house through the celebration"),
    Param("seed", "int", 26, "Seed", 0, 9999, 1),
], "Times Square on the front gable: a glowing crystal ball waits at the apex star, then glides "
   "down the centre of the zigzag over the last seconds while the seconds count down in big "
   "digits; at zero the whole house bursts gold and white, sparks fly and HAPPY NEW YEAR "
   "scrolls across the zigzag. Loop it to preview, or set Timing to clock to land exactly at "
   "midnight. Target the whole front gable (or the whole house).", group="Scenes")
def ball_drop(t, ctx, P, st):
    L, idx = ctx.layout, ctx.idx
    n = len(L.pixels)
    frame = [BLACK] * n
    if not idx:
        return frame
    _bd_setup(ctx, st)
    px = L.pixels
    clock = ctx.clock
    seed = P["seed"]
    allowed = st["allowed"]
    N = float(P["seconds"])
    cel = float(P["celebrate"])

    # ---------------------------------------------------------- where are we in the show
    if P["mode"] == "clock":
        r, drop_id = _bd_clock_remaining(P, _now())
    else:
        C = P["pause"] + N + cel
        k = math.floor(clock / C)
        r, drop_id = P["pause"] + N - (clock - k * C), k
    # r: seconds until the ball lands (negative: seconds since, while celebrating)

    # ---------------------------------------------------------- boom bookkeeping
    rng_seed = (seed * 1_000_003 + drop_id * 7919) & 0x7FFFFFFF
    if r <= 0:
        since = -r
        if st["boom_id"] != drop_id:
            st["boom_id"] = drop_id
            st["rng"] = random.Random(rng_seed)
            rng = st["rng"]
            st["sparks"].clear()
            st["msg"] = {}
            # a firework plan for the celebration: offsets after the landing
            plan, tt = [], 0.6
            while tt < cel - 2.5 and len(plan) < 400:
                plan.append(tt)
                tt += (0.35 + 0.5 * rng.random()) if tt < 6 else (0.9 + 1.6 * rng.random())
            st["plan"] = [p for p in plan if p > since]   # started late: skip the past
            if since < 0.5 and P["sparks"] > 0:
                cols = [WHITE, GOLD, (1.0, 0.85, 0.4)]
                _bd_burst(st, rng, st["cx"], st["ybot"], int(70 * P["sparks"]), 15.0, cols, up=True)
        rng = st["rng"]
        plan = st["plan"]
        fc = P["colors"] or [GOLD]
        while plan and plan[0] <= since:
            plan.pop(0)
            if P["sparks"] > 0:
                side = st["side"] if (since > MSG_START and str(P["message"]).strip()) else None
                if side:                            # keep the message clear: burst beside it
                    i = side[rng.randrange(len(side))]
                    x, y = px[i].x, px[i].y + 0.6 + 2.0 * rng.random()
                else:
                    i = idx[rng.randrange(len(idx))]
                    x = px[i].x
                    y = max(px[i].y, st["ybot"]) + 1.0 + 2.5 * rng.random()
                c = fc[rng.randrange(len(fc))]
                _bd_burst(st, rng, x, y, int(32 * P["sparks"]), 10.0 + 4 * rng.random(),
                          [c, c, lerp(c, WHITE, 0.6)])
    elif st["boom_id"] is not None and r > 0:
        st["boom_id"] = None

    # ---------------------------------------------------------- spark physics
    dt, steps = _substeps(_frame_dt(ctx))
    for _ in range(steps):
        kd = math.exp(-2.3 * dt)
        for s in st["sparks"]:
            s[4] += dt
            s[2] *= kd
            s[3] = s[3] * kd - 4.0 * dt
            s[0] += s[2] * dt
            s[1] += s[3] * dt
        st["sparks"] = [s for s in st["sparks"] if s[4] < s[5]]

    # ---------------------------------------------------------- the scene
    cx, ybot, ystar = st["cx"], st["ybot"], st["ystar"]
    zig = st["zig_idx"]
    if r > N:                                       # waiting at the top
        by = ystar
        phase = "wait"
    elif r > 0:                                     # coming down
        by = ybot + (ystar - ybot) * (r / N)
        phase = "drop"
    else:
        by = ybot
        phase = "boom"
    st["ball"], st["phase"], st["remaining"] = (cx, by), phase, r

    rest = st["rest"]
    gcol = P["glow_color"]
    if phase == "boom":
        since = -r
        fade = _smooth(min(1.0, (cel - since) / 2.0))     # fade out at the end of the celebration
        _bd_boom(frame, st, ctx, P, since, fade)
        if fade < 1.0 and (P["wait_glow"] > 0.14 or any(P["sky_color"])):
            back = 1.0 - fade                       # hand back to the waiting scene, no dark gap
            for i in rest:
                _add(frame, i, P["glow_color"], back * _bd_wait_level(P, px[i], i, clock, seed))
            _bd_wait_sky(frame, st, px, P, clock, seed, back)
    else:
        # the rest of the house: soft glitter while waiting; drains with the ball while it drops
        tick = 0.0
        if phase == "drop":
            f = r - math.floor(r)                    # 1 -> 0 over each second
            tick = math.exp(-(1.0 - f) / 0.18) * (0.6 if r > 3 else 1.0)
        for i in rest:
            p = px[i]
            if phase == "drop":
                below = _smooth((by - p.y) / 1.5 + 0.5) if p.y > ybot - 0.5 else 1.0
                lvl = 0.05 + 0.25 * below + 0.35 * tick
            else:
                lvl = _bd_wait_level(P, p, i, clock, seed)
            g, _ = glint(i, clock, 0.08, 0.05, 0.4, seed, 1)
            c = lerp(gcol, WARM_WHITE, 0.6 * g)
            _add(frame, i, c, lvl + 0.5 * g)
        # the countdown on the zigzag
        if phase == "drop" and P["countdown"] == "digits":
            nsec = max(1, math.ceil(r - 1e-9))
            _bd_digit(frame, st, ctx, P, nsec, r)
        elif phase == "drop" and P["countdown"] == "rows":
            dc = P["digit_color"]
            for row in st["rows"]:
                ry = px[row[0]].y
                v = 0.55 * _smooth((by - ry) / 0.8 + 0.2)
                for i in row:
                    _add(frame, i, dc, v)
        elif phase == "wait":
            _bd_wait_sky(frame, st, px, P, clock, seed, 1.0)
        if phase == "drop":
            _bd_wait_sky(frame, st, px, P, clock, seed, 0.3, stars=False)
        size = 1.0
        if phase == "wait" and P["mode"] == "clock" and r < N + 60:
            size += 0.15 * math.sin(clock * 6.3)   # the last minute: the waiting ball pulses
        _bd_ball(frame, st, L, P, clock, cx, by, size, _bd_show(r) if phase == "drop" and
                 P["countdown"] == "digits" else 0.0)

    # ---------------------------------------------------------- sparks (with afterglow)
    fresh = [BLACK] * n
    for s in st["sparks"]:
        u = s[4] / s[5]
        b = (1.0 - u) ** 1.2
        if u > 0.45:
            b *= 0.4 + 0.6 * hash01(s[7], int(clock * 16))
        c = lerp(WHITE, s[6], min(1.0, u * 5)) if u < 0.2 else s[6]
        geo.splat(fresh, L, s[0], s[1], (c[0] * b, c[1] * b, c[2] * b), 1.0, allowed)
    glow = st["glow"]
    zset = st["zset"]
    if phase != "boom":
        st["mw"] = 0.0
    kd = math.exp(-dt * steps / 0.12) if dt > 0 else 1.0
    for i in idx:
        f = fresh[i]
        o = glow.get(i)
        if o is not None:
            c = (max(f[0], o[0] * kd), max(f[1], o[1] * kd), max(f[2], o[2] * kd))
        else:
            c = f
        if c[0] + c[1] + c[2] > 0.004:
            glow[i] = c
            _add(frame, i, c, 1.0 - 0.85 * st["mw"] if i in zset else 1.0)
        elif o is not None:
            del glow[i]
    return _clamp(frame, idx)


def _bd_wait_level(P, p, i, clock, seed) -> float:
    """The rest of the house while the ball waits: a soft glow, with slow travelling bands."""
    lvl = P["wait_glow"] + 0.05 * math.sin(clock * 0.8 + hash01(i, seed) * 6.28)
    ch = P["wait_chase"]
    if ch > 0:
        w = (0.5 + 0.5 * math.sin(p.x * 0.3 - clock * 1.6)) ** 3
        lvl = lvl * (1.0 - 0.5 * ch) + ch * 0.5 * w
    return lvl


def _bd_wait_sky(frame, st, px, P, clock, seed, k, stars=True):
    """The zigzag behind the waiting ball: the sky colour with a slow shimmer down the rows,
    and a few silver stars."""
    sky = P["sky_color"]
    lit = any(sky)
    if lit:
        for r, row in enumerate(st["rows"]):
            for i in row:
                s = 0.7 + 0.3 * math.sin(clock * 1.1 - r * 0.7 + hash01(i, seed, 5) * 1.5)
                _add(frame, i, sky, k * s)
    if not stars:
        return
    rate, amt = (0.12, 0.7) if lit else (0.05, 0.35)
    for i in st["zig_idx"]:                         # a few stars waiting with the ball
        g, _ = glint(i, clock, rate, 0.3, 0.6, seed + 3, 1)
        if g > 0.02:
            _add(frame, i, (0.7, 0.8, 1.0), k * amt * g)


def _bd_show(r: float) -> float:
    """How much the countdown number has the stage (0..1): each new number takes it for
    the first ~0.6 s of its second (the ball shrinks to a bright core), then hands it
    back to the ball. Both stay visible throughout."""
    f = r - math.floor(r)                            # 1 -> 0 over each second
    return _smooth((f - 0.35) / 0.2)


def _bd_ball(frame, st, L, P, clock, x, y, size=1.0, show=0.0):
    """The crystal ball: white-hot core, faceted gold / ice glints, a soft halo.
    show > 0 (a countdown number on stage): smaller, without the dark ring."""
    R = 1.15 * size * (1.0 - 0.3 * show)
    ring = 0.6 * (1.0 - show)
    halo = 0.22 * (1.0 - show)
    allowed = st["allowed"]
    bc = P["ball_colors"] or [WHITE]
    seed = P["seed"]
    fac = int(clock * 7)
    # between the apex star and the zigzag's top row there are no bulbs (~2 bulbs of gap):
    # hand the ball over from the star to the top row's centre so it never vanishes
    star, mid = st["star"], st.get("top_mid") or []
    ys, yt = st["ystar"], st["ytop"]
    bridging = bool(star) and bool(mid) and ys - yt > 1e-6 and yt < y < ys
    if bridging:
        ring = 0.0
        g = (ys - y) / (ys - yt)
        for grp, w in ((star, min(1.0, 2.0 * (1.0 - g))), (mid, min(1.0, 2.0 * g))):
            for i in grp:
                if i in allowed and w > 0:
                    c = lerp(bc[int(hash01(i, fac, seed) * 997) % len(bc)], WHITE, 0.5)
                    a = frame[i]
                    frame[i] = (max(a[0], c[0] * w), max(a[1], c[1] * w), max(a[2], c[2] * w))
    for i, d in geo.near(L, x, y, R + 1.4):
        if i not in allowed:
            continue
        if d < R:
            core = 1.0 - (d / R) ** 2
            h = hash01(i, fac, seed)
            c = bc[int(h * 997) % len(bc)]
            c = lerp(c, WHITE, 0.55 * core)
            b = 0.8 + 0.2 * core
            edge = _smooth((R - d) / 0.45)
            a = frame[i]
            w = edge
            frame[i] = (a[0] * (1 - w) + c[0] * b * w, a[1] * (1 - w) + c[1] * b * w,
                        a[2] * (1 - w) + c[2] * b * w)
        else:
            q = 1.0 - (d - R) / 1.4
            a = frame[i]
            k = 1.0 - ring * q                        # a dark ring sets the ball off the digits
            hq = halo * q * q
            frame[i] = (a[0] * k + hq, a[1] * k + 0.75 * hq, a[2] * k + 0.3 * hq)
    # the star bulbs: glow with the ball while it waits, dim once it has left
    for i in st["star"]:
        d = abs(y - L.pixels[i].y)
        if d >= R and not bridging:
            _add(frame, i, GOLD, 0.25)


def _bd_digit_cells(st, px, nsec: int) -> list:
    """[(bulb, weight)] lighting the number on the zigzag's text rows (the bottom five),
    centred, as bold as fits. Digits stand still, so they are drawn crisply: each bulb
    by how much of its own footprint lit font columns cover."""
    key = ("cells", nsec)
    cache = st["txt"]
    out = cache.get(key)
    if out is not None:
        return out
    rows = st["rows"][:font.ROWS]
    bm = font.layout(str(nsec))
    out = []
    if rows and bm.width:
        nr = len(rows)
        dx = sorted(px[b].x - px[a].x for r in rows for a, b in zip(r, r[1:]) if px[b].x - px[a].x > 1e-6)
        pitch = max(0.2, min(2.0, dx[len(dx) // 2])) if dx else 1.0
        top = rows[-1]
        avail = px[top[-1]].x - px[top[0]].x + pitch
        cw = max(0.3, min(2.0 * pitch, avail / bm.width))
        x0 = st["cx"] - bm.width * cw / 2
        h = pitch / 2
        for k, row in enumerate(rows):
            gr = font.ROWS - 1 - (round(k * (font.ROWS - 1) / (nr - 1)) if nr > 1 else 2)
            for i in row:
                x = px[i].x
                cov = 0.0
                lo, hi = (x - h - x0) / cw, (x + h - x0) / cw
                c = max(0, math.floor(lo))
                while c < hi and c < bm.width:
                    if bm.cols[c] >> gr & 1:
                        cov += min(hi, c + 1) - max(lo, c)
                    c += 1
                w = _smooth(cov * cw / pitch * 1.4 - 0.2)
                if w > 0.02:
                    out.append((i, w))
    if len(cache) > 128:
        cache.clear()
    cache[key] = out
    return out


def _bd_digit(frame, st, ctx, P, nsec, r):
    cells = _bd_digit_cells(st, ctx.layout.pixels, nsec)
    k = 0.3 + 0.7 * _bd_show(r)                       # each new number lands bright, then dims
    dc = P["digit_color"]
    for i, w in cells:
        _add(frame, i, dc, w * k)


def _bd_boom(frame, st, ctx, P, since, fade):
    """The moment: white flash -> gold glitter on the whole target; the message follows
    on the zigzag (its glitter makes way as the letters come in)."""
    clock, seed = ctx.clock, P["seed"]
    zig = set(st["zig_idx"])
    msg = bool(str(P["message"]).strip()) and bool(zig) and since > MSG_START
    mw = _smooth((since - MSG_START) / 0.8) if msg else 0.0
    st["mw"] = mw * fade
    flash = math.exp(-since / 0.22)
    goldl = 0.7 * math.exp(-since / 1.4) + P["party_glow"]
    rate = 0.9 * math.exp(-since / 5.0) + 0.12
    base = lerp(GOLD, WHITE, flash)
    for i in ctx.idx:
        k = fade * (1.0 - mw) if i in zig else fade
        if k <= 0:
            continue
        g, _ = glint(i, clock, rate, 0.02, 0.3, seed + 9, 1)
        lvl = (goldl + flash) * (0.7 + 0.3 * hash01(i, seed, 8))
        _add(frame, i, lerp(base, WHITE, 0.7 * g), (lvl + 0.8 * g) * k)
    if msg:
        _bd_message(frame, st, ctx, P, since, mw * fade)


def _bd_message(frame, st, ctx, P, since, w):
    eff = _text()
    if eff is None:
        return
    mk = (P["message"], tuple(P["message_colors"] or ()))
    Pm = st.get("msg_P")
    if Pm is None or st.get("msg_key") != mk:
        cols = [_hex(c) for c in (P["message_colors"] or [GOLD])]
        Pm = eff.resolve({"text": P["message"], "speed": 6.0, "colors": cols, "color_mode": "letters",
                          "sparkle": 0.5, "border": "twinkle", "border_color": "#ffd060", "size": 1.2})
        st["msg_P"], st["msg_key"] = Pm, mk
    Pm = dict(Pm)
    Pm["_travel"] = Pm["speed"] * (since - MSG_START)
    tctx = Ctx(ctx.layout, st["zig_idx"], ctx.clock, ctx.dt, getattr(ctx, "bpm", 120.0), ctx.cache)
    try:
        out = eff.fn(ctx.clock, tctx, Pm, st["msg"])
    except Exception:  # noqa: BLE001 - never let the text renderer take the show down
        return
    for i in st["zig_idx"]:
        c = out[i]
        if c[0] or c[1] or c[2]:
            _add(frame, i, c, w)


def _hex(c) -> str:
    return "#%02x%02x%02x" % tuple(int(round(max(0.0, min(1.0, v)) * 255)) for v in c)
