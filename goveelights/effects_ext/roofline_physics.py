"""Gravity on the rooflines.

    coaster  Roller coaster: balls of light ride the roofline under real gravity,
             plunging down the gables, crawling up on a chain lift, cresting the peaks.
    embers   Sparks & embers: sparks pour off the highest peak, slide down the roof
             slopes, fly off the eaves, fall through the air and land on the roofline
             below, where they flare and cool.

Both are frame effects with memory (state). Physics runs in bulb-spacing units
(distances) and seconds, in small fixed sub-steps so it behaves the same at any
frame rate; dt is capped so a stalled frame never teleports anything.

A "track" is a stretch of the layer's target that is physically continuous: the
target's bulbs in strip order, split wherever the next bulb is on another run or
too far away to be a neighbour. On the house, target "Eaves" is one track with the
two gables; "all" gives three tracks (eaves, zigzag, lower roofline).
"""
from __future__ import annotations

import math
import random

from .. import geometry as geo
from ..color import clamp, hash01, lerp, palette
from ..effects import Param, frame_effect

MAX_DT = 0.25         # longest step simulated per frame (s): fits 5x master speed at 20 fps
HIST_MAX = 256        # hard cap on a car's trail history
CREEP = 1.5           # drift speed on flats when the chain lift is off (bulbs/s)
STEP = 1 / 60         # physics sub-step (s)
WHITE = (1.0, 1.0, 1.0)


# ------------------------------------------------------------------ shared helpers
def _signature(layout, idx) -> tuple:
    return (id(layout), len(layout.pixels), hash(tuple(idx)))


def _tracks(layout, idx) -> list:
    """The target split into physically continuous paths (see module doc)."""
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
            if pth.length > 1e-6:          # coincident bulbs make no track
                out.append(pth)
    return out


def _doubles_back(pth) -> bool:
    """True for a path that zigzags back and forth across the house (a fill such as
    the Peak tree rows) rather than following a roofline."""
    flips, last = 0, 0
    for a, b in zip(pth.x, pth.x[1:]):
        dx = b - a
        if abs(dx) < 0.3:
            continue
        sg = 1 if dx > 0 else -1
        if last and sg != last:
            flips += 1
            if flips > 2:
                return True
        last = sg
    return False


def _roof_tracks(layout, idx) -> list:
    """Tracks a coaster can ride: rooflines only. A zigzag fill is used only when the
    target holds nothing else (e.g. target "Peak")."""
    tracks = _tracks(layout, idx)
    roof = [t for t in tracks if not _doubles_back(t)]
    return roof or tracks


def _tangent(pth, s: float) -> tuple[float, float]:
    """Unit direction (dx/ds, dy/ds) of the path at s (increasing s)."""
    k, _ = pth.locate(s)
    ds = pth.s[k + 1] - pth.s[k]
    if ds <= 0:
        return 1.0, 0.0
    return (pth.x[k + 1] - pth.x[k]) / ds, (pth.y[k + 1] - pth.y[k]) / ds


def _blank(n: int, bg) -> list:
    return [bg] * n


def _finish(frame: list, idx) -> list:
    for i in idx:
        c = frame[i]
        if c[0] > 1 or c[1] > 1 or c[2] > 1:
            frame[i] = (min(1.0, c[0]), min(1.0, c[1]), min(1.0, c[2]))
    return frame


def _put_max(frame, i, c, b):
    a = frame[i]
    frame[i] = (max(a[0], c[0] * b), max(a[1], c[1] * b), max(a[2], c[2] * b))


# ------------------------------------------------------------------ roller coaster
class _Car:
    __slots__ = ("tr", "s", "v", "dir", "hist", "k")

    def __init__(self, tr, s, v, k):
        self.tr, self.s, self.v, self.k = tr, s, v, k
        self.dir = 1 if v >= 0 else -1
        self.hist: list[tuple[float, float]] = []      # (time, s), oldest first


def _assign_tracks(tracks, count: int) -> list[int]:
    """Which track each car rides: shared out by track length (largest remainder),
    every track getting one before any gets a second, longest tracks first."""
    if not tracks or count <= 0:
        return []
    order = sorted(range(len(tracks)), key=lambda t: -tracks[t].length)
    total = sum(tracks[t].length for t in order) or 1.0
    per = {t: 0 for t in order}
    for t in order[:count]:
        per[t] = 1
    for _ in range(count - min(count, len(order))):
        t = max(order, key=lambda t: tracks[t].length / total * count - per[t])
        per[t] += 1
    out = []
    for t in order:
        out += [t] * per[t]
    return out


def _coaster_reset(state, layout, idx, P):
    tracks = _roof_tracks(layout, idx)
    state.clear()
    state["sig"] = _signature(layout, idx)
    state["seed"] = P["seed"]
    state["tracks"] = tracks
    state["time"] = 0.0
    state["rng"] = random.Random(P["seed"] * 7919 + 17)
    state["sparks"] = []
    state["cars"] = []
    # height range of each track: with gravity it sets what "fast" means for the sparkle
    state["height"] = [max(1.0, max(t.y) - min(t.y)) for t in tracks]


def _fast(state, P) -> list[float]:
    """Speed above which a car throws sparks, per track (follows live gravity edits)."""
    g = P["gravity"]
    if state.get("fast_g") != g:
        state["fast_g"] = g
        state["fast"] = [0.4 * math.sqrt(2 * g * h) for h in state["height"]]
    return state["fast"]


def _coaster_sync_cars(state, P):
    """Match the car list to the cars param without disturbing cars that stay."""
    tracks, cars = state["tracks"], state["cars"]
    want = _assign_tracks(tracks, P["cars"]) if tracks else []
    if [c.tr for c in cars] == want:
        return
    keep = []
    pool = list(cars)
    for k, tr in enumerate(want):
        hit = next((c for c in pool if c.tr == tr), None)
        if hit is not None:
            pool.remove(hit)
            keep.append(hit)
            continue
        # new car: spread evenly along its track, alternate directions
        on = sum(1 for t in want if t == tr)
        j = sum(1 for t in want[:k] if t == tr)
        pth = tracks[tr]
        s = pth.length * (j + 0.5 + 0.25 * hash01(P["seed"], k)) / on
        v = (1 if (k % 2 == 0) else -1) * max(4.0, P["lift"])
        keep.append(_Car(tr, min(pth.length, s), v, k))
    for k, c in enumerate(keep):
        c.k = k
    state["cars"] = keep


def _coaster_step(car, pth, P, h):
    g, fr, lift = P["gravity"], P["friction"], P["lift"]
    L = pth.length
    if L <= 0:
        car.s, car.v = 0.0, 0.0
        return
    s, v = car.s, car.v
    if abs(v) > 0.05:
        car.dir = 1 if v > 0 else -1
    d = car.dir
    sl = pth.slope(s)
    # chain lift: on a climb or a flat, a car slower than the chain is caught and
    # carried at chain speed (gravity and friction are taken by the chain)
    if lift > 0 and sl * d > -0.02 and abs(v) < lift:
        v = d * (abs(v) + (lift - abs(v)) * (1 - math.exp(-5 * h)))
    else:
        v += (-g * sl - fr * v) * h
        if lift <= 0 and abs(sl) < 0.02 and abs(v) < CREEP:
            # no chain: on a flat the car still drifts on gently, so it reaches the
            # next slope and rocks there instead of parking dead on the eave
            v = d * (abs(v) + (CREEP - abs(v)) * (1 - math.exp(-2 * h)))
    s += v * h
    if P["ends"] == "wrap":
        if s < 0 or s > L:
            s %= L
    elif s < 0:
        s, v = -s, -v * 0.85
    elif s > L:
        s, v = 2 * L - s, -v * 0.85
    car.s, car.v = clamp(s, 0.0, L), v


@frame_effect("coaster", "Roller coaster", [
    Param("cars", "int", 3, "Cars", 1, 12, 1, help="balls of light on the track (shared among the target's rooflines)"),
    Param("colors", "colors", ["#ff1010", "#10ff30", "#ffb000"], "Car colours", help="each car takes the next colour"),
    Param("gravity", "number", 30, "Gravity", 2, 120, 1,
          help="how hard the cars are pulled downhill (bulbs/s²): higher = wilder plunges"),
    Param("friction", "number", 0.12, "Friction", 0, 2, 0.01, help="how fast the cars lose speed"),
    Param("lift", "number", 6, "Chain lift speed", 0, 25, 0.5,
          help="slow cars on climbs and flats are towed at this speed, so they always make it over "
               "the crest; 0 = no chain: pure gravity, the cars just drift slowly along the flat eaves and roll back off the slopes"),
    Param("trail", "number", 0.6, "Trail (s)", 0, 3, 0.05,
          help="how long the glowing trail lasts: short when crawling, a long streak at full speed"),
    Param("ends", "choice", "bounce", "At the ends", options=["bounce", "wrap"],
          help="bounce off the end of the track, or come back round from the other end"),
    Param("sparkle", "number", 0.6, "Speed sparkle", 0, 1, 0.05, help="sparks thrown off the wheels at top speed"),
    Param("bump", "bool", False, "Cars bump", help="cars on the same track knock into each other instead of passing through"),
    Param("background", "color", "#000000"),
    Param("seed", "int", 1, "Seed", 0, 9999, 1),
], "Balls of light ride the roofline like a roller coaster: towed slowly up each gable, "
   "pausing at the top, then plunging down with a long glowing streak and racing across the "
   "flat eaves, throwing sparks at full speed.", group="Physics")
def coaster(t, ctx, P, state):
    layout, idx = ctx.layout, ctx.idx
    n = len(layout.pixels)
    frame = _blank(n, P["background"])
    if not idx:
        return frame
    if state.get("sig") != _signature(layout, idx) or state.get("seed") != P["seed"]:
        _coaster_reset(state, layout, idx, P)
    tracks = state["tracks"]
    if not tracks:
        return frame
    _coaster_sync_cars(state, P)
    cars, rng = state["cars"], state["rng"]
    fasts = _fast(state, P)
    dt = min(MAX_DT, max(0.0, ctx.dt or 0.0))
    state["time"] += dt
    now = state["time"]

    # --- physics
    steps = max(1, math.ceil(dt / STEP)) if dt > 0 else 0
    h = dt / steps if steps else 0.0
    for _ in range(steps):
        for c in cars:
            _coaster_step(c, tracks[c.tr], P, h)
        if P["bump"] and len(cars) > 1:
            for a in range(len(cars)):
                for b in range(a + 1, len(cars)):
                    A, B = cars[a], cars[b]
                    if A.tr == B.tr and abs(A.s - B.s) < 1.0 and (B.s - A.s) * (A.v - B.v) > 0:
                        A.v, B.v = B.v * 0.95, A.v * 0.95      # equal masses: swap speeds

    # --- trails (history) and speed sparks
    trail = P["trail"]
    sparks = state["sparks"]
    for c in cars:
        if dt > 0 or not c.hist:              # paused (dt 0): time stands still, add nothing
            c.hist.append((now, c.s))
        while len(c.hist) > 2 and c.hist[1][0] < now - trail:
            c.hist.pop(0)
        if len(c.hist) > HIST_MAX:
            del c.hist[:-HIST_MAX]
        fast = fasts[c.tr]
        sp = abs(c.v)
        if P["sparkle"] > 0 and dt > 0 and sp > fast:
            want = P["sparkle"] * 30 * min(1.5, (sp - fast) / fast) * dt
            while want > 0 and len(sparks) < 120:
                if rng.random() < want:
                    life = 0.25 + 0.35 * rng.random()
                    sparks.append([c.tr, c.s - c.dir * rng.random() * 1.2,
                                   c.v * (0.2 + 0.3 * rng.random()), 0.0, life, c.k])
                want -= 1
    for sk in sparks:
        sk[3] += dt
        sk[1] += sk[2] * dt
        sk[2] *= math.exp(-4 * dt)
    sparks[:] = [sk for sk in sparks if sk[3] < sk[4] and 0 <= sk[1] <= tracks[sk[0]].length]

    # --- render: trails first (max), then heads, then sparks (added)
    cols = P["colors"]
    for c in cars:
        pth = tracks[c.tr]
        col = cols[c.k % len(cols)]
        if trail > 0:
            _trail(frame, pth, c.hist, now, trail, col)
    for c in cars:
        pth = tracks[c.tr]
        col = cols[c.k % len(cols)]
        hot = clamp(abs(c.v) / max(1.0, 2.5 * fasts[c.tr]))
        geo.splat_path(frame, pth, c.s, lerp(col, WHITE, 0.45 * hot), 1.5, mode="max")
    for sk in sparks:
        f = 1 - sk[3] / sk[4]
        fl = 0.55 + 0.45 * hash01(int(sk[3] * 40), int(sk[1] * 10), sk[5])
        col = lerp(cols[sk[5] % len(cols)], WHITE, 0.75)
        b = f * fl
        geo.splat_path(frame, tracks[sk[0]], sk[1], (col[0] * b, col[1] * b, col[2] * b), 0.9)
    return _finish(frame, idx)


def _trail(frame, pth, hist, now, trail, col):
    """Fading trail along the path through the car's recent positions: each bulb takes
    the brightness of the latest moment the car passed it."""
    S, order = pth.s, pth.order
    half = pth.length / 2
    for (t0, s0), (t1, s1) in zip(hist, hist[1:]):
        if abs(s1 - s0) > half or abs(s1 - s0) < 1e-6:        # wrapped round, or standing
            continue
        b0 = (1 - (now - t0) / trail)
        b1 = (1 - (now - t1) / trail)
        if b1 <= 0:
            continue
        b0, b1 = max(0.0, b0) ** 2, b1 ** 2
        lo, hi = (s0, s1) if s0 < s1 else (s1, s0)
        k, _ = pth.locate(lo)
        j = k
        while j < len(S) and S[j] <= hi:
            if S[j] >= lo:
                f = (S[j] - s0) / (s1 - s0)
                _put_max(frame, order[j], col, b0 + (b1 - b0) * f)
            j += 1


# ------------------------------------------------------------------ sparks & embers
SLIDE, FALL, REST = 0, 1, 2


class _Spark:
    __slots__ = ("mode", "tr", "s", "v", "x", "y", "vx", "vy", "heat", "wind", "landed", "k")

    def __init__(self, k):
        self.k = k
        self.mode, self.tr, self.s, self.v = SLIDE, 0, 0.0, 0.0
        self.x = self.y = self.vx = self.vy = 0.0
        self.heat, self.wind, self.landed = 1.0, 0.0, -1.0


def _embers_reset(state, layout, idx, P):
    px = layout.pixels
    tracks = _tracks(layout, idx)
    where = {}                                      # bulb -> (track, s)
    for t, pth in enumerate(tracks):
        for j, i in enumerate(pth.order):
            where[i] = (t, pth.s[j])
    bins: dict[int, list[int]] = {}
    for i in idx:
        bins.setdefault(math.floor(px[i].x), []).append(i)
    # does anything in the target lie below this bulb? (if not, sparks always land on it)
    below = {}
    for i in idx:
        p = px[i]
        below[i] = any(abs(px[j].x - p.x) <= 1.0 and px[j].y < p.y - 0.4
                       for b in (math.floor(p.x) - 1, math.floor(p.x), math.floor(p.x) + 1)
                       for j in bins.get(b, ()))
    ys = [px[i].y for i in idx]
    # a flat target (one level, no slope to slide down): sparks also start anywhere
    # along it and slide a little before dripping off, so it never stays dark
    scatter = [i for i in idx if i in where] if max(ys) - min(ys) < 1.0 else []
    state.clear()
    state.update(sig=_signature(layout, idx), seed=P["seed"], tracks=tracks, where=where, bins=bins,
                 below=below, allowed=set(idx), sources=_sources(layout, idx, where, bins),
                 scatter=scatter, sparks=[], acc=0.0,
                 rng=random.Random(P["seed"] * 104729 + 3), count=0,
                 box=(min(px[i].x for i in idx) - 4, max(px[i].x for i in idx) + 4,
                      min(ys) - 3))
    # pre-warm: run the fountain for a while so the first frame already looks steady
    warm = min(P["life"], 3.0)
    while warm > 1e-9:
        d = min(0.1, warm)
        _embers_advance(state, P, layout, d)
        warm -= d


def _sources(layout, idx, where, bins) -> list[tuple[float, float, int | None, bool]]:
    """Where sparks pour from: the target's highest peak(s); failing that the bulbs
    crowning the target area (e.g. the gable apex over the zigzag); failing that its
    top bulb. -> [(x, y, bulb on a track or None, anything below?)]"""
    px = layout.pixels
    tgt = set(idx)
    top = max(px[i].y for i in idx)
    pts = [(o["x"], o["y"], o["i"]) for o in geo.peaks(layout) if o["i"] in tgt and o["y"] >= top - 1.0]
    if not pts:
        xs = [px[i].x for i in idx]
        lo, hi = min(xs), max(xs)
        mid = (lo + hi) / 2

        def over_target(p):              # target bulbs well under it to catch what falls
            return any(abs(px[j].x - p.x) <= 2.0 and px[j].y < p.y - 3.0
                       for b in range(math.floor(p.x) - 2, math.floor(p.x) + 3) for j in bins.get(b, ()))

        above = sorted((p for p in px if p.i not in tgt and p.y > top + 0.3 and lo - 1 <= p.x <= hi + 1),
                       key=lambda p: (-p.y, abs(p.x - mid)))
        a = next((p for p in above if over_target(p)), None)
        if a is not None:
            pts = [(a.x, a.y + 0.2, None)]
        else:
            # the target's own top bulb, preferring one on a track so sparks slide off it
            i = max(idx, key=lambda i: (i in where, px[i].y, -abs(px[i].x - mid)))
            pts = [(px[i].x, px[i].y, i)]
    out = []
    for x, y, i in pts:
        has_below = any(px[j].y < y - 0.5 and abs(px[j].x - x) < 4
                        for b in range(math.floor(x) - 4, math.floor(x) + 5) for j in bins.get(b, ()))
        out.append((x, y, i if i in where else None, has_below))
    return out


def _spawn(state, P):
    rng = state["rng"]
    srcs, scatter = state["sources"], state["scatter"]
    if not srcs and not scatter:
        return
    sp = _Spark(state["count"])
    state["count"] += 1
    sp.wind = (rng.random() * 2 - 1) * P["drift"] * 4.0
    sp.heat = 0.9 + 0.1 * rng.random()
    if scatter and (not srcs or srcs[0][2] is not None or rng.random() < 0.5):
        # flat target: a spark lands anywhere on the line and skids off along it (mixed
        # half and half with sparks raining from a peak overhead, when there is one)
        sp.mode = SLIDE
        sp.tr, sp.s = state["where"][scatter[rng.randrange(len(scatter))]]
        sp.v = (1 if rng.random() < 0.5 else -1) * (1.2 + 2.3 * rng.random())
        state["sparks"].append(sp)
        return
    x, y, bulb, has_below = srcs[rng.randrange(len(srcs))]
    spill = bulb is None or (has_below and rng.random() < P["spill"])
    if spill:
        sp.mode = FALL
        sp.x, sp.y = x + (rng.random() - 0.5) * 0.6, y - 0.05
        sp.vx = (rng.random() * 2 - 1) * (1.5 + 12.0 * P["spread"])
        sp.vy = rng.random() * (0.5 + 3.0 * P["spread"])
    else:
        sp.mode = SLIDE
        sp.tr, sp.s = state["where"][bulb]
        sp.v = (1 if rng.random() < 0.5 else -1) * (1.0 + 2.5 * rng.random())
    state["sparks"].append(sp)


def _fall_from(sp, pth, s, v):
    """Leave the track at s with speed v along it: becomes a free-falling spark."""
    tx, ty = _tangent(pth, s)
    sp.mode = FALL
    sp.x, sp.y = pth.point(s)
    sp.y -= 0.02
    sp.vx, sp.vy = tx * v, ty * v


def _land(sp, state, bulb, P, rng):
    """Hit a bulb: flare up and settle there (skidding a little along its track)."""
    px_where = state["where"].get(bulb)
    sp.heat = min(1.0, sp.heat + 0.5 * P["glow"])
    sp.landed = 0.0
    if px_where is None:                      # a lone bulb: rest exactly on it
        sp.mode, sp.tr, sp.s, sp.v = REST, -1, float(bulb), 0.0
        return
    sp.tr, sp.s = px_where
    tx, _ = _tangent(state["tracks"][sp.tr], sp.s)
    sp.mode, sp.v = REST, sp.vx * tx * (0.25 + 0.2 * rng.random())


def _embers_step(state, P, layout, h, rng):
    g, fr = P["gravity"], P["friction"]
    tracks, px = state["tracks"], layout.pixels
    bins, below = state["bins"], state["below"]
    leap = P["leap"]
    for sp in state["sparks"]:
        if sp.mode == FALL:
            y0 = sp.y
            sp.vx += (sp.wind - 0.6 * sp.vx) * h
            sp.vy += (-g - 0.6 * sp.vy) * h
            sp.x += sp.vx * h
            sp.y += sp.vy * h
            if sp.vy < 0:
                # crossing a target bulb on the way down? (highest one first)
                hit = None
                fx = math.floor(sp.x)
                for b in (fx - 1, fx, fx + 1):
                    for j in bins.get(b, ()):
                        p = px[j]
                        if sp.y <= p.y < y0 and abs(p.x - sp.x) <= 0.6 and (hit is None or p.y > px[hit].y):
                            hit = j
                if hit is not None:
                    # bulbs with more roofline below only catch some sparks (the rest fall
                    # past them); the lowest roofline catches everything
                    if not below[hit] or rng.random() < P["catch"]:
                        sp.x, sp.y = px[hit].x, px[hit].y
                        _land(sp, state, hit, P, rng)
            continue
        if sp.tr < 0:                       # resting on a lone bulb
            continue
        pth = tracks[sp.tr]
        sl = pth.slope(sp.s)
        d = 1 if sp.v >= 0 else -1
        f = fr if sp.mode == SLIDE else 6.0
        sp.v += (-g * sl - f * sp.v) * h
        s1 = sp.s + sp.v * h
        if s1 < 0 or s1 > pth.length:       # ran off the end of the track
            _fall_from(sp, pth, clamp(s1, 0.0, pth.length), sp.v)
            continue
        sl1 = pth.slope(s1)
        if sp.mode == SLIDE:
            if sl * d < -0.2 and sl1 * d > -0.05 and rng.random() < leap:
                # bottom of a slope: fly off the corner of the roof
                _fall_from(sp, pth, sp.s, sp.v)
                continue
            if abs(sp.v) < 0.8 and abs(sl1) < 0.05:
                _fall_from(sp, pth, s1, sp.v)       # lost its speed on a flat: drips off
                continue
        sp.s = s1


@frame_effect("embers", "Sparks & embers", [
    Param("rate", "number", 10, "Sparks per second", 0.5, 60, 0.5),
    Param("colors", "colors", ["#ffffff", "#ffe070", "#ff9a10", "#ff4a00", "#c01000"], "Colours (hot to cool)",
          help="a spark starts with the first colour and cools through the rest"),
    Param("gravity", "number", 20, "Gravity", 2, 80, 1, help="how fast sparks slide and fall (bulbs/s²)"),
    Param("life", "number", 2.5, "Life (s)", 0.5, 10, 0.1, help="how long a spark burns before it cools out"),
    Param("glow", "number", 0.7, "Glow on landing", 0, 1, 0.05,
          help="how brightly a landing spark flares up and how long it lingers as an ember"),
    Param("friction", "number", 0.8, "Roof friction", 0, 4, 0.05, help="higher = sparks slide down the roof more slowly"),
    Param("leap", "number", 0.6, "Fly off at the eaves", 0, 1, 0.05,
          help="share of sparks that leap off where a slope meets the flat eave (the rest run on along it)"),
    Param("spill", "number", 0.3, "Tumble off the peak", 0, 1, 0.05,
          help="share of sparks that drop straight off the peak into the air instead of sliding down "
               "(only where there are lights below to land on, e.g. the zigzag and porch)"),
    Param("spread", "number", 0.4, "Spray width", 0, 1, 0.05,
          help="how widely the sparks that tumble off the peak spray out sideways, like a sparkler fountain"),
    Param("catch", "number", 0.15, "Caught on the way down", 0, 1, 0.05,
          help="chance a falling spark settles on a light it passes (the lowest roofline catches all)"),
    Param("drift", "number", 0.3, "Drift", 0, 1, 0.05, help="sideways wander of falling sparks"),
    Param("background", "color", "#000000"),
    Param("seed", "int", 1, "Seed", 0, 9999, 1),
], "Sparks pour from the highest peak like a sparkler: white-hot sparks slide down both slopes of "
   "the roof, fly off the corners of the eaves and rain down onto the lights below, where they "
   "flare and glow orange-red as they cool.", group="Physics")
def embers(t, ctx, P, state):
    layout, idx = ctx.layout, ctx.idx
    n = len(layout.pixels)
    frame = _blank(n, P["background"])
    if not idx:
        return frame
    if state.get("sig") != _signature(layout, idx) or state.get("seed") != P["seed"]:
        _embers_reset(state, layout, idx, P)
    _embers_advance(state, P, layout, min(MAX_DT, max(0.0, ctx.dt or 0.0)))
    _embers_draw(frame, state, P, layout)
    return _finish(frame, idx)


def _embers_advance(state, P, layout, dt):
    """Spawn, move, cool and cull the sparks over dt seconds."""
    rng = state["rng"]
    sparks = state["sparks"]

    # --- spawn
    state["acc"] += P["rate"] * dt
    while state["acc"] >= 1.0:
        state["acc"] -= 1.0
        if len(sparks) < 400:
            _spawn(state, P)

    # --- physics
    steps = max(1, math.ceil(dt / STEP)) if dt > 0 else 0
    h = dt / steps if steps else 0.0
    for _ in range(steps):
        _embers_step(state, P, layout, h, rng)

    # --- cool, cull
    life = P["life"]
    rest_life = life * (0.4 + 1.2 * P["glow"])
    x0, x1, y0 = state["box"]
    keep = []
    for sp in sparks:
        if sp.mode == REST:
            sp.heat -= dt / rest_life
            sp.landed += dt
        else:
            sp.heat -= dt / life
        if sp.heat > 0 and (sp.mode != FALL or (sp.y > y0 and x0 < sp.x < x1)):
            keep.append(sp)
    sparks[:] = keep


def _embers_draw(frame, state, P, layout):
    sparks = state["sparks"]
    tracks, allowed, cols = state["tracks"], state["allowed"], P["colors"]
    glow = P["glow"]
    for sp in sparks:
        heat = sp.heat
        col = palette(cols, 1.0 - heat, cyclic=False)
        fl = 0.75 + 0.25 * hash01(sp.k, int(sp.heat * 60))
        b = min(1.0, heat * 2.5) * fl
        c = (col[0] * b, col[1] * b, col[2] * b)
        if sp.mode == FALL:
            geo.splat(frame, layout, sp.x, sp.y, c, 0.9, allowed)
            continue
        if sp.tr < 0:
            i = int(sp.s)
            a = frame[i]
            frame[i] = (a[0] + c[0], a[1] + c[1], a[2] + c[2])
            continue
        pth = tracks[sp.tr]
        if sp.mode == SLIDE:
            geo.splat_path(frame, pth, sp.s, c, 1.0)
            # short streak behind a sliding spark
            geo.splat_path(frame, pth, sp.s - sp.v * 0.06, (c[0] * 0.35, c[1] * 0.35, c[2] * 0.35), 1.0)
        else:
            geo.splat_path(frame, pth, sp.s, c, 1.0)
            if sp.landed < 0.3 and glow > 0:
                # landing flash: a brief bloom onto the neighbours
                fb = glow * (1 - sp.landed / 0.3)
                fc = lerp(col, (1.0, 0.9, 0.6), 0.5)
                geo.splat_path(frame, pth, sp.s, (fc[0] * fb, fc[1] * fb, fc[2] * fb), 2.2)
