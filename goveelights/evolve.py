"""'Let it evolve': endless variations of a preset that stay in its character.

Each step is a fresh variation of the *original* preset (not of the previous step),
so the look wanders but never drifts away: numeric settings move within ±amount of
their range around the original value, random seeds change (new twinkle patterns),
and occasionally the palette moves to a neighbour in the same palette family."""
from __future__ import annotations

import copy
import random

from .effects import EFFECTS
from .fx import FX
from .palettes import CATEGORIES

# never vary these: identity, location and clock settings, and timers/modes where the
# value is a schedule rather than a look (a dusk demo loop, a tree relight, a finale...)
SKIP = {"seed", "target", "value", "lat", "lon", "shift", "demo", "at", "seconds",
        "celebrate", "intro", "relight", "finale", "clear", "burst_every"}


def _family(pal: str) -> list[str]:
    for names in CATEGORIES.values():
        if pal in names:
            return list(names)
    return []


def _vary(params: dict, schema, rng: random.Random, amount: float):
    for p in schema:
        if p.name in SKIP and p.name != "seed":
            continue
        if p.name == "seed" and p.kind == "int":
            params["seed"] = rng.randrange(0, 9999)
            continue
        if p.kind in ("number", "int") and p.min is not None and p.max is not None:
            base = params.get(p.name, p.default)
            try:
                base = float(base)
            except (TypeError, ValueError):
                continue
            if base == 0 and p.min == 0:
                continue                              # 0 is 'off' / 'never': keep it off
            # vary relative to the original for wide-range settings (a 40-bulb gradient
            # shouldn't jump to 2 or 400), but never by less than a sliver of the range
            span = min((p.max - p.min) * amount, max(abs(base), (p.max - p.min) * 0.01) * amount * 1.5)
            v = min(p.max, max(p.min, base + rng.uniform(-span, span)))
            if p.name == "speed" and base != 0 and (v > 0) != (base > 0):
                v = -v                                # keep the direction of travel
            params[p.name] = int(round(v)) if p.kind == "int" else round(v, 3)


def variation(scene: dict, step: int, amount: float = 0.2, key: str = "") -> dict:
    rng = random.Random(f"{key}:{step}")
    out = copy.deepcopy(scene)
    for L in out.get("layers", []):
        fx = EFFECTS.get(L.get("effect"))
        if not fx:
            continue
        L.setdefault("params", {})
        _vary(L["params"], fx.params, rng, amount)
        for u in L.get("fx", []):
            f = FX.get(u.get("type"))
            if f:
                u.setdefault("params", {})
                _vary(u["params"], f.params, rng, amount * 0.6)
        if L.get("palette") and rng.random() < 0.3:
            fam = [p for p in _family(L["palette"]) if p != L["palette"]]
            if fam:
                L["palette"] = rng.choice(fam)
    out["name"] = scene.get("name", "Scene")
    return out
