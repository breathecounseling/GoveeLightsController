"""Effect library. Each effect is a pure function

    fn(t: float, p: Pixel, P: dict) -> Color (float RGB 0..1)

of time in seconds, one pixel (see layout.Pixel) and resolved params. Being
pure, any frame can be rendered for any time: the live loop, the preview and
tests all call the same function.

Add an effect with the @effect decorator; its param list drives the UI.

Positional effects take `axis`:
  strip - along the whole virtual strip (all runs end to end), in bulbs
  run   - along each run separately, in bulbs
  x     - left/right across the unrolled house, in bulb-spacing units
  y     - bottom to top (fills / rising effects on the peak), same units
"""
from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from .color import (BLACK, Color, clamp, fbm2, hash01, hex_to_rgb, hsv, lerp, palette, scale)
from .layout import Pixel


@dataclass
class Param:
    name: str
    kind: str                 # color | colors | number | int | bool | choice | text
    default: Any
    label: str = ""
    min: float | None = None
    max: float | None = None
    step: float | None = None
    options: list[str] | None = None
    help: str = ""

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if v not in (None, "")}
        d.setdefault("label", self.name.replace("_", " ").capitalize())
        return d


@dataclass
class Effect:
    name: str
    label: str
    fn: Callable[[float, Pixel, dict], Color]
    params: list[Param] = field(default_factory=list)
    description: str = ""
    group: str = ""            # picker group, e.g. "Scenes", "Physics"
    frame: bool = False        # True: fn(t, ctx, P, state) -> whole frame (effects with memory)

    def resolve(self, params: dict | None) -> dict:
        """Merge with defaults, coerce types, convert colours to floats."""
        out: dict[str, Any] = {}
        params = params or {}
        for prm in self.params:
            v = params.get(prm.name, prm.default)
            try:
                if prm.kind == "color":
                    v = hex_to_rgb(v)
                elif prm.kind == "pixels":
                    v = {int(k): hex_to_rgb(c) for k, c in (v or {}).items()}
                elif prm.kind == "colors":
                    v = [hex_to_rgb(c) for c in (v or prm.default)] or [hex_to_rgb(c) for c in prm.default]
                elif prm.kind == "int":
                    v = int(v)
                elif prm.kind == "number":
                    v = float(v)
                elif prm.kind == "bool":
                    v = bool(v)
                elif prm.kind == "choice" and v not in (prm.options or []):
                    v = prm.default
                elif prm.kind == "text":          # free text: always a plain, bounded string
                    v = ("" if v is None else str(v))[:200]
            except (TypeError, ValueError):
                v = self.resolve({})[prm.name] if params else prm.default
            if prm.kind in ("int", "number"):
                if prm.min is not None:
                    v = max(prm.min, v)
                if prm.max is not None:
                    v = min(prm.max, v)
            out[prm.name] = v
        return out

    def to_dict(self) -> dict:
        return {"name": self.name, "label": self.label, "description": self.description,
                "group": self.group, "frame": self.frame, "params": [p.to_dict() for p in self.params]}


EFFECTS: dict[str, Effect] = {}


def effect(name: str, label: str, params: list[Param], description: str = "", group: str = ""):
    """Per-pixel effect: fn(t, pixel, P) -> colour. Pure: any frame for any time."""
    def deco(fn):
        EFFECTS[name] = Effect(name, label, fn, params, description, group)
        return fn
    return deco


def frame_effect(name: str, label: str, params: list[Param], description: str = "", group: str = ""):
    """Whole-frame effect with memory: fn(t, ctx, P, state) -> list of colours for every
    pixel of the virtual strip (only ctx.idx, the layer's target pixels, are used).
    `state` is a dict that persists between frames (particles, accumulated snow…);
    ctx has layout, idx, clock, dt (seconds since last frame, already scaled by master
    speed), bpm and a per-scene cache. The first frame has dt == 0."""
    def deco(fn):
        EFFECTS[name] = Effect(name, label, fn, params, description, group, frame=True)
        return fn
    return deco


# ------------------------------------------------------------ helpers
AXES = ["strip", "run", "x", "y"]


def coord(p: Pixel, axis: str) -> tuple[float, float]:
    """Position along an axis and that axis' span, in bulb units."""
    if axis == "run":
        return float(p.ri), float(p.rn)
    if axis == "x":
        return p.sx, p.spanx + 1.0
    if axis == "y":
        return p.sy, p.spany + 1.0
    return float(p.i), float(p.n)


def travel(t: float, P: dict) -> float:
    """Distance moved so far. The engine integrates speed frame by frame into
    P["_travel"], so changing or modulating speed never makes the pattern jump."""
    return P.get("_travel", P.get("speed", 0.0) * t)


def P_center():
    return [Param("center_x", "number", 0.47, "Centre left/right", 0, 1, 0.01),
            Param("center_y", "number", 1.0, "Centre height", 0, 1, 0.01)]


def dist_from_center(p: Pixel, P: dict) -> tuple[float, float, float]:
    dx = p.sx - P["center_x"] * p.spanx
    dy = (p.sy - P["center_y"] * p.spany) * 2.0     # unrolled view squashes height
    return dx, dy, math.hypot(dx, dy)


def P_axis(default="strip"):
    return Param("axis", "choice", default, options=AXES, help="direction the effect travels / is laid out along")


def P_speed(default=10.0, lo=-60.0, hi=60.0, label="Speed (bulbs/s)"):
    return Param("speed", "number", default, label, lo, hi, 0.5, help="negative reverses direction")


# ------------------------------------------------------------ effects
@effect("solid", "Solid colour", [Param("color", "color", "#ff6a00")])
def solid(t, p, P):
    return P["color"]


@effect("chase", "Chase", [
    Param("colors", "colors", ["#ffffff"], help="lit dots cycle through these"),
    Param("background", "color", "#000000"),
    Param("spacing", "number", 6, "Spacing (bulbs)", 1, 100, 1),
    Param("width", "number", 1, "Dot width (bulbs)", 0.2, 50, 0.1),
    P_speed(8), P_axis(),
], "Evenly spaced dots marching along.")
def chase(t, p, P):
    s, _ = coord(p, P["axis"])
    x = s - travel(t, P)
    sp, w = P["spacing"], P["width"]
    # the bulb covers [x-0.5, x+0.5]; light it by how much of a dot overlaps it, so dots
    # glide smoothly between bulbs instead of hopping (anti-aliasing)
    best, cov = None, 0.0
    for k in (math.floor((x - 0.5) / sp), math.floor((x + 0.5) / sp)):
        a = k * sp
        o = max(0.0, min(x + 0.5, a + w) - max(x - 0.5, a))
        if o > cov:
            best, cov = k, o
    if best is None or cov <= 0:
        return P["background"]
    cols = P["colors"]
    return lerp(P["background"], cols[best % len(cols)], min(1.0, cov / min(1.0, w)))


@effect("comet", "Comet with tail", [
    Param("color", "color", "#ffffff"),
    Param("tail_color", "color", "#3060ff", help="tail fades from head colour to this"),
    Param("background", "color", "#000000"),
    Param("tail", "number", 15, "Tail length (bulbs)", 1, 200, 1),
    Param("count", "int", 1, "Comets", 1, 20, 1),
    Param("gap", "number", 0, "Gap before repeat (bulbs)", 0, 300, 1),
    P_speed(25), P_axis(),
], "A bright head with a fading tail; several comets are spaced evenly.")
def comet(t, p, P):
    s, span = coord(p, P["axis"])
    period = (span + P["gap"]) / P["count"]
    v = P["speed"]
    head = travel(t, P)
    d = ((head - s) if v >= 0 else (s - head)) % period
    if d >= P["tail"]:
        return P["background"]
    f = 1.0 - d / P["tail"]
    c = lerp(P["tail_color"], P["color"], f ** 3) if d > 0.5 else P["color"]
    return lerp(P["background"], c, f * f)


@effect("wipe", "Colour wipe", [
    Param("colors", "colors", ["#ff0000", "#00ff00", "#0000ff"]),
    Param("hold", "number", 1.0, "Hold (s)", 0, 60, 0.5),
    P_speed(20, 0.5, 200), P_axis(),
], "Fills the lights with each colour in turn.")
def wipe(t, p, P):
    s, span = coord(p, P["axis"])
    cols = P["colors"]
    cycle = span + P["hold"] * P["speed"]          # distance per fill, including the hold
    d = abs(travel(t, P))
    k = int(d // cycle)
    filled = d - k * cycle
    cov = min(1.0, max(0.0, filled - s + 0.5))        # soft, gliding edge
    return lerp(cols[(k - 1) % len(cols)], cols[k % len(cols)], cov)


@effect("wave", "Sine wave", [
    Param("color", "color", "#00a0ff"),
    Param("color2", "color", "#000000", "Trough colour"),
    Param("wavelength", "number", 20, "Wavelength (bulbs)", 2, 500, 1),
    Param("sharpness", "number", 1, "Sharpness", 0.2, 8, 0.1, help=">1 makes narrower crests"),
    P_speed(8), P_axis(),
], "A travelling sine wave between two colours.")
def wave(t, p, P):
    s, _ = coord(p, P["axis"])
    ph = (s - travel(t, P)) / P["wavelength"]
    v = 0.5 + 0.5 * math.sin(2 * math.pi * ph)
    return lerp(P["color2"], P["color"], v ** P["sharpness"])


@effect("twinkle", "Twinkle", [
    Param("colors", "colors", ["#ffffff", "#ffd38a"]),
    Param("background", "color", "#000000"),
    Param("density", "number", 0.25, "Density", 0.01, 1, 0.01, help="share of bulbs lit at once"),
    Param("duration", "number", 1.5, "Twinkle length (s)", 0.1, 20, 0.1),
    Param("seed", "int", 1, "Seed", 0, 9999, 1),
], "Bulbs fade in and out at random.")
def twinkle(t, p, P):
    dur = P["duration"]
    ph = hash01(p.i, P["seed"], 7) * dur
    k = math.floor((t + ph) / dur)
    if hash01(p.i, k, P["seed"]) >= P["density"]:
        return P["background"]
    f = (t + ph) / dur - k
    env = math.sin(math.pi * f) ** 2
    cols = P["colors"]
    c = cols[int(hash01(p.i, k, P["seed"], 3) * len(cols))]
    return lerp(P["background"], c, env)


_sparkle_memo = threading.local()   # per-thread: engine and thumbnails don't share


@effect("sparkle", "Sparkle", [
    Param("color", "color", "#ffffff"),
    Param("background", "color", "#000000"),
    Param("rate", "number", 6, "Flashes per second", 0.2, 100, 0.2),
    Param("decay", "number", 0.25, "Decay (s)", 0.02, 3, 0.01),
    Param("seed", "int", 3, "Seed", 0, 9999, 1),
], "Quick random flashes; good as an 'add' layer over another effect.")
def sparkle(t, p, P):
    # time is cut into slots; each slot flashes a few random pixels
    # the slots are shared by every bulb, so work out once per frame which bulb each
    # recent slot lit (O(look + n) per frame rather than O(look * n))
    slot = 1.0 / P["rate"]
    look = max(1, int(P["decay"] * 4 / slot) + 1)
    key = (t, P["rate"], P["decay"], P["seed"], p.n)
    m = getattr(_sparkle_memo, "m", None)
    if m is None or m[0] != key:
        lit: dict = {}
        k0 = math.floor(t / slot)
        for k in range(k0, k0 - look, -1):
            i = int(hash01(k, P["seed"]) * p.n)
            v = math.exp(-(t - k * slot) / P["decay"])
            if v > lit.get(i, 0.0):
                lit[i] = v
        m = (key, lit)
        _sparkle_memo.m = m
    return lerp(P["background"], P["color"], m[1].get(p.i, 0.0))


@effect("candy_cane", "Candy-cane stripes", [
    Param("colors", "colors", ["#ff0000", "#ffffff"]),
    Param("width", "number", 3, "Stripe width (bulbs)", 1, 100, 1),
    Param("blend", "number", 0, "Edge softness", 0, 1, 0.05),
    P_speed(2), P_axis(),
    Param("crisp", "bool", False, "Hard edges",
          help="no one-bulb softening at the stripe edges (no pink in-between bulbs); stripes step bulb by bulb"),
], "Moving stripes of repeating colours.")
def candy_cane(t, p, P):
    s, _ = coord(p, P["axis"])
    x = (s - travel(t, P)) / P["width"]
    cols = P["colors"]
    k = math.floor(x)
    f = x - k
    c = cols[k % len(cols)]
    b = min(0.5, max(P["blend"], 0.5 / P["width"]))    # at least one bulb of edge softening, never past the middle
    if P.get("crisp"):
        b = min(0.5, P["blend"])
        if b <= 0:
            return c
    if f > 1 - b:
        return lerp(c, cols[(k + 1) % len(cols)], (f - (1 - b)) / b * 0.5)
    if f < b:
        return lerp(c, cols[(k - 1) % len(cols)], (b - f) / b * 0.5)
    return c


FIRE = [hex_to_rgb(c) for c in ("#000000", "#3a0000", "#a01000", "#ff4000", "#ff9a10", "#ffe070")]


@effect("fire", "Fire", [
    Param("colors", "colors", ["#3a0000", "#a01000", "#ff4000", "#ff9a10", "#ffe070"], help="cold to hot"),
    Param("intensity", "number", 1.0, "Intensity", 0.2, 2, 0.05),
    Param("scale", "number", 4, "Flame size (bulbs)", 0.5, 40, 0.5),
    Param("speed", "number", 1.5, "Flicker speed", 0.1, 10, 0.1),
    Param("rise", "number", 0.0, "Rise", 0, 1, 0.05,
          help="0 = flicker everywhere; >0 cools toward the top of each run (use with axis y on the peak)"),
    Param("seed", "int", 5, "Seed", 0, 9999, 1),
    P_axis(),
], "Flickering flames from smooth noise.")
def fire(t, p, P):
    s, _ = coord(p, P["axis"])
    sc = P["scale"]
    if P["axis"] == "y":
        # flames move upward: sample noise along x, scroll the y lookup with time
        n = fbm2(p.sx / sc, (p.sy - P["speed"] * 4 * t) / sc, 3, P["seed"])
    else:
        n = fbm2(s / sc, t * P["speed"], 3, P["seed"])
    heat = clamp((n - 0.25) * 1.8 * P["intensity"])
    if P["rise"] > 0:
        heat *= clamp(1.0 - p.rny * P["rise"] * 1.2)
    return palette([BLACK, *P["colors"]], heat, cyclic=False)


@effect("breathe", "Breathing", [
    Param("colors", "colors", ["#ff2020"], help="each breath uses the next colour"),
    Param("period", "number", 4, "Breath length (s)", 0.3, 60, 0.1),
    Param("min", "number", 0.05, "Min brightness", 0, 1, 0.01),
], "Whole run fades up and down.")
def breathe(t, p, P):
    per = P["period"]
    k = int(t // per)
    f = 0.5 - 0.5 * math.cos(2 * math.pi * (t / per))
    cols = P["colors"]
    return scale(cols[k % len(cols)], P["min"] + (1 - P["min"]) * f)


@effect("rainbow", "Rainbow", [
    Param("wavelength", "number", 60, "Length of one rainbow (bulbs)", 2, 2000, 1),
    Param("saturation", "number", 1, "Saturation", 0, 1, 0.01),
    P_speed(10), P_axis(),
], "Scrolling rainbow.")
def rainbow(t, p, P):
    s, _ = coord(p, P["axis"])
    return hsv((s - travel(t, P)) / P["wavelength"], P["saturation"], 1.0)


@effect("gradient", "Gradient drift", [
    Param("colors", "colors", ["#ff3000", "#ff00a0", "#4000ff"]),
    Param("length", "number", 80, "Gradient length (bulbs)", 2, 2000, 1),
    P_speed(2), P_axis(),
], "Smooth blend through colours, slowly drifting.")
def gradient(t, p, P):
    s, _ = coord(p, P["axis"])
    return palette(P["colors"], (s - travel(t, P)) / P["length"])


@effect("alternate", "Alternating colours", [
    Param("colors", "colors", ["#ff0000", "#00c000"]),
    Param("group", "int", 1, "Bulbs per colour", 1, 50, 1),
    Param("period", "number", 2, "Swap every (s)", 0, 60, 0.1, help="0 = never swap"),
    Param("fade", "number", 0.3, "Crossfade (s)", 0, 30, 0.05),
    P_axis(),
], "Groups of bulbs in alternating colours that step or fade along.")
def alternate(t, p, P):
    s, _ = coord(p, P["axis"])
    cols = P["colors"]
    g = int(s // P["group"])
    per = P["period"]
    if per <= 0:
        return cols[g % len(cols)]
    k = int(t // per)
    a = cols[(g + k) % len(cols)]
    fade = min(P["fade"], per)
    into = t - k * per
    if fade > 0 and into > per - fade:
        return lerp(a, cols[(g + k + 1) % len(cols)], (into - (per - fade)) / fade)
    return a


@effect("scanner", "Scanner", [
    Param("color", "color", "#ff0000"),
    Param("background", "color", "#000000"),
    Param("width", "number", 6, "Eye width (bulbs)", 1, 60, 0.5),
    P_speed(30, 1, 200), P_axis("run"),
], "A light bouncing back and forth.")
def scanner(t, p, P):
    s, span = coord(p, P["axis"])
    span = max(1.0, span - 1)
    f = (travel(t, P) / span) % 2.0
    pos = (f if f <= 1 else 2 - f) * span
    v = max(0.0, 1 - abs(s - pos) / P["width"])
    return lerp(P["background"], P["color"], v * v)


@effect("ripple", "Ripple", [
    Param("colors", "colors", ["#00a0ff", "#000000", "#ffffff", "#000000"]),
    Param("wavelength", "number", 12, "Ring spacing", 2, 100, 0.5),
    Param("sharpness", "number", 0, "Ring sharpness", 0, 8, 0.1, help="0 = smooth colour rings, higher = thin bright rings"),
    P_speed(8, -60, 60, "Speed (outward)"), *P_center(),
], "Rings spreading out from a point (default: the front gable apex).")
def ripple(t, p, P):
    _, _, d = dist_from_center(p, P)
    f = (d - travel(t, P)) / P["wavelength"]
    c = palette(P["colors"], f)
    if P["sharpness"] > 0:
        c = scale(c, (0.5 + 0.5 * math.cos(2 * math.pi * f)) ** P["sharpness"])
    return c


@effect("sweep", "Sweep", [
    Param("colors", "colors", ["#ffffff", "#000000", "#000000"]),
    Param("arms", "int", 1, "Arms", 1, 12, 1),
    P_speed(20, -240, 240, "Speed (turns/min)"), *P_center(),
], "A beam rotating around a point, like a lighthouse.")
def sweep(t, p, P):
    dx, dy, _ = dist_from_center(p, P)
    a = (math.atan2(dy, dx) / (2 * math.pi)) % 1.0
    return palette(P["colors"], a * P["arms"] - travel(t, P) / 60.0)


@effect("plasma", "Plasma", [
    Param("colors", "colors", ["#ff0080", "#ff8000", "#00c0ff", "#8000ff"]),
    Param("scale", "number", 12, "Blob size (bulbs)", 2, 80, 0.5),
    P_speed(3, -20, 20, "Flow"),
], "Swirling colour blobs across the whole house.")
def plasma(t, p, P):
    x, y, sc = p.sx / P["scale"], p.sy * 2 / P["scale"], travel(t, P) * 0.1
    v = (math.sin(x + sc) + math.sin(y * 1.3 - sc * 0.7) + math.sin((x + y) * 0.7 + sc * 0.5)
         + math.sin(math.hypot(x - 3, y - 1) - sc * 1.1))
    return palette(P["colors"], v / 8 + 0.5)


@effect("aurora", "Aurora", [
    Param("colors", "colors", ["#00ff80", "#00a0ff", "#8000ff", "#00ff40"]),
    Param("scale", "number", 15, "Curtain size (bulbs)", 2, 80, 0.5),
    Param("contrast", "number", 1.5, "Contrast", 0.5, 4, 0.1),
    P_speed(4, -20, 20, "Drift"),
], "Slow drifting curtains of colour.")
def aurora(t, p, P):
    tr = travel(t, P) * 0.05
    n = fbm2(p.sx / P["scale"] + tr, p.sy / P["scale"] * 2 + t * 0.03, 3, 11)
    v = clamp((n - 0.5) * P["contrast"] + 0.5)
    c = palette(P["colors"], fbm2(p.sx / (P["scale"] * 3) - tr * 0.5, 0.0, 2, 5) * 2)
    return scale(c, v * v)


@effect("bursts", "Bursts", [
    Param("colors", "colors", ["#ff2020", "#20ff40", "#4060ff", "#ffd020", "#ffffff"]),
    Param("interval", "number", 1.2, "Time between bursts (s)", 0.1, 10, 0.1),
    Param("life", "number", 1.5, "Burst life (s)", 0.2, 6, 0.1),
    Param("burst_speed", "number", 12, "Burst speed (bulbs/s)", 1, 60, 0.5),
    Param("ring", "number", 2.5, "Ring width", 0.5, 10, 0.5),
    Param("seed", "int", 9, "Seed", 0, 9999, 1),
], "Fireworks: rings bursting from random spots.")
def bursts(t, p, P):
    iv, life = P["interval"], P["life"]
    k0 = math.floor(t / iv)
    best = BLACK
    for k in range(k0, k0 - min(8, int(life / iv) + 2), -1):
        age = t - k * iv
        if age < 0 or age > life:
            continue
        cx = hash01(k, P["seed"], 1) * p.spanx
        cy = hash01(k, P["seed"], 2) * p.spany
        d = math.hypot(p.sx - cx, (p.sy - cy) * 2)
        r = age * P["burst_speed"]
        v = max(0.0, 1 - abs(d - r) / P["ring"]) * (1 - age / life) ** 1.5
        if v > 0:
            cols = P["colors"]
            c = scale(cols[int(hash01(k, P["seed"], 3) * len(cols))], v)
            best = (max(best[0], c[0]), max(best[1], c[1]), max(best[2], c[2]))
    return best


# ------------------------------------------------------------ sparkle family
from .sparkle import envelope, glint, glint_bloom, wobble  # noqa: E402


def P_glint(density=0.5, attack=0.03, decay=0.25):
    return [Param("density", "number", density, "Glints per bulb per second", 0.01, 6, 0.01),
            Param("attack", "number", attack, "Flash attack (s)", 0.005, 0.5, 0.005),
            Param("decay", "number", decay, "Flash decay (s)", 0.02, 2, 0.01),
            Param("bloom", "number", 0.35, "Bloom into neighbours", 0, 1, 0.05)]


def glint_color(base: Color, spark: Color, whiteness: float, g: float) -> Color:
    """A glint is hot (sparkle colour) at its peak and cools to the base colour."""
    m = max(base) or 1.0
    full = (base[0] / m, base[1] / m, base[2] / m)          # base hue at full brightness
    hot = lerp(full, spark, whiteness * g)
    return (hot[0] * g, hot[1] * g, hot[2] * g)


@effect("glitter", "Glitter", [
    Param("colors", "colors", ["#ffb030", "#ff6a10", "#ffd890"], help="the fabric under the glitter"),
    Param("sparkle", "color", "#ffffff", "Sparkle colour"),
    Param("whiteness", "number", 0.8, "Hot white core", 0, 1, 0.05),
    Param("level", "number", 0.22, "Base brightness", 0, 1, 0.01),
    Param("shimmer", "number", 0.5, "Base shimmer", 0, 1, 0.05),
    *P_glint(0.6, 0.02, 0.22),
    Param("length", "number", 60, "Colour spread (bulbs)", 2, 500, 1),
    P_speed(2, -30, 30, "Colour drift"),
    Param("seed", "int", 4, "Seed", 0, 9999, 1),
], "Professional glitter: a shimmering base with hot flashes that cool into its colour.")
def glitter(t, p, P):
    s = float(p.i)
    base = palette(P["colors"], (s - travel(t, P)) / P["length"])
    w = 1 - P["shimmer"] + P["shimmer"] * wobble(p.i, t, 0.7, P["seed"])
    g, _ = glint_bloom(p, t, P["density"], P["attack"], P["decay"], P["seed"], P["bloom"])
    b = scale(base, P["level"] * w)
    hot = glint_color(base, P["sparkle"], P["whiteness"], g)
    return (b[0] + hot[0], b[1] + hot[1], b[2] + hot[2])


@effect("shimmer", "Shimmer", [
    Param("colors", "colors", ["#d8e8ff", "#7fb0ff", "#ffffff"]),
    Param("level", "number", 0.3, "Base brightness", 0, 1, 0.01),
    Param("width", "number", 8, "Sheen width (bulbs)", 1, 60, 0.5),
    Param("spacing", "number", 70, "Sheen spacing (bulbs)", 10, 400, 1),
    Param("flicker", "number", 0.6, "Flicker depth", 0, 1, 0.05),
    Param("flicker_rate", "number", 6, "Flicker speed", 0.5, 30, 0.5),
    Param("density", "number", 3, "Glints inside the sheen /s", 0, 12, 0.1),
    Param("decay", "number", 0.15, "Glint decay (s)", 0.02, 1, 0.01),
    P_speed(18), P_axis(),
    Param("seed", "int", 8, "Seed", 0, 9999, 1),
], "Bands of light sweep across like light catching sequins, flickering and glinting inside.")
def shimmer(t, p, P):
    s, _ = coord(p, P["axis"])
    sp = P["spacing"]
    x = ((s - travel(t, P)) / sp) % 1.0
    u = (x - 0.5) * sp / P["width"]
    sheen = math.exp(-u * u * 2.5)
    fl = 1 - P["flicker"] + P["flicker"] * wobble(p.i, t, P["flicker_rate"], P["seed"])
    base = palette(P["colors"], p.i / 37.0)
    v = P["level"] + (1 - P["level"]) * sheen * fl
    g, _ = glint(p.i, t, P["density"] * sheen, 0.01, P["decay"], P["seed"]) if sheen > 0.05 else (0.0, 0)
    c = scale(base, v)
    return (c[0] + g, c[1] + g, c[2] + g)


@effect("sequins", "Sequins", [
    Param("colors", "colors", ["#ffd000", "#ff0060"], help="the two sides of each sequin"),
    Param("spacing", "number", 90, "Brush stroke spacing (bulbs)", 10, 500, 1),
    Param("ragged", "number", 6, "Ragged edge (bulbs)", 0, 40, 0.5),
    Param("flash", "number", 0.9, "Flip flash", 0, 1, 0.05),
    Param("decay", "number", 0.3, "Flash decay (s)", 0.03, 2, 0.01),
    Param("level", "number", 0.55, "Resting brightness", 0.05, 1, 0.01),
    P_speed(12, -60, 60, "Brush speed"), P_axis(),
    Param("seed", "int", 2, "Seed", 0, 9999, 1),
], "A brush sweeps along flipping sequins between two colours, each catching the light as it turns.")
def sequins(t, p, P):
    s, _ = coord(p, P["axis"])
    s += (hash01(p.i, P["seed"]) - 0.5) * 2 * P["ragged"]
    d = travel(t, P) - s
    n = math.floor(d / P["spacing"])
    cols = P["colors"]
    c = cols[n % len(cols)]
    v = P["speed"]
    age = ((d - n * P["spacing"]) / abs(v)) if v else 99.0
    g = envelope(age, 0.02, P["decay"]) * P["flash"]
    base = scale(c, P["level"])
    hot = glint_color(c, (1.0, 1.0, 1.0), 0.7, g)
    return (base[0] + hot[0], base[1] + hot[1], base[2] + hot[2])


@effect("fizz", "Champagne fizz", [
    Param("colors", "colors", ["#ffcf70", "#fff2c8"]),
    Param("level", "number", 0.12, "Glow", 0, 1, 0.01),
    Param("rise", "number", 3.0, "Rise speed (units/s)", 0.3, 20, 0.1),
    Param("columns", "number", 1.2, "Bubble column width", 0.3, 6, 0.1),
    Param("density", "number", 0.8, "Bubbles per column /s", 0.05, 6, 0.05),
    Param("decay", "number", 0.12, "Bubble size (s)", 0.02, 1, 0.01),
    Param("seed", "int", 6, "Seed", 0, 9999, 1),
], "Bubbles rising up the zigzag (and twinkling along flat runs) like champagne.")
def fizz(t, p, P):
    col = int(p.sx / P["columns"])
    tt = t - p.sy / P["rise"]                    # the same bubble reaches higher rows later
    g, key = glint(col * 131 + sum(map(ord, p.run)) % 97, tt, P["density"], 0.02, P["decay"], P["seed"], 1)
    cols = P["colors"]
    c = cols[key % len(cols)] if cols else (1.0, 0.8, 0.4)
    base = scale(cols[0], P["level"] * (0.7 + 0.3 * wobble(p.i, t, 0.5, P["seed"])))
    return (base[0] + c[0] * g, base[1] + c[1] * g, base[2] + c[2] * g)


@effect("fireflies", "Fireflies", [
    Param("colors", "colors", ["#c8ff40", "#ffd040"]),
    Param("density", "number", 0.06, "Fireflies per bulb /s", 0.005, 1, 0.005),
    Param("life", "number", 2.2, "Glow length (s)", 0.3, 8, 0.1),
    Param("drift", "number", 2.0, "Drift (bulbs)", 0, 8, 0.5),
    Param("snap", "number", 0.0, "Snap on", 0, 1, 0.05,
          help="0 = fade in as slowly as out; 1 = flash on almost at once, then fade slowly"),
    Param("seed", "int", 12, "Seed", 0, 9999, 1),
], "Soft glowing points that fade in, drift a little and fade out.")
def fireflies(t, p, P):
    best = BLACK
    reach = int(P["drift"]) + 1
    for q in range(p.ri - reach, p.ri + reach + 1):
        if q < 0 or q > p.rn - 1:
            continue                                     # stay inside this run
        j = p.seq[q]                                     # its bulb (the Tip carries on from the Peak)
        vj = p.i + (q - p.ri)                            # where it sits along the run, in strip units
        L = P["life"] * 1.3
        k = math.floor((t + hash01(j, P["seed"], 1) * L) / L)
        if hash01(j, k, P["seed"]) >= min(1.0, P["density"] * L):
            continue
        age = (t + hash01(j, P["seed"], 1) * L) - k * L
        f = age / P["life"]
        if f > 1:
            continue
        pk = 0.5 - 0.42 * P["snap"]                       # when the glow peaks (0.5: symmetric)
        env = (math.sin(0.5 * math.pi * f / pk) if f < pk
               else math.sin(0.5 * math.pi * (1.0 - f) / (1.0 - pk))) ** 2
        center = vj + (hash01(j, k, P["seed"], 2) - 0.5) * 2 * P["drift"] * f
        v = env * max(0.0, 1 - abs(p.i - center))
        if v > 0:
            cols = P["colors"]
            c = scale(cols[int(hash01(j, k, 3) * len(cols))], v)
            best = (max(best[0], c[0]), max(best[1], c[1]), max(best[2], c[2]))
    return best


@effect("off", "Off (black)", [])
def off(t, p, P):
    return BLACK


# ------------------------------------------------------------ paint, pictures, countdown
import time as _time  # noqa: E402

from .pictures import PICTURE_COLORS, PICTURES, sample as pic_sample, text_bitmap  # noqa: E402


@effect("paint", "Paint", [
    Param("pixels", "pixels", {}, help="painted bulbs: {virtual index: colour}"),
    Param("background", "color", "#000000"),
], "Your own finger painting.")
def paint(t, p, P):
    c = P["pixels"].get(p.i)
    return c if c is not None else P["background"]


def _lubdub(u: float) -> float:
    """A heartbeat over one period (0..1): a strong beat, a softer one just after, rest."""
    a = math.exp(-((u - 0.06) / 0.05) ** 2)
    b = 0.7 * math.exp(-((u - 0.26) / 0.06) ** 2)
    return max(a, b)


@effect("picture", "Picture", [
    Param("image", "choice", "tree", options=list(PICTURES)),
    Param("colors", "colors", [], help="leave empty for the picture's own colours"),
    Param("background", "color", "#000000"),
    Param("width", "number", 1.0, "Width", 0.4, 1.5, 0.05),
    Param("pulse", "number", 0.0, "Pulse", 0, 1, 0.05),
    Param("period", "number", 2.0, "Pulse speed (s)", 0.3, 10, 0.1),
    Param("beat", "number", 0.0, "Heartbeat", 0, 1, 0.05,
          help="the picture swells and shrinks from its centre, lub-dub, once per pulse period"),
], "A small picture (tree, star, heart, pumpkin…) drawn on the zigzag. Target the Peak.")
def picture(t, p, P):
    w = P["width"]
    if P["beat"] > 0:
        w *= 1.0 - P["beat"] * 0.3 * (1.0 - _lubdub((t / P["period"]) % 1.0))
    ch = pic_sample(PICTURES[P["image"]], p.rnx, p.rny, w)
    if ch == ".":
        return P["background"]
    cols = P["colors"] or [hex_to_rgb(h) for h in PICTURE_COLORS.get(P["image"], ["#ffffff"])]
    c = cols[(int(ch) - 1) % len(cols)]
    if P["pulse"] > 0:
        c = scale(c, 1 - P["pulse"] * (0.5 - 0.5 * math.cos(2 * math.pi * t / P["period"])))
    return c


@effect("countdown", "Countdown", [
    Param("target", "number", 0, "Target time (unix seconds; 0 = demo loop)"),
    Param("colors", "colors", ["#ffffff", "#ffd000", "#ff2050"], help="digits, eaves fill, final flash"),
    Param("seconds", "int", 10, "Count the last (s)", 3, 99, 1),
    Param("part", "choice", "digits", options=["digits", "fill"],
          help="digits: big numbers (target the Peak); fill: a bar that fills up (target the eaves)"),
], "Counts down to a moment: big digits on the zigzag while the eaves fill up, then a flash at zero.")
def countdown(t, p, P):
    tgt = P["target"]
    remaining = (tgt - _time.time()) if tgt else (P["seconds"] - (t % (P["seconds"] + 2)))
    cols = P["colors"]
    if remaining <= 0:                              # the moment: white flash fading over 2 s
        return scale(cols[-1], max(0.0, 1 + remaining / 2.0))
    if P["part"] == "digits":
        n = int(math.ceil(remaining))
        if n > P["seconds"]:
            return scale(cols[0], 0.08)
        bmp = text_bitmap(str(n))
        ch = pic_sample(bmp, p.rnx, p.rny, min(1.0, len(bmp[0]) / 9))
        frac = remaining - math.floor(remaining)    # each digit fades as its second passes
        return scale(cols[0], 0.6 + 0.4 * frac) if ch != "." else BLACK
    fill = 1 - min(1.0, remaining / max(1, P["seconds"]))
    return cols[1 % len(cols)] if p.ru <= fill else scale(cols[1 % len(cols)], 0.05)


@effect("digits", "Number", [
    Param("value", "int", 24, "Number", 0, 99, 1),
    Param("colors", "colors", ["#ffffff"]),
    Param("pulse", "number", 0.3, "Pulse", 0, 1, 0.05),
], "A number on the zigzag (e.g. days to go). Target the Peak.")
def digits(t, p, P):
    bmp = text_bitmap(str(P["value"]))
    ch = pic_sample(bmp, p.rnx, p.rny, min(1.0, len(bmp[0]) / 9))
    if ch == ".":
        return BLACK
    return scale(P["colors"][0], 1 - P["pulse"] * (0.5 - 0.5 * math.cos(2 * math.pi * t / 1.5)))


# ------------------------------------------------------------ weather overlays
@effect("snowfall", "Snowfall", [
    Param("color", "color", "#ffffff"),
    Param("density", "number", 0.6, "Flakes", 0.05, 3, 0.05),
    Param("fall", "number", 1.2, "Fall speed", 0.2, 6, 0.1),
    Param("seed", "int", 14, "Seed", 0, 9999, 1),
], "Snowflakes drifting down the zigzag and settling as twinkles along the eaves.")
def snowfall(t, p, P):
    col = int(p.sx / 1.3)
    tt = t + p.sy / P["fall"]                    # higher bulbs see the flake first
    g, _ = glint(col * 977 + 13, tt, P["density"] * 0.4, 0.15, 0.25, P["seed"], 1)
    return scale(P["color"], g)


@effect("rain", "Rain", [
    Param("color", "color", "#4080ff"),
    Param("density", "number", 1.5, "Drops", 0.1, 6, 0.1),
    Param("seed", "int", 15, "Seed", 0, 9999, 1),
], "Quick blue drops streaking down and spattering.")
def rain(t, p, P):
    col = int(p.sx / 1.0)
    tt = t + p.sy / 8.0
    g, _ = glint(col * 641 + 7, tt, P["density"] * 0.5, 0.01, 0.08, P["seed"], 1)
    return scale(P["color"], g)


@effect("lightning", "Lightning", [
    Param("color", "color", "#e0e8ff"),
    Param("interval", "number", 8, "Average gap (s)", 1, 60, 0.5),
    Param("seed", "int", 16, "Seed", 0, 9999, 1),
], "Rare, sudden multi-flicker flashes across part of the house, like a storm.")
def lightning(t, p, P):
    iv = P["interval"]
    k = math.floor(t / iv)
    start = k * iv + hash01(k, P["seed"]) * iv * 0.6
    age = t - start
    if age < 0 or age > 0.9:
        return BLACK
    centre, width = hash01(k, P["seed"], 1), 0.25 + 0.5 * hash01(k, P["seed"], 2)
    if abs(p.u - centre) > width:
        return BLACK
    v = 0.0
    for j, (at, amp) in enumerate(((0.0, 1.0), (0.12, 0.6), (0.3, 0.9))):   # strobe-like double/triple strike
        v = max(v, amp * math.exp(-max(0.0, age - at) / 0.05) if age >= at else 0.0)
    return scale(P["color"], v)


# ------------------------------------------------------------ camera mapping code
MAP_ON, MAP_OFF, MAP_SLOT, MAP_BITS = 1.0, 1.0, 0.5, 11     # 10-bit id (up to 1024 bulbs) + parity


def map_cycle() -> float:
    return MAP_ON + MAP_OFF + MAP_BITS * 2 * MAP_SLOT + 0.5


@effect("mapcode", "Mapping code", [Param("level", "number", 0.35, "Brightness", 0.05, 1, 0.05)],
        "Each bulb blinks its own binary ID for camera mapping.")
def mapcode(t, p, P):
    """Cycle: all on, all off, then for each bit b: bulbs with bit b set, then the inverse."""
    on = (P["level"],) * 3
    x = t % map_cycle()
    if x < MAP_ON:
        return on
    x -= MAP_ON
    if x < MAP_OFF:
        return BLACK
    x -= MAP_OFF
    slot = int(x // MAP_SLOT)
    if slot >= MAP_BITS * 2:
        return BLACK
    b, inverse = divmod(slot, 2)
    code = p.i | ((bin(p.i).count("1") & 1) << (MAP_BITS - 1))     # id bits + parity bit
    bit = (code >> b) & 1
    return on if bit ^ inverse else BLACK


# ------------------------------------------------------------ what you'll see
# Plain-language descriptions shown in the app's pickers (they override the short
# ones above). Written as "what it looks like on the house".
LOOKS = {
    "solid": "Every bulb the same colour, holding still.",
    "chase": "Evenly spaced dots marching along the lights, like a theatre marquee.",
    "comet": "A bright head racing along with a tail that fades behind it. Several comets space themselves out.",
    "scanner": "A single glowing eye sliding back and forth along each run, like a car's scanner light.",
    "wipe": "Colour paints itself along the lights from one end, then the next colour paints over it.",
    "wave": "Soft bands of light and dark rolling along, like ripples on water seen from the side.",
    "candy_cane": "Crisp repeating stripes (red/white by default) sliding slowly along.",
    "alternate": "Bulbs alternate colours in groups; every few seconds the colours swap places, with a fade.",
    "gradient": "A smooth blend through the palette, stretched along the house and drifting slowly.",
    "rainbow": "The full rainbow laid along the lights and scrolling.",
    "ripple": "Rings spreading outward from a point (the front gable's apex by default), like a stone dropped in a pond.",
    "sweep": "A beam rotating around a point like a lighthouse or radar, so its light swings across the gable and eaves.",
    "plasma": "Big soft blobs of colour that swirl and merge, like a lava lamp spread over the house.",
    "aurora": "Slow drifting curtains of light that brighten and fade, like the northern lights.",
    "bursts": "Fireworks: rings of colour bursting from random spots and fading out.",
    "twinkle": "Random bulbs gently fade in and out, like stars.",
    "sparkle": "Quick sharp flashes on random single bulbs. Best on top of another layer.",
    "fire": "Flickering orange flames. With axis 'y' on the peak, the flames rise up the zigzag.",
    "breathe": "The whole layer slowly fades up and down, like breathing.",
    "glitter": "A dim shimmering base with hot white flashes that cool into the colour, like glitter catching light.",
    "shimmer": "Bright bands sweep across while bulbs inside them flicker and glint, like light passing over sequins.",
    "sequins": "A brush sweeps along flipping every bulb between two colours, each one flashing as it turns.",
    "fizz": "Tiny bright bubbles rising up the zigzag like champagne, twinkling along the flat eaves.",
    "fireflies": "Soft warm points that glow up, drift a bulb or two, and fade away.",
    "off": "Nothing: a dark layer. Useful as a mask or to blank part of the house.",
    "paint": "Exactly what you painted with your finger in paint mode.",
    "picture": "A little picture (tree, star, snowflake, heart, pumpkin, present, smile) drawn on the zigzag. Set 'Where' to the Peak.",
    "countdown": "Big digits counting down on the zigzag while the eaves fill up, then a bright flash at zero.",
    "digits": "A number shown on the zigzag, gently pulsing (used for 'days to go').",
    "snowfall": "Snowflakes drifting down the zigzag and twinkling along the eaves.",
    "rain": "Fast blue raindrops streaking down.",
    "lightning": "Every few seconds, a sudden flickering flash across part of the house.",
    "mapcode": "Each bulb blinks its own ID code (used by camera mapping).",
}
for _n, _d in LOOKS.items():
    if _n in EFFECTS:
        EFFECTS[_n].description = _d


# ------------------------------------------------------------ picker groups
GROUP_ORDER = ["Scenes", "Physics", "Music", "Sparkle & glitter", "Along the lights", "Across the house (2D)",
               "Text, pictures & numbers", "Weather", "Texture & time", "Utility"]
_GROUPS = {
    "Sparkle & glitter": ["glitter", "shimmer", "sequins", "fizz", "fireflies", "twinkle", "sparkle"],
    "Along the lights": ["solid", "chase", "comet", "scanner", "wipe", "wave", "candy_cane", "alternate",
                         "gradient", "rainbow"],
    "Across the house (2D)": ["ripple", "sweep", "plasma", "aurora", "bursts"],
    "Text, pictures & numbers": ["picture", "digits", "countdown", "paint"],
    "Weather": ["snowfall", "rain", "lightning"],
    "Texture & time": ["fire", "breathe"],
    "Utility": ["off", "mapcode"],
}
for _g, _names in _GROUPS.items():
    for _n in _names:
        if _n in EFFECTS and not EFFECTS[_n].group:
            EFFECTS[_n].group = _g
