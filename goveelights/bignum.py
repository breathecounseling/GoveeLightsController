"""Big numbers on the zigzag, bulb for bulb (the countdown and the days-to-go number).

The Peak is a little screen six rows tall: 27 bulbs across at the bottom, 10 at the
top, and the rows don't line up (the bottom two sit on whole bulb positions, the top
four half a bulb across from them). Squeezing a bitmap into that triangle by
proportion made strokes three bulbs fat, holes one row high and the tops of two-digit
numbers fall off the narrow top rows, so from the street 3, 8, 9 and 0 all looked alike.

Here each glyph is drawn in bulbs: one font row per zigzag row (all six), one font
column per bulb, centred on the gable.

    one digit   a bold font, 6 wide (strokes two bulbs wide, holes two wide)
    two digits  a 5-wide font (strokes one bulb, holes three), two bulbs apart; the
                glyphs keep their top rows inside the Peak's 10-bulb top row

On a row that sits half a bulb across from the font's columns, each bulb takes the
column on its side towards the middle of the gable: a lone digit flares out by half a
bulb on each side instead of breaking its strokes, and the two digits of a pair each
step half a bulb outward, so the gap between them widens instead of closing up.

A zigzag with five rows uses the 5-row font (font.GLYPHS), one with more than six the
six rows nearest the bottom that the number fits. A run that continues the zigzag (the
Tip, layout "extends") is not used for the digits, so the number is the same with or
without it; `crown` gives its apex for a marker.

    cells(layout, idx, text) -> {bulb index: 1.0} for the lit bulbs
    crown(layout, idx)       -> the Tip's apex bulb(s), [] without a Tip
"""
from __future__ import annotations

from . import font
from . import geometry as geo

# one digit: bold, six bulbs wide (top row first)
BOLD = {
    "0": (".####.", "##..##", "##..##", "##..##", "##..##", ".####."),
    "1": ("..##..", ".###..", "..##..", "..##..", "..##..", ".####."),
    "2": (".####.", "##..##", "...##.", "..##..", ".##...", "######"),
    "3": ("#####.", "....##", "..###.", "....##", "....##", "#####."),
    "4": ("##..##", "##..##", "##..##", "######", "....##", "....##"),
    "5": ("######", "##....", "#####.", "....##", "....##", "#####."),
    "6": (".####.", "##....", "#####.", "##..##", "##..##", ".####."),
    "7": ("######", "....##", "...##.", "..##..", "..##..", "..##.."),
    "8": (".####.", "##..##", ".####.", "##..##", "##..##", ".####."),
    "9": (".####.", "##..##", "##..##", ".#####", "....##", ".####."),
}
# two digits: five bulbs wide, two apart (12 across). The Peak's top row is only 10
# bulbs, so a glyph's top-row outer corner can fall off it: the glyphs are drawn so
# that costs nothing that matters (round tops; at worst a bar a bulb short)
PAIR = {
    "0": (".###.", "#...#", "#...#", "#...#", "#...#", ".###."),
    "1": ("..#..", ".##..", "..#..", "..#..", "..#..", ".###."),
    "2": (".###.", "#...#", "...#.", "..#..", ".#...", "#####"),
    "3": ("####.", "....#", ".###.", "....#", "....#", "####."),
    "4": ("#..#.", "#..#.", "#..#.", "#####", "...#.", "...#."),
    "5": ("#####", "#....", "####.", "....#", "....#", "####."),
    "6": (".###.", "#....", "####.", "#...#", "#...#", ".###."),
    "7": ("#####", "....#", "...#.", "..#..", "..#..", "..#.."),
    "8": (".###.", "#...#", ".###.", "#...#", "#...#", ".###."),
    "9": (".###.", "#...#", "#...#", ".####", "....#", ".###."),
}
GAP = 2


def _bitmap(text: str, nrows: int) -> list[str]:
    """The text as rows of '#'/'.', top row first."""
    s = "".join(ch for ch in str(text) if ch in BOLD)
    if not s:
        return []
    if nrows >= 6:
        if len(s) == 1:
            return list(BOLD[s])
        gl = [PAIR[ch] for ch in s]
        return [(("." * GAP).join(g[r] for g in gl)) for r in range(6)]
    gl = [font.GLYPHS[ch] for ch in s]                 # a shorter zigzag: the 5-row font
    return [".".join(g[r] for g in gl) for r in range(5)]


def zigzag(layout, idx) -> tuple[list[list[int]], list[list[int]]]:
    """(rows, tip_rows): the target's zigzag rows bottom first (each left to right), split
    into the zigzag's own rows and those of a run that continues it (the Tip)."""
    key = ("bignum-zig", tuple(idx))
    c = geo._cache(layout)
    if key in c:
        return c[key]
    px = layout.pixels
    groups: dict[str, list[int]] = {}
    for i in idx:
        groups.setdefault(px[i].run, []).append(i)
    rows = None
    for g in sorted(groups.values(), key=len, reverse=True):
        rows = geo._rows_by_reversal(px, g)
        if rows:
            break
    if not rows:                                   # no zigzag: the target's rows by height
        rows = []
        for i in sorted(idx, key=lambda i: px[i].y):
            if rows and abs(px[i].y - px[rows[-1][0]].y) <= 0.45:
                rows[-1].append(i)
            else:
                rows.append([i])
    rows = [sorted(r, key=lambda i: px[i].x) for r in rows if r]
    rows.sort(key=lambda r: sum(px[i].y for i in r) / len(r))
    own = [r for r in rows if not all(getattr(px[i], "ext", False) for i in r)]
    tip = [r for r in rows if all(getattr(px[i], "ext", False) for i in r)]
    c[key] = out = (own or rows, tip if own else [])
    return out


def crown(layout, idx) -> list[int]:
    """The Tip's apex: its top row's middle bulb(s); [] without a Tip."""
    _, tip = zigzag(layout, idx)
    if not tip:
        return []
    px = layout.pixels
    top = tip[-1]
    mid = sum(px[i].x for i in top) / len(top)
    best = min(abs(px[i].x - mid) for i in top)
    return [i for i in top if abs(px[i].x - mid) <= best + 0.05]


def cells(layout, idx, text) -> dict[int, float]:
    """{bulb: 1.0} for the bulbs that draw `text` (digits) on the target's zigzag."""
    key = ("bignum-cells", tuple(idx), str(text))
    c = geo._cache(layout)
    if key in c:
        return c[key]
    rows, _ = zigzag(layout, idx)
    out: dict[int, float] = {}
    nf = 6 if len(rows) >= 6 else 5
    bm = _bitmap(text, nf)
    if not rows or not bm:
        c[key] = out
        return out
    px = layout.pixels
    W = len(bm[0])
    dx = sorted(px[b].x - px[a].x for r in rows for a, b in zip(r, r[1:]) if px[b].x - px[a].x > 1e-6)
    pitch = dx[len(dx) // 2] if dx else 1.0
    top_all = rows[-1]
    cx = sum(px[i].x for i in top_all) / len(top_all)
    # which rows: all of a six-row zigzag; on a taller one the lowest six the number fits
    # (wide enough at the top); a shorter one maps its rows onto the font's by height
    use = rows
    if len(rows) > len(bm):
        fits = [k for k in range(len(rows) - len(bm), -1, -1)
                if (px[rows[k + len(bm) - 1][-1]].x - px[rows[k + len(bm) - 1][0]].x) / pitch + 1 >= W]
        k0 = fits[-1] if fits else 0
        use = rows[k0:k0 + len(bm)]
    nr = len(use)
    # the font's columns sit on whole or half bulb positions, whichever most rows have
    # (weighted to the narrow top rows, where every bulb counts)
    halves = 0.0
    for k, r in enumerate(use):
        f = [((px[i].x - cx) / pitch) % 1.0 for i in r]
        on_half = sum(1 for v in f if 0.25 <= v < 0.75) > len(f) / 2
        halves += (1 + k / max(1, nr - 1)) * (1 if on_half else -1)
    phase = 0.5 if halves > 0 else 0.0
    left = -(W - 1) / 2
    left = round(left - phase) + phase             # a font column on the rows' grid
    for k, r in enumerate(use):
        g = (nf - 1 - (round(k * (nf - 1) / (nr - 1)) if nr > 1 else nf // 2))
        line = bm[g]
        for i in r:
            X = (px[i].x - cx) / pitch
            u = X - left                           # font column, fractional
            fr = u - round(u)
            if abs(fr) <= 0.25:
                cols = [round(u)]
            elif abs(X) < 0.25:                     # in the middle, between two columns: either
                cols = [int(u // 1), int(u // 1) + 1]
            elif X < 0:                             # half a bulb off: the column towards the middle
                cols = [int(u // 1) + 1]
            else:
                cols = [int(u // 1)]
            if any(0 <= cc < W and line[cc] == "#" for cc in cols):
                out[i] = 1.0
    if len(c) > 512:
        for k in [k for k in c if isinstance(k, tuple) and k and k[0] == "bignum-cells"]:
            del c[k]
    c[key] = out
    return out
