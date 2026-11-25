"""Glint engine shared by the sparkle generators and FX.

A glint is a short flash on one bulb: a fast attack (eased in) and an
exponential decay. Every bulb has several independent "voices" whose timing
is hashed from the bulb index, so the pattern never repeats visibly and any
frame can be computed for any time (pure function: no state needed).
"""
from __future__ import annotations

import math
import threading

from .color import hash01, noise2

_memo = threading.local()     # per-thread: the engine and thumbnail renders don't share


def envelope(age: float, attack: float, decay: float) -> float:
    if age < 0:
        return 0.0
    if age < attack:
        x = age / attack
        return x * x
    return math.exp(-(age - attack) / decay)


def glint(i: int, t: float, rate: float, attack: float, decay: float, seed: int = 0,
          voices: int = 2) -> tuple[float, int]:
    """Brightest glint on bulb i at time t -> (intensity 0..1, event id).
    rate = average glints per bulb per second."""
    if rate <= 0:
        return 0.0, 0
    L = max(attack + decay * 3.0, 0.05)              # slot long enough to hold a whole glint
    p = min(1.0, rate * L / voices)
    best, key = 0.0, 0
    for v in range(voices):
        ph = hash01(i, v, seed, 91) * L
        k = math.floor((t + ph) / L)
        for kk in (k, k - 1):
            if hash01(i, kk, v, seed) < p:
                start = kk * L - ph + hash01(i, kk, v, seed, 5) * L * 0.5
                e = envelope(t - start, attack, decay)
                if e > best:
                    best, key = e, kk * 7 + v
    return best, key


def glint_frame(i: int, t: float, rate: float, attack: float, decay: float, seed: int) -> tuple[float, int]:
    """glint() memoised for the current frame, so neighbours reuse each other's work."""
    key = (t, rate, attack, decay, seed)
    if getattr(_memo, "key", None) != key:
        _memo.key, _memo.vals = key, {}
    v = _memo.vals.get(i)
    if v is None:
        v = _memo.vals[i] = glint(i, t, rate, attack, decay, seed)
    return v


def glint_bloom(p, t: float, rate: float, attack: float, decay: float, seed: int,
                bloom: float) -> tuple[float, int]:
    """Glint on a bulb including light spilling from its neighbours in the same run."""
    g, key = glint_frame(p.i, t, rate, attack, decay, seed)
    if bloom > 0:
        if p.ri > 0:
            g = max(g, glint_frame(p.i - 1, t, rate, attack, decay, seed)[0] * bloom)
        if p.ri < p.rn - 1:
            g = max(g, glint_frame(p.i + 1, t, rate, attack, decay, seed)[0] * bloom)
    return g, key


def wobble(i: int, t: float, rate: float, seed: int = 0) -> float:
    """Smooth per-bulb random brightness 0..1 (each bulb at its own pace)."""
    r = rate * (0.6 + 0.8 * hash01(i, seed, 33))
    return noise2(t * r, i * 1.618 + seed, seed + 3)
