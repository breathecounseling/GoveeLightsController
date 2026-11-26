"""A tiny 5-row pixel font for the zigzag (and anything else with a few rows).

Each glyph is 5 strings, top row first, '#' lit and '.' dark; glyphs are 1-5
columns wide (most are 3) and text is laid out with a 1-column gap between
glyphs. Lower case is drawn as upper case; unknown characters are skipped.

    layout(text) -> Bitmap with .width, .cols (5-bit masks per column, bit 0 = top
                    row), .letter (index of the glyph each column belongs to, -1 in
                    gaps), .chars (the glyphs drawn, in order) and .word (word number)
"""
from __future__ import annotations

from dataclasses import dataclass, field

ROWS = 5
HEART = "♥"
STAR = "*"

GLYPHS: dict[str, tuple[str, ...]] = {
    "A": (".#.", "#.#", "###", "#.#", "#.#"),
    "B": ("##.", "#.#", "##.", "#.#", "##."),
    "C": (".##", "#..", "#..", "#..", ".##"),
    "D": ("##.", "#.#", "#.#", "#.#", "##."),
    "E": ("###", "#..", "##.", "#..", "###"),
    "F": ("###", "#..", "##.", "#..", "#.."),
    "G": (".##.", "#...", "#.##", "#..#", ".##."),
    "H": ("#.#", "#.#", "###", "#.#", "#.#"),
    "I": ("###", ".#.", ".#.", ".#.", "###"),
    "J": ("..#", "..#", "..#", "#.#", ".#."),
    "K": ("#..#", "#.#.", "##..", "#.#.", "#..#"),
    "L": ("#..", "#..", "#..", "#..", "###"),
    "M": ("#...#", "##.##", "#.#.#", "#...#", "#...#"),
    "N": ("#..#", "##.#", "#.##", "#..#", "#..#"),
    "O": (".##.", "#..#", "#..#", "#..#", ".##."),
    "P": ("##.", "#.#", "##.", "#..", "#.."),
    "Q": (".##.", "#..#", "#..#", "#.#.", ".#.#"),
    "R": ("##.", "#.#", "##.", "#.#", "#.#"),
    "S": (".##", "#..", ".#.", "..#", "##."),
    "T": ("###", ".#.", ".#.", ".#.", ".#."),
    "U": ("#.#", "#.#", "#.#", "#.#", "###"),
    "V": ("#.#", "#.#", "#.#", "#.#", ".#."),
    "W": ("#...#", "#...#", "#.#.#", "##.##", "#...#"),
    "X": ("#.#", "#.#", ".#.", "#.#", "#.#"),
    "Y": ("#.#", "#.#", ".#.", ".#.", ".#."),
    "Z": ("###", "..#", ".#.", "#..", "###"),
    "0": ("###", "#.#", "#.#", "#.#", "###"),
    "1": (".#.", "##.", ".#.", ".#.", "###"),
    "2": ("##.", "..#", ".#.", "#..", "###"),
    "3": ("##.", "..#", ".#.", "..#", "##."),
    "4": ("#.#", "#.#", "###", "..#", "..#"),
    "5": ("###", "#..", "##.", "..#", "##."),
    "6": (".##", "#..", "###", "#.#", "###"),
    "7": ("###", "..#", ".#.", ".#.", ".#."),
    "8": ("###", "#.#", "###", "#.#", "###"),
    "9": ("###", "#.#", "###", "..#", "##."),
    " ": ("...", "...", "...", "...", "..."),
    "!": ("#", "#", "#", ".", "#"),
    "?": ("##.", "..#", ".#.", "...", ".#."),
    "-": ("...", "...", "###", "...", "..."),
    ":": (".", "#", ".", "#", "."),
    ".": (".", ".", ".", ".", "#"),
    ",": ("..", "..", "..", ".#", "#."),
    "'": ("#", "#", ".", ".", "."),
    "+": ("...", ".#.", "###", ".#.", "..."),
    "&": (".#..", "#.#.", ".#.#", "#.#.", ".#.#"),
    "#": (".#.#.", "#####", ".#.#.", "#####", ".#.#."),
    "/": ("..#", "..#", ".#.", "#..", "#.."),
    HEART: (".#.#.", "#####", "#####", ".###.", "..#.."),
    STAR: ("..#..", "#####", ".###.", ".#.#.", "#...#"),
}

# typed stand-ins for symbols that are awkward to enter on a phone
ALIASES = {"<3": HEART, "❤": HEART, "♡": HEART, "☆": STAR, "★": STAR,
           "’": "'", "‘": "'", "–": "-", "—": "-", '"': "'", "`": "'", "_": "-"}


def normalise(text: str) -> str:
    """Upper-case and map aliases (<3 -> heart); drop characters the font lacks."""
    s = str(text or "")
    for a, b in ALIASES.items():
        s = s.replace(a, b)
    s = s.upper()
    return "".join(ch for ch in s if ch in GLYPHS)


@dataclass
class Bitmap:
    width: int = 0
    cols: list[int] = field(default_factory=list)      # per column: bit r set = row r (0 = top) lit
    letter: list[int] = field(default_factory=list)    # glyph number per column (-1 = gap / space)
    word: list[int] = field(default_factory=list)      # word number per column
    chars: str = ""                                    # the glyphs drawn, one per glyph number


_CACHE: dict[str, Bitmap] = {}


def layout(text: str) -> Bitmap:
    """Lay a message out as columns (cached)."""
    key = str(text or "")
    bm = _CACHE.get(key)
    if bm is not None:
        return bm
    s = normalise(key)
    bm = Bitmap(chars=s)
    word = 0
    for k, ch in enumerate(s):
        g = GLYPHS[ch]
        if ch == " ":
            if k and s[k - 1] != " ":
                word += 1
        elif bm.cols and s[k - 1] != " ":
            _push(bm, 0, -1, word)                        # 1-column gap between glyphs
        for c in range(len(g[0])):
            m = 0
            for r in range(ROWS):
                if g[r][c] == "#":
                    m |= 1 << r
            _push(bm, m, -1 if ch == " " else k, word)
    bm.width = len(bm.cols)
    if len(_CACHE) > 256:
        _CACHE.clear()
    _CACHE[key] = bm
    return bm


def _push(bm: Bitmap, mask: int, letter: int, word: int):
    bm.cols.append(mask)
    bm.letter.append(letter)
    bm.word.append(word)


def render(text: str) -> list[str]:
    """The message as 5 strings of '#'/'.' (handy for tests and debugging)."""
    bm = layout(text)
    return ["".join("#" if m >> r & 1 else "." for m in bm.cols) for r in range(ROWS)]
