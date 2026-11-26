"""Beat sync from the phone's microphone (Play -> Listen): the phone-side detection
functions (run with node), POST /api/beat (validation, auth/CSRF, decay), music modulation
sources, the beat FX and the music presets."""
from __future__ import annotations

import json
import math
import random
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_integration import Rig  # noqa: E402
from test_r3_web import APP, extract, run_node  # noqa: E402
from test_rack import house, valid  # noqa: E402

from goveelights import audio as A  # noqa: E402
from goveelights import modulation  # noqa: E402
from goveelights.app_server import Server, make_handler  # noqa: E402
from goveelights.auth import Auth  # noqa: E402
from goveelights.effects import EFFECTS  # noqa: E402
from goveelights.fx import FX, Ctx  # noqa: E402
from goveelights.scene import Scene  # noqa: E402

BUDGET_MS = 8.0
MUSIC_PRESETS = ["music-pulse", "party-beat", "plasma-party", "glitter-ball"]


def load(pid):
    with open(f"presets/{pid}.json", encoding="utf-8") as f:
        return json.load(f)


def pattern(bpm, pos, seconds, jitter=0.009, miss=0.0, seed=1, start=0.3):
    """Onsets of a drum pattern: `pos` are the hits' places in a 4-beat bar, in beats."""
    rng = random.Random(seed)
    p = 60.0 / bpm
    out, bar = [], start
    while bar < start + seconds:
        for q in pos:
            if rng.random() >= miss:
                out.append(bar + q * p + rng.uniform(-jitter, jitter))
        bar += 4 * p
    return sorted(out)


def clicks(bpm, seconds, jitter=0.0, miss=0.0, extra=0.0, seed=1, start=0.3):
    """Onset times of a click track: timing jitter (s, uniform ±), missed beats and
    stray off-beat hits (fractions)."""
    rng = random.Random(seed)
    p = 60.0 / bpm
    out, t = [], start
    while t < start + seconds:
        if rng.random() >= miss:
            out.append(t + rng.uniform(-jitter, jitter))
        if rng.random() < extra:
            out.append(t + p * rng.uniform(0.3, 0.7))
        t += p
    return sorted(out)


# ------------------------------------------------------------------ phone side (node)
@unittest.skipUnless(shutil.which("node"), "node is not installed")
class PhoneDetectionTests(unittest.TestCase):
    def js(self, *names):
        return "\n".join(extract(APP, n) for n in names)

    def bpm(self, onsets):
        return run_node(self.js("estimateBpm") + f"\nconsole.log(JSON.stringify(estimateBpm({json.dumps(onsets)})))")

    def test_120_with_jitter_and_missed_beats(self):
        for seed in range(6):
            with self.subTest(seed=seed):
                r = self.bpm(clicks(120, 10, jitter=0.02, miss=0.2, seed=seed))
                self.assertAlmostEqual(r["bpm"], 120, delta=2, msg=r)
                self.assertGreater(r["conf"], 0.25)

    def test_other_tempos_and_stray_hits(self):
        for bpm in (72, 95, 128, 140, 174):
            with self.subTest(bpm=bpm):
                r = self.bpm(clicks(bpm, 12, jitter=0.015, miss=0.15, extra=0.1, seed=bpm))
                got = r["bpm"]
                if bpm < 80:                    # octave correction: slow songs count double time
                    self.assertAlmostEqual(got, bpm * 2, delta=3, msg=r)
                elif bpm > 160:                 # and very fast ones half time (a 174 DnB nods at 87)
                    self.assertAlmostEqual(got, bpm / 2, delta=1.5, msg=r)
                else:
                    self.assertAlmostEqual(got, bpm, delta=2, msg=r)

    def test_syncopated_patterns_find_the_beat(self):
        """Kick patterns that aren't four on the floor (8-10 ms jitter): the beat, or its
        octave, never a 3:2 or a sixteenth grid; four on the floor stays where it is, with
        missed beats too; random hits aren't taken for a tempo (conf under 0.45)."""
        cases = [("hip-hop 1, 2.5, 3", 90, [0, 1.5, 2], (90,)),
                 ("DnB 1, 2.5", 174, [0, 1.5], (174, 87)),
                 ("reggaeton 3-3-2", 95, [0, 0.75, 1.5, 2, 2.75, 3.5], (95,)),
                 ("house kick + off-beat bass", 128, [0, 0.5, 1, 1.5, 2, 2.5, 3, 3.5], (128,)),
                 ("half-time 140", 140, [0, 2.5], (140, 70)),
                 ("kick 1 and 3", 85, [0, 2], (85,))]
        cases += [(f"four on the floor {b}", b, [0, 1, 2, 3], (b, b * 2)) for b in (75, 100, 120, 128, 140)]
        cases += [("four on the floor, 30% missed", 120, [0, 1, 2, 3], (120,), 0.3)]
        for name, bpm, pos, ok, *miss in cases:
            for seed in (1, 2, 3):
                with self.subTest(name, seed=seed):
                    r = self.bpm(pattern(bpm, pos, 10, jitter=0.01, miss=miss[0] if miss else 0, seed=seed))
                    self.assertGreater(r["conf"], 0.45, r)
                    self.assertTrue(any(abs(r["bpm"] - o) <= 2.5 for o in ok), (name, r))
        for seed in range(8):
            with self.subTest("random hits", seed=seed):
                rng, on, t = random.Random(seed), [], 0.3
                while t < 10:
                    t += rng.uniform(0.15, 0.75)
                    on.append(t)
                r = self.bpm(on)
                self.assertLess(r["conf"], 0.45, r)

    def test_phase_lock_ignores_off_beat_bass(self):
        """128 BPM kicks with a bass on 90% of the off-beats, in the same band (the bass a bit
        weaker). The locked phase stays on the kicks, onset after onset; a plain average of
        the onset phases would flip between the two."""
        rng = random.Random(5)
        p, on, ws = 60 / 128, [], []
        t = 0.4
        while t < 20:
            on.append(t + rng.uniform(-0.008, 0.008)); ws.append(rng.uniform(1.8, 2.6))
            if rng.random() < 0.9:
                on.append(t + p / 2 + rng.uniform(-0.008, 0.008)); ws.append(rng.uniform(1.2, 1.9))
            t += p
        out = run_node(self.js("beatPhase", "phaseLock", "lockPhase") + f"""
const on = {json.dumps(on)}, ws = {json.dumps(ws)}, p = 60 / 128, st = {{}}, err = [];
for (let i = 0; i < on.length; i++){{
  phaseLock(st, on[i], ws[i], 128, on.slice(Math.max(0, i - 24), i + 1), ws.slice(Math.max(0, i - 24), i + 1));
  const now = on[i] + 0.05, ph = lockPhase(st, 128, now);
  if (ph != null && on[i] > 3){{ const truth = ((now - 0.4) / p) % 1; let e = ph - truth; e -= Math.round(e); err.push(Math.abs(e)) }}
}}
const fold = beatPhase(on, 128, 20.0, ws), truth = ((20.0 - 0.4) / p) % 1;
console.log(JSON.stringify([Math.max(...err), err.length, Math.abs(((fold - truth) % 1 + 1.5) % 1 - 0.5)]))""")
        worst, n, fold = out
        self.assertGreater(n, 50)
        self.assertLess(worst, 0.06)                    # within ~30 ms the whole way
        self.assertLess(fold, 0.06)                     # the folded first guess picks the kicks too

    def test_phase_lock_turns_round_onto_the_stronger_hits(self):
        """Locked half a beat wrong (on the bass): the stronger kicks turn it round."""
        p = 0.5
        on = [0.3 + k * p / 2 for k in range(60)]         # kicks on even, bass on odd
        ws = [2.5 if k % 2 == 0 else 1.3 for k in range(60)]
        out = run_node(self.js("beatPhase", "phaseLock", "lockPhase") + f"""
const on = {json.dumps(on)}, ws = {json.dumps(ws)};
const st = {{anchor: 0.3 + 0.25, bpm: 120, miss: 0, on: 0, off: 0}};
for (let i = 0; i < on.length; i++) phaseLock(st, on[i], ws[i], 120, on.slice(0, i + 1), ws.slice(0, i + 1));
console.log(JSON.stringify(lockPhase(st, 120, 0.3 + 60 * 0.25)))""")
        self.assertLess(min(out, 1 - out), 0.03)

    def test_music_confidence(self):
        out = run_node(self.js("musicConf") + """
console.log(JSON.stringify([musicConf([], 10, null), musicConf([1, 2, 3], 10, 0.9),
  musicConf([7, 7.5, 8, 8.5, 9, 9.5, 10], 10, 0.8), musicConf([7, 7.5, 8, 8.5, 9, 9.5, 10], 10, null),
  musicConf([9, 9.5, 10], 10, 0.9)]))""")
        none, old, full, no_tempo, few = out
        self.assertEqual(none, 0)
        self.assertEqual(old, 0)                          # nothing in the last 4 s
        self.assertEqual(full, 1)
        self.assertLess(no_tempo, 0.8)
        self.assertLess(few, 0.6)

    def test_auto_range_does_not_stretch_room_noise(self):
        out = run_node(self.js("autoRange") + """
let s = 3; const rnd = () => (s = (s * 16807) % 2147483647) / 2147483647;
const st = {}; let top = 0;
for (let k = 0; k < 40 / 0.015; k++){ const v = autoRange(st, 0.15 + 0.05 * rnd(), 0.015); if (k * 0.015 > 25) top = Math.max(top, v) }
const st2 = {}; let lo = 1, hi = 0;
for (let k = 0; k < 40 / 0.015; k++){ const t = k * 0.015, v = autoRange(st2, 0.3 + 0.5 * Math.abs(Math.sin(t * 3)), 0.015); if (t > 25){ lo = Math.min(lo, v); hi = Math.max(hi, v) } }
console.log(JSON.stringify([top, lo, hi]))""")
        top, lo, hi = out
        self.assertLess(top, 0.5)                         # a quiet room stays low
        self.assertLess(lo, 0.1)                          # music still uses the whole range
        self.assertGreater(hi, 0.9)

    def test_too_few_onsets(self):
        self.assertIsNone(self.bpm([0.5, 1.0, 1.5]))
        self.assertIsNone(self.bpm([]))

    def test_onset_picker_finds_kicks_in_noise(self):
        """A flux signal at 100 Hz: noise plus a kick every 0.5 s (some quieter). The picker
        fires once per kick, close to it, and never on the noise alone."""
        out = run_node(self.js("onsetPick") + """
let s = 7; const rnd = () => (s = (s * 16807) % 2147483647) / 2147483647;
const st = {}, hits = [], str = [];
for (let k = 0; k < 1000; k++){ const t = k / 100, ph = (t - 0.25) % 0.5;
  let f = 2 + rnd() * 3; if (t > 0.2 && ph >= 0 && ph < 0.03) f += (Math.floor(t / 0.5) % 3 ? 25 : 12);
  const x = onsetPick(st, f, t); if (x){ hits.push(+t.toFixed(2)); str.push(x) } }
console.log(JSON.stringify([hits, str]))""")
        out, strength = out
        self.assertTrue(all(x > 1 for x in strength), strength)     # the onset's strength: flux / threshold
        kicks = [0.25 + 0.5 * k for k in range(20)]
        self.assertGreaterEqual(len(out), 18)
        self.assertLessEqual(len(out), 20, out)
        for h in out:
            self.assertLess(min(abs(h - k) for k in kicks), 0.035, out)

    def test_phase_and_helpers(self):
        on = clicks(120, 6, jitter=0.01, seed=3, start=0.1)
        out = run_node(self.js("beatPhase", "bandFlux", "follow", "autoRange") + f"""
const on = {json.dumps(on)};
const st = {{}}; let v = []; for (let k = 0; k < 50; k++) v.push(autoRange(st, k % 10 / 10, 0.05));
console.log(JSON.stringify([beatPhase(on, 120, 6.1), beatPhase(on, 120, 6.35),
  bandFlux([1, 5, 5, 0], [9, 2, 8, 4], 1, 3), bandFlux(null, [0, 3, 3], 1, 9),
  follow(0, 1, 0.05, 0.05, 0.5), follow(1, 0, 0.05, 0.05, 0.5), Math.min(...v), Math.max(...v)]))""")
        ph0, ph1, f1, f2, up, down, vmin, vmax = out
        self.assertLess(min(ph0, 1 - ph0), 0.06)          # 6.1 s is on a beat (grid from 0.1 s)
        self.assertAlmostEqual(ph1, 0.5, delta=0.06)
        self.assertAlmostEqual(f1, (3 + 4) / 3)
        self.assertAlmostEqual(f2, 3.0)
        self.assertGreater(up, 0.6)                         # quick attack
        self.assertGreater(down, 0.9)                       # slow release
        self.assertGreaterEqual(vmin, 0.0)
        self.assertLessEqual(vmax, 1.0)

    def test_page_wiring(self):
        tog = extract(APP, "listenToggle")
        self.assertIn("echoCancellation: false", tog)
        self.assertIn("noiseSuppression: false", tog)
        self.assertIn("autoGainControl: false", tog)
        self.assertIn("NotAllowedError", tog)
        self.assertIn("listenSupport()", tog)
        self.assertIn("window.isSecureContext", extract(APP, "listenSupport"))
        self.assertIn("remote_url", extract(APP, "listenExplain"))
        self.assertIn("'X-Requested-With': 'lights'", extract(APP, "listenSend"))
        self.assertIn("1 / 12", extract(APP, "listenTick"))        # at most ~12 updates a second
        self.assertIn("visibilitychange", APP)
        self.assertIn("listenStop('Listening stopped when the screen went off", APP)
        # the screen stays on while listening (auto-lock would hide the page and stop it)
        self.assertIn("navigator.wakeLock.request('screen')", extract(APP, "listenWake"))
        self.assertIn("keep the screen on", extract(APP, "listenTick"))
        self.assertIn("listenWake(LS)", tog)
        self.assertIn("was.wake.release()", extract(APP, "listenStop"))
        # the AudioContext is made and resumed inside the tap, before the await, and the
        # analyser reaches the output through a muted gain
        self.assertLess(tog.index("new AC()"), tog.index("await navigator.mediaDevices.getUserMedia"))
        self.assertLess(tog.index("ac.resume()"), tog.index("await navigator.mediaDevices.getUserMedia"))
        self.assertIn("mute.gain.value = 0", tog)
        self.assertIn("mute.connect(ac.destination)", tog)
        self.assertIn("ac.state === 'running'", tog)
        # phones tell themselves apart; tempo and lock are forgotten with the music
        self.assertIn("client: LISTEN_ID", extract(APP, "listenSend"))
        self.assertIn("client: LISTEN_ID", extract(APP, "listenStop"))
        tick = extract(APP, "listenTick")
        self.assertIn("L.cand = null", tick)
        self.assertIn("L.lock = {}", tick)
        self.assertIn("e.conf > 0.45", tick)
        self.assertIn("phaseLock(", tick)
        self.assertIn("lockPhase(", extract(APP, "listenSend"))
        self.assertIn("'audio_offset_ms', 'settings'", APP)          # the Sync offset slider
        self.assertIn('id="kSync"', Path("goveelights/web/app.html").read_text(encoding="utf-8"))


# ------------------------------------------------------------------ server state
class AudioStateTests(unittest.TestCase):
    def test_validation(self):
        s = A.AudioState()
        good = {"level": 0.5, "bass": 0.2}
        s.update(good, now=10.0)
        for bad in ({}, {"level": 0.5}, {"level": 2, "bass": 0}, {"level": "x", "bass": 0},
                    {**good, "bpm": 500}, {**good, "bpm": "120"}, {**good, "phase": 1.5},
                    {**good, "beat": "yes"}, {**good, "ago": -1}, {**good, "rtt": 1e9},
                    {"level": float("nan"), "bass": 0}, {"level": True, "bass": 0}, [1]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                s.update(bad, now=11.0)
        self.assertEqual(s.updates, 1)                       # nothing taken from a bad update

    def test_hold_fade_and_none(self):
        s = A.AudioState()
        self.assertIsNone(s.snapshot(0.0))
        s.update({"level": 0.8, "bass": 0.6, "bpm": 120, "phase": 0.0}, now=100.0)
        a = s.snapshot(101.0)
        self.assertEqual(a["presence"], 1.0)
        self.assertAlmostEqual(a["level"], 0.8)
        self.assertAlmostEqual(s.snapshot(100.0 + A.HOLD + A.FADE / 2)["presence"], 0.5)
        self.assertAlmostEqual(s.snapshot(100.0 + A.HOLD + A.FADE / 2)["level"], 0.4)
        self.assertIsNone(s.snapshot(100.0 + A.HOLD + A.FADE + 0.01))
        s.clear()
        self.assertIsNone(s.snapshot(100.5))

    def test_phase_alignment_corrects_for_network_delay(self):
        s = A.AudioState()
        s.offset = 0.0
        # the phone was exactly on a beat when it sent; the trip took 100 ms (rtt 200)
        s.update({"level": 0.5, "bass": 0.5, "bpm": 120, "phase": 0.0, "rtt": 200}, now=50.0)
        a = s.snapshot(50.0)
        self.assertAlmostEqual(a["beat_age"], 0.1, places=6)
        self.assertAlmostEqual(s.snapshot(50.4)["beat_age"], 0.0, places=6)      # next beat at 50.4
        # no tempo yet: the onset itself, sent 150 ms after it happened, with 40 ms one way
        s2 = A.AudioState()
        s2.offset = 0.0
        s2.update({"level": 0.5, "bass": 0.5, "beat": True, "ago": 150, "rtt": 80}, now=20.0)
        b = s2.snapshot(20.0)
        self.assertIsNone(b["phase"])
        self.assertAlmostEqual(b["beat_age"], 0.19, places=6)

    def test_sync_offset_renders_ahead(self):
        s = A.AudioState()
        self.assertAlmostEqual(s.offset, A.OFFSET_MS / 1000)
        s.offset = 0.08
        s.update({"level": 0.5, "bass": 0.5, "bpm": 120, "phase": 0.0}, now=50.0)
        self.assertAlmostEqual(s.snapshot(50.0)["beat_age"], 0.08, places=6)   # the beat shows 80 ms early
        s2 = A.AudioState()
        s2.offset = 0.05
        s2.update({"level": 0.5, "bass": 0.5, "beat": True, "ago": 100}, now=20.0)
        self.assertAlmostEqual(s2.snapshot(20.0)["beat_age"], 0.15, places=6)

    def test_beat_grid_is_phase_locked_not_replaced(self):
        """60 s at 120 BPM, 12 updates a second whose phase jitters (gaussian, 10-50 ms):
        the server's grid wraps once a beat (120 times), and the effects count 120 beats."""
        from goveelights.scene import Scene
        L = house()
        for sd in (0.01, 0.03, 0.05):
            with self.subTest(jitter=sd):
                rng = random.Random(int(sd * 1000))
                s = A.AudioState()
                sc = Scene.from_dict({"layers": [{"effect": "beat_chase", "params": {"flip": 0, "speed": 0}}]})
                wraps, prev, nxt = 0, None, 100.0
                for k in range(25 * 60):
                    t = 100.0 + k / 25
                    while nxt <= t:                     # the phone's phase is off by the jitter
                        ph = ((nxt - 100.0 + rng.gauss(0, sd)) / 0.5) % 1.0
                        s.update({"level": 0.5, "bass": 0.5, "bpm": 120, "phase": ph}, now=nxt)
                        nxt += 1 / 12
                    a = s.snapshot(t)
                    if prev is not None and a["beat_age"] < prev - 0.03:
                        wraps += 1
                    prev = a["beat_age"]
                    sc.render(t, L, {"bpm": 120}, a)
                self.assertAlmostEqual(wraps, 120, delta=2)
                self.assertAlmostEqual(sc.layers[0].state["n"], 120, delta=2)

    def test_relocks_after_a_tempo_change_or_a_gap(self):
        s = A.AudioState()
        s.offset = 0.0
        s.update({"level": 0.5, "bass": 0.5, "bpm": 120, "phase": 0.0}, now=10.0)
        s.update({"level": 0.5, "bass": 0.5, "bpm": 120, "phase": 0.5}, now=10.1)   # half a beat out: moves a little
        self.assertAlmostEqual(s.snapshot(10.1)["phase"], 0.2 + 0.25 * A.GAIN, places=6)
        s.update({"level": 0.5, "bass": 0.5, "bpm": 140, "phase": 0.5}, now=10.2)   # a new song: taken outright
        self.assertAlmostEqual(s.snapshot(10.2)["phase"], 0.5, places=6)
        s.update({"level": 0.5, "bass": 0.5, "bpm": 140, "phase": 0.1}, now=12.0)   # after a gap: outright
        self.assertAlmostEqual(s.snapshot(12.0)["phase"], 0.1, places=6)

    def test_music_confidence_scales_presence(self):
        s = A.AudioState()
        s.update({"level": 0.4, "bass": 0.4, "music": 0.0}, now=10.0)
        a = s.snapshot(10.0)
        self.assertEqual(a["presence"], 0.0)
        ctx = Ctx(house(), [], 10.0, 0.05, 120.0, {}, a)
        p = A.pulse(ctx, 0.25, idle=True)
        self.assertEqual(p["music"], 0.0)
        self.assertAlmostEqual(p["beat"], 1.0)                 # the master tempo's beat, not a dead one
        s.update({"level": 0.4, "bass": 0.4, "music": 0.5}, now=10.5)
        self.assertAlmostEqual(s.snapshot(10.5)["presence"], 0.5)
        with self.assertRaises(ValueError):
            s.update({"level": 0.4, "bass": 0.4, "music": 2}, now=11.0)

    def test_several_phones(self):
        s = A.AudioState()
        s.offset = 0.0
        ra = s.update({"level": 0.9, "bass": 0.9}, now=10.0, client="a")
        rb = s.update({"level": 0.1, "bass": 0.1}, now=10.01, client="b")
        self.assertTrue(ra["active"])
        self.assertFalse(rb["active"])
        self.assertAlmostEqual(s.snapshot(10.02)["level"], 0.9)         # the first one keeps the lights
        s.clear("b")                                                     # b's stop doesn't cut a off
        self.assertAlmostEqual(s.snapshot(10.03)["level"], 0.9)
        s.update({"level": 0.2, "bass": 0.2}, now=10.04, client="b")
        s.clear("a")
        self.assertAlmostEqual(s.snapshot(10.05)["level"], 0.2)         # b takes over when a stops
        self.assertTrue(s.is_active("b", 10.05))
        # more than MAX_RATE a second from one phone: the extra ones are dropped
        self.assertTrue(s.update({"level": 0.3, "bass": 0.3}, now=10.05, client="b")["dropped"])
        self.assertAlmostEqual(s.snapshot(10.05)["level"], 0.2)
        # a phone that went quiet hands over to the other one
        s.update({"level": 0.7, "bass": 0.7}, now=10.5, client="c")
        self.assertAlmostEqual(s.snapshot(10.6)["level"], 0.2)
        s.update({"level": 0.7, "bass": 0.7}, now=13.2, client="c")
        self.assertAlmostEqual(s.snapshot(13.2)["level"], 0.7)

    def test_clear_takes_the_lock(self):
        """{stop: true} comes from an HTTP thread while frames render: clear() must wait for
        the lock, or snapshot() can see bpm vanish half-way."""
        s = A.AudioState()
        s.update({"level": 0.5, "bass": 0.5, "bpm": 120, "phase": 0.0}, now=10.0)
        with s.lock:
            th = threading.Thread(target=s.clear)
            th.start()
            th.join(0.2)
            self.assertTrue(th.is_alive())                      # blocked while a frame holds the lock
            self.assertIsNotNone(s.sources)
            self.assertTrue(s.sources)
        th.join(2)
        self.assertFalse(th.is_alive())
        self.assertIsNone(s.snapshot(10.0))

    def test_pulse_follows_the_layers_smooth_grid(self):
        a = A.synthetic(0.2, 120)                               # the music says 0.4 of a beat in
        ctx = Ctx(house(), [], 1.0, 0.05, 120.0, {}, a, 7.25)  # the layer's glided grid says 0.25
        p = A.pulse(ctx, 0.25, True, ctx.beats)
        self.assertAlmostEqual(p["phase"], 0.25)
        self.assertAlmostEqual(p["beat_age"], 0.125)
        self.assertEqual(p["count"], 7)
        self.assertIsNone(A.pulse(ctx, 0.25, True)["count"])  # without the grid: straight from the music

    def test_pulse_falls_back_to_tempo(self):
        ctx = Ctx(house(), [], 10.0, 0.05, 120.0, {})
        p = A.pulse(ctx, 0.25, idle=True)
        self.assertEqual(p["music"], 0.0)
        self.assertAlmostEqual(p["beat"], 1.0)                 # 10 s at 120 BPM is on the beat
        self.assertEqual(A.pulse(ctx, 0.25, idle=False)["beat"], 0.0)
        ctx.audio = A.synthetic(0.25, 120)                     # half a beat in
        p = A.pulse(ctx, 0.25, idle=True)
        self.assertEqual(p["music"], 1.0)
        self.assertAlmostEqual(p["beat"], math.exp(-1.0))


class ModulationSourceTests(unittest.TestCase):
    def test_music_sources(self):
        spec = {"source": "level", "depth": 0.5}
        self.assertEqual(modulation.apply(spec, 10, 0, 40, 3.0), 10)           # no music: unchanged
        a = {"level": 1.0, "bass": 0.0, "beat_age": 0.0, "phase": 0.0, "bpm": 120, "presence": 1.0}
        self.assertAlmostEqual(modulation.apply(spec, 10, 0, 40, 3.0, audio=a), 20)
        self.assertAlmostEqual(modulation.apply({"source": "bass", "depth": 1}, 10, 0, 40, 3.0, audio=a), 10)
        self.assertAlmostEqual(modulation.apply({"source": "beat", "depth": 1}, 10, 0, 40, 3.0, audio=a), 30)
        # idle: keeps time with the tempo without music (on the beat at 3.0 s, 120 BPM)
        self.assertAlmostEqual(modulation.apply({"source": "beat", "depth": 1, "idle": True}, 10, 0, 40, 3.0), 30)
        self.assertEqual(modulation.apply({"source": "beat", "depth": 1, "idle": True}, 10, 0, 40, 3.0, is_int=True), 30)

    def test_synced_lfo_does_not_jump_on_a_tempo_change(self):
        sc =Scene.from_dict({"layers": [{"effect": "chase", "params": {"speed": 10},
                                          "mod": {"spacing": {"sync": True, "beats": 4, "depth": 1}}}]})
        L = house()
        for k in range(2000):                                   # 100 s at 120 BPM
            sc.render(k / 20, L, {"bpm": 120})
        before = sc.layers[0].beats
        sc.render(100.0, L, {"bpm": 121})
        self.assertAlmostEqual(sc.layers[0].beats - before, 0.05 * 121 / 60, places=6)

    def test_beat_grid_keeps_up_with_the_music_at_any_master_speed(self):
        """The music runs in real time: with the master Speed at 0, 0.5, 2 or 3 the layer's
        beat grid still sits on the music's beat (and without music Speed scales it)."""
        L = house()
        for ms in (0.0, 0.5, 1.0, 2.0, 3.0):
            with self.subTest(speed=ms):
                sc = Scene.from_dict({"layers": [{"effect": "solid"}]})
                worst = 0.0
                for k in range(400):
                    t = k / 25
                    a = A.synthetic(t + 0.17, 120)
                    sc.render(t, L, {"bpm": 120, "speed": ms}, a)
                    if t > 3:
                        worst = max(worst, abs((sc.layers[0].beats - a["phase"] + 0.5) % 1 - 0.5))
                self.assertLess(worst, 0.02)
        sc = Scene.from_dict({"layers": [{"effect": "solid"}]})
        sc.render(0.0, L, {"bpm": 120, "speed": 2})
        b0 = sc.layers[0].beats
        sc.render(1.0 / 4, L, {"bpm": 120, "speed": 2})
        self.assertAlmostEqual(sc.layers[0].beats - b0, 1.0)      # no music: 2x speed, 2x beats

    def test_beat_grid_glides_onto_the_music(self):
        sc = Scene.from_dict({"layers": [{"effect": "solid"}]})
        L = house()
        for k in range(200):
            t = k / 20
            a = A.synthetic(t + 0.17, 120)                      # the music is 0.17 s ahead
            sc.render(t, L, {"bpm": 120}, a)
        err = (sc.layers[0].beats - A.synthetic(199 / 20 + 0.17, 120)["phase"] + 0.5) % 1 - 0.5
        self.assertLess(abs(err), 0.01)


class MusicEffectTests(unittest.TestCase):
    def test_beat_fx(self):
        L = house()
        idx = list(range(L.n))
        frame = [(0.5, 0.5, 0.5)] * L.n
        f = FX["beat"]
        P = f.resolve({"amount": 0.6, "flash": 0.5})
        self.assertIs(f.fn(frame, Ctx(L, idx, 1.0, 0.05, 120.0, {}), P, {}), frame)   # no music: untouched
        on = f.fn(frame, Ctx(L, idx, 1.0, 0.05, 120.0, {}, A.synthetic(0.0, 120)), P, {})
        off = f.fn(frame, Ctx(L, idx, 1.0, 0.05, 120.0, {}, A.synthetic(0.4, 120)), P, {})
        self.assertGreater(on[0][0], 0.7)                        # flash on the beat
        self.assertLess(off[0][0], 0.35)                         # dim between beats
        tempo = f.resolve({"idle": "tempo"})
        self.assertIsNot(f.fn(frame, Ctx(L, idx, 1.25, 0.05, 120.0, {}), tempo, {}), frame)

    def test_music_rows_fill_from_the_bottom_and_change_colour_on_beats(self):
        from goveelights.effects_ext.occasions import field
        L = house()
        sc = Scene.from_dict({"layers": [{"effect": "music_rows"}]})
        F = field(L, L.indices("all"))
        seen, lit_rows = set(), []
        for k in range(80):
            t = k / 20
            cols = sc.render(t, L, {"bpm": 120}, A.synthetic(t, 120))
            valid(self, cols, L.n)
            row_v = [max(max(cols[i]) for i in r) for r in F["rows"]]
            lit_rows.append(sum(v > 0.5 for v in row_v))
            # rows light bottom-up: a lit row never sits above a dark one
            on = [v > 0.5 for v in row_v]
            self.assertEqual(on, sorted(on, reverse=True), (t, row_v))
            seen.add(tuple(round(x, 2) for x in cols[F["rows"][0][0]]))
        self.assertGreaterEqual(max(lit_rows), 5)                 # big kicks reach the top
        self.assertLessEqual(min(lit_rows), 2)                    # and fall back between them
        self.assertGreater(len({c for c in seen if max(c) > 0.5}), 4)  # colours change

    def test_a_beat_counts_once_even_if_the_grid_glides_back(self):
        from goveelights.effects_ext.music import _new_beat
        st, got = {}, []
        for c, age in ((4, 0.49), (5, 0.005), (4, 0.495), (5, 0.01), (5, 0.25), (6, 0.0), (6, 0.1)):
            got.append(_new_beat(st, {"beat_age": age, "count": c}))
        self.assertEqual(got, [False, True, False, False, False, True, False])
        # without a count (no layer grid): a small backward correction isn't a beat, a wrap is
        st = {}
        got = [_new_beat(st, {"beat_age": a}) for a in (0.30, 0.34, 0.29, 0.40, 0.02)]
        self.assertEqual(got, [False, False, False, False, True])

    def test_beat_chase_jumps_on_beats(self):
        L = house()
        fx = EFFECTS["beat_chase"]
        P = fx.resolve({"speed": 0, "jump": 5, "flip": 0})
        st = {}
        pos = []
        for k in range(41):
            t = k / 20
            fx.fn(t, Ctx(L, L.indices("all"), t, 0.05 if k else 0.0, 120.0, {}, A.synthetic(t, 120)), P, st)
            pos.append(st["pos"])
        self.assertAlmostEqual(pos[39], 3 * 5, delta=0.3)           # 3 beats by 1.95 s (t=0 has no beat before it)
        self.assertGreater(pos[40] - pos[39], 2.5)                  # the 4th jump is under way
        self.assertLess(pos[9] - pos[2], 0.5)                       # still between beats


class PresetTests(unittest.TestCase):
    def test_music_presets_render_in_budget_with_and_without_music(self):
        L = house()
        for pid in MUSIC_PRESETS:
            for music in (False, True):
                with self.subTest(preset=pid, music=music):
                    sc = Scene.from_dict(load(pid))
                    au = (lambda t: A.synthetic(t, 124)) if music else (lambda t: None)
                    for k in range(20):
                        sc.render(k / 20, L, {"bpm": 124}, au(k / 20))
                    best = None
                    for _ in range(3):
                        t0 = time.perf_counter()
                        for k in range(40):
                            t = 1 + k / 20
                            cols = sc.render(t, L, {"bpm": 124}, au(t))
                        ms = (time.perf_counter() - t0) * 1000 / 40
                        best = ms if best is None else min(best, ms)
                    valid(self, cols, L.n)
                    self.assertLess(best, BUDGET_MS, f"{pid}: {best:.1f} ms/frame")

    def test_party_presets_react_only_when_music_plays(self):
        L = house()
        for pid in ("plasma-party", "glitter-ball"):
            with self.subTest(preset=pid):
                a, b = Scene.from_dict(load(pid)), Scene.from_dict(load(pid))
                diff = 0.0
                for k in range(40):
                    t = k / 20
                    x = a.render(t, L)
                    y = b.render(t, L, None, A.synthetic(t, 120))
                    diff = max(diff, max(abs(p - q) for c, d in zip(x, y) for p, q in zip(c, d)))
                self.assertGreater(diff, 0.2)

    def test_new_presets_are_tagged(self):
        for pid in ("music-pulse", "party-beat"):
            self.assertIn("Music", load(pid)["tags"])
            self.assertIn("Party", load(pid)["tags"])
        self.assertEqual(EFFECTS["music_rows"].group, "Music")


# ------------------------------------------------------------------ the endpoint
class BeatEndpointTests(unittest.TestCase):
    def setUp(self):
        self.r = Rig(self, datetime(2026, 12, 5, 20, 0).astimezone())
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.auth = Auth(Path(tmp.name) / "auth.json")
        srv = Server(("127.0.0.1", 0), make_handler(self.r.ctl, lambda: None, self.auth))
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        self.url = f"http://127.0.0.1:{srv.server_address[1]}/api/beat"

    def post(self, body, headers=None):
        h = {"Content-Type": "application/json", "X-Requested-With": "lights", **(headers or {})}
        req = urllib.request.Request(self.url, json.dumps(body).encode(), method="POST", headers={k: v for k, v in h.items() if v})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_sets_tempo_and_exposes_audio(self):
        e = self.r.ctl.engine
        code, j = self.post({"level": 0.7, "bass": 0.9, "bpm": 127.6, "phase": 0.25, "beat": True, "ago": 40, "rtt": 60})
        self.assertEqual((code, j["ok"], j["bpm"]), (200, True, 128.0))
        self.assertEqual(e.master["bpm"], 128.0)
        a = e.audio.snapshot(e.wall())
        self.assertAlmostEqual(a["level"], 0.7, places=3)
        self.assertAlmostEqual(a["bpm"], 127.6)
        self.assertEqual(self.r.ctl.status()["music"], 1.0)
        # a wobble under 1 BPM leaves the tempo alone; the config isn't rewritten for it
        self.post({"level": 0.7, "bass": 0.9, "bpm": 128.4, "phase": 0.5})
        self.assertEqual(e.master["bpm"], 128.0)
        saved = json.loads(Path(self.r.tmp, "cfg.json").read_text()).get("master", {})
        self.assertNotEqual(saved.get("bpm"), 128.0)
        # the engine renders with it
        self.r.ctl.play_preset("music-pulse", fade=0)
        e.render(e.now())
        # stop: gone at once
        self.assertEqual(self.post({"stop": True})[0], 200)
        self.assertIsNone(e.audio.snapshot(e.wall()))
        self.assertEqual(self.r.ctl.status()["music"], 0.0)

    def test_the_music_tempo_never_reaches_the_config(self):
        """Moving Speed, Hue or the palette while (or after) listening saves those, not the
        song's tempo over the owner's tap tempo."""
        ctl, e = self.r.ctl, self.r.ctl.engine
        ctl.set_master({"bpm": 100})                                  # the owner's tap tempo
        self.post({"level": 0.7, "bass": 0.9, "bpm": 140, "phase": 0.0})
        self.assertEqual(e.master["bpm"], 140.0)
        ctl.set_master({"speed": 1.5})
        ctl.set_master({"hue": 0.25, "palette": ""})
        saved = json.loads(Path(self.r.tmp, "cfg.json").read_text())["master"]
        self.assertEqual(saved["bpm"], 100.0)
        self.assertEqual((saved["speed"], saved["hue"]), (1.5, 0.25))
        ctl.set_master({"bpm": 90})                                   # tapping still saves
        self.assertEqual(json.loads(Path(self.r.tmp, "cfg.json").read_text())["master"]["bpm"], 90.0)

    def test_sync_offset_setting(self):
        ctl, e = self.r.ctl, self.r.ctl.engine
        self.assertEqual(ctl.status()["audio_offset_ms"], round(A.OFFSET_MS))
        ctl.settings({"audio_offset_ms": 120})
        self.assertAlmostEqual(e.audio.offset, 0.12)
        self.assertEqual(ctl.status()["audio_offset_ms"], 120)
        self.assertEqual(json.loads(Path(self.r.tmp, "cfg.json").read_text())["engine"]["audio_offset_ms"], 120)
        ctl.settings({"audio_offset_ms": 5000})
        self.assertEqual(ctl.status()["audio_offset_ms"], 400)
        with self.assertRaises(ValueError):
            ctl.settings({"audio_offset_ms": float("nan")})
        ctl.build_engine()                                            # kept across a rebuild
        self.assertAlmostEqual(ctl.engine.audio.offset, 0.4)

    def test_one_phones_stop_leaves_the_other_listening(self):
        e = self.r.ctl.engine
        self.post({"client": "phoneA", "level": 0.8, "bass": 0.8, "bpm": 124, "phase": 0.0})
        self.post({"client": "phoneB", "level": 0.2, "bass": 0.2, "bpm": 90, "phase": 0.0})
        self.assertEqual(e.master["bpm"], 124.0)                      # only the active phone sets the tempo
        self.assertEqual(self.post({"client": "phoneB", "stop": True})[0], 200)
        self.assertAlmostEqual(e.audio.snapshot(e.wall())["level"], 0.8)
        self.assertEqual(self.post({"client": "../x", "level": 0.2, "bass": 0.2})[0], 400)
        self.post({"client": "phoneA", "stop": True})
        self.assertIsNone(e.audio.snapshot(e.wall()))

    def test_overlays_hear_the_music(self):
        e = self.r.ctl.engine
        self.r.ctl.play_preset("music-pulse", fade=0)
        seen = []

        class Spy:
            def render(self, t, layout, master=None, audio=None):
                seen.append((master, audio))
                return [(0.0, 0.0, 0.0)] * layout.n
        e.overlays["spy"] = (Spy(), "add", 1.0)
        self.post({"level": 0.7, "bass": 0.9, "bpm": 128, "phase": 0.0})
        e.render(e.now())
        master, audio = seen[-1]
        self.assertEqual(master["bpm"], 128.0)
        self.assertIsNotNone(audio)
        self.assertAlmostEqual(audio["level"], 0.7, places=3)

    def test_decays_without_updates(self):
        e = self.r.ctl.engine
        self.post({"level": 0.5, "bass": 0.5})
        t = e.wall()
        self.assertIsNotNone(e.audio.snapshot(t + 1.9))
        self.assertLess(e.audio.snapshot(t + A.HOLD + 0.8)["presence"], 0.3)
        self.assertIsNone(e.audio.snapshot(t + A.HOLD + A.FADE + 0.1))

    def test_validation(self):
        for bad in ({"level": 3, "bass": 0}, {"bass": 0.1}, {"level": 0.1, "bass": 0.1, "bpm": 9999},
                    {"level": 0.1, "bass": 0.1, "beat": 1}, [1, 2]):
            with self.subTest(bad=bad):
                code, j = self.post(bad)
                self.assertEqual(code, 400, j)
                self.assertFalse(j["ok"])
        self.assertIsNone(self.r.ctl.engine.audio.snapshot())
        self.assertEqual(self.r.ctl.engine.master["bpm"], 120.0)

    def test_remote_address_is_remembered_for_the_listen_hint(self):
        ctl = self.r.ctl
        self.assertEqual(ctl.status()["remote_url"], "")
        for bad in ("", "evil.example/<script>", "a b", "x" * 300):
            ctl.note_remote(bad)
        self.assertEqual(ctl.status()["remote_url"], "")
        ctl.note_remote("Lights.Example.com")
        self.assertEqual(ctl.status()["remote_url"], "https://lights.example.com")
        saved = json.loads(Path(self.r.tmp, "cfg.json").read_text())["server"]["remote_url"]
        self.assertEqual(saved, "https://lights.example.com")
        # the whole read-modify-write happens under the config lock (it runs on an HTTP thread)
        with ctl.cfg.lock:
            th = threading.Thread(target=ctl.note_remote, args=("other.example.com",))
            th.start()
            th.join(0.2)
            self.assertTrue(th.is_alive())                  # waits for the lock (it's held here)
            self.assertEqual(ctl.cfg.data["server"]["remote_url"], "https://lights.example.com")
        th.join(2)
        self.assertEqual(ctl.status()["remote_url"], "https://other.example.com")

    def test_csrf_and_auth_like_other_posts(self):
        code, j = self.post({"level": 0.5, "bass": 0.5}, {"X-Requested-With": ""})
        self.assertEqual(code, 403)
        self.assertIsNone(self.r.ctl.engine.audio.snapshot())
        self.auth.set_password("a long enough password")
        with self.auth.lock:
            self.auth.data["lan_without_login"] = False
            self.auth._save()
        code, j = self.post({"level": 0.5, "bass": 0.5})
        self.assertEqual(code, 401)
        self.assertTrue(j["login"])
        self.assertIsNone(self.r.ctl.engine.audio.snapshot())


if __name__ == "__main__":
    unittest.main()
