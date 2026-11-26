"""Music from the phone's microphone ("Listen" on the Play tab).

The phone does the listening (Web Audio: onsets, tempo, loudness) and POSTs small
updates to /api/beat. This module keeps the latest one and turns it into what effects
see as ``ctx.audio`` each frame:

    {"level": 0..1, "bass": 0..1,      smoothed loudness and low-band energy
     "beat_age": s,                    seconds since the last beat (grid or onset)
     "phase": 0..1 | None,             where we are inside the current beat
     "bpm": float | None, "presence": 0..1}

An update counts fully for HOLD seconds, then fades out over FADE seconds and is gone,
so nothing sticks if the phone locks or the page is closed. Presence is also scaled by
the phone's own confidence that music is playing, so a quiet room between songs falls
back to the master tempo even while the phone keeps posting.

The beat grid is phase-locked to the phone's updates (a small gain per update), so the
jitter of the phone's phase estimate and of the round trip doesn't reach the lights, and
it runs ``offset`` seconds ahead of the music to make up for the delays nobody measures
(microphone input, a frame of rendering, UDP and the Govee controller).

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
OFFSET_MS = 70.0                     # default output latency: render this far ahead of the music
GAIN = 0.2                           # how much of each update's phase error the beat grid takes
MAX_STEP = 0.25                      # ...after clamping the error to this fraction of a beat
RELOCK_GAP = 1.0                     # s without updates after which a new phase is taken outright
RELOCK_BPM = 0.04                    # and after a tempo change bigger than this (fraction)
MAX_RATE = 30.0                      # updates a second taken from one phone (the rest are dropped)


def _num(v, what: str, lo: float, hi: float) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ValueError(f"{what} must be a number")
    if not lo <= v <= hi:
        raise ValueError(f"{what} must be between {lo:g} and {hi:g}")
    return float(v)


def _wrap(x: float) -> float:
    """x folded into -0.5..0.5 (a phase difference in beats)."""
    return (x + 0.5) % 1.0 - 0.5


class _Source:
    """One listening phone."""

    def __init__(self):
        self.last: float | None = None        # when its last update arrived
        self.level = self.bass = 0.0
        self.music = 1.0                      # the phone's confidence that music is playing
        self.bpm: float | None = None
        self.anchor: float | None = None      # a time on the (smoothed) beat grid: a beat starts there
        self.onset: float | None = None       # the last detected onset


class AudioState:
    """The music from the listening phones (thread-safe).

    Each phone (``client``) is kept apart. Effects follow one of them, the *active* one,
    until it stops or goes quiet for HOLD + FADE seconds; then the most recent other one
    takes over. So two phones listening at once don't fight over the beat, and one
    phone's stop doesn't cut the other off."""

    def __init__(self, clock=time.time):
        self.clock = clock
        self.lock = threading.Lock()
        self.offset = OFFSET_MS / 1000.0      # s the lights run ahead of the music (output latency)
        self.sources: dict[str, _Source] = {}
        self.active: str | None = None
        self.updates = 0

    def clear(self, client: str | None = None):
        """Forget every phone (client None), or just that one."""
        with self.lock:
            if client is None:
                self.sources.clear()
            else:
                self.sources.pop(client, None)
            if self.active not in self.sources:
                self.active = None

    def _live(self, now: float) -> str | None:
        """The phone effects follow now (call with the lock held)."""
        def alive(k):
            s = self.sources.get(k)
            return s is not None and s.last is not None and -5 < now - s.last < HOLD + FADE
        if self.active is not None and alive(self.active):
            return self.active
        live = [k for k in self.sources if alive(k)]
        self.active = max(live, key=lambda k: self.sources[k].last) if live else None
        return self.active

    def is_active(self, client: str, now: float | None = None) -> bool:
        now = self.clock() if now is None else now
        with self.lock:
            return self._live(now) == client

    def update(self, d: dict, now: float | None = None, client: str = "") -> dict:
        """Validate and take one update from a phone:
            level, bass   0..1 (required)
            bpm           30..300 or null (no confident tempo yet)
            phase         0..1, position inside the beat when the phone sent this
            beat          true if an onset was detected since the last update
            ago           ms from that onset to the moment of sending (0..3000)
            rtt           the phone's measured round trip to us in ms (0..5000); half
                          of it is taken as the network delay
            music         0..1, optional (default 1): how sure the phone is that music
                          is playing (recent onsets, tempo confidence); scales presence
        The beat grid is phase-locked, not replaced: each update moves it by GAIN of its
        (clamped, wrapped) phase error. A new phase is taken outright on the first update,
        after a tempo change of more than RELOCK_BPM or after a gap of RELOCK_GAP.
        Returns {"bpm", "active": effects follow this phone, "dropped": it came too soon
        after the last one (more than MAX_RATE a second) and was ignored}.
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
        music = _num(d.get("music", 1.0), "music", 0.0, 1.0)
        now = self.clock() if now is None else now
        with self.lock:
            s = self.sources.get(client)
            if s is None:
                s = self.sources[client] = _Source()
            elif s.last is not None and 0 <= now - s.last < 1.0 / MAX_RATE:
                return {"bpm": s.bpm, "active": self._live(now) == client, "dropped": True}
            gap = None if s.last is None else now - s.last
            s.last = now
            s.level, s.bass, s.music = level, bass, music
            if beat:
                s.onset = now - lag - ago
            if bpm is not None and phase is not None:
                per = 60.0 / bpm
                target = now - lag - phase * per          # where the phone says this beat started
                if (s.anchor is None or s.bpm is None or gap is None or gap > RELOCK_GAP
                        or abs(bpm - s.bpm) > RELOCK_BPM * s.bpm):
                    s.anchor = target
                else:
                    old = 60.0 / s.bpm
                    s.anchor += math.floor((now - s.anchor) / old) * old   # keep it within a beat of now
                    err = _wrap((target - s.anchor) / per)
                    s.anchor += max(-MAX_STEP, min(MAX_STEP, err)) * GAIN * per
                s.bpm = bpm
            elif bpm is None:
                s.bpm = s.anchor = None                 # the phone lost the tempo
            self.updates += 1
            return {"bpm": bpm, "active": self._live(now) == client, "dropped": False}

    def snapshot(self, now: float | None = None) -> dict | None:
        """What effects see this frame, or None when no music is coming in."""
        now = self.clock() if now is None else now
        with self.lock:
            k = self._live(now)
            if k is None:
                return None
            s = self.sources[k]
            age = now - s.last
            presence = 1.0 if age <= HOLD else max(0.0, 1.0 - (age - HOLD) / FADE)
            presence *= s.music
            ahead = now + self.offset                   # the lights trail the sound: render ahead
            phase = None
            if s.bpm and s.anchor is not None:
                per = 60.0 / s.bpm
                beat_age = (ahead - s.anchor) % per
                phase = beat_age / per
            elif s.onset is not None:
                beat_age = max(0.0, ahead - s.onset)
            else:
                beat_age = NO_BEAT
            return {"level": s.level * presence, "bass": s.bass * presence, "beat_age": beat_age,
                    "phase": phase, "bpm": s.bpm, "presence": presence}


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

    beats: the layer's beat count (ctx.beats). The scene already glides it onto the
    music's grid, so when it's given the beat and phase come from it (smooth: it never
    steps back or skips a beat), and ``count`` is the whole beat number; effects that
    step on each beat should count changes of ``count``. Without it they come straight
    from the music (and ``count`` is None).
    idle=True: without music the beat keeps time with the master tempo (and level/bass
    follow a gentle kick-drum envelope), crossfading as the music comes and goes.
    idle=False: without music everything is 0 and ``music`` is 0.
    Returns {"beat": 0..1 flash, "level", "bass", "beat_age", "phase", "music": 0..1,
    "count": int | None}."""
    a = getattr(ctx, "audio", None)
    p = a["presence"] if a else 0.0
    out = {"beat": 0.0, "level": 0.0, "bass": 0.0, "beat_age": NO_BEAT, "phase": 0.0, "music": p,
           "count": None}
    grid = False
    if a:
        age, ph = a["beat_age"], a["phase"]
        if ph is not None and beats is not None and a.get("bpm"):
            ph = beats % 1.0
            age = ph * 60.0 / a["bpm"]
            grid = True
        out["beat"] = env(age, decay) * p
        out["level"], out["bass"] = a["level"], a["bass"]
        out["beat_age"] = age
        out["phase"] = ph if ph is not None else 0.0
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
            grid = beats is not None
    if grid:
        out["count"] = math.floor(beats)
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
