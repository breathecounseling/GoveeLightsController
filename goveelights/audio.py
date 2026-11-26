"""Music from the phone's microphone ("Listen" on the Play tab).

The phone does the listening (Web Audio: onsets, tempo, loudness) and POSTs small
updates to /api/beat. This module keeps the latest one and turns it into what effects
see as ``ctx.audio`` each frame:

    {"level": 0..1, "bass": 0..1,      smoothed loudness and low-band energy
     "beat_age": s,                    seconds since the last beat (grid or onset)
     "phase": 0..1 | None,             where we are inside the current beat
     "bpm": float | None, "presence": 0..1}

An update counts fully for HOLD seconds, then fades out over FADE seconds and is gone,
so nothing sticks if the phone locks or the page is closed.

Effects that should also look good without music use ``pulse()``: it crossfades from
the music's beats to the master tempo as the music fades away.
"""
from __future__ import annotations

import math
import threading
import time

HOLD = 2.0
FADE = 1.0
NO_BEAT = 99.0                       # beat_age when there has never been a beat


def _num(v, what: str, lo: float, hi: float) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ValueError(f"{what} must be a number")
    if not lo <= v <= hi:
        raise ValueError(f"{what} must be between {lo:g} and {hi:g}")
    return float(v)


class AudioState:
    """The latest update from a listening phone (thread-safe)."""

    def __init__(self, clock=time.time):
        self.clock = clock
        self.lock = threading.Lock()
        self.clear()

    def clear(self):
        self.last: float | None = None        # when the last update arrived
        self.level = self.bass = 0.0
        self.bpm: float | None = None
        self.anchor: float | None = None      # a time on the beat grid (a beat starts there)
        self.onset: float | None = None       # the last detected onset
        self.updates = 0

    def update(self, d: dict, now: float | None = None) -> dict:
        """Validate and take one update from the phone:
            level, bass   0..1 (required)
            bpm           30..300 or null (no confident tempo yet)
            phase         0..1, position inside the beat when the phone sent this
            beat          true if an onset was detected since the last update
            ago           ms from that onset to the moment of sending (0..3000)
            rtt           the phone's measured round trip to us in ms (0..5000); half
                          of it is taken as the network delay
        Raises ValueError for anything malformed (nothing is changed then)."""
        if not isinstance(d, dict):
            raise ValueError("beat update must be an object")
        level = _num(d.get("level"), "level", 0.0, 1.0)
        bass = _num(d.get("bass"), "bass", 0.0, 1.0)
        bpm = d.get("bpm")
        bpm = None if bpm is None else _num(bpm, "bpm", 30.0, 300.0)
        phase = d.get("phase")
        phase = None if phase is None else _num(phase, "phase", 0.0, 1.0)
        beat = d.get("beat", False)
        if not isinstance(beat, bool):
            raise ValueError("beat must be true or false")
        ago = _num(d.get("ago", 0), "ago", 0.0, 3000.0) / 1000.0
        lag = _num(d.get("rtt", 0), "rtt", 0.0, 5000.0) / 2000.0
        lag = min(lag, 0.5)                     # a very slow link: don't shift the beat by more
        now = self.clock() if now is None else now
        with self.lock:
            self.last = now
            self.level, self.bass = level, bass
            if beat:
                self.onset = now - lag - ago
            if bpm is not None and phase is not None:
                self.bpm = bpm
                self.anchor = now - lag - phase * 60.0 / bpm
            elif bpm is None:
                self.bpm = self.anchor = None          # the phone lost the tempo
            self.updates += 1
        return {"bpm": bpm}

    def snapshot(self, now: float | None = None) -> dict | None:
        """What effects see this frame, or None when no music is coming in."""
        now = self.clock() if now is None else now
        with self.lock:
            if self.last is None:
                return None
            age = now - self.last
            if age >= HOLD + FADE or age < -5:
                return None
            presence = 1.0 if age <= HOLD else max(0.0, 1.0 - (age - HOLD) / FADE)
            phase = None
            if self.bpm and self.anchor is not None:
                per = 60.0 / self.bpm
                beat_age = (now - self.anchor) % per
                phase = beat_age / per
            elif self.onset is not None:
                beat_age = max(0.0, now - self.onset)
            else:
                beat_age = NO_BEAT
            return {"level": self.level * presence, "bass": self.bass * presence, "beat_age": beat_age,
                    "phase": phase, "bpm": self.bpm, "presence": presence}


def synthetic(t: float, bpm: float = 120.0) -> dict:
    """A made-up song for previews and tests: a kick on every beat (the first of each bar
    hardest), loudness that swells over 8 bars. Shaped like AudioState.snapshot()."""
    per = 60.0 / bpm
    beat_age = t % per
    n = int(t // per)
    accent = 1.0 if n % 4 == 0 else 0.7
    kick = math.exp(-beat_age / 0.12) * accent
    swell = 0.55 + 0.35 * math.sin(2 * math.pi * t / (32 * per))
    return {"level": min(1.0, swell * (0.75 + 0.25 * kick)), "bass": min(1.0, 0.15 + 0.85 * kick),
            "beat_age": beat_age, "phase": beat_age / per, "bpm": bpm, "presence": 1.0}


# ------------------------------------------------------------ for effects
def tempo_beat(clock: float, bpm: float, beats: float | None = None) -> tuple[float, float]:
    """(phase, beat_age) on the master tempo alone: the beat grid when no music plays."""
    bpm = max(20.0, float(bpm or 120.0))
    b = clock * bpm / 60.0 if beats is None else beats
    ph = b % 1.0
    return ph, ph * 60.0 / bpm


def env(beat_age: float, decay: float) -> float:
    """A beat's flash: 1 on the beat, falling away exponentially."""
    if beat_age >= NO_BEAT:
        return 0.0
    return math.exp(-max(0.0, beat_age) / max(0.01, decay))


def pulse(ctx, decay: float = 0.25, idle: bool = True, beats: float | None = None) -> dict:
    """Beat, level and bass for this frame, from the music when it's there.

    idle=True: without music the beat keeps time with the master tempo (and level/bass
    follow a gentle kick-drum envelope), crossfading as the music comes and goes.
    idle=False: without music everything is 0 and ``music`` is 0.
    Returns {"beat": 0..1 flash, "level", "bass", "beat_age", "phase", "music": 0..1}."""
    a = getattr(ctx, "audio", None)
    p = a["presence"] if a else 0.0
    out = {"beat": 0.0, "level": 0.0, "bass": 0.0, "beat_age": NO_BEAT, "phase": 0.0, "music": p}
    if a:
        out["beat"] = env(a["beat_age"], decay) * p
        out["level"], out["bass"] = a["level"], a["bass"]
        out["beat_age"] = a["beat_age"]
        out["phase"] = a["phase"] if a["phase"] is not None else 0.0
    if idle and p < 1.0:
        ph, age = tempo_beat(ctx.clock, getattr(ctx, "bpm", 120.0), beats)
        k = 1.0 - p
        e = env(age, decay)
        out["beat"] += e * k
        kick = env(age, 0.35)
        out["level"] += (0.45 + 0.35 * kick) * k
        out["bass"] += (0.25 + 0.7 * kick) * k
        if not a or a["beat_age"] >= NO_BEAT or p < 0.5:
            out["beat_age"] = age
            out["phase"] = ph
    return out


def signal(source: str, audio: dict | None, decay: float = 0.25, idle: bool = False,
           clock: float = 0.0, bpm: float = 120.0, beats: float | None = None) -> float | None:
    """A modulation source in 0..1: "level", "bass" or "beat". None means "no signal"
    (no music and idle off): the parameter keeps its own value."""
    if not audio and not idle:
        return None

    class _C:                       # pulse() wants a ctx
        pass
    c = _C()
    c.audio, c.clock, c.bpm = audio, clock, bpm
    p = pulse(c, decay, idle, beats)
    return max(0.0, min(1.0, p.get(source, p["beat"])))
