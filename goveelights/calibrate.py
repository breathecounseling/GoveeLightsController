"""Per-run colour and brightness balance ("Match the runs").

Runs can differ in batch, age and length, so the same "warm white" can look a little
different from run to run, and a long run can be dimmer at its far end. Each run can
carry a calibration in its config entry:

    "calibration": {"brightness": 0.9,          # 0.3..1   whole run a bit dimmer
                    "gain": [1.0, 0.95, 0.85],  # 0.4..1   per-channel red, green, blue (white point)
                    "gamma": 1.0,               # 0.6..1.6 this run's mid-tones (1 = off)
                    "fade": 0.0}                # 0..0.4   far-end fade compensation (0 = off)

It is applied at the output stage (engine.to_device_frames: after rendering and the
global brightness / late-night dim, before the frame is sent), so every effect, show and
picture gets it. Missing or default values change nothing: output stays byte-identical.

Gains only ever lower a channel (never above 1), so the brightest colour a run can show
is unchanged and nothing clips: "a bit brighter" on a run that is already at full makes
the other runs a bit dimmer instead (never below CASCADE_FLOOR this way), and says so.

The older per-run "trim" ([r, g, b]) is read as the gain when there is no calibration
gain, and is folded into "calibration" the first time the run is calibrated here.

Fade compensation: a long run loses a little voltage along its length, so its far end
(the last bulb, away from the box) can look dimmer than the start. With fade f the first
bulb is dimmed by f, falling linearly to no change at the last bulb, so the run looks
even end to end.
"""
from __future__ import annotations

import math

BRIGHT = (0.3, 1.0)
GAIN = (0.4, 1.0)
GAMMA = (0.6, 1.6)
FADE = (0.0, 0.4)
STEP_BRIGHT = 0.05
STEP_WARM = 0.04
STEP_FADE = 0.05

IDENTITY = {"brightness": 1.0, "gain": [1.0, 1.0, 1.0], "gamma": 1.0, "fade": 0.0}

# the colours "Match the runs" shows on every run at once
SWATCHES = {
    "warm": ("Warm white", "#ffb46b"),
    "cool": ("Cool white", "#ffffff"),
    "red": ("Red", "#ff0000"),
    "green": ("Green", "#00ff00"),
    "blue": ("Blue", "#0000ff"),
    "gold": ("Gold", "#ffa000"),
}


def _num(v, lo: float, hi: float, default: float, rnd: bool = True) -> float:
    try:
        v = float(v)
    except (TypeError, ValueError):
        raise ValueError("calibration values must be numbers") from None
    if not math.isfinite(v):
        raise ValueError("calibration values must be numbers")
    v = max(lo, min(hi, v))
    return round(v, 3) if rnd else v


def read(dev: dict) -> dict:
    """The calibration a config entry asks for, every field filled in and clamped."""
    c = dev.get("calibration") if isinstance(dev.get("calibration"), dict) else {}
    gain = c.get("gain") if isinstance(c.get("gain"), (list, tuple)) and len(c["gain"]) == 3 else None
    if gain is None and isinstance(dev.get("trim"), (list, tuple)) and len(dev["trim"]) == 3:
        gain = dev["trim"]                       # the older colour trim
    out = dict(IDENTITY, gain=[1.0, 1.0, 1.0])
    # each field on its own: a hand-edited typo in one must not stop the lights or lose the others
    for k, lim, dflt in (("brightness", BRIGHT, 1.0), ("gamma", GAMMA, 1.0), ("fade", FADE, 0.0)):
        try:
            out[k] = _num(c.get(k, dflt), *lim, dflt)
        except ValueError:
            pass
    if gain:
        try:
            out["gain"] = [_num(v, 0.2, 1.0, 1.0, rnd=False) for v in gain]
        except ValueError:
            pass
    return out


def is_identity(c: dict) -> bool:
    return (c["brightness"] == 1.0 and c["gamma"] == 1.0 and c["fade"] == 0.0
            and all(g == 1.0 for g in c["gain"]))


def write(dev: dict, c: dict) -> dict:
    """Store calibration c (clamped) in a config entry; the default removes it, so an
    untouched run's entry looks as it always did."""
    c = {"brightness": _num(c.get("brightness", 1.0), *BRIGHT, 1.0),
         "gain": [_num(v, 0.2, 1.0, 1.0) for v in (c.get("gain") or [1.0, 1.0, 1.0])],
         "gamma": _num(c.get("gamma", 1.0), *GAMMA, 1.0),
         "fade": _num(c.get("fade", 0.0), *FADE, 0.0)}
    if len(c["gain"]) != 3:
        raise ValueError("gain needs three numbers (red, green, blue)")
    dev.pop("trim", None)                        # folded into the calibration
    if is_identity(c):
        dev.pop("calibration", None)
    else:
        dev["calibration"] = c
    return c


def apply_to_run(run, c: dict):
    """Copy a calibration onto a layout Run (what to_device_frames reads)."""
    g = tuple(c["gain"])
    run.trim = None if g == (1.0, 1.0, 1.0) else g
    run.bright, run.rgamma, run.fade = c["brightness"], c["gamma"], c["fade"]


# one tap of "a bit warmer" / "a bit cooler": (red, green, blue) taken off. "Cooler" on a
# run that was made warmer gives back exactly one "warmer" step (and the other way round),
# so taps always undo cleanly.
WARM_STEP = (0.0, STEP_WARM * 0.5, STEP_WARM * 1.5)    # less blue, a little less green
COOL_STEP = (STEP_WARM * 1.5, STEP_WARM * 0.25, 0.0)   # less red, a touch less green
CASCADE_FLOOR = 0.6     # "brighter" on a run at full brings the others down, but not below this


def nudge_note(cals: dict[str, dict], mac: str, what: str, names: dict | None = None) -> str:
    """What a nudge did that the owner might not expect (empty when nothing to say)."""
    if what != "brighter" or mac not in cals or cals[mac]["brightness"] < 1.0:
        return ""
    name = (names or {}).get(mac) or "This run"
    if not any(m != mac and o["brightness"] > CASCADE_FLOOR for m, o in cals.items()):
        return (f"{name} is already as bright as it goes, and the other runs are as low as this "
                f"button takes them. Try \"A bit dimmer\" on another run instead.")
    return f"{name} is already at full, so the other runs came down a little to match it."


def nudge(cals: dict[str, dict], mac: str, what: str) -> dict[str, dict]:
    """One tap of 'a bit warmer / cooler / brighter / dimmer / more even' on one run.
    cals: mac -> calibration (all runs; 'brighter' on a run already at full dims the
    others instead, down to CASCADE_FLOOR). Returns the changed calibrations (mac -> calibration)."""
    if mac not in cals:
        raise ValueError("unknown run")
    c = {k: (list(v) if isinstance(v, list) else v) for k, v in cals[mac].items()}
    gain = list(c["gain"])
    changed = {}

    def step(d, sign):
        return [min(1.0, max(GAIN[0], x + sign * dx)) for x, dx in zip(gain, d)]

    if what == "warmer":                         # undo "cooler" first
        gain = step(COOL_STEP, +1) if gain[0] < 1.0 else step(WARM_STEP, -1)
    elif what == "cooler":                       # undo "warmer" first
        gain = step(WARM_STEP, +1) if gain[2] < 1.0 else step(COOL_STEP, -1)
    elif what == "brighter":
        if c["brightness"] < 1.0:
            c["brightness"] = min(1.0, c["brightness"] + STEP_BRIGHT)
        else:                                    # already full: the other runs come down to it
            for m, o in cals.items():
                if m != mac and o["brightness"] > CASCADE_FLOOR:
                    o = dict(o, gain=list(o["gain"]))
                    o["brightness"] = round(max(CASCADE_FLOOR, o["brightness"] - STEP_BRIGHT), 3)
                    changed[m] = o
            return changed
    elif what == "dimmer":
        c["brightness"] = max(BRIGHT[0], c["brightness"] - STEP_BRIGHT)
    elif what == "even":                         # far end looks dimmer: more compensation
        c["fade"] = min(FADE[1], c["fade"] + STEP_FADE)
    elif what == "less-even":
        c["fade"] = max(0.0, c["fade"] - STEP_FADE)
    else:
        raise ValueError(f"unknown adjustment {what!r}")
    c["gain"] = [round(x, 3) for x in gain]
    c["brightness"] = round(c["brightness"], 3)
    c["fade"] = round(c["fade"], 3)
    changed[mac] = c
    return changed


def pixel_gains(layout) -> tuple[list | None, list | None]:
    """Per visible pixel (virtual order): the (r, g, b) multiplier, or None where the
    run is untouched; and the run's extra gamma, or None where it is 1. (None, None)
    when every run is untouched. Cached on the layout until a run's calibration changes,
    so the render loop pays for a few attribute reads per frame."""
    sig = tuple((getattr(r, "trim", None), getattr(r, "bright", 1.0), getattr(r, "fade", 0.0),
                 getattr(r, "rgamma", 1.0)) for r in layout.runs)
    cached = getattr(layout, "_cal_cache", None)
    if cached is not None and cached[0] == sig:
        return cached[1], cached[2]
    if all(s == (None, 1.0, 0.0, 1.0) for s in sig):
        mult = gam = None
    else:
        runs = {r.mac: r for r in layout.runs}
        mult, gam = [], []
        for p in layout.pixels:
            r = runs[p.mac]
            tr = getattr(r, "trim", None)
            br, fade, rg = getattr(r, "bright", 1.0), getattr(r, "fade", 0.0), getattr(r, "rgamma", 1.0)
            gam.append(rg if rg != 1.0 else None)
            if tr is None and br == 1.0 and fade == 0.0:
                mult.append(None)
                continue
            k = br
            if fade:
                k *= 1.0 - fade * (1.0 - p.local / max(1, r.count - 1))
            tr = tr or (1.0, 1.0, 1.0)
            mult.append((tr[0] * k, tr[1] * k, tr[2] * k))
        if all(g is None for g in gam):
            gam = None
    layout._cal_cache = (sig, mult, gam)
    return mult, gam
