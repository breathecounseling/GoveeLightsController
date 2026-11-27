"""December pictures for the zigzag: the Peak as a tiny picture screen (six rows, about 27
bulbs across at the bottom and 10 at the top), with a matching trim on every other run.

    peak_picture  One of several winter pictures drawn on the zigzag with a gentle
                  animation, while the rest of the target (eaves, porch, garage, a fourth
                  run) gets a trim that goes with it and keeps time with the picture:

        wreath       a green wreath with a red bow, berries and little lights that twinkle,
                     a gold light running round the ring; trim: warm white garland with
                     red berry glints
        snowman      a snowman's face (coal eyes that blink, carrot nose, coal smile, rosy
                     cheeks) in a red bobble hat and a striped scarf, snow falling round
                     him; trim: icy blue with drifting snow glints
        gingerbread  a gingerbread house: white icing, gumdrops on the roof, a candy door
                     and two windows glowing warm; trim: a slow candy-cane stripe
        present      a red present with a gold ribbon and bow that unties itself, the lid
                     lifts, gold light pours out, then it wraps itself again; trim: red and
                     gold that flashes gold when the present opens
        star         a big white-gold five-pointed star filling the gable, its icy glow
                     pulsing outward; trim: deep blue with pulses running out from the gable
        ornament     a red bauble with a gold cap hanging from the apex, swinging gently,
                     a glint on its shine; trim: green garland with gold beads
        rudolph      Rudolph's face (brow, ears, eyes that blink, a tan muzzle) under
                     branching antlers, his red nose at the bottom blinking; trim: night
                     blue with stars and a red glow that runs out with each blink
        joy          J, O, Y in gold on red, one big letter at a time (strokes two bulbs
                     thick), then a burst of gold glitter under a star;
                     trim: red and gold with glitter

The picture is drawn in "bulb units" (X across, about one unit per bulb, 0 at the
middle; Y up, 0 on the bottom row and 5 on the top row), so shapes keep their proportions
on the house's wide, flat triangle. Every moving edge is anti-aliased between bulbs.
Without a zigzag in the target the target's bounding box is the canvas.
"""
from __future__ import annotations

import math

from ..color import BLACK, hash01, lerp, noise2
from ..effects import Param, frame_effect
from ..sparkle import glint, wobble
from .showpieces import _apex, _clamp, canvas

TAU = 2 * math.pi
STYLES = ["wreath", "snowman", "gingerbread", "present", "star", "ornament", "rudolph", "joy"]

WARM = (1.0, 0.6, 0.24)
GOLD = (1.0, 0.62, 0.1)
NIGHT = (0.0, 0.03, 0.28)


def _sm(x: float) -> float:
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    return x * x * (3 - 2 * x)


def _cov(sd: float, soft: float = 0.45) -> float:
    """Coverage of a shape at signed distance sd (negative inside), soft at the edge."""
    return min(1.0, max(0.0, 0.5 - sd / (2 * soft)))


def _ell(X, Y, cx, cy, a, b) -> float:
    """Signed distance (roughly, in bulb units) to an ellipse."""
    return (math.hypot((X - cx) / a, (Y - cy) / b) - 1.0) * min(a, b)


def _box(X, Y, x0, x1, y0, y1) -> float:
    dx = max(x0 - X, X - x1)
    dy = max(y0 - Y, Y - y1)
    return max(dx, dy)


def _seg(X, Y, ax, ay, bx, by) -> float:
    """Distance to the segment a-b (bulb units)."""
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 <= 0 else min(1.0, max(0.0, ((X - ax) * dx + (Y - ay) * dy) / L2))
    return math.hypot(X - ax - t * dx, Y - ay - t * dy)


def _poly(X, Y, pts) -> float:
    """Signed distance to a closed polygon (negative inside)."""
    d = min(_seg(X, Y, *pts[k - 1], *pts[k]) for k in range(len(pts)))
    inside = False
    for k in range(len(pts)):
        (x0, y0), (x1, y1) = pts[k - 1], pts[k]
        if (y0 > Y) != (y1 > Y) and X < x0 + (Y - y0) * (x1 - x0) / (y1 - y0):
            inside = not inside
    return -d if inside else d


def _star5(cx, cy, r, sx, inner=0.4):
    """A five-pointed star's outline, point up, stretched sx times across."""
    pts = []
    for k in range(10):
        a = math.pi / 2 + k * math.pi / 5
        rr = r if k % 2 == 0 else r * inner
        pts.append((cx + rr * math.cos(a) * sx, cy + rr * math.sin(a)))
    return pts


def _sc(c, v):
    return (c[0] * v, c[1] * v, c[2] * v)


def _mix(a, b, w):
    return lerp(a, b, min(1.0, max(0.0, w)))


# ====================================================================== the pictures
# each takes (X, Y, i, T, P, g) and returns a colour; g carries per-frame values

def _wreath(X, Y, i, T, P, g):
    bg = _sc(NIGHT, 0.5)
    cy, a, b = 2.75, 6.6, 2.6
    r = math.hypot(X / a, (Y - cy) / b)
    c = bg
    ring = _cov((abs(r - 0.76) - 0.26) * 4.5, 0.5)
    if ring > 0:
        n = noise2(X * 0.7, Y * 0.9, 7)
        green = _mix((0.0, 0.55, 0.06), (0.05, 0.85, 0.12), n)
        if hash01(i, 41) < 0.16:                          # red berries
            green = (1.0, 0.02, 0.02)
        elif hash01(i, 42) < 0.14:                        # little warm lights that twinkle
            tw = glint(i, T, 0.35, 0.15, 0.6, 5)[0]
            green = _mix(_sc((1.0, 0.8, 0.45), 0.8), (1.0, 0.95, 0.8), tw)
        ang = math.atan2((Y - cy) / b, X / a) / TAU % 1.0   # a gold light running round
        d = (ang - g["run"]) % 1.0
        d = min(d, 1 - d)
        green = _mix(green, GOLD, 0.9 * max(0.0, 1 - d / 0.05))
        c = _mix(c, green, ring)
    # the bow, low in the middle: two loops, a knot and two tails
    br = 0.9 + 0.1 * math.sin(T * 1.3)
    loops = min(_ell(X, Y, -1.7, 0.75, 1.5 * br, 0.6), _ell(X, Y, 1.7, 0.75, 1.5 * br, 0.6))
    knot = _ell(X, Y, 0, 0.6, 0.6, 0.6)
    tails = min(_box(X, Y, -2.6 - 0.4 * br, -0.6, -0.4, 0.4), _box(X, Y, 0.6, 2.6 + 0.4 * br, -0.4, 0.4))
    red = (1.0, 0.0, 0.03)
    bow = _cov(min(loops, tails))
    c = _mix(c, red, bow)
    c = _mix(c, (1.0, 0.18, 0.12), _cov(knot))
    return c


def _snowman(X, Y, i, T, P, g):
    # falling snow round him on a night-blue sky
    sky = _sc(NIGHT, 0.55)
    fl = (hash01(int((X + 20) * 1.0 + 0.5), 7) * 4 + T * 0.55) % 4.0
    snow = max(0.0, 1 - abs((Y + fl) % 4.0 - 2.0) / 0.45) if hash01(int(X + 40.5), 3) < 0.55 else 0.0
    c = _mix(sky, (0.75, 0.82, 1.0), snow * 0.8)
    face = (0.86, 0.92, 1.0)
    head = _ell(X, Y, 0, 1.6, 6.2, 2.6)
    c = _mix(c, face, _cov(head))
    if head < 0.3:
        # rosy cheeks
        c = _mix(c, (1.0, 0.3, 0.42), _cov(min(_ell(X, Y, -3.4, 2.07, 0.9, 0.4), _ell(X, Y, 3.4, 2.07, 0.9, 0.4))))
        # coal eyes (they blink: the lids close for a moment)
        if abs(Y - 3.25) < 0.5:
            eyes = abs(abs(X) - 2.0) - 0.6
            c = _mix(c, (0.0, 0.0, 0.0), _cov(eyes, 0.15) * (1 - g["blink"]))
        # the carrot: thick at the face, thinner and deeper to the tip, a little wiggle
        tip = 3.6 + 0.35 * math.sin(T * 2.2)
        if 0.0 <= X <= tip and abs(Y - 2.07) < 0.5:
            f = X / tip
            c = _mix(c, _mix((1.0, 0.42, 0.0), (0.9, 0.18, 0.0), f), _cov(f - 1.0, 0.08))
        # coal smile on the row below
        if abs(Y - 1.18) < 0.5:
            for k in (-2.4, -1.2, 0.0, 1.2, 2.4):
                c = _mix(c, (0.0, 0.0, 0.0), _cov(abs(X - k) - 0.35, 0.2))
    # a striped scarf on the bottom row, its end blowing a little
    if Y < 0.5:
        flap = 0.6 * math.sin(T * 1.7)
        if abs(X) < 6.2 + (flap if X > 0 else 0):
            stripe = (math.floor((X + 20) / 1.5) % 2 == 0)
            c = (1.0, 0.02, 0.04) if stripe else (0.0, 0.7, 0.12)
    # a red bobble hat above his face with a white fur brim and bobble
    if Y > 3.9:
        if Y < 4.7 and abs(X) < 4.4:
            c = (0.9, 0.92, 1.0)
        elif Y >= 4.7:
            if abs(X) < 0.9:
                c = (1.0, 1.0, 1.0)
            elif abs(X) < 3.6:
                c = (1.0, 0.0, 0.03)
    return c


def _gingerbread(X, Y, i, T, P, g):
    ginger = (0.62, 0.17, 0.0)
    icing = (0.95, 0.95, 1.0)
    edge = i in g["ends"]                                # bulbs on the triangle's outline
    if Y > 2.6:                                          # the roof (the zigzag's own triangle)
        if edge:
            return _sc(icing, 0.85 + 0.15 * glint(i, T, 0.25, 0.05, 0.4, 2)[0])
        k = int(math.floor(X + 0.5))
        if k % 3 == 0:
            gum = [(1.0, 0.0, 0.05), (0.0, 0.85, 0.1), (1.0, 0.85, 0.0), (0.6, 0.0, 1.0)][(k // 3 + int(Y)) % 4]
            return _sc(gum, 0.75 + 0.25 * wobble(i, T, 0.5, 4))
        return _sc(ginger, 0.85)
    if Y > 1.6:                                          # the icing along the eave, dripping
        return _mix(icing, ginger, 0.5 if int(math.floor(X + 0.5)) % 3 == 0 else 0.0)
    # the walls sit in from the roof's edge, with snow on the ground either side
    if abs(X) > 7.6:
        return _sc((0.55, 0.65, 1.0), 0.5) if Y < 0.5 else _sc(NIGHT, 0.5)
    if abs(X) < 1.3:                                     # a candy-cane door
        return (1.0, 0.05, 0.1) if (math.floor((X + Y + 9) * 1.2) % 2 == 0) else (1.0, 0.85, 0.85)
    for wx in (-4.6, 4.6):                               # two windows glowing warm
        if abs(X - wx) < 1.4 and Y > 0.6:
            f = 0.75 + 0.25 * wobble(i, T, 1.6, 11)
            return _sc((1.0, 0.75, 0.2), f)
    if abs(abs(X) - 7.6) < 0.5:                          # icing on the corners
        return _sc(icing, 0.8)
    return ginger


def _present(X, Y, i, T, P, g):
    ph = g["phase"]                                      # 0..1 through one wrap-open-wrap cycle
    red, gold = (1.0, 0.0, 0.04), (1.0, 0.66, 0.0)
    bg = _sc(NIGHT, 0.45)
    # timeline: 0-.35 wrapped and twinkling, .35-.5 the bow unties and the ribbon slides off,
    # .5-.6 the lid lifts, .6-.85 light pours out, .85-1 it wraps itself again
    untie = _sm((ph - 0.35) / 0.15) * (1 - _sm((ph - 0.86) / 0.1))
    lift = _sm((ph - 0.5) / 0.1) * (1 - _sm((ph - 0.86) / 0.1))
    glow = _sm((ph - 0.58) / 0.06) * (1 - _sm((ph - 0.8) / 0.08))
    c = bg
    box = _box(X, Y, -5.2, 5.2, -0.5, 2.6)
    if box < 0.5:
        c = _mix(c, red, _cov(box))
        rib = abs(X) - 0.6 + untie * 7.0                 # the ribbon shrinks away from the middle
        rib_h = abs(Y - 1.18) - 0.45 + untie * 2.0
        if rib < 0 or (rib_h < 0 and abs(X) > untie * 5.5):
            c = gold
    # the lid: row 3 when shut, lifts and tilts away when open
    ly = 3.25 + lift * 1.75
    lx = lift * 8.0                                      # it slides off to the side
    lid = _box(X, Y, -5.6 + lx, 5.6 + lx, ly - 0.45, ly + 0.45)
    if lid < 0.5:
        c = _mix(c, red, _cov(lid))
        if abs(X - lx) < 0.6 and untie < 0.5:
            c = gold
    # the bow on top (unties: its loops shrink)
    bs = 1 - untie
    if bs > 0.05:
        loops = min(_ell(X, Y, -1.6, 4.7, 1.4 * bs, 0.55), _ell(X, Y, 1.6, 4.7, 1.4 * bs, 0.55))
        c = _mix(c, gold, _cov(loops) * bs)
        c = _mix(c, (1.0, 0.85, 0.3), _cov(_ell(X, Y, 0, 4.4, 0.6, 0.5)) * bs)
    if glow > 0 and Y > 2.5 and abs(X) < 8.0 and _box(X, Y, -5.6 + lx, 5.6 + lx, ly - 0.45, ly + 0.45) > 0.3:
        # gold light pours up out of the open box, with sparkles
        ray = 0.55 + 0.45 * math.cos(math.atan2(Y - 2.0, X) * 9 - T * 3.0)
        v = glow * (1 - abs(X) / 9.0) * ray * 1.4
        sp = glint(i, T, 2.0, 0.03, 0.3, 17)[0] * glow
        c = _mix(c, (1.0, 0.8, 0.35), min(1.0, v + sp))
    return c


# a five-pointed star drawn for the six rows (a regular one loses its feet between rows):
# its top point on the top two rows, arms on the fourth, two legs apart on the bottom rows
_STAR_R = [(0.0, 6.0), (1.7, 4.0), (6.6, 3.5), (3.4, 1.9), (6.4, -0.9), (0.0, 1.6)]
STAR = _STAR_R + [(-x, y) for x, y in reversed(_STAR_R[1:-1])]


def _star(X, Y, i, T, P, g):
    # a big five-pointed star filling the gable (its top point on the top row, its arms
    # on the middle row, its feet on the bottom row), white-gold with an icy glow and
    # pulses of light running outward through the night round it
    bg = _sc(NIGHT, 0.6)
    tw = glint(i, T, 0.12, 0.2, 0.8, 9)[0]
    c = _mix(bg, (0.6, 0.7, 1.0), tw * 0.5)
    sd = _poly(X, Y, STAR)
    r = math.hypot(X / 2.3, Y - 2.6)
    if sd > 0:                                           # the glow and the outward pulses
        pulse = 0.5 + 0.5 * math.cos((r - T * 2.2) * 2.0)
        glow = max(0.0, 1 - sd / 1.6) * (0.25 + 0.75 * pulse ** 3)
        c = _mix(c, (0.3, 0.55, 1.0), glow * 0.6)
    core = max(0.0, 1 - r / 1.6) * (0.5 + 0.5 * math.sin(T * 1.5))
    body = _mix((1.0, 0.82, 0.4), (1.0, 1.0, 0.95), core + 0.25)
    body = _mix(body, (1.0, 1.0, 1.0), glint(i, T, 0.5, 0.06, 0.5, 13)[0])
    return _mix(c, body, _cov(sd, 0.35))


def _ornament(X, Y, i, T, P, g):
    ang = g["swing"]
    px, py = 0.0, 8.5                                    # the pivot, above the apex
    L = 6.0
    cx, cy = px + L * math.sin(ang), py - L * math.cos(ang)
    bg = _sc((0.0, 0.45, 0.06), 0.5)                    # dark green, like the tree behind
    tw = glint(i, T, 0.15, 0.1, 0.7, 21)[0]
    c = _mix(bg, WARM, tw * 0.7)
    # the hook string to the cap
    t = (Y - cy) / (py - cy) if py != cy else 0.0
    if 0 < t <= 1 and Y > cy + 2.2:
        sx = cx + (px - cx) * t
        c = _mix(c, (1.0, 0.75, 0.3), _cov(abs(X - sx) - 0.15, 0.4) * 0.8)
    # the cap
    cap = _box(X - cx, Y - cy, -1.1, 1.1, 2.2, 2.9)
    c = _mix(c, GOLD, _cov(cap, 0.4))
    ball = _ell(X, Y, cx, cy, 3.3, 2.35)
    if ball < 0.6:
        # shading: brighter up-left, deeper red at the rim, a gold band round the middle
        rx, ry = (X - cx) / 3.3, (Y - cy) / 2.35
        shade = 0.6 + 0.4 * max(0.0, 1 - math.hypot(rx + 0.35, ry - 0.35))
        col = _sc((1.0, 0.0, 0.05), shade)
        if abs(Y - cy) < 0.45:
            col = (1.0, 0.6, 0.05)
        hl = max(0.0, 1 - math.hypot(rx + 0.45, ry - 0.5) / 0.32)
        col = _mix(col, (1.0, 0.85, 0.8), hl * (0.6 + 0.4 * g["shine"]))
        c = _mix(c, col, _cov(ball, 0.45))
    return c


def _rudolph(X, Y, i, T, P, g):
    # his face from the front: a broad brow narrowing to his muzzle, ears out to the
    # sides, branching antlers on the top rows, and his nose at the bottom of his face
    # (the brightest thing in the picture) blinking, its glow spilling on to his muzzle
    bg = _sc(NIGHT, 0.6)
    tw = glint(i, T, 0.12, 0.2, 0.9, 31)[0]
    c = _mix(bg, (0.8, 0.85, 1.0), tw * 0.7)
    fur = (0.5, 0.17, 0.0)
    muzzle = (0.85, 0.5, 0.2)
    antler = (1.0, 0.8, 0.5)
    ax = abs(X)
    horns = min(_seg(ax, Y, 1.6, 3.6, 5.5, 4.6), _seg(ax, Y, 2.5, 3.9, 1.5, 5.2),
                _seg(ax, Y, 4.5, 4.3, 4.4, 5.2))
    c = _mix(c, antler, _cov(horns - 0.45, 0.3))
    head = _poly(ax, Y, [(0.0, 3.8), (3.4, 3.7), (3.7, 2.6), (2.2, 0.9), (1.6, -0.4), (0.0, -0.4)])
    ears = _ell(ax, Y, 4.9, 3.2, 1.5, 0.45)
    c = _mix(c, fur, _cov(min(head, ears), 0.4))
    c = _mix(c, muzzle, _cov(_ell(X, Y, 0, 0.4, 2.3, 1.15), 0.4))
    eyes = _ell(ax, Y, 1.6, 2.1, 0.55, 0.45)
    c = _mix(c, (1.0, 1.0, 1.0), _cov(eyes, 0.3) * (1 - g["blink"]))
    nb = g["nose"]
    halo = max(0.0, 1 - math.hypot(X / 3.0, Y / 1.4)) * nb
    c = _mix(c, (1.0, 0.15, 0.05), halo * 0.5)
    nose = _ell(X, Y, 0, 0.1, 1.4, 0.75)
    return _mix(c, _sc((1.0, 0.0, 0.0), 0.3 + 0.7 * nb), _cov(nose, 0.35))


# JOY one big letter at a time (six rows are too few for three letters side by side to
# read from the street): each letter is drawn with strokes two bulbs thick, filling the
# gable, and the word ends with a burst of gold glitter under a star
JOY_STEP, JOY_BURST = 2.2, 1.8
JOY_CYCLE = 3 * JOY_STEP + JOY_BURST


def _letter(ch, X, Y) -> float:
    w = 0.85                                             # half the stroke width
    if ch == "J":
        return min(_seg(X, Y, -3.2, 4.75, 3.6, 4.75), _seg(X, Y, 1.8, 4.75, 1.8, 1.6),
                   abs(_ell(X, Y, -0.6, 1.6, 2.4, 1.6)) + (0 if Y <= 1.6 else 9),
                   _seg(X, Y, -3.0, 1.6, -3.0, 2.2)) - w
    if ch == "O":
        return abs(_ell(X, Y, 0, 2.5, 4.2, 2.5)) - w
    return min(_seg(X, Y, -4.0, 5.0, 0, 2.3), _seg(X, Y, 4.0, 5.0, 0, 2.3),
               _seg(X, Y, 0, 2.3, 0, -0.3)) - w


def _joy(X, Y, i, T, P, g):
    red = (0.32, 0.0, 0.01)
    gold = (1.0, 0.62, 0.05)
    k, u = g["joy"]
    if k < 3:
        # the letter wipes in from the left, a shimmer runs through it, then it fades
        sd = _letter("JOY"[k], X, Y)
        wipe = _sm((u * 6.0 - (X + 6.0) / 12.0) / 0.25)
        fade = 1 - _sm((u - 0.86) / 0.14)
        s = (X + 8.0 - u * 30.0) % 24.0
        shimmer = max(0.0, 1 - abs(s - 12) / 2.0)
        col = _mix(gold, (1.0, 0.95, 0.75), shimmer)
        return _mix(red, col, _cov(sd, 0.35) * wipe * fade)
    # the burst: gold glitter all over the gable, a star on the top rows
    sp = glint(i, T, 3.0, 0.05, 0.35, 23)[0]
    fade = (1 - _sm((u - 0.7) / 0.3)) * _sm(u / 0.1)
    c = _mix(red, (1.0, 0.85, 0.4), sp * fade)
    st = _cov(_poly(X, Y, _star5(0.0, 4.4, 1.0, 2.0)), 0.35)
    return _mix(c, (1.0, 0.95, 0.7), st * fade)


DRAW = {"wreath": _wreath, "snowman": _snowman, "gingerbread": _gingerbread, "present": _present,
        "star": _star, "ornament": _ornament, "rudolph": _rudolph, "joy": _joy}


# ====================================================================== the trims
def _trim(style, i, d, f, T, g, seed):
    """Colour of a bulb off the zigzag: d = distance from the apex (layout units, along
    x mostly), f = 0..1 height. Each trim goes with its picture."""
    if style == "wreath":
        base = _sc(WARM, 0.8 + 0.2 * wobble(i, T, 0.3, seed))
        b = glint(i, T, 0.12, 0.15, 0.9, seed + 1)[0]
        return _mix(base, (1.0, 0.0, 0.02), b)
    if style == "snowman":
        base = _sc((0.25, 0.55, 1.0), 0.45 + 0.1 * wobble(i, T, 0.2, seed))
        s = glint(i, T, 0.25, 0.1, 1.2, seed + 2)[0]
        return _mix(base, (0.95, 0.97, 1.0), s)
    if style == "gingerbread":
        k = math.floor((d * 0.6 - T * 0.8) / 2.0) % 2
        return (1.0, 0.02, 0.04) if k == 0 else (0.85, 0.85, 0.85)
    if style == "present":
        k = math.floor((d * 0.5 + T * 0.5) / 2.0) % 2
        base = _sc((1.0, 0.0, 0.04), 0.7) if k == 0 else _sc(GOLD, 0.7)
        flash = g["flash"]
        sp = glint(i, T, 1.5, 0.03, 0.4, seed + 3)[0] * flash
        return _mix(base, (1.0, 0.85, 0.45), min(1.0, flash * 0.6 + sp))
    if style == "star":
        base = _sc(NIGHT, 0.8)
        p = 0.5 + 0.5 * math.cos((d - T * 6.0) * 0.35)
        return _mix(base, (0.8, 0.9, 1.0), p ** 6 * 0.9)
    if style == "ornament":
        base = _sc((0.0, 0.6, 0.08), 0.6 + 0.15 * wobble(i, T, 0.25, seed))
        if hash01(i, seed, 5) < 0.2:
            return _sc(GOLD, 0.8 + 0.2 * glint(i, T, 0.3, 0.1, 0.6, seed + 4)[0])
        w = glint(i, T, 0.1, 0.15, 0.8, seed + 5)[0]
        return _mix(base, WARM, w)
    if style == "rudolph":
        tw = glint(i, T, 0.1, 0.2, 1.0, seed + 6)[0]
        c = _mix(_sc(NIGHT, 0.7), (0.85, 0.9, 1.0), tw)
        ripple = max(0.0, 1 - abs(d - g["ripple"]) / 3.0) * max(0.0, 1 - g["ripple"] / 70.0)
        return _mix(c, (1.0, 0.02, 0.0), ripple)
    # joy
    k = math.floor((d * 0.5 - T * 0.6) / 1.5) % 2
    base = _sc((1.0, 0.0, 0.03), 0.75) if k == 0 else _sc(GOLD, 0.75)
    s = glint(i, T, 0.3, 0.04, 0.5, seed + 7)[0]
    return _mix(base, (1.0, 0.95, 0.8), s)


# ====================================================================== the effect
@frame_effect("peak_picture", "December pictures", [
    Param("style", "choice", "wreath", "Picture", options=STYLES,
          help="wreath, snowman, gingerbread house, a present that opens, a winter star, a swinging "
               "ornament, Rudolph, or JOY"),
    Param("level", "number", 0.85, "Picture brightness", 0.2, 1, 0.01),
    Param("trim_level", "number", 0.7, "Trim brightness", 0, 1, 0.01,
          help="the eaves, porch and garage: a trim that goes with the picture (0 = dark)"),
    Param("speed", "number", 1.0, "Animation speed", 0.25, 3, 0.05),
    Param("cycle", "number", 18, "Present only: opens every (s)", 8, 60, 1,
          help="only used by the present: the time to untie, open, shine and wrap again"),
    Param("seed", "int", 8, "Seed", 0, 9999, 1),
], "The Peak as a little picture screen for December: a wreath with a bow, a snowman, a "
   "gingerbread house, a present that unwraps itself, a winter star, a swinging ornament, "
   "Rudolph with his blinking nose, or JOY. Everything else in the target (the eaves, porch, "
   "garage) gets a trim that goes with the picture. Leave the target as the whole house. "
   "Made for a Peak with six rows of zigzag; on a different zigzag the picture still "
   "lights but may look squashed or cut off.",
   group="Scenes")
def peak_picture(t, ctx, P, st):
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
    T = (clock - st["start"]) * P["speed"]
    style = P["style"] if P["style"] in DRAW else "wreath"
    lvl, tl, seed = P["level"], P["trim_level"], P["seed"]

    geo = st.get("geo")
    if geo is None or st.get("geo_key") != id(cv):
        # bulb units: one unit per bulb across (the bottom row's spacing), 5 between the
        # bottom and top rows
        px = L.pixels
        zig = cv["zig"]
        rows: dict[int, list[int]] = {}
        for i in zig:
            rows.setdefault(round(cv["uv"][i][1] * 20), []).append(i)
        bottom = sorted(rows.get(min(rows), []) if rows else [], key=lambda i: px[i].x)
        if len(bottom) > 1:
            pitch = (px[bottom[-1]].x - px[bottom[0]].x) / (len(bottom) - 1)
        else:
            pitch = 1.0
        hw = (max(px[i].x for i in zig) - min(px[i].x for i in zig)) / 2 if zig else 1.0
        sx = hw / max(1e-6, abs(pitch)) if pitch else 1.0
        xy = {i: (u * sx, v * 5.0) for i, (u, v) in cv["uv"].items()}
        ends = set()
        for r in rows.values():                    # the outline: each row's two end bulbs
            r = sorted(r, key=lambda i: px[i].x)
            ends.update((r[0], r[-1]))
        ax, ay = cv["apex"]
        rest = [(i, math.hypot(px[i].x - ax, (px[i].y - ay) * 0.5),
                 (px[i].y - L.miny) / max(1e-6, L.maxy - L.miny)) for i in cv["rest"]]
        geo = st["geo"] = {"xy": xy, "ends": ends, "rest": rest}
        st["geo_key"] = id(cv)
    xy, ends = geo["xy"], geo["ends"]

    g = {"ends": ends}
    # per-frame values shared by the bulbs
    g["run"] = (T * 0.12) % 1.0
    bt = T % 5.3                                             # a blink every few seconds
    g["blink"] = max(0.0, 1 - abs(bt - 4.9) / 0.15)
    g["phase"] = (T / max(1.0, P["cycle"])) % 1.0
    ph = g["phase"]
    g["flash"] = _sm((ph - 0.58) / 0.04) * (1 - _sm((ph - 0.72) / 0.12))
    g["swing"] = 0.22 * math.sin(T * TAU / 3.4)
    g["shine"] = max(0.0, math.cos(T * TAU / 3.4))
    nt = T % 2.4                                             # Rudolph's nose: blink, and a double blink
    nose = max(math.exp(-((nt - 0.3) / 0.22) ** 2), 0.8 * math.exp(-((nt - 0.95) / 0.18) ** 2))
    g["nose"] = 0.25 + 0.75 * nose
    g["ripple"] = nt * 30.0
    jt = T % JOY_CYCLE                                       # JOY: which letter, how far in
    g["joy"] = (int(jt // JOY_STEP), (jt % JOY_STEP) / JOY_STEP) if jt < 3 * JOY_STEP \
        else (3, (jt - 3 * JOY_STEP) / JOY_BURST)

    draw = DRAW[style]
    for i, (X, Y) in xy.items():
        c = draw(X, Y, i, T, P, g)
        frame[i] = _sc(c, lvl)
    if tl > 0:
        for i, d, f in geo["rest"]:
            frame[i] = _sc(_trim(style, i, d, f, T, g, seed), tl)
    if tl > 0 and style in ("star", "ornament") and cv["rest"]:
        for i in _apex(L, idx, 1):                 # the bulb crowning the picture
            frame[i] = _sc((1.0, 0.95, 0.8) if style == "star" else GOLD, lvl)
    return _clamp(frame, idx)
