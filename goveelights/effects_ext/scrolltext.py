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
WINDOWS = ["fade", "full"]
FADE_S = 0.4              # cross-fade when a still message changes, or the text is edited
FLANK = 0.2               # 'fade' window: letter strength on the far flanks

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
    for year in range(today.year, today.year + 5):     # 02-29 can be up to 4 years away
        try:
            target = _dt.date(year, m, d)
        except ValueError:                           # 02-29 in a non-leap year
            continue
        if target >= today:
            return (target - today).days
    return 0


def game_text(g: dict, detail: bool) -> tuple[str, str] | None:
    """('DEN 17', 'PIT 10[  3RD 4:12]') for a game dict, or None if it is unusable."""
    try:
        ours = f"{str(g['team']).upper()} {int(g.get('score', 0))}"
        theirs = f"{str(g.get('opponent', '')).upper()} {int(g.get('opp_score', 0))}".strip()
    except (KeyError, TypeError, ValueError):
        return None
    if detail and g.get("detail") and g.get("state") == "in":
        theirs += "  " + str(g["detail"]).replace(" - ", " ")
    return ours, theirs


SEP = " - "               # between the two teams' scores, drawn dim
SEP_RGB = (0.38, 0.38, 0.38)


def build_message(P: dict, now: float) -> tuple[str, dict]:
    """The message to show and extras: {"accent": (start, end, colour)} (team colours
    for our side of a score), {"dim": (start, end)} (the separator between the scores)
    and {"score_key": ...} to spot score changes."""
    src, text, extra = P.get("source", "custom"), str(P.get("text", "")), {}
    label = str(P.get("label", "")).strip() or "XMAS"
    days = days_until(P.get("date", "12-25"), now)
    game = livedata.current_game() if (src == "score" or "{score}" in text) else None
    gt = game_text(game, bool(P.get("score_detail"))) if game else None
    pre = "FINAL " if gt and game.get("state") == "post" else ""
    score = f"{pre}{gt[0]}{SEP}{gt[1]}" if gt else ""
    if src == "clock":
        text = "{time}"
    elif src == "days":
        if days > 0:
            text = f"{days} DAY{'S' if days != 1 else ''} TO {label}"
    elif src == "score" and gt:
        text = score
        a0 = len(font.normalise(pre))
        a1 = a0 + len(font.normalise(gt[0]))
        extra["accent"] = (a0, a1, _team_colour(game))
        extra["dim"] = (a1, a1 + len(font.normalise(SEP)))
        extra["score_key"] = (game.get("id"), game.get("score"), game.get("opp_score"))
    if "{" in text:
        text = (text.replace("{time}", _clock(P.get("clock", "12h"), now)).replace("{days}", str(days))
                .replace("{label}", label).replace("{score}", score))
    return text.upper(), extra


def _luma(c) -> float:
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def _team_colour(g: dict):
    """The team's colour as a bright LED colour. Dark colours (navy, forest green,
    maroon) read as off or turn into odd hues when brightened, so the first colour
    that is genuinely bright is used; failing that the brighter one if it is not dark."""
    cands = []
    for h in (g.get("colors") or [])[:2]:
        try:
            c = hex_to_rgb(h)
        except (TypeError, ValueError, AttributeError):
            continue
        if _luma(c) >= 0.18:
            m = max(c)
            return (c[0] / m, c[1] / m, c[2] / m)
        cands.append(c)
    c = max(cands, key=max, default=None)
    if c is not None and max(c) >= 0.5:
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
    tl = min(px[i].x for i in top) if top else 0.0
    tr = max(px[i].x for i in top) if top else 0.0
    d = {
        "cells": cells, "border": border, "rest": [i for i in idx if i not in used], "rows": nr,
        "pitch": max(0.2, min(2.0, pitch)),
        "xl": min(xs) if xs else 0.0, "xr": max(xs) if xs else 0.0,
        # the full-height part of the window (under the top text row) and its centre
        "xc": (tl + tr) / 2, "tl": tl, "tr": tr,
        # per text bulb: how far outside the full-height part it is (house units)
        "out": [max(0.0, tl - x, x - tr) for _, x, _ in cells],
    }
    cache[key] = d
    return d


def _letter_colours(bm: font.Bitmap, P: dict, clock: float, extra: dict) -> list:
    """Colour of each glyph of the message (None for spaces)."""
    cols, mode = P["colors"] or [WHITE], P["color_mode"]
    out, n = [], 0
    accent, dim = extra.get("accent"), extra.get("dim")
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
        if dim and dim[0] <= k < dim[1]:
            c = SEP_RGB
        out.append(c)
        n += 1
    if mode == "words":                              # fill per word (needs the word numbers)
        wnum, w = [], int(extra.get("word0", 0))     # word by word: that word's place in the message
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
        if dim:
            for k in range(dim[0], min(dim[1], len(out))):
                if out[k] is not None:
                    out[k] = SEP_RGB
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
    Param("speed", "number", 5.0, "Scroll speed", -20, 20, 0.25,
          help="zigzag bulbs per second; 0 holds the text still, centred; negative scrolls to the right"),
    Param("size", "number", 1.2, "Letter width", 0.8, 2.3, 0.05,
          help="width of one font column, in zigzag bulbs: bigger letters are bolder but fewer fit"),
    Param("gap", "int", 10, "Gap before repeating", 0, 60, 1,
          help="blank font columns before the message comes round again"),
    Param("window", "choice", "fade", "Edges", options=WINDOWS,
          help="fade: scrolling letters are full strength where the zigzag is tall enough for all 5 rows and "
               "only a soft preview on the low, wide flanks (easiest to read); full: the same everywhere"),
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
    Param("hold", "number", 0, "Word by word (s)", 0, 10, 0.25,
          help="above 0: show the message one word at a time, still and centred, each for this "
               "long (easier to read from the street); 0 scrolls the whole message"),
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
    pitch = D["pitch"]
    cw = P["size"] * pitch                              # one font column, in house units
    h = pitch / (2 * cw)                                # half a bulb's footprint, in columns
    speed = float(P.get("speed", 0.0))
    sign = 0 if abs(speed) < 1e-6 else (1 if speed > 0 else -1)
    tr = travel(t, P) * pitch                           # house units scrolled (positive = leftwards)

    # ---- message (live sources refresh every frame; the font layout is cached by string)
    msg, extra = build_message(P, _now())
    hold = float(P.get("hold", 0) or 0)
    words = msg.split() if hold > 0 else []
    if words:                                           # word by word: each one still and centred
        k = int(math.floor(clock / hold)) % len(words)
        msg, sign = words[k], 0
        extra = dict(extra, word0=k)
        extra.pop("accent", None)
        extra.pop("dim", None)
    _score_events(state, extra, P, dt)
    if not D["cells"]:                                  # no zigzag to write on (a flat run)
        return _flat_marquee(frame, ctx, P, msg, extra, clock, travel(t, P))
    copies = _update_copies(state, P, D, msg, extra, sign, tr, cw, dt)

    # ---- celebration when our score goes up; a short dip when theirs does
    flash, fcol = state.get("flash", 0.0), state.get("flash_rgb") or P["border_color"]
    strobe = flash * (0.5 + 0.5 * math.cos(2 * math.pi * clock * 3.0)) if flash else 0.0
    bg = P["background"]
    if strobe:
        bg = lerp(bg, scale(fcol, 0.45), strobe)
    for i in ctx.idx:
        frame[i] = bg

    # ---- each text bulb by how much of its footprint lit glyph columns cover
    flow = P["color_mode"] == "flow"
    pal = P["colors"] or [WHITE]
    xl = D["xl"]
    span = max(1.0, D["xr"] - xl)
    br = P["brightness"] * (1.0 - 0.6 * strobe) * (1.0 - 0.45 * state.get("dip", 0.0))
    glint = P["sparkle"] * 0.12
    seed = P["seed"]
    gt = clock * 3.0
    fade = P["window"] == "fade" and sign != 0        # a still word is shown whole
    inv = 1.0 / (2 * h)
    draw = []                                           # (x of column 0, width, weight, bitmap, colours)
    for c in copies:
        if c["w"] > 0 and c["bm"].width:
            draw.append((_copy_x(c, D, tr, cw), c["bm"].width, c["w"], c["bm"],
                         _letter_colours(c["bm"], P, clock, c["extra"])))
    if draw:
        for (i, x, gr), out in zip(D["cells"], D["out"]):
            r = g = b = cov = 0.0
            for x0, W, w, bm, lcols in draw:
                q = (x - x0) / cw
                lo, hi = q - h, q + h
                if hi <= 0 or lo >= W:
                    continue
                cols_, letters = bm.cols, bm.letter
                c = max(0, math.floor(lo))
                while c < hi and c < W:
                    if cols_[c] >> gr & 1:
                        ov = (min(hi, c + 1) - max(lo, c)) * w
                        col = lcols[letters[c]] if letters[c] >= 0 else None
                        if flow or col is None:
                            col = palette(pal, (x - xl) / span * 0.6 - clock * 0.08)
                        r += col[0] * ov
                        g += col[1] * ov
                        b += col[2] * ov
                        cov += ov
                    c += 1
            if cov <= 1e-6:
                continue
            col = (r / cov, g / cov, b / cov)
            cov = min(1.0, cov * inv)
            if glint:
                ph = gt + hash01(i, seed, 5)
                kb = math.floor(ph)
                if hash01(i, kb, seed) < glint:
                    col = lerp(col, WHITE, 0.75 * (1.0 - (ph - kb)) ** 2)
            k = br
            if fade and out > cw:
                k *= max(FLANK, 1.0 - (1.0 - FLANK) * (out - cw) / 2.0)
            frame[i] = lerp(bg, scale(col, k), cov * cov * (3 - 2 * cov))

    # ---- the top row: border
    mode = P["border"]
    if D["border"] and (mode != "off" or flash):
        bc = P["border_color"]
        for i, x in D["border"]:
            if mode == "glow":
                v = 0.35 + 0.08 * math.sin(2 * math.pi * (clock * 0.25 + x * 0.05))
            elif mode == "marquee":
                # dots every 3 bulbs marching with the text
                u = (x + (0.0 if sign == 0 else tr)) / pitch / 3.0
                f = u - math.floor(u)
                v = 0.12 + 0.88 * clamp(1.0 - abs(f - 0.5) * 3.0)
            elif mode == "twinkle":
                rate = 0.25 + 0.35 * hash01(i, seed, 11)
                s = 0.5 + 0.5 * math.sin(2 * math.pi * (clock * rate + hash01(i, seed, 12)))
                v = 0.1 + 0.9 * s ** 4
            else:
                v = 0.0
            c = scale(bc, v)
            if flash:                                   # the top row strobes in the team colour
                c = lerp(c, scale(fcol, 0.25 + 0.75 * (strobe / flash)), min(1.0, flash * 2.0))
            frame[i] = c
    return frame


def _flat_marquee(frame, ctx, P, msg, extra, clock, tr):
    """A target with no rows to write letters on (a straight run): the message goes by
    as a one-row marquee instead, each font column a bulb lit in its letter's colour
    (brighter where the letter is denser), so the layer still shows something."""
    bm = font.layout(msg)
    bg = P["background"]
    px = ctx.layout.pixels
    order = sorted(ctx.idx, key=lambda i: (px[i].x, px[i].i))
    if not bm.width:
        for i in order:
            frame[i] = bg
        return frame
    lcols = _letter_colours(bm, P, clock, extra)
    pal = P["colors"] or [WHITE]
    cw = max(0.5, float(P.get("size", 1.2) or 1.2))
    period = bm.width + max(1, int(P.get("gap", 10) or 0))
    br = P["brightness"]
    for k, i in enumerate(order):
        c = int(math.floor((k + tr) / cw)) % period
        bits = bm.cols[c] if c < bm.width else 0
        if not bits:
            frame[i] = bg
            continue
        col = lcols[bm.letter[c]] if bm.letter[c] >= 0 else None
        if col is None or P["color_mode"] == "flow":
            col = palette(pal, k / max(1, len(order)) * 0.6 - clock * 0.08)
        dens = bin(bits).count("1") / font.ROWS
        frame[i] = lerp(bg, scale(col, br), 0.45 + 0.55 * dens)
    return frame


def _score_events(state: dict, extra: dict, P: dict, dt: float):
    """Flash when our score goes up (the controller already holds scores back to match the
    TV); a brief dip of the letters when the other side scores."""
    sk = extra.get("score_key")
    if sk is not None:
        prev = state.get("score_key")
        if prev is not None and prev[0] == sk[0]:
            try:
                if sk[1] > prev[1]:
                    state["flash"] = 1.0
                    state["flash_rgb"] = (extra.get("accent") or (0, 0, None))[2] or P["border_color"]
                elif sk[2] > prev[2]:
                    state["dip"] = 1.0
            except TypeError:                           # malformed scores: no celebration
                pass
        state["score_key"] = sk
    state["flash"] = max(0.0, state.get("flash", 0.0) - dt / 4.0)
    state["dip"] = max(0.0, state.get("dip", 0.0) - dt / 2.5)


def _copy_x(c: dict, D: dict, tr: float, cw: float) -> float:
    """Left edge (house units) of a copy of the message right now."""
    if c["centre"]:
        return D["xc"] - c["bm"].width * cw / 2
    return c["p"] - tr if c["move"] else c["p"]


def _new_copy(msg: str, extra: dict, p: float = 0.0, move: bool = True, centre: bool = False,
              w: float = 1.0) -> dict:
    return {"msg": msg, "extra": extra, "bm": font.layout(msg), "p": p, "move": move,
            "centre": centre, "w": w, "dying": False}


def _update_copies(state, P, D, msg, extra, sign, tr, cw, dt) -> list:
    """The copies of the message on (or about to enter) the display.

    Each copy keeps the message it was given when it entered, so text never changes
    mid-word: a new time or score appears with the next pass. Copies live in house
    units, so the text simply turns round when the speed changes sign. Editing the
    message (or its source) fades the old text out while the new one enters; a still,
    centred message cross-fades when it changes."""
    copies = state.get("copies")
    pkey = (P.get("text"), P.get("source"), P.get("date"), P.get("label"), P.get("clock"),
            bool(P.get("score_detail")))
    fresh = copies is None
    if fresh:
        copies = []
    elif state.get("pkey") != pkey:                     # edited: fade the old text out
        for c in copies:
            c["dying"] = True
    state["pkey"] = pkey
    was = state.get("sign")
    state["sign"] = sign
    step = dt / FADE_S
    live = [c for c in copies if not c["dying"]]

    if sign == 0:
        if was is not None and was != 0:                   # just stopped: fade the scroll out
            for c in live:
                c["dying"] = True
            live = []
        cur = live[0] if live else None
        if cur is not None and cur["msg"] != msg:
            cur["dying"] = True
            cur = None
        if cur is None:
            cur = _new_copy(msg, extra, move=False, centre=True, w=1.0 if fresh else 0.0)
            copies.append(cur)
        cur["extra"] = extra
    else:
        if was == 0:                                    # just started: the centred text scrolls off
            for c in live:
                c["p"] = _copy_x(c, D, tr, cw) + tr
                c["move"], c["centre"] = True, False
        rb = D["xr"] + D["pitch"] / 2                   # outer edges of the end bulbs
        lb = D["xl"] - D["pitch"] / 2
        gap = max(0, P["gap"]) * cw
        # drop copies that have left the window (either way)
        copies = [c for c in copies if not c["move"] or
                  (lb - cw < _copy_x(c, D, tr, cw) + c["bm"].width * cw and _copy_x(c, D, tr, cw) < rb + cw)]
        live = sorted((c for c in copies if not c["dying"]), key=lambda c: _copy_x(c, D, tr, cw))
        bm = font.layout(msg)
        wu = max(bm.width * cw, cw)                     # spacing never collapses for empty text
        for _ in range(64):
            if sign > 0:                                # entering on the right, moving left
                if live:
                    last = live[-1]
                    nxt = _copy_x(last, D, tr, cw) + max(last["bm"].width * cw, cw) + gap
                    if nxt >= rb:
                        break
                else:
                    nxt = rb
                c = _new_copy(msg, extra, p=nxt + tr)
                live.append(c)
            else:                                       # entering on the left, moving right
                if live:
                    end = _copy_x(live[0], D, tr, cw) - gap
                    if end <= lb:
                        break
                else:
                    end = lb
                c = _new_copy(msg, extra, p=end - wu + tr)
                live.insert(0, c)
            copies.append(c)
        else:                                           # a huge jump in travel: start over
            copies = [c for c in copies if c["dying"]]
            copies.append(_new_copy(msg, extra, p=(rb if sign > 0 else lb - wu) + tr))

    for c in copies:
        c["w"] = max(0.0, c["w"] - step) if c["dying"] else min(1.0, c["w"] + step)
    copies = [c for c in copies if not (c["dying"] and c["w"] <= 0)][-80:]
    state["copies"] = copies
    return copies


def _target_key(ctx) -> tuple:
    """A cache key for the layer's target (ctx has the resolved indices, not the name)."""
    idx = ctx.idx
    return ("idx", len(idx), idx[0] if idx else -1, idx[-1] if idx else -1, hash(tuple(idx)))
