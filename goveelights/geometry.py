"""Geometry helpers for effects that use the shape of the house.

Coordinates are the layout's: x across the unrolled house (bulb-spacing units, as
seen from the street), y up. All lookups are cached per layout object, so effects
can call them every frame.

    rows(layout, target)          horizontal rows (e.g. the zigzag's 6 rows), bottom first
    peaks(layout)                 local high points along each run, highest first
    star(layout, target)          the bulbs crowning a target area (e.g. gable apex over the tree)
    path(layout, indices)         ordered path with arc length and slope (for gravity)
    surface_below(layout, x, y)   the highest bulb under a point (where things land)
    splat(frame, layout, x, y, c) draw a point between bulbs, anti-aliased (smooth motion)
    nearest(layout, x, y)         closest bulb to a point
"""
from __future__ import annotations

import math


def _cache(layout) -> dict:
    c = getattr(layout, "_geo_cache", None)
    if c is None:
        c = {}
        try:
            layout._geo_cache = c
        except AttributeError:
            pass
    return c


def indices(layout, target="all") -> list[int]:
    return layout.indices(target) if hasattr(layout, "indices") else list(range(len(layout.pixels)))


# ------------------------------------------------------------------ rows
def rows(layout, target="all", tol: float = 0.45) -> list[list[int]]:
    """Group the target's bulbs into horizontal rows by height (bottom row first),
    each row sorted left to right. For the Peak this gives the zigzag's rows."""
    key = ("rows", repr(target), tol)
    c = _cache(layout)
    if key in c:
        return c[key]
    px = layout.pixels
    order = indices(layout, target)
    out = _rows_by_reversal(px, order)
    if out is None:                               # not a zigzag: group by height
        out = []
        for i in sorted(order, key=lambda i: px[i].y):
            if out and abs(px[i].y - px[out[-1][0]].y) <= tol:
                out[-1].append(i)
            else:
                out.append([i])
    out = [sorted(r, key=lambda i: px[i].x) for r in out]
    out.sort(key=lambda r: sum(px[i].y for i in r) / len(r))
    c[key] = out
    return out


def _rows_by_reversal(px, order: list[int]) -> list[list[int]] | None:
    """A zigzag is wired row by row, reversing direction at each end. Split the strip
    order wherever the horizontal direction flips; accept it only if that gives several
    rows that are each roughly level and at clearly different heights."""
    if len(order) < 8:
        return None
    rows_, cur, direction = [], [order[0]], 0
    for a, b in zip(order, order[1:]):
        dx = px[b].x - px[a].x
        d = 1 if dx > 0.2 else -1 if dx < -0.2 else 0
        if d and direction and d != direction and len(cur) >= 2:
            rows_.append(cur)
            cur = []
        if d:
            direction = d
        cur.append(b)
    rows_.append(cur)
    if len(rows_) < 3 or any(len(r) < 2 for r in rows_):
        return None
    ys = [sum(px[i].y for i in r) / len(r) for r in rows_]
    spread = max(max(px[i].y for i in r) - min(px[i].y for i in r) for r in rows_)
    gaps = sorted(abs(a - b) for a, b in zip(ys, ys[1:]))
    if spread > max(0.8, gaps[len(gaps) // 2] * 0.9):
        return None
    return rows_


# ----------------------------------------------------------------- peaks
def peaks(layout, min_rise: float = 1.5) -> list[dict]:
    """Local high points along each run: {"i", "x", "y", "run", "rise"}, highest first.
    rise = how far it stands above the lowest bulb within 15 bulbs either side."""
    c = _cache(layout)
    if "peaks" in c:
        return c["peaks"]
    out = []
    for r in layout.runs:
        ps = r.pixels
        for k, p in enumerate(ps):
            lo, hi = max(0, k - 3), min(len(ps), k + 4)
            if any(ps[j].y > p.y + 1e-6 for j in range(lo, hi)):
                continue
            # skip the second bulb of a flat-topped peak
            if k > 0 and abs(ps[k - 1].y - p.y) < 1e-6 and any(o["i"] == ps[k - 1].i for o in out):
                continue
            wlo, whi = max(0, k - 15), min(len(ps), k + 16)
            rise = p.y - min(ps[j].y for j in range(wlo, whi))
            if rise >= min_rise:
                out.append({"i": p.i, "x": p.x, "y": p.y, "run": r.name, "rise": rise})
    out.sort(key=lambda o: -o["y"])
    c["peaks"] = out
    return out


def star(layout, target="all", count: int = 2) -> list[int]:
    """The bulbs that crown a target area: the highest bulbs anywhere in the layout
    within the target's x-range that sit above it (e.g. the gable apex over the
    zigzag tree). Falls back to the target's own top bulbs."""
    key = ("star", repr(target), count)
    c = _cache(layout)
    if key in c:
        return c[key]
    px = layout.pixels
    tgt = set(indices(layout, target))
    if not tgt:
        c[key] = []
        return []
    xs = [px[i].x for i in tgt]
    top = max(px[i].y for i in tgt)
    lo, hi = min(xs), max(xs)
    mid = (lo + hi) / 2
    above = [p.i for p in px if p.i not in tgt and p.y > top and lo - 1 <= p.x <= hi + 1]
    pool = above or list(tgt)
    pool.sort(key=lambda i: (-px[i].y, abs(px[i].x - mid)))
    c[key] = pool[:count]
    return c[key]


# ------------------------------------------------------------------ paths
class Path:
    """An ordered chain of bulbs with arc length s (bulb units) and the slope at each."""

    def __init__(self, layout, order: list[int]):
        px = layout.pixels
        self.order = order
        self.s = [0.0]
        for a, b in zip(order, order[1:]):
            d = math.hypot(px[b].x - px[a].x, px[b].y - px[a].y)
            # a jump between runs (or across a hidden gap) counts as one bulb gap
            self.s.append(self.s[-1] + (d if d < 3 else 1.0))
        self.length = self.s[-1]
        self.y = [px[i].y for i in order]
        self.x = [px[i].x for i in order]

    def locate(self, s: float) -> tuple[int, float]:
        """(segment k, fraction f) so the point lies between order[k] and order[k+1]."""
        if s <= 0:
            return 0, 0.0
        if s >= self.length:
            return max(0, len(self.order) - 2), 1.0
        lo, hi = 0, len(self.s) - 1
        while hi - lo > 1:
            m = (lo + hi) // 2
            if self.s[m] <= s:
                lo = m
            else:
                hi = m
        seg = self.s[hi] - self.s[lo]
        return lo, (s - self.s[lo]) / seg if seg else 0.0

    def point(self, s: float) -> tuple[float, float]:
        k, f = self.locate(s)
        if len(self.order) == 1:
            return self.x[0], self.y[0]
        return self.x[k] + (self.x[k + 1] - self.x[k]) * f, self.y[k] + (self.y[k + 1] - self.y[k]) * f

    def slope(self, s: float) -> float:
        """dy/ds at s: positive going uphill in the direction of increasing s."""
        k, _ = self.locate(s)
        if len(self.order) < 2:
            return 0.0
        ds = self.s[k + 1] - self.s[k]
        return (self.y[k + 1] - self.y[k]) / ds if ds else 0.0


def path(layout, order: list[int]) -> Path:
    key = ("path", tuple(order))
    c = _cache(layout)
    if key not in c:
        c[key] = Path(layout, order)
    return c[key]


def run_path(layout, target="all") -> Path:
    """The target's bulbs in strip order as one path."""
    return path(layout, indices(layout, target))


# --------------------------------------------------------------- surfaces
def surface_below(layout, x: float, y: float, target="all", reach: float = 0.75):
    """(index, y) of the highest bulb at roughly this x that is below y: where a
    falling particle lands. None if nothing is below."""
    key = ("bins", repr(target))
    c = _cache(layout)
    if key not in c:
        bins: dict[int, list[int]] = {}
        for i in indices(layout, target):
            p = layout.pixels[i]
            bins.setdefault(math.floor(p.x), []).append(i)
        c[key] = bins
    bins = c[key]
    px = layout.pixels
    best = None
    for b in (math.floor(x) - 1, math.floor(x), math.floor(x) + 1):
        for i in bins.get(b, ()):
            p = px[i]
            if p.y < y and abs(p.x - x) <= reach and (best is None or p.y > px[best].y):
                best = i
    return (best, px[best].y) if best is not None else None


# ----------------------------------------------------------- splat / nearest
def _grid(layout) -> dict:
    c = _cache(layout)
    if "grid" not in c:
        g: dict[tuple[int, int], list[int]] = {}
        for p in layout.pixels:
            g.setdefault((math.floor(p.x), math.floor(p.y)), []).append(p.i)
        c["grid"] = g
    return c["grid"]


def near(layout, x: float, y: float, radius: float) -> list[tuple[int, float]]:
    """[(index, distance)] of bulbs within radius of a point."""
    g, px, out = _grid(layout), layout.pixels, []
    r = math.ceil(radius)
    for gx in range(math.floor(x) - r, math.floor(x) + r + 1):
        for gy in range(math.floor(y) - r, math.floor(y) + r + 1):
            for i in g.get((gx, gy), ()):
                d = math.hypot(px[i].x - x, px[i].y - y)
                if d <= radius:
                    out.append((i, d))
    return out


def nearest(layout, x: float, y: float, radius: float = 3.0):
    c = near(layout, x, y, radius)
    return min(c, key=lambda t: t[1])[0] if c else None


def splat(frame: list, layout, x: float, y: float, color, radius: float = 1.0,
          allowed: set | None = None, mode: str = "add"):
    """Draw a point at (x, y) between bulbs: each bulb within `radius` gets the colour
    weighted by closeness, so moving points glide instead of hopping bulb to bulb.
    mode "add" sums light, "max" keeps the brightest."""
    for i, d in near(layout, x, y, radius):
        if allowed is not None and i not in allowed:
            continue
        w = max(0.0, 1.0 - d / radius)
        w = w * w * (3 - 2 * w)                      # smooth falloff
        a = frame[i]
        if mode == "max":
            frame[i] = (max(a[0], color[0] * w), max(a[1], color[1] * w), max(a[2], color[2] * w))
        else:
            frame[i] = (a[0] + color[0] * w, a[1] + color[1] * w, a[2] + color[2] * w)


def splat_path(frame: list, pth: Path, s: float, color, width: float = 1.0, mode: str = "add"):
    """Anti-aliased point at arc length s along a path (bulb units)."""
    k, f = pth.locate(s)
    order = pth.order
    for j in range(max(0, k - int(width) - 1), min(len(order), k + int(width) + 3)):
        d = abs(pth.s[j] - s)
        if d < width:
            w = 1 - d / width
            a = frame[order[j]]
            if mode == "max":
                frame[order[j]] = (max(a[0], color[0] * w), max(a[1], color[1] * w), max(a[2], color[2] * w))
            else:
                frame[order[j]] = (a[0] + color[0] * w, a[1] + color[1] * w, a[2] + color[2] * w)
