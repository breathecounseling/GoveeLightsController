"""Scenes: a stack of layers. Each layer is a small rack:

    generator (effect)  ->  FX chain  ->  blend into the layers below

Preset JSON (all fields except "effect" optional; v1 presets still load):
{
  "name": "Comets with trails", "brightness": 1.0, "tags": ["Holiday"], "favorite": false,
  "layers": [
    {"effect": "comet", "params": {"speed": 25},
     "mod": {"speed": {"shape": "sine", "rate": 0.1, "depth": 0.4}},       # LFOs on params
     "palette": "Icy",                                                      # restyles colour params
     "fx": [{"type": "trails", "params": {"length": 0.8}, "mod": {}, "enabled": true}],
     "blend": "add", "opacity": 1.0, "target": "all" | "<run or zone>" | [...], "enabled": true}
  ]
}

Blend modes (layer colour c over what is below, b; opacity o):
  normal    b + (c - b) * o          add       b + c * o
  multiply  b * lerp(1, c, o)        screen    1 - (1 - b)(1 - c*o)
  lighten   max(b, c*o)              mask      b * lerp(1, luminance(c), o)

Rendering is stateful (speed is integrated into distance travelled, FX like
trails remember previous frames), but the first frame rendered at time t
matches the pure function at t, so previews and tests stay deterministic.
"""
from __future__ import annotations

import colorsys
import copy as _copy
import json
from dataclasses import dataclass, field
from pathlib import Path

from . import modulation
from .color import BLACK, Color, hex_to_rgb, luminance
from . import effects_ext  # noqa: F401  (registers plug-in effects)
from .effects import EFFECTS, Effect
from .fx import FX, Ctx
from .layout import Layout
from .palettes import all_palettes

BLENDS = ["normal", "add", "multiply", "screen", "lighten", "mask"]
BLEND_INFO = {
    "normal": "Covers the layers below. Lower the opacity to see through it.",
    "add": "Adds its light to what's below: lit parts get brighter and colours mix (red + green = yellow). Best for sparkles and comets over a base.",
    "lighten": "Each bulb shows whichever layer is brighter, so this layer's dark parts become see-through. Clean overlays without washing out.",
    "screen": "Like add but gentler: brightens without blowing out to white.",
    "multiply": "Darkens: the layer below only shows where this layer is bright, tinted by this layer's colour. Good for moving shadows.",
    "mask": "This layer becomes a stencil: the layers below only show where it's lit. Its colour is ignored. Use a wave or chase to cut moving shapes out of a gradient.",
}


def blend(mode: str, b: Color, c: Color, o: float) -> Color:
    if mode == "add":
        return (b[0] + c[0] * o, b[1] + c[1] * o, b[2] + c[2] * o)
    if mode == "multiply":
        return (b[0] * (1 - o + c[0] * o), b[1] * (1 - o + c[1] * o), b[2] * (1 - o + c[2] * o))
    if mode == "screen":        # the formula needs inputs in 0..1 (layer frames can exceed 1)
        b0, b1, b2 = (min(1.0, max(0.0, v)) for v in b)
        c0, c1, c2 = (min(1.0, max(0.0, v * o)) for v in c)
        return (1 - (1 - b0) * (1 - c0), 1 - (1 - b1) * (1 - c1), 1 - (1 - b2) * (1 - c2))
    if mode == "lighten":
        return (max(b[0], c[0] * o), max(b[1], c[1] * o), max(b[2], c[2] * o))
    if mode == "mask":
        m = 1 - o + luminance(c) * o
        return (b[0] * m, b[1] * m, b[2] * m)
    if mode == "over":          # overlays only (not offered for layers): brightness is opacity
        a = max(c[0], c[1], c[2], 0.0) * o
        if a <= 0:
            return b
        a = min(1.0, a)
        return (b[0] * (1 - a) + c[0] * o, b[1] * (1 - a) + c[1] * o, b[2] * (1 - a) + c[2] * o)
    return (b[0] + (c[0] - b[0]) * o, b[1] + (c[1] - b[1]) * o, b[2] + (c[2] - b[2]) * o)


PALETTE_KEEP = {"background", "detail"}


def apply_palette(fx: Effect, P: dict, pal: list[Color]):
    """Restyle an effect's colour params from a palette (backgrounds and details, e.g.
    ghost eyes, stay)."""
    singles = [p for p in fx.params if p.kind == "color" and p.name not in PALETTE_KEEP]
    for p in fx.params:
        if p.kind == "colors":
            P[p.name] = pal
    for k, p in enumerate(singles):
        P[p.name] = pal[k % len(pal)]


def resolve_mod(fx: Effect, base: dict, mods: dict, clock: float, bpm: float, seed: int) -> dict:
    if not mods:
        return base
    P = dict(base)
    for k, (name, spec) in enumerate(mods.items()):
        prm = next((p for p in fx.params if p.name == name), None)
        if prm and prm.kind in ("number", "int"):
            P[name] = modulation.apply(spec, base[name], prm.min, prm.max, clock, bpm, seed + k, prm.kind == "int")
    return P


@dataclass
class FxUnit:
    type: str
    params: dict = field(default_factory=dict)
    mod: dict = field(default_factory=dict)
    enabled: bool = True

    def __post_init__(self):
        if self.type not in FX:
            raise ValueError(f"unknown fx {self.type!r}; known: {', '.join(sorted(FX))}")
        self.fx = FX[self.type]
        self.resolved = self.fx.resolve(self.params)
        self.state: dict = {}

    def to_dict(self):
        return {"type": self.type, "params": self.params, "mod": self.mod, "enabled": self.enabled}


@dataclass
class Layer:
    effect: str
    params: dict = field(default_factory=dict)
    blend: str = "normal"
    opacity: float = 1.0
    target: str | list[str] = "all"
    enabled: bool = True
    mod: dict = field(default_factory=dict)
    fx: list = field(default_factory=list)
    palette: str = ""

    def __post_init__(self):
        if self.effect not in EFFECTS:
            raise ValueError(f"unknown effect {self.effect!r}; known: {', '.join(sorted(EFFECTS))}")
        if self.blend not in BLENDS:
            raise ValueError(f"unknown blend {self.blend!r}; known: {', '.join(BLENDS)}")
        self.gen: Effect = EFFECTS[self.effect]
        self.resolved = self.gen.resolve(self.params)
        self.units = [u if isinstance(u, FxUnit) else FxUnit(**{k: v for k, v in u.items()
                                                               if k in ("type", "params", "mod", "enabled")})
                      for u in (self.fx or [])]
        self.clock: float | None = None
        self.travel = 0.0
        self.state: dict = {}           # memory for frame effects (particles, snow…)

    # kept for callers of the v1 API
    @property
    def fx_effect(self) -> Effect:
        return self.gen

    def applies(self, run: str, mac: str) -> bool:
        tg = self.target
        if tg in ("all", "", None):
            return True
        tg = [tg] if isinstance(tg, str) else tg
        return "all" in tg or run in tg or mac in tg

    def to_dict(self) -> dict:
        d = {"effect": self.effect, "params": self.params, "blend": self.blend, "opacity": self.opacity,
             "target": self.target, "enabled": self.enabled}
        if self.mod:
            d["mod"] = self.mod
        if self.units:
            d["fx"] = [u.to_dict() for u in self.units]
        if self.palette:
            d["palette"] = self.palette
        return d


LAYER_KEYS = ("effect", "params", "blend", "opacity", "target", "enabled", "mod", "fx", "palette")


@dataclass
class Scene:
    layers: list[Layer]
    name: str = "untitled"
    brightness: float = 1.0
    tags: list[str] = field(default_factory=list)
    favorite: bool = False
    palettes: dict = field(default_factory=dict)     # user palettes (name -> hex list), set by the app

    def __post_init__(self):
        self._last_t: float | None = None
        self._cache: dict = {}

    @classmethod
    def from_dict(cls, d: dict, palettes: dict | None = None) -> "Scene":
        layers = [Layer(**{k: v for k, v in L.items() if k in LAYER_KEYS}) for L in d.get("layers", [])]
        return cls(layers, d.get("name", "untitled"), float(d.get("brightness", 1.0)),
                   list(d.get("tags", [])), bool(d.get("favorite", False)), palettes or {})

    @classmethod
    def load(cls, path: str | Path, palettes: dict | None = None) -> "Scene":
        with open(path, encoding="utf-8") as f:
            return cls.from_dict(json.load(f), palettes)

    @classmethod
    def single(cls, effect: str, params: dict | None = None) -> "Scene":
        return cls([Layer(effect, params or {})], name=effect)

    def to_dict(self) -> dict:
        d = {"name": self.name, "brightness": self.brightness, "layers": [L.to_dict() for L in self.layers]}
        if self.tags:
            d["tags"] = self.tags
        if self.favorite:
            d["favorite"] = True
        return d

    def adopt_state(self, old: "Scene | None", copy: bool = False):
        """Keep motion continuous when a scene is replaced by an edited copy.
        copy=True: take a deep copy of each layer's effect memory instead of sharing it
        (needed when the old scene keeps rendering during a crossfade, or one dict
        would be stepped twice per frame with two parameter sets)."""
        if not old:
            return
        dup = _copy.deepcopy if copy else (lambda v: v)
        self._last_t = old._last_t
        for a, b in zip(self.layers, old.layers):
            if a.effect == b.effect and b.clock is not None:
                a.clock, a.travel, a.state = b.clock, b.travel, dup(b.state)
                for ua, ub in zip(a.units, b.units):
                    if ua.type == ub.type:
                        ua.state = dup(ub.state)

    def _palette(self, name: str) -> list[Color] | None:
        if not name:
            return None
        key = ("pal", name)
        if key not in self._cache:
            hexes = all_palettes(self.palettes).get(name)
            cols = []
            for h in hexes or ():
                try:
                    cols.append(hex_to_rgb(h))
                except (ValueError, TypeError, AttributeError, IndexError):
                    pass                                # a bad user entry must not stop every frame
            self._cache[key] = cols or None
        return self._cache[key]

    def render(self, t: float, layout: Layout, master: dict | None = None) -> list[Color]:
        """Colour for every visible pixel, in virtual-strip order (floats, clamped).
        master: {"speed": 1, "hue": 0, "palette": "", "bpm": 120}"""
        m = master or {}
        ms = float(m.get("speed", 1.0))
        bpm = float(m.get("bpm", 120))
        master_pal = self._palette(m.get("palette", ""))
        dt = 0.0 if self._last_t is None else min(0.5, max(0.0, t - self._last_t))
        self._last_t = t
        n = layout.n
        out = [BLACK] * n
        px = layout.pixels
        ctx_cache = self._cache.setdefault("ctx", {})
        for li, L in enumerate(self.layers):
            if not L.enabled:
                continue
            idx = layout.indices(L.target)
            if not idx:
                continue
            ldt = dt * ms
            if L.clock is None:               # first frame: start "as if running since t=0"
                L.clock = t * ms
                L.travel = float(L.resolved.get("speed", 0.0)) * L.clock
                ldt = 0.0
            else:
                L.clock += ldt
            P = L.resolved
            pal = master_pal or self._palette(L.palette)
            if pal and L.blend == "mask" and li:
                pal = None                    # a mask's colours are a stencil, not a look
            if pal:
                P = dict(P)
                apply_palette(L.gen, P, pal)
            P = resolve_mod(L.gen, P, L.mod, L.clock, bpm, li * 7)
            if "speed" in P:
                L.travel += float(P["speed"]) * ldt
                if P is L.resolved:
                    P = dict(P)
                P["_travel"] = L.travel
            fn, clock = L.gen.fn, L.clock
            if L.gen.frame:
                frame = fn(clock, Ctx(layout, idx, clock, ldt, bpm, ctx_cache), P, L.state)
                if len(frame) != n:
                    frame = (list(frame) + [BLACK] * n)[:n]
            else:
                frame = [BLACK] * n
                for i in idx:
                    frame[i] = fn(clock, px[i], P)
            if L.units:
                ctx = Ctx(layout, idx, clock, ldt, bpm, ctx_cache)
                for ui, U in enumerate(L.units):
                    if U.enabled:
                        UP = U.resolved
                        if pal and any(p.kind == "colors" for p in U.fx.params):
                            UP = dict(UP)
                            apply_palette(U.fx, UP, pal)
                        UP = resolve_mod(U.fx, UP, U.mod, clock, bpm, li * 7 + ui * 3 + 101)
                        frame = U.fx.fn(frame, ctx, UP, U.state)
            mode, o = (L.blend if li else "normal"), L.opacity
            if mode == "normal" and o == 1.0:
                for i in idx:
                    out[i] = frame[i]
            else:
                for i in idx:
                    out[i] = blend(mode, out[i], frame[i], o)
        br = self.brightness
        hue = float(m.get("hue", 0.0)) % 1.0
        res = []
        for c in out:
            r, g, b = (min(1.0, max(0.0, c[0] * br)), min(1.0, max(0.0, c[1] * br)), min(1.0, max(0.0, c[2] * br)))
            if hue and (r or g or b):
                h, s, v = colorsys.rgb_to_hsv(r, g, b)
                r, g, b = colorsys.hsv_to_rgb((h + hue) % 1.0, s, v)
            res.append((r, g, b))
        return res
