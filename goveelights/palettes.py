"""Palette library. A palette is an ordered list of colours; effects with a
`colors` param can take any palette, so one tap restyles a whole preset.
User palettes live in the config ("palettes": {name: ["#rrggbb", ...]})
and override built-ins of the same name."""
from __future__ import annotations

BUILTIN: dict[str, list[str]] = {
    "Rainbow": ["#ff0000", "#ff8000", "#ffff00", "#00ff00", "#00ffff", "#0000ff", "#8000ff"],
    "Classic Xmas": ["#ff0000", "#00b000"],
    "Candy cane": ["#ff0000", "#ffffff"],
    "Warm white": ["#ffb060", "#ff9030", "#ffd090"],
    "Icy": ["#ffffff", "#80c0ff", "#2060ff"],
    "Fire": ["#3a0000", "#a01000", "#ff4000", "#ff9a10", "#ffe070"],
    "Ocean": ["#001a60", "#0060a0", "#00c0c0", "#40ffd0"],
    "Aurora": ["#00ff80", "#00a0ff", "#8000ff", "#00ff40"],
    "Halloween": ["#ff5000", "#7000ff", "#40ff40"],
    "Sunset": ["#ff2060", "#ff6000", "#ffb000", "#6020a0"],
    "Patriotic": ["#ff0000", "#ffffff", "#0030ff"],
    "Orange & blue": ["#ff4f10", "#0030c0"],
    "Valentine": ["#ff0040", "#ff80a0", "#ffffff"],
    "Shamrock": ["#00c000", "#80ff40", "#ffd700"],
    "Pastel": ["#ffb3ba", "#ffdfba", "#ffffba", "#baffc9", "#bae1ff"],
    "Gold": ["#ffd700", "#ff9000", "#fff0b0"],
}


def all_palettes(user: dict | None = None) -> dict[str, list[str]]:
    out = dict(BUILTIN)
    for k, v in (user or {}).items():
        if isinstance(v, list) and v:
            out[k] = v
    return out
