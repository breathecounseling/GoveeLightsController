"""Nightly-experience integration: dusk intro at lights-on, in-game scoreboards and the
built-in shows. The controller is driven with a fake clock and no network: no UDP socket,
no background threads, no render loop."""
import json
import shutil
import sys
import tempfile
import threading
import types
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, "tests")
from test_rack import house  # noqa: E402

from goveelights import controller as C  # noqa: E402
from goveelights import schedule as sched  # noqa: E402
from goveelights import shows, shows_builtin  # noqa: E402
from goveelights.config import Config  # noqa: E402
from goveelights.engine import Engine  # noqa: E402
from goveelights.output import MemoryOutput  # noqa: E402
from goveelights.scene import Scene  # noqa: E402

PRESETS = Path("presets")


# ------------------------------------------------------------ test rig
class Clock:
    def __init__(self, t: float):
        self.t = float(t)

    def time(self) -> float:
        return self.t


class _NoThread:
    def __init__(self, *a, **k):
        pass

    def start(self):
        pass


class _FakeLan:
    def status(self, ip, timeout=1.0):
        return None

    def scan(self, ips):
        pass


class _FakeDiag:
    def __init__(self, cfg, bind_ip=""):
        self.lan, self.streamers = _FakeLan(), {}

    def scan(self, extra=None):
        pass

    def release_all(self):
        pass


class _FakeOut(MemoryOutput):
    def __init__(self, *a, **k):
        super().__init__()
        self._last = {}

    def start(self, force=False):
        self.started = True


class _QuietEngine(Engine):
    def start(self, live=True):          # no render thread: the tests render on demand
        if live:
            self.set_live(True)


class Rig:
    """Patches the controller module for one test and builds a Controller on the real house."""

    def __init__(self, test: unittest.TestCase, now: datetime, cfg_extra: dict | None = None, tmp: str | None = None):
        self.clock = Clock(now.timestamp())
        clock = self.clock

        class FakeDT(datetime):
            @classmethod
            def now(cls, tz=None):
                d = datetime.fromtimestamp(clock.t)
                return d.astimezone(tz) if tz else d

        saved = {k: getattr(C, k) for k in ("time", "datetime", "threading", "DiagApp", "GoveeRazerOutput", "Engine")}
        test.addCleanup(lambda: [setattr(C, k, v) for k, v in saved.items()])
        C.time = types.SimpleNamespace(time=clock.time)
        C.datetime = FakeDT
        C.threading = types.SimpleNamespace(RLock=threading.RLock, Event=threading.Event, Thread=_NoThread)
        C.DiagApp, C.GoveeRazerOutput, C.Engine = _FakeDiag, _FakeOut, _QuietEngine
        if tmp is None:
            tmp = tempfile.mkdtemp()
            test.addCleanup(shutil.rmtree, tmp, True)
        self.tmp = tmp
        path = Path(tmp) / "cfg.json"
        cfg = Config(path)
        if not cfg.devices:
            cfg.import_layout("layouts/house.json")
            for k, d in enumerate(cfg.devices.values()):
                d["ip"] = f"10.0.0.{k + 10}"
        for k, v in (cfg_extra or {}).items():
            if isinstance(v, dict) and isinstance(cfg.data.get(k), dict):
                cfg.data[k].update(v)
            else:
                cfg.data[k] = v
        cfg.save()
        self.ctl = C.Controller(cfg, presets=C.PresetStore(PRESETS))

    def tick(self, seconds: float = 1.0, step: float = 1.0, schedule: bool = True):
        """Advance the fake clock, doing the controller's once-a-second work (and the
        10-second schedule check) on the way."""
        end = self.clock.t + seconds
        while self.clock.t < end - 1e-9:
            self.clock.t = min(end, self.clock.t + step)
            self.ctl._tick_once(self.clock.t)
            if schedule and int(self.clock.t) % 10 == 0:
                self.ctl._schedule_tick()

    def effects(self) -> list[str]:
        sc = self.ctl.engine.scene
        return [layer.effect for layer in sc.layers] if sc else []


def evening_on(d: date, sch: dict) -> datetime:
    return sched.window(d, sch)[0]


SCH = {"enabled": True, "on": "sunset", "on_offset_min": 0, "off_time": "23:00",
       "default": {"preset": "warm-white"}, "rules": []}


def scene_effects_of(d: dict) -> list[str]:
    return [l["effect"] for l in (d or {}).get("layers", [])]


def scene_effects(ctl) -> list[str]:
    return [l["effect"] for l in (ctl.scene_dict or {}).get("layers", [])]


# --------------------------------------------------------------- dusk intro
class DuskIntroTests(unittest.TestCase):
    D = date(2026, 12, 3)

    def rig(self, minutes=20, at_min=0.5, sch=None, tmp=None):
        sch = {**SCH, "dusk_intro_minutes": minutes, **(sch or {})}
        on = evening_on(self.D, sch)
        r = Rig(self, on + timedelta(minutes=at_min), {"schedule": sch}, tmp=tmp)
        r.on = on
        r.ctl._schedule_tick()
        return r

    def test_off_by_default(self):
        r = self.rig(minutes=0)
        self.assertEqual(r.ctl.playing["kind"], "preset")
        self.assertEqual(r.ctl.playing["id"], "warm-white")
        self.assertIsNone(r.ctl.status()["dusk_intro"])

    def test_intro_plays_then_crossfades_into_the_program(self):
        r = self.rig(minutes=20)
        ctl = r.ctl
        self.assertEqual(ctl.playing["kind"], "dusk-intro")
        self.assertEqual(scene_effects(ctl), ["dusk"])
        self.assertEqual(ctl.scene_dict["layers"][0].get("target", "all"), "all")
        self.assertTrue(ctl.engine.live)
        st = ctl.status()
        self.assertEqual(st["playing"]["kind"], "dusk-intro")
        self.assertAlmostEqual(st["dusk_intro"]["left"], 19.5 * 60, delta=70)
        self.assertEqual(st["dusk_intro"]["then"], {"preset": "warm-white"})
        # the dusk sky renders on the house
        cols = ctl.engine.render(1.0)
        self.assertEqual(len(cols), ctl.engine.layout.n)
        self.assertTrue(any(sum(c) > 0.05 for c in cols))
        # schedule checks during the intro don't restart it or jump to the program
        r.tick(10 * 60)
        self.assertEqual(ctl.playing["kind"], "dusk-intro")
        r.tick(10 * 60)
        self.assertEqual(ctl.playing["kind"], "preset")
        self.assertEqual(ctl.playing["id"], "warm-white")
        self.assertIsNone(ctl.dusk_intro)
        self.assertIsNone(ctl.status()["dusk_intro"])
        self.assertEqual(ctl.engine._prev.name, "Dusk intro")   # crossfading out of the dusk sky
        self.assertEqual(ctl.engine._fade_s, 8.0)
        self.assertEqual(ctl.engine.transition, "crossfade")
        self.assertTrue(ctl.engine.live)
        r.tick(120)                                           # stays on the program afterwards
        self.assertEqual(ctl.playing["id"], "warm-white")

    def test_playlist_program_crossfades_too(self):
        r = self.rig(minutes=5, sch={"default": {"playlist": "Christmas"}})
        self.assertEqual(r.ctl.playing["kind"], "dusk-intro")
        r.tick(5 * 60)
        self.assertEqual(r.ctl.playing["kind"], "playlist")
        self.assertEqual(r.ctl.engine._fade_s, 8.0)
        self.assertIsNotNone(r.ctl.playlist)

    def test_restart_mid_window_plays_only_what_is_left(self):
        r = self.rig(minutes=20, at_min=15)
        self.assertEqual(r.ctl.playing["kind"], "dusk-intro")
        self.assertAlmostEqual(r.ctl.dusk_intro["until"] - r.clock.t, 5 * 60, delta=70)
        r2 = self.rig(minutes=20, at_min=25)          # past the intro: straight to the program
        self.assertEqual(r2.ctl.playing["kind"], "preset")
        self.assertIsNone(r2.ctl.dusk_intro)
        r3 = self.rig(minutes=20, at_min=19.8)         # a few seconds left: not worth it
        self.assertEqual(r3.ctl.playing["kind"], "preset")

    def test_manual_play_ends_the_intro(self):
        r = self.rig(minutes=20)
        r.ctl.play_preset("candy-cane")                 # the owner takes over
        self.assertIsNone(r.ctl.dusk_intro)
        r.tick(25 * 60)
        self.assertEqual(r.ctl.playing["id"], "candy-cane")
        r2 = self.rig(minutes=20)
        r2.ctl.stop()
        r2.tick(25 * 60)
        self.assertIsNone(r2.ctl.playing)
        self.assertFalse(r2.ctl.engine.live)

    def test_manual_show_during_the_intro_returns_to_the_program(self):
        r = self.rig(minutes=20)
        ctl = r.ctl
        r.tick(60)
        self.assertTrue(ctl.play_show("Christmas Eve spectacular"))
        self.assertIsNone(ctl.dusk_intro)
        self.assertEqual(ctl.engine._prev.name, "Dusk intro")       # the show fades in from the dusk sky
        self.assertEqual(ctl.interrupt["playing"]["id"], "warm-white")
        self.assertEqual([l["effect"] for l in ctl.interrupt["scene"]["layers"]],
                         scene_effects_of(ctl.presets.get("warm-white")))
        total = sum(c["seconds"] for c in shows_builtin.BUILTIN_SHOWS["Christmas Eve spectacular"]["cues"])
        r.tick(total + 10)
        self.assertIsNone(ctl.interrupt)
        self.assertEqual(ctl.playing["kind"], "preset")
        self.assertEqual(ctl.playing["id"], "warm-white")
        self.assertNotIn("dusk", r.effects())
        self.assertNotEqual(ctl.status()["playing"]["kind"], "dusk-intro")

    def test_live_tap_during_the_intro_goes_to_the_program(self):
        r = self.rig(minutes=20)
        r.tick(30)
        r.ctl.set_live(True)
        self.assertIsNone(r.ctl.dusk_intro)
        self.assertEqual(r.ctl.playing["id"], "warm-white")
        self.assertEqual(r.ctl.engine._fade_s, 8.0)
        self.assertTrue(r.ctl.engine.live)
        r.tick(40 * 60)
        self.assertEqual(r.ctl.playing["id"], "warm-white")
        # Live off during the intro: back on later shows the program, not the dusk sky
        r2 = self.rig(minutes=20)
        r2.ctl.set_live(False)
        self.assertFalse(r2.ctl.engine.live)
        r2.ctl.set_live(True)
        self.assertEqual(r2.ctl.playing["id"], "warm-white")
        self.assertNotIn("dusk", r2.effects())

    def test_intro_over_a_visitor_pick_ends_cleanly(self):
        r = self.rig(minutes=20)
        ctl = r.ctl
        ctl.vqueue.append({"id": "candy-cane", "client": "x"})
        r.tick(2)
        self.assertTrue(ctl.interrupt.get("visitor"))
        ctl.set_live(True)                                       # the owner taps Live mid-pick
        self.assertEqual(ctl.playing["kind"], "event")           # the pick keeps playing ...
        r.tick(70)
        self.assertEqual(ctl.playing["id"], "warm-white")        # ... then the program, not the dusk sky
        self.assertNotIn("dusk", r.effects())

    def test_schedule_save_does_not_replay_the_intro(self):
        r = self.rig(minutes=20)
        ctl = r.ctl
        r.tick(60)
        ctl.set_live(True)                                       # ended by hand
        ctl.resume_schedule()                                    # what a schedule save does
        r.tick(20)
        self.assertIsNone(ctl.dusk_intro)
        self.assertEqual(ctl.playing["id"], "warm-white")
        # saved while the intro is still running: it carries on, not restarted
        r2 = self.rig(minutes=20)
        until = r2.ctl.dusk_intro["until"]
        r2.tick(5 * 60)
        r2.ctl.resume_schedule()
        r2.tick(20)
        self.assertEqual(r2.ctl.playing["kind"], "dusk-intro")
        self.assertEqual(r2.ctl.dusk_intro["until"], until)
        r2.tick(15 * 60)
        self.assertEqual(r2.ctl.playing["id"], "warm-white")
        r2.ctl.resume_schedule()
        r2.tick(20)
        self.assertEqual(r2.ctl.playing["id"], "warm-white")    # not a second intro after it finished

    def test_waits_for_an_interrupt_before_switching(self):
        r = self.rig(minutes=2)
        ctl = r.ctl
        d, _ = shows.celebration("touchdown", ["#0a2343", "#fc4c02"])
        r.tick(60)
        self.assertTrue(ctl.start_interrupt(d, 60, "Touchdown!"))
        r.tick(40)                                     # intro time is up, but the celebration is on
        self.assertEqual(ctl.playing["kind"], "event")
        self.assertIsNotNone(ctl.dusk_intro)
        r.tick(30)
        self.assertIsNone(ctl.interrupt)
        self.assertEqual(ctl.playing["kind"], "preset")

    def test_fixed_on_time_far_from_sunset_has_no_intro(self):
        sch = {"on": "time", "on_time": "12:00"}
        r = self.rig(minutes=20, sch=sch)
        self.assertEqual(r.ctl.playing["kind"], "preset")

    def test_bad_values_are_harmless(self):
        for v in ("abc", None, -5, 1e9):
            r = self.rig(minutes=v)
            self.assertIn(r.ctl.playing["kind"], ("preset", "dusk-intro"))

    def test_lights_off_time_cancels_the_intro(self):
        r = self.rig(minutes=20)
        on, off = sched.window(self.D, r.ctl.cfg.data["schedule"])
        r.clock.t = off.timestamp() + 1
        r.ctl._schedule_tick()
        self.assertIsNone(r.ctl.dusk_intro)
        self.assertFalse(r.ctl.engine.live)


# --------------------------------------------------------------- scoreboard
GAME = {"id": "g1", "state": "in", "team": "DEN", "opponent": "PIT", "score": 17, "opp_score": 10,
        "colors": ["#0a2343", "#fc4c02"], "detail": "Q3 5:32"}


class ScoreboardTests(unittest.TestCase):
    def rig(self, every=10, seconds=20, enabled=True):
        r = Rig(self, datetime(2026, 11, 29, 19, 0).astimezone(),
                {"game": {"enabled": enabled, "scoreboard_every": every, "scoreboard_seconds": seconds, "delay": 30,
                          "teams": [{"league": "nfl", "team": "DEN"}]},
                 "schedule": {"enabled": False}})
        r.ctl.play_preset("warm-white")
        r.ctl.set_live(True)
        r.ctl.games = {"nfl:DEN": dict(GAME)}
        return r

    def live_tick(self, r, seconds, step=2.0, polling=True):
        """polling: the live-data loop keeps refreshing the game (as _poll_game does)."""
        end = r.clock.t + seconds
        while r.clock.t < end:
            r.clock.t += step
            if polling:
                for v in r.ctl.games.values():
                    v["polled"] = r.clock.t
            with r.ctl.lock:
                r.ctl._scoreboard_tick(r.clock.t)
            r.ctl._tick_once(r.clock.t)

    def test_shows_every_n_minutes_and_puts_back_the_program(self):
        r = self.rig()
        ctl = r.ctl
        self.live_tick(r, 30)
        self.assertIsNone(ctl.interrupt)                # waits for the delayed score first
        self.live_tick(r, 14)
        self.assertIsNotNone(ctl.interrupt)
        self.assertTrue(ctl.interrupt.get("scoreboard"))
        self.assertTrue(ctl.playing["name"].startswith("Scoreboard"))
        layers = {l.effect: l for l in ctl.engine.scene.layers}
        self.assertIn("text", layers)
        self.assertEqual(layers["text"].params["source"], "score")
        self.assertEqual(layers["text"].target, "Peak")
        self.live_tick(r, 22)
        self.assertIsNone(ctl.interrupt)
        self.assertEqual(ctl.playing["id"], "warm-white")
        starts = 0
        for _ in range(30 * 30):                       # half an hour of the game: three more
            had = ctl.interrupt is not None
            self.live_tick(r, 2)
            starts += (ctl.interrupt is not None) and not had
        self.assertEqual(starts, 3)

    def test_off_when_disabled_or_no_game(self):
        for r in (self.rig(every=0), self.rig(enabled=False)):
            self.live_tick(r, 15 * 60, step=5)
            self.assertIsNone(r.ctl.interrupt)
        r = self.rig()
        r.ctl.games = {"nfl:DEN": {**GAME, "state": "post"}}
        self.live_tick(r, 15 * 60, step=5)
        self.assertIsNone(r.ctl.interrupt)
        self.assertIsNone(r.ctl._scoreboard_at)

    def test_never_when_lights_off_or_busy(self):
        r = self.rig()
        r.ctl.set_live(False)
        self.live_tick(r, 120)
        self.assertIsNone(r.ctl.interrupt)
        r = self.rig()
        self.assertTrue(r.ctl.play_show("Christmas show"))
        self.live_tick(r, 60)
        self.assertEqual(r.ctl.interrupt["label"], "Show: Christmas show")
        self.assertIn("show", r.ctl.interrupt)
        r = self.rig()
        r.ctl.map_start()
        self.live_tick(r, 60)
        self.assertIsNone(r.ctl.interrupt)
        r.ctl.map_stop()
        r = self.rig()
        r.ctl.enter_diag()
        self.live_tick(r, 60)
        self.assertIsNone(r.ctl.interrupt)

    def test_waits_for_a_pending_celebration_and_yields_to_it(self):
        r = self.rig()
        ctl = r.ctl
        ctl._pending.append((r.clock.t + 1000, "touchdown", GAME["colors"]))
        self.live_tick(r, 120)
        self.assertIsNone(ctl.interrupt)                 # a touchdown is about to be celebrated
        ctl._pending.clear()
        self.live_tick(r, 4)
        self.assertTrue(ctl.interrupt.get("scoreboard"))
        d, secs = shows.celebration("touchdown", GAME["colors"])
        self.assertTrue(ctl.start_interrupt(d, secs, d["name"]))   # the score change wins
        self.assertNotIn("scoreboard", ctl.interrupt)
        self.assertEqual(ctl.playing["name"], "Touchdown!")
        r.tick(secs + 2, schedule=False)
        self.assertEqual(ctl.playing["id"], "warm-white")    # back to the program, not the scoreboard

    def test_showtime_takes_over_from_a_scoreboard(self):
        r = self.rig()
        ctl = r.ctl
        ctl.cfg.data["showtimes"] = [{"show": "Christmas show", "from": "19:01", "to": "19:01", "every": 60}]
        self.live_tick(r, 44)
        self.assertTrue(ctl.interrupt.get("scoreboard"))
        self.live_tick(r, 18)                                  # 19:01: the hourly show starts anyway
        self.assertEqual(ctl.status()["show"], "Christmas show")
        self.assertNotIn("scoreboard", ctl.interrupt)
        self.assertEqual(ctl.interrupt["playing"]["id"], "warm-white")   # and still goes back to the program

    def test_no_scoreboard_on_failing_data(self):
        r = self.rig()
        r.ctl.games = {"nfl:DEN": {**GAME, "error": "timeout"}}
        self.live_tick(r, 120)
        self.assertIsNone(r.ctl.interrupt)

    def test_only_followed_and_freshly_polled_games(self):
        r = self.rig()
        r.ctl.cfg.data["game"]["teams"] = [{"league": "nfl", "team": "KC"}]     # the owner switched teams
        self.live_tick(r, 15 * 60, step=5)
        self.assertIsNone(r.ctl.interrupt)
        r = self.rig()
        r.ctl.games["nfl:DEN"]["polled"] = r.clock.t
        self.live_tick(r, 15 * 60, step=5, polling=False)      # polling stalled: the old 'in' state goes stale
        self.assertIsNone(r.ctl.interrupt)
        self.assertIsNone(r.ctl._scoreboard_at)

    def test_poll_game_records_the_poll_time(self):
        r = self.rig()
        saved = (C.live.fetch_json, C.live.find_game)
        self.addCleanup(lambda: (setattr(C.live, "fetch_json", saved[0]), setattr(C.live, "find_game", saved[1])))
        C.live.fetch_json = lambda url: {}
        C.live.find_game = lambda data, team: dict(GAME)
        r.ctl.games = {}
        r.ctl._poll_game({"league": "nfl", "team": "DEN"}, "nfl:DEN")
        self.assertEqual(r.ctl.games["nfl:DEN"]["polled"], r.clock.t)
        self.live_tick(r, 50, polling=False)
        self.assertTrue(r.ctl.interrupt and r.ctl.interrupt.get("scoreboard"))

    def test_scene_renders_for_any_game(self):
        L = house()
        for g in (GAME, {}, None, {"team": "<script>", "colors": ["bad", "#12"]}, {"colors": ["#ffffff"]},
                  {"team": "KC", "colors": ["#e31837", "#e31837"]}):
            d = shows.scoreboard_scene(g)
            sc = Scene.from_dict(d)
            cols = sc.render(1.0, L)
            self.assertEqual(len(cols), L.n)
            self.assertTrue(all(0 <= v <= 1 for c in cols for v in c))
            chase = d["layers"][0]["params"]["colors"]
            self.assertEqual(len(chase), 2)
            self.assertNotEqual(chase[0], chase[1])
        self.assertEqual(shows.scoreboard_scene(GAME)["layers"][1]["params"]["text"], "GO DEN!")


# ------------------------------------------------------------ built-in shows
class BuiltinShowTests(unittest.TestCase):
    def test_every_cue_preset_exists_and_renders(self):
        L = house()
        ids = {p.stem for p in PRESETS.glob("*.json")}
        from goveelights.engine import Engine as E
        for name, sh in shows_builtin.BUILTIN_SHOWS.items():
            self.assertTrue(sh["cues"], name)
            self.assertIn(sh["end"], ("resume", "loop"))
            for c in sh["cues"]:
                self.assertIn(c.get("transition", "crossfade"), E.TRANSITIONS)
                self.assertGreaterEqual(float(c["seconds"]), 5)
                if c.get("countdown_to"):
                    continue
                self.assertIn(c["preset"], ids, f"{name}: missing preset {c['preset']}")
                Scene.from_dict(json.loads((PRESETS / f"{c['preset']}.json").read_text())).render(1.0, L)

    def test_the_three_shows(self):
        B = shows_builtin.BUILTIN_SHOWS
        xmas = B["Christmas Eve spectacular"]
        secs = sum(c["seconds"] for c in xmas["cues"])
        self.assertTrue(8 * 60 <= secs <= 10 * 60, secs)
        self.assertEqual(xmas["end"], "resume")
        order = [c["preset"] for c in xmas["cues"]]
        self.assertEqual(order[0], "tree-lighting-ceremony")
        self.assertEqual(order[-1], "christmas-tree")
        for p in ("snow-piling-up", "ember-falls", "porch-fireworks", "grand-finale", "text-merry-christmas"):
            self.assertIn(p, order)
        nye = B["New Year's Eve countdown"]["cues"]
        j = next(k for k, c in enumerate(nye) if c.get("countdown_to") == "00:00")
        self.assertAlmostEqual(sum(c["seconds"] for c in nye[:j + 1]), 300, delta=10)   # last ~5 min
        self.assertEqual(nye[j + 1]["preset"], "grand-finale")
        self.assertIn("text-happy-new-year", [c.get("preset") for c in nye[j + 1:]])
        self.assertEqual(B["Winter wonderland loop"]["end"], "loop")
        santa = B["Santa's coming"]
        self.assertEqual(santa["end"], "loop")
        secs = sum(c["seconds"] for c in santa["cues"])
        self.assertTrue(5 * 60 <= secs <= 7 * 60, secs)
        order = [c["preset"] for c in santa["cues"]]
        for p in ("starry-night", "advent-calendar", "starry-night-sleigh", "gift-wrap", "icicles", "christmas-tree"):
            self.assertIn(p, order)

    def test_listed_playable_copyable_not_overwritten(self):
        r = Rig(self, datetime(2026, 12, 24, 19, 0).astimezone())
        ctl = r.ctl
        lst = {s["name"]: s for s in ctl.shows_list()}
        for n in shows_builtin.BUILTIN_SHOWS:
            self.assertTrue(lst[n]["builtin"])
        self.assertFalse(lst["Christmas show"]["builtin"])          # the user's own
        self.assertTrue(480 <= lst["Christmas Eve spectacular"]["seconds"] <= 600)
        # playable
        ctl.play_preset("warm-white")
        self.assertTrue(ctl.play_show("Christmas Eve spectacular"))
        self.assertEqual(ctl.status()["show"], "Christmas Eve spectacular")
        r.tick(600, schedule=False)
        self.assertIsNone(ctl.interrupt)
        self.assertEqual(ctl.playing["id"], "warm-white")
        # copy
        n = ctl.copy_show("Christmas Eve spectacular")
        self.assertEqual(n, "Christmas Eve spectacular (copy)")
        self.assertNotIn("builtin", ctl.cfg.data["shows"][n])
        self.assertEqual(ctl.copy_show("Christmas Eve spectacular"), "Christmas Eve spectacular (copy 2)")
        with self.assertRaises(ValueError):
            ctl.copy_show("No such show")
        # a user show of the same name wins; deleting it brings the built-in back
        ctl.cfg.data["shows"]["Winter wonderland loop"] = {"end": "resume", "cues": [{"preset": "comets", "seconds": 5}]}
        self.assertEqual(ctl.show_def("Winter wonderland loop")["cues"][0]["preset"], "comets")
        lst = {s["name"]: s for s in ctl.shows_list()}
        self.assertTrue(lst["Winter wonderland loop"]["overrides_builtin"])
        self.assertFalse(lst["Winter wonderland loop"]["builtin"])
        del ctl.cfg.data["shows"]["Winter wonderland loop"]
        self.assertEqual(ctl.show_def("Winter wonderland loop")["end"], "loop")
        # saving the shows list never stores untouched built-ins
        saved = shows_builtin.without_builtin_copies({**shows_builtin.builtin_shows(), "Mine": {"cues": []}})
        self.assertEqual(list(saved), ["Mine"])
        edited = shows_builtin.builtin_shows()
        edited["Winter wonderland loop"]["cues"].pop()
        self.assertIn("Winter wonderland loop", shows_builtin.without_builtin_copies(edited))

    def test_api_actions(self):
        """The HTTP API's show actions, called on the handler directly (no server, no port)."""
        from goveelights import app_server
        r = Rig(self, datetime(2026, 12, 24, 19, 0).astimezone())
        H = app_server.make_handler(r.ctl, lambda: None, None)
        h = object.__new__(H)
        res = h._api("show/copy", {"name": "New Year's Eve countdown"})
        self.assertEqual(res["name"], "New Year's Eve countdown (copy)")
        res = h._api("show/copy", {"name": "Winter wonderland loop", "as": "My winter"})
        self.assertEqual(res["name"], "My winter")
        with self.assertRaises(ValueError):
            h._api("show/copy", {"name": "Winter wonderland loop", "as": "My winter"})
        # a UI that sends the built-ins back with the user's shows doesn't store them
        h._api("shows", {"value": {**shows_builtin.builtin_shows(), "Mine": {"end": "resume", "cues": []}}})
        self.assertEqual(list(r.ctl.cfg.data["shows"]), ["Mine"])
        self.assertTrue(h._api("show/play", {"name": "Christmas Eve spectacular"})["ok"])
        self.assertEqual(r.ctl.status()["show"], "Christmas Eve spectacular")
        # the schedule section takes the new setting as it is
        h._api("schedule", {"schedule": {"dusk_intro_minutes": 20}})
        self.assertEqual(r.ctl.cfg.data["schedule"]["dusk_intro_minutes"], 20)

    def test_loop_show_loops(self):
        r = Rig(self, datetime(2026, 12, 24, 19, 0).astimezone())
        ctl = r.ctl
        ctl.play_preset("warm-white")
        self.assertTrue(ctl.play_show("Winter wonderland loop"))
        total = sum(c["seconds"] for c in shows_builtin.BUILTIN_SHOWS["Winter wonderland loop"]["cues"])
        r.tick(total * 2 + 30, step=5, schedule=False)
        self.assertEqual(ctl.status()["show"], "Winter wonderland loop")
        self.assertEqual(ctl.interrupt["show"]["idx"], 0)

    def test_cues_do_not_drift(self):
        r = Rig(self, datetime(2026, 12, 24, 19, 0, 0).astimezone())
        cues = shows_builtin.BUILTIN_SHOWS["Christmas Eve spectacular"]["cues"]
        t0 = r.clock.t
        r.ctl.play_show("Christmas Eve spectacular")
        r.tick(sum(c["seconds"] for c in cues[:-1]) + 1.5, step=0.7, schedule=False)    # a jittery tick
        st = r.ctl.interrupt["show"]
        self.assertEqual(st["idx"], len(cues) - 1)
        self.assertAlmostEqual(st["next_at"] - t0, sum(c["seconds"] for c in cues), delta=0.01)
        self.assertAlmostEqual(r.ctl.interrupt["until"] - st["next_at"], 3, delta=0.01)


# ----------------------------------------------------------- New Year's Eve
class NewYearTests(unittest.TestCase):
    NAME = "New Year's Eve countdown"

    def cues(self):
        return shows_builtin.BUILTIN_SHOWS[self.NAME]["cues"]

    def rig(self, h, m, s=0, **extra):
        r = Rig(self, datetime(2026, 12, 31, h, m, s).astimezone(), {"schedule": {"enabled": False}, **extra})
        r.ctl.play_preset("warm-white")
        r.ctl.set_live(True)
        r.midnight = datetime(2027, 1, 1, 0, 0).astimezone().timestamp()
        return r

    def j(self):
        return next(k for k, c in enumerate(self.cues()) if c.get("countdown_to"))

    def test_countdown_hits_zero_at_midnight(self):
        r = self.rig(23, 55)
        ctl = r.ctl
        self.assertTrue(ctl.play_show(self.NAME))
        st = ctl.interrupt["show"]
        self.assertAlmostEqual(st["anchor"], r.midnight, delta=0.5)
        r.tick(r.midnight - 61 - r.clock.t, schedule=False)
        self.assertLess(ctl.interrupt["show"]["idx"], self.j())
        r.tick(2, schedule=False)                                 # 23:59:00: the countdown starts
        self.assertEqual(ctl.interrupt["show"]["idx"], self.j())
        cd = [l for l in ctl.engine.scene.layers if l.effect == "countdown"]
        self.assertTrue(cd)
        self.assertAlmostEqual(cd[0].params["target"], r.midnight, delta=0.5)
        self.assertEqual(ctl.interrupt["label"], f"Show: {self.NAME}")   # the New Year event stood aside
        r.tick(r.midnight - r.clock.t + 1, schedule=False)        # midnight: the grand finale
        self.assertEqual(ctl.interrupt["show"]["idx"], self.j() + 1)
        self.assertEqual([l.effect for l in ctl.engine.scene.layers], ["fireworks"])
        self.assertEqual(ctl.interrupt["label"], f"Show: {self.NAME}")
        rest = sum(c["seconds"] for c in self.cues()[self.j() + 1:])
        r.tick(rest + 5, schedule=False)
        self.assertIsNone(ctl.interrupt)                          # done; no late event party
        self.assertEqual(ctl.playing["id"], "warm-white")
        self.assertIn(("party", "New Year", datetime(2027, 1, 1).astimezone().isoformat()),
                      ctl._events_done)

    def test_started_early_repeats_the_build_up(self):
        r = self.rig(23, 45)
        ctl = r.ctl
        ctl.play_show(self.NAME)
        seen = set()
        while r.clock.t < r.midnight - 66:
            r.tick(5, step=1, schedule=False)
            seen.add(ctl.interrupt["show"]["idx"])
        r.tick(r.midnight - 62 - r.clock.t, schedule=False)
        seen.add(ctl.interrupt["show"]["idx"])
        self.assertNotIn(self.j(), seen)
        self.assertIn(0, seen)
        r.tick(2, schedule=False)
        self.assertEqual(ctl.interrupt["show"]["idx"], self.j())
        r.tick(r.midnight - r.clock.t + 1, schedule=False)
        self.assertEqual(ctl.interrupt["show"]["idx"], self.j() + 1)

    def test_started_late_jumps_to_the_countdown(self):
        r = self.rig(23, 58, 30)
        ctl = r.ctl
        ctl.play_show(self.NAME)
        r.tick(32, schedule=False)
        self.assertEqual(ctl.interrupt["show"]["idx"], self.j())

    def test_tried_out_in_the_evening_is_an_ordinary_countdown(self):
        r = self.rig(20, 0)
        ctl = r.ctl
        ctl.play_show(self.NAME)
        self.assertIsNone(ctl.interrupt["show"]["anchor"])
        before = sum(c["seconds"] for c in self.cues()[:self.j()])
        r.tick(before + 1.5, schedule=False)
        self.assertEqual(ctl.interrupt["show"]["idx"], self.j())
        cd = [l for l in ctl.engine.scene.layers if l.effect == "countdown"][0]
        self.assertAlmostEqual(cd.params["target"] - r.clock.t, 60, delta=2)
        r.tick(62, schedule=False)
        self.assertEqual(ctl.interrupt["show"]["idx"], self.j() + 1)
        # the regular New Year countdown event is unaffected when the show isn't running
        r.tick(sum(c["seconds"] for c in self.cues()[self.j() + 1:]) + 5, schedule=False)
        self.assertIsNone(ctl.interrupt)

    def test_event_countdown_still_works_without_the_show(self):
        r = self.rig(23, 58, 50)
        r.tick(15, schedule=False)
        self.assertEqual(r.ctl.interrupt["label"], "New Year countdown")

    def test_showtime_on_the_date_switches_lights_on(self):
        stt = [{"show": self.NAME, "from": "23:55", "to": "23:55", "every": 60, "date": "12-31",
                "only_when_on": False, "enabled": True}]
        r = self.rig(23, 54, 58, showtimes=stt)
        r.ctl.stop()
        self.assertFalse(r.ctl.engine.live)
        r.tick(5, schedule=False)
        self.assertEqual(r.ctl.status()["show"], self.NAME)
        self.assertTrue(r.ctl.engine.live)
        r.tick(15 * 60, step=2, schedule=False)
        self.assertIsNone(r.ctl.interrupt)
        self.assertFalse(r.ctl.engine.live)                      # back off afterwards
        # ... and not on another date
        r2 = Rig(self, datetime(2026, 12, 30, 23, 54, 58).astimezone(), {"showtimes": stt})
        r2.ctl.play_preset("warm-white")
        r2.ctl.set_live(True)
        r2.tick(5, schedule=False)
        self.assertIsNone(r2.ctl.interrupt)

    def test_showtime_is_not_lost_to_a_visitor_pick_or_a_reminder(self):
        stt = [{"show": self.NAME, "from": "23:55", "to": "23:55", "every": 60, "date": "12-31", "enabled": True}]
        r = self.rig(23, 54, 30, showtimes=stt)
        r.ctl.vqueue.extend([{"id": "candy-cane", "client": "a"}, {"id": "comets", "client": "b"}])
        r.tick(2, schedule=False)
        self.assertTrue(r.ctl.interrupt.get("visitor"))
        r.tick(32, schedule=False)                               # 23:55: the show takes over the pick
        self.assertEqual(r.ctl.status()["show"], self.NAME)
        self.assertEqual(r.ctl.interrupt["playing"]["id"], "warm-white")
        self.assertEqual(len(r.ctl.vqueue), 1)                   # the next pick waits for the show
        # a 'days to' reminder at 23:55:00 only delays it a few seconds
        r = self.rig(23, 54, 58, showtimes=stt)
        self.assertTrue(r.ctl.start_interrupt(shows.days_scene(1), 8, "Days to go"))
        r.tick(5, schedule=False)
        self.assertEqual(r.ctl.interrupt["label"], "Days to go")
        r.tick(10, schedule=False)
        self.assertEqual(r.ctl.status()["show"], self.NAME)
        r.tick(60, schedule=False)
        self.assertEqual(r.ctl.status()["show"], self.NAME)      # started once, not restarted

    def test_lights_off_time_does_not_cut_the_show(self):
        sch = {"enabled": True, "on": "time", "on_time": "17:00", "off_time": "00:00",
               "default": {"preset": "warm-white"}, "rules": []}
        r = self.rig(23, 55, schedule=sch)
        r.ctl._schedule_tick()
        self.assertTrue(r.ctl.engine.live)
        r.ctl.play_show(self.NAME, manual=False)
        r.tick(r.midnight - r.clock.t + 30, schedule=True)
        self.assertEqual(r.ctl.status()["show"], self.NAME)          # still celebrating after 00:00
        r.tick(6 * 60, schedule=True)
        self.assertIsNone(r.ctl.interrupt)
        self.assertFalse(r.ctl.engine.live)                          # and then the schedule's lights-off


if __name__ == "__main__":
    unittest.main()
