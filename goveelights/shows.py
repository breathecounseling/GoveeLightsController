"""Things that happen on their own: countdown events, game-day celebrations,
weather overlays, and the maths for camera mapping."""
from __future__ import annotations

import colorsys
import logging
import math
import statistics
import time
from datetime import datetime, timedelta

from . import live
from .scene import Scene

log = logging.getLogger(__name__)


def led_bright(hx: str) -> str:
    """Team colours like navy are too dark for bulbs: keep the hue, lift the brightness."""
    r, g, b = (int(hx[i:i + 2], 16) / 255 for i in (1, 3, 5))
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    if s < 0.2:
        return "#ffffff" if v > 0.5 else "#c0c8ff"
    r, g, b = colorsys.hsv_to_rgb(h, max(s, 0.75), max(v, 0.9))
    return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))


# ------------------------------------------------------------ celebrations
def celebration(kind: str, colors: list[str]) -> tuple[dict, float]:
    """(scene, seconds) for a game event, in the team's colours."""
    c = [led_bright(x) for x in colors] or ["#ff4f10", "#3060ff"]
    if kind == "touchdown":
        return {"name": "Touchdown!", "layers": [
            {"effect": "alternate", "params": {"colors": c, "group": 3, "period": 0.25, "fade": 0}},
            {"effect": "bursts", "params": {"colors": c + ["#ffffff"], "interval": 0.35, "life": 1.4, "burst_speed": 18},
             "blend": "lighten", "fx": [{"type": "trails", "params": {"length": 0.3}}]},
            {"effect": "sparkle", "params": {"rate": 25, "decay": 0.12}, "blend": "add"}]}, 30.0
    if kind == "win":
        return {"name": "Victory!", "layers": [
            {"effect": "gradient", "params": {"colors": c, "length": 20, "speed": 25},
             "fx": [{"type": "glitter", "params": {"density": 1.0, "decay": 0.15, "base": 0.5}}]},
            {"effect": "bursts", "params": {"colors": c + ["#ffffff"], "interval": 0.6}, "blend": "lighten"}]}, 90.0
    # field goal, goal, run…
    return {"name": "Score!", "layers": [
        {"effect": "sequins", "params": {"colors": c[:2] if len(c) > 1 else c * 2, "spacing": 60, "speed": 40}},
        {"effect": "sparkle", "params": {"rate": 15, "decay": 0.1}, "blend": "add"}]}, 15.0


def scoreboard_scene(game: dict | None) -> dict:
    """The live score on the zigzag (the text effect's "score" source, which shows the
    broadcast-delayed score) with the eaves chasing in the team's colours."""
    game = game if isinstance(game, dict) else {}
    cols = []
    for x in game.get("colors") or []:
        try:
            cols.append(led_bright(str(x)))
        except (ValueError, IndexError):
            continue
    cols = cols[:2] or ["#ff4f00", "#2050ff"]
    if len(cols) == 1 or cols[0] == cols[1]:
        cols = [cols[0], "#ffffff" if cols[0] != "#ffffff" else "#ff4f00"]
    team = "".join(ch for ch in str(game.get("team") or "").upper() if ch.isalnum())[:10]
    return {"name": "Scoreboard", "layers": [
        {"effect": "chase", "target": ["Eaves", "Lower roofline"],
         "params": {"colors": cols, "spacing": 4, "width": 2, "speed": 3, "axis": "strip"}},
        {"effect": "text", "target": "Peak",
         "params": {"source": "score", "text": f"GO {team}!" if team else "GAME ON!", "colors": ["#ffffff"],
                    "color_mode": "solid", "speed": 5.5, "sparkle": 0.1, "border": "twinkle",
                    "border_color": cols[1], "score_detail": True}}]}


def countdown_scene(target: float, count_s: int, digits_s: int, colors=None) -> dict:
    cols = colors or ["#ffffff", "#ffd000", "#ffffff"]
    return {"name": "Countdown", "layers": [
        {"effect": "countdown", "params": {"target": target, "seconds": count_s, "part": "fill", "colors": cols}},
        {"effect": "countdown", "params": {"target": target, "seconds": digits_s, "part": "digits", "colors": cols},
         "target": "Peak"}]}


def days_scene(days: int, colors=None) -> dict:
    cols = colors or ["#ff2020", "#00c040"]
    return {"name": f"{days} {'day' if days == 1 else 'days'} to go", "layers": [
        {"effect": "glitter", "params": {"colors": cols, "level": 0.2, "density": 0.3}},
        {"effect": "digits", "params": {"value": days, "colors": ["#ffffff"]}, "target": "Peak"}]}


# ---------------------------------------------------------------- events
def next_occurrence(ev: dict, now: datetime) -> datetime:
    hh, mm = (int(x) for x in ev.get("time", "00:00").split(":"))
    m, d = int(ev["month"]), int(ev["day"])
    for year in (now.year, now.year + 1):
        try:
            at = now.replace(year=year, month=m, day=d, hour=hh, minute=mm, second=0, microsecond=0)
        except ValueError:            # Feb 29 in a non-leap year
            continue
        if at > now - timedelta(minutes=float(ev.get("minutes", 10))):
            return at
    return now.replace(year=now.year + 1, month=m, day=d, hour=hh, minute=mm, second=0, microsecond=0)


DEFAULT_EVENTS = [
    {"name": "New Year", "month": 1, "day": 1, "time": "00:00", "countdown": 60, "digits": 10,
     "celebrate": "fireworks", "minutes": 10, "reminders": False, "lights_on": True, "enabled": True},
    {"name": "Christmas", "month": 12, "day": 25, "time": "00:00", "countdown": 0, "digits": 10,
     "celebrate": "festive-glitter", "minutes": 0, "reminders": True, "remind_from_days": 24,
     "lights_on": False, "enabled": True},
]


# --------------------------------------------------------------- mapping
def fit_mapping(layout, points, only: list[int] | None = None) -> dict[int, tuple[float, float]]:
    """Measured image points [(virtual index, x, y)] -> positions in the layout's
    coordinate frame. Scale, rotation and offset are fitted against the bulbs of runs
    that already have a mapped shape, so mapping part of the house keeps the rest of
    it in proportion; with nothing mapped yet, 1 unit = the median neighbour gap.
    Bulbs of runs without a shape (placeholders below the house) are placed with the
    transform fitted on the shaped runs, not matched to their placeholder line."""
    px = layout.pixels
    shaped = {r.mac for r in layout.runs if r.geometry}
    allm = {}
    for p in points:                                  # [id, x, y, (conf)]
        i, x, y = int(p[0]), float(p[1]), float(p[2])
        if 0 <= i < len(px) and math.isfinite(x) and math.isfinite(y):
            allm[i] = (x, -y)
    meas = allm
    if only is not None:
        keep = set(only)
        meas = {i: v for i, v in allm.items() if i in keep}
    if len(meas) < 8:
        raise ValueError(f"only {len(meas)} bulbs were decoded in that area; need at least 8")

    def pairs(m):
        return [(i, i + 1) for i in m if i + 1 in m and px[i].mac == px[i + 1].mac]

    gaps = [math.dist(meas[i], meas[j]) for i, j in pairs(meas)]
    if len(gaps) < 4:
        raise ValueError("not enough neighbouring bulbs decoded to measure the spacing")
    # reference bulbs: shaped runs in the target, else shaped runs anywhere in the image
    ref = {i: v for i, v in meas.items() if px[i].mac in shaped}
    if len(ref) < 8:
        ref = {i: v for i, v in allm.items() if px[i].mac in shaped}
    ratios = []
    for i, j in pairs(ref):
        mg = math.dist(ref[i], ref[j])
        if mg > 0:
            lg = math.dist((px[i].x, px[i].y), (px[j].x, px[j].y))
            ratios.append((lg, lg / mg))
    unit = [r for lg, r in ratios if 0.85 <= lg <= 1.15]   # bulbs that are 1 unit apart in the layout
    if len(unit) >= 4:
        s = statistics.median(unit)
    elif len(ratios) >= 4:
        s = statistics.median(r for _, r in ratios)
    else:
        mg = statistics.median(gaps)
        if not mg > 0:
            raise ValueError("the decoded bulbs sit on top of each other; cannot measure the spacing")
        s = 1.0 / mg
    if not (s > 0 and math.isfinite(s)):
        raise ValueError("the decoded bulbs sit on top of each other; cannot measure the spacing")
    fit = ref if len(ref) >= 3 else meas
    ids = sorted(fit)
    A = [(fit[i][0] * s, fit[i][1] * s) for i in ids]
    B = [(px[i].x, px[i].y) for i in ids]
    ca = (sum(a[0] for a in A) / len(A), sum(a[1] for a in A) / len(A))
    cb = (sum(b[0] for b in B) / len(B), sum(b[1] for b in B) / len(B))
    th = 0.0
    if fit is ref:
        num = sum((a[0] - ca[0]) * (b[1] - cb[1]) - (a[1] - ca[1]) * (b[0] - cb[0]) for a, b in zip(A, B))
        den = sum((a[0] - ca[0]) * (b[0] - cb[0]) + (a[1] - ca[1]) * (b[1] - cb[1]) for a, b in zip(A, B))
        th = math.atan2(num, den)

    def rms(t):
        co, si = math.cos(t), math.sin(t)
        return math.sqrt(sum((cb[0] + co * (a[0] - ca[0]) - si * (a[1] - ca[1]) - b[0]) ** 2 +
                             (cb[1] + si * (a[0] - ca[0]) + co * (a[1] - ca[1]) - b[1]) ** 2
                             for a, b in zip(A, B)) / len(A))
    if abs(th) > math.radians(25):
        # the camera should be level-ish: a big angle is either a poor old estimate (level
        # still fits about as well) or a rotated / tilted video, which must not be saved
        if rms(0.0) > 1.5 * rms(th) + 0.5:
            raise ValueError(f"the video looks rotated by about {abs(math.degrees(th)):.0f} degrees; "
                             "record in landscape and hold the camera level")
        th = 0.0
    co, si = math.cos(th), math.sin(th)
    out = {}
    for i in sorted(meas):
        dx, dy = meas[i][0] * s - ca[0], meas[i][1] * s - ca[1]
        out[i] = (cb[0] + co * dx - si * dy, cb[1] + si * dx + co * dy)
    return out


def _extend(known: list[int], pos: dict, count: int, span: int = 4) -> list[tuple[float, float]]:
    """Positions for bulbs 0..count-1 of a run with no old shape: linear between the
    nearest measured bulbs, and straight on past the ends (direction from up to `span`
    measured bulbs at that end, 1 unit per bulb if only one bulb was measured)."""
    def step(a, b):
        if a == b:
            return (1.0, 0.0)
        return ((pos[b][0] - pos[a][0]) / (b - a), (pos[b][1] - pos[a][1]) / (b - a))
    lo, hi = known[0], known[-1]
    s_lo = step(lo, known[min(span, len(known) - 1)])
    s_hi = step(known[max(0, len(known) - 1 - span)], hi)
    out = []
    for i in range(count):
        if i <= lo:
            out.append((pos[lo][0] + (i - lo) * s_lo[0], pos[lo][1] + (i - lo) * s_lo[1]))
        elif i >= hi:
            out.append((pos[hi][0] + (i - hi) * s_hi[0], pos[hi][1] + (i - hi) * s_hi[1]))
        else:
            a = max(k for k in known if k <= i)
            b = min(k for k in known if k >= i)
            f = 0.0 if a == b else (i - a) / (b - a)
            out.append((pos[a][0] + f * (pos[b][0] - pos[a][0]), pos[a][1] + f * (pos[b][1] - pos[a][1])))
    return out


def merged_geometry(layout, measured: dict[int, tuple[float, float]], max_gap: int = 8) -> dict[str, list]:
    """Per-run dense geometry knots [[local, x, y], ...]: measured bulbs where we have
    them, interpolated across short gaps, and the old estimate elsewhere."""
    geo = {}
    for r in layout.runs:
        est = r.positions()
        pos = {p.local: measured[p.i] for p in r.pixels if p.i in measured}
        known = sorted(pos)
        if not r.geometry and known:
            # no old shape (a new strip): its (i, 0) estimate is meaningless, so carry the
            # measured bulbs on past both ends, along the last few measured bulbs
            est = _extend(known, pos, r.count)
        knots = []
        for local in range(r.count):
            if local in pos:
                x, y = pos[local]
            else:
                lo = max((k for k in known if k < local), default=None)
                hi = min((k for k in known if k > local), default=None)
                if lo is not None and hi is not None and hi - lo <= max_gap:
                    f = (local - lo) / (hi - lo)
                    x = pos[lo][0] + f * (pos[hi][0] - pos[lo][0])
                    y = pos[lo][1] + f * (pos[hi][1] - pos[lo][1])
                else:
                    x, y = est[local]
            knots.append([local, round(x, 3), round(y, 3)])
        if pos:
            geo[r.mac] = knots
    return geo


def scene(d: dict, pals) -> Scene:
    return Scene.from_dict(d, pals)


__all__ = ["celebration", "countdown_scene", "days_scene", "next_occurrence", "DEFAULT_EVENTS",
           "fit_mapping", "merged_geometry", "led_bright", "live", "time"]
