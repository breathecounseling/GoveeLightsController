"""Christmas tree scene: a tree DRAWN on the Peak's honeycomb, its star on the top.

    tree      the whole scene (frame effect, it remembers when it was lit):
              boughs + garland + ornaments with glints + star on top, a "lighting the
              tree" intro and an optional periodic relight.
    presents  wrapped boxes with ribbons for the porch under the tree (frame effect:
              boxes are counted from the start of the target, so they start whole).

How the tree is drawn (round 13: built for the triangular honeycomb screen)
----------------------------------------------------------------------------
The Peak's rows are offset half a bulb (a honeycomb triangle, see hexcanvas). Lighting
it edge to edge in green only says "the gable is green", so the tree is a picture on it
(tree_plan):
* a fir in TRUE proportions (round 15: the rows really hang hexcanvas.aspect, ~0.66, of
  a bulb spacing apart, so a tree drawn one bulb per row was 2-3x too wide on the
  house): its sides grow half a bulb a row (the narrowest step the lattice has), in
  tiers of three rows that step back in under the tier above; about 1.5x taller than
  wide with the Tip, about square without it (it is only six rows tall); `spread`
  steps each tier further out for a wider tree;
* a night sky round it (dim, faint far stars) with a black margin hugging the tree;
* a trunk: the middle of the bottom row (one bulb under a slender tree), the rest of
  that row is dark ground;
* the star on the top row's middle (with the Tip: the apex and the two under it), with
  dimmer rays either side over a wide tree; it lights on target "Peak" alone too;
* ornaments as bold PAIRS of side-by-side bulbs in two or three colours, well apart
  (single bulbs on a slender tree, where a pair would fill a row);
* the garland as a swag along a lattice diagonal (or, "strand", dashes along each
  tier's hem).
Every per-bulb choice is seeded by the cell's lattice position, so a dead bulb never
moves anything. The rest of the target (e.g. the gable outline) glows warmly round it,
brightest by the star.

If the target has no zigzag the whole target is lit as a tree row by row, as before
(tree_geometry, _tree_flat).
"""
from __future__ import annotations

import math
import zlib

from .. import geometry
from . import hexcanvas
from ..color import BLACK, hash01, lerp
from ..effects import Param, frame_effect, travel
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


def ornaments(layout, geo: dict, density: float, seed: int, ncol: int, state: dict | None = None) -> dict:
    """Deterministic ornament bulbs -> colour index. Never two ornaments touching.
    Only the latest selection is remembered (in the layer's frame state when given),
    so modulating density or seed never grows a cache."""
    density = round(max(0.0, density) * 100) / 100
    key = (tuple(geo["body"]), density, seed, ncol)
    memo = state if state is not None else geo
    hit = memo.get("orn")
    if hit is not None and hit[0] == key and hit[1] is layout:
        return hit[2]
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
    memo["orn"] = (key, layout, chosen)
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
    Param("garland", "color", "#ff8c00", "Garland colour",
          help="deep gold by default, so the paler, hotter star stands apart from it"),
    Param("garland_style", "choice", "swag", "Garland style",
          options=["strand", "swag", "none"],
          help="swag: drapes along the honeycomb's diagonal, creeping across the tree; "
               "strand: a garland along the hem of each tier; none: no garland"),
    Param("garland_width", "number", 2, "Garland width (bulbs)", 0.5, 10, 0.1),
    Param("garland_spacing", "number", 20, "Garland spacing (bulbs)", 3, 120, 0.5,
          help="distance between garland strands; large = a single strand"),
    Param("speed", "number", 1.2, "Garland speed (bulbs/s)", -10, 10, 0.1,
          help="how fast the garland creeps along; negative reverses"),
    Param("garland_sparkle", "number", 1.2, "Garland sparkle", 0, 6, 0.1,
          help="glints per garland bulb per second"),
    Param("ornament_colors", "colors", ["#ff0000", "#1848ff"],
          "Ornament colours", help="two or three bold colours read best; the first three are used on the Peak"),
    Param("density", "number", 0.18, "Ornament density", 0, 0.5, 0.01,
          help="share of tree bulbs that are ornaments"),
    Param("glints", "number", 0.12, "Ornament glints per second", 0, 3, 0.01,
          help="how often each ornament catches the light"),
    Param("star", "color", "#ffd060", "Star colour",
          help="keep it paler/whiter than the garland so the star reads as the brightest thing"),
    Param("star_pulse", "number", 0.3, "Star pulse", 0, 1, 0.05, help="depth of the star's gentle breathing"),
    Param("burst_every", "number", 12, "Star burst every (s)", 0, 120, 1,
          help="the star flares and throws sparks onto the outline; 0 = never"),
    Param("glow", "color", "#ffb070", "Outline glow colour",
          help="the rest of the target (e.g. the gable outline) glows softly in this"),
    Param("glow_level", "number", 0.18, "Outline glow", 0, 1, 0.01, help="0 = outline dark"),
    Param("pre_glow", "number", 0, "Outline glow while lighting", 0, 1, 0.05,
          help="share of the outline glow already on while the tree lights (0 = outline dark "
               "until the star ignites)"),
    Param("ignite_flash", "number", 0, "Ignition flash", 0, 1, 0.05,
          help="when the star ignites the whole outline flashes white for a moment"),
    Param("halo", "number", 0.5, "Star halo", 0, 1, 0.05,
          help="the outline bulbs next to the star take on its glow"),
    Param("intro", "number", 8, "Lighting intro (s)", 0, 60, 0.5,
          help="when the scene starts the tree lights from the trunk up, ornaments pop on, "
               "then the star ignites; 0 = no intro"),
    Param("relight", "number", 0, "Relight every (min)", 0, 120, 0.5,
          help="dim the tree and light it again with the intro; 0 = never"),
    Param("trunk", "color", "#a03c00", "Trunk colour",
          help="the middle of the bottom row, under the boughs"),
    Param("sky", "color", "#0a1a60", "Night sky colour",
          help="the Peak round the tree; a clean black margin always hugs the tree"),
    Param("sky_level", "number", 0.06, "Night sky brightness", 0, 0.5, 0.01,
          help="keep it low: the tree reads best as a bright shape on a dark gable; 0 = black"),
    Param("sky_twinkle", "number", 0.02, "Far stars", 0, 1, 0.01,
          help="faint stars twinkling in the sky round the tree (glints per bulb per second)"),
    Param("spread", "number", 0.0, "Tier step", 0, 3, 0.5,
          help="0 draws the tree in true proportions (about 1.5 times taller than wide as "
               "the rows really hang, each tier stepping back in under the one above); more "
               "steps each tier of boughs further out (bulbs each side) for a wider, squatter "
               "tree. The honeycomb rows sit half a bulb apart, so a half step only shows on "
               "some rows"),
    Param("seed", "int", 7, "Seed", 0, 9999, 1, help="which bulbs are ornaments, and the sparkle pattern"),
]

FADE_S = 2.5          # relight: how long the tree takes to go dark before relighting


def _timeline(clock: float, P: dict, state: dict) -> tuple[float | None, float]:
    """(seconds into the intro or None when it is over / disabled, overall dimmer)."""
    D, R = P["intro"], P["relight"] * 60.0
    if "start" not in state or clock < state["start"] - 1e-6:
        state["start"] = clock
        state["ignite_at"] = clock + 0.82 * D if D > 0 else -1e9
        state.pop("fade", None)
    if state.get("fade") is not None:
        age = clock - state["fade"]
        if age >= FADE_S:
            state["start"], state["fade"] = clock, None
            state["ignite_at"] = clock + 0.82 * D if D > 0 else -1e9
        else:
            return None, 1.0 - _smooth(age / FADE_S)
    tau = clock - state["start"]
    if R > 0 and tau >= max(R, D + 5):
        state["fade"] = clock
        return None, 1.0
    if D > 0 and tau < D:
        return tau, 1.0
    return None, 1.0


def _tree_flat(t, ctx, P, state):
    """The tree when the target has no zigzag: every row of the target lit as boughs."""
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
    orn = ornaments(layout, geo, P["density"], seed, len(ocols), state)

    # intro phases (fractions of the intro length)
    if intro is not None:
        x = intro / D
        front = x / 0.55 * 1.35 - 0.2          # body fill front, in tree heights
        star_on = _smooth((intro - 0.82 * D) / 0.15)
        glow_on = _smooth((x - 0.84) / 0.16)
    else:
        front, star_on, glow_on = 9.0, 1.0, 1.0
    ignite = clock - state.get("ignite_at", -1e9)    # seconds since the star ignited

    # star bursts: one per slot, jittered, counted from when the tree was lit;
    # none while the intro runs (the star ignites last, with its own big burst)
    B = P["burst_every"]
    burst_age = 1e9
    if B > 0 and intro is None and dim >= 1.0:
        bc = clock - state["start"] - max(0.0, D)      # measured from when the intro ended
        k = math.floor(bc / B)
        for kk in (k, k - 1):
            st = kk * B + B * (0.3 + 0.5 * hash01(kk, seed, 61))
            if kk >= 0 and bc >= st:
                burst_age = min(burst_age, bc - st)
    big = 1.0
    if 0 <= ignite < burst_age:
        burst_age, big = ignite, 1.5                 # the ignition burst is the big one
    burst = envelope(burst_age, 0.05, 0.7) if burst_age < 6 else 0.0
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
    ov = min(1.0, lvl * 1.15)                        # ornaments follow the tree's brightness, a little brighter
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
            ovs = ov * (1 - shade * 0.5 * (1 - h))
            oc = (oc[0] * ovs, oc[1] * ovs, oc[2] * ovs)
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
                c = lerp(c, sc, halo * 0.4 * (1 - near_star[i] / 1.8) * star_on)
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
            hs = hash01(i, int(clock * 15), seed)
            if hs > 0.6:                             # about 1 bulb in 3 flickers: twinkling, not a band
                c = lerp(c, (1.0, 0.82, 0.55), 0.85 * e * (0.4 + 1.5 * (hs - 0.6)))
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
            v = 0.82 * pulse * (1 - b) + b                 # rests below full: headroom for pulses and bursts
            c = (sc[0] * v, sc[1] * v, sc[2] * v)
            hot = min(1.0, 0.1 + 0.5 * b + 0.4 * tw)       # always a touch hotter than the garland
            c = (c[0] + (1 - c[0]) * hot, c[1] + (1 - c[1]) * hot, c[2] + (1 - c[2]) * hot * 0.75)
            out[i] = _clamp01((c[0] * star_on * dim, c[1] * star_on * dim, c[2] * star_on * dim))

    # ---- outline glow, star halo, burst sparks
    gl, glv = P["glow"], P["glow_level"]
    glow_on = max(glow_on, P["pre_glow"])
    flash = P["ignite_flash"] * envelope(ignite, 0.04, 0.5) if 0 <= ignite < 4 else 0.0
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
        if flash > 0:
            c = lerp(c, (1.0, 0.95, 0.85), flash)
        out[i] = _clamp01((c[0] * dim, c[1] * dim, c[2] * dim))
    return out


# ------------------------------------------------------------------ the tree on the honeycomb
# The Peak is a triangle of offset rows (a honeycomb). The tree is DRAWN on it: a tiered
# silhouette with 60-degree lattice edges, black sky round it, a trunk on the bottom row
# and a bold star on the top (the Tip's apex when there is one). Every per-bulb choice is
# seeded by the cell's lattice key, so a dead bulb never moves the picture.

def _tier_sizes(f: int) -> list[int]:
    """Rows per tier of boughs, top tier first: one tier for a very short tree, two of two
    rows for four (the Peak without the Tip: a straight triangle unless `spread` steps the
    lower tier out), else tiers of three (the tier that is left over at the top is two
    rows, a row over goes to the bottom tier)."""
    if f <= 3:
        return [f] if f > 0 else []
    if f == 4:
        return [2, 2]
    n3, rem = divmod(f, 3)
    sizes = [3] * n3
    if rem == 1:
        sizes[-1] += 1
    elif rem == 2:
        sizes.insert(0, 2)
    return sizes


def _snap(h: float, half: bool) -> float:
    """The smallest half-width >= h that a centred row can have: a half row's cells sit at
    k + 0.5, a whole row's at k."""
    if half:
        return math.ceil(h - 0.5 - 1e-6) + 0.5
    return float(math.ceil(h - 1e-6))


def tree_plan(layout, idx: list[int], spread: float = 0.0) -> dict | None:
    """The tree drawn on the target's honeycomb, or None when the target has no zigzag
    (then the flat tree is used). Cached per layout, target and spread."""
    spread = round(max(0.0, min(3.0, spread)) * 2) / 2
    key = ("treeplan", tuple(idx), spread)
    cache = geometry._cache(layout)
    if key in cache:
        return cache[key]
    hc = hexcanvas.true(layout, idx)
    if hc is None or hc.top < 3:
        cache[key] = None
        return None
    top = hc.top
    tip = hc.widths[-1] <= 1                       # a single apex bulb (the Tip)
    role: dict[tuple, str] = {}

    def put(r, x, what):
        c = hc.cell(r, x)
        if c is not None and c.key not in role:
            role[c.key] = what

    # the star: the apex (+ the two under it with the Tip), rays either side
    if tip:
        put(top, 0, "star")
        put(top - 1, -0.5, "star")
        put(top - 1, 0.5, "star")
        ray_at = [(top - 1, -1.5), (top - 1, 1.5)]
        fol_top = top - 2
    else:
        for c in hc.apex_cells:
            role[c.key] = "star"
        sx = max(abs(c.x) for c in hc.apex_cells)
        ray_at = [(top, -sx - 1), (top, sx + 1)]
        fol_top = top - 1
    # the boughs, in TRUE proportions (a row is hc.aspect bulb spacings tall, ~0.66 on the
    # house): a fir is ~1.5x taller than it is wide, so its sides grow by the narrowest
    # step the honeycomb allows, half a bulb a row (the 60-degree lattice line; rows
    # alternate whole and half places, so a centred row is always one bulb wider than the
    # one above). Tiers of three rows step back in by half a bulb under the tier above (a
    # fir's layered silhouette at the same slender slope); `spread` steps each tier out
    # further (wider, squatter trees).
    frows = list(range(fol_top, 0, -1))           # top first
    sizes = _tier_sizes(len(frows))
    hw, n_r, tier_of, bottom_rows, hw_of = None, 0, {}, set(), {}
    tier_top = None
    for t_i, n in enumerate(sizes):
        for j in range(n):
            r = frows[n_r]
            n_r += 1
            half = bool(hc.row(r)) and hc.row(r)[0].d % 2 != 0
            if hw is None:
                hw = 0.5 if half else 0.0
            elif j == 0:
                hw = _snap(tier_top + 0.5 + spread, half)
            else:
                hw += 0.5
            if j == 0:
                tier_top = hw
            lim = min(hw, max(abs(c.x) for c in hc.row(r)) - 1.0) if hc.row(r) else hw
            hw_of[r] = lim
            tier_of[r] = t_i
            if j == n - 1:
                bottom_rows.add(r)
            for c in hc.row(r):
                if abs(c.x) <= lim + 1e-6 and c.key not in role:
                    role[c.key] = "leaf"
    # dimmer rays either side of the star, only over a wide tree: over a slender one they
    # make the top a bar as wide as the boughs below it (a T, not a star)
    slender = max(hw_of.values(), default=0.0) <= 2.0
    if not slender:
        for r, x in ray_at:
            put(r, x, "ray")
    # the trunk: the middle of the bottom row, one bulb under a slender tree (three under a
    # wide one), so it is narrower than the boughs as a real trunk is
    hem = hw_of.get(1, hw or 0.0)
    th = 1.0 if hem >= 2.5 else 0.5
    for c in hc.row(0):
        if abs(c.x) <= th + 1e-6:
            role[c.key] = "trunk"
    allowed = hc.allowed
    px = layout.pixels
    star, rays, trunk, sky = [], [], [], []
    leaf: dict[int, tuple] = {}
    span = max(1, fol_top - 1)
    for c in hc.cells:
        if c.i is None or c.i not in allowed:
            continue
        w = role.get(c.key, "sky")
        if w == "star":
            star.append(c.i)
        elif w == "ray":
            rays.append(c.i)
        elif w == "trunk":
            trunk.append(c.i)
        elif w == "leaf":
            h = (c.r - 1) / span
            hwr = hw_of.get(c.r, 1.0)
            edge = abs(c.x) >= hwr - 0.5 - 1e-6
            leaf[c.i] = (c.r, c.x, h, tier_of.get(c.r, 0), edge, c.r in bottom_rows, c.key)
        else:
            sky.append(c.i)
    # where the star shines from (layout units), for its halo and sparks
    sc = [c for c in hc.cells if role.get(c.key) == "star"]
    sx = sum(c.pos[0] for c in sc) / len(sc) if sc else 0.0
    sy = sum(c.pos[1] for c in sc) / len(sc) if sc else 0.0
    on = set(star) | set(rays) | set(trunk) | set(leaf) | set(sky)
    rest = [i for i in idx if 0 <= i < len(px) and i not in on and hc.of(i) is None]
    dist = {i: math.hypot(px[i].x - sx, px[i].y - sy) for i in idx if 0 <= i < len(px)}
    # sky bulbs touching the tree stay black: a clean margin round the silhouette
    tree_keys = {k for k, w in role.items() if w in ("leaf", "trunk", "star")}
    margin = set()
    for i in sky:
        c = hc.of(i)
        if any(nb.key in tree_keys for nb in hc.neighbours(c)):
            margin.add(i)
    # ornament slots: pairs of side-by-side boughs, never touching each other
    pairs = []
    for c in hc.cells:
        if role.get(c.key) != "leaf":
            continue
        n2 = hc.cell(c.r, c.x + 1)
        if n2 is not None and role.get(n2.key) == "leaf":
            pairs.append((c, n2))
    # a stable id per bulb for its sparkle (bulb indexes move when a bulb dies)
    sid = {}
    for i in idx:
        if 0 <= i < len(px):
            c = hc.of(i)
            sid[i] = (1 << 20) + (c.r << 10) + c.d + 512 if c is not None else \
                (zlib.crc32(str(px[i].mac).encode()) % 4093 << 12) + px[i].local
    out = {"hc": hc, "star": star, "sid": sid,
           "leaf_cells": sum(1 for w in role.values() if w == "leaf"),
           "leaf_inner": {v[6] for v in leaf.values() if not v[4]} | {
               c.key for c in hc.cells if c.i is None and role.get(c.key) == "leaf"
               and abs(c.x) < hw_of.get(c.r, 1.0) - 0.5 - 1e-6}, "rays": rays, "trunk": trunk, "leaf": leaf, "sky": sky,
           "margin": margin, "rest": rest, "dist": dist, "pairs": pairs, "tip": tip,
           "rows": top + 1, "fol_top": fol_top, "tiers": len(sizes), "slender": slender,
           "ph": {i: (hash01(*v[6], 11) * TAU, 0.25 + 0.35 * hash01(*v[6], 12), hash01(*v[6], 13))
                  for i, v in leaf.items()}}
    cache[key] = out
    return out


def _hexdist(a, b) -> int:
    """Steps between two cells on the honeycomb."""
    dq, dr = a.q - b.q, a.r - b.r
    return (abs(dq) + abs(dr) + abs(dq + dr)) // 2


def hex_ornaments(plan: dict, density: float, seed: int, ncol: int, state: dict | None = None) -> dict:
    """Ornaments as bold PAIRS of side-by-side bulbs (single bulbs on a slender tree):
    {bulb index: colour index}. Placed by lattice key (dead bulbs never move them), never touching each other."""
    density = round(max(0.0, density) * 100) / 100
    key = ("hex", id(plan), density, seed, ncol)
    memo = state if state is not None else plan
    hit = memo.get("horn")
    if hit is not None and hit[0] == key:
        return hit[1]
    hc = plan["hc"]
    nleaf = plan["leaf_cells"]                    # dead bulbs included: nothing moves when one dies
    if plan.get("slender"):
        # a slender tree is only two to four bulbs across: a pair would fill a row, so
        # its ornaments are single bulbs with green between them, inside the silhouette
        # (one on the edge reads as a bough sticking out)
        want = int(round(density * nleaf * 1.2))
        cand = sorted(((c,) for c in hc.cells if c.key in plan["leaf_inner"]),
                      key=lambda p: hash01(*p[0].key, seed, 41))
        gap = 1
    else:
        want = int(round(density * nleaf / 2.2))
        cand = sorted(plan["pairs"], key=lambda p: hash01(*p[0].key, seed, 41))
        gap = 2
    taken: list = []
    chosen = []
    for grp in cand:
        if len(chosen) >= want:
            break
        # green between ornaments (two bulbs of it round pairs), so each reads on its own
        if any(_hexdist(c, o) <= gap for c in grp for o in taken):
            continue
        # keep the top tier's tip and the star's neighbourhood clear
        if grp[0].r >= plan["fol_top"]:
            continue
        chosen.append(grp)
        taken += list(grp)
    chosen.sort(key=lambda p: (-p[0].r, p[0].d))
    out = {}
    k0 = int(hash01(seed, 43) * max(1, ncol))
    for n, grp in enumerate(chosen):
        k = (k0 + n) % max(1, ncol)
        for c in grp:
            if c.i is not None and c.i in plan["leaf"]:
                out[c.i] = k
    memo["horn"] = (key, out)
    return out


def _burst_state(clock, P, state, intro, dim, seed):
    B, D = P["burst_every"], P["intro"]
    burst_age = 1e9
    if B > 0 and intro is None and dim >= 1.0:
        bc = clock - state["start"] - max(0.0, D)
        k = math.floor(bc / B)
        for kk in (k, k - 1):
            st = kk * B + B * (0.3 + 0.5 * hash01(kk, seed, 61))
            if kk >= 0 and bc >= st:
                burst_age = min(burst_age, bc - st)
    big = 1.0
    ignite = clock - state.get("ignite_at", -1e9)
    if 0 <= ignite < burst_age:
        burst_age, big = ignite, 1.5
    return burst_age, big, ignite


def _tree_hex(t, ctx, P, state, plan) -> list:
    layout = ctx.layout
    n = len(layout.pixels)
    out = [BLACK] * n
    clock = ctx.clock
    intro, dim = _timeline(clock, P, state)
    D = P["intro"]
    seed = P["seed"]
    if intro is not None:
        x = intro / D
        front = x / 0.55 * 1.35 - 0.2              # the lighting front, in tree heights
        trunk_on = _smooth(x / 0.08)
        star_on = _smooth((intro - 0.82 * D) / 0.15)
        glow_on = _smooth((x - 0.84) / 0.16)
    else:
        front, trunk_on, star_on, glow_on = 9.0, 1.0, 1.0, 1.0
    burst_age, big, ignite = _burst_state(clock, P, state, intro, dim, seed)
    burst = envelope(burst_age, 0.05, 0.7) if burst_age < 6 else 0.0
    dist, sid = plan["dist"], plan["sid"]
    sparking = bool(plan["star"]) and burst_age < 2.5
    if sparking:
        ring_r = burst_age * 9.0
        fall = math.exp(-burst_age / 0.9) * big
        flick = int(clock * 20)

    def sparks(i: int) -> float:
        d = dist.get(i, 99.0)
        if d > ring_r + 2.5 or d > 12:
            return 0.0
        e = math.exp(-((d - ring_r) / 1.1) ** 2)
        if d < ring_r:
            hsh = hash01(sid[i], flick, seed, 3)
            if hsh > 0.65:
                e = max(e, (hsh - 0.65) / 0.35 * math.exp(-(ring_r - d) / 3.0))
        return min(1.0, e * fall * (0.55 + 0.45 * hash01(sid[i], flick, seed, 4)))

    def spark_on(c, e):
        return (c[0] + (1 - c[0]) * e, c[1] + (0.9 - c[1]) * e, c[2] + (0.55 - c[2]) * e)

    # ---- night sky round the tree: dim, a clean black margin, a few far stars
    sk, skl, skt = P["sky"], P["sky_level"], P["sky_twinkle"]
    margin = plan["margin"]
    for i in plan["sky"]:
        if i in margin:
            c = BLACK
        else:
            c = (sk[0] * skl, sk[1] * skl, sk[2] * skl)
            if skt > 0:
                g = glint(sid[i], clock, skt, 0.4, 1.0, seed + 5, 1)[0] * 0.15
                if g > 0:
                    c = (c[0] + (0.8 - c[0]) * g, c[1] + (0.85 - c[1]) * g, c[2] + (1.0 - c[2]) * g)
        if sparking and hash01(sid[i], flick, seed, 6) > 0.7:   # only a sprinkle: the sky stays dark
            e = sparks(i) * 0.6
            if e > 0:
                c = spark_on(c, e)
        out[i] = _clamp01((c[0] * dim, c[1] * dim, c[2] * dim))

    # ---- trunk
    tk = P["trunk"]
    lvl = P["level"]
    for i in plan["trunk"]:
        v = lvl * 0.8 * trunk_on * dim
        out[i] = _clamp01((tk[0] * v, tk[1] * v, tk[2] * v))

    # ---- boughs, garland, ornaments
    body, body2 = P["body"], P["body2"]
    shim, shade = P["shimmer"], P["shade"]
    gcol, gstyle = P["garland"], P["garland_style"]
    gw, gsp = P["garland_width"], P["garland_spacing"]
    period = max(3.0, gsp * 0.4)
    ghalf = max(0.5, gw / 4)
    tr = travel(t, P) * 0.5
    gspark, grate = P["garland_sparkle"], P["glints"]
    ocols = (P["ornament_colors"] or [(1.0, 0.0, 0.0)])[:3]
    orn = hex_ornaments(plan, P["density"], seed, len(ocols), state)
    ov = min(1.0, lvl * 1.15)
    soft = 0.22
    ph = plan["ph"]
    ghost = 0.12 * P["pre_glow"] if intro is not None else 0.0
    for i, (r, x, h, tier, edge, bottom, key) in plan["leaf"].items():
        if intro is not None:
            lit = _smooth((front - h * 0.85 - 0.15 * min(1.0, abs(x) / 4)) / soft)
        else:
            lit = 1.0
        a, rate, mix = ph[i]
        w = 1 - shim * (0.5 + 0.5 * math.sin(clock * rate * TAU + a))
        v = lvl * w * (1 - shade * (1 - h))
        c = lerp(body, body2, mix)
        c = (c[0] * v, c[1] * v, c[2] * v)
        if i in orn:
            oc = ocols[orn[i] % len(ocols)]
            oc = (oc[0] * ov, oc[1] * ov, oc[2] * ov)
            if intro is not None:
                pop_t = D * (0.5 + 0.3 * (h * 0.7 + 0.3 * hash01(*key, seed, 47)))
                age = intro - pop_t
                if age >= 0:
                    g = envelope(age, 0.02, 0.35)
                    c = lerp(c, oc, _smooth(age / 0.08))
                    c = (c[0] + (1 - c[0]) * g, c[1] + (1 - c[1]) * g * 0.9, c[2] + (1 - c[2]) * g * 0.7)
            else:
                g = glint(sid[i], clock, grate, 0.03, 0.4, seed, 1)[0] * 0.6 if grate > 0 else 0.0
                c = (oc[0] + (1 - oc[0]) * g, oc[1] + (1 - oc[1]) * g, oc[2] + (1 - oc[2]) * g)
        elif gstyle != "none":
            if gstyle == "swag":
                u = x - 0.5 * r - tr                      # along a 60-degree lattice line
                d = u % period
                d = min(d, period - d)
                cov = max(0.0, min(1.0, ghalf + 0.5 - d))
            else:                                         # strand: each tier's lowest row
                cov = 0.0
                if bottom:
                    wave = 0.5 + 0.5 * math.cos((x - tr) / max(1.5, gsp * 0.25) * TAU)
                    cov = max(0.0, min(1.0, (wave - 0.4) * 3.0))      # dashes creeping along
            if cov > 0:
                gc = gcol
                if gspark > 0:
                    g = glint(sid[i], clock, gspark * 0.5, 0.02, 0.18, seed + 1, 2)[0] * cov
                    gc = (gc[0] + (1 - gc[0]) * g, gc[1] + (1 - gc[1]) * g, gc[2] + (1 - gc[2]) * g * 0.8)
                gv = min(1.0, lvl * 1.1)
                c = lerp(c, (gc[0] * gv, gc[1] * gv, gc[2] * gv), cov)
        if intro is not None and abs(front - h * 0.85) < soft * 1.6:
            e = max(0.0, 1 - abs(front - h * 0.85 - soft * 0.5) / (soft * 1.1))
            hs = hash01(*key, int(clock * 15), seed)
            if hs > 0.6:
                c = lerp(c, (1.0, 0.82, 0.55), 0.85 * e * (0.4 + 1.5 * (hs - 0.6)))
        if sparking:
            e = sparks(i) * 0.5
            if e > 0:
                c = spark_on(c, e)
        c = (c[0] * lit * dim, c[1] * lit * dim, c[2] * lit * dim)
        if ghost > 0 and lit < 1:                         # the unlit tree, waiting
            g = ghost * (1 - lit) * dim
            c = (max(c[0], body[0] * g), max(c[1], body[1] * g), max(c[2], body[2] * g))
        out[i] = _clamp01(c)

    # ---- the star (and its rays)
    sc = P["star"]
    if star_on > 0:
        pulse = 1 - P["star_pulse"] * (0.5 + 0.5 * math.sin(clock * TAU / 3.2))
        b = min(1.0, burst * big)
        for i in plan["star"]:
            tw = glint(sid[i], clock, 0.8, 0.03, 0.25, seed + 2, 1)[0]
            v = 0.82 * pulse * (1 - b) + b
            c = (sc[0] * v, sc[1] * v, sc[2] * v)
            hot = min(1.0, 0.15 + 0.5 * b + 0.35 * tw)
            c = (c[0] + (1 - c[0]) * hot, c[1] + (1 - c[1]) * hot, c[2] + (1 - c[2]) * hot * 0.75)
            out[i] = _clamp01((c[0] * star_on * dim, c[1] * star_on * dim, c[2] * star_on * dim))
        for i in plan["rays"]:
            tw = glint(sid[i], clock, 0.5, 0.05, 0.4, seed + 3, 1)[0]
            v = (0.3 + 0.5 * b + 0.4 * tw) * pulse
            out[i] = _clamp01((sc[0] * v * star_on * dim, sc[1] * v * star_on * dim, sc[2] * v * star_on * dim))

    # ---- the rest of the target (the gable outline): a soft warm frame, the star's halo
    gl, glv = P["glow"], P["glow_level"]
    glow_on = max(glow_on, P["pre_glow"])
    halo = P["halo"]
    if not plan["tip"]:
        # without the Tip the outline's apex sits right over the tree's small star: a full
        # halo there glows bigger than the star itself and reads as a second, floating star
        halo *= 0.4
    flash = P["ignite_flash"] * envelope(ignite, 0.04, 0.5) if 0 <= ignite < 4 else 0.0
    for i in plan["rest"]:
        v = glv * (0.85 + 0.15 * wobble(sid[i], clock, 0.3, seed))
        c = (gl[0] * v, gl[1] * v, gl[2] * v)
        d = dist.get(i, 99.0)
        if halo > 0 and d < 3.5 and plan["star"]:
            f = min(1.0, halo * 1.4 * (1 - d / 3.5) ** 1.2)
            c = lerp(c, sc, f * star_on)
        c = (c[0] * glow_on, c[1] * glow_on, c[2] * glow_on)
        if sparking:
            e = sparks(i)
            if e > 0:
                c = spark_on(c, e)
        if flash > 0:
            c = lerp(c, (1.0, 0.95, 0.85), flash)
        out[i] = _clamp01((c[0] * dim, c[1] * dim, c[2] * dim))
    return out


@frame_effect("tree", "Christmas tree", TREE_PARAMS,
              "A Christmas tree drawn for the Peak's honeycomb: a tiered fir with straight "
              "60-degree sides against a dark night sky, a trunk on the bottom row, a garland "
              "swag along the lattice diagonal, bold paired ornaments in two or three colours "
              "and a bright star on top (on the Tip's apex when there is one) that breathes and "
              "now and then bursts into sparks. Starts with a tree-lighting: trunk, boughs from "
              "the bottom up, ornaments pop on, the star ignites last. The rest of the target "
              "(e.g. the gable outline) glows warmly round it. Without a zigzag in the target "
              "the whole target is lit as a tree, row by row.",
              group="Scenes")
def tree(t, ctx, P, state):
    n = len(ctx.layout.pixels)
    idx = [i for i in ctx.idx if 0 <= i < n]
    if not idx:
        return [BLACK] * n
    plan = tree_plan(ctx.layout, idx, P["spread"])
    if plan is None:
        return _tree_flat(t, ctx, P, state)
    return _tree_hex(t, ctx, P, state, plan)



# ------------------------------------------------------------------ presents
@frame_effect("presents", "Presents", [
    Param("colors", "colors", ["#ff0000", "#1040ff", "#ff0060", "#a020ff", "#00a000"], "Wrapping paper",
          help="rich, saturated colours that differ from the ribbon and the glow"),
    Param("ribbon", "color", "#fff0c0", "Ribbon colour", help="the centre bulb of each present"),
    Param("background", "color", "#ff8a30", "Glow colour", help="warm glow in the gaps between the presents"),
    Param("glow", "number", 0.12, "Glow between presents", 0, 1, 0.01, help="0 = gaps dark"),
    Param("size", "int", 5, "Present size (bulbs)", 2, 12, 1,
          help="odd sizes put the ribbon dead centre; even sizes get a two-bulb ribbon"),
    Param("level", "number", 0.75, "Brightness", 0.05, 1, 0.01),
    Param("twinkle", "number", 0.35, "Twinkle", 0, 1, 0.05, help="how much the wrapping shimmers"),
    Param("glints", "number", 0.3, "Ribbon glints per second", 0, 4, 0.05),
    Param("seed", "int", 5, "Seed", 0, 9999, 1, help="which present gets which wrapping"),
], "A row of wrapped presents under the tree: blocks of coloured wrapping, each tied with a "
   "glinting pale-gold ribbon in its centre, on a warm glow. Boxes start whole at the edge of the "
   "target. Made for the porch under the front gable.",
   group="Scenes")
def presents(t, ctx, P, state):
    n = len(ctx.layout.pixels)
    out = [BLACK] * n
    idx = [i for i in ctx.idx if 0 <= i < n]
    if not idx:
        return out
    size = max(2, int(P["size"]))
    period = size + 1
    seed = P["seed"]
    lv, tw, gr = P["level"], P["twinkle"], P["glints"]
    bg, gv, rb = P["background"], P["glow"], P["ribbon"]
    cols = P["colors"] or [(1.0, 0.0, 0.0)]
    nc = len(cols)
    rib = {(size - 1) // 2, size // 2}           # centre bulb(s)
    clock = ctx.clock

    def wrap(k):
        return int(hash01(k, seed, 9) * nc) % nc

    for q, i in enumerate(idx):                 # numbered within the target, so boxes start whole
        k, j = divmod(q, period)
        if j == size:                            # the gap between presents
            g = gv * (0.8 + 0.2 * wobble(i, clock, 0.3, seed))
            out[i] = _clamp01((bg[0] * g, bg[1] * g, bg[2] * g))
        elif j in rib:
            g = glint(i, clock, gr, 0.03, 0.35, seed, 1)[0] if gr > 0 else 0.0
            out[i] = _clamp01((rb[0] * lv + (1 - rb[0] * lv) * g, rb[1] * lv + (1 - rb[1] * lv) * g,
                               rb[2] * lv + (1 - rb[2] * lv) * g * 0.8))
        else:
            ci = wrap(k)
            if k > 0 and nc > 1 and ci == wrap(k - 1):
                ci = (ci + 1) % nc
            c = cols[ci]
            v = lv * (1 - tw * 0.5 * (1 - wobble(i, clock, 0.5, seed)))
            out[i] = _clamp01((c[0] * v, c[1] * v, c[2] * v))
    return out
