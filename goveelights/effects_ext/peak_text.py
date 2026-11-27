"""Text on the Peak in the hex-native font (goveelights/hexfont.py): type a phrase and
choose one big letter at a time or a marquee.

    letter   each letter fills the Peak's six rows, strokes two bulbs thick, held for a
             moment, then a short dark beat (so double letters read as two); a space is
             a longer dark beat, the end of the message a longer one still
    marquee  the phrase steps across the Peak a bulb at a time (each step a quick
             cross-fade), letters as tall as the Peak and one bulb thick; a letter is at
             full strength only while all of it is on the Peak, and a dim preview while
             the sloped sides cut it, so a letter is never read half cut off

Bulbs in the target that are not part of the zigzag (the Eaves, the Accent/porch) keep
time with the text: 'pulse' flashes them in each new letter's colour (letter mode) or
runs a chase that steps with the marquee; 'glow' is a steady wash of the colours. With
the Tip on the Peak, letters stay on the Peak and the Tip is a crown over them.
"""
from __future__ import annotations

import math

from .. import hexfont
from ..color import BLACK, hsv, lerp, scale
from ..effects import Param, frame_effect

GROUP = "Text, pictures & numbers"
MODES = ["letter", "marquee"]
COLOR_MODES = ["letters", "words", "solid", "rainbow"]
TRIMS = ["pulse", "glow", "off"]
WHITE = (1.0, 1.0, 1.0)
HEART_RGB = (1.0, 0.06, 0.25)
FLANK = 0.22               # marquee: strength of a letter out on the sloped flanks
RISE, FALL, BEAT = 0.08, 0.14, 0.22


def _sm(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def colours(text: str, P: dict, T: float) -> list:
    """Per glyph of the normalised text its colour (None for a space)."""
    cols, mode = P["colors"] or [WHITE], P["color_mode"]
    out, n, w = [], 0, 0
    for k, ch in enumerate(text):
        if ch == " ":
            if k and text[k - 1] != " ":
                w += 1
            out.append(None)
            continue
        if ch == hexfont.HEART and mode != "solid":
            c = HEART_RGB
        elif mode == "solid":
            c = cols[0]
        elif mode == "words":
            c = cols[w % len(cols)]
        elif mode == "rainbow":
            c = hsv(n * 0.11 - T * 0.03, 1.0, 1.0)
        else:
            c = cols[n % len(cols)]
        out.append(c)
        n += 1
    return out


def letter_timeline(text: str, hold: float, end: float) -> tuple[list, float]:
    """[(start, length, glyph number)] for the letters, and the cycle length."""
    slots, t = [], 0.0
    for k, ch in enumerate(text):
        if ch == " ":
            t += hold * 0.6
            continue
        slots.append((t, hold, k))
        t += hold + BEAT
    return slots, t + end


def _geo(ctx, st):
    key = (id(ctx.layout), id(ctx.layout.pixels), len(ctx.idx), ctx.idx[0] if ctx.idx else -1)
    g = st.get("geo")
    if g is None or st.get("geo_key") != key:
        lat = hexfont.lattice(ctx.layout, ctx.idx)
        if lat.peak_rows < 5:                       # no zigzag to write on: everything is trim
            lat = hexfont.Lattice()
        on = {i for i, _, _ in lat.cells}
        px = ctx.layout.pixels
        rest = sorted((i for i in ctx.idx if i not in on), key=lambda i: (px[i].run, px[i].ri))
        top = [i for i, _ in lat.rows[lat.peak_rows - 1]] if lat.peak_rows else []
        tip_rows = lat.rows[lat.peak_rows:]
        crown = [i for r in tip_rows[1:] for i, _ in r]
        g = st["geo"] = {"lat": lat, "rest": rest, "top": top, "crown": crown}
        st["geo_key"] = key
    return g


@frame_effect("peak_text", "Text on the Peak", [
    Param("text", "text", "MERRY CHRISTMAS", "Message",
          help="A-Z, 0-9, ! ? - + . , ' : & # / and <3 for a heart"),
    Param("mode", "choice", "letter", "Show it as", options=MODES,
          help="letter: one big letter at a time, filling the Peak (easiest to read from the street); "
               "marquee: the phrase steps across the Peak"),
    Param("colors", "colors", ["#ff1a1a", "#00d040"], "Letter colours"),
    Param("color_mode", "choice", "letters", "Colouring", options=COLOR_MODES,
          help="letters: each letter the next colour; words: each word; solid: all the first colour; rainbow"),
    Param("background", "color", "#000000", help="the Peak behind the letters (keep it dark)"),
    Param("hold", "number", 1.1, "Letter time (s)", 0.4, 4, 0.05, help="letter mode: how long each letter shows"),
    Param("speed", "number", 4.0, "Marquee speed", 1, 12, 0.25, help="marquee: bulbs per second"),
    Param("pause", "number", 2.0, "Pause at the end (s)", 0, 10, 0.25,
          help="dark before the message starts again"),
    Param("brightness", "number", 1.0, "Brightness", 0.1, 1, 0.05),
    Param("trim", "choice", "pulse", "Eaves and porch", options=TRIMS,
          help="the rest of the target: pulse flashes with each letter (or chases with the marquee); "
               "glow is a steady wash of the letter colours; off leaves it dark"),
    Param("trim_level", "number", 0.6, "Trim brightness", 0, 1, 0.05),
    Param("border_color", "color", "#ffb000", "Marquee top row"),
], "Type a phrase and show it on the Peak in a font drawn for its honeycomb of bulbs: one big "
   "letter at a time, or a marquee that steps across the gable. The eaves and porch keep time "
   "with it. Target the Peak, or the whole house for the trim.", group=GROUP)
def peak_text(t, ctx, P, st):
    L = ctx.layout
    n = len(L.pixels)
    frame = [BLACK] * n
    if not ctx.idx:
        return frame
    if "start" not in st or ctx.clock < st["start"] - 1e-6:
        st["start"] = ctx.clock
    T = ctx.clock - st["start"]
    g = _geo(ctx, st)
    lat = g["lat"]
    text = hexfont.normalise(P["text"]) or " "
    cols = colours(text, P, T)
    lvl = P["brightness"]
    bg = P["background"]
    tl = P["trim_level"]
    lit = [c for c in cols if c] or [WHITE]
    pulse = 0.0                                     # trim: how fresh the latest letter is
    cur = lit[0]
    step = 0
    if lat.cells:
        for i, _, _ in lat.cells:
            frame[i] = bg
    if P["mode"] == "marquee" and lat.cells:
        step = _marquee(frame, lat, text, cols, T, P, lvl, bg, g)
    elif lat.cells:
        slots, cyc = letter_timeline(text, P["hold"], P["pause"])
        u = T % cyc if cyc > 0 else 0.0
        for s0, h, k in slots:
            if s0 <= u < s0 + h:
                a = _sm((u - s0) / RISE) * (1 - _sm((u - (s0 + h - FALL)) / FALL))
                ch, c = text[k], cols[k] or WHITE
                for i, v in hexfont.render_big(lat, ch, lat.peak_rows).items():
                    frame[i] = lerp(bg, scale(c, lvl), v * a)
                cur, pulse = c, math.exp(-(u - s0) / 0.35)
                # the Tip: a small crown in the letter's colour that flashes as each letter
                # comes; its lowest row stays dark so the crown never joins the letter's top
                for i in g["crown"]:
                    frame[i] = scale(c, lvl * a * (0.3 + 0.7 * pulse))
                break
    # the trim: everything in the target off the zigzag
    if P["trim"] != "off" and tl > 0:
        rest = g["rest"]
        for j, i in enumerate(rest):
            if P["trim"] == "glow":
                c = lit[(j // 6) % len(lit)]
                frame[i] = scale(c, tl * 0.6)
            elif P["mode"] == "marquee":
                on = (j + step) % 4 == 0
                c = lit[((j + step) // 4) % len(lit)]
                frame[i] = scale(c, tl * (1.0 if on else 0.18))
            else:
                frame[i] = scale(cur, tl * (0.22 + 0.78 * pulse))
    return frame


def _marquee(frame, lat, text, cols, T, P, lvl, bg, g) -> int:
    # the text comes in just right of where the Peak is full height (its top row): out on
    # the low flanks a letter is only ever a dim preview, so there is nothing to wait for
    lt, rt = lat.width_at(lat.peak_rows - 1)
    W = hexfont.line_width(text)
    period = int(math.ceil(W + (rt - lt) + 4 + P["pause"] * P["speed"]))
    s = T * P["speed"]
    k = int(math.floor(s))
    f = _sm((s - k - 0.72) / 0.28)                  # each step: hold, then a quick cross-fade
    gl = hexfont.line_layout(text)
    fit = g.setdefault("fit", {})                   # (glyph, left X) -> bulbs off the Peak

    def strength(ch, gx):
        key = (ch, gx)
        if key not in fit:
            fit[key] = hexfont.off_peak(lat, ch, gx)
        m = fit[key]
        return 1.0 if m == 0 else 0.7 if m <= 2 else FLANK

    def draw(step):
        x = math.floor(rt) + 2 - (step % period)
        out = {}
        for i, (c, q) in hexfont.render_line(lat, text, x).items():
            ch, gx, _ = gl[q]
            out[i] = (c * strength(ch, x + gx), q)
        return out

    A, B = draw(k), draw(k + 1)
    for i in set(A) | set(B):
        ca, qa = A.get(i, (0.0, -1))
        cb, qb = B.get(i, (0.0, -1))
        q = qa if qa >= 0 else qb
        col = cols[q] or WHITE
        frame[i] = lerp(bg, scale(col, lvl), ca * (1 - f) + cb * f)
    # the Tip: a chase stepping with the text
    bc = P["border_color"]
    for j, i in enumerate(lat.tip):
        on = (j + k) % 3 == 0
        frame[i] = scale(bc, lvl * (0.75 if on else 0.08))
    return k
