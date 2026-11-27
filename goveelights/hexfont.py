"""A hex-native font for the Peak: letters and numbers drawn for a honeycomb of bulbs.

The Peak's zigzag is a triangular screen whose rows are offset half a bulb from the row
below (the owner's diagram of the bottom two rows):

           11    10     9     8     7
        1      2      3     4     5     6

so its bulbs sit on a honeycomb (hex) lattice, not a square grid. A stroke at 60 degrees
runs straight from bulb to bulb; a vertical stroke is a zig-zag that steps half a bulb
left and right from row to row; a horizontal stroke is a row of bulbs.

Each glyph is a set of strokes (polylines) on a design box 4 units tall (0 = baseline,
4 = cap height) and a few units wide. The strokes are placed on the lattice and every
bulb is lit by how close it is to a stroke (its coverage), so one outline gives both
sizes:

    big      one letter at a time, filling the Peak's six rows (cap height = 5 row
             steps, 1.25 bulbs per design unit across), strokes two bulbs thick
    marquee  a line of text that steps across the Peak (the same six rows, 1.5 bulbs per
             design unit across), strokes one bulb thick

The hard letters (M W N K R S G Q and 2 5 6 8 9 0) have their own outline per size where
the shared one does not land cleanly on the lattice.

    lattice(layout, idx)          -> Lattice: the zigzag's bulbs as honeycomb cells
    Lattice.ideal(counts)         -> a Lattice of made-up rows (tests, ASCII previews)
    glyph_strokes(ch, size)       -> the strokes of one glyph at a size (design units)
    advance(ch, size)             -> how far the next glyph starts (bulbs)
    render_big(lat, ch, ...)      -> {bulb: coverage} for one big centred glyph
    render_line(lat, text, x, ...) -> {bulb: (coverage, glyph number)} for marquee text
    ascii(lat, cov)               -> the lit bulbs as text, top row first (for eyeballing)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

SQ = math.sqrt(3) / 2          # row spacing of a honeycomb, in bulb spacings
CAP = 4.0                      # design units from baseline to cap height

# --------------------------------------------------------------------------- the font
# Strokes per glyph, in design units: (width, [polyline, ...]). Width 3 is the norm;
# a polyline is a list of (x, y) points. Round shapes are drawn as octagons: on six rows
# a curve is a corner cut at 60 degrees, which the lattice draws as a clean diagonal.
_O = [(1, 0), (2, 0), (3, 1), (3, 3), (2, 4), (1, 4), (0, 3), (0, 1), (1, 0)]

GLYPHS: dict[str, tuple[float, list]] = {
    "A": (3, [[(0, 0), (0, 3), (1, 4), (2, 4), (3, 3), (3, 0)], [(0, 2), (3, 2)]]),
    "B": (3, [[(0, 0), (0, 4), (2, 4), (3, 3.2), (2, 2), (0, 2)], [(2, 2), (3, 1), (2, 0), (0, 0)]]),
    "C": (3, [[(3, 3.4), (2, 4), (1, 4), (0, 3), (0, 1), (1, 0), (2, 0), (3, 0.6)]]),
    "D": (3, [[(0, 0), (0, 4), (2, 4), (3, 3), (3, 1), (2, 0), (0, 0)]]),
    "E": (3, [[(3, 4), (0, 4), (0, 0), (3, 0)], [(0, 2), (2.2, 2)]]),
    "F": (3, [[(3, 4), (0, 4), (0, 0)], [(0, 2), (2.2, 2)]]),
    "G": (3, [[(3, 3.4), (2, 4), (1, 4), (0, 3), (0, 1), (1, 0), (2, 0), (3, 1), (3, 2), (1.8, 2)]]),
    "H": (3, [[(0, 0), (0, 4)], [(3, 0), (3, 4)], [(0, 2), (3, 2)]]),
    "I": (2, [[(1, 0), (1, 4)], [(0, 4), (2, 4)], [(0, 0), (2, 0)]]),
    "J": (3, [[(3, 4), (3, 1), (2, 0), (1, 0), (0, 1)]]),
    "K": (3, [[(0, 0), (0, 4)], [(3, 4), (0.6, 2), (3, 0)]]),
    "L": (3, [[(0, 4), (0, 0), (3, 0)]]),
    "M": (4, [[(0, 0), (0, 4), (2, 2), (4, 4), (4, 0)]]),
    "N": (3, [[(0, 0), (0, 4), (3, 0), (3, 4)]]),
    "O": (3, [_O]),
    "P": (3, [[(0, 0), (0, 4), (2, 4), (3, 3), (2, 2), (0, 2)]]),
    "Q": (3, [_O, [(1.8, 1.2), (3.2, -0.2)]]),
    "R": (3, [[(0, 0), (0, 4), (2, 4), (3, 3), (2, 2), (0, 2)], [(1.4, 2), (3, 0)]]),
    "S": (3, [[(3, 3.4), (2, 4), (1, 4), (0, 3), (1, 2), (2, 2), (3, 1), (2, 0), (1, 0), (0, 0.6)]]),
    "T": (4, [[(0, 4), (4, 4)], [(2, 4), (2, 0)]]),
    "U": (3, [[(0, 4), (0, 1), (1, 0), (2, 0), (3, 1), (3, 4)]]),
    "V": (4, [[(0, 4), (2, 0), (4, 4)]]),
    "W": (4, [[(0, 4), (0, 0), (2, 2), (4, 0), (4, 4)]]),
    "X": (3, [[(0, 4), (3, 0)], [(0, 0), (3, 4)]]),
    "Y": (4, [[(0, 4), (2, 2), (4, 4)], [(2, 2), (2, 0)]]),
    "Z": (3, [[(0, 4), (3, 4), (0, 0), (3, 0)]]),
    "0": (3, [_O]),
    "1": (2, [[(0, 3), (1, 4), (1, 0)]]),
    "2": (3, [[(0, 3), (1, 4), (2, 4), (3, 3), (0, 0), (3, 0)]]),
    "3": (3, [[(0, 4), (3, 4), (1.5, 2.3), (2, 2.3), (3, 1.3), (3, 1), (2, 0), (1, 0), (0, 1)]]),
    "4": (3, [[(2.4, 0), (2.4, 4), (0, 1.4), (3, 1.4)]]),
    "5": (3, [[(3, 4), (0, 4), (0, 2.3), (2, 2.3), (3, 1.3), (3, 1), (2, 0), (0, 0)]]),
    "6": (3, [[(3, 4), (1, 4), (0, 3), (0, 1), (1, 0), (2, 0), (3, 1), (2, 2), (0, 2)]]),
    "7": (3, [[(0, 4), (3, 4), (1, 0)]]),
    "8": (3, [[(1, 2), (0, 3), (1, 4), (2, 4), (3, 3), (2, 2), (1, 2), (0, 1), (1, 0), (2, 0), (3, 1), (2, 2)]]),
    "9": (3, [[(3, 2), (1, 2), (0, 3), (1, 4), (2, 4), (3, 3), (3, 1), (2, 0), (0, 0)]]),
    "!": (1, [[(0.5, 4), (0.5, 1.6)], [(0.5, 0), (0.5, 0.01)]]),
    "?": (3, [[(0, 3), (1, 4), (2, 4), (3, 3), (1.5, 1.6), (1.5, 1.2)], [(1.5, 0), (1.5, 0.01)]]),
    "-": (2, [[(0, 2), (2, 2)]]),
    "+": (3, [[(0, 2), (3, 2)], [(1.5, 0.6), (1.5, 3.4)]]),
    ".": (1, [[(0.5, 0), (0.5, 0.01)]]),
    ",": (1, [[(0.8, 0.3), (0.2, -0.6)]]),
    "'": (1, [[(0.5, 4), (0.5, 3)]]),
    ":": (1, [[(0.5, 0.6), (0.5, 0.61)], [(0.5, 3), (0.5, 3.01)]]),
    "&": (3, [[(3, 0), (0.6, 3.2), (1.2, 4), (1.9, 3.3), (0, 1), (0.8, 0), (1.8, 0), (3, 1.5)]]),
    "#": (4, [[(1.3, 0), (1.8, 4)], [(2.5, 0), (3, 4)], [(0.4, 1.3), (3.8, 1.3)], [(0.4, 2.7), (3.8, 2.7)]]),
    "/": (3, [[(0, 0), (3, 4)]]),
    "♥": (4, [[(2, 0), (0, 2.4), (0.4, 3.6), (1.2, 4), (2, 3.2), (2.8, 4), (3.6, 3.6), (4, 2.4), (2, 0)],
              [(2, 0.8), (2, 3)], [(1, 2.4), (3, 2.4)], [(1.2, 3), (2.8, 3)], [(1.6, 1.6), (2.4, 1.6)]]),
    " ": (2, []),
}
HEART = "♥"

# Per size, glyphs redrawn where the shared outline does not land cleanly on the lattice.
# Big overrides are drawn a row at a time: y in steps of 0.8 is one of the six rows.
_OB = [(1, 0), (2.5, 0), (3.5, 0.8), (3.5, 3.2), (2.5, 4), (1, 4), (0, 3.2), (0, 0.8), (1, 0)]
_8B = [(1, 2.4), (0, 3.2), (1, 4), (2, 4), (3, 3.2), (2, 2.4), (1, 2.4), (0, 1.6), (0, 0.8), (1, 0),
       (2, 0), (3, 0.8), (3, 1.6), (2, 2.4)]
OVERRIDES: dict[str, dict[str, tuple[float, list]]] = {
    "big": {
        "G": (3, [[(3, 4), (1, 4), (0, 3.2), (0, 0.8), (1, 0), (2, 0), (3, 0.8), (3, 2.4), (1.6, 2.4)]]),
        "O": (3.5, [_OB]),
        "Q": (3.5, [_OB, [(2.2, 1.6), (3.8, -0.3)]]),
        # S: rounded at both ends, its top end open on the right (a 9 and an 8 are closed
        # there) and a hook low on the left (a 5 has a hard corner)
        "S": (3, [[(3, 3.7), (2.4, 4), (1, 4), (0, 3.2), (1, 2.4), (2, 2.4), (3, 1.6), (3, 0.8), (2, 0), (1, 0),
                   (0, 0.8)]]),
        # P: a bowl three rows deep (an F's bar is a row higher and open)
        "P": (3, [[(0, 0), (0, 4), (2, 4), (3, 3.2), (3, 2.4), (2, 1.6), (0, 1.6)]]),
        "5": (3, [[(3, 4), (0, 4), (0, 2.4), (2, 2.4), (3, 1.6), (3, 0.8), (2, 0), (0, 0)]]),
        # an open 4: the left stem stops at the bar, the right one runs the full height
        "4": (3, [[(0, 4), (0, 1.6), (3, 1.6)], [(2.5, 4), (2.5, 0)]]),
        # a slashed zero, so 0 never reads as O (which is also wider)
        "0": (3, [[(1, 0), (2, 0), (3, 0.8), (3, 3.2), (2, 4), (1, 4), (0, 3.2), (0, 0.8), (1, 0)],
                  (0.5, [(1.0, 1.4), (2.0, 2.6)])]),
        "8": (3, [_8B]),
        "M": (4, [[(0, 0), (0, 4)], [(4, 0), (4, 4)], (0.5, [(0, 4), (2, 1.6), (4, 4)])]),
        "N": (3, [[(0, 0), (0, 4)], [(3, 0), (3, 4)], (0.6, [(0, 4), (3, 0)])]),
        "W": (4, [[(0, 0), (0, 4)], [(4, 0), (4, 4)], (0.5, [(0, 0), (2, 2.4), (4, 0)])]),
        # X: slimmer where the strokes cross, full weight on the legs (never spidery)
        "X": (3, [(0.7, [(0.75, 3), (2.25, 1)]), (0.7, [(0.75, 1), (2.25, 3)]),
                  [(0, 4), (0.75, 3)], [(2.25, 3), (3, 4)], [(0, 0), (0.75, 1)], [(2.25, 1), (3, 0)]]),
        "K": (3, [[(0, 0), (0, 4)], [(3, 4), (0.4, 2)], [(0.9, 2.4), (3, 0)]]),
        "Z": (3, [[(0, 4), (3, 4)], (0.7, [(3, 4), (0, 0)]), [(0, 0), (3, 0)]]),
        "2": (3, [[(0, 3.2), (1, 4), (2, 4), (3, 3.2), (3, 2.4)], (0.7, [(3, 2.4), (0, 0)]), [(0, 0), (3, 0)]]),
        "R": (3, [[(0, 0), (0, 4), (2, 4), (3, 3.2), (2, 2.4), (0, 2.4)], [(1.6, 2.4), (3, 0)]]),
    },
    "marquee": {},
}

# design units -> lattice: (bulbs per unit across, rows from baseline to cap, stroke half-width)
SIZES = {
    "big": {"sx": 2.0, "rows": 5, "hw": 1.0, "gap": 2.0},
    "marquee": {"sx": 1.5, "rows": 5, "hw": 0.5, "gap": 2.0},     # the same six rows, slimmer
}
SOFT = 0.06                    # how gently a stroke's edge fades between bulbs


def normalise(text: str) -> str:
    s = str(text).upper().replace("<3", HEART)
    return "".join(ch for ch in s if ch in GLYPHS)


def _glyph(ch: str, size: str) -> tuple[float, list]:
    o = OVERRIDES.get(size, {}).get(ch)
    if o is None and SIZES[size]["rows"] == SIZES["big"]["rows"]:
        o = OVERRIDES["big"].get(ch)              # the same six rows: the same hand-drawn outline
    return o or GLYPHS.get(ch) or GLYPHS[" "]


def glyph_strokes(ch: str, size: str = "marquee") -> list:
    return _glyph(ch, size)[1]


def width(ch: str, size: str = "marquee") -> float:
    """A glyph's width in bulbs at a size."""
    return _glyph(ch, size)[0] * SIZES[size]["sx"]


def advance(ch: str, size: str = "marquee") -> float:
    return width(ch, size) + SIZES[size]["gap"]


# --------------------------------------------------------------------------- the lattice
@dataclass
class Lattice:
    """The zigzag as honeycomb cells: per bulb (index, X, row), X in bulbs from the
    middle of the bottom row, rows from 0 at the bottom; Y = row * SQ. Bulbs of a run
    that continues the zigzag (the Tip) are in `tip` (their rows above the Peak's)."""
    cells: list[tuple[int, float, int]] = field(default_factory=list)
    rows: list[list[tuple[int, float]]] = field(default_factory=list)   # per row (index, X), left to right
    peak_rows: int = 0
    tip: list[int] = field(default_factory=list)

    @classmethod
    def ideal(cls, counts=(27, 23, 20, 16, 12, 10), tip=()) -> "Lattice":
        """Rows of these many bulbs, each centred over the bottom row on the honeycomb."""
        rows, cells, k = [], [], 0
        allc = list(counts) + list(tip)
        par0 = ((allc[0] - 1) / 2) % 1.0
        for r, n in enumerate(allc):
            xs = _snap([j - (n - 1) / 2 for j in range(n)], (par0 + 0.5 * r) % 1.0)
            row = []
            for x in xs:
                row.append((k, x))
                cells.append((k, x, r))
                k += 1
            rows.append(row)
        lat = cls(cells, rows, len(counts))
        lat.tip = [i for r in rows[len(counts):] for i, _ in r]
        return lat

    def width_at(self, r: int) -> tuple[float, float]:
        row = self.rows[r] if 0 <= r < len(self.rows) else []
        return (row[0][1], row[-1][1]) if row else (0.0, 0.0)


def _snap(xs: list[float], par: float, lean: float = 0.0) -> list[float]:
    """Shift a row (its bulbs' X, left to right) by under half a bulb so its bulbs sit on
    the honeycomb (X = par mod 1). An exact tie (half a bulb either way) goes the way the
    row leans (lean), else to the right."""
    if not xs:
        return xs
    off = (xs[0] - par) % 1.0               # how far the row is to the right of the lattice
    if abs(off - 0.5) < 1e-6:
        d = -0.5 if lean < 0 else 0.5
    else:
        d = -off if off < 0.5 else 1.0 - off
    return [round((x + d) * 2) / 2 for x in xs]


def _zig_rows(layout, idx) -> tuple[list[list[int]], list[list[int]]]:
    """(peak rows, tip rows) of the target's zigzag, bottom first, each left to right."""
    from . import geometry as geo
    from .effects_ext.showpieces import canvas
    px = layout.pixels
    cv = canvas(layout, idx)
    zig = cv["zig"]
    if not zig:
        return [], []
    tipset = set(cv.get("tip") or [])
    out = []
    for part in ([i for i in zig if i not in tipset], sorted(tipset)):
        if not part:
            out.append([])
            continue
        part = sorted(part)
        rr = geo._rows_by_reversal(px, part)
        if rr is None:                                 # group by height
            rr = []
            ys = sorted({round(px[i].y, 3) for i in part})
            gaps = sorted(b - a for a, b in zip(ys, ys[1:]) if b - a > 0.05)
            tol = (gaps[len(gaps) // 2] * 0.5) if gaps else 0.3
            for i in sorted(part, key=lambda i: px[i].y):
                if rr and abs(px[i].y - px[rr[-1][-1]].y) <= tol:
                    rr[-1].append(i)
                else:
                    rr.append([i])
        rr = [sorted(r, key=lambda i: px[i].x) for r in rr if r]
        rr.sort(key=lambda r: sum(px[i].y for i in r) / len(r))
        out.append(rr)
    return out[0], out[1]


def lattice(layout, idx) -> Lattice:
    """The target's zigzag (and the Tip on it, if any) as a honeycomb. Each row keeps
    its measured centre to the nearest half bulb, then steps by under half a bulb onto
    the lattice (every row half a bulb across from the row below). A bulb marked dead
    (Bulb check) keeps its place in its row, so letters never shift round it."""
    from .effects_ext.showpieces import dead_in
    px = layout.pixels
    prows, trows = _zig_rows(layout, idx)
    if not prows:
        return Lattice()
    rows = prows + trows
    dx = sorted(px[b].x - px[a].x for r in rows for a, b in zip(r, r[1:]) if px[b].x - px[a].x > 1e-6)
    pitch = dx[len(dx) // 2] if dx else 1.0
    # every row's bulbs as x positions, dead ones included (None in place of an index)
    ys = [sum(px[i].y for i in r) / len(r) for r in rows]
    full = [[(px[i].x, i) for i in r] for r in rows]
    for x, y in dead_in(layout, [i for r in rows for i in r]):
        k = min(range(len(rows)), key=lambda k: abs(ys[k] - y))
        full[k].append((x, None))
    full = [sorted(r, key=lambda v: v[0]) for r in full]
    base = full[0]
    c0 = (base[0][0] + base[-1][0]) / 2
    par0 = ((len(base) - 1) / 2) % 1.0
    lat = Lattice(peak_rows=len(prows))
    for r, row in enumerate(full):
        n = len(row)
        cx = (row[0][0] + row[-1][0]) / 2
        lean = (cx - c0) / pitch
        centre = round(lean * 2) / 2                 # measured centre, to half a bulb
        xs = _snap([j - (n - 1) / 2 + centre for j in range(n)], (par0 + 0.5 * r) % 1.0, lean)
        live = [(i, x) for (_, i), x in zip(row, xs) if i is not None]
        lat.rows.append(live)
        lat.cells.extend((i, x, r) for i, x in live)
    lat.tip = [i for r in trows for i in r]
    return lat


# --------------------------------------------------------------------------- rasterising
def placed(ch: str, size: str, x0: float, y0: float = 0.0, scale: float = 1.0) -> list:
    """The glyph's strokes as segments (ax, ay, bx, by) on the lattice: X in bulbs, Y in
    rows (0 = the bottom row), its left edge at X = x0 and its baseline on row y0."""
    S = SIZES[size]
    sx = S["sx"] * scale
    ry = S["rows"] / CAP * scale
    c = x0 + width(ch, size) * scale / 2

    def X(x):
        # a quarter bulb out from the glyph's middle: a vertical stroke then lands a
        # quarter bulb off every bulb of the honeycomb, so it takes whole bulbs on every
        # row (never two half-lit ones), whichever way a row is offset
        v = x0 + x * sx
        return v + (-0.25 if v < c - 0.3 else 0.25)

    def Y(y):
        # whole rows: a bar that falls between two rows goes up to the row above
        v = y * ry
        if abs(v % 1 - 0.5) < 1e-6:
            v += 0.5
        return y0 + v

    segs = []
    for line in glyph_strokes(ch, size):
        wt = 1.0
        if isinstance(line, tuple):                   # (weight, points): a thinner/bolder stroke
            wt, line = line
        pts = [(X(x), Y(y)) for x, y in line]
        if len(pts) == 1:
            pts = pts * 2
        segs.extend((a[0], a[1], b[0], b[1], wt) for a, b in zip(pts, pts[1:]))
    return segs


def spans(segs, r: int) -> list[tuple[float, float, float]]:
    """Where the strokes cross row r: the X range of each segment inside the row's band
    (half a row above and below it), and the stroke's weight."""
    lo, hi = r - 0.5, r + 0.5
    out = []
    for ax, ay, bx, by, wt in segs:
        if max(ay, by) < lo - 1e-9 or min(ay, by) > hi + 1e-9:
            continue
        if abs(by - ay) < 1e-9:
            if lo - 1e-9 <= ay < hi - 1e-9:
                out.append((min(ax, bx), max(ax, bx), wt))
            continue
        t0, t1 = sorted(((lo - ay) / (by - ay), (hi - ay) / (by - ay)))
        t0, t1 = max(0.0, t0), min(1.0, t1)
        if t1 < t0 or (t1 - t0) * abs(by - ay) < 1e-6 and not (lo <= ay + t0 * (by - ay) < hi):
            continue
        xa, xb = ax + t0 * (bx - ax), ax + t1 * (bx - ax)
        out.append((min(xa, xb), max(xa, xb), wt))
    return out


def coverage(sp, X: float, hw: float, soft: float = SOFT) -> float:
    """How much of a bulb at X a stroke covers. Each row a stroke is about 2 * hw bulbs
    wide whatever its slope: a vertical stroke reaches hw either side of where it crosses
    the row, a slanted one (crossing over a stretch of the row) less, but always at least
    a quarter bulb, so a stroke is never broken."""
    best = 0.0
    for a, b, wt in sp:
        e = max(0.25, hw * wt - (b - a) / 2)
        d = max(a - X, X - b, 0.0)
        best = max(best, min(1.0, max(0.0, 0.5 + (e - d) / (2 * soft))))
    return best


def render_glyph(lat: Lattice, segs, hw: float, r0: int, r1: int) -> dict[int, float]:
    out = {}
    for r in range(max(0, r0), min(len(lat.rows) - 1, r1) + 1):
        sp = spans(segs, r)
        if not sp:
            continue
        for i, X in lat.rows[r]:
            # a bulb is on or off: half-lit bulbs read as stray dots from the street
            if coverage(sp, X, hw) > 0.5:
                out[i] = 1.0
    return out


def render_big(lat: Lattice, ch: str, rows: int | None = None, dx: float = 0.0, hw: float | None = None,
               use_tip: bool = False) -> dict[int, float]:
    """One glyph, big, centred on the Peak (shifted dx bulbs): {bulb: coverage}."""
    S = SIZES["big"]
    nr = rows or lat.peak_rows or 6
    w = width(ch, "big")
    x0 = math.floor(-w / 2 + dx)                  # whole bulbs, so stems sit as designed
    segs = placed(ch, "big", x0, 0.0)
    return render_glyph(lat, segs, S["hw"] if hw is None else hw, 0, (len(lat.rows) if use_tip else nr) - 1)


def line_layout(text: str, size: str = "marquee") -> list[tuple[str, float, float]]:
    """(glyph, left X, width) for each glyph of a line, the line starting at X = 0."""
    out, x = [], 0.0
    for ch in normalise(text):
        w = width(ch, size)
        out.append((ch, x, w))
        x += w + SIZES[size]["gap"]
    return out


def render_line(lat: Lattice, text: str, x: float, rows: int | None = None, size: str = "marquee",
                hw: float | None = None, row0: int = 0) -> dict[int, tuple[float, int]]:
    """A line of text whose left edge is at X = x (a whole bulb), baseline on row row0:
    {bulb: (coverage, glyph number)}."""
    S = SIZES[size]
    hw = S["hw"] if hw is None else hw
    top = row0 + (rows or S["rows"])
    out: dict[int, tuple[float, int]] = {}
    for k, (ch, gx, w) in enumerate(line_layout(text, size)):
        if ch == " ":
            continue
        for i, c in render_glyph(lat, placed(ch, size, x + gx, row0), hw, row0, top).items():
            if c > out.get(i, (0.0, 0))[0]:
                out[i] = (c, k)
    return out


def off_peak(lat: Lattice, ch: str, x0: float, size: str = "marquee", row0: int = 0) -> int:
    """How many of a glyph's bulbs (its left edge at X = x0) would fall off the sides of
    the Peak: 0 when the whole letter is on it."""
    S = SIZES[size]
    segs = placed(ch, size, x0, row0)
    n = 0
    for r in range(row0, min(len(lat.rows), lat.peak_rows or len(lat.rows))):
        if r > row0 + S["rows"]:
            break
        sp = spans(segs, r)
        if not sp or not lat.rows[r]:
            continue
        lo, hi = lat.rows[r][0][1], lat.rows[r][-1][1]
        par = lo % 1.0
        a = min(v[0] for v in sp) - 2
        b = max(v[1] for v in sp) + 2
        X = math.floor(a) + par
        while X <= b:
            if (X < lo - 0.01 or X > hi + 0.01) and coverage(sp, X, S["hw"]) > 0.5:
                n += 1
            X += 1.0
    return n


def line_width(text: str, size: str = "marquee") -> float:
    gl = line_layout(text, size)
    return (gl[-1][1] + gl[-1][2]) if gl else 0.0


# --------------------------------------------------------------------------- previews
def ascii(lat: Lattice, cov: dict, lit: float = 0.5) -> str:
    """The lattice as text, top row first, two characters per bulb: '#' lit, '+' partly,
    '.' dark (rows offset by one character per half bulb)."""
    if not lat.cells:
        return ""
    xmin = min(X for _, X, _ in lat.cells)
    lines = []
    for row in reversed(lat.rows):
        s = {}
        for i, X in row:
            c = cov.get(i, 0.0)
            c = c[0] if isinstance(c, tuple) else c
            s[int(round((X - xmin) * 2))] = "#" if c >= lit else "+" if c > 0.15 else "."
        w = max(s) + 1 if s else 0
        lines.append("".join(s.get(k, " ") for k in range(w)).rstrip())
    return "\n".join(lines)
