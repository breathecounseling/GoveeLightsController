"""December pictures for the zigzag: the Peak as a tiny picture screen, with a matching
trim on every other run.

The Peak is a triangle of OFFSET rows (27, 23, 20, 16, 12 and 10 bulbs, and with the Tip
7, 4 and 1 on up to the apex): every bulb hangs midway between two bulbs of the row
below, so the screen is a honeycomb, not a square grid (see hexcanvas.py). Each picture
is drawn for that lattice, bulb by bulb: bold shapes two bulbs thick, every bulb either
fully in the picture or dark (no dim half-lit edges), a few strong colours, centred on
the wide rows and with dark space round it so it reads from the street at night.

    peak_picture  One of several winter pictures drawn on the zigzag with a gentle
                  animation, while the rest of the target gets a trim that goes with it.
                  The trim follows the hardware: the Eaves (a single row of bulbs
                  pointing out to the street) carry rhythm and sparkle; the Accent / porch
                  run (pointing down at the siding) washes the wall in one big colour.

        wreath       a round green wreath with a red bow at the bottom and warm lights
                     twinkling in it, a gold light running round the ring; eaves: warm
                     white with red glints; wash: deep red
        snowman      a snowman's face: a red hat, coal eyes that blink, a carrot nose, a
                     coal smile, a few flakes drifting past; eaves: icy sparkle; wash: icy blue
        gingerbread  a gingerbread house: iced roof with gumdrops, walls with two warm
                     windows and a red door; eaves: candy-cane stripe; wash: warm amber
        present      a red present with a gold ribbon and bow that unties itself, the lid
                     lifts, gold light pours out, then it wraps itself again; eaves: red and
                     gold, flashing gold when it opens; wash: red, glowing gold when it opens
        star         a big warm-white five-pointed star on the dark gable, a shimmer
                     running up through it, a few far-off stars twinkling; eaves: icy pulses
                     running out from the apex; wash: deep blue
        ornament     a big red bauble on a gold cap swinging from the apex, a glint on its
                     shine, and two little gold baubles either side; eaves: green garland
                     with gold beads; wash: warm red
        rudolph      Rudolph's face (brown, white eyes that blink) under wide branching
                     antlers, his big red nose blinking; eaves: night blue with stars and a
                     red glow that runs out with each blink; wash: blue that blushes red
        joy          J, O, Y in gold, one big letter at a time on the dark gable, then a
                     burst of gold glitter under a star; eaves: red and gold; wash: red

With the Tip (a run that carries the zigzag on up to the gable apex, see addrun.py) the
pictures use its rows: the star grows to fill the taller triangle with its top point on
the apex, the wreath hangs from a gold bow on the apex, the snowman's hat rises to a
point with its bobble on the apex, the gingerbread roof comes to an iced point, the
present's bow and the light pouring out reach up into it, the ornament's hook and the
JOY star sit on the apex and Rudolph's antlers branch up into it.

Without a zigzag in the target (a porch on its own, say) the target gets the trim.
Details (twinkles, flakes, gumdrops) are seeded per lattice cell, so nothing shifts when
a bulb is marked dead.
"""
from __future__ import annotations

import math

from ..color import BLACK, hash01, lerp
from ..effects import Param, frame_effect
from ..layout import role_of
from ..sparkle import glint, wobble
from . import hexcanvas as hx
from .showpieces import _apex, _clamp, canvas

TAU = 2 * math.pi
STYLES = ["wreath", "snowman", "gingerbread", "present", "star", "ornament", "rudolph", "joy"]

WARM = (1.0, 0.6, 0.24)
GOLD = (1.0, 0.62, 0.1)
NIGHT = (0.0, 0.03, 0.28)
RED = (1.0, 0.0, 0.03)
WHITE = (1.0, 1.0, 1.0)
SNOW = (0.85, 0.92, 1.0)


def _sm(x: float) -> float:
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    return x * x * (3 - 2 * x)


def _sc(c, v):
    return (c[0] * v, c[1] * v, c[2] * v)


def _mix(a, b, w):
    return lerp(a, b, min(1.0, max(0.0, w)))


def _seed(cell) -> int:
    """A per-cell seed that never moves (the cell's place on the lattice)."""
    return (cell.r + 20) * 1000 + cell.d + 500


# ====================================================================== designs
# A design is rows of spans: {row: [(x from, x to, character), ...]}, x in columns (0 is
# the centre line; even rows sit on whole columns, odd rows on half columns, as on the
# house), every bulb from..to inclusive. Characters are looked up by each picture's
# shader. Rows 0-5 are the Peak, 6-8 the Tip.

def _cells(hc, design: dict) -> list:
    """[(cell, character)] of a design on this screen (spans off the screen drop out)."""
    out = []
    for r, spans in design.items():
        for a, b, ch in spans:
            n = int(round(b - a))
            for k in range(n + 1):
                c = hc.cell(r, a + k)
                if c is not None:
                    out.append((c, ch))
    return out


def _with(base: dict, **rows) -> dict:
    d = dict(base)
    for k, v in rows.items():
        d[int(k[1:])] = v
    return d


# ---- the star: its top point on the top row, arms across the middle, legs apart
STAR = {
    5: [(-0.5, 0.5, "S")],
    4: [(-1, 1, "S")],
    3: [(-5.5, -1.5, "S"), (-0.5, 0.5, "C"), (1.5, 5.5, "S")],
    2: [(-3, -1, "S"), (0, 0, "C"), (1, 3, "S")],
    1: [(-2.5, -1.5, "S"), (-0.5, 0.5, "S"), (1.5, 2.5, "S")],
    0: [(-4, -3, "S"), (3, 4, "S")],
}
# with the Tip: a taller star, its top point on the apex
STAR_TIP = {
    8: [(0, 0, "S")],
    7: [(-0.5, 0.5, "S")],
    6: [(-1, 1, "S")],
    5: [(-4.5, -0.5, "S"), (0.5, 4.5, "S")],
    4: [(-3, -1, "S"), (0, 0, "C"), (1, 3, "S")],
    3: [(-2.5, -0.5, "S"), (0.5, 2.5, "S")],
    2: [(-3, -1, "S"), (1, 3, "S")],
    1: [(-4.5, -2.5, "S"), (2.5, 4.5, "S")],
    0: [(-5, -4, "S"), (4, 5, "S")],
}

# ---- the wreath: a ring two bulbs thick round a dark middle, the bow at the bottom
WREATH = {
    5: [(-2.5, 2.5, "G")],
    4: [(-5, -2, "G"), (2, 5, "G")],
    3: [(-6.5, -3.5, "G"), (3.5, 6.5, "G")],
    2: [(-7, -5, "G"), (-3, -2, "R"), (2, 3, "R"), (5, 7, "G")],
    1: [(-6.5, -3.5, "G"), (-2.5, -1.5, "R"), (-0.5, 0.5, "K"), (1.5, 2.5, "R"), (3.5, 6.5, "G")],
    0: [(-5, -3, "G"), (-1, -1, "R"), (1, 1, "R"), (3, 5, "G")],
}
WREATH_TIP = {          # hung from a gold bow on the apex by a red ribbon
    8: [(0, 0, "Y")],
    7: [(-1.5, -0.5, "Y"), (0.5, 1.5, "Y")],
    6: [(0, 0, "r")],
}

# ---- the snowman's face under a red hat
SNOWMAN = {
    5: [(-3.5, 3.5, "H")],
    4: [(-5, 5, "H")],
    3: [(-4.5, -3.5, "W"), (-2.5, -1.5, "E"), (-0.5, 0.5, "W"), (1.5, 2.5, "E"), (3.5, 4.5, "W")],
    2: [(-6, -1, "W"), (0, 3, "O"), (4, 6, "W")],
    1: [(-5.5, -3.5, "W"), (-2.5, 2.5, "M"), (3.5, 5.5, "W")],
    0: [(-4, 4, "W")],
}
SNOWMAN_TIP = {         # the hat rises to a point, its bobble on the apex
    8: [(0, 0, "P")],
    7: [(-0.5, 0.5, "H")],
    6: [(-2, 2, "H")],
}

# ---- the gingerbread house: iced roof with gumdrops over walls with windows and a door
GINGER = {
    5: [(-4.5, -4.5, "I"), (-3.5, -2.5, "g"), (-1.5, -1.5, "u"), (-0.5, 0.5, "g"), (1.5, 1.5, "v"),
        (2.5, 3.5, "g"), (4.5, 4.5, "I")],
    4: [(-6, -6, "I"), (-5, -4, "g"), (-3, -3, "v"), (-2, 1, "g"), (2, 2, "u"), (3, 4, "g"), (5, 5, "I")],
    3: [(-7.5, 7.5, "I")],
    2: [(-6, -5, "g"), (-4, -3, "Y"), (-2, 2, "g"), (3, 4, "Y"), (5, 6, "g")],
    1: [(-5.5, -4.5, "g"), (-3.5, -3.5, "Y"), (-2.5, -1.5, "g"), (-0.5, 0.5, "D"), (1.5, 2.5, "g"),
        (3.5, 3.5, "Y"), (4.5, 5.5, "g")],
    0: [(-6, -2, "g"), (-1, 1, "D"), (2, 6, "g")],
}
GINGER_TIP = {
    8: [(0, 0, "I")],
    7: [(-1.5, -1.5, "I"), (-0.5, 0.5, "g"), (1.5, 1.5, "I")],
    6: [(-3, -3, "I"), (-2, -1, "g"), (0, 0, "u"), (1, 2, "g"), (3, 3, "I")],
}

# ---- the present: a box with a gold ribbon, its lid and a bow (drawn in parts: they move)
BOX = {
    2: [(-6, -2, "B"), (-1, 1, "Y"), (2, 6, "B")],
    1: [(-5.5, -1.5, "B"), (-0.5, 0.5, "Y"), (1.5, 5.5, "B")],
    0: [(-6, -2, "B"), (-1, 1, "Y"), (2, 6, "B")],
}
LID = [(-7.5, -1.5, "B"), (-0.5, 0.5, "Y"), (1.5, 7.5, "B")]       # on a half row
BOW = {
    5: [(-3.5, -2.5, "Y"), (2.5, 3.5, "Y")],
    4: [(-4, -4, "Y"), (-2, -2, "Y"), (-1, 1, "K"), (2, 2, "Y"), (4, 4, "Y")],
}
BOW_TIP = {             # with the Tip the bow stands taller
    7: [(-1.5, -1.5, "Y"), (1.5, 1.5, "Y")],
    6: [(-3, -2, "Y"), (-1, 1, "K"), (2, 3, "Y")],
    5: [(-3.5, -3.5, "Y"), (-1.5, -1.5, "Y"), (-0.5, 0.5, "Y"), (1.5, 1.5, "Y"), (3.5, 3.5, "Y")],
    4: [(-1, 1, "Y")],
}

# ---- Rudolph: antlers, ears, a brown face with white eyes, the red nose at the bottom
RUDOLPH = {
    5: [(-4.5, -4.5, "A"), (-2.5, -2.5, "A"), (2.5, 2.5, "A"), (4.5, 4.5, "A")],
    4: [(-5, -2, "A"), (2, 5, "A")],
    3: [(-6.5, -5.5, "F"), (-3.5, 3.5, "F"), (5.5, 6.5, "F")],
    2: [(-4, -3, "F"), (-2, -2, "E"), (-1, 1, "F"), (2, 2, "E"), (3, 4, "F")],
    1: [(-3.5, -1.5, "F"), (-0.5, 0.5, "N"), (1.5, 3.5, "F")],
    0: [(-2, -2, "F"), (-1, 1, "N"), (2, 2, "F")],
}
RUDOLPH_TIP = {         # the antlers' tines reach up into the Tip
    7: [(-1.5, -1.5, "A"), (1.5, 1.5, "A")],
    6: [(-3, -3, "A"), (-2, -2, "A"), (2, 2, "A"), (3, 3, "A")],
    5: [(-4.5, -4.5, "A"), (-2.5, -2.5, "A"), (2.5, 2.5, "A"), (4.5, 4.5, "A")],
}

# ---- JOY: one big letter at a time, strokes two bulbs thick
LETTERS = {
    "J": {
        5: [(-1.5, 4.5, "L")],
        4: [(2, 3, "L")],
        3: [(1.5, 3.5, "L")],
        2: [(2, 3, "L")],
        1: [(-4.5, -3.5, "L"), (1.5, 3.5, "L")],
        0: [(-4, 2, "L")],
    },
    "O": {
        5: [(-2.5, 2.5, "L")],
        4: [(-4, -3, "L"), (3, 4, "L")],
        3: [(-5.5, -4.5, "L"), (4.5, 5.5, "L")],
        2: [(-6, -5, "L"), (5, 6, "L")],
        1: [(-5.5, -4.5, "L"), (4.5, 5.5, "L")],
        0: [(-4, 4, "L")],
    },
    "Y": {
        5: [(-4.5, -3.5, "L"), (3.5, 4.5, "L")],
        4: [(-3, -2, "L"), (2, 3, "L")],
        3: [(-1.5, 1.5, "L")],
        2: [(-1, 1, "L")],
        1: [(-0.5, 0.5, "L")],
        0: [(-1, 1, "L")],
    },
}
JOY_STAR = {
    5: [(-0.5, 0.5, "S")],
    4: [(-2, 2, "S")],
    3: [(-1.5, -1.5, "S"), (1.5, 1.5, "S")],
}
JOY_STAR_TIP = {
    8: [(0, 0, "S")],
    7: [(-1.5, 1.5, "S")],
    6: [(-1, -1, "S"), (1, 1, "S")],
}


# ====================================================================== the pictures
# each takes (hc, T, g) and returns {bulb index: colour} for the lit cells; every other
# cell of the screen stays dark

def _paint(hc, design, shade, T, g, out=None, dx=0.0, dy=0):
    out = {} if out is None else out
    if dx or dy:
        design = {r + dy: [(a + dx, b + dx, ch) for a, b, ch in sp] for r, sp in design.items()}
    for c, ch in _cells(hc, design):
        if c.i is None or c.i not in hc.allowed:
            continue
        col = shade(ch, c, T, g)
        if col is not None:
            out[c.i] = col
    return out


def _dark_cells(hc, lit):
    return [c for c in hc.cells if c.i is not None and c.i in hc.allowed and c.i not in lit]


def _sky(hc, lit, T, seed, colour, rate=0.04, peak=0.45):
    """A few faint twinkles in the dark round the picture (never more than a handful)."""
    out = {}
    for c in _dark_cells(hc, lit):
        if c.r == 0:
            continue
        v = glint(_seed(c), T, rate, 0.25, 0.9, seed)[0]
        if v > 0.02:
            out[c.i] = _sc(colour, v * peak)
    return out


def _star(hc, T, g):
    tip = hc.has_tip
    design = STAR_TIP if tip else STAR
    beat = 0.5 + 0.5 * math.sin(T * 1.5)

    def shade(ch, c, T, g):
        # warm white-gold with a shimmer running up through it
        s = 0.5 + 0.5 * math.cos((c.y * 0.9 + abs(c.x) * 0.35 - T * 2.4))
        body = _mix((1.0, 0.72, 0.28), (1.0, 0.95, 0.8), 0.35 + 0.65 * s ** 3)
        if ch == "C":
            body = _mix(body, WHITE, 0.4 + 0.5 * beat)
        return _mix(body, WHITE, glint(_seed(c), T, 0.4, 0.05, 0.4, 13)[0] * 0.8)
    lit = _paint(hc, design, shade, T, g)
    out = _sky(hc, lit, T, 9, (0.6, 0.75, 1.0), 0.02, 0.4)       # a few far-off stars
    out.update(lit)
    return out


def _wreath(hc, T, g):
    run = (T * 0.12) % 1.0
    sway = math.sin(T * 1.3)

    def shade(ch, c, T, g):
        if ch == "G":
            k = _seed(c)
            green = (0.0, 0.62, 0.08) if hash01(k, 7) < 0.6 else (0.08, 0.85, 0.14)
            if hash01(k, 42) < 0.22:                     # little warm lights that twinkle
                tw = glint(k, T, 0.35, 0.15, 0.6, 5)[0]
                green = _mix((0.85, 0.6, 0.3), (1.0, 0.95, 0.8), tw)
            ang = math.atan2(c.y - 2.6, c.x / 2.4) / TAU % 1.0  # a gold light running round
            d = (ang - run) % 1.0
            d = min(d, 1 - d)
            return _mix(green, GOLD, 0.95 * max(0.0, 1 - d / 0.06))
        if ch in "Rr":
            return _sc(RED, 0.85 + 0.15 * sway)
        if ch == "K":
            return (1.0, 0.15, 0.1)
        return _mix(GOLD, (1.0, 0.85, 0.4), 0.5 + 0.5 * sway)    # Y: the bow on the apex
    out = _paint(hc, WREATH, shade, T, g)
    if hc.has_tip:
        _paint(hc, WREATH_TIP, shade, T, g, out)
    return out


def _snowman(hc, T, g):
    blink = g["blink"]

    def shade(ch, c, T, g):
        if ch == "W":
            return SNOW
        if ch == "H":
            return RED
        if ch == "P":
            return _sc(WHITE, 0.9 + 0.1 * math.sin(T * 2.0))
        if ch == "O":                                   # the carrot, deeper to its tip
            f = c.x / 3.0
            return _mix((1.0, 0.45, 0.0), (1.0, 0.2, 0.0), f)
        if ch == "E":                                   # coal eyes: the lids close a moment
            return _sc(SNOW, blink) if blink > 0.05 else None
        return None                                     # M: the coal smile (dark)
    lit = _paint(hc, SNOWMAN, shade, T, g)
    if hc.has_tip:
        _paint(hc, SNOWMAN_TIP, shade, T, g, lit)
    face = {c.i for c, _ in _cells(hc, SNOWMAN) if c.i is not None}
    out = {}
    # a few flakes drifting down past him, out to the sides
    for c in _dark_cells(hc, lit):
        if c.i in face or abs(c.x) < 7:
            continue
        col = c.d
        if hash01(col + 400, 3) > 0.45:
            continue
        fall = (hash01(col + 400, 7) * 6 + T * 0.7) % 7.0
        v = max(0.0, 1 - abs((hc.peak_rows - fall) - c.y) / 0.6)
        if v > 0:
            out[c.i] = _sc((0.7, 0.8, 1.0), 0.5 * v)
    out.update(lit)
    return out


def _gingerbread(hc, T, g):
    def shade(ch, c, T, g):
        k = _seed(c)
        if ch == "g":
            return (0.62, 0.18, 0.0)
        if ch == "I":
            return _sc((0.95, 0.95, 1.0), 0.85 + 0.15 * glint(k, T, 0.25, 0.05, 0.4, 2)[0])
        if ch == "u":
            return (1.0, 0.0, 0.05)
        if ch == "v":
            return (0.0, 0.85, 0.1)
        if ch == "Y":                                   # windows glowing warm
            return _sc((1.0, 0.88, 0.5), 0.85 + 0.15 * wobble(k, T, 1.6, 11))
        return (1.0, 0.03, 0.08)                         # D: the red candy door
    out = _paint(hc, GINGER, shade, T, g)
    if hc.has_tip:
        _paint(hc, GINGER_TIP, shade, T, g, out)
    return out


def _present(hc, T, g):
    ph = g["phase"]                                      # 0..1 through one wrap-open-wrap cycle
    gold = (1.0, 0.66, 0.0)
    # timeline: 0-.35 wrapped, .35-.5 the bow unties and the ribbon slides off, .5-.6 the
    # lid lifts, .6-.85 light pours out, .85-1 it wraps itself again
    untie = _sm((ph - 0.35) / 0.15) * (1 - _sm((ph - 0.86) / 0.1))
    lift = _sm((ph - 0.5) / 0.1) * (1 - _sm((ph - 0.86) / 0.1))
    glow = _sm((ph - 0.52) / 0.1) * (1 - _sm((ph - 0.8) / 0.08))

    def shade(ch, c, T, g):
        if ch == "B":
            return RED
        if ch == "K":
            return _mix((1.0, 0.85, 0.3), RED, untie) if untie < 0.98 else None
        # Y: the ribbon slides off from the middle out
        gone = untie * 9.0 - abs(c.x) * 0.6 - abs(c.y - 1.5) * 0.4
        if ch == "Y" and gone > 0.5:
            return RED if c.r <= 3 else None
        return gold
    out = _paint(hc, BOX, shade, T, g)
    # the lid: row 3 when shut, lifts two rows and slides off to the side when open
    dy = int(round(lift * 2))
    dx = round(lift * 6)
    lid = {3 + dy: [(a + dx, b + dx, ch) for a, b, ch in LID]}
    lid_cells = {}
    _paint(hc, lid, shade, T, g, lid_cells)
    if untie < 0.98:
        bow = BOW_TIP if hc.has_tip else BOW
        _paint(hc, bow, shade, T, g, out, dy=dy)
    if glow > 0:
        # gold light pours up out of the open box, with sparkles
        for c in hc.cells:
            if c.i is None or c.i not in hc.allowed or c.r < 3 or c.i in lid_cells:
                continue
            ray = 0.55 + 0.45 * math.cos(math.atan2(c.y - 2.0, c.x) * 9 - T * 3.0)
            spread = max(0.0, 1 - abs(c.x) / (6.0 + (c.y - 2) * 1.0))
            v = glow * spread * ray * 1.3
            sp = glint(_seed(c), T, 2.0, 0.03, 0.3, 17)[0] * glow
            if v + sp > 0.03:
                out[c.i] = _mix(out.get(c.i, BLACK), (1.0, 0.8, 0.35), min(1.0, v + sp))
    out.update(lid_cells)
    return out


def _ornament(hc, T, g):
    ang = g["swing"]
    top = hc.top
    py = top + 3.0                                       # the pivot, above the apex
    cy = 1.9 if not hc.has_tip else 2.4
    L = py - cy
    cx = L * math.sin(ang)
    R = 2.6 if not hc.has_tip else 2.9
    out = {}
    # the hook: a gold thread from the apex to the cap
    capy = cy + R / hc.row_h
    thread = hx.Line([(cx * (py - top) / L, top + 0.2), (cx * (py - capy - 0.6) / L, capy + 0.6)], 1.05)
    for i, w in hc.raster(thread).items():
        out[i] = _sc((1.0, 0.7, 0.25), 0.85)
    for i, w in hc.raster(hx.Line([(cx - 1.0, capy - 0.2), (cx + 1.0, capy - 0.2)], 0.9)).items():
        out[i] = GOLD                                    # the cap
    # two little gold baubles hanging from the roof either side
    for sx in (-1, 1):
        bx = sx * 8.5 + 0.4 * math.sin(T * TAU / 2.9 + sx)
        for i in hc.raster(hx.Line([(sx * 8.5, 2.6), (bx, 1.8)], 0.5)):
            out[i] = _sc((1.0, 0.7, 0.25), 0.7)
        for i in hc.raster(hx.Circle(bx, 0.6, 1.2)):
            out[i] = GOLD
    # the bauble: deep red, brighter up-left, a glint on its shine
    ball = hc.raster(hx.Circle(cx, cy, R), 0.2)
    for i, w in ball.items():
        c = hc.of(i)
        rx, ry = (c.x - cx) / R, (c.y - cy) * hc.row_h / R
        shade = 0.7 + 0.3 * max(0.0, 1 - math.hypot(rx + 0.35, ry - 0.35))
        col = _sc(RED, shade)
        hl = max(0.0, 1 - math.hypot(rx + 0.45, ry - 0.45) / 0.3)
        col = _mix(col, (1.0, 0.85, 0.8), hl * (0.5 + 0.5 * g["shine"]))
        out[i] = _mix(out.get(i, BLACK), col, w)
    return out


def _rudolph(hc, T, g):
    nb = g["nose"]
    blink = g["blink"]

    def shade(ch, c, T, g):
        if ch == "A":
            return (1.0, 0.72, 0.35)
        if ch == "F":
            if c.r <= 1 and abs(c.x) < 2.6:             # his nose's glow on his muzzle
                return _mix((0.55, 0.16, 0.0), (1.0, 0.1, 0.02), 0.6 * nb)
            return (0.55, 0.16, 0.0)
        if ch == "E":
            return _sc(WHITE, 1 - blink) if blink < 0.95 else (0.55, 0.16, 0.0)
        return _sc(RED, 0.3 + 0.7 * nb)                  # N: the nose
    lit = _paint(hc, RUDOLPH, shade, T, g)
    if hc.has_tip:
        _paint(hc, RUDOLPH_TIP, shade, T, g, lit)
    out = _sky(hc, lit, T, 31, (0.8, 0.85, 1.0), 0.03, 0.45)
    out.update(lit)
    return out


# JOY one big letter at a time (six rows are too few for three letters side by side to
# read from the street), then the word ends with a burst of gold glitter under a star
JOY_STEP, JOY_BURST = 2.2, 1.8
JOY_CYCLE = 3 * JOY_STEP + JOY_BURST


def _joy(hc, T, g):
    gold = (1.0, 0.62, 0.05)
    out = {}
    k, u = g["joy"]

    def star(ch, c, T, g):
        tw = 0.8 + 0.2 * math.sin(T * 2.5)
        return _sc((1.0, 0.9, 0.6), tw)
    if hc.has_tip:                                       # a star on the apex all along
        _paint(hc, JOY_STAR_TIP, star, T, g, out)
    if k < 3:
        # the letter wipes in from the left, a shimmer runs through it, then it fades
        fade = 1 - _sm((u - 0.86) / 0.14)

        def letter(ch, c, T, g):
            wipe = 1.0 if (c.x + 7.0) / 14.0 < u * 5.0 else 0.0
            s = (c.x + 8.0 - u * 30.0) % 24.0
            shimmer = max(0.0, 1 - abs(s - 12) / 2.0)
            v = wipe * fade
            return _sc(_mix(gold, (1.0, 0.95, 0.75), shimmer), v) if v > 0.01 else None
        _paint(hc, LETTERS["JOY"[k]], letter, T, g, out)
        return out
    # the burst: gold glitter all over the gable, a star on the top rows
    fade = (1 - _sm((u - 0.7) / 0.3)) * _sm(u / 0.1)
    for c in hc.cells:
        if c.i is None or c.i not in hc.allowed:
            continue
        sp = glint(_seed(c), T, 1.0, 0.05, 0.35, 23)[0] * fade
        if sp > 0.02:
            out[c.i] = _sc((1.0, 0.85, 0.4), sp)
    if not hc.has_tip:
        _paint(hc, JOY_STAR, lambda *a: _sc((1.0, 0.95, 0.7), fade), T, g, out)
    return out


DRAW = {"wreath": _wreath, "snowman": _snowman, "gingerbread": _gingerbread, "present": _present,
        "star": _star, "ornament": _ornament, "rudolph": _rudolph, "joy": _joy}


# ====================================================================== the trims
def _trim_kind(L, name) -> str:
    """'wash' for a run that lights a wall (the Accent / porch, pointing down at the
    siding), else 'eaves' (a row of bulbs pointing out at the street)."""
    r = L.run(name) if hasattr(L, "run") else None
    role = (getattr(r, "role", None) if r is not None else None) or role_of(name)
    n = str(name).lower()
    if role in ("porch", "accent") or any(w in n for w in ("porch", "accent", "garage", "door", "window")):
        return "wash"
    return "eaves"


def _wash(style, i, f, T, g):
    """The Accent / porch: one big colour washing the siding, breathing slowly."""
    b = 0.85 + 0.15 * math.sin(T * 0.7 + f * 2.0)
    if style == "wreath":
        return _sc((1.0, 0.02, 0.03), 0.8 * b)
    if style == "snowman":
        return _sc((0.25, 0.55, 1.0), 0.75 * b)
    if style == "gingerbread":
        return _sc((1.0, 0.45, 0.08), 0.8 * b)
    if style == "present":
        return _mix(_sc(RED, 0.8 * b), (1.0, 0.7, 0.2), g["flash"])
    if style == "star":
        return _sc((0.05, 0.15, 0.85), 0.7 * b)
    if style == "ornament":
        return _sc((1.0, 0.05, 0.02), 0.75 * b)
    if style == "rudolph":
        return _mix(_sc((0.05, 0.12, 0.75), 0.7 * b), (1.0, 0.03, 0.0), 0.65 * (g["nose"] - 0.25) / 0.75)
    return _sc((1.0, 0.02, 0.05), 0.8 * b)               # joy


def _trim(style, i, d, f, T, g, seed):
    """The Eaves (and any other run): rhythm and sparkle; d = distance from the gable's
    centre line (layout units), f = 0..1 height."""
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
   "Rudolph with his blinking nose, or JOY, each drawn bulb by bulb for the Peak's offset "
   "rows, bold on a dark gable. The eaves sparkle and the porch / accent lights wash the "
   "wall in a colour that goes with the picture. Leave the target as the whole house. "
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
    clock = ctx.clock
    if "start" not in st or clock < st["start"] - 1e-6:
        st["start"] = clock
    T = (clock - st["start"]) * P["speed"]
    style = P["style"] if P["style"] in DRAW else "wreath"
    lvl, tl, seed = P["level"], P["trim_level"], P["seed"]
    hc = hx.get(L, idx)

    geo = st.get("geo")
    if geo is None or st.get("geo_key") != (id(L), tuple(idx)):
        px = L.pixels
        screen = set(hc.live()) if hc else set()
        rest_i = [i for i in idx if i not in screen]
        if hc:
            # the centre line of the gable in layout units (for the eaves' rhythm)
            bot = [c.pos[0] for c in hc.rows[0].cells]
            mid = (min(bot) + max(bot)) / 2
            cv = canvas(L, idx)
            crown_l = _apex(L, idx, 1) if cv["rest"] else []
        else:
            mid = (min(px[i].x for i in idx) + max(px[i].x for i in idx)) / 2
            crown_l = []
        # heights from the bulbs that are not a Tip, so the trims look the same with it
        ys = [p.y for p in px if not getattr(p, "ext", False)] or [0.0]
        miny, maxy = min(ys), max(ys)
        rest = [(i, abs(px[i].x - mid),
                 (px[i].y - miny) / max(1e-6, maxy - miny), _trim_kind(L, px[i].run)) for i in rest_i]
        crown = []
        if hc and hc.has_tip:
            crown = [c.i for c in hc.apex_cells if c.i is not None and c.i in hc.allowed][:1]
        geo = st["geo"] = {"rest": rest, "screen": screen, "crown": crown, "crown_l": crown_l}
        st["geo_key"] = (id(L), tuple(idx))

    g = {}
    # per-frame values shared by the bulbs
    bt = T % 5.3                                             # a blink every few seconds
    g["blink"] = max(0.0, 1 - abs(bt - 4.9) / 0.15)
    g["phase"] = (T / max(1.0, P["cycle"])) % 1.0
    ph = g["phase"]
    g["flash"] = _sm((ph - 0.58) / 0.04) * (1 - _sm((ph - 0.72) / 0.12))
    g["swing"] = 0.07 * math.sin(T * TAU / 3.4)
    g["shine"] = max(0.0, math.cos(T * TAU / 3.4))
    nt = T % 2.4                                             # Rudolph's nose: blink, and a double blink
    nose = max(math.exp(-((nt - 0.3) / 0.22) ** 2), 0.8 * math.exp(-((nt - 0.95) / 0.18) ** 2))
    g["nose"] = 0.25 + 0.75 * nose
    g["ripple"] = nt * 30.0
    jt = T % JOY_CYCLE                                       # JOY: which letter, how far in
    g["joy"] = (int(jt // JOY_STEP), (jt % JOY_STEP) / JOY_STEP) if jt < 3 * JOY_STEP \
        else (3, (jt - 3 * JOY_STEP) / JOY_BURST)

    if hc is not None:
        for i, c in DRAW[style](hc, T, g).items():
            frame[i] = _sc(c, lvl)
    if tl > 0:
        px = L.pixels                              # details are seeded per bulb (si), so they
        for i, d, f, kind in geo["rest"]:          # stay on their bulbs when one is marked dead
            si = px[i].si
            c = _wash(style, si, f, T, g) if kind == "wash" else _trim(style, si, d, f, T, g, seed)
            frame[i] = _sc(c, tl)
    if style in ("star", "ornament"):
        if geo["crown"]:
            for i in geo["crown"]:                 # with the Tip: the apex itself crowns the picture
                frame[i] = _sc((1.0, 0.95, 0.8) if style == "star" else GOLD, lvl)
        elif tl > 0:
            for i in geo["crown_l"]:               # the bulb over the gable crowning the picture
                frame[i] = _sc((1.0, 0.95, 0.8) if style == "star" else GOLD, lvl)
    return _clamp(frame, idx)
