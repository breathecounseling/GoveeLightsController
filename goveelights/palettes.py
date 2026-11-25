"""Palette library, grouped into categories.

Tuned for LEDs rather than screens: very dark colours read as "off" on a bulb
and pastels wash out toward white, so these favour saturated colours with
clear hue steps. A palette is an ordered list of colours; any effect with
colour params can take any palette, so one tap restyles a whole preset.

User palettes live in the config ("palettes": {name: ["#rrggbb", ...]}) and
override built-ins of the same name; they appear under "Mine".
"""
from __future__ import annotations

CATEGORIES: dict[str, dict[str, list[str]]] = {
    "Classic": {
        "Rainbow": ["#ff0000", "#ff8000", "#ffff00", "#00ff00", "#00ffff", "#0000ff", "#8000ff"],
        "Warm white": ["#ffb060", "#ff9030", "#ffd090"],
        "Candlelight": ["#ff9329", "#ffb46b", "#ff7a1a"],
        "Cool white": ["#ffffff", "#d8e8ff", "#b8d0ff"],
        "Gold": ["#ffd700", "#ff9000", "#fff0b0"],
        "Fire": ["#3a0000", "#a01000", "#ff4000", "#ff9a10", "#ffe070"],
    },
    "Warm": {
        "Sunset": ["#ff2060", "#ff6000", "#ffb000", "#6020a0"],
        "Golden hour": ["#ff8a00", "#ffb347", "#ff5e3a", "#ffd27a"],
        "Ember": ["#ff2a00", "#ff6a00", "#ffa000", "#ff4500"],
        "Citrus": ["#ffd000", "#ff8c00", "#a8ff00", "#ffef5a"],
        "Desert": ["#ff7f3f", "#ffb07a", "#d9480f", "#ffd8a8"],
        "Autumn": ["#ff4500", "#ff8c00", "#c81e1e", "#ffb000"],
        "Spice market": ["#ff3d00", "#ffab00", "#c2185b", "#ff6f00"],
    },
    "Cool": {
        "Ocean": ["#001a60", "#0060a0", "#00c0c0", "#40ffd0"],
        "Icy": ["#ffffff", "#80c0ff", "#2060ff"],
        "Glacier": ["#a0e9ff", "#00b4ff", "#ffffff", "#5a8dff"],
        "Deep sea": ["#0010ff", "#0080ff", "#00ffd0", "#4000ff"],
        "Lagoon": ["#00ffc8", "#00c8ff", "#00ff7a", "#0096ff"],
        "Moonlight": ["#c8d8ff", "#6a8cff", "#ffffff", "#3050ff"],
        "Twilight": ["#3a00ff", "#8a2be2", "#0090ff", "#ff40c0"],
    },
    "Nature": {
        "Aurora": ["#00ff80", "#00a0ff", "#8000ff", "#00ff40"],
        "Forest": ["#00a000", "#4cff00", "#007a3d", "#b0ff60"],
        "Spring meadow": ["#7cff4d", "#ffe600", "#ff7ad9", "#4dd2ff"],
        "Tropical": ["#00ffa0", "#ff4fa0", "#ffd000", "#00b4ff"],
        "Coral reef": ["#ff5a5f", "#ff9e6d", "#00d1c1", "#ffd166"],
        "Lavender fields": ["#b57bff", "#7a5cff", "#ff9ff3", "#e0c3ff"],
        "Firefly": ["#ffe066", "#a0ff40", "#ffb000"],
        "Volcano": ["#ff0000", "#ff5500", "#ffaa00", "#550000"],
    },
    "Neon": {
        "Synthwave": ["#ff00d4", "#7a00ff", "#00e5ff", "#ff3c8e"],
        "Neon city": ["#ff0080", "#00ffff", "#ffe600", "#8000ff"],
        "Cyberpunk": ["#fcee0a", "#ff003c", "#00f0ff"],
        "Club": ["#ff0040", "#0040ff", "#ff00ff"],
        "Electric": ["#00a2ff", "#ffffff", "#7a00ff"],
        "Acid": ["#a0ff00", "#ff00a0", "#00ffe0", "#ffee00"],
        "Rave": ["#ff0000", "#00ff00", "#0000ff", "#ffff00", "#ff00ff", "#00ffff"],
    },
    "Elegant": {
        "Champagne": ["#ffd9a0", "#fff2d6", "#ffb347", "#ffffff"],
        "Rose gold": ["#ff9e80", "#ffcdb2", "#ff6f61", "#fff0e6"],
        "Platinum": ["#ffffff", "#cfd8ff", "#e6f0ff"],
        "Royal": ["#5b00ff", "#ffd700", "#b000ff"],
        "Emerald & gold": ["#00c060", "#ffd000", "#00ff90"],
        "Sapphire": ["#0030ff", "#6080ff", "#ffffff"],
        "Ruby": ["#ff0030", "#ff4060", "#ff9090"],
        "Amethyst": ["#9b00ff", "#d580ff", "#6000c0"],
        "Black tie": ["#ffffff", "#ffd700", "#c0c0ff"],
    },
    "Soft": {
        "Pastel": ["#ffb3ba", "#ffdfba", "#ffffba", "#baffc9", "#bae1ff"],
        "Cotton candy": ["#ff9ad5", "#9ad5ff", "#ffffff"],
        "Sorbet": ["#ff8c8c", "#ffc38c", "#ffe78c", "#8cffc3"],
        "Peach": ["#ffb38a", "#ff8a8a", "#ffd6a5"],
        "Mint": ["#80ffc0", "#c0fff0", "#40e0a0"],
    },
    "Holidays": {
        "Classic Xmas": ["#ff0000", "#00b000"],
        "Candy cane": ["#ff0000", "#ffffff"],
        "Winter wonderland": ["#ffffff", "#a0d8ff", "#4080ff", "#e0f0ff"],
        "Christmas gold": ["#ff0000", "#00b000", "#ffd000"],
        "Hanukkah": ["#0040ff", "#ffffff", "#80b0ff"],
        "New Year": ["#ffd700", "#ffffff", "#c0c0ff"],
        "Valentine": ["#ff0040", "#ff80a0", "#ffffff"],
        "Shamrock": ["#00c000", "#80ff40", "#ffd700"],
        "Easter": ["#ffb3de", "#b3e5ff", "#d0ffb3", "#fff0b3"],
        "Patriotic": ["#ff0000", "#ffffff", "#0030ff"],
        "Halloween": ["#ff5000", "#7000ff", "#40ff40"],
        "Thanksgiving": ["#ff6a00", "#b04000", "#ffc000", "#c02000"],
        "Orange & blue": ["#ff4f10", "#0030c0"],
    },
}

BUILTIN: dict[str, list[str]] = {n: c for cat in CATEGORIES.values() for n, c in cat.items()}


def all_palettes(user: dict | None = None) -> dict[str, list[str]]:
    out = dict(BUILTIN)
    for k, v in (user or {}).items():
        if isinstance(v, list) and v:
            out[k] = v
    return out


def categories(user: dict | None = None) -> dict[str, list[str]]:
    """Category -> palette names, with the user's own palettes first."""
    cats = {}
    mine = [n for n in (user or {}) if n not in BUILTIN]
    if mine:
        cats["Mine"] = mine
    for c, pals in CATEGORIES.items():
        cats[c] = list(pals)
    return cats
