"""Text on the Peak in the hex-native font (goveelights/hexfont.py): type a phrase and
choose one big letter at a time ('single') or a marquee.

    single   each letter fills the Peak's six rows, strokes two bulbs thick, held for a
             moment (hold), then a short dark beat (gap) so double letters read as two;
             a space is a longer dark beat (word_gap), the end of the message a longer one
             still (pause). Letters come in with a fade, a cut, a wipe up the rows or a
             white flash.
    marquee  the phrase steps across the Peak a bulb at a time (each step a quick
             cross-fade), letters as tall as the Peak and one bulb thick. A letter is lit
             only once (nearly) all of it is on the Peak: the sloped sides never show a
             letter cut in half, and nothing out on the low flanks reads as stray dots.

The rest of the target keeps time with the text. The Eaves (bulbs that point out to the
street) are the rhythm: 'pulse' flashes them in each letter's colour as it arrives,
'chase' steps a chase with every letter (or every marquee step), 'echo' holds the colour
of the letter on show. The Accent/porch run (pointing down, washing the siding) frames
it: 'follow' washes the wall in the letter's colour, 'fixed' in one colour of your own.
With the Tip on the Peak, letters stay on the Peak and the Tip is a crown over them.

The message can be your own text or, as with 'Scrolling text', the clock, the live game
score or days to go. A message only changes between passes, never mid-word. A target with
no zigzag to write on falls back to 'Scrolling text' (a one-row marquee on a straight
run), so the layer still shows the phrase.
"""
from __future__ import annotations

import math

from .. import hexfont
from ..color import BLACK, hsv, lerp, scale
from ..effects import EFFECTS, Param, frame_effect
from . import scrolltext

GROUP = "Text, pictures & numbers"
MODES = ["single", "marquee"]
COLOR_MODES = ["letters", "words", "solid", "rainbow"]
TRANSITIONS = ["fade", "cut", "wipe", "flash"]
EAVES = ["pulse", "chase", "echo", "off"]
WALLS = ["follow", "fixed", "off"]
REPEATS = ["loop", "once"]
SOURCES = scrolltext.SOURCES
WHITE = (1.0, 1.0, 1.0)
HEART_RGB = (1.0, 0.06, 0.25)
CUT = 0.35                 # marquee: a letter the sloped sides cut by a bulb or two, clearly dimmer
RISE, FALL, WIPE = 0.08, 0.14, 0.3
WALL_WORDS = ("accent", "porch", "garage", "door", "window", "wall wash")


def _sm(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def colours(text: str, P: dict, T: float, extra: dict | None = None) -> list:
    """Per glyph of the normalised text its colour (None for a space). extra may carry
    {"accent": (start, end, colour)} and {"dim": (start, end)} for a game score."""
    cols, mode = P["colors"] or [WHITE], P["color_mode"]
    extra = extra or {}
    accent, dim = extra.get("accent"), extra.get("dim")
    out, n, w = [], 0, int(extra.get("word0", 0))
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
        if accent and accent[2] is not None and accent[0] <= k < accent[1]:
            c = accent[2]
        if dim and dim[0] <= k < dim[1]:
            c = scrolltext.SEP_RGB
        out.append(c)
        n += 1
    return out


def letter_timeline(text: str, hold: float, end: float, gap: float = 0.22,
                    word_gap: float | None = None) -> tuple[list, float]:
    """[(start, length, glyph number)] for the letters, and the cycle length: each letter
    `hold` seconds, `gap` dark between letters, `word_gap` dark between words, `end` dark
    after the last letter."""
    word_gap = hold * 0.6 + gap if word_gap is None else word_gap
    slots, t = [], 0.0
    pending = 0.0                                   # dark still owed before the next letter
    for k, ch in enumerate(text):
        if ch == " ":
            if slots:
                pending = max(pending, word_gap)
            continue
        t += pending
        slots.append((t, hold, k))
        t += hold
        pending = gap
    return slots, t + end


def _wall_runs(layout) -> set:
    """Names of runs that wash a wall (the Accent/porch run under the eaves)."""
    out = set()
    for r in getattr(layout, "runs", []):
        name = str(r.name).lower()
        if r.role in ("porch", "accent") or any(w in name for w in WALL_WORDS):
            out.add(r.name)
    return out


def _geo(ctx, st):
    key = (id(ctx.layout), id(ctx.layout.pixels), len(ctx.idx), ctx.idx[0] if ctx.idx else -1,
           ctx.idx[-1] if ctx.idx else -1)
    g = st.get("geo")
    if g is None or st.get("geo_key") != key:
        lat = hexfont.lattice(ctx.layout, ctx.idx)
        if lat.peak_rows < 5:                       # no zigzag to write on
            lat = hexfont.Lattice()
        on = {i for i, _, _ in lat.cells}
        px = ctx.layout.pixels
        rest = sorted((i for i in ctx.idx if i not in on), key=lambda i: (px[i].run, px[i].ri))
        walls = _wall_runs(ctx.layout)
        eaves = [i for i in rest if px[i].run not in walls]
        wall = [i for i in rest if px[i].run in walls]
        tip_rows = lat.rows[lat.peak_rows:]
        crown = [i for r in tip_rows[1:] for i, _ in r]
        g = st["geo"] = {"lat": lat, "eaves": eaves, "wall": wall, "crown": crown}
        st["geo_key"] = key
    return g


def _message(P: dict) -> tuple[str, dict]:
    """The phrase (normalised for the font) and the score extras."""
    src, raw = P.get("source", "custom"), str(P.get("text", ""))
    if src == "custom" and "{" not in raw:
        return hexfont.normalise(raw), {}
    msg, extra = scrolltext.build_message(P, scrolltext._now())
    return hexfont.normalise(msg), extra


def _fallback(t, ctx, P, st):
    """No zigzag in the target: 'Scrolling text' with the same message and colours."""
    Q = EFFECTS["text"].resolve({})
    for k in ("text", "source", "colors", "background", "brightness", "date", "label", "clock",
              "score_detail"):
        if k in P:
            Q[k] = P[k]
    Q["color_mode"] = P["color_mode"] if P["color_mode"] in scrolltext.COLOR_MODES else "letters"
    Q["speed"] = P["speed"]
    Q["_travel"] = P.get("_travel", P["speed"] * t)
    Q["sparkle"] = 0.0
    Q["border"] = "off"
    return scrolltext.scroll_text(t, ctx, Q, st.setdefault("fallback", {}))


@frame_effect("peak_text", "Text on the Peak", [
    Param("text", "text", "MERRY CHRISTMAS", "Message",
          help="A-Z, 0-9, ! ? - + . , ' : & # / and <3 for a heart; lower case is fine. "
               "May include {time}, {days}, {score} or {label}"),
    Param("mode", "choice", "single", "Show it as", options=MODES,
          help="single: one big letter at a time, filling the Peak (easiest to read from the street); "
               "marquee: the phrase steps across the Peak"),
    Param("colors", "colors", ["#ff1a1a", "#00d040"], "Letter colours"),
    Param("color_mode", "choice", "letters", "Colouring", options=COLOR_MODES,
          help="letters: each letter the next colour (neighbours stay apart); words: each word; "
               "solid: all the first colour; rainbow"),
    Param("background", "color", "#000000", help="the Peak behind the letters (keep it dark)"),
    Param("hold", "number", 0.7, "Letter time (s)", 0.3, 3, 0.05, help="single: how long each letter shows"),
    Param("gap", "number", 0.22, "Between letters (s)", 0.05, 1.5, 0.01,
          help="single: the dark beat between letters (so double letters read as two)"),
    Param("word_gap", "number", 0.65, "Between words (s)", 0.1, 3, 0.05,
          help="single: the longer dark beat between words"),
    Param("transition", "choice", "fade", "Letters come in", options=TRANSITIONS,
          help="single: fade in and out, cut, wipe up the rows, or a white flash"),
    Param("speed", "number", 4.0, "Marquee speed", 1, 12, 0.25, help="marquee: bulbs per second"),
    Param("pause", "number", 1.6, "Pause at the end (s)", 0, 10, 0.25,
          help="dark before the message starts again"),
    Param("repeat", "choice", "loop", "Repeat", options=REPEATS, help="loop the message, or show it once"),
    Param("brightness", "number", 1.0, "Brightness", 0.1, 1, 0.05),
    Param("eaves", "choice", "pulse", "Eaves", options=EAVES,
          help="the eaves (pointing out to the street) keep time: pulse flashes each letter's colour as it "
               "comes; chase steps with the letters; echo holds the letter's colour; off leaves them dark"),
    Param("eaves_level", "number", 0.6, "Eaves brightness", 0, 1, 0.05),
    Param("wall", "choice", "follow", "Porch wall wash", options=WALLS,
          help="the porch/accent run washing the siding: follow the letter's colour, a fixed colour, or off"),
    Param("wall_color", "color", "#ffb000", "Wall colour", help="for wall wash 'fixed'"),
    Param("wall_level", "number", 0.35, "Wall brightness", 0, 1, 0.05),
    Param("border_color", "color", "#ffb000", "Tip chase colour", help="marquee: the chase on the Tip"),
    Param("source", "choice", "custom", "Source", options=SOURCES,
          help="custom: your message; clock: the time; score: the live game score (your message when no game "
               "is on); days: days to go until the date below"),
    Param("clock", "choice", "12h", "Clock style", options=scrolltext.CLOCKS),
    Param("date", "text", "12-25", "Count to (MM-DD)", help="for source 'days'"),
    Param("label", "text", "XMAS", "Counting to", help="for source 'days': 24 DAYS TO <this>"),
    Param("score_detail", "bool", False, "Show game clock", help="for source 'score': add the quarter/time"),
], "Type a phrase and show it on the Peak in a font drawn for its honeycomb of bulbs: one big "
   "letter at a time (the easiest to read from the street), or a marquee across the gable. "
   "The eaves keep time and the porch washes the wall in the letter's colour.", group=GROUP)
def peak_text(t, ctx, P, st):
    L = ctx.layout
    n = len(L.pixels)
    frame = [BLACK] * n
    if not ctx.idx:
        return frame
    g = _geo(ctx, st)
    lat = g["lat"]
    if not lat.cells:
        return _fallback(t, ctx, P, st)
    if "start" not in st or ctx.clock < st["start"] - 1e-6:     # first frame, or the clock went back
        for k in ("pkey", "msg", "last", "wash", "mq_letter", "flash", "dip", "score_key"):
            st.pop(k, None)
        st["start"] = ctx.clock
    T = ctx.clock - st["start"]
    dt = min(0.1, max(0.0, ctx.dt or 0.0))
    mode = P["mode"]

    # the message: fixed for a whole pass, so a new time or score never changes mid-word
    pkey = (P["text"], P["source"], P["date"], P["label"], P["clock"], P["score_detail"], mode,
            P["hold"], P["gap"], P["word_gap"], P["pause"], P["speed"])
    if st.get("pkey") != pkey:
        st["pkey"], st["t0"], st["pass"] = pkey, T, 0
        st["msg"] = None
    if st["msg"] is None or T - st["t0"] >= st["cyc"]:
        if st["msg"] is not None:
            st["t0"] += st["cyc"] if T - st["t0"] < 2 * st["cyc"] else (T - st["t0"])
            st["pass"] += 1
        msg, extra = _message(P)
        msg = msg or " "
        st["msg"], st["extra"] = msg, extra
        if mode == "marquee":
            st["cyc"] = _marquee_period(lat, msg, P) / P["speed"]
        else:
            st["cyc"] = max(0.5, letter_timeline(msg, P["hold"], P["pause"], P["gap"], P["word_gap"])[1])
    # a score change celebrates at once (the trim strobes), even though the score on the
    # Peak waits for the next pass
    live = scrolltext.build_message(P, scrolltext._now())[1] if P["source"] == "score" else {}
    scrolltext._score_events(st, live, P, dt)
    msg, extra = st["msg"], st["extra"]
    u = T - st["t0"]
    done = P["repeat"] == "once" and st["pass"] > 0
    cols = colours(msg, P, T, extra)
    lvl = P["brightness"]
    bg = P["background"]
    for i, _, _ in lat.cells:
        frame[i] = bg
    lit = [c for c in cols if c] or [WHITE]
    cur, pulse, step, on = None, 0.0, 0, False
    if done:
        pass
    elif mode == "marquee":
        cur, pulse, step = _marquee(frame, lat, msg, cols, u, P, lvl, bg, st)
        on = cur is not None
    else:
        slots, _ = letter_timeline(msg, P["hold"], P["pause"], P["gap"], P["word_gap"])
        for nslot, (s0, h, k) in enumerate(slots):
            if s0 <= u < s0 + h:
                c = cols[k] or WHITE
                _letter(frame, lat, msg[k], c, u - s0, h, P, lvl, bg)
                a = _sm((u - s0) / RISE) * (1 - _sm((u - (s0 + h - FALL)) / FALL))
                cur, pulse, step, on = c, math.exp(-(u - s0) / 0.3), nslot, True
                # the Tip: a small crown in the letter's colour that flashes as each letter
                # comes; its lowest row stays dark so the crown never joins the letter's top
                for i in g["crown"]:
                    frame[i] = scale(c, lvl * a * (0.3 + 0.7 * pulse))
                break
        else:
            step = sum(1 for s0, _, _ in slots if s0 <= u)
    if cur is not None:
        st["last"] = cur
    _trim(frame, g, P, lit, cur, st.get("last") or lit[0], pulse, step, on, done, st, T)
    return frame


def _letter(frame, lat, ch, c, v, h, P, lvl, bg):
    """One big letter, v seconds into its h-second slot, with the chosen transition."""
    tr = P["transition"]
    cov = hexfont.render_big(lat, ch, lat.peak_rows)
    out = 1 - _sm((v - (h - FALL)) / FALL)
    col = scale(c, lvl)
    if tr == "cut":
        a, rows = 1.0, None
    elif tr == "wipe":
        a, rows = out, (lat.peak_rows + 1) * min(1.0, v / WIPE)
    elif tr == "flash":
        a, rows = out, None
        if v < 0.1:
            col = lerp(scale(WHITE, lvl), col, v / 0.1)
    else:
        a, rows = _sm(v / RISE) * out, None
    if rows is not None:
        rowof = {i: r for i, _, r in lat.cells}
        for i, x in cov.items():
            if rowof.get(i, 0) < rows:
                frame[i] = lerp(bg, col, x * a)
        return
    for i, x in cov.items():
        frame[i] = lerp(bg, col, x * a)


def _trim(frame, g, P, lit, cur, last, pulse, step, on, done, st, T):
    """The Eaves keep the rhythm; the Accent/porch washes the wall."""
    fl = st.get("flash", 0.0)                         # our side scored: strobe the team colour
    fcol = st.get("flash_rgb") or P["border_color"]
    strobe = fl * (0.5 + 0.5 * math.cos(2 * math.pi * T * 3.0)) if fl else 0.0
    el, mode = P["eaves_level"], P["eaves"]
    if mode != "off" and el > 0 and not done:
        eaves = g["eaves"]
        for j, i in enumerate(eaves):
            if mode == "chase":
                k = (j + step) % 4 == 0
                c = lit[((j + step) // 4) % len(lit)]
                frame[i] = scale(c, el * (1.0 if k else 0.15))
            elif mode == "echo":
                frame[i] = scale(last, el * (0.85 if on else 0.45))
            else:                                     # pulse
                c = cur or last
                frame[i] = scale(c, el * ((0.2 + 0.8 * pulse) if on else 0.12))
    wl, wmode = P["wall_level"], P["wall"]
    if wmode != "off" and wl > 0:
        c = P["wall_color"] if wmode == "fixed" else last
        # follow: ease the wash toward the letter's colour, so the wall never flickers
        w = st.get("wash") or c
        w = lerp(w, c, 0.12)
        st["wash"] = w
        for i in g["wall"]:
            frame[i] = scale(w, wl)
    if strobe:
        for i in g["eaves"] + g["wall"]:
            frame[i] = lerp(frame[i], fcol, strobe)


def _marquee_period(lat, text, P) -> int:
    lt, rt = lat.width_at(lat.peak_rows - 1)
    W = hexfont.line_width(text)
    return int(math.ceil(W + (rt - lt) + 4 + P["pause"] * P["speed"]))


def _marquee(frame, lat, text, cols, u, P, lvl, bg, st):
    """Draw the marquee u seconds into its pass. Returns (colour of the letter now fully
    on the Peak or None, how fresh it is, step number)."""
    # the text comes in just right of where the Peak is full height (its top row): out on
    # the low flanks a letter would be cut by the slopes, so it stays dark there
    lt, rt = lat.width_at(lat.peak_rows - 1)
    s = u * P["speed"]
    k = int(math.floor(s))
    f = _sm((s - k - 0.72) / 0.28)                  # each step: hold, then a quick cross-fade
    gl = hexfont.line_layout(text)
    fit = st.setdefault("fit", {})                  # (glyph, left X) -> bulbs off the Peak

    def strength(ch, gx):
        key = (ch, gx)
        if key not in fit:
            fit[key] = hexfont.off_peak(lat, ch, gx)
        m = fit[key]
        return 1.0 if m == 0 else CUT if m <= 2 else 0.0

    full = []

    def draw(step):
        x = math.floor(rt) + 2 - step
        out = {}
        for q, (ch, gx, _) in enumerate(gl):
            if ch != " " and strength(ch, x + gx) == 1.0:
                full.append((step, q))
        for i, (c, q) in hexfont.render_line(lat, text, x).items():
            ch, gx, _ = gl[q]
            v = c * strength(ch, x + gx)
            if v > 0:
                out[i] = (v, q)
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
    now = [q for s_, q in full if s_ == k]
    if not now:
        st["mq_letter"] = None
        return None, 0.0, k
    q = now[len(now) // 2]
    if st.get("mq_letter") != q:                    # a new letter has just come fully on
        st["mq_letter"], st["mq_since"] = q, u
    return cols[q] or WHITE, math.exp(-(u - st.get("mq_since", u)) / 0.35), k
