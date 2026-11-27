"""The text generator behind Play -> "Words on the Peak": type a phrase, pick one big letter
at a time or a marquee, and get a scene for 'Text on the Peak' (effects_ext/peak_text.py)
to preview, play or save as a preset.

    scene(opts)                 -> a scene dict (raises ValueError on nothing to show)
    preview(scene, layout, ...) -> {"fps", "frames", "partial"}: frames from the very start
                                   of the message (hex per bulb, as the preset thumbnails),
                                   up to 30 s; partial when the message runs longer
    unsupported(text)           -> the characters the font will skip

opts: text, mode ('single' | 'marquee'), colors (1-4 '#rrggbb'), color_mode, hold (s per
letter, single), speed (bulbs/s, marquee), name (for saving).
"""
from __future__ import annotations

import math
import re

from . import hexfont

CHARSET = "A-Z 0-9 ! ? - + . , ' : & # / and <3 for a heart"
MODES = ("single", "marquee")
COLOR_MODES = ("letters", "words", "solid", "rainbow")
MAX_LEN = 60
TARGET = ["Peak", "Eaves", "Accent"]       # letters on the Peak; the eaves and porch keep time
PAUSE = 1.6                                # peak_text's default pause at the end of the message
DWELL = 0.6                                # peak_text's default stop on each marquee letter
PREVIEW_MAX = 30.0                         # seconds of preview; a longer phrase says "first 30 s"
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def unsupported(text: str) -> list[str]:
    s = str(text or "").upper().replace("<3", "")
    return sorted({ch for ch in s if ch not in hexfont.GLYPHS and not ch.isspace()})


def _num(v, lo, hi, d):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return d
    return d if math.isnan(v) else max(lo, min(hi, v))


def scene(opts: dict) -> dict:
    text = " ".join(str(opts.get("text") or "").split())[:MAX_LEN]
    if not hexfont.normalise(text).strip():
        raise ValueError(f"type something to show: {CHARSET}")
    mode = opts.get("mode") if opts.get("mode") in MODES else "single"
    cols = [c for c in (opts.get("colors") or []) if isinstance(c, str) and _HEX.match(c)][:4] or ["#ff1a1a", "#00d040"]
    cm = opts.get("color_mode") if opts.get("color_mode") in COLOR_MODES else "letters"
    params = {"text": text, "mode": mode, "colors": cols, "color_mode": cm,
              "hold": round(_num(opts.get("hold"), 0.3, 3, 0.7), 2),
              "speed": round(_num(opts.get("speed"), 1, 12, 4), 2)}
    name = " ".join(str(opts.get("name") or "").split())[:40] or text.title()
    return {"name": name, "tags": ["Text"],
            "layers": [{"effect": "peak_text", "target": list(TARGET), "params": params}]}


def length(scene_dict: dict) -> float:
    """Seconds for one pass of the message, as the house plays it."""
    from .effects_ext.peak_text import letter_timeline, marquee_cycle
    P = scene_dict["layers"][0]["params"]
    text = hexfont.normalise(P.get("text", ""))
    pause = float(P.get("pause", PAUSE))            # the house's end pause, so the phone loops with it
    if P.get("mode") == "marquee":                  # the house's pass, a stop on each letter (dwell)
        return marquee_cycle(text, float(P.get("speed", 4)), pause, float(P.get("dwell", DWELL)))
    return letter_timeline(text, float(P.get("hold", 0.7)), pause, float(P.get("gap", 0.22)),
                           float(P.get("word_gap", 0.65)))[1]


def preview(scene_dict: dict, layout, look: dict | None = None, seconds: float = 10.0, fps: float = 6.0,
            palettes=None) -> dict:
    """The message from its first letter, as the preset thumbnails send frames."""
    from .scene import Scene
    sc = Scene.from_dict(scene_dict, palettes)
    fps = max(2.0, min(12.0, float(fps)))
    seconds = max(1.0, float(seconds))
    n = int(min(PREVIEW_MAX, seconds) * fps)
    out = []
    for k in range(n):
        cols = sc.render(k / fps, layout, look)
        out.append("".join(f"{int(c[0] * 255 + .5):02x}{int(c[1] * 255 + .5):02x}{int(c[2] * 255 + .5):02x}"
                           for c in cols))
    return {"fps": fps, "frames": out, "partial": seconds > PREVIEW_MAX + 0.5}
