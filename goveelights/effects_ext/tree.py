"""Christmas tree scene: the front gable's zigzag as a lit tree, the gable apex as its star.

    tree      the whole scene (frame effect, it remembers when it was lit):
              body + travelling garland + ornaments with glints + star on top,
              a "lighting the tree" intro and an optional periodic relight.
    presents  wrapped boxes with ribbons for the porch under the tree.

How the tree finds itself in the target
---------------------------------------
* The tree is the part of the target that is wired as a zigzag (rows that reverse
  direction, e.g. the Peak run). If nothing in the target is a zigzag, the whole
  target is the tree, its rows grouped by height.
* The star is the bulbs crowning the tree (geometry.star: on the house the gable apex,
  Eaves bulbs 42/43). They are OUTSIDE the Peak, so target "Whole front gable"
  (Peak + Front gable outline) to light them. If the star bulbs are not in the
  target, the middle of the tree's top row becomes the star instead.
* Every other target bulb (e.g. the rest of the gable outline) gets a soft warm glow,
  brighter near the star, so the star stands out.
"""
from __future__ import annotations

import math

from .. import geometry
from ..color import BLACK, hash01, lerp
from ..effects import Param, effect, frame_effect, travel
from ..sparkle import envelope, glint, wobble

TAU = 2 * math.pi


def _smooth(x: float) -> float:
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    return x * x * (3 - 2 * x)


def _clamp01(c):
    r, g, b = c
    return (0.0 if r < 0 else 1.0 if r > 1 else r,
            0.0 if g < 0 else 1.0 if g > 1 else g,
            0.0 if b < 0 else 1.0 if b > 1 else b)


# ------------------------------------------------------------------ geometry
def _rows_by_height(px, order, tol=0.45):
    out = []
    for i in sorted(order, key=lambda i: px[i].y):
        if out and abs(px[i].y - px[out[-1][0]].y) <= tol:
            out[-1].append(i)
        else:
            out.append([i])
    return out


def tree_geometry(layout, idx: list[int]) -> dict:
    """Split a target into tree body (rows), star bulbs and the rest (glow).
    Cached per layout object and target."""
    key = ("tree", tuple(idx))
    c = geometry._cache(layout)
    if key in c:
        return c[key]
    px = layout.pixels
    groups: dict[str, list[int]] = {}
    for i in idx:
        groups.setdefault(px[i].run, []).append(i)
    tree, rows = [], None
    for g in sorted(groups.values(), key=len, reverse=True):
        r = geometry._rows_by_reversal(px, g)
        if r:
            tree, rows = g, r
            break
    if rows is None:
        tree = list(idx)
        rows = _rows_by_height(px, tree) if tree else []
    rows = [sorted(r, key=lambda i: px[i].x) for r in rows]
    rows.sort(key=lambda r: sum(px[i].y for i in r) / len(r))
    tset, iset = set(tree), set(idx)

    # star: bulbs crowning the tree, if the target has them; else top row middle
    star = []
    if tree:
        xs = [px[i].x for i in tree]
        lo, hi, top = min(xs), max(xs), max(px[i].y for i in tree)
        mid = (lo + hi) / 2
        above = sorted((p.i for p in px if p.i not in tset and p.y > top + 0.3 and lo - 1 <= p.x <= hi + 1),
                       key=lambda i: (-px[i].y, abs(px[i].x - mid)))[:2]
        if above and all(i in iset for i in above):
            star = above
        else:
            toprow = rows[-1]
            cx = sum(px[i].x for i in toprow) / len(toprow)
            star = sorted(toprow, key=lambda i: abs(px[i].x - cx))[:2 if len(toprow) >= 4 else 1]
            if len(tree) - len(star) < 1:
                star = []
    sset = set(star)
    nrows = len(rows)
    info = {}
    for r, row in enumerate(rows):
        n = len(row)
        for k, i in enumerate(row):
            if i in sset:
                continue
            h = r / (nrows - 1) if nrows > 1 else 0.0
            u = k / (n - 1) if n > 1 else 0.5
            info[i] = (r, h, u)
    body = [i for i in tree if i in info]
    pth = geometry.path(layout, body) if body else None
    s_of = {i: pth.s[k] for k, i in enumerate(body)} if pth else {}
    if star:
        sx = sum(px[i].x for i in star) / len(star)
        sy = sum(px[i].y for i in star) / len(star)
    else:
        sx = sy = 0.0
    rest = [i for i in idx if i not in tset and i not in sset]
    dist = {i: math.hypot(px[i].x - sx, px[i].y - sy) for i in idx} if star else {}
    near_star = {i: dist[i] for i in body if dist.get(i, 99.0) < 1.8}
    out = {"body": body, "info": info, "near_star": near_star, "s": s_of, "star": star, "rest": rest, "dist": dist,
           "rows": nrows, "length": pth.length if pth else 0.0,
           # per-bulb constants for shimmer and the two-green body mix
           "ph": {i: (hash01(i, 11) * TAU, 0.25 + 0.35 * hash01(i, 12), hash01(i, 13)) for i in body}}
    c[key] = out
    return out


def ornaments(layout, geo: dict, density: float, seed: int, ncol: int) -> dict:
    """Deterministic ornament bulbs -> colour index. Never two ornaments touching."""
    key = ("tree-orn", id(geo), round(density, 4), seed, ncol)
    c = geometry._cache(layout)
    if key in c:
        return c[key]
    px = layout.pixels
    chosen: dict[int, int] = {}
    pts: list[tuple[float, float]] = []
    cand = sorted((i for i in geo["body"] if i not in geo["near_star"]), key=lambda i: hash01(i, seed, 41))
    want = int(round(density * len(cand)))
    for i in cand:
        if len(chosen) >= want:
            break
        x, y = px[i].x, px[i].y
        if any((x - a) ** 2 + ((y - b) * 1.25) ** 2 < 1.6 for a, b in pts):
            continue
        chosen[i] = 0
        pts.append((x, y))
    # colours: walk ornaments left to right, row by row, never repeating a neighbour's
    order = sorted(chosen, key=lambda i: (geo["info"][i][0], px[i].x))
    last = -1
    for i in order:
        k = int(hash01(i, seed, 43) * ncol) % ncol
        if ncol > 1 and k == last:
            k = (k + 1) % ncol
        chosen[i] = last = k
    c[key] = chosen
    return chosen


# ------------------------------------------------------------------ the tree
TREE_PARAMS = [
    Param("body", "color", "#00c832", "Tree colour",
          help="the needles; deep saturated green reads best on LEDs"),
    Param("body2", "color", "#1c8a00", "Second needle colour",
          help="mixed in per bulb for a natural, not flat, green"),
    Param("level", "number", 0.85, "Tree brightness", 0.05, 1, 0.01),
    Param("shimmer", "number", 0.25, "Needle shimmer", 0, 1, 0.05,
          help="gentle per-bulb breathing of the tree body"),
    Param("shade", "number", 0.25, "Shade toward the bottom", 0, 1, 0.05,
          help="darker lower boughs, lighter tip, like light falling on a real tree"),
    Param("garland", "color", "#ffa000", "Garland colour"),
    Param("garland_style", "choice", "strand", "Garland style",
          options=["strand", "swag", "none"],
          help="strand: runs along the wiring, row by row, back and forth; "
               "swag: diagonal drapes across the tree; none: no garland"),
    Param("garland_width", "number", 3, "Garland width (bulbs)", 0.5, 10, 0.1),
    Param("garland_spacing", "number", 14, "Garland spacing (bulbs)", 3, 120, 0.5,
          help="distance between garland strands; large = a single strand"),
    Param("speed", "number", 1.2, "Garland speed (bulbs/s)", -10, 10, 0.1,
          help="how fast the garland creeps along; negative reverses"),
    Param("garland_sparkle", "number", 1.2, "Garland sparkle", 0, 6, 0.1,
          help="glints per garland bulb per second"),
    Param("ornament_colors", "colors", ["#ff0000", "#1848ff", "#ff0a78", "#e8f0ff", "#ff0000", "#a020ff"],
          "Ornament colours"),
    Param("density", "number", 0.18, "Ornament density", 0, 0.5, 0.01,
          help="share of tree bulbs that are ornaments"),
    Param("glints", "number", 0.12, "Ornament glints per second", 0, 3, 0.01,
          help="how often each ornament catches the light"),
    Param("star", "color", "#ffa820", "Star colour"),
    Param("star_pulse", "number", 0.2, "Star pulse", 0, 1, 0.05, help="depth of the star's gentle breathing"),
    Param("burst_every", "number", 12, "Star burst every (s)", 0, 120, 1,
          help="the star flares and throws sparks onto the outline; 0 = never"),
    Param("glow", "color", "#ff9a40", "Outline glow colour",
          help="the rest of the target (e.g. the gable outline) glows softly in this"),
    Param("glow_level", "number", 0.3, "Outline glow", 0, 1, 0.01, help="0 = outline dark"),
    Param("halo", "number", 0.5, "Star halo", 0, 1, 0.05,
          help="the outline bulbs next to the star take on its glow"),
    Param("intro", "number", 8, "Lighting intro (s)", 0, 60, 0.5,
          help="when the scene starts the tree lights from the trunk up, ornaments pop on, "
               "then the star ignites; 0 = no intro"),
    Param("relight", "number", 0, "Relight every (min)", 0, 120, 0.5,
          help="dim the tree and light it again with the intro; 0 = never"),
    Param("seed", "int", 7, "Seed", 0, 9999, 1, help="which bulbs are ornaments, and the sparkle pattern"),
]

FADE_S = 2.5          # relight: how long the tree takes to go dark before relighting


def _timeline(clock: float, P: dict, state: dict) -> tuple[float | None, float]:
    """(seconds into the intro or None when it is over / disabled, overall dimmer)."""
    if "start" not in state or clock < state["start"] - 1e-6:
        state["start"] = clock
        state.pop("fade", None)
    D, R = P["intro"], P["relight"] * 60.0
    if state.get("fade") is not None:
        age = clock - state["fade"]
        if age >= FADE_S:
            state["start"], state["fade"] = clock, None
        else:
            return None, 1.0 - _smooth(age / FADE_S)
    tau = clock - state["start"]
    if R > 0 and tau >= max(R, D + 5):
        state["fade"] = clock
        return None, 1.0
    if D > 0 and tau < D:
        return tau, 1.0
    return None, 1.0


@frame_effect("tree", "Christmas tree", TREE_PARAMS,
              "The zigzag becomes a lit Christmas tree: shimmering green boughs, a gold garland "
              "creeping along the rows with sparkle, coloured ornaments that catch the light, and "
              "a warm gold star on the gable apex that breathes and now and then bursts into sparks. "
              "Starts with a tree-lighting: trunk to tip, ornaments pop on, the star ignites last. "
              "Target 'Whole front gable' so the star (on the outline) lights too; on 'Peak' alone "
              "the top of the tree becomes the star.",
              group="Scenes")
def tree(t, ctx, P, state):
    layout = ctx.layout
    px = layout.pixels
    n = len(px)
    out = [BLACK] * n
    idx = [i for i in ctx.idx if 0 <= i < n]
    if not idx:
        return out
    geo = tree_geometry(layout, idx)
    clock = ctx.clock
    intro, dim = _timeline(clock, P, state)
    D = P["intro"]
    seed = P["seed"]
    info, s_of, ph = geo["info"], geo["s"], geo["ph"]
    ocols = P["ornament_colors"] or [(1.0, 0.0, 0.0)]
    orn = ornaments(layout, geo, P["density"], seed, len(ocols))

    # intro phases (fractions of the intro length)
    if intro is not None:
        x = intro / D
        front = x / 0.55 * 1.35 - 0.2          # body fill front, in tree heights
        star_on = _smooth((intro - 0.82 * D) / 0.15)
        ignite = (x - 0.82) * D                # seconds since the star ignited
        glow_on = _smooth((x - 0.84) / 0.16)
    else:
        front, star_on, ignite, glow_on = 9.0, 1.0, -1.0, 1.0

    # star bursts: one per slot, jittered
    B = P["burst_every"]
    burst_age = 1e9
    if B > 0:
        k = math.floor(clock / B)
        for kk in (k, k - 1):
            st = kk * B + B * (0.3 + 0.5 * hash01(kk, seed, 61))
            if clock >= st:
                burst_age = min(burst_age, clock - st)
    if 0 <= ignite < burst_age:
        burst_age = ignite
    burst = envelope(burst_age, 0.05, 0.7) if burst_age < 6 else 0.0
    big = 1.5 if burst_age == ignite else 1.0        # the ignition burst is the big one
    has_star = bool(geo["star"])
    dist = geo["dist"]
    sparking = has_star and burst_age < 2.5
    if sparking:
        ring_r = burst_age * 9.0                     # sparks race out from the star...
        fall = math.exp(-burst_age / 0.9) * big      # ...and die away
        flick = int(clock * 20)

    def sparks(i: int) -> float:
        """Burst light on bulb i: a bright ring racing outwards, glitter left behind."""
        d = dist.get(i, 99.0)
        if d > ring_r + 2.5 or d > 12:
            return 0.0
        e = math.exp(-((d - ring_r) / 1.1) ** 2)
        if d < ring_r:
            hsh = hash01(i, flick, seed, 3)
            if hsh > 0.65:
                e = max(e, (hsh - 0.65) / 0.35 * math.exp(-(ring_r - d) / 3.0))
        return min(1.0, e * fall * (0.55 + 0.45 * hash01(i, flick, seed, 4)))

    # ---- body, garland, ornaments
    body, body2 = P["body"], P["body2"]
    lvl, shim, shade = P["level"], P["shimmer"], P["shade"]
    gcol, gstyle = P["garland"], P["garland_style"]
    gw, gsp = P["garland_width"], P["garland_spacing"]
    half = gw / 2
    tr = travel(t, P)
    gspark = P["garland_sparkle"]
    grate = P["glints"]
    soft = 0.22
    near_star, sc, halo = geo["near_star"], P["star"], P["halo"]
    for i in geo["body"]:
        r, h, u = info[i]
        if intro is not None:
            hh = h * 0.85 + 0.15 * abs(u - 0.5) * 2      # each row lights centre-out
            lit = _smooth((front - hh) / soft)
            if lit <= 0:
                continue
        else:
            lit = 1.0
        a, rate, mix = ph[i]
        w = 1 - shim * (0.5 + 0.5 * math.sin(clock * rate * TAU + a))
        v = lvl * w * (1 - shade * (1 - h))
        c = lerp(body, body2, mix)
        c = (c[0] * v, c[1] * v, c[2] * v)

        if i in orn:
            oc = ocols[orn[i] % len(ocols)]
            if intro is not None:
                pop_t = D * (0.5 + 0.3 * (h * 0.7 + 0.3 * hash01(i, seed, 47)))
                age = intro - pop_t
                if age >= 0:
                    g = envelope(age, 0.02, 0.35)
                    c = lerp(c, oc, _smooth(age / 0.08))
                    c = (c[0] + (1 - c[0]) * g, c[1] + (1 - c[1]) * g * 0.9, c[2] + (1 - c[2]) * g * 0.7)
            else:
                g = glint(i, clock, grate, 0.03, 0.4, seed, 1)[0] if grate > 0 else 0.0
                c = (oc[0] + (1 - oc[0]) * g * 0.85, oc[1] + (1 - oc[1]) * g * 0.85, oc[2] + (1 - oc[2]) * g * 0.85)
        elif i in near_star:
            # the boughs right under the star catch its light (no garland over the star)
            if star_on > 0 and halo > 0:
                c = lerp(c, sc, halo * 0.7 * (1 - near_star[i] / 1.8) * star_on)
        elif gstyle != "none":
            if gstyle == "swag":
                q = px[i].x + r * 1.3 - tr
            else:
                q = s_of.get(i, 0.0) - tr
            d = q % gsp
            d = min(d, gsp - d)
            cov = max(0.0, min(d + 0.5, half) - max(d - 0.5, -half)) / min(1.0, gw)
            if cov > 0:
                cov = min(1.0, cov)
                core = 0.75 + 0.25 * (1 - min(1.0, d / max(half, 0.5)))
                gc = (gcol[0] * core, gcol[1] * core, gcol[2] * core)
                if gspark > 0:
                    g = glint(i, clock, gspark, 0.02, 0.18, seed + 1, 2)[0] * cov
                    gc = (gc[0] + (1 - gc[0]) * g, gc[1] + (1 - gc[1]) * g, gc[2] + (1 - gc[2]) * g * 0.8)
                c = lerp(c, gc, cov)
        if intro is not None and front - (h * 0.85) < soft * 1.6:
            # the lighting front: a warm sparkle where the lights are just coming on
            e = max(0.0, 1 - abs(front - h * 0.85 - soft * 0.5) / (soft * 1.1))
            e *= 0.5 + 0.5 * hash01(i, int(clock * 15), seed)
            c = (c[0] + 0.8 * e, c[1] + 0.6 * e, c[2] + 0.25 * e)
        if sparking:
            e = sparks(i) * 0.65
            if e > 0:
                c = (c[0] + (1 - c[0]) * e, c[1] + (0.9 - c[1]) * e, c[2] + (0.55 - c[2]) * e)
        out[i] = _clamp01((c[0] * lit * dim, c[1] * lit * dim, c[2] * lit * dim))

    # ---- star
    star = geo["star"]
    if star and star_on > 0:
        pulse = 1 - P["star_pulse"] * (0.5 + 0.5 * math.sin(clock * TAU / 3.2))
        for i in star:
            tw = glint(i, clock, 0.8, 0.03, 0.25, seed + 2, 1)[0]
            b = min(1.0, burst * big)
            v = pulse * (1 - b) + b
            c = (sc[0] * v, sc[1] * v, sc[2] * v)
            hot = min(1.0, 0.55 * b + 0.4 * tw)
            c = (c[0] + (1 - c[0]) * hot, c[1] + (1 - c[1]) * hot, c[2] + (1 - c[2]) * hot * 0.75)
            out[i] = _clamp01((c[0] * star_on * dim, c[1] * star_on * dim, c[2] * star_on * dim))

    # ---- outline glow, star halo, burst sparks
    gl, glv = P["glow"], P["glow_level"]
    for i in geo["rest"]:
        v = glv * (0.85 + 0.15 * wobble(i, clock, 0.3, seed))
        c = (gl[0] * v, gl[1] * v, gl[2] * v)
        d = dist.get(i, 99.0)
        if has_star and halo > 0 and d < 3.5:
            f = halo * (1 - d / 3.5) ** 1.5
            c = lerp(c, sc, f * star_on)
        c = (c[0] * glow_on, c[1] * glow_on, c[2] * glow_on)
        if sparking:
            e = sparks(i)
            if e > 0:
                c = (c[0] + (1 - c[0]) * e, c[1] + (0.9 - c[1]) * e, c[2] + (0.55 - c[2]) * e)
        out[i] = _clamp01((c[0] * dim, c[1] * dim, c[2] * dim))
    return out


# ------------------------------------------------------------------ presents
@effect("presents", "Presents", [
    Param("colors", "colors", ["#ff0000", "#00a000", "#1040ff", "#ff0060", "#ffa000"], "Wrapping paper"),
    Param("ribbon", "color", "#ffd060", "Ribbon colour"),
    Param("background", "color", "#ff8a30", help="warm glow between the presents"),
    Param("glow", "number", 0.12, "Glow between presents", 0, 1, 0.01),
    Param("size", "int", 4, "Present size (bulbs)", 2, 12, 1),
    Param("level", "number", 0.75, "Brightness", 0.05, 1, 0.01),
    Param("twinkle", "number", 0.35, "Twinkle", 0, 1, 0.05, help="how much the wrapping shimmers"),
    Param("glints", "number", 0.3, "Ribbon glints per second", 0, 4, 0.05),
    Param("seed", "int", 5, "Seed", 0, 9999, 1),
], "A row of wrapped presents under the tree: blocks of coloured wrapping, each tied with a "
   "glinting gold ribbon bulb, on a warm glow. Made for the porch under the front gable.",
   group="Scenes")
def presents(t, p, P):
    size = max(2, P["size"])
    period = size + 1
    k, j = divmod(p.ri, period)
    bg = P["background"]
    if j == size:                                       # the gap between presents
        g = P["glow"] * (0.8 + 0.2 * wobble(p.i, t, 0.3, P["seed"]))
        return (bg[0] * g, bg[1] * g, bg[2] * g)
    seed = P["seed"]
    if j == size // 2:                                  # the ribbon
        g = glint(p.i, t, P["glints"], 0.03, 0.35, seed, 1)[0] if P["glints"] > 0 else 0.0
        c = P["ribbon"]
        v = P["level"]
        return _clamp01((c[0] * v + (1 - c[0] * v) * g, c[1] * v + (1 - c[1] * v) * g,
                         c[2] * v + (1 - c[2] * v) * g * 0.8))
    cols = P["colors"]
    c = cols[int(hash01(k, seed, 9) * len(cols)) % len(cols)]
    if k > 0 and len(cols) > 1 and c == cols[int(hash01(k - 1, seed, 9) * len(cols)) % len(cols)]:
        c = cols[(cols.index(c) + 1) % len(cols)]
    v = P["level"] * (1 - P["twinkle"] * 0.5 * (1 - wobble(p.i, t, 0.5, seed)))
    return (c[0] * v, c[1] * v, c[2] * v)
