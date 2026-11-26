"""Dusk sky, depth and symmetry tools.

    dusk         (effect)  the house coloured like the real sky behind it at this moment
    heartbeat    (effect)  a lub-dub pulse of the whole layer (pairs with the cascade FX)
    cascade      (FX)      delays the layer per bulb by height / distance: waves fall or ripple
    apex_mirror  (FX)      mirrors the layer around the main gable apex instead of the strip centre

The house faces east and viewers look west from the street, so the real sunset happens
behind the roof: the dusk effect follows the sun's elevation (computed with the same
NOAA-style equations as goveelights.schedule) through civil and nautical twilight.
"""
from __future__ import annotations

import math
import threading
import time as _time
from datetime import date, datetime, timezone

from .. import geometry as geo
from .. import livedata
from ..color import BLACK, Color, clamp, hash01, hex_to_rgb, lerp, palette
from ..effects import Param, effect, travel
from ..fx import fx
from ..schedule import sun_times

# ======================================================================= sun
def sun_position(ts: float, lat: float, lon: float) -> tuple[float, float]:
    """(elevation, azimuth) of the sun's centre in degrees at unix time ts.
    Azimuth is clockwise from north (west = 270). lon east-positive. Same series as
    schedule.sun_times (mean anomaly, equation of centre, ecliptic longitude), with the
    hour angle measured from the nearest solar transit."""
    jd = ts / 86400.0 + 2440587.5
    n = round(jd - 2451545.0 + lon / 360.0)          # solar day whose transit is closest
    jstar = n - lon / 360.0
    M = (357.5291 + 0.98560028 * jstar) % 360
    Mr = math.radians(M)
    C = 1.9148 * math.sin(Mr) + 0.0200 * math.sin(2 * Mr) + 0.0003 * math.sin(3 * Mr)
    lam = math.radians((M + C + 180 + 102.9372) % 360)
    jtransit = 2451545.0 + jstar + 0.0053 * math.sin(Mr) - 0.0069 * math.sin(2 * lam)
    dec = math.asin(math.sin(lam) * math.sin(math.radians(23.4397)))
    H = math.radians((jd - jtransit) * 360.0)
    phi = math.radians(max(-89.9, min(89.9, lat)))
    s = math.sin(phi) * math.sin(dec) + math.cos(phi) * math.cos(dec) * math.cos(H)
    el = math.degrees(math.asin(max(-1.0, min(1.0, s))))
    az = math.degrees(math.atan2(math.sin(H), math.cos(H) * math.sin(phi) - math.tan(dec) * math.cos(phi))) + 180
    return el, az % 360


def sunset_azimuth(lat: float, lon: float, d: date | None = None) -> float:
    """Where the sun goes down today (degrees from north); 240 if it doesn't set."""
    d = d or datetime.now().date()
    try:
        _, ss = sun_times(d, lat, lon)
    except (ValueError, ZeroDivisionError):
        ss = None
    if ss is None:
        return 240.0
    return sun_position(ss.timestamp(), lat, lon)[1]


# ======================================================================= dusk
H = hex_to_rgb
# Sky keyframes by solar elevation (degrees): four colours from the horizon (bottom bulbs)
# up to the zenith (top bulbs). Kept saturated and never very dark: LEDs read dark
# colours as "off" and pastels as white.
SKY = [
    (10.0, [H("#ffb020"), H("#ff8a30"), H("#3a70ff"), H("#1448ff")]),   # late afternoon
    (4.0, [H("#ff9010"), H("#ff5a20"), H("#e03a80"), H("#2a3cff")]),    # golden hour
    (0.5, [H("#ff6a00"), H("#ff3420"), H("#d82070"), H("#3a22e0")]),    # sunset
    (-3.0, [H("#ff4000"), H("#f01838"), H("#a018a8"), H("#2418d0")]),   # civil twilight
    (-6.0, [H("#e02404"), H("#b8104c"), H("#6418b4"), H("#1818c0")]),   # end of civil twilight
    (-9.0, [H("#b01c18"), H("#7c147c"), H("#3c18c0"), H("#1020c8")]),   # nautical twilight
]
NIGHT_FROM, NIGHT_FULL = -9.0, -13.0          # blend into the night palette across these
DEMO_LO, DEMO_HI = 7.0, -14.0                  # elevation sweep of the demo
_memo = threading.local()


def _sky_stops(el: float) -> list[Color]:
    if el >= SKY[0][0]:
        return SKY[0][1]
    for (e0, c0), (e1, c1) in zip(SKY, SKY[1:]):
        if el >= e1:
            f = (e0 - el) / (e0 - e1)
            f = f * f * (3 - 2 * f)
            return [lerp(a, b, f) for a, b in zip(c0, c1)]
    return SKY[-1][1]


def _demo_elevation(t: float, demo: float) -> float:
    """Demo cycle: dusk over `demo` s, hold the night for a quarter of that, then a quick
    rewind back to the start (so the loop never jumps)."""
    cyc = demo * 1.35
    u = (t % cyc) / demo
    if u <= 1.0:
        return DEMO_LO + (DEMO_HI - DEMO_LO) * u
    if u <= 1.25:
        return DEMO_HI
    f = (u - 1.25) / 0.10
    f = f * f * (3 - 2 * f)
    return DEMO_HI + (DEMO_LO - DEMO_HI) * f


def dusk_sky(t: float, P: dict) -> dict:
    """Per-frame sky state (computed once per frame, shared by every bulb)."""
    night = P["colors"]
    lat, lon = P["lat"], P["lon"]
    if lat == 0 and lon == 0:                        # the default: wherever the house is
        lat, lon = livedata.home()
    key = (t, P["demo"], lat, lon, P["shift"], tuple(night), P["night_level"],
           P["sun_glow"])
    m = getattr(_memo, "dusk", None)
    if m is not None and m[0] == key:
        return m[1]
    if P["demo"] > 0:
        el = _demo_elevation(t, P["demo"])
        ak = (lat, lon, datetime.now().date())
        az_m = getattr(_memo, "az", None)
        if az_m is None or az_m[0] != ak:
            az_m = (ak, sunset_azimuth(lat, lon))
            _memo.az = az_m
        az = az_m[1]
    else:
        el, az = sun_position(_time.time() + P["shift"] * 60.0, lat, lon)
    stops = _sky_stops(el)
    wn = clamp((NIGHT_FROM - el) / (NIGHT_FROM - NIGHT_FULL))
    wn = wn * wn * (3 - 2 * wn)
    if wn > 0:
        nl = P["night_level"]
        nstops = [palette([(c[0] * nl, c[1] * nl, c[2] * nl) for c in night], k / 3, cyclic=False)
                  for k in range(4)]
        stops = [lerp(a, b, wn) for a, b in zip(stops, nstops)]
    # the afterglow over the roof where the sun went down: strongest around sunset
    glow = P["sun_glow"] * math.exp(-((el - 0.0) / 5.0) ** 2) if el > -12 else 0.0
    sun_x = 0.5 + (az - 270.0) / 70.0                # house spans roughly +-35 deg of the view west
    sky = {"el": el, "az": az, "stops": stops, "night": wn, "glow": glow, "sun_x": sun_x}
    _memo.dusk = (key, sky)
    return sky


_lattice: dict = {}


def _vnoise(x: float, y: float, seed: int) -> float:
    """Same value noise as color.noise2, with the lattice hashes memoised (the cloud field
    only touches a few dozen lattice points per frame)."""
    xi, yi = math.floor(x), math.floor(y)
    fx, fy = x - xi, y - yi
    fx, fy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
    lat = _lattice
    if len(lat) > 4096:
        lat.clear()
    v = []
    for k in ((xi, yi), (xi + 1, yi), (xi, yi + 1), (xi + 1, yi + 1)):
        h = lat.get((k, seed))
        if h is None:
            h = lat[(k, seed)] = hash01(k[0], k[1], seed)
        v.append(h)
    top = v[0] + (v[1] - v[0]) * fx
    bot = v[2] + (v[3] - v[2]) * fx
    return top + (bot - top) * fy


def _vivid(c: Color, amount: float) -> Color:
    """Saturate c by `amount` (0..1) at the same brightness, keeping its hue."""
    hi, lo = max(c), min(c)
    if amount <= 0 or hi <= 1e-6 or hi - lo <= 1e-6:
        return c
    s = (hi - lo) / hi
    s2 = s + (1.0 - s) * amount
    lo2 = hi * (1.0 - s2)
    k = (hi - lo2) / (hi - lo)                       # stretch away from the brightest channel
    return (hi - (hi - c[0]) * k, hi - (hi - c[1]) * k, hi - (hi - c[2]) * k)


GLOW = H("#ffa818")
STAR = H("#e8eeff")
STAR_WINDOW, STAR_FADE = 30.0, 3.0
AURORA = [H("#00ff60"), H("#10ff90"), H("#00e0c0"), H("#9020ff")]


@effect("dusk", "Sky at dusk", [
    Param("colors", "colors", ["#1a60ff", "#2a18d0", "#7a10c8"], "Night palette",
          help="the sky once it's dark, from the lowest bulbs to the highest"),
    Param("night_level", "number", 0.65, "Night brightness", 0.2, 1, 0.05,
          help="dims the night palette (keep it high: dark blues read as off on LEDs)"),
    Param("demo", "number", 0, "Demo: whole dusk in (s)", 0, 600, 1,
          help="0 = follow the real sun; otherwise race through a whole dusk in this many seconds, "
               "hold the night, then loop"),
    Param("shift", "number", 0, "Clock shift (min)", -720, 720, 5,
          help="show the sky as it will be this many minutes from now (real-time mode)"),
    Param("clouds", "number", 0.35, "Clouds", 0, 1, 0.05, help="drifting brighter and darker patches"),
    Param("speed", "number", 0.6, "Cloud drift (bulbs/s)", -10, 10, 0.1, help="negative drifts the other way"),
    Param("sun_glow", "number", 0.6, "Sunset glow", 0, 1, 0.05,
          help="warm afterglow low on the side of the house where the sun went down"),
    Param("vivid", "number", 0, "Vivid colours", 0, 1, 0.05,
          help="pushes the dusky pinks and mauves between the gold and the blue toward pure magenta "
               "and violet (muted mixes look salmon on LEDs); 0 = natural sky"),
    Param("stars", "number", 0.12, "Stars", 0, 0.5, 0.01, help="share of the upper bulbs that twinkle once it's dark"),
    Param("star_height", "number", 0.35, "Stars from height", 0, 1, 0.05,
          help="stars only above this height (0 = the porch and flat eaves get stars too)"),
    Param("aurora", "number", 0, "Night aurora", 0, 1, 0.05,
          help="once it's fully dark, slow green, teal and violet curtains drift across the upper "
               "bulbs (the gable), so the night keeps moving; 0 = off"),
    Param("height", "choice", "blend", "Gradient over", options=["blend", "whole house", "each run"],
          help="whole house: porch = horizon, apex = zenith; each run: every run gets the full sky; "
               "blend: half of each, so the gables sweep from horizon to zenith and the porch stays warmest"),
    Param("lat", "number", 0, "Latitude", -89, 89, 0.0001,
          help="0 with longitude 0 = the house's location from the schedule settings"),
    Param("lon", "number", 0, "Longitude (east +)", -180, 180, 0.0001,
          help="0 with latitude 0 = the house's location from the schedule settings"),
], "The house glows like the sky behind it right now: gold and orange low on the porch, rose and "
   "violet across the eaves and deep blue at the apex as the sun sets, deepening through twilight "
   "into a calm night palette with a few twinkling stars. Soft cloud-like patches drift across.",
    group="Scenes")
def dusk(t, p, P):
    sky = dusk_sky(t, P)
    mode = P["height"]
    h = p.ny if mode == "whole house" else p.rny if mode == "each run" else 0.5 * (p.ny + p.rny)
    # away from where the sun set, the same height shows sky from higher up (cooler)
    far = abs(p.nx - sky["sun_x"])
    c = palette(sky["stops"], clamp(h + 0.3 * (far - 0.45)), cyclic=False)
    g = sky["glow"]
    if g > 0:
        dx = far / 0.45
        w = g * math.exp(-dx * dx) * (1 - h)
        if w > 0.004:
            c = lerp(c, GLOW, min(1.0, w))
    cl = P["clouds"]
    if cl > 0:
        x = p.x * 0.16 - travel(t, P) * 0.16
        v = 0.65 * _vnoise(x, p.y * 0.3, 7) + 0.35 * _vnoise(x * 2.3 + 5.1, p.y * 0.6, 11)
        k = 1 - cl + cl * (0.35 + 1.15 * v)
        c = (c[0] * k, c[1] * k, c[2] * k)
    vv = P["vivid"]
    if vv > 0:
        c = _vivid(c, vv)
    wn = sky["night"]
    au = P["aurora"]
    if wn > 0 and au > 0:
        # curtains hang from the top: strongest at the apex, gone below mid-height
        hh = clamp((h - 0.25) / 0.35)
        if hh > 0:
            v = _vnoise(p.x * 0.1 + t * 0.14, t * 0.06, 23)
            v = clamp((v - 0.25) * 2.5)
            if v > 0:
                col = palette(AURORA, _vnoise(p.x * 0.035 - t * 0.04, 0.5, 29), cyclic=False)
                c = lerp(c, col, au * wn * hh * v)
    st = P["stars"]
    if wn > 0 and st > 0 and h > P["star_height"]:
        # the stars are re-chosen every STAR_WINDOW seconds, the old set fading out over
        # the last STAR_FADE s while the new one fades in: they appear, sparkle and fade
        win = math.floor(t / STAR_WINDOW)
        u = t - win * STAR_WINDOW
        xf = clamp((u - (STAR_WINDOW - STAR_FADE)) / STAR_FADE)
        s = 0.0
        for w, k in ((1.0 - xf, win), (xf, win + 1)):
            if w > 0 and hash01(p.i, 4242 + k) < st:
                r = hash01(p.i, 77 + k)
                tw = 0.5 + 0.5 * math.sin(t * (0.6 + 1.4 * r) * 2 * math.pi + r * 40)
                s += w * tw ** 6
        if s > 0.002:
            c = lerp(c, STAR, wn * min(1.0, s))
    return (clamp(c[0]), clamp(c[1]), clamp(c[2]))


# ================================================================== heartbeat
@effect("heartbeat", "Heartbeat", [
    Param("colors", "colors", ["#ff1030"], help="each beat uses the next colour"),
    Param("background", "color", "#000000"),
    Param("bpm", "number", 62, "Beats per minute", 20, 200, 1),
    Param("strength", "number", 1, "Second thump", 0, 1, 0.05, help="how strong the 'dub' after the 'lub' is"),
    Param("decay", "number", 0.18, "Thump length (s)", 0.04, 1, 0.01),
], "The whole layer thumps lub-dub like a heartbeat. Add the Depth cascade FX 'from apex' "
   "and each beat ripples outward from the peak.", group="Texture & time")
def heartbeat(t, p, P):
    per = 60.0 / P["bpm"]
    k = math.floor(t / per)
    u = t - k * per
    dec = P["decay"]

    def thump(a):
        if a < 0:
            return 0.0
        if a < 0.03:
            return (a / 0.03) ** 2
        return math.exp(-(a - 0.03) / dec)
    v = min(1.0, thump(u) + P["strength"] * 0.75 * thump(u - 0.28 * min(1.0, per)))
    cols = P["colors"]
    return lerp(P["background"], cols[k % len(cols)], v)


# ================================================================= apex point
def apex_point(layout, idx: list[int], how: str = "auto") -> tuple[float, float]:
    """The point a target is symmetric about / ripples from.
    auto: the main gable apex (geometry.star of the Peak) if it crowns this target,
    else the highest roof peak in the target, else the target's top middle."""
    px = layout.pixels
    if not idx:
        return 0.0, 0.0
    sel = set(idx)
    xs = [px[i].x for i in idx]
    lo, hi = min(xs), max(xs)

    def mean(ii):
        return sum(px[i].x for i in ii) / len(ii), max(px[i].y for i in ii)
    star = geo.star(layout, "Peak")
    pk = [o["i"] for o in geo.peaks(layout) if o["i"] in sel]
    star_in = bool(star) and any(i in sel for i in star)
    star_over = bool(star) and lo - 1 <= mean(star)[0] <= hi + 1
    if how == "main gable apex" and star:
        return mean(star)
    if how == "highest peak in target" and pk:
        return px[pk[0]].x, px[pk[0]].y
    if how != "middle of target":
        if star_in:
            return mean(star)
        if pk:
            return px[pk[0]].x, px[pk[0]].y
        if star_over:
            return mean(star)
    top = max(px[i].y for i in idx)
    return (lo + hi) / 2, top


# ==================================================================== cascade
CASCADE_MODES = ["height-down", "height-up", "from apex", "from the street"]
MAX_DELAY = 2.0
_MIN_STEP = 1.0 / 120          # history resolution: bounded memory at any frame rate


def _cascade_geometry(ctx, mode: str, curve: float, st: dict):
    """Per target bulb: delay fraction 0..1, viewer-distance fraction 0..1 (for the depth
    fade), and the bulbs sorted by delay. Cached in state, recomputed when the layout,
    target, mode or curve change. Only a layout/target change clears the history (it is
    stored per target bulb, so it stays valid when just the delays change)."""
    g = st.get("geom")
    same_target = bool(g) and g[0] is ctx.layout and g[1] is ctx.idx and len(g[3]) == len(ctx.idx)
    if same_target and g[2] == (mode, curve):
        return g[3], g[4], g[5]
    px, idx = ctx.layout.pixels, ctx.idx
    if not idx:
        fr, order, far = [], [], []
    else:
        ys = [px[i].y for i in idx]
        xs = [px[i].x for i in idx]
        ylo, yhi = min(ys), max(ys)
        ax, ay = apex_point(ctx.layout, idx)
        half = max(1e-6, max(abs(x - ax) for x in xs))
        yspan = max(1e-6, yhi - ylo)
        # how far each bulb is from the viewer: lower roofs are in front (the porch roof is
        # nearer the street than the eaves) and the middle is a little nearer than the ends
        far = [0.8 * (y - ylo) / yspan + 0.2 * abs(x - ax) / half for x, y in zip(xs, ys)]
        if mode == "height-up":
            raw = [y - ylo for y in ys]
        elif mode == "from apex":
            raw = [math.hypot(x - ax, y - ay) for x, y in zip(xs, ys)]
        elif mode == "from the street":
            raw = far
        else:                                   # height-down: the roof first, the porch last
            raw = [yhi - y for y in ys]
        lo, hi = min(raw), max(raw)
        span = hi - lo
        fr = [((r - lo) / span) ** curve if span > 1e-9 else 0.0 for r in raw]
        order = sorted(range(len(idx)), key=lambda k: fr[k])
        flo, fhi = min(far), max(far)
        far = [(f - flo) / (fhi - flo) if fhi - flo > 1e-9 else 0.0 for f in far]
    st["geom"] = (ctx.layout, ctx.idx, (mode, curve), fr, order, far)
    if not same_target:
        st["hist"] = []                          # history is aligned to the target: start over
    return fr, order, far


@fx("cascade", "Depth cascade", [
    Param("mode", "choice", "height-down", "Direction", options=CASCADE_MODES,
          help="height-down: falls from the roof to the porch; height-up: rises; from apex: ripples "
               "outward from the gable peak; from the street: starts on the lowest roof nearest the "
               "street and travels back up the house"),
    Param("delay", "number", 0.8, "Max delay (s)", 0.05, MAX_DELAY, 0.05,
          help="how far behind the last bulbs run: longer = slower, deeper waves"),
    Param("curve", "number", 1.0, "Delay curve", 0.3, 3, 0.05,
          help="1 = even; below 1 = speeds up as it goes (like a falling drop); above 1 = slows down"),
    Param("depth_fade", "number", 0.25, "Depth fade", 0, 0.9, 0.05,
          help="bulbs farther from the street (higher up the house, out at its ends) are this much "
               "dimmer, for a sense of distance"),
], "Delays the layer bulb by bulb so anything it does sweeps across the house with real depth: "
   "flashes fall from the roof down to the porch, or ripple outward from the apex. The layer "
   "cascades in when it starts.")
def cascade(frame, ctx, P, st):
    idx = ctx.idx
    if not idx or len(frame) != len(ctx.layout.pixels):
        return frame
    mode = P["mode"] if P["mode"] in CASCADE_MODES else "height-down"
    fr, order, far = _cascade_geometry(ctx, mode, float(P["curve"]), st)
    maxd = min(MAX_DELAY, max(0.0, float(P["delay"])))
    raw_dt = max(0.0, float(ctx.dt or 0.0))
    dt = min(0.1, raw_dt)
    # the layer restarted (its clock jumped) or was switched off for a while (no frames
    # for over a second of wall time): forget the old frames and cascade in afresh
    clock, now = float(ctx.clock or 0.0), _time.monotonic()
    last = st.get("clock")
    if st.get("hist") and ((last is not None and abs(clock - (last + raw_dt)) > 0.5)
                           or now - st.get("wall", now) > 1.0):
        st["hist"] = []
    st["clock"], st["wall"] = clock, now
    t = st.get("t", 0.0) + dt
    st["t"] = t
    hist = st.setdefault("hist", [])
    vals = [frame[i] for i in idx]
    if not hist:
        st["start"] = t
    if hist and (t <= hist[-1][0] or (len(hist) >= 2 and hist[-1][0] - hist[-2][0] < _MIN_STEP)):
        hist[-1] = (t, vals)                     # paused or very fast frames: refresh the newest
    else:
        hist.append((t, vals))
    keep_from = t - maxd - 0.05
    cut = 0
    while cut + 1 < len(hist) and hist[cut + 1][0] <= keep_from:
        cut += 1
    if cut:
        del hist[:cut]
    out = list(frame)
    fade = P["depth_fade"]
    j = len(hist) - 1
    t0 = hist[0][0]
    start = st.get("start", t0)
    for k in order:                              # increasing delay: walk the history backwards once
        f = fr[k]
        tau = t - f * maxd
        i = idx[k]
        if tau < t0 - 1e-9:
            if tau < start - 1e-9:
                out[i] = BLACK                   # not there yet: the layer cascades in
                continue
            tau = t0                             # delay just made longer: hold the oldest frame
        while j > 0 and hist[j - 1][0] >= tau:
            j -= 1
        b = hist[j]
        if j > 0 and b[0] > tau:
            a = hist[j - 1]
            w = (tau - a[0]) / (b[0] - a[0])
            ca, cb = a[1][k], b[1][k]
            c = (ca[0] + (cb[0] - ca[0]) * w, ca[1] + (cb[1] - ca[1]) * w, ca[2] + (cb[2] - ca[2]) * w)
        else:
            c = b[1][k]
        if fade:
            m = 1 - fade * far[k]
            c = (c[0] * m, c[1] * m, c[2] * m)
        out[i] = c
    return out


# ================================================================ apex mirror
MIRROR_SOURCES = ["right half (north)", "left half (south)", "both (brightest wins)"]
MIRROR_CENTRES = ["auto", "main gable apex", "highest peak in target", "middle of target"]


def _mirror_partners(ctx, centre: str):
    """For each target bulb: its partner across the apex (nearest bulb to the reflected
    position x' = 2*apex_x - x at the same height), or None. Cached per layout/target."""
    key = ("apex_mirror", id(ctx.layout), id(ctx.idx), len(ctx.idx), centre)
    hit = ctx.cache.get(key)
    if hit and hit[0] is ctx.layout and hit[1] is ctx.idx:
        return hit[2]
    px, idx = ctx.layout.pixels, ctx.idx
    ax, ay = apex_point(ctx.layout, idx, centre)
    side = {}
    for i in idx:
        dx = px[i].x - ax
        side[i] = 0 if abs(dx) < 0.25 else (1 if dx > 0 else -1)
    partner = {}
    pts = [(i, px[i].x, px[i].y, px[i].run) for i in idx]
    for i in idx:
        s = side[i]
        if s == 0:
            partner[i] = i
            continue
        rx, ry, run = 2 * ax - px[i].x, px[i].y, px[i].run
        best, bd = None, 2.5 * 2.5            # accept partners within 2.5 bulbs of the reflection
        for j, x, y, r in pts:
            if side[j] == s:
                continue
            # prefer the same run, so the gable outline mirrors onto the outline, not the tree
            d = (x - rx) ** 2 + ((y - ry) * 2) ** 2 + (0.0 if r == run else 1.5)
            if d < bd:
                best, bd = j, d
        partner[i] = best
    # bulbs nearest the apex, weighted, for the flare
    near = []
    for i in idx:
        d = math.hypot(px[i].x - ax, (px[i].y - ay) * 1.5)
        if d < 3.0:
            w = 1 - d / 3.0
            near.append((i, w * w * (3 - 2 * w)))
    res = (side, partner, near, (ax, ay))
    ctx.cache[key] = (ctx.layout, ctx.idx, res)
    return res


@fx("apex_mirror", "Mirror at the apex", [
    Param("source", "choice", "right half (north)", "Source side", options=MIRROR_SOURCES,
          help="which half is copied onto the other; both: each bulb shows the brighter of itself and its mirror"),
    Param("centre", "choice", "auto", "Mirror around", options=MIRROR_CENTRES,
          help="auto: the front gable apex if it's over this target, else the target's highest peak"),
    Param("flare", "number", 0.5, "Peak flare", 0, 1, 0.05,
          help="light arriving at the apex flares up there, brighter and whiter (only when it stands "
               "out from the rest of the layer, so a steady background never flares)"),
    Param("blank_unpaired", "bool", True, "Darken unmatched bulbs",
          help="bulbs on the copied-to side with no mirror partner (e.g. the garage beyond the far "
               "end of the reflection) go dark instead of running out of step"),
], "Folds the layer at the gable apex instead of the middle of the lights: the two sides of the "
   "roof become mirror images, so anything moving meets at the peak and flares there.")
def apex_mirror(frame, ctx, P, st):
    idx = ctx.idx
    if not idx or len(frame) != len(ctx.layout.pixels):
        return frame
    side, partner, near, _ = _mirror_partners(ctx, P["centre"] if P["centre"] in MIRROR_CENTRES else "auto")
    src = P["source"]
    out = list(frame)
    if src == "both (brightest wins)":
        for i in idx:
            j = partner[i]
            if j is not None and j != i:
                a, b = frame[i], frame[j]
                out[i] = (max(a[0], b[0]), max(a[1], b[1]), max(a[2], b[2]))
    else:
        dest = -1 if src == "right half (north)" else 1
        dark = None
        for i in idx:
            if side[i] == dest:
                j = partner[i]
                if j is not None:
                    out[i] = frame[j]
                elif P["blank_unpaired"]:
                    if dark is None:             # the layer's background: its darkest colour
                        dark = min((frame[k] for k in idx), key=lambda c: c[0] + c[1] + c[2])
                    out[i] = dark
    fl = P["flare"]
    if fl > 0 and near:
        # brightest light arriving at the apex, whitened a little and splashed around it;
        # driven by how much it stands out from the layer, so a lit background never flares
        peak = max((out[i] for i, _ in near), key=lambda c: c[0] + c[1] + c[2])
        pl = (peak[0] + peak[1] + peak[2]) / 3
        ml = sum(out[i][0] + out[i][1] + out[i][2] for i in idx) / (3 * len(idx))
        amt = clamp((pl - ml) / max(0.05, pl))
        m = max(peak)
        if m > 0.02 and amt > 0.01:
            hot = lerp(peak, (m, m, m), 0.35)
            for i, w in near:
                k = fl * w * 0.8 * amt
                c = out[i]
                out[i] = (min(1.0, c[0] + hot[0] * k), min(1.0, c[1] + hot[1] * k), min(1.0, c[2] + hot[2] * k))
    return out
