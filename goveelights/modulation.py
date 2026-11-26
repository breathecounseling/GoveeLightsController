"""Modulators (LFOs) that drive any numeric effect/FX parameter.

Spec stored with a layer: "mod": {"<param>": {"shape": "sine", "rate": 0.2,
"depth": 0.5, "phase": 0, "sync": false, "beats": 4}}
  shape  sine | triangle | saw | square | random (smooth) | step (sample & hold)
  rate   cycles per second (when not synced)
  sync   follow the master tempo; one cycle = `beats` beats
  depth  -1..1, fraction of half the parameter's range (negative inverts)
  source lfo (default) | level | bass | beat: follow the music the phone hears
         (Play -> Listen). Music sources push the value up from the slider by
         depth x half the range at full signal; beat flashes and falls over `decay`
         seconds. idle: true keeps time with the master tempo when no music plays;
         otherwise the slider value stays as it is without music.
"""
from __future__ import annotations

import math

from .audio import signal as _signal
from .color import hash01, noise2

SHAPES = ["sine", "triangle", "saw", "square", "random", "step"]
SOURCES = ["lfo", "level", "bass", "beat"]


def wave(shape: str, ph: float, seed: int = 0) -> float:
    """Bipolar wave value in -1..1 for phase `ph` in cycles."""
    f = ph % 1.0
    if shape == "triangle":
        return 1 - 4 * abs(f - 0.5)
    if shape == "saw":
        return 2 * f - 1
    if shape == "square":
        return 1.0 if f < 0.5 else -1.0
    if shape == "random":
        return noise2(ph, 0.5, seed + 17) * 2 - 1
    if shape == "step":
        return hash01(math.floor(ph), seed + 29) * 2 - 1
    return math.sin(2 * math.pi * ph)


def apply(spec: dict, base: float, lo: float | None, hi: float | None, clock: float,
          bpm: float = 120.0, seed: int = 0, is_int: bool = False, beats: float | None = None,
          audio: dict | None = None) -> float:
    """beats: the layer's beat count (integrated over tempo changes, so a tempo change
    never makes a synced LFO jump); None works it out from clock and bpm.
    audio: this frame's music (see audio.AudioState.snapshot) for the music sources."""
    if not spec or spec.get("enabled") is False:
        return base
    src = spec.get("source", "lfo")
    if src in ("level", "bass", "beat"):
        s = _signal(src, audio, float(spec.get("decay", 0.25)), bool(spec.get("idle", False)), clock, bpm, beats)
        if s is None:
            return base
        half = (hi - lo) / 2 if lo is not None and hi is not None else (abs(base) or 1.0)
        v = base + float(spec.get("depth", 0.5)) * half * s
        if lo is not None:
            v = max(lo, v)
        if hi is not None:
            v = min(hi, v)
        return round(v) if is_int else v
    if spec.get("sync"):
        b = clock * (bpm / 60.0) if beats is None else beats
        cycles = b / max(0.125, float(spec.get("beats", 4)))
    else:
        cycles = clock * float(spec.get("rate", 0.2))
    cycles += float(spec.get("phase", 0))
    half = (hi - lo) / 2 if lo is not None and hi is not None else (abs(base) or 1.0)
    v = base + float(spec.get("depth", 0.5)) * half * wave(spec.get("shape", "sine"), cycles, seed)
    if lo is not None:
        v = max(lo, v)
    if hi is not None:
        v = min(hi, v)
    return round(v) if is_int else v
