"""The Peak as a honeycomb screen: one lattice cell per bulb, for pictures designed for it.

The Peak is a zigzag: rows of bulbs wired left to right, then right to left, each row a
little shorter (about 27, 23, 20, 16, 12, 10 bulbs, and with the Tip 7, 4, 1 on up to the
apex). The rows are OFFSET by half a bulb: every bulb sits midway between two bulbs of
the row below, so the screen is a triangle on a honeycomb (hex) lattice, not a square
grid. Square-grid bitmaps squeezed onto it come out as scattered dots; shapes drawn for
the lattice read cleanly from the street (verticals zig-zag, 60-degree diagonals are
straight). This module gives effects that lattice:

    hc = hexcanvas.get(layout, idx)       # None when the target has no zigzag

Coordinates ("design units"): x in COLUMNS (one bulb spacing; 0 is the screen's centre
line, a cell's x is a whole or half number), y in ROWS (0 is the bottom row, the Tip's
apex is the top row). Each cell also has a doubled column d = 2x (an integer, d + r has
the same parity on every cell) and axial coordinates (q, r) with q = (d - r - p) / 2,
where p is the parity of the bottom row.

Distances (circles, line widths, arcs) are measured with a row ``row_h`` columns tall
(the ideal honeycomb, sqrt(3)/2, by default), so a circle of radius 3 is round on the
lattice and a line at 60 degrees runs straight along it.

    hc.rows / hc.widths / hc.row(r)        the rows bottom first and their bulb counts
    hc.cell(r, x) / hc.at(r, x)            the cell (or its bulb index; None if dead/none)
    hc.at_axial(q, r), hc.nearest(x, y)
    hc.centre, hc.apex, hc.apex_cells      centre column (0), top-centre point, its cells
    hc.centroid                            (0, y) the triangle's centre of mass (widest rows)
    hc.triangle(pad)                       the fitted outline as a polygon in design units
    hc.fill_triangle(inset) / hc.outline_triangle(width)
    hc.raster(shape, soft)                 {bulb index: coverage 0..1}
        shapes: Circle, Polygon, Line, Arc, Union, Difference (or any callable (x, y) -> signed distance)
    hc.column(x, width) / hc.walk(r, x, direction, steps)   lattice-native strokes
    Sprite(text, palette) + hc.sprite(sprite, row, col)     hand-drawn pixel art
    hc.paint(frame, coverage, colour, mode)  /  hc.ascii(...)   draw / debug

A dead bulb (marked broken in the Bulb check, layout ``dead``) keeps its cell: the
lattice is built from every visible bulb's physical position, so nothing shifts when one
dies; the cell's ``i`` is just None and drawing skips it. Hidden bulbs are not part of
the screen. Cells are keyed by (r, d), a seed that never moves.

Nothing here draws by itself and no existing effect uses it unless it imports it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .. import geometry as geo

ROW_H = math.sqrt(3) / 2         # an ideal honeycomb: rows sqrt(3)/2 of a bulb spacing apart

# the six lattice directions as (doubled-column step, row step)
DIRECTIONS = {
    "e": (2, 0), "w": (-2, 0),
    "ne": (1, 1), "nw": (-1, 1),
    "se": (1, -1), "sw": (-1, -1),
}


# ====================================================================== cells and rows
@dataclass
class Cell:
    r: int                  # row, 0 = bottom
    d: int                  # doubled column (2 * x)
    i: int | None           # the bulb's index in layout.pixels; None if it is marked dead
    run: str                # the physical run's name (Peak or Tip)
    mac: str
    local: int              # physical bulb index on its run
    tip: bool               # part of a run that continues the zigzag (the Tip)
    pos: tuple              # its (x, y) in the layout
    p0: int = 0             # parity of the bottom row (for the axial q)

    @property
    def x(self) -> float:
        return self.d / 2

    @property
    def y(self) -> float:
        return float(self.r)

    @property
    def q(self) -> int:
        return (self.d - self.r - self.p0) // 2

    @property
    def key(self) -> tuple[int, int]:
        """(r, d): where the cell is on the lattice; stable whatever bulbs die."""
        return (self.r, self.d)

    @property
    def dead(self) -> bool:
        return self.i is None


@dataclass
class Row:
    r: int
    cells: list = field(default_factory=list)       # left to right
    tip: bool = False

    @property
    def n(self) -> int:
        return len(self.cells)

    @property
    def x0(self) -> float:
        return self.cells[0].x

    @property
    def x1(self) -> float:
        return self.cells[-1].x

    @property
    def half_width(self) -> float:
        return (self.x1 - self.x0) / 2


# ====================================================================== shapes
# every shape is a callable (x, y) -> signed distance in columns (negative inside), in
# design units; the canvas passes row_h so distances are true on the lattice

def _seg(px, py, ax, ay, bx, by) -> float:
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 <= 0 else min(1.0, max(0.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


class Shape:
    def sd(self, x: float, y: float, rh: float) -> float:       # pragma: no cover - abstract
        raise NotImplementedError

    def __or__(self, other):
        return Union(self, other)

    def __sub__(self, other):
        return Difference(self, other)


class Circle(Shape):
    """A disc centred at (cx, cy) with radius r (columns). ring=w makes it a ring w wide."""

    def __init__(self, cx: float, cy: float, r: float, ring: float | None = None):
        self.cx, self.cy, self.r, self.ring = cx, cy, r, ring

    def sd(self, x, y, rh):
        d = math.hypot(x - self.cx, (y - self.cy) * rh) - self.r
        return abs(d) - self.ring / 2 if self.ring else d


class Polygon(Shape):
    """A filled polygon through points [(x, y), ...] (design units)."""

    def __init__(self, pts):
        self.pts = [(float(a), float(b)) for a, b in pts]

    def sd(self, x, y, rh):
        pts = [(a, b * rh) for a, b in self.pts]
        Y = y * rh
        d = min(_seg(x, Y, *pts[k - 1], *pts[k]) for k in range(len(pts)))
        inside = False
        for k in range(len(pts)):
            (x0, y0), (x1, y1) = pts[k - 1], pts[k]
            if (y0 > Y) != (y1 > Y) and x < x0 + (Y - y0) * (x1 - x0) / (y1 - y0):
                inside = not inside
        return -d if inside else d


class Line(Shape):
    """A stroke through points [(x, y), ...] (a polyline), `width` columns wide."""

    def __init__(self, pts, width: float = 1.0):
        self.pts = [(float(a), float(b)) for a, b in pts]
        self.width = width

    def sd(self, x, y, rh):
        pts = [(a, b * rh) for a, b in self.pts]
        Y = y * rh
        if len(pts) == 1:
            d = math.hypot(x - pts[0][0], Y - pts[0][1])
        else:
            d = min(_seg(x, Y, *pts[k], *pts[k + 1]) for k in range(len(pts) - 1))
        return d - self.width / 2


class Arc(Shape):
    """Part of a ring: centre (cx, cy), radius r, from angle a0 to a1 (degrees,
    0 = right, 90 = up, anticlockwise), `width` columns wide, round ends."""

    def __init__(self, cx, cy, r, a0, a1, width: float = 1.0):
        self.cx, self.cy, self.r, self.a0, self.a1, self.width = cx, cy, r, a0, a1, width

    def sd(self, x, y, rh):
        dx, dy = x - self.cx, (y - self.cy) * rh
        span = (self.a1 - self.a0) % 360 or 360
        a = (math.degrees(math.atan2(dy, dx)) - self.a0) % 360
        if a <= span:
            return abs(math.hypot(dx, dy) - self.r) - self.width / 2
        ends = []
        for ang in (self.a0, self.a0 + span):
            t = math.radians(ang)
            ends.append(math.hypot(dx - self.r * math.cos(t), dy - self.r * math.sin(t)))
        return min(ends) - self.width / 2


class Union(Shape):
    def __init__(self, *shapes):
        self.shapes = shapes

    def sd(self, x, y, rh):
        return min(_sd(s, x, y, rh) for s in self.shapes)


class Difference(Shape):
    """a minus b."""

    def __init__(self, a, b):
        self.a, self.b = a, b

    def sd(self, x, y, rh):
        return max(_sd(self.a, x, y, rh), -_sd(self.b, x, y, rh))


def _sd(shape, x, y, rh) -> float:
    if isinstance(shape, Shape):
        return shape.sd(x, y, rh)
    return shape(x, y)                   # a plain function of design units


def coverage(sd: float, soft: float) -> float:
    """How much of a bulb a shape covers at signed distance sd: soft = 0 is crisp (in or
    out, best for bold pictures), soft = 0.5 blends half a column either side of the edge."""
    if soft <= 0:
        return 1.0 if sd <= 1e-9 else 0.0
    return min(1.0, max(0.0, 0.5 - sd / (2 * soft)))


# ====================================================================== sprites
class Sprite:
    """Hand-drawn pixel art on the hex lattice, as a multi-line string drawn the way the
    bulbs hang: one text line per row (top line = top row) and one character per HALF
    column, so the cells of neighbouring rows sit between each other:

        Sprite('''
            . R .
           R R R R
            R . R
        ''', {"R": (1, 0, 0)})

    A space is not part of the sprite; '.' is part of it but dark (painted black by
    hc.sprite(..., dark=True), else left alone); any other character takes its colour from
    the palette (a missing character raises KeyError). All cells must sit on the same
    lattice: on neighbouring lines they alternate between odd and even character places
    (a cell on the wrong place raises ValueError). Common leading indentation is ignored.
    """

    def __init__(self, text: str, palette: dict | None = None):
        lines = text.split("\n")
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        if not lines:
            raise ValueError("an empty sprite")
        indent = min(len(ln) - len(ln.lstrip(" ")) for ln in lines if ln.strip())
        lines = [ln[indent:].rstrip() for ln in lines]
        self.palette = dict(palette or {})
        self.cells: list[tuple[int, int, str]] = []        # (doubled column, row from bottom, char)
        parity = None
        for j, ln in enumerate(reversed(lines)):
            for k, ch in enumerate(ln):
                if ch == " ":
                    continue
                if ch == "\t":
                    raise ValueError("sprites use spaces, not tabs")
                p = (k + j) % 2
                if parity is None:
                    parity = p
                elif p != parity:
                    raise ValueError(f"sprite cell {ch!r} at line {len(lines) - j}, place {k + 1} is "
                                     "off the lattice (neighbouring lines alternate odd and even places)")
                if ch != "." and ch not in self.palette:
                    raise KeyError(f"sprite character {ch!r} has no colour in the palette")
                self.cells.append((k, j, ch))
        self.parity = parity or 0
        self.width = max(len(ln) for ln in lines)            # in half columns
        self.height = len(lines)

    def colour(self, ch: str):
        return None if ch == "." else self.palette[ch]


# ====================================================================== the canvas
def _split_rows(pos: list, order: list[int], pitch_hint: float | None = None) -> list[list[int]]:
    """Physical bulb order -> rows: a new row wherever the horizontal direction flips (the
    zigzag's turns) or the height jumps."""
    if not order:
        return []
    steps = [abs(pos[b][0] - pos[a][0]) for a, b in zip(order, order[1:]) if abs(pos[b][0] - pos[a][0]) > 1e-6]
    pitch = pitch_hint or (sorted(steps)[len(steps) // 2] if steps else 1.0)
    rows, cur, direction = [], [order[0]], 0
    for a, b in zip(order, order[1:]):
        dx = pos[b][0] - pos[a][0]
        dy = pos[b][1] - pos[a][1]
        d = 1 if dx > 0.25 * pitch else -1 if dx < -0.25 * pitch else 0
        if (d and direction and d != direction and len(cur) >= 2) or abs(dy) > 0.4 * pitch:
            rows.append(cur)
            cur = []
            direction = 0
        if d:
            direction = d
        cur.append(b)
    rows.append(cur)
    return rows


def _zigzag_runs(layout, idx):
    """(host run, [runs that continue it]) for the target's zigzag, or (None, [])."""
    px = layout.pixels
    want = {px[i].run for i in idx} if idx is not None else None
    best, size = None, 0
    for r in layout.runs:
        if r.extends or not r.geometry:
            continue
        if want is not None and r.name not in want:
            continue
        pos = r.positions()
        order = [k for k in range(r.count) if k not in r.hidden]
        rows = _split_rows(pos, order)
        if len(rows) < 3 or any(len(x) < 2 for x in rows):
            continue
        ys = [sum(pos[k][1] for k in x) / len(x) for x in rows]
        if len({round(y, 3) for y in ys}) < len(ys):     # rows must be at different heights
            continue
        if len(order) > size:
            best, size = r, len(order)
    if best is None:
        return None, []
    exts = layout.extensions(best) if hasattr(layout, "extensions") else []
    return best, exts


class HexCanvas:
    def __init__(self, layout, idx=None, row_h: float = ROW_H):
        self.layout = layout
        self.row_h = float(row_h)
        host, exts = _zigzag_runs(layout, idx)
        if host is None:
            raise ValueError("no zigzag in this target")
        self.host = host
        self.exts = exts
        live = {}                                 # (mac, local) -> pixel index
        for p in layout.pixels:
            live[(p.mac, p.local)] = p.i
        raw = []                                  # per row: [(run, local, (x, y))], tip flag
        steps = []
        for run in [host] + list(exts):
            pos = run.positions()
            order = [k for k in range(run.count) if k not in run.hidden]
            rows = _split_rows(pos, order)
            for rw in rows:
                steps += [abs(pos[b][0] - pos[a][0]) for a, b in zip(rw, rw[1:])]
            for rw in rows:
                raw.append(([(run, k, pos[k]) for k in rw], run is not host))
        steps = [s for s in steps if s > 1e-6]
        self.pitch = sorted(steps)[len(steps) // 2] if steps else 1.0
        raw.sort(key=lambda e: sum(b[2][1] for b in e[0]) / len(e[0]))
        # the centre line: the middle of the bottom (widest) row
        bottom = raw[0][0]
        bx = [b[2][0] for b in bottom]
        self._cx = (min(bx) + max(bx)) / 2
        nb = self._colsteps(sorted(bottom, key=lambda b: b[2][0]))
        self.p0 = (nb[-1] // 2) % 2               # the bottom row centred on 0: its parity
        self.rows: list[Row] = []
        self.cells: list[Cell] = []
        self._by_key: dict[tuple[int, int], Cell] = {}
        self._by_i: dict[int, Cell] = {}
        ys = []
        for r, (bulbs, tip) in enumerate(raw):
            wired_first = bulbs[0][2][0]
            bulbs = sorted(bulbs, key=lambda b: b[2][0])
            st = self._colsteps(bulbs)            # doubled offsets from the row's first bulb
            span = st[-1]
            want = 2 * ((bulbs[0][2][0] + bulbs[-1][2][0]) / 2 - self._cx) / self.pitch   # centre, doubled
            par = (r + self.p0) % 2
            # the first bulb's doubled column: on this row's parity, its centre nearest the
            # geometry's; a tie goes toward the end where the row's wiring starts
            base = want - span / 2
            lo = math.floor(base)
            cands = [c for c in (lo - 1, lo, lo + 1, lo + 2) if (c + par) % 2 == 0]
            starts_right = abs(wired_first - bulbs[-1][2][0]) < abs(wired_first - bulbs[0][2][0])
            d0 = min(cands, key=lambda c: (round(abs(c - base), 6), -c if starts_right else c))
            row = Row(r=r, tip=tip)
            for (run, k, pxy), s in zip(bulbs, st):
                c = Cell(r=r, d=d0 + s, i=live.get((run.mac, k)), run=run.name, mac=run.mac, local=k,
                         tip=tip, pos=pxy, p0=self.p0)
                row.cells.append(c)
                self.cells.append(c)
                self._by_key[c.key] = c
                if c.i is not None:
                    self._by_i[c.i] = c
            ys.append(sum(b[2][1] for b in bulbs) / len(bulbs))
            self.rows.append(row)
        gaps = sorted(b - a for a, b in zip(ys, ys[1:]))
        self.aspect = (gaps[len(gaps) // 2] / self.pitch) if gaps else 1.0     # measured row gap / pitch
        self.allowed = set(self._by_i) if idx is None else set(self._by_i) & set(idx)
        self._fit()

    def _colsteps(self, bulbs) -> list[int]:
        """Doubled column offsets of a row's bulbs (sorted left to right) from its first:
        2 per bulb spacing, so a gap (a bulb missing from the geometry) leaves a hole."""
        out, s = [0], 0
        for a, b in zip(bulbs, bulbs[1:]):
            s += 2 * max(1, round((b[2][0] - a[2][0]) / self.pitch))
            out.append(s)
        return out

    def _fit(self):
        """The triangle's sides: half width = a - b * y, fitted to the rows' ends."""
        ys = [rw.r for rw in self.rows]
        hw = [max(abs(rw.x0), abs(rw.x1)) for rw in self.rows]
        n = len(ys)
        my, mh = sum(ys) / n, sum(hw) / n
        var = sum((y - my) ** 2 for y in ys)
        b = -sum((y - my) * (h - mh) for y, h in zip(ys, hw)) / var if var else 0.0
        self._a, self._b = mh + b * my, b

    # ------------------------------------------------------------ lookups
    @property
    def widths(self) -> list[int]:
        """Bulbs per row, bottom first (a dead bulb still counts)."""
        return [rw.n for rw in self.rows]

    @property
    def top(self) -> int:
        return len(self.rows) - 1

    @property
    def has_tip(self) -> bool:
        return any(rw.tip for rw in self.rows)

    @property
    def peak_rows(self) -> int:
        return sum(1 for rw in self.rows if not rw.tip)

    centre = 0.0                                   # the centre line is x = 0

    def row(self, r: int) -> list[Cell]:
        return list(self.rows[r].cells) if 0 <= r < len(self.rows) else []

    def cell(self, r: int, x: float) -> Cell | None:
        """The cell in row r at column x (a whole or half number); None if there is none."""
        return self._by_key.get((int(r), int(round(2 * x))))

    def at(self, r: int, x: float) -> int | None:
        """The bulb index at row r, column x; None if there is no cell or its bulb is dead."""
        c = self.cell(r, x)
        return c.i if c else None

    def at_axial(self, q: int, r: int) -> int | None:
        c = self._by_key.get((int(r), 2 * int(q) + int(r) + self.p0))
        return c.i if c else None

    def of(self, i: int) -> Cell | None:
        """The cell of bulb index i (None if i is not on the screen)."""
        return self._by_i.get(i)

    def nearest(self, x: float, y: float) -> Cell | None:
        rh = self.row_h
        return min(self.cells, key=lambda c: (math.hypot(c.x - x, (c.y - y) * rh), c.r, c.d), default=None)

    def half_width(self, y: float) -> float:
        """The fitted triangle's half width (columns) at height y."""
        return max(0.0, self._a - self._b * y)

    @property
    def apex(self) -> tuple[float, float]:
        """(0, top row): the top of the screen on its centre line."""
        return (0.0, float(self.top))

    @property
    def apex_cells(self) -> list[Cell]:
        """The cell(s) of the top row nearest the centre line (one on an odd row, two on even)."""
        top = self.rows[-1].cells
        m = min(abs(c.d) for c in top)
        return [c for c in top if abs(c.d) == m]

    @property
    def tip_y(self) -> float:
        """Where the fitted sides meet (the gable's point, above the top row without the Tip)."""
        return self._a / self._b if self._b > 1e-9 else float(self.top)

    @property
    def centroid(self) -> tuple[float, float]:
        """(0, y): the screen's centre of mass, low down where the rows are widest: where a
        picture should be centred."""
        tot = sum(self.widths)
        return (0.0, sum(rw.r * rw.n for rw in self.rows) / tot)

    def triangle(self, pad: float = 0.5) -> list[tuple[float, float]]:
        """The screen's outline as a polygon in design units: the bottom row's ends and the
        fitted point, `pad` columns outside the outer bulbs."""
        bot = -pad / self.row_h                     # pad columns below the bottom row
        hw = self._a + pad - self._b * bot
        top = self.tip_y + pad / self.row_h
        return [(-hw, bot), (hw, bot), (0.0, top)]

    def live(self) -> list[int]:
        """Every working bulb on the screen (in the target), bottom row first, left to right."""
        return [c.i for c in self.cells if c.i is not None and c.i in self.allowed]

    # ------------------------------------------------------------ rasterising
    def raster(self, shape, soft: float = 0.0, cells=None) -> dict[int, float]:
        """{bulb index: coverage 0..1} of a shape (a Shape or a function (x, y) -> signed
        distance in design units) over the screen's working bulbs."""
        out = {}
        rh = self.row_h
        for c in cells if cells is not None else self.cells:
            if c.i is None or c.i not in self.allowed:
                continue
            w = coverage(_sd(shape, c.x, c.y, rh), soft)
            if w > 0:
                out[c.i] = w
        return out

    def cells_of(self, shape, soft: float = 0.0) -> list[Cell]:
        """The cells (dead ones too) a shape covers at least half: for designing."""
        rh = self.row_h
        return [c for c in self.cells if coverage(_sd(shape, c.x, c.y, rh), soft) >= 0.5]

    def fill_triangle(self, inset: int = 0) -> dict[int, float]:
        """The whole screen, less `inset` bulbs in from each row's ends and the bottom/top."""
        out = {}
        for rw in self.rows[inset:len(self.rows) - inset if inset else None]:
            cs = rw.cells[inset:rw.n - inset if inset else None]
            out.update({c.i: 1.0 for c in cs if c.i is not None and c.i in self.allowed})
        return out

    def outline_triangle(self, width: int = 1) -> dict[int, float]:
        """The screen's border, `width` bulbs thick: the bottom row(s), each row's end bulbs
        and the top row(s) (the apex with the Tip)."""
        out = {}
        k = max(1, int(width))
        for rw in self.rows:
            edge = rw.r < k or rw.r > self.top - k
            cs = rw.cells if edge else rw.cells[:k] + rw.cells[-k:]
            out.update({c.i: 1.0 for c in cs if c.i is not None and c.i in self.allowed})
        return out

    def column(self, x: float, width: int = 2, rows=None) -> dict[int, float]:
        """A vertical stroke exactly `width` bulbs wide on every row: per row the bulbs
        nearest column x (ties to the right). On the lattice it zig-zags by half a bulb,
        the straightest a vertical can be."""
        out = {}
        for rw in self.rows if rows is None else [self.rows[r] for r in rows if 0 <= r < len(self.rows)]:
            cs = sorted(rw.cells, key=lambda c: (abs(c.x - x), -c.d))[:width]
            out.update({c.i: 1.0 for c in cs if c.i is not None and c.i in self.allowed})
        return out

    def walk(self, r: int, x: float, direction: str, steps: int) -> list[Cell]:
        """Cells from (r, x) along a lattice direction (e, w, ne, nw, se, sw): straight
        lines on the honeycomb. Stops at the screen's edge."""
        dd, dr = DIRECTIONS[direction]
        out, R, D = [], int(r), int(round(2 * x))
        for _ in range(int(steps) + 1):
            c = self._by_key.get((R, D))
            if c is None:
                break
            out.append(c)
            R, D = R + dr, D + dd
        return out

    def neighbours(self, cell: Cell) -> list[Cell]:
        return [n for dd, dr in DIRECTIONS.values() if (n := self._by_key.get((cell.r + dr, cell.d + dd)))]

    # ------------------------------------------------------------ sprites
    def sprite_cells(self, sp: Sprite, row: int = 0, col: float = 0.0) -> list[tuple[Cell, str]]:
        """Where a sprite's cells land: its bottom line on `row`, its middle on column
        `col` (moved by half a column, to the right, when that would put it off the
        lattice). Cells off the screen are dropped."""
        target = 2 * col - (sp.width - 1) / 2            # doubled offset for the sprite's middle
        par = (int(row) + self.p0 + sp.parity) % 2        # need (k + off + row + j + p0) even
        lo = math.floor(target)
        off = min((c for c in (lo - 1, lo, lo + 1, lo + 2) if (c + par) % 2 == 0),
                  key=lambda c: (abs(c - target), -c))
        out = []
        for k, j, ch in sp.cells:
            c = self._by_key.get((int(row) + j, k + off))
            if c is not None:
                out.append((c, ch))
        return out

    def sprite(self, sp: Sprite, row: int = 0, col: float = 0.0, dark: bool = False) -> dict[int, tuple]:
        """{bulb index: colour} for a sprite placed on the screen ('.' cells black when dark)."""
        out = {}
        for c, ch in self.sprite_cells(sp, row, col):
            if c.i is None or c.i not in self.allowed:
                continue
            col_ = sp.colour(ch)
            if col_ is None:
                if dark:
                    out[c.i] = (0.0, 0.0, 0.0)
            else:
                out[c.i] = tuple(float(v) for v in col_)
        return out

    # ------------------------------------------------------------ drawing
    def paint(self, frame: list, cov, colour=None, mode: str = "over") -> list:
        """Draw onto a frame (a list of (r, g, b) per bulb) in place, and return it.
        cov: {i: coverage} with one colour, or {i: colour} (from hc.sprite) with colour None.
        mode: over (blend by coverage), max (keep the brighter), add."""
        for i, w in cov.items():
            if not 0 <= i < len(frame):
                continue
            if colour is None:
                c, w = w, 1.0
            else:
                c = colour
            a = frame[i]
            if mode == "add":
                frame[i] = (a[0] + c[0] * w, a[1] + c[1] * w, a[2] + c[2] * w)
            elif mode == "max":
                frame[i] = (max(a[0], c[0] * w), max(a[1], c[1] * w), max(a[2], c[2] * w))
            else:
                frame[i] = (a[0] + (c[0] - a[0]) * w, a[1] + (c[1] - a[1]) * w, a[2] + (c[2] - a[2]) * w)
        return frame

    def ascii(self, marks=None, on: str = "#", off: str = ".", dead: str = "x") -> str:
        """The screen as text, drawn the way the bulbs hang (the Sprite format): marks is a
        {bulb index: coverage or character} dict (coverage >= 0.5 shows `on`) or a function
        cell -> character."""
        dmin = min(c.d for c in self.cells)
        dmax = max(c.d for c in self.cells)
        lines = []
        for rw in reversed(self.rows):
            buf = [" "] * (dmax - dmin + 1)
            for c in rw.cells:
                if callable(marks):
                    ch = marks(c)
                elif c.i is None:
                    ch = dead
                elif marks is None:
                    ch = on
                else:
                    v = marks.get(c.i)
                    ch = v if isinstance(v, str) else (on if v is not None and v >= 0.5 else off)
                buf[c.d - dmin] = ch
            lines.append("".join(buf).rstrip())
        return "\n".join(lines)


def get(layout, idx=None, row_h: float = ROW_H) -> HexCanvas | None:
    """The honeycomb canvas of the target's zigzag (cached per layout), or None when the
    target has none. idx: the target's bulb indexes (None = the whole zigzag)."""
    key = ("hexcanvas", None if idx is None else tuple(idx), row_h)
    c = geo._cache(layout)
    if key not in c:
        try:
            c[key] = HexCanvas(layout, idx, row_h)
        except ValueError:
            c[key] = None
    return c[key]
