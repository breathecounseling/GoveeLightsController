"""Little moments the visitor page can send to the house (also usable as layers).

    flurry         A snow flurry: a burst of flakes tumbles down over the whole house,
                   each aimed at one bulb, swaying as it falls (anti-aliased, so it
                   glides between bulbs) and glinting briefly where it lands before it
                   melts. One burst of N seconds, or a continuous flurry.
    colour_ripple  A ring of colour spreads out from the front gable's apex (the highest
                   bulb) across the whole house, with a bright leading edge and a
                   sparkling wake that holds the colour for a moment and then fades.
                   One burst of N seconds, or repeating.

Both are @frame_effect generators. Everything is a pure function of the time since the
layer started, so any frame step (0, huge, stalled) is safe; the flake schedule comes
from random.Random(seed, cycle) and only the current and previous cycles are kept, so
memory is bounded however long they run. Used by the controller as "over" overlays
(their brightness is their opacity), so a visitor's moment shows clearly on top of
whatever is playing.
"""
from __future__ import annotations

import math
import random

from .. import geometry as geo
from ..color import BLACK, hash01, lerp
from ..effects import Param, frame_effect

MAX_FLAKES = 360              # per cycle: bounds memory and per-frame work
WHITE = (1.0, 1.0, 1.0)


def _sig(ctx) -> tuple:
    L, idx = ctx.layout, ctx.idx
    return (id(L), len(L.pixels), len(idx), idx[0] if idx else -1, idx[-1] if idx else -1, id(idx))


def _smooth(x: float) -> float:
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    return x * x * (3 - 2 * x)


def _elapsed(t: float, st: dict) -> float:
    """Seconds since this layer first rendered (the burst starts on its first frame)."""
    if "t0" not in st or not isinstance(t, (int, float)) or t != t:
        st["t0"] = t if isinstance(t, (int, float)) and t == t else 0.0
    return max(0.0, t - st["t0"])


def _clamp(frame: list, idx) -> list:
    for i in idx:
        r, g, b = frame[i]
        if r > 1.0 or g > 1.0 or b > 1.0:
            frame[i] = (min(1.0, r), min(1.0, g), min(1.0, b))
    return frame


# ====================================================================== flurry
FALL = 3.2                    # bulbs per second at fall speed 1


def _flurry_cycle(st, ctx, P, k: int, period: float) -> list:
    """The flakes of cycle k: (land, x, y_land, sway, phase, speed, tint, glow, spawn), times
    relative to the cycle start. Every flake starts above the roof at its spawn time and
    lands on its own bulb. Only the cycles still on screen are cached (bounded)."""
    cyc = st.setdefault("cycles", {})
    keep = st.get("keep", 1)
    key = (k, P["amount"], P["seconds"], P["fall"], P["seed"], P["repeat"])
    for kk in list(cyc):
        if not (k - keep <= kk[0] <= k) or kk[1:] != key[1:]:
            del cyc[kk]
    if key in cyc:
        return cyc[key]
    px, idx, top = ctx.layout.pixels, ctx.idx, st["top"]
    rng = random.Random(P["seed"] * 7919 + k * 104729 + 17)
    land_end = max(0.6, period - 1.3)              # leave time to melt before a burst ends
    span = period if P["repeat"] else land_end
    n = min(MAX_FLAKES, max(1, int(round(P["amount"] * span))))
    flakes = []
    for _ in range(n):
        i = idx[rng.randrange(len(idx))]
        p = px[i]
        v = FALL * P["fall"] * rng.uniform(0.75, 1.3)
        fall_t = (top - p.y) / v
        if P["repeat"]:
            spawn = rng.uniform(0, period)
        else:                                       # land before the burst's end where possible
            spawn = rng.uniform(0, max(0.0, land_end - fall_t) * 0.97 + 0.03)
        flakes.append((spawn + fall_t, p.x + rng.uniform(-0.25, 0.25), p.y,
                       rng.uniform(0.4, 1.3), rng.uniform(0, 2 * math.pi),
                       v, rng.random(), rng.uniform(0.7, 1.4), spawn))
    flakes.sort()
    cyc[key] = flakes
    return flakes


@frame_effect("flurry", "Snow flurry", [
    Param("color", "color", "#ffffff", "Flake colour", help="white reads best; a touch of blue looks icy"),
    Param("amount", "number", 45, "Flakes per second", 5, 120, 1, help="how thick the flurry is"),
    Param("seconds", "number", 6, "Burst length (s)", 2, 30, 0.5,
          help="how long one flurry lasts, from the first flake to the last melting"),
    Param("repeat", "bool", True, "Keep snowing",
          help="on: a new flurry follows each one; off: a single burst (what visitors send)"),
    Param("fall", "number", 1.0, "Fall speed", 0.3, 3, 0.05),
    Param("wind", "number", 0.4, "Wind", -3, 3, 0.1, help="sideways drift in bulbs per second; negative blows the other way"),
    Param("seed", "int", 3, "Seed", 0, 9999, 1),
], "A flurry of snow tumbling down over the whole house: flakes sway as they fall and "
   "glint where they land on the roofline before melting.", group="Weather")
def flurry(t, ctx, P, st):
    L, idx = ctx.layout, ctx.idx
    frame = [BLACK] * len(L.pixels)
    if not idx:
        return frame
    sig = _sig(ctx)
    if st.get("sig") != sig:
        st["sig"] = sig
        st["cycles"] = {}
        st["allowed"] = set(idx)
        st["top"] = max(L.pixels[i].y for i in idx) + 1.5
    lo_y = min(L.pixels[i].y for i in idx)
    slowest = (st["top"] - lo_y) / (FALL * P["fall"] * 0.75) + 1.5     # longest fall + melt
    st["keep"] = min(8, math.ceil(slowest / float(P["seconds"])))
    e = _elapsed(t, st)
    period = float(P["seconds"])
    k = int(e // period) if P["repeat"] else 0
    if not P["repeat"] and e > period:
        return frame
    allowed, top, wind = st["allowed"], st["top"], P["wind"]
    base = P["color"]
    icy = lerp(base, (0.72, 0.84, 1.0), 0.5)
    for kk in range(k - st["keep"], k + 1):
        if kk < 0:
            continue
        c0 = kk * period
        tl = e - c0                                  # time within that cycle
        fade = 1.0
        if not P["repeat"]:
            fade = _smooth((period - tl) / 0.8)      # the whole burst ends gently
        for (land, x, yl, sway, ph, v, tint, glow, spawn) in _flurry_cycle(st, ctx, P, kk, period):
            age = tl - land                          # < 0 falling, >= 0 landed
            if tl < spawn or age >= glow:
                continue
            col = lerp(base, icy, tint * 0.6)
            if age < 0:
                y = yl - age * v                     # height above the landing spot
                h = y - yl
                sx = x - age * wind * 0.5 + sway * math.sin(ph + tl * 2.3) * min(1.0, h / 2.5)
                appear = _smooth((top - y) / 1.5)    # drifts in from above the roof
                w = 0.85 * appear * fade
                geo.splat(frame, L, sx, y, (col[0] * w, col[1] * w, col[2] * w), 1.15, allowed, "max")
            elif age < glow:
                g = math.exp(-age / (glow * 0.35)) * (1.0 - age / glow)
                g *= 0.85 + 0.15 * math.sin(ph * 3 + age * 25)       # a little twinkle
                w = g * fade
                geo.splat(frame, L, x, yl, (col[0] * w, col[1] * w, col[2] * w), 0.9, allowed, "max")
    return _clamp(frame, idx)


# ====================================================================== colour ripple
def _origin(L) -> tuple[float, float]:
    """The front gable's apex: the bulbs crowning the zigzag (the Peak), or on a layout
    without one the highest bulb (nearest the middle on a tie)."""
    px = L.pixels
    try:
        top = geo.star(L, "Peak", 2)
    except Exception:  # noqa: BLE001 - a layout without that run
        top = []
    if top:
        return sum(px[i].x for i in top) / len(top), sum(px[i].y for i in top) / len(top)
    xs = [p.x for p in px]
    mid = (min(xs) + max(xs)) / 2
    p = max(px, key=lambda p: (round(p.y, 3), -abs(p.x - mid)))
    return p.x, p.y


@frame_effect("colour_ripple", "Colour ripple", [
    Param("color", "color", "#ff1e3c", "Colour"),
    Param("seconds", "number", 4, "Length (s)", 1.5, 20, 0.5, help="from the first spark at the apex to the last fade"),
    Param("repeat", "bool", True, "Repeat", help="off: a single ripple (what visitors send)"),
    Param("width", "number", 3, "Ring width (bulbs)", 1, 10, 0.5),
    Param("wake", "number", 0.85, "Colour left behind", 0, 1, 0.05,
          help="how much colour stays on the house after the ring passes"),
    Param("seed", "int", 5, "Seed", 0, 9999, 1),
], "A ring of colour spreading from the front gable's apex across the whole house, with a "
   "bright edge and a sparkling wake that fades away.", group="Across the house (2D)")
def colour_ripple(t, ctx, P, st):
    L, idx = ctx.layout, ctx.idx
    n = len(L.pixels)
    frame = [BLACK] * n
    if not idx:
        return frame
    sig = _sig(ctx)
    if st.get("sig") != sig:
        ox, oy = _origin(L)
        px = L.pixels
        d = {i: math.hypot(px[i].x - ox, px[i].y - oy) for i in idx}
        st.update(sig=sig, dist=d, reach=max(d.values()) + 1.0)
    e = _elapsed(t, st)
    T = float(P["seconds"])
    if P["repeat"]:
        e %= T
    elif e >= T:
        return frame
    reach, dist = st["reach"], st["dist"]
    travel = T * 0.55                               # the ring crosses the house in about half the time
    k = min(1.0, e / travel)
    front = reach * (1 - (1 - k) ** 1.7) + (e - travel) * 4.0 * (k >= 1)   # fast start, eases out
    width = P["width"]
    fade = _smooth((T - e) / (T * 0.35))           # everything fades over the last third
    col = P["color"]
    hot = lerp(col, WHITE, 0.45)
    wake = P["wake"]
    seed = P["seed"]
    cyc = int(t // T) if P["repeat"] else 0
    for i, di in dist.items():
        behind = front - di                         # > 0: the ring has passed this bulb
        if behind < -width:
            continue
        if behind < 0:                              # the soft leading slope
            a = _smooth(1 + behind / width)
            c = lerp(col, hot, a)
            w = a
        else:
            edge = math.exp(-behind / (width * 0.8))
            lin = math.exp(-behind / (reach * 0.45))
            w = edge + (1 - edge) * wake * (0.75 + 0.25 * lin)
            c = lerp(col, hot, edge * 0.8)
            if behind > width:                      # sparkles twinkle in the wake
                g = hash01(i, int(e * 9), seed, cyc)
                if g > 0.9:
                    c, w = lerp(col, WHITE, 0.6), max(w, 0.95)
        w *= fade
        frame[i] = (c[0] * w, c[1] * w, c[2] * w)
    return frame


# ====================================================================== veil
@frame_effect("veil", "Dim veil", [
    Param("depth", "number", 0.55, "How dark", 0, 0.95, 0.05, help="how far the layers below dim at the middle"),
    Param("seconds", "number", 6, "Length (s)", 1, 60, 0.5),
    Param("fade", "number", 1.2, "Fade in/out (s)", 0.1, 10, 0.1),
    Param("repeat", "bool", False, "Repeat"),
], "A grey veil for the Multiply blend: the layers below dim smoothly, hold, then brighten "
   "again. Visitor snow uses it so white flakes show over a bright white scene.", group="Utility")
def veil(t, ctx, P, st):
    L, idx = ctx.layout, ctx.idx
    frame = [BLACK] * len(L.pixels)
    e = _elapsed(t, st)
    T = float(P["seconds"])
    if P["repeat"]:
        e %= T
    f = min(float(P["fade"]), T / 2)
    k = _smooth(min(e, T - e) / f) if e < T else 0.0
    g = 1.0 - P["depth"] * k
    for i in idx:
        frame[i] = (g, g, g)
    return frame
