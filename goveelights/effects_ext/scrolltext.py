"""Scrolling text and a live scoreboard on the zigzag.

The zigzag (the Peak run) is a little triangular display: rows of 27, 23, 20, 16,
12 and 10 bulbs. Text uses the bottom five rows with the 5-row pixel font in
goveelights/font.py; anything above (the top row) can carry a twinkling or
marquee border. Glyph columns are laid out by x position, so letters stay upright
and columns line up across rows of different widths (the rows are staggered by
half a bulb), and every bulb is lit by how much of a lit glyph column its own
footprint covers, so the text glides smoothly instead of hopping bulb to bulb.

Message sources: your own text, the clock, the live game score (from
goveelights.livedata, which the controller updates while polling games) or days
to go until a date. Your text may also contain {time}, {days}, {score} or {label}.
"""
from __future__ import annotations

import datetime as _dt
import math
import time as _time

from .. import font, geometry, livedata
from ..color import BLACK, clamp, hash01, hex_to_rgb, hsv, lerp, palette, scale
from ..effects import Param, frame_effect, travel

GROUP = "Text, pictures & numbers"
WHITE = (1.0, 1.0, 1.0)
HEART_RGB = hex_to_rgb("#ff1040")
COLOR_MODES = ["letters", "words", "rainbow", "flow", "solid"]
SOURCES = ["custom", "clock", "score", "days"]
BORDERS = ["twinkle", "marquee", "glow", "off"]
CLOCKS = ["12h", "12h am/pm", "24h"]

_now = _time.time          # patched by tests


# ------------------------------------------------------------------ messages
def _clock(fmt: str, now: float) -> str:
    lt = _time.localtime(now)
    if fmt == "24h":
        return f"{lt.tm_hour}:{lt.tm_min:02d}"
    h = lt.tm_hour % 12 or 12
    s = f"{h}:{lt.tm_min:02d}"
    return s + (" AM" if lt.tm_hour < 12 else " PM") if fmt == "12h am/pm" else s


def parse_date(s: str) -> tuple[int, int]:
    """'MM-DD' (also M/D, MM.DD) -> (month, day); falls back to Christmas."""
    try:
        txt = str(s).strip().replace("/", "-").replace(".", "-")
        m, d = (int(v) for v in txt.split("-")[:2])
        _dt.date(2024, m, d)                         # validates (2024 allows 02-29)
        return m, d
    except (TypeError, ValueError):
        return 12, 25


def days_until(mmdd: str, now: float) -> int:
    m, d = parse_date(mmdd)
    today = _dt.date.fromtimestamp(now)
    for year in (today.year, today.year + 1, today.year + 2, today.year + 4):
        try:
            target = _dt.date(year, m, d)
        except ValueError:                           # 02-29 in a non-leap year
            continue
        if target >= today:
            return (target - today).days
    return 0


def game_text(g: dict, detail: bool) -> tuple[str, str] | None:
    """('DEN 17', 'PIT 10[ Q3 ...]') for a game dict, or None if it is unusable."""
    try:
        ours = f"{str(g['team']).upper()} {int(g.get('score', 0))}"
        theirs = f"{str(g.get('opponent', '')).upper()} {int(g.get('opp_score', 0))}".strip()
    except (KeyError, TypeError, ValueError):
        return None
    if detail and g.get("detail"):
        theirs += "  " + str(g["detail"]).replace(" - ", " ")
    return ours, theirs


def build_message(P: dict, now: float) -> tuple[str, dict]:
    """The message to show and extras: {"accent": (start, end, colour)} (team colours
    for our side of a score) and {"score_key": ...} to spot score changes."""
    src, text, extra = P.get("source", "custom"), str(P.get("text", "")), {}
    label = str(P.get("label", "")).strip() or "XMAS"
    days = days_until(P.get("date", "12-25"), now)
    game = livedata.current_game() if (src == "score" or "{score}" in text) else None
    gt = game_text(game, bool(P.get("score_detail"))) if game else None
    score = f"{gt[0]}  {gt[1]}" if gt else ""
    if src == "clock":
        text = "{time}"
    elif src == "days":
        if days > 0:
            text = f"{days} DAY{'S' if days != 1 else ''} TO {label}"
    elif src == "score" and gt:
        text = score
        extra["accent"] = (0, len(font.normalise(gt[0])), _team_colour(game))
        extra["score_key"] = (game.get("id"), game.get("score"), game.get("opp_score"))
    if "{" in text:
        text = (text.replace("{time}", _clock(P.get("clock", "12h"), now)).replace("{days}", str(days))
                .replace("{label}", label).replace("{score}", score))
    return text.upper(), extra


def _team_colour(g: dict):
    for h in (g.get("colors") or [])[:2]:
        try:
            c = hex_to_rgb(h)
        except (TypeError, ValueError, AttributeError):
            continue
        if max(c) > 0.25:                            # team colours like navy read as off on LEDs
            m = max(c)
            return (c[0] / m, c[1] / m, c[2] / m)
    return None


# ------------------------------------------------------------------ display
def _display(layout, target, idx: list[int], cache: dict) -> dict:
    """Where the text goes for this target: text bulbs (index, x, glyph row from the
    top), border bulbs above them, the rest of the target, and the window's extent."""
    key = ("scrolltext", id(layout), id(layout.pixels), len(layout.pixels), repr(target))
    d = cache.get(key)
    if d is not None:
        return d
    px = layout.pixels
    tset = set(idx)
    best = None
    for run in getattr(layout, "runs", []):
        try:
            order = layout.indices(run.name)
        except Exception:  # noqa: BLE001
            continue
        rr = geometry._rows_by_reversal(px, order) if order else None
        if not rr:
            continue
        rr = [sorted((i for i in r if i in tset), key=lambda i: px[i].x) for r in rr]
        rr.sort(key=lambda r: sum(px[i].y for i in r) / len(r) if r else 0)
        n = sum(len(r) for r in rr)
        if n and (best is None or n > best[0]):
            best = (n, [r for r in rr if r])
    rows = best[1] if best else None
    if rows is None and idx:                        # no zigzag: the target's own rows by height
        rows = geometry._rows_by_reversal(px, list(idx))
        if rows is None:
            rows = []
            for i in sorted(idx, key=lambda i: px[i].y):
                if rows and abs(px[i].y - px[rows[-1][0]].y) <= 0.45:
                    rows[-1].append(i)
                else:
                    rows.append([i])
        rows = [sorted(r, key=lambda i: px[i].x) for r in rows]
        rows.sort(key=lambda r: sum(px[i].y for i in r) / len(r))
        if len(rows) < 2:
            rows = []
    rows = rows or []
    text_rows, border_rows = rows[:font.ROWS], rows[font.ROWS:]
    nr = len(text_rows)
    cells = [(i, px[i].x, font.ROWS - 1 - k) for k, r in enumerate(text_rows) for i in r]
    border = [(i, px[i].x) for r in border_rows for i in r]
    used = {c[0] for c in cells} | {b[0] for b in border}
    dx = sorted(px[b].x - px[a].x for r in rows for a, b in zip(r, r[1:]) if px[b].x - px[a].x > 1e-6)
    pitch = dx[len(dx) // 2] if dx else 1.0
    xs = [c[1] for c in cells]
    top = text_rows[-1] if text_rows else []
    d = {
        "cells": cells, "border": border, "rest": [i for i in idx if i not in used], "rows": nr,
        "pitch": max(0.2, min(2.0, pitch)),
        "xl": min(xs) if xs else 0.0, "xr": max(xs) if xs else 0.0,
        # centre of the full-height part of the window (the top text row)
        "xc": (min(px[i].x for i in top) + max(px[i].x for i in top)) / 2 if top else 0.0,
    }
    cache[key] = d
    return d


def _letter_colours(bm: font.Bitmap, P: dict, clock: float, extra: dict) -> list:
    """Colour of each glyph of the message (None for spaces)."""
    cols, mode = P["colors"] or [WHITE], P["color_mode"]
    out, n = [], 0
    accent = extra.get("accent")
    for k, ch in enumerate(bm.chars):
        if ch == " ":
            out.append(None)
            continue
        if mode == "solid":
            c = cols[0]
        elif mode == "words":
            c = None
        elif mode == "rainbow":
            c = hsv(n * 0.09 - clock * 0.04, 1.0, 1.0)
        else:
            c = cols[n % len(cols)]
        if ch == font.HEART and mode != "solid":
            c = HEART_RGB
        if accent and accent[2] is not None and accent[0] <= k < accent[1]:
            c = accent[2]
        out.append(c)
        n += 1
    if mode == "words":                              # fill per word (needs the word numbers)
        wnum, w = [], 0
        for k, ch in enumerate(bm.chars):
            if ch == " " and k and bm.chars[k - 1] != " ":
                w += 1
            wnum.append(w)
        out = [None if c is None and ch == " " else (c or cols[wnum[k] % len(cols)])
               for k, (c, ch) in enumerate(zip(out, bm.chars))]
        if accent and accent[2] is not None:
            for k in range(accent[0], min(accent[1], len(out))):
                if out[k] is not None:
                    out[k] = accent[2]
    return out


@frame_effect("text", "Scrolling text", [
    Param("text", "text", "MERRY CHRISTMAS", "Message",
          help="what to scroll (A-Z, 0-9, ! ? - : . ' + & # / and <3 for a heart). "
               "May include {time}, {days}, {score} or {label}"),
    Param("source", "choice", "custom", "Source", options=SOURCES,
          help="custom: your message; clock: the time; score: the live game score (your message when "
               "no game is on); days: days to go until the date below"),
    Param("colors", "colors", ["#ff1a1a", "#00d040", "#ffc000"], "Letter colours",
          help="cycled per letter (or per word); 'flow' blends them across the display"),
    Param("color_mode", "choice", "letters", "Colouring", options=COLOR_MODES,
          help="letters: each letter the next colour; words: each word; rainbow: a rainbow along the "
               "message; flow: colours drift across the display; solid: all the first colour"),
    Param("background", "color", "#000000", help="behind the letters (keep it dark so the text reads)"),
    Param("speed", "number", 3.5, "Scroll speed", -15, 15, 0.25,
          help="bulb widths per second; 0 holds the text still, centred; negative scrolls to the right"),
    Param("size", "number", 0.85, "Letter width", 0.6, 1.6, 0.05,
          help="width of one font column in bulb units: bigger letters are bolder but fewer fit"),
    Param("gap", "int", 10, "Gap before repeating", 0, 60, 1, help="blank columns before the message repeats"),
    Param("brightness", "number", 1.0, "Brightness", 0.1, 1, 0.05, help="of the letters"),
    Param("sparkle", "number", 0.25, "Glints", 0, 1, 0.05,
          help="random bulbs in the letters flash bright, like glitter catching the light"),
    Param("border", "choice", "twinkle", "Top row", options=BORDERS,
          help="the zigzag's top row above the text: twinkling, a marquee chase, a steady glow, or off"),
    Param("border_color", "color", "#ffb000", "Top row colour"),
    Param("clock", "choice", "12h", "Clock style", options=CLOCKS, help="for source 'clock' or {time}"),
    Param("date", "text", "12-25", "Count to (MM-DD)", help="for source 'days', e.g. 12-25 or 01-01"),
    Param("label", "text", "XMAS", "Counting to", help="for source 'days': 24 DAYS TO <this>"),
    Param("score_detail", "bool", False, "Show game clock", help="for source 'score': add the quarter/time"),
    Param("seed", "int", 7, "Seed", 0, 9999, 1),
], "Words glide across the zigzag like a marquee sign, letter by letter in festive colours with glints, under a twinkling top row. Can also show the time, the live game score or days to go. Target the Peak.", group=GROUP)
def scroll_text(t, ctx, P, state):
    layout = ctx.layout
    n = len(layout.pixels)
    frame = [BLACK] * n
    if not ctx.idx:
        return frame
    D = _display(layout, _target_key(ctx), ctx.idx, ctx.cache)
    clock = ctx.clock
    dt = min(0.1, max(0.0, ctx.dt or 0.0))
    bg = P["background"]
    for i in ctx.idx:
        frame[i] = bg

    # ---- message (live sources refresh every frame; layout is cached by string)
    now = _now()
    msg, extra = build_message(P, now)
    bm = font.layout(msg)
    W = bm.width
    if "origin" not in state:                           # start fresh: text enters from the edge
        state["origin"] = travel(t, P)
    sk = extra.get("score_key")
    if sk is not None:
        prev = state.get("score_key")
        if prev is not None and prev[0] == sk[0] and prev != sk:
            state["flash"] = 1.0                        # the score changed: celebrate
        state["score_key"] = sk
    flash = state["flash"] = max(0.0, state.get("flash", 0.0) - dt / 4.0)

    cw = P["size"]
    speed = float(P.get("speed", 0.0))
    tc = (travel(t, P) - state["origin"]) / cw          # columns scrolled so far
    period = W + max(0, P["gap"])
    h = D["pitch"] / (2 * cw)                           # half a bulb's footprint, in columns
    inv = 1.0 / (2 * h)
    still = abs(speed) < 1e-6
    if still:
        base, sign = (D["xc"] - W * cw / 2) / cw, 0
    elif speed > 0:
        base, sign = D["xr"] / cw + h - tc, 1           # column 0 starts just off the right edge
    else:
        base, sign = (D["xl"] - W * cw) / cw - h - tc, -1   # last column starts just off the left

    # ---- per-glyph colours, then each text bulb by coverage of lit columns
    lcols = _letter_colours(bm, P, clock, extra) if W else []
    flow = P["color_mode"] == "flow"
    pal = P["colors"] or [WHITE]
    span = max(1.0, D["xr"] - D["xl"])
    br = P["brightness"]
    glint = P["sparkle"] * 0.12
    seed = P["seed"]
    gt = clock * 3.0
    cols_, letters = bm.cols, bm.letter
    strobe = flash * (0.55 + 0.45 * math.cos(2 * math.pi * clock * 3.0)) if flash else 0.0
    if W:
        for i, x, gr in D["cells"]:
            q = x / cw - base
            lo, hi = q - h, q + h
            r = g = b = cov = 0.0
            c = math.floor(lo)
            while c < hi:
                ov = min(hi, c + 1) - max(lo, c)
                if ov > 0:
                    if still:
                        cm = c if 0 <= c < W else -1
                    else:
                        k = c // period
                        cm = c - k * period
                        if (sign > 0 and k < 0) or (sign < 0 and k > 0) or cm >= W:
                            cm = -1
                    if cm >= 0 and cols_[cm] >> gr & 1:
                        col = lcols[letters[cm]] if letters[cm] >= 0 else None
                        if flow or col is None:
                            col = palette(pal, (x - D["xl"]) / span * 0.6 - clock * 0.08)
                        r += col[0] * ov
                        g += col[1] * ov
                        b += col[2] * ov
                        cov += ov
                c += 1
            if cov <= 0:
                continue
            cov = min(1.0, cov * inv)
            col = (r * inv / cov, g * inv / cov, b * inv / cov)
            if glint:
                ph = gt + hash01(i, seed, 5)
                kb = math.floor(ph)
                if hash01(i, kb, seed) < glint:
                    col = lerp(col, WHITE, 0.75 * (1.0 - (ph - kb)) ** 2)
            if strobe:
                col = lerp(col, WHITE, strobe)
            col = scale(col, br)
            frame[i] = lerp(bg, col, cov * cov * (3 - 2 * cov))

    # ---- the top row: border
    mode = P["border"]
    if mode != "off" and D["border"]:
        bc = P["border_color"]
        for j, (i, x) in enumerate(D["border"]):
            if mode == "glow":
                v = 0.35 + 0.08 * math.sin(2 * math.pi * (clock * 0.25 + x * 0.05))
            elif mode == "marquee":
                # dots every 3 bulbs marching with the text
                u = (x / D["pitch"] + (0 if still else (tc * cw) / D["pitch"])) / 3.0
                f = u - math.floor(u)
                v = 0.12 + 0.88 * clamp(1.0 - abs(f - 0.5) * 3.0)
            else:
                rate = 0.25 + 0.35 * hash01(i, seed, 11)
                s = 0.5 + 0.5 * math.sin(2 * math.pi * (clock * rate + hash01(i, seed, 12)))
                v = 0.1 + 0.9 * s ** 4
            frame[i] = scale(bc, v)
    return frame


def _target_key(ctx) -> tuple:
    """A cache key for the layer's target (ctx has the resolved indices, not the name)."""
    idx = ctx.idx
    return ("idx", len(idx), idx[0] if idx else -1, idx[-1] if idx else -1, hash(tuple(idx)))
