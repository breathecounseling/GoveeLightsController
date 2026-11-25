"""Tiny pictures and digits for the zigzag (6 rows), drawn by row from the top.
'.' is off; '1'..'4' are palette colours. Pictures are sampled in run-relative
coordinates, so they stretch to fit whichever run they target (the Peak)."""
from __future__ import annotations

PICTURES: dict[str, list[str]] = {
    "tree": [
        "......4......",
        ".....111.....",
        "....12111....",
        "...1111131...",
        "..131111211..",
        "......3......",
    ],
    "star": [
        "......1......",
        ".....111.....",
        "1111111111111",
        "...1111111...",
        "..111...111..",
        ".11.......11.",
    ],
    "snowflake": [
        "...1..1..1...",
        "....1.1.1....",
        "..1111111111.",
        "....1.1.1....",
        "...1..1..1...",
        "......1......",
    ],
    "heart": [
        ".............",
        "...11...11...",
        "..1111.1111..",
        "...1111111...",
        ".....111.....",
        "......1......",
    ],
    "pumpkin": [
        "......3......",
        "....11111....",
        "...1211121...",
        "..111111111..",
        "..112222211..",
        "...1111111...",
    ],
    "present": [
        "....2.2.2....",
        ".....222.....",
        "..111121111..",
        "..222222222..",
        "..111121111..",
        "..111121111..",
    ],
    "smile": [
        ".............",
        "....1...1....",
        "....1...1....",
        "..1.......1..",
        "...1.....1...",
        "....11111....",
    ],
}

# default colours for '1', '2', '3', '4' in each picture
PICTURE_COLORS = {
    "tree": ["#00c040", "#ff2020", "#7a3b10", "#ffd000"],
    "star": ["#ffd000"],
    "snowflake": ["#d0ecff"],
    "heart": ["#ff1040"],
    "pumpkin": ["#ff6000", "#ffd040", "#20a020"],
    "present": ["#e01020", "#ffd000"],
    "smile": ["#ffd000"],
}

# 3x5 digits, drawn in rows 1..5 of the 6 (row 0, the top, stays dark)
DIGITS = {
    "0": ["111", "101", "101", "101", "111"], "1": ["010", "110", "010", "010", "111"],
    "2": ["111", "001", "111", "100", "111"], "3": ["111", "001", "111", "001", "111"],
    "4": ["101", "101", "111", "001", "001"], "5": ["111", "100", "111", "001", "111"],
    "6": ["111", "100", "111", "101", "111"], "7": ["111", "001", "010", "010", "010"],
    "8": ["111", "101", "111", "101", "111"], "9": ["111", "101", "111", "001", "111"],
}


def text_bitmap(text: str) -> list[str]:
    """Digits laid out side by side with a 1-column gap, padded to 6 rows."""
    rows = [""] * 5
    for k, ch in enumerate(text):
        g = DIGITS.get(ch)
        if not g:
            continue
        for r in range(5):
            rows[r] += ("." if k else "") + g[r].replace("0", ".")
    return ["." * len(rows[0])] + rows


def sample(bitmap: list[str], rnx: float, rny: float, cols_span: float = 1.0) -> str:
    """Character of the bitmap at run-relative (x 0..1 left→right, y 0..1 bottom→top)."""
    nrows = len(bitmap)
    r = min(nrows - 1, max(0, int((1 - rny) * nrows - 1e-9)))     # top row first
    width = len(bitmap[0])
    x = 0.5 + (rnx - 0.5) / cols_span
    if x < 0 or x >= 1:
        return "."
    c = min(width - 1, int(x * width))
    return bitmap[r][c]
