"""Effects that dance to the music the phone hears (Play -> Listen; see goveelights/audio.py).

    music_rows  the front gable's zigzag as a bass meter: rows light from the bottom up
                with every kick, the whole thing flashes on the beat and changes colour
                with each beat; the rest of the house glows with the loudness and the
                apex flashes white on the beat.
    beat_chase  dots that drift, then jump ahead on every beat (and turn round every few
                beats), colours stepping on each beat, brightness riding the loudness.
    beat (FX)   any layer's brightness follows the beat, the loudness or the bass, with an
                optional white flash on the beat.

Without music the generators keep time with the master tempo (tap tempo), so they look
right in previews and with the phone locked; the FX does nothing without music unless
its "without music" option says to keep the tempo.
"""
from __future__ import annotations

import math

from ..audio import pulse
from ..color import BLACK
from ..effects import Param, coord, frame_effect
from ..fx import fx
from .occasions import field

GROUP = "Music"
PARTY = ["#ff0060", "#ff7a00", "#ffd400", "#00ff7a", "#00b4ff", "#8a2cff"]
MAX_DT = 0.6


def _new_beat(st: dict, m: dict) -> bool:
    """True on the frame a new beat starts. With the layer's beat count (pulse()'s
    ``count``) that is when the count passes the highest one seen, so a grid that glides
    back a little never counts a beat twice. Without it: when the time since the beat
    wraps round (falls back by more than half), not on a small backward correction."""
    age, cnt = m["beat_age"], m.get("count")
    prev = st.get("age")
    st["age"] = age
    if cnt is not None:
        top = st.get("cnt")
        if top is None or cnt < top - 4:        # first frame, or a new grid (scene rebuilt)
            st["cnt"] = cnt
            return False
        if cnt > top:
            st["cnt"] = cnt
            return True
        return False
    st.pop("cnt", None)
    return prev is not None and age < prev - 0.03 and age < prev * 0.5


def _ease(dt: float, tau: float) -> float:
    return 1.0 - math.exp(-dt / max(1e-3, tau)) if dt > 0 else 0.0


def _mix(a, b, f):
    return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f)


# ------------------------------------------------------------ music rows
@frame_effect("music_rows", "Music rows (bass meter)", [
    Param("colors", "colors", PARTY, help="each beat moves on to the next colour"),
    Param("height", "number", 1.0, "Bass height", 0.2, 1.6, 0.05, help="how far up the rows a full kick reaches"),
    Param("decay", "number", 0.3, "Beat flash (s)", 0.05, 1.5, 0.05, help="how long each beat's flash lasts"),
    Param("fall", "number", 0.35, "Fall (s)", 0.05, 2, 0.05, help="how fast the lit rows drop back"),
    Param("floor", "number", 0.08, "Unlit rows", 0, 0.5, 0.01, help="glow of the rows above the bass"),
    Param("rest", "number", 0.7, "Rest of the house", 0, 1, 0.05, help="how strongly the other bulbs follow the music"),
    Param("idle", "choice", "tempo", options=["tempo", "still"],
          help="without music: keep time with the tempo, or hold a quiet glow"),
], "The gable zigzag as a bass meter: rows light from the bottom up on every kick, flash and change "
   "colour on the beat; the rest of the house glows with the loudness.", group=GROUP)
def music_rows(t, ctx, P, st):
    dt = min(MAX_DT, max(0.0, ctx.dt))
    F = field(ctx.layout, ctx.idx)
    rows, n = F["rows"], max(1, F["n"])
    m = pulse(ctx, P["decay"], P["idle"] == "tempo", ctx.beats)
    cols = P["colors"] or [(1.0, 0.2, 0.5)]
    if _new_beat(st, m):
        st["k"] = st.get("k", 0) + 1
        st["from"] = st.get("cur", cols[0])
        st["since"] = 0.0
    k = st.get("k", 0)
    st["since"] = st.get("since", 1.0) + dt
    target = cols[k % len(cols)]
    cur = _mix(st.get("from", target), target, min(1.0, st["since"] / 0.12))     # quick colour change
    st["cur"] = cur
    top = cols[(k + 1) % len(cols)]
    drive = min(1.0, max(m["bass"], m["beat"] * 0.9) * P["height"])
    h = st.get("h", 0.0)
    h += (drive - h) * (_ease(dt, 0.03) if drive > h else _ease(dt, P["fall"]))  # fast attack, slow fall
    st["h"] = h
    frame = [BLACK] * ctx.layout.n
    fl, beat = P["floor"], m["beat"]
    boost = 0.72 + 0.28 * beat
    lit = h * n
    for r, row in enumerate(rows):
        fill = max(0.0, min(1.0, lit - r))
        c = _mix(cur, top, r / max(1, n - 1) * 0.6)                                # a little gradient upwards
        if 0.0 < fill < 1.0 or (fill >= 1.0 and lit - r < 2.0):
            c = _mix(c, (1.0, 1.0, 1.0), 0.35 * beat * fill)                      # the leading row burns white
        v = (fl + (1.0 - fl) * fill) * boost
        c = (c[0] * v, c[1] * v, c[2] * v)
        for i in row:
            frame[i] = c
    rest_k = P["rest"]
    if F["rest"] and rest_k > 0:
        lvl = max(m["level"] * 0.85, beat)
        v = (0.18 + 0.82 * lvl) * rest_k
        rc = (cur[0] * v, cur[1] * v, cur[2] * v)
        for i in F["rest"]:
            frame[i] = rc
        for i in F["apex"]:
            frame[i] = _mix(rc, (1.0, 1.0, 1.0), beat)
    return frame


# ------------------------------------------------------------ beat chase
@frame_effect("beat_chase", "Beat chase", [
    Param("colors", "colors", PARTY, help="dot colours; they step along on every beat"),
    Param("background", "color", "#000000"),
    Param("spacing", "number", 9, "Spacing (bulbs)", 2, 60, 1),
    Param("width", "number", 2.5, "Dot width (bulbs)", 0.5, 20, 0.5),
    Param("jump", "number", 4.5, "Jump per beat (bulbs)", 0, 30, 0.5),
    Param("speed", "number", 2.0, "Drift (bulbs/s)", 0, 30, 0.5),
    Param("flip", "int", 8, "Turn round every (beats)", 0, 64, 1, help="0: never"),
    Param("decay", "number", 0.25, "Beat flash (s)", 0.05, 1.5, 0.05),
    Param("axis", "choice", "strip", options=["strip", "run", "x"]),
    Param("idle", "choice", "tempo", options=["tempo", "still"], help="without music: keep time with the tempo, or just drift"),
], "Dots drift, then jump ahead on every beat and turn round every few beats; colours step on the beat "
   "and the brightness rides the loudness.", group=GROUP)
def beat_chase(t, ctx, P, st):
    dt = min(MAX_DT, max(0.0, ctx.dt))
    m = pulse(ctx, P["decay"], P["idle"] == "tempo", ctx.beats)
    d = st.get("dir", 1.0)
    if _new_beat(st, m):
        st["n"] = st.get("n", 0) + 1
        if P["flip"] and st["n"] % P["flip"] == 0:
            d = -d
        st["target"] = st.get("target", 0.0) + P["jump"] * d
    st["dir"] = d
    st["target"] = st.get("target", 0.0) + P["speed"] * d * dt
    pos = st.get("pos", st["target"])
    pos += (st["target"] - pos) * _ease(dt, 0.05)                      # the jump glides in ~0.1 s
    st["pos"] = pos
    cnt = st.get("n", 0)
    cols, bg = P["colors"] or [(1.0, 1.0, 1.0)], P["background"]
    sp, w = P["spacing"], P["width"]
    beat = m["beat"]
    bright = 0.5 + 0.5 * max(beat, min(1.0, m["level"] * 1.1))
    white = 0.45 * beat
    frame = [BLACK] * ctx.layout.n
    px, ax = ctx.layout.pixels, P["axis"]
    for i in ctx.idx:
        s, _ = coord(px[i], ax)
        x = s - pos
        best, cov = None, 0.0
        for kk in (math.floor((x - 0.5) / sp), math.floor((x + 0.5) / sp)):
            a = kk * sp
            o = max(0.0, min(x + 0.5, a + w) - max(x - 0.5, a))
            if o > cov:
                best, cov = kk, o
        if best is None or cov <= 0:
            frame[i] = bg
            continue
        c = cols[(best + cnt) % len(cols)]
        c = _mix(c, (1.0, 1.0, 1.0), white)
        f = min(1.0, cov / min(1.0, w)) * bright
        frame[i] = _mix(bg, c, f)
    return frame


# ------------------------------------------------------------ beat FX
@fx("beat", "Beat & loudness (music)", [
    Param("follow", "choice", "beat", options=["beat", "level", "bass"],
          help="beat: flash on each beat · level: follow the loudness · bass: follow the low end"),
    Param("amount", "number", 0.6, "Amount", 0, 1, 0.05, help="how dark the layer gets between beats / in quiet parts"),
    Param("decay", "number", 0.25, "Beat flash (s)", 0.05, 1.5, 0.05),
    Param("flash", "number", 0.0, "White flash", 0, 1, 0.05, help="whiten the layer on each beat"),
    Param("idle", "choice", "still", options=["still", "tempo"],
          help="without music: leave the layer alone, or pulse with the tempo"),
], "Makes the layer's brightness follow the music the phone hears (Play → Listen).")
def beat_fx(frame, ctx, P, st):
    idle = P["idle"] == "tempo"
    if not getattr(ctx, "audio", None) and not idle:
        return frame
    m = pulse(ctx, P["decay"], idle, getattr(ctx, "beats", None))
    s = max(0.0, min(1.0, m[P["follow"]]))
    a = P["amount"]
    k = 1.0 - a + a * s
    wf = P["flash"] * m["beat"]
    out = list(frame)
    for i in ctx.idx:
        c = frame[i]
        r, g, b = c[0] * k, c[1] * k, c[2] * k
        if wf > 0 and (c[0] or c[1] or c[2]):
            r, g, b = r + (1 - r) * wf, g + (1 - g) * wf, b + (1 - b) * wf
        out[i] = (r, g, b)
    return out
