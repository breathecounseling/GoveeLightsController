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
from dataclasses import dataclass, field
from typing import Any, Callable

from .color import (BLACK, Color, clamp, fbm2, hash01, hex_to_rgb, hsv, lerp, palette, scale)
from .layout import Pixel


@dataclass
class Param:
    name: str
    kind: str                 # color | colors | number | int | bool | choice
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

    def resolve(self, params: dict | None) -> dict:
        """Merge with defaults, coerce types, convert colours to floats."""
        out: dict[str, Any] = {}
        params = params or {}
        for prm in self.params:
            v = params.get(prm.name, prm.default)
            try:
                if prm.kind == "color":
                    v = hex_to_rgb(v)
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
                "params": [p.to_dict() for p in self.params]}


EFFECTS: dict[str, Effect] = {}


def effect(name: str, label: str, params: list[Param], description: str = ""):
    def deco(fn):
        EFFECTS[name] = Effect(name, label, fn, params, description)
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
    sp = P["spacing"]
    k = math.floor(x / sp)
    if (x - k * sp) < P["width"]:
        cols = P["colors"]
        return cols[k % len(cols)]
    return P["background"]


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
    return cols[k % len(cols)] if s < filled else cols[(k - 1) % len(cols)]


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


@effect("sparkle", "Sparkle", [
    Param("color", "color", "#ffffff"),
    Param("background", "color", "#000000"),
    Param("rate", "number", 6, "Flashes per second", 0.2, 100, 0.2),
    Param("decay", "number", 0.25, "Decay (s)", 0.02, 3, 0.01),
    Param("seed", "int", 3, "Seed", 0, 9999, 1),
], "Quick random flashes; good as an 'add' layer over another effect.")
def sparkle(t, p, P):
    # time is cut into slots; each slot flashes a few random pixels
    slot = 1.0 / P["rate"]
    out = 0.0
    look = max(1, int(P["decay"] * 4 / slot) + 1)
    k0 = math.floor(t / slot)
    for k in range(k0, k0 - look, -1):
        if int(hash01(k, P["seed"]) * p.n) == p.i:
            age = t - k * slot
            out = max(out, math.exp(-age / P["decay"]))
    return lerp(P["background"], P["color"], out)


@effect("candy_cane", "Candy-cane stripes", [
    Param("colors", "colors", ["#ff0000", "#ffffff"]),
    Param("width", "number", 3, "Stripe width (bulbs)", 1, 100, 1),
    Param("blend", "number", 0, "Edge softness", 0, 1, 0.05),
    P_speed(2), P_axis(),
], "Moving stripes of repeating colours.")
def candy_cane(t, p, P):
    s, _ = coord(p, P["axis"])
    x = (s - travel(t, P)) / P["width"]
    cols = P["colors"]
    k = math.floor(x)
    f = x - k
    c = cols[k % len(cols)]
    b = P["blend"]
    if b > 0 and f > 1 - b:
        return lerp(c, cols[(k + 1) % len(cols)], (f - (1 - b)) / b)
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


@effect("off", "Off (black)", [])
def off(t, p, P):
    return BLACK
