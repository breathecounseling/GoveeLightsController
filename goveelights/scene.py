"""Scenes: a stack of effect layers combined with blend modes.

Preset JSON:
{
  "name": "Gradient + twinkle",
  "brightness": 1.0,
  "layers": [
    {"effect": "gradient", "params": {...}},
    {"effect": "twinkle", "params": {...}, "blend": "add", "opacity": 0.8,
     "target": "all" | "<run name>" | ["<run>", ...], "enabled": true}
  ]
}

Blend modes (layer colour c over what is below, b; opacity o):
  normal    b + (c - b) * o
  add       b + c * o
  multiply  b * lerp(1, c, o)
  screen    1 - (1 - b)(1 - c*o)
  lighten   max(b, c*o)
  mask      b * lerp(1, luminance(c), o)   - the layer's brightness gates what is below
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .color import BLACK, Color, luminance
from .effects import EFFECTS, Effect
from .layout import Layout

BLENDS = ["normal", "add", "multiply", "screen", "lighten", "mask"]


def blend(mode: str, b: Color, c: Color, o: float) -> Color:
    if mode == "add":
        return (b[0] + c[0] * o, b[1] + c[1] * o, b[2] + c[2] * o)
    if mode == "multiply":
        return (b[0] * (1 - o + c[0] * o), b[1] * (1 - o + c[1] * o), b[2] * (1 - o + c[2] * o))
    if mode == "screen":
        return (1 - (1 - b[0]) * (1 - c[0] * o), 1 - (1 - b[1]) * (1 - c[1] * o), 1 - (1 - b[2]) * (1 - c[2] * o))
    if mode == "lighten":
        return (max(b[0], c[0] * o), max(b[1], c[1] * o), max(b[2], c[2] * o))
    if mode == "mask":
        m = 1 - o + luminance(c) * o
        return (b[0] * m, b[1] * m, b[2] * m)
    return (b[0] + (c[0] - b[0]) * o, b[1] + (c[1] - b[1]) * o, b[2] + (c[2] - b[2]) * o)


@dataclass
class Layer:
    effect: str
    params: dict = field(default_factory=dict)
    blend: str = "normal"
    opacity: float = 1.0
    target: str | list[str] = "all"
    enabled: bool = True

    def __post_init__(self):
        if self.effect not in EFFECTS:
            raise ValueError(f"unknown effect {self.effect!r}; known: {', '.join(sorted(EFFECTS))}")
        if self.blend not in BLENDS:
            raise ValueError(f"unknown blend {self.blend!r}; known: {', '.join(BLENDS)}")
        self.fx: Effect = EFFECTS[self.effect]
        self.resolved = self.fx.resolve(self.params)

    def applies(self, run: str, mac: str) -> bool:
        tg = self.target
        if tg in ("all", "", None):
            return True
        tg = [tg] if isinstance(tg, str) else tg
        return run in tg or mac in tg

    def to_dict(self) -> dict:
        return {"effect": self.effect, "params": self.params, "blend": self.blend,
                "opacity": self.opacity, "target": self.target, "enabled": self.enabled}


@dataclass
class Scene:
    layers: list[Layer]
    name: str = "untitled"
    brightness: float = 1.0

    @classmethod
    def from_dict(cls, d: dict) -> "Scene":
        layers = [Layer(**{k: v for k, v in L.items() if k in
                           ("effect", "params", "blend", "opacity", "target", "enabled")})
                  for L in d.get("layers", [])]
        return cls(layers, d.get("name", "untitled"), float(d.get("brightness", 1.0)))

    @classmethod
    def load(cls, path: str | Path) -> "Scene":
        with open(path, encoding="utf-8") as f:
            return cls.from_dict(json.load(f))

    @classmethod
    def single(cls, effect: str, params: dict | None = None) -> "Scene":
        return cls([Layer(effect, params or {})], name=effect)

    def to_dict(self) -> dict:
        return {"name": self.name, "brightness": self.brightness, "layers": [L.to_dict() for L in self.layers]}

    def render(self, t: float, layout: Layout) -> list[Color]:
        """Colour for every visible pixel, in virtual-strip order (floats, clamped)."""
        out = []
        active = [L for L in self.layers if L.enabled]
        br = self.brightness
        for p in layout.pixels:
            c = BLACK
            for L in active:
                if L.applies(p.run, p.mac):
                    c = blend(L.blend, c, L.fx.fn(t, p, L.resolved), L.opacity)
            out.append((min(1.0, max(0.0, c[0] * br)), min(1.0, max(0.0, c[1] * br)), min(1.0, max(0.0, c[2] * br))))
        return out
