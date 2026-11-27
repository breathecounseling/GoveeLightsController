"""Showpieces that use the shape of the house: the zigzag as a little triangular canvas,
and paths that cross from one run to the next.

    fan           The zigzag as a triangle seen from its bottom centre: a turkey fanning
                  its tail (feathers open, shimmer and glint, a little turkey in front),
                  a harvest sunset (the sun sinks behind the rows, the sky glows on), or
                  a slowly turning sunburst. The rest of the target glows warm.
    falling_star  A falling star in 2D across the house: it enters above the zigzag,
                  streaks down through its rows, crosses the roofline and lands on the
                  lowest run (the porch and garage) with a burst of glitter. Lights
                  whatever bulbs it passes, so a newly placed run joins in on its own.
    sleigh_tour   Santa's sleigh on a grand tour: along the whole roofline one way, a
                  swoop down, back along the lower roofline (porch, garage), and up again,
                  round and round. Rudolph's red nose leads, a gold trail glitters behind,
                  and stardust drifts off the gable onto the zigzag below. Any other
                  roofline run in the target (a fourth box, say) joins the loop.
    candlelight   Every bulb a candle: warm white made from RGB that breathes and wavers
                  gently (no flicker), with a soft swell of light that flows out from the
                  gable apex every so often: a quiet moment of thanks.

All are @frame_effect generators: memory lives in the layer's state dict, randomness is
hashed or comes from a random.Random kept there (deterministic for a seed and a frame
sequence), lists are bounded, and a stalled or reversed clock is handled.

Colour craft for real bulbs (RGB only over realtime, no white diode): warm whites are
built from full red, ~60 % green and ~25 % blue; the darkest levels used for glows stay
above about 12 % so they don't band, and every moving thing is anti-aliased between
bulbs so nothing hops or flickers at 20-25 fps.
"""
from __future__ import annotations

import math
import random

from .. import geometry as geo
from ..color import BLACK, hash01, lerp, noise2
from ..effects import Param, frame_effect
from ..sparkle import envelope, glint, wobble

TAU = 2 * math.pi
WARM = (1.0, 0.6, 0.24)
GOLD = (1.0, 0.62, 0.12)
MAX_DT = 0.1


def _smooth(x: float) -> float:
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    return x * x * (3 - 2 * x)


def _dt(ctx) -> float:
    d = ctx.dt or 0.0
    if not (d > 0) or d != d:
        return 0.0
    return min(MAX_DT, d)


def _clamp(frame: list, idx) -> list:
    for i in idx:
        r, g, b = frame[i]
        if not (0.0 <= r <= 1.0 and 0.0 <= g <= 1.0 and 0.0 <= b <= 1.0):
            frame[i] = (min(1.0, max(0.0, r)), min(1.0, max(0.0, g)), min(1.0, max(0.0, b)))
    return frame


def _scale(c, v):
    return (c[0] * v, c[1] * v, c[2] * v)


def _add(frame, i, c, w=1.0):
    a = frame[i]
    frame[i] = (a[0] + c[0] * w, a[1] + c[1] * w, a[2] + c[2] * w)


def _over(frame, i, c, w=1.0):
    """Light over a coloured sky: the sky shows through only as much as the light leaves
    room for (added onto a blue sky, orange turns pink)."""
    a = frame[i]
    r, g, b = c[0] * w, c[1] * w, c[2] * w
    k = 1.0 - min(1.0, max(r, g, b))
    frame[i] = (a[0] * k + r, a[1] * k + g, a[2] * k + b)


def _sig(ctx) -> tuple:
    L, idx = ctx.layout, ctx.idx
    return (id(L), len(L.pixels), len(idx), hash(tuple(idx)))


# ====================================================================== the canvas
def dead_in(layout, group: list[int]) -> list[tuple[float, float]]:
    """Where the dead bulbs are that belong with a group of one run's bulbs: those of its
    run next to the group (or next to such a dead bulb), so a picture keeps its outline and
    scale round them. Empty when nothing is marked dead."""
    dead_at = getattr(layout, "dead_at", None)
    if not dead_at or not group:
        return []
    px = layout.pixels
    dead = dead_at.get(px[group[0]].mac)
    if not dead:
        return []
    locs = {px[i].local for i in group}
    out, grew = {}, True
    while grew:
        grew = False
        for k, xy in dead.items():
            if k not in out and (k - 1 in locs or k + 1 in locs):
                out[k] = xy
                locs.add(k)
                grew = True
    return [out[k] for k in sorted(out)]


def canvas(layout, idx: list[int]) -> dict:
    """The zigzag in a target as a triangle: per bulb (u, v) with u -1..1 across and
    v 0..1 up, plus the bulbs that are not part of it ("rest"). With no zigzag in the
    target the whole target is the canvas (its bounding box)."""
    key = ("showpiece-canvas", tuple(idx))
    c = geo._cache(layout)
    if key in c:
        return c[key]
    px = layout.pixels
    groups: dict[str, list[int]] = {}
    for i in idx:
        groups.setdefault(px[i].run, []).append(i)
    zig = []
    for g in sorted(groups.values(), key=len, reverse=True):
        if geo._rows_by_reversal(px, g):
            zig = g
            break
    if not zig:
        zig = list(idx)
    zset = set(zig)
    uv = {}
    if zig:
        # the scale comes from the zigzag's own run: a run that continues it (the Tip on
        # top of the Peak) sits above, at v > 1, so pictures keep their size and shape
        base = [i for i in zig if not getattr(px[i], "ext", False)] or zig
        gone = dead_in(layout, base)            # dead bulbs still mark the picture's outline
        xs = [px[i].x for i in base] + [x for x, _ in gone]
        ys = [px[i].y for i in base] + [y for _, y in gone]
        cx, hw = (min(xs) + max(xs)) / 2, max(0.5, (max(xs) - min(xs)) / 2)
        y0, H = min(ys), max(0.5, max(ys) - min(ys))
        for i in zig:
            uv[i] = ((px[i].x - cx) / hw, (px[i].y - y0) / H)
        apex = (cx, max(px[i].y for i in zig))
        # how high the triangle's tip would be (in canvas heights): a zigzag is usually a
        # trapezoid (its top row is still several bulbs wide), so |u| + v / k <= 1
        gone = [(x, y, (x - cx) / hw, (y - y0) / H) for x, y in gone]
        allv = list(uv.values()) + [(u, v) for _, _, u, v in gone]
        k = max([v / (1 - abs(u)) for u, v in allv if abs(u) < 0.9] or [1.0])
        k = max(1.0, min(4.0, k))
    else:
        apex, y0, H, k, gone = (0.0, 0.0), 0.0, 1.0, 1.0, []
    out = {"zig": zig, "uv": uv, "rest": [i for i in idx if i not in zset], "apex": apex, "k": k,
           "y0": y0, "H": H, "tip": [i for i in zig if getattr(px[i], "ext", False)],
           "gone": gone,                           # gone: (x, y, u, v) of the zigzag's dead bulbs
           "si": {i: px[i].si for i in idx}}       # per bulb its stable seed (Pixel.si)
    c[key] = out
    return out


def _apex(layout, idx: list[int], count: int = 2) -> list[int]:
    """The bulbs crowning the target's zigzag (the gable apex over it), if the target has
    them; else the target's highest bulbs."""
    px = layout.pixels
    cv = canvas(layout, idx)
    zs = set(cv["zig"])
    pool = []
    if cv["zig"] and len(zs) < len(idx):
        xs = [px[i].x for i in cv["zig"]]
        top = max(px[i].y for i in cv["zig"])
        pool = [i for i in idx if i not in zs and px[i].y > top and min(xs) - 1 <= px[i].x <= max(xs) + 1]
    pool = pool or list(idx)
    mid = cv["apex"][0]
    return sorted(pool, key=lambda i: (-px[i].y, abs(px[i].x - mid)))[:count]


# ====================================================================== fan
FAN_STYLES = ["turkey", "sunset", "sunburst"]
PIVOT = 0.14          # the fan's pivot sits this far (in canvas heights) below the bottom row


def _polar(u: float, v: float, k: float = 1.0) -> tuple[float, float]:
    """(angle 0..1 from right to left, radius 0..1 where 1 is the canvas' edge)."""
    dy = v + PIVOT
    a = math.atan2(dy, u)
    ca, sa = abs(math.cos(a)), math.sin(a)
    edge = (1.0 + PIVOT / k) / max(0.2, ca + sa / k)     # the edge |u| + v / k = 1 along this ray
    return min(1.0, max(0.0, a / math.pi)), math.hypot(u, dy) / edge


@frame_effect("fan", "Turkey fan & harvest sun", [
    Param("style", "choice", "turkey", "Picture", options=FAN_STYLES,
          help="turkey: a turkey fanning its tail feathers; sunset: the sun sinks behind the rows "
               "and the sky glows on; sunburst: golden rays turning slowly"),
    Param("colors", "colors", ["#ff0800", "#ff5000", "#ffa800", "#d81000"], "Feather / ray colours",
          help="from the outside in, mirrored on both sides; deep autumn colours read best on bulbs"),
    Param("feathers", "int", 7, "Feathers / rays", 3, 16, 1),
    Param("tip", "color", "#ffe0a0", "Feather tips", help="the pale edge at the end of every feather"),
    Param("body", "color", "#5a1400", "Turkey body", help="a dark chestnut; bulbs show brown as deep orange"),
    Param("wattle", "color", "#ff0000", "Wattle"),
    Param("level", "number", 0.9, "Brightness", 0.2, 1, 0.01),
    Param("intro", "number", 4, "Opening (s)", 0, 20, 0.5,
          help="the fan opens (turkey and sunburst) or the sun starts high (sunset) when the scene starts"),
    Param("sink", "number", 50, "Sunset takes (s)", 5, 600, 1, help="sunset: from high in the sky to gone"),
    Param("speed", "number", 0.6, "Ripple / turn speed", 0, 4, 0.05,
          help="turkey: how often a shimmer runs out along the feathers; sunburst: how fast the rays turn"),
    Param("glints", "number", 0.25, "Tip glints per second", 0, 2, 0.01),
    Param("ray2", "color", "#b02800", "Sunburst: between the rays",
          help="the colour of every other ray (white with red rays makes a peppermint swirl)"),
    Param("hub", "color", "#ffc860", "Sunburst: centre"),
    Param("glow", "color", "#ff8a20", "Outline glow", help="the rest of the target"),
    Param("glow_level", "number", 0.35, "Outline glow level", 0, 1, 0.01),
    Param("seed", "int", 5, "Seed", 0, 9999, 1),
], "The zigzag as a little triangular picture seen from its bottom centre: a turkey fanning its "
   "tail of autumn feathers with pale tips that glint (a little turkey in front, red wattle and "
   "all), a harvest sunset that sinks behind the rows while the sky glows on, or a slowly turning "
   "sunburst. The rest of the target (e.g. the gable outline) glows warm. Target 'Whole front "
   "gable' or the Peak.", group="Scenes")
def fan(t, ctx, P, st):
    L = ctx.layout
    n = len(L.pixels)
    frame = [BLACK] * n
    idx = [i for i in ctx.idx if 0 <= i < n]
    if not idx:
        return frame
    cv = canvas(L, idx)
    clock = ctx.clock
    if "start" not in st or clock < st["start"] - 1e-6:
        st["start"] = clock
    age = clock - st["start"]
    style, lvl, seed = P["style"], P["level"], P["seed"]
    cols = P["colors"] or [GOLD]
    nf = max(3, int(P["feathers"]))
    D = P["intro"]
    opened = _smooth(age / D) if D > 0 else 1.0
    polar = st.get("polar")
    if polar is None or st.get("polar_key") != id(cv):
        polar = st["polar"] = {i: _polar(u, v, cv["k"]) for i, (u, v) in cv["uv"].items()}
        st["polar_key"] = id(cv)

    if style == "turkey":
        _turkey(frame, cv, polar, P, clock, age, opened, cols, nf, lvl, seed)
    elif style == "sunset":
        _sunset(frame, cv, polar, P, clock, age, lvl, seed)
    else:
        _sunburst(frame, cv, polar, P, clock, opened, cols, nf, lvl, seed)

    gl, gv = P["glow"], P["glow_level"]
    if gv > 0:
        ax, ay = cv["apex"]
        px = L.pixels
        if style == "sunset":
            after = _sunset_phase(age, P)[1]
        for i in cv["rest"]:
            w = 0.8 + 0.2 * wobble(cv["si"][i], clock, 0.25, seed)
            if style == "sunset":                    # the outline carries on the sky around the zigzag
                f = (px[i].y - cv["y0"]) / cv["H"]
                frame[i] = _scale(_sky(max(0.0, f), after), min(1.0, gv * 1.4) * w)
                continue
            d = math.hypot(px[i].x - ax, px[i].y - ay)
            v = gv * w * (1.0 + 0.6 * max(0.0, 1 - d / 8))
            frame[i] = _scale(gl, min(1.0, v) * (0.3 + 0.7 * opened))
    return _clamp(frame, idx)


def _turkey(frame, cv, polar, P, clock, age, opened, cols, nf, lvl, seed):
    tip, body, wattle = P["tip"], P["body"], P["wattle"]
    grate = P["glints"]
    ripple_every = 1.0 / P["speed"] if P["speed"] > 0 else 0.0
    rip = (age % ripple_every) / ripple_every * 1.6 - 0.3 if ripple_every else -9.0
    for i, (u, v) in cv["uv"].items():
        a, r = polar[i]
        # the turkey in front: a dark chestnut body low in the middle, a pale head on a
        # neck above it with the red wattle hanging beside the beak
        bx, by = u / 0.27, (v + 0.05) / 0.55
        hx, hy = u / 0.15, (v - 0.68) / 0.24
        wx, wy = (u - 0.12) / 0.1, (v - 0.47) / 0.17
        if wx * wx + wy * wy <= 1.0:
            frame[i] = _scale(wattle, lvl)
            continue
        hd = hx * hx + hy * hy
        if hd <= 1.0:
            frame[i] = _scale((1.0, 0.72, 0.42), lvl)
            continue
        if bx * bx + by * by <= 1.0 or (abs(u) < 0.08 and v < 0.6):
            sh = 0.75 + 0.25 * wobble(cv["si"][i], clock, 0.4, seed)
            frame[i] = _scale(body, lvl * sh)
            continue
        if hd <= 2.2:                                          # a dark ring sets the head off the fan
            frame[i] = _scale(body, lvl * 0.25)
            continue
        # feathers: sectors fanning out from the pivot, opening from the middle outwards
        spread = abs(a - 0.5) * 2                              # 0 straight up, 1 flat
        if spread > opened * 1.02:
            continue
        f = a * nf
        k = int(min(nf - 1, f))
        edge = min(f - k, k + 1 - f)                           # 0 at a feather's edge
        c = cols[min(k, nf - 1 - k) % len(cols)]               # mirrored: the tail is symmetric
        sep = _smooth(edge / 0.2)                              # a dark vein between feathers
        if r < 0.5:                                            # the downy base: deeper
            c = _scale(c, 0.6 + 0.4 * r / 0.5)
        elif r > 0.88:                                         # the pale tip with its glint
            c = lerp(c, tip, 0.75)
            if grate > 0:
                g = glint(cv["si"][i], clock, grate, 0.04, 0.35, seed, 1)[0]
                c = lerp(c, (1.0, 0.95, 0.8), g * 0.8)
        v_ = lvl * (0.22 + 0.78 * sep)
        d = r - rip                                            # a shimmer runs out along the feathers
        if -0.25 < d < 0.25:
            v_ *= 1.0 + 0.35 * (1 - abs(d) / 0.25)
        frame[i] = _scale(c, min(1.0, v_))


SKY = [(1.0, 0.55, 0.05), (1.0, 0.3, 0.0), (1.0, 0.08, 0.02), (0.85, 0.0, 0.22), (0.45, 0.0, 0.5),
       (0.22, 0.02, 0.6)]
SKY_LATE = [(1.0, 0.26, 0.0), (0.95, 0.08, 0.0), (0.8, 0.0, 0.16), (0.5, 0.0, 0.36), (0.26, 0.0, 0.5),
            (0.12, 0.02, 0.5)]


def _sky(f: float, after: float):
    """The sunset sky at height f (0 horizon .. 1 top of the zigzag, more above)."""
    k = min(0.999, max(0.0, f / 1.25)) * (len(SKY) - 1)
    j = int(k)
    return lerp(lerp(SKY[j], SKY[j + 1], k - j), lerp(SKY_LATE[j], SKY_LATE[j + 1], k - j), after)


def _sunset_phase(age: float, P) -> tuple[float, float]:
    sink = max(1.0, P["sink"])
    sun_v = 0.5 - 1.0 * _smooth(age / sink)                   # the sun's centre, in canvas heights
    return sun_v, _smooth((age - sink * 0.75) / (sink * 0.4))  # the afterglow takes over


def _sunset(frame, cv, polar, P, clock, age, lvl, seed):
    sun_v, after = _sunset_phase(age, P)
    for i, (u, v) in cv["uv"].items():
        f = max(0.0, v + 0.1 * (noise2(u * 1.5 + clock * 0.05, v * 2, seed) - 0.5))
        c = _sky(f, after)
        sv = 0.78 + 0.22 * (1 - min(1.0, f))
        # the sun: a hot gold disc with an orange rim and a soft halo; its bottom slips
        # below the lowest row as it sets
        d = math.hypot(u * 1.1, (v - sun_v) * 1.5)
        if d < 0.34:
            c, sv = lerp((1.0, 0.8, 0.42), (1.0, 0.45, 0.0), _smooth((d - 0.14) / 0.2)), 1.0
        elif d < 0.8:
            h = (1 - (d - 0.34) / 0.46) ** 2 * (1 - after * 0.6)
            c = lerp(c, (1.0, 0.45, 0.02), h)
            sv = sv + (1 - sv) * h
        frame[i] = _scale(c, lvl * sv)


def _sunburst(frame, cv, polar, P, clock, opened, cols, nf, lvl, seed):
    turn = clock * P["speed"] * 0.08
    for i, (u, v) in cv["uv"].items():
        a, r = polar[i]
        if r < 0.28:
            frame[i] = _scale(P["hub"], lvl)
            continue
        if abs(a - 0.5) * 2 > opened * 1.02:
            continue
        f = (a + turn) * nf * 2
        k = math.floor(f)
        e = min(f - k, k + 1 - f)
        def ray(j):
            return cols[(j // 2) % len(cols)] if j % 2 == 0 else P["ray2"]
        # soft edges: a bulb on the line between two rays is a mix of both, so a turning
        # ray glides from bulb to bulb instead of stepping (no flicker on real bulbs)
        w = 0.5 + 0.5 * _smooth(e / 0.35)
        c = lerp(ray(k + (1 if f - k > 0.5 else -1)), ray(k), w)
        frame[i] = _scale(c, lvl * (0.8 + 0.2 * _smooth(e / 0.35)) * (1.05 - 0.25 * r))


# ====================================================================== falling star
FS_MAX_SPARKS = 160


@frame_effect("falling_star", "Falling star (whole house)", [
    Param("every", "number", 7, "Star every (s)", 2, 120, 0.5, help="seconds between falling stars"),
    Param("speed", "number", 13, "Speed (bulbs/s)", 4, 80, 1),
    Param("tail", "number", 12, "Tail length (bulbs)", 2, 30, 0.5),
    Param("head", "color", "#fff4e0", "Star colour"),
    Param("tail_color", "color", "#90b0ff", "Tail colour", help="the head's colour fades into this along the tail"),
    Param("burst_colors", "colors", ["#ffd040", "#ffffff", "#ffb000"], "Landing glitter"),
    Param("burst", "number", 1.0, "Landing burst", 0, 2, 0.05, help="how big the glitter is where it lands; 0 = none"),
    Param("sparks", "number", 1.0, "Sparks shed", 0, 3, 0.05, help="glints left behind where the star crossed"),
    Param("background", "color", "#000c60", "Sky colour"),
    Param("stars", "number", 0.6, "Background twinkle", 0, 2, 0.05),
    Param("seed", "int", 3, "Seed", 0, 9999, 1),
], "A falling star crosses the whole house: it enters above the zigzag, streaks down through its "
   "rows with a fading blue-white tail, crosses the roofline and lands on the lowest run (porch "
   "and garage) in a burst of gold glitter, shedding sparks on every line it crosses. Each one "
   "falls a little differently, alternately from the left and the right. Target the whole house.",
   group="Scenes")
def falling_star(t, ctx, P, st):
    L = ctx.layout
    px = L.pixels
    n = len(px)
    frame = [BLACK] * n
    idx = [i for i in ctx.idx if 0 <= i < n]
    if not idx:
        return frame
    sky = P["background"]
    for i in idx:
        frame[i] = sky
    if st.get("key") != _sig(ctx):
        st["key"] = _sig(ctx)
        st["allowed"] = set(idx)
        cv = canvas(L, idx)
        zig = cv["zig"] if cv["zig"] and len(cv["zig"]) < len(idx) else idx
        zx = [px[i].x for i in zig]
        zy = [px[i].y for i in zig]
        st["aim"] = ((min(zx) + max(zx)) / 2, (min(zy) + max(zy)) / 2, (max(zx) - min(zx)) / 2)
        st["top"] = max(px[i].y for i in idx) + 1.5
        # land on the lowest line of the house; a run that has no mapped shape yet (it sits
        # on a placeholder line below the house) is not where the porch really is
        shaped = {r.name for r in L.runs if r.geometry}
        ground = [i for i in idx if px[i].run in shaped] or idx
        lo = min(px[i].y for i in ground)
        st["floor"] = sorted((i for i in ground if px[i].y <= lo + 1.5), key=lambda i: px[i].x)
        st["stars"], st["sparks"], st["fall"], st["burst"] = [], [], None, None
    allowed = st["allowed"]
    clock = ctx.clock
    seed = P["seed"]
    if st.get("seed") != seed or "rng" not in st:
        st["seed"], st["rng"] = seed, random.Random(seed * 4421 + 17)
    rng = st["rng"]
    if P["stars"] > 0:
        rate = 0.05 * P["stars"]
        for i in idx:
            g, _ = glint(i, clock, rate, 0.6, 0.9, seed, 1)
            if g > 0.02:
                _add(frame, i, (0.75, 0.85, 1.0), 0.4 * g)

    every = max(1.0, P["every"])
    nxt = st.get("next")
    if nxt is None or nxt > clock + every * 1.2 + 1 or nxt < clock - 3 * every:
        nxt = st["next"] = clock + 0.8
    if st["fall"] is None and clock >= nxt:
        k = st.get("count", 0)
        st["count"] = k + 1
        st["next"] = nxt + every * (0.85 + 0.3 * rng.random())
        while st["next"] <= clock:
            st["next"] += every
        ax, ay, hw = st["aim"]
        side = 1 if k % 2 == 0 else -1
        floor = st["floor"]
        # land on the lowest line, on the far side of the zigzag from where it entered
        cand = [i for i in floor if (px[i].x - ax) * side < 0 and abs(px[i].x - ax) < hw * 2.5 + 6] or floor
        e = cand[rng.randrange(len(cand))]
        ex, ey = px[e].x, px[e].y
        # enter above, on the other side, aimed through (near) the middle of the zigzag
        mx, my = ax + (rng.random() - 0.5) * hw * 0.5, ay + (rng.random() - 0.5) * 0.6
        sy = st["top"]
        f = (sy - my) / max(0.5, my - ey)
        sx = mx + (mx - ex) * f
        st["fall"] = {"t": 0.0, "p": [(sx, sy), (mx, my), (ex, ey)], "land": e}
    dt = _dt(ctx)
    fl = st["fall"]
    head = None
    if fl is not None:
        (x0, y0), (x1, y1), (x2, y2) = fl["p"]
        seg = math.hypot(x1 - x0, y1 - y0) + math.hypot(x2 - x1, y2 - y1)
        fl["t"] += dt * P["speed"] / max(1.0, seg)
        tail = P["tail"] / max(1.0, seg)

        def at(q):
            q = min(1.0, max(0.0, q))
            a, b = (1 - q) ** 2, 2 * q * (1 - q)
            return (a * x0 + b * x1 + q * q * x2, a * y0 + b * y1 + q * q * y2)

        q = fl["t"]
        hc, tc = P["head"], P["tail_color"]
        steps = max(4, int(P["tail"] * 2.5))
        glow = [BLACK] * n                           # the tail, brightest sample per bulb...
        for s in range(steps, -1, -1):
            u = s / steps
            qq = q - u * tail
            if qq < 0 or qq > 1:
                continue
            x, y = at(qq)
            w = (1 - u) ** 1.3
            c = lerp(hc, tc, min(1.0, u * 1.4))
            geo.splat(glow, L, x, y, _scale(c, w * (1.0 if s == 0 else 0.85)), 1.3 if s == 0 else 1.1,
                      allowed, mode="max")
        for i in idx:                                # ...over the sky
            g = glow[i]
            if g[0] or g[1] or g[2]:
                _over(frame, i, g)
        if q <= 1:
            head = at(q)
            # shed sparks on the bulbs it passes
            lam = 30 * P["sparks"] * dt
            kk = int(lam) + (1 if rng.random() < lam - int(lam) else 0)
            for _ in range(kk):
                if len(st["sparks"]) >= FS_MAX_SPARKS:
                    break
                x, y = at(q - rng.random() * tail * 0.7)
                hit = geo.nearest(L, x, y, 1.2)
                if hit is not None and hit in allowed:
                    st["sparks"].append([hit, 0.0, 0.6 + 0.9 * rng.random(), rng.randrange(1 << 20)])
        if q - tail > 1:
            st["fall"] = None
        if q >= 1 and not fl.get("landed"):
            fl["landed"] = True
            if P["burst"] > 0:
                st["burst"] = {"x": x2, "y": y2, "age": 0.0}
    for s in st["sparks"]:
        s[1] += dt
    st["sparks"] = [s for s in st["sparks"] if s[1] < s[2]]
    tw = int(clock * 12)
    bc = P["burst_colors"] or [GOLD]
    for i, a, life, key in st["sparks"]:
        b = envelope(a, 0.05, life * 0.4) * (0.5 + 0.5 * hash01(key, tw))
        _over(frame, i, bc[key % len(bc)], 0.7 * b)
    bs = st["burst"]
    if bs is not None:
        bs["age"] += dt
        a = bs["age"]
        R = 2.0 + 9.0 * P["burst"] * _smooth(a / 1.2)
        fade = math.exp(-a / (0.5 + 0.5 * P["burst"]))
        if fade < 0.02:
            st["burst"] = None
        else:
            for i, d in geo.near(L, bs["x"], bs["y"], R + 1.5):
                if i not in allowed or abs(px[i].y - bs["y"]) > 1.6:   # glitter along the line it landed on
                    continue
                ring = math.exp(-((d - R * 0.8) / 1.4) ** 2)
                h = hash01(i, tw, seed, 7)
                g = max(ring, (h - 0.55) / 0.45 if d < R and h > 0.55 else 0.0)
                _over(frame, i, bc[int(hash01(i, seed, 9) * len(bc)) % len(bc)], fade * g)
    if head is not None:
        # a white-hot core in the star's own colour (white added onto an orange tail is pink)
        geo.splat(frame, L, head[0], head[1], lerp(P["head"], (1.0, 1.0, 1.0), 0.4), 0.9, allowed, mode="max")
    return _clamp(frame, idx)


# ====================================================================== sleigh tour
ST_MAX_SPARKS = 360
ST_MAX_DUST = 120


def _roof_chains(layout, idx) -> list:
    """The target's roofline runs (not zigzag fills) as continuous chains of bulbs."""
    px = layout.pixels
    groups, cur = [], []
    for i in idx:
        if cur:
            a, b = px[cur[-1]], px[i]
            if a.run != b.run or math.hypot(b.x - a.x, b.y - a.y) > 3.5:
                groups.append(cur)
                cur = []
        cur.append(i)
    if cur:
        groups.append(cur)
    chains = [g for g in groups if len(g) >= 6 and geo._rows_by_reversal(px, g) is None
              and not geo.continues_a_fill(px, g)]
    return [geo.path(layout, g) for g in chains if geo.path(layout, g).length > 2]


def _route(layout, idx) -> list:
    """A closed loop over the roofline chains: the highest first, then each next chain
    from its end nearest to where the last one finished, with flights in between.
    [(kind, data, u0, u1)] where kind is "path" (data: (Path, s_from, s_to)) or "fly"
    (data: ((x0, y0), (x1, y1), arc height))."""
    chains = _roof_chains(layout, idx)
    if not chains:
        return []
    chains.sort(key=lambda p: (-sum(p.y) / len(p.y), -p.length))
    first, rest = chains[0], chains[1:]
    # start the loop at the first chain's end farthest from the second chain (so the
    # sleigh crosses the whole top roofline before dropping to the next)
    if rest:
        nx = rest[0].point(0)
        fwd = math.hypot(first.x[-1] - nx[0], first.y[-1] - nx[1]) < math.hypot(first.x[0] - nx[0], first.y[0] - nx[1])
        nx2 = rest[0].point(rest[0].length)
        fwd = fwd or math.hypot(first.x[-1] - nx2[0], first.y[-1] - nx2[1]) < math.hypot(
            first.x[0] - nx2[0], first.y[0] - nx2[1])
    else:
        fwd = True
    legs = [(first, 0.0, first.length) if fwd else (first, first.length, 0.0)]
    for c in rest:
        ex, ey = legs[-1][0].point(legs[-1][2])
        d0 = math.hypot(c.x[0] - ex, c.y[0] - ey)
        d1 = math.hypot(c.x[-1] - ex, c.y[-1] - ey)
        legs.append((c, 0.0, c.length) if d0 <= d1 else (c, c.length, 0.0))
    out, u = [], 0.0
    for k, (pth, s0, s1) in enumerate(legs):
        ln = abs(s1 - s0)
        out.append(("path", (pth, s0, s1), u, u + ln))
        u += ln
        nxt = legs[(k + 1) % len(legs)]
        a = pth.point(s1)
        b = nxt[0].point(nxt[1])
        d = math.hypot(b[0] - a[0], b[1] - a[1])
        if len(legs) == 1 and d < 1.5:
            continue
        fly = max(4.0, d * 0.6)                       # flights are quick: half the distance
        out.append(("fly", (a, b, 1.5 + 0.12 * d), u, u + fly))
        u += fly
    return out


def _where(route, total, u):
    """("path", Path, s) or ("fly", (x, y), None) for loop position u."""
    u %= total
    for kind, data, u0, u1 in route:
        if u0 <= u < u1 or (u1 == total and u >= u0):
            f = (u - u0) / max(1e-9, u1 - u0)
            if kind == "path":
                pth, s0, s1 = data
                return "path", pth, s0 + (s1 - s0) * f
            (ax, ay), (bx, by), h = data
            return "fly", (ax + (bx - ax) * f, ay + (by - ay) * f + h * 4 * f * (1 - f)), None
    kind, data, _, _ = route[-1]
    return ("path", data[0], data[2]) if kind == "path" else ("fly", data[1], None)


def _range(frame, route, total, u0, u1, col, gain, allowed, L):
    """Light the loop stretch [u0, u1] (u0 < u1, may wrap): crisp anti-aliased ends on
    the runs, a soft splat in flight."""
    for kind, data, a, b in route:
        for shift in (0.0, -total, total):
            lo, hi = max(a, u0 + shift), min(b, u1 + shift)
            if hi <= lo:
                continue
            if kind == "path":
                pth, s0, s1 = data
                k = (s1 - s0) / max(1e-9, b - a)
                sa, sb = s0 + (lo - a) * k, s0 + (hi - a) * k
                if sb < sa:
                    sa, sb = sb, sa
                j0, _ = pth.locate(sa)
                for j in range(max(0, j0 - 1), len(pth.order)):
                    sj = pth.s[j]
                    if sj - 0.5 > sb:
                        break
                    cov = min(sb, sj + 0.5) - max(sa, sj - 0.5)
                    if cov > 0:
                        _add(frame, pth.order[j], col, min(1.0, cov) * gain)
            else:
                (ax, ay), (bx, by), h = data
                f = ((lo + hi) / 2 - a) / max(1e-9, b - a)
                x, y = ax + (bx - ax) * f, ay + (by - ay) * f + h * 4 * f * (1 - f)
                geo.splat(frame, L, x, y, _scale(col, gain * min(1.0, hi - lo)), 1.2, allowed)


@frame_effect("sleigh_tour", "Santa's sleigh: grand tour", [
    Param("speed", "number", 12, "Flying speed (bulbs/s)", 3, 40, 0.5),
    Param("pause", "number", 1.5, "Pause between laps (s)", 0, 60, 0.5,
          help="a moment of quiet sky before the next lap; 0 = round and round"),
    Param("reindeer", "int", 5, "Reindeer", 0, 9, 1, help="the first one is Rudolph with his red nose"),
    Param("sleigh_length", "number", 3.5, "Sleigh length (bulbs)", 2, 6, 0.5),
    Param("sleigh_color", "color", "#ff0a0a", "Sleigh colour"),
    Param("trim_color", "color", "#ffb020", "Sleigh trim (gold)"),
    Param("deer_color", "color", "#ff8a30", "Reindeer colour"),
    Param("nose_color", "color", "#ff0000", "Rudolph's nose"),
    Param("sparkle_colors", "colors", ["#ffc030", "#ffe080", "#ffffff"], "Trail sparkle colours"),
    Param("trail", "number", 2.4, "Trail lingers (s)", 0.3, 8, 0.1),
    Param("trail_level", "number", 0.65, "Trail brightness", 0.2, 1, 0.05),
    Param("sparkle", "number", 1.2, "Trail density", 0, 3, 0.05),
    Param("dust", "number", 0.5, "Stardust", 0, 1, 0.05,
          help="sparkles that drift down off the gable onto the zigzag below (if it is in the target)"),
    Param("background", "color", "#000a50", "Sky colour"),
    Param("stars", "number", 0.7, "Background twinkle", 0, 2, 0.05),
    Param("seed", "int", 12, "Seed", 0, 9999, 1),
], "Santa's sleigh on a grand tour of the house: Rudolph's red nose leads the reindeer and a "
   "red-and-gold sleigh along the whole top roofline, over both gables, swoops down to the lower "
   "roofline (porch, garage) and flies back, then up again, round and round. A gold trail "
   "glitters behind it and stardust drifts off the gable onto the zigzag. Any other roofline run "
   "in the target joins the loop. Target the whole house.", group="Scenes")
def sleigh_tour(t, ctx, P, st):
    L = ctx.layout
    n = len(L.pixels)
    frame = [BLACK] * n
    idx = [i for i in ctx.idx if 0 <= i < n]
    if not idx:
        return frame
    sky = P["background"]
    for i in idx:
        frame[i] = sky
    if st.get("key") != _sig(ctx):
        st["key"] = _sig(ctx)
        st["allowed"] = set(idx)
        st["route"] = _route(L, idx)
        st["total"] = st["route"][-1][3] if st["route"] else 0.0
        st["u"], st["wait"] = 0.0, 0.0
        st["sparks"], st["dust"] = [], []
    allowed = st["allowed"]
    clock = ctx.clock
    seed = P["seed"]
    if st.get("seed") != seed or "rng" not in st:
        st["seed"], st["rng"] = seed, random.Random(seed * 7919 + 5)
    rng = st["rng"]
    if P["stars"] > 0:
        rate = 0.05 * P["stars"]
        for i in idx:
            g, _ = glint(i, clock, rate, 0.6, 0.9, seed, 1)
            if g > 0.02:
                _add(frame, i, (0.75, 0.85, 1.0), 0.38 * g)
    route, total = st["route"], st["total"]
    if not route or total <= 0:
        return _clamp(frame, idx)
    nd = int(P["reindeer"])
    gap = 2.0
    sl0 = nd * gap + 0.6
    sl1 = sl0 + P["sleigh_length"]
    dt = _dt(ctx)
    lap_len = total + sl1 + 2.0                       # the whole train clears the start before a pause
    # ---- move
    if st["wait"] > 0:
        st["wait"] = max(0.0, st["wait"] - dt)
    else:
        u_prev = st["u"]
        kind, _, _ = _where(route, total, u_prev)
        spd = P["speed"] * (1.8 if kind == "fly" else 1.0)
        st["u"] = u_prev + spd * dt
        if P["pause"] > 0 and st["u"] >= lap_len:
            st["u"] = 0.0
            st["wait"] = P["pause"]
        elif P["pause"] <= 0:
            st["u"] %= total
        # sparkles scattered just behind the sleigh
        lam = 24.0 * P["sparkle"] * dt
        k = int(lam) + (1 if rng.random() < lam - int(lam) else 0)
        sc = P["sparkle_colors"] or [GOLD]
        for _ in range(k):
            u = st["u"] - sl1 - 1.2 - rng.random() * 2.0
            if P["pause"] > 0 and (u < 0 or u > total):
                continue
            kind, pth, s = _where(route, total, u)
            col = sc[rng.randrange(len(sc))]
            if col[0] > 0.9 and col[1] > 0.9 and col[2] > 0.9 and rng.random() < 0.5:
                col = sc[0]                           # white half as often: the trail stays golden
            if kind != "path":
                continue
            if rng.random() < P["dust"] * 0.3 and len(st["dust"]) < ST_MAX_DUST:
                x, y = pth.point(s)
                st["dust"].append([x, y, (rng.random() - 0.5) * 0.6, 0.0, 0.0,
                                   P["trail"] * (0.9 + 0.8 * rng.random()), col, rng.randrange(1 << 20)])
            elif len(st["sparks"]) < ST_MAX_SPARKS:
                st["sparks"].append([pth, s, 0.0, P["trail"] * (0.45 + 0.75 * rng.random()), col,
                                     rng.randrange(1 << 20)])
    for s in st["sparks"]:
        s[2] += dt
    st["sparks"] = [s for s in st["sparks"] if s[2] < s[3]]
    for p in st["dust"]:
        p[4] += dt
        p[3] = (p[3] - 1.8 * dt) * math.exp(-1.4 * dt)
        p[2] *= math.exp(-1.0 * dt)
        p[0] += p[2] * dt
        p[1] += p[3] * dt
    st["dust"] = [p for p in st["dust"] if p[4] < p[5]]
    # ---- draw: trail, dust, then the train on top
    tw = int(clock * 14)
    smax = P["trail_level"]
    for pth, s, a, life, col, key in st["sparks"]:
        u = a / life
        b = _smooth(a / 0.3) * (1 - u) ** 1.4 * (0.45 + 0.55 * hash01(key, tw)) * smax
        if hash01(key, tw, 3) < 0.08:
            b = min(smax, b * 1.8 + 0.12)
        geo.splat_path(frame, pth, s, _scale(col, b), 0.9, mode="max")
    for p in st["dust"]:
        u = p[4] / p[5]
        b = 0.75 * (1 - u) ** 1.2 * (0.5 + 0.5 * hash01(p[7], tw))
        geo.splat(frame, L, p[0], p[1], _scale(p[6], b), 1.0, allowed, mode="max")
    if st["wait"] > 0:
        return _clamp(frame, idx)
    U = st["u"]

    def visible(off):
        return P["pause"] <= 0 or 0 <= U - off <= total

    def rng_(a, b, col, gain):
        a, b = U - b, U - a
        if P["pause"] > 0:
            a, b = max(0.0, a), min(total, b)
            if b <= a:
                return
        _range(frame, route, total, a, b, col, gain, allowed, L)

    # the train blots out the sky behind it so the colours stay pure
    hk, _, _ = _where(route, total, U)
    for kind, pth, s in [_where(route, total, U - o) for o in [x * 0.5 for x in range(int(sl1 * 2) + 3)]
                         if visible(o)]:
        if kind == "path":
            j, f = pth.locate(s)
            for jj in (j, j + 1):
                if 0 <= jj < len(pth.order) and abs(pth.s[jj] - s) < 0.75:
                    frame[pth.order[jj]] = BLACK
    rng_(0.0, sl0, P["trim_color"], 0.1)                    # the reins
    for k in range(nd):
        off = k * gap
        if not visible(off):
            continue
        kind, pth, s = _where(route, total, U - off)
        if k == 0:
            pulse = 0.85 + 0.15 * math.sin(clock * 8.0)
            c = _scale(P["nose_color"], pulse)
            w = 1.2
        else:
            c = _scale(P["deer_color"], 0.72 + 0.12 * math.sin(clock * 7.0 + off * 1.7))
            w = 0.9
        if kind == "path":
            geo.splat_path(frame, pth, s, c, w)
        else:
            geo.splat(frame, L, pth[0], pth[1], c, 1.2, allowed)
    rng_(sl0, sl0 + 0.9, P["trim_color"], 1.0)
    rng_(sl0 + 0.9, sl1 - 0.45, P["sleigh_color"], 1.0)
    rng_(sl0 + 0.9, sl1 - 0.45, (1.0, 0.86, 0.6), 0.2)
    rng_(sl1 - 0.45, sl1, P["trim_color"], 0.8)
    if visible(sl0 + P["sleigh_length"] / 2):
        kind, pth, s = _where(route, total, U - sl0 - P["sleigh_length"] / 2)
        gl = 0.35 + 0.25 * math.sin(clock * 11.0)
        if kind == "path":
            geo.splat_path(frame, pth, s, (gl, gl * 0.9, gl * 0.7), 0.8)
    return _clamp(frame, idx)


# ====================================================================== candlelight
@frame_effect("candlelight", "Candlelight", [
    Param("color", "color", "#ff9a3c", "Candle colour", help="a warm white made from red, some green, a little blue"),
    Param("color2", "color", "#ff6a10", "Deeper flame", help="the flames lean towards this as they waver"),
    Param("level", "number", 0.7, "Brightness", 0.15, 1, 0.01),
    Param("waver", "number", 0.3, "Waver", 0, 0.8, 0.01,
          help="how much each candle breathes and wavers (slow and smooth: never a flicker)"),
    Param("swell_every", "number", 14, "Swell every (s)", 0, 120, 1,
          help="a soft wave of light flows out from the gable apex; 0 = never"),
    Param("swell_color", "color", "#ffd8a0", "Swell colour"),
    Param("star", "color", "#fff0c0", "Apex light", help="the bulbs at the gable apex glow like a lantern"),
    Param("seed", "int", 9, "Seed", 0, 9999, 1),
], "Every bulb a candle: a warm white built from RGB that breathes and wavers gently, each at its "
   "own pace, with a lantern glow at the gable apex and, every so often, a soft swell of golden "
   "light that flows out from the apex across the whole house. A quiet, grateful moment.",
   group="Ambient")
def candlelight(t, ctx, P, st):
    L = ctx.layout
    px = L.pixels
    n = len(px)
    frame = [BLACK] * n
    idx = [i for i in ctx.idx if 0 <= i < n]
    if not idx:
        return frame
    if st.get("key") != _sig(ctx):
        st["key"] = _sig(ctx)
        star = _apex(L, idx)
        sx = sum(px[i].x for i in star) / max(1, len(star)) if star else 0.0
        sy = sum(px[i].y for i in star) / max(1, len(star)) if star else 0.0
        st["star"] = set(star)
        st["dist"] = {i: math.hypot(px[i].x - sx, px[i].y - sy) for i in idx}
        st["far"] = max(st["dist"].values()) if st["dist"] else 1.0
    clock = ctx.clock
    c1, c2, lv, wv, seed = P["color"], P["color2"], P["level"], P["waver"], P["seed"]
    E = P["swell_every"]
    front = None
    if E > 0:
        ph = clock % E
        front = ph * 22.0 - 3.0                       # bulbs/s outwards from the apex
    sw, star, dist = P["swell_color"], st["star"], st["dist"]
    for i in idx:
        w = wobble(i, clock, 0.35, seed)
        w2 = wobble(i, clock, 0.9, seed + 1)
        v = lv * (1 - wv * (0.6 * w + 0.4 * w2))
        c = lerp(c1, c2, 0.5 * w2)
        c = _scale(c, v)
        if front is not None:
            d = dist[i] - front
            if -6 < d < 2:
                e = (1 - abs(d + 2) / 4) if d > -6 else 0.0
                e = max(0.0, e) * (1 - min(1.0, dist[i] / (st["far"] + 1)) * 0.5)
                c = lerp(c, _scale(sw, min(1.0, lv * 1.3)), _smooth(e) * 0.7)
        if i in star:
            g = 0.85 + 0.15 * math.sin(clock * TAU / 4.0)
            c = _scale(P["star"], g)
        frame[i] = c
    return _clamp(frame, idx)
