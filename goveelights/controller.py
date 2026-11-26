"""Ties everything together for the creator app: engine, presets, playlists,
schedule, and the Phase 1 diagnostics (which share the UDP socket)."""
from __future__ import annotations

import json
import logging
import os
import random
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import calendar_builtin
from . import evolve as evo
from . import live, livedata, shows, tonight
from . import shows_builtin
from . import schedule as sched
from .config import Config
from .diag_server import App as DiagApp
from .engine import Engine
from .layout import Layout
from .output import GoveeRazerOutput, MultiOutput, WledDdpOutput
from .scene import Scene

log = logging.getLogger(__name__)
PRESETS_DIR = Path(os.environ.get("GOVEE_PRESETS", Path(__file__).resolve().parent.parent / "presets"))
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

# The only colours a visitor can send (the page shows these; anything else is refused).
VISITOR_COLOURS = [("Red", "#ff1a1a"), ("Orange", "#ff6a00"), ("Gold", "#ffb000"), ("Green", "#00d040"),
                   ("Ice blue", "#00d8ff"), ("Blue", "#1a4dff"), ("Purple", "#a030ff"), ("Pink", "#ff2fa8")]
# Visitor moments: (seconds between two from one visitor, seconds between any two at all).
VISITOR_LIMITS = {"sparkle": (15.0, 3.0), "snow": (20.0, 4.0), "colour": (8.0, 1.5)}
# And for all of them together: at most this many moments in any window of this many seconds.
VISITOR_BUDGET = (4, 10.0)


# Windows opens a device, not a file, for these names (con.json is the console).
RESERVED = {"con", "prn", "aux", "nul", *(f"{d}{i}" for d in ("com", "lpt") for i in range(1, 10))}


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:64] or "preset"
    return s + "-preset" if s in RESERVED else s


class PresetStore:
    def __init__(self, folder: Path = PRESETS_DIR):
        self.dir = Path(folder)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, pid: str) -> Path:
        if not _ID.match(pid or "") or pid in RESERVED:
            raise ValueError(f"bad preset id {pid!r}")
        return self.dir / f"{pid}.json"

    def list(self) -> list[dict]:
        out = []
        for f in sorted(self.dir.glob("*.json")):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            out.append({"id": f.stem, "name": d.get("name", f.stem), "tags": d.get("tags", []),
                        "favorite": bool(d.get("favorite")), "mtime": f.stat().st_mtime})
        return sorted(out, key=lambda x: x["name"].lower())

    def set_meta(self, pid: str, favorite=None, tags=None):
        d = self.get(pid)
        if favorite is not None:
            d["favorite"] = bool(favorite)
        if tags is not None:
            d["tags"] = [str(t).strip() for t in tags if str(t).strip()]
        self._path(pid).write_text(json.dumps(d, indent=2) + "\n", encoding="utf-8")

    def get(self, pid: str) -> dict:
        return json.loads(self._path(pid).read_text(encoding="utf-8"))

    def save(self, scene: dict, pid: str | None = None) -> str:
        Scene.from_dict(scene)                    # validate
        pid = pid or slug(scene.get("name", "preset"))
        self._path(pid).write_text(json.dumps(scene, indent=2) + "\n", encoding="utf-8")
        return pid

    def delete(self, pid: str):
        self._path(pid).unlink(missing_ok=True)


class Controller:
    def __init__(self, cfg: Config, bind_ip: str = "", presets: PresetStore | None = None):
        self.cfg = cfg
        self.diag = DiagApp(cfg, bind_ip)          # owns the UDP socket and per-device streamers
        self.lan = self.diag.lan
        self.presets = presets or PresetStore()
        self.lock = threading.RLock()
        self.engine: Engine | None = None
        self.scene_dict: dict | None = None        # what is playing (for the editor)
        self.playing: dict | None = None           # {"kind": preset|scene|playlist, "name", "id"}
        self.playlist: dict | None = None          # {"name", "order", "idx", "next_at", "fade"}
        self.override_until: datetime | None = None
        self.diag_active = False
        self.interrupt: dict | None = None          # celebration/countdown in progress
        self.map_state: dict | None = None
        self.weather: dict = {}
        self.games: dict = {}                       # "league:team" -> latest game info
        self._pending: list = []                    # delayed celebrations [(at, kind, colors)]
        self._events_done: set = set()
        self._last_reminder = 0.0
        self._forced_weather: dict = {}             # test overlays {kind: until}
        self.health: dict[str, dict] = {}           # per-device self-healing state
        self.evolve: dict | None = None             # 'let it evolve' state
        self.vqueue: list[dict] = []                # visitor requests waiting
        self._vlast: dict[str, float] = {}          # visitor cooldowns by client
        self._vframe: tuple = (0.0, "")             # cached live frame for the visitor page
        self._vmoments: list[float] = []            # recent visitor moment times (all kinds, capped)
        self._vhold_until = 0.0                     # no new visitor pick before this: an owner feature's turn
        self._showtimes_done: set = set()
        self.dusk_intro: dict | None = None         # {"until", "program", "on"}: the dusk sky at lights-on
        self._dusk_done: str | None = None          # the evening window ("on" time) an intro already ran for
        self._scoreboard_at: float | None = None    # when the next in-game scoreboard is due
        self._sched_key = "unset"
        self.sched_status: dict = {}
        self._stop = threading.Event()
        self.started = time.time()
        self.build_engine()
        self.diag.scan()
        threading.Thread(target=self._tick_loop, name="controller", daemon=True).start()
        threading.Thread(target=self._live_loop, name="live-data", daemon=True).start()
        threading.Thread(target=self._health_loop, name="self-heal", daemon=True).start()

    # --------------------------------------------------------- engine
    @property
    def ecfg(self) -> dict:
        return self.cfg.data.setdefault("engine", {})

    def build_engine(self):
        """(Re)build the virtual strip from config. Keeps the scene and live state."""
        with self.lock:
            old = self.engine
            was_live = bool(old and old.live)
            layout = Layout.from_config(self.cfg)
            refresh = float(self.ecfg.get("refresh_s", 1.0))
            govee = {r.mac: self.cfg.devices[r.mac] for r in layout.runs
                     if self.cfg.devices[r.mac].get("kind", "govee") == "govee"}
            wled = {r.mac: self.cfg.devices[r.mac] for r in layout.runs
                    if self.cfg.devices[r.mac].get("kind") == "wled"}
            out = GoveeRazerOutput(self.lan, govee, refresh_s=refresh, streamers=self.diag.streamers)
            if wled:
                w = WledDdpOutput(wled, refresh_s=refresh)
                out = MultiOutput(out, {k: w for k in wled})
            if old:
                old.stop(release=False)
            e = Engine(layout, out, fps=float(self.ecfg.get("fps", 20)), gamma=float(self.ecfg.get("gamma", 1.0)),
                       brightness=float(self.ecfg.get("brightness", 1.0)))
            e.master.update(self.cfg.data.get("master", {}))
            e.dither = bool(self.ecfg.get("dither", True))
            self._thumbs = {}
            if old and old.scene:
                e.set_scene(old.scene)
            e.start(live=False)
            self.engine = e
            if was_live:
                self.set_live(True, manual=False)

    def set_live(self, on: bool, manual: bool = True):
        if manual:
            with self.lock:
                self._dusk_to_program(fade=8.0 if on else 0.0)   # a Live tap ends the dusk intro
            self._manual()
        if on and self.diag_active:
            if not manual:
                return            # the schedule never interrupts diagnostics
            self.exit_diag()
        if on and not any(d.get("ip") for d in self.cfg.devices.values() if d.get("pixel_count")):
            raise ValueError("no devices with a known IP; scan first")
        if manual and self.interrupt is not None:
            with self.lock:
                if self.interrupt is not None:
                    if on:
                        self.interrupt["live"] = True     # don't switch off again when it ends
                    else:
                        self.interrupt = None             # Live off ends a show/celebration
        if not on:
            with self.lock:
                self.vqueue.clear()                   # lights off: waiting picks would play unseen later
        # a tap on Live always re-takes every box, in case the Govee app grabbed one
        self.engine.set_live(on, release=True, force=on and manual)

    def settings(self, b: dict):
        e = self.engine
        with self.cfg.lock:
            if "fps" in b:
                e.fps = self.ecfg["fps"] = max(1.0, min(60.0, float(b["fps"])))
            if "gamma" in b:
                e.gamma = self.ecfg["gamma"] = max(0.5, min(3.0, float(b["gamma"])))
            if "brightness" in b:
                e.brightness = self.ecfg["brightness"] = max(0.0, min(1.0, float(b["brightness"])))
            if "dither" in b:
                e.dither = self.ecfg["dither"] = bool(b["dither"])
            if "self_heal" in b:
                self.ecfg["self_heal"] = bool(b["self_heal"])
            if "reenable_min" in b:
                self.ecfg["reenable_min"] = max(0.0, min(120.0, float(b["reenable_min"])))
            self.cfg.save()

    def set_master(self, b: dict):
        m = self.engine.master
        with self.cfg.lock:
            if "speed" in b:
                m["speed"] = max(0.0, min(5.0, float(b["speed"])))
            if "hue" in b:
                m["hue"] = float(b["hue"]) % 1.0
            if "palette" in b:
                m["palette"] = str(b["palette"] or "")
            if "bpm" in b:
                m["bpm"] = max(20.0, min(300.0, float(b["bpm"])))
            self.cfg.data["master"] = dict(m)
            self.cfg.save()
        if "blackout" in b:
            self.engine.blackout = bool(b["blackout"])

    @property
    def user_palettes(self) -> dict:
        return self.cfg.data.setdefault("palettes", {})

    def thumbnail(self, scene_dict: dict, key=None, frames: int = 24, fps: float = 8.0) -> dict:
        """A short loop rendered by the real engine, for animated preset cards."""
        m = self.engine.master
        look = {"palette": m.get("palette", ""), "hue": m.get("hue", 0.0)}     # previews match what plays
        if key is not None:
            key = (key, look["palette"], round(look["hue"], 3))
            if key in self._thumbs:
                return self._thumbs[key]
        sc = Scene.from_dict(scene_dict, self.user_palettes)
        L = self.engine.layout
        out = []
        start = 2.0
        if any(layer.gen.frame for layer in sc.layers):
            # effects with memory (tree lighting up, snow piling, fireworks) start empty:
            # let them run a few seconds first so the preview shows them in full swing
            start = 6.0
            for k in range(40):
                sc.render(k * 0.15, L, look)
        for k in range(frames):
            cols = sc.render(start + k / fps, L, look)
            out.append("".join(f"{int(c[0] * 255 + .5):02x}{int(c[1] * 255 + .5):02x}{int(c[2] * 255 + .5):02x}"
                               for c in cols))
        res = {"fps": fps, "frames": out}
        if key is not None:
            if len(self._thumbs) > 300:          # simple bound: drop the oldest entries
                for k in list(self._thumbs)[:100]:
                    self._thumbs.pop(k, None)
            self._thumbs[key] = res
        return res

    # -------------------------------------------------------- playback
    def _manual(self):
        """A manual action pauses the schedule until its next on/off change."""
        self.dusk_intro = None                    # the user took over: no switch to the program later
        if self.cfg.data.get("schedule", {}).get("enabled"):
            ev = sched.evaluate(datetime.now().astimezone(), self.cfg.data["schedule"])
            self.override_until = datetime.fromisoformat(ev["next_change"])

    def play_scene(self, d: dict, label: dict | None = None, fade: float = 0.0, manual: bool = True,
                   keep_playlist: bool = False, continuous: bool = False):
        scene = Scene.from_dict(d, self.user_palettes)
        with self.lock:
            if manual:
                self._manual()
                self.interrupt = None
            self.evolve = None                        # play_evolve sets it again right after
            if not keep_playlist:
                self.playlist = None
            self.scene_dict = scene.to_dict()
            self.playing = label or {"kind": "scene", "name": scene.name}
            self.engine.set_scene(scene, fade, continuous=continuous)

    def play_preset(self, pid: str, fade: float = 1.0, manual: bool = True, keep_playlist: bool = False):
        d = self.presets.get(pid)
        self.play_scene(d, {"kind": "preset", "id": pid, "name": d.get("name", pid)}, fade, manual, keep_playlist)

    def play_evolve(self, pid: str, minutes: float = 3.0, amount: float = 0.2, manual: bool = True,
                    fade: float | None = None):
        """Play a preset, then keep crossfading into fresh variations of it."""
        base = self.presets.get(pid)
        self.play_preset(pid, fade=1.5 if fade is None else fade, manual=manual)
        with self.lock:
            self.evolve = {"id": pid, "base": base, "step": 0, "minutes": max(0.25, float(minutes)),
                           "amount": max(0.02, min(0.6, float(amount))),
                           "next_at": time.time() + max(0.25, float(minutes)) * 60}
            self.playing = {"kind": "evolve", "id": pid, "name": base.get("name", pid)}

    def _evolve_tick(self, now: float):
        ev = self.evolve
        if not ev or self.interrupt or self.map_state or now < ev["next_at"]:
            return
        ev["step"] += 1
        ev["next_at"] = now + ev["minutes"] * 60
        d = evo.variation(ev["base"], ev["step"], ev["amount"], ev["id"])
        try:
            scene = Scene.from_dict(d, self.user_palettes)
        except ValueError as e:
            log.warning("evolve step failed: %s", e)
            return
        self.scene_dict = scene.to_dict()
        self.engine.set_scene(scene, 8.0, continuous=True)   # keep snow piles, tree, etc.
        self.playing = {"kind": "evolve", "id": ev["id"], "name": ev["base"].get("name", ev["id"])}

    def play_playlist(self, name: str, manual: bool = True, fade: float | None = None):
        pl = self.cfg.data.get("playlists", {}).get(name)
        if not pl or not pl.get("items"):
            raise ValueError(f"playlist {name!r} is empty or missing")
        order = list(range(len(pl["items"])))
        if pl.get("shuffle"):
            random.shuffle(order)
        with self.lock:
            if manual:
                self._manual()
                self.interrupt = None                 # as play_scene: the owner's pick ends a show/celebration
            self.evolve = None
            self.playlist = {"name": name, "order": order, "idx": -1, "next_at": 0.0, "fade": float(pl.get("fade", 2))}
            self._advance(first=True, fade=fade)

    def _advance(self, first=False, fade: float | None = None):
        pl_cfg = self.cfg.data.get("playlists", {}).get(self.playlist["name"])
        if not pl_cfg or not pl_cfg.get("items"):
            self.playlist = None
            return
        st = self.playlist
        st["idx"] = (st["idx"] + 1) % len(st["order"])
        if st["idx"] >= len(pl_cfg["items"]):
            st["idx"] = 0
        item = pl_cfg["items"][st["order"][st["idx"]] % len(pl_cfg["items"])]
        st["next_at"] = time.time() + max(0.1, float(item.get("minutes", 5))) * 60
        try:
            self.play_preset(item["preset"], fade=(fade or 0) if first else st["fade"], manual=False, keep_playlist=True)
        except (OSError, ValueError, KeyError) as e:
            log.warning("playlist item %s failed: %s", item, e)
        self.playing = {"kind": "playlist", "name": st["name"], "item": item.get("preset"),
                        "item_name": (self.playing or {}).get("name")}

    def run_program(self, prog: dict | None, manual: bool = False, fade: float | None = None,
                    live: bool = True):
        """fade: seconds to crossfade from what is playing (None: each kind's usual).
        live=False: only set up the program, leave the lights as they are."""
        if not prog:
            self.stop(manual=manual)
            return
        if prog.get("playlist"):
            self.play_playlist(prog["playlist"], manual=manual, fade=fade)
        elif prog.get("evolve"):
            self.play_evolve(prog["evolve"], float(prog.get("minutes", 3)), manual=manual, fade=fade)
        elif prog.get("preset"):
            self.play_preset(prog["preset"], fade=1.0 if fade is None else fade, manual=manual)
        if live:
            self.set_live(True, manual=manual)

    def stop(self, manual: bool = True, release: bool = True):
        with self.lock:
            if manual:
                self._manual()
            self.playlist, self.playing, self.evolve = None, None, None
            self.interrupt = None                     # Stop really stops shows and celebrations too
            self.vqueue.clear()                       # the visitors who asked are long gone by next time
            self.engine.set_scene(None)
            self.engine.set_live(False, release=release)

    # ---------------------------------------------------- interruptions
    def start_interrupt(self, d: dict, seconds: float, label: str, force_live: bool = False,
                        fade: float = 0.3, transition: str = "crossfade") -> bool:
        """Play a scene for a while (celebration, countdown), then put back what was playing."""
        with self.lock:
            if self.diag_active or self.map_state:
                return False
            if not self.engine.live and not force_live:
                return False
            sc = Scene.from_dict(d, self.user_palettes)     # a bad scene leaves no state behind
            if self.interrupt is None:
                self.interrupt = {"scene": self.scene_dict, "playing": self.playing, "live": self.engine.live}
            self.interrupt["until"] = time.time() + seconds
            self.interrupt["label"] = label
            for k in ("show", "visitor", "scoreboard", "event"):
                self.interrupt.pop(k, None)
            self.engine.set_scene(sc, fade, transition=transition)
            self.playing = {"kind": "event", "name": label}
            if force_live:
                self.set_live(True, manual=False)
            log.info("interrupt: %s for %.0f s", label, seconds)
            return True

    def end_interrupt(self):
        with self.lock:
            st, self.interrupt = self.interrupt, None
            if not st:
                return
            self.engine.set_scene(Scene.from_dict(st["scene"], self.user_palettes) if st["scene"] else None, 1.5)
            self.playing = st["playing"]
            if not st["live"]:
                if self._schedule_on_pending():
                    self._schedule_tick()             # the schedule's lights-on came during it: stay on
                else:
                    self.vqueue.clear()
                    self.engine.set_live(False, release=True)

    def _schedule_on_pending(self) -> bool:
        """The schedule wants the lights on with a program it hasn't applied yet (its
        on-change arrived while an interrupt held it back)."""
        sch = self.cfg.data.get("schedule", {})
        if not sch.get("enabled") or self.diag_active or self.map_state:
            return False
        now = datetime.now().astimezone()
        if self.override_until and now < self.override_until:
            return False
        try:
            ev = sched.evaluate(now, sch)
        except (TypeError, ValueError, KeyError):
            return False
        return bool(ev.get("active")) and json.dumps(ev["program"], sort_keys=True) != self._sched_key

    # ----------------------------------------------------------- shows
    ANCHOR_WINDOW = 20 * 60          # a clock-pinned countdown cue is honoured this long before its time
    OWNS_AFTER = 10 * 60             # lights-off waits for a show pinned to a moment until this long after it

    def show_def(self, name: str) -> dict | None:
        """A user show, or else the built-in one of that name (the user's copy wins)."""
        sh = (self.cfg.data.get("shows") or {}).get(name)
        return sh if isinstance(sh, dict) else shows_builtin.BUILTIN_SHOWS.get(name)

    def shows_list(self) -> list[dict]:
        """Built-in shows first, then the user's; one entry per name."""
        user = self.cfg.data.get("shows") or {}
        names = list(shows_builtin.BUILTIN_SHOWS) + [n for n in user if n not in shows_builtin.BUILTIN_SHOWS]
        out = []
        for name in names:
            sh = self.show_def(name) or {}
            cues = self._show_cues(sh)
            mine = isinstance(user.get(name), dict)
            out.append({"name": name, "builtin": not mine, "overrides_builtin": mine and name in shows_builtin.BUILTIN_SHOWS,
                        "cues": len(cues), "seconds": round(sum(self._cue_secs(c) for c in cues)),
                        "loop": sh.get("end") == "loop", "description": str(sh.get("description", ""))})
        return out

    def copy_show(self, name: str, new_name: str = "") -> str:
        """Copy a show (built-in or user) into the user's shows, to edit."""
        src = self.show_def(name)
        if not src:
            raise ValueError(f"no show called {name!r}")
        user = self.cfg.data.get("shows") or {}
        new_name = str(new_name or "").strip()[:80]
        if not new_name:
            new_name, k = f"{name} (copy)", 2
            while new_name in user or new_name in shows_builtin.BUILTIN_SHOWS:
                new_name, k = f"{name} (copy {k})", k + 1
        elif new_name in user and new_name != name:
            raise ValueError(f"there is already a show called {new_name!r}")
        d = json.loads(json.dumps(src))
        d.pop("builtin", None)
        with self.cfg.lock:
            self.cfg.data["shows"] = {**user, new_name: d}
            self.cfg.save()
        return new_name

    @staticmethod
    def _show_cues(show: dict | None) -> list[dict]:
        return [c for c in (show or {}).get("cues", []) if shows_builtin.cue_ok(c)]

    @staticmethod
    def _cue_secs(cue: dict) -> float:
        try:
            secs = max(1.0, float(cue.get("seconds", 20)))
        except (TypeError, ValueError):
            secs = 20.0
        if cue.get("countdown_to"):
            secs = float(int(max(3, min(99, secs))))     # what the countdown scene can show (and plays)
        return secs

    def _show_anchor(self, cues: list[dict], now: float) -> tuple[float | None, int | None]:
        """(unix time, cue index) of a countdown cue pinned to the clock, if that time is near."""
        j = next((k for k, c in enumerate(cues) if c.get("countdown_to")), None)
        if j is None:
            return None, None
        try:
            hm = sched._hm(str(cues[j]["countdown_to"]))
        except (ValueError, AttributeError):
            return None, j
        nowd = datetime.fromtimestamp(now).astimezone()
        at = nowd.replace(hour=hm.hour, minute=hm.minute, second=0, microsecond=0)
        if at.timestamp() <= now:
            at = datetime.fromtimestamp(at.timestamp() + 86400).astimezone().replace(
                hour=hm.hour, minute=hm.minute, second=0, microsecond=0)
        left = at.timestamp() - now
        return (at.timestamp() if 0 < left <= self.ANCHOR_WINDOW else None), j

    def _cue_scene(self, cue: dict, st: dict | None = None, now: float | None = None) -> dict:
        if cue.get("countdown_to"):
            secs = int(self._cue_secs(cue))
            target = (st or {}).get("anchor") or (now or time.time()) + secs
            return shows.countdown_scene(target, secs, min(10, secs))
        return self.presets.get(cue["preset"])

    def _cue_end(self, st: dict, cues: list[dict], idx: int, now: float) -> float:
        """When cue idx (starting now) gives way to the next one."""
        if idx == st.get("anchor_idx") and st.get("anchor"):
            return st["anchor"]                       # a pinned countdown ends exactly on time
        prev = st.get("next_at")
        secs = self._cue_secs(cues[idx])
        if prev and 0 <= now - prev < 2:              # chain from the planned time: no drift over a show
            return prev + secs
        return now + secs

    def _show_until(self, st: dict, cues: list[dict]) -> float:
        rest = sum(self._cue_secs(c) for c in cues[st["idx"] + 1:])
        if st["loop"]:
            return float(st.get("stop_at") or 10 ** 12)
        return st["next_at"] + rest + 3

    def play_show(self, name: str, manual: bool = True, force_live: bool | None = None) -> bool:
        show = self.show_def(name)
        cues = self._show_cues(show)
        if not cues:
            raise ValueError(f"show {name!r} has no cues")
        if manual:
            with self.lock:
                self._dusk_to_program(swap_only=True)    # the show returns to the program, not the dusk sky
            self._manual()
        now = time.time()
        anchor, aidx = self._show_anchor(cues, now)
        st = {"name": name, "idx": 0, "loop": (show or {}).get("end") == "loop", "anchor": anchor,
              "anchor_idx": aidx, "owns": anchor, "next_at": None}
        first = cues[0]
        with self.lock:
            ok = self.start_interrupt(self._cue_scene(first, st, now), 10 ** 12, f"Show: {name}",
                                      force_live=manual if force_live is None else force_live,
                                      fade=float(first.get("fade", 1.5)),
                                      transition=first.get("transition", "crossfade"))
            if ok:
                st["next_at"] = self._cue_end(st, cues, 0, now)
                self.interrupt["show"] = st
                self.interrupt["until"] = self._show_until(st, cues)
        return ok

    def _show_tick(self, now: float):
        it = self.interrupt
        st = it and it.get("show")
        if not st:
            return
        cues = self._show_cues(self.show_def(st["name"]))
        if not cues:
            self.end_interrupt()
            return
        a, j = st.get("anchor"), st.get("anchor_idx")
        pinned = a is not None and j is not None and j < len(cues)
        jump = pinned and st["idx"] < j and now >= a - self._cue_secs(cues[j])
        if not jump and now < st["next_at"]:
            return
        if jump:
            nxt = j                                   # the clock says it's time for the countdown
        else:
            nxt = st["idx"] + 1
            if pinned and nxt == j and now < a - self._cue_secs(cues[j]) - 1:
                nxt = 0                               # early: play the build-up again until it's time
            elif nxt >= len(cues):
                if not st["loop"]:
                    self.end_interrupt()
                    return
                nxt = 0
        if pinned and nxt > j:
            st["anchor"] = None                       # the moment has passed; later loops are ordinary
        cue = cues[nxt]
        try:
            self.engine.set_scene(Scene.from_dict(self._cue_scene(cue, st, now), self.user_palettes),
                                  float(cue.get("fade", 1.5)), transition=cue.get("transition", "crossfade"))
        except (OSError, ValueError, KeyError, TypeError) as e:
            log.warning("show cue %s failed: %s", cue, e)
        st["next_at"] = self._cue_end(st, cues, nxt, now)
        st["idx"] = nxt
        it["until"] = self._show_until(st, cues)

    def _show_owns(self, ts: float) -> bool:
        """A running show has its own countdown pinned to (about) this moment."""
        st = (self.interrupt or {}).get("show") or {}
        return bool(st.get("owns")) and abs(st["owns"] - ts) < 300

    def _showtimes_tick(self):
        now = datetime.now().astimezone()
        mins = now.hour * 60 + now.minute
        for k, stt in enumerate(self.cfg.data.get("showtimes", [])):
            if not stt.get("enabled", True):
                continue
            try:
                a = sched._hm(stt.get("from", "17:00"))
                b = sched._hm(stt.get("to", "22:00"))
            except ValueError:
                continue
            lo, hi = a.hour * 60 + a.minute, b.hour * 60 + b.minute
            # the after-midnight part of a window that crosses midnight belongs to the evening it began
            night = now - timedelta(days=1) if lo > hi and mins <= hi else now
            dates = stt.get("date") or stt.get("dates")      # optional "MM-DD" (or a list): e.g. only on 12-31
            if dates:                                       # a date wins over weekdays (the UI hides them)
                if night.strftime("%m-%d") not in ([dates] if isinstance(dates, str) else dates):
                    continue
            elif stt.get("days") and night.weekday() not in stt["days"]:
                continue
            every = max(5, int(stt.get("every", 60)))
            inside = lo <= mins <= hi if lo <= hi else (mins >= lo or mins <= hi)     # may cross midnight
            if not inside or ((mins - lo) % 1440) % every:
                continue
            key = (k, now.date().isoformat(), mins)
            if key in self._showtimes_done:
                continue
            if stt.get("only_when_on", True) and not self.engine.live:
                continue                            # the schedule may switch them on later this minute
            it = self.interrupt
            if it and not (it.get("scoreboard") or it.get("visitor")):
                continue                            # a countdown or celebration: try again next tick this minute
            try:
                # only_when_on false: switch the lights on for it (and off again afterwards).
                # A scoreboard or visitor pick stands aside (queued picks resume after the show).
                if self.play_show(stt["show"], manual=False, force_live=not stt.get("only_when_on", True)):
                    self._showtimes_done.add(key)
                    self._limit_forced_loop(now, b)
            except (KeyError, ValueError, TypeError, OSError) as e:
                self._showtimes_done.add(key)
                log.warning("showtime failed: %s", e)

    def _limit_forced_loop(self, now: datetime, to):
        """A loop show that a showtime switched the lights on for would otherwise run
        forever (the schedule never sees a change while the lights are meant to be off):
        it stops at the end of the showtime's 'to' minute, after at least one full pass."""
        with self.lock:
            it = self.interrupt
            st = (it or {}).get("show")
            if not st or not st.get("loop") or it.get("live"):
                return
            end = now.replace(hour=to.hour, minute=to.minute, second=0, microsecond=0)
            if end < now - timedelta(minutes=1):
                end += timedelta(days=1)                  # a window that crosses midnight
            cues = self._show_cues(self.show_def(st["name"]))
            st["stop_at"] = max(end.timestamp() + 60, time.time() + sum(self._cue_secs(c) for c in cues))
            it["until"] = self._show_until(st, cues)

    # ------------------------------------------------------------- pad
    def pad(self, action: str, on: bool | None = None, color: str = "#ff2000") -> str:
        """Momentary performance effects layered over whatever is playing."""
        e, now = self.engine, time.time()
        S = lambda d: Scene.from_dict(d)  # noqa: E731
        if action == "blackout":
            e.blackout = bool(on) if on is not None else not e.blackout
            return "blackout" if e.blackout else "lights back"
        if action == "white":                     # colour-matching aid: every run plain white
            if on is False:
                e.overlays.pop("pad:white", None)
            else:
                e.overlays["pad:white"] = (S({"layers": [{"effect": "solid", "params": {"color": "#ffffff"}}]}),
                                           "normal", 1.0, now + 120, now, 0)
            return "white test"
        if action == "hue":
            e.hue_sweep = (now, 4.0)
            return "hue sweep"
        hold = on is not None
        if hold and on is False:
            e.overlays.pop("pad:" + action, None)
            return "off"
        until = now + (3600 if hold else {"flash": 1.5, "strobe": 3, "sparkle": 5, "fireworks": 8,
                                           "wash": 3, "comet": 4}.get(action, 3))
        spec = {
            "flash": ({"layers": [{"effect": "solid", "params": {"color": "#ffffff"}}]}, "add", 1.0, 0.35),
            "strobe": ({"layers": [{"effect": "solid", "params": {"color": "#ffffff"},
                                    "fx": [{"type": "strobe", "params": {"rate": 12, "duty": 0.35}}]}]}, "add", 1.0, 0),
            "sparkle": ({"layers": [{"effect": "sparkle", "params": {"rate": 40, "decay": 0.2}}]}, "add", 1.0, 0),
            "fireworks": ({"layers": [{"effect": "bursts", "params": {"interval": 0.4, "life": 1.5}}]}, "lighten", 1.0, 0),
            "wash": ({"layers": [{"effect": "solid", "params": {"color": color}}]}, "normal", 0.9, 1.2),
            "comet": ({"layers": [{"effect": "comet", "params": {"speed": 80, "count": 3, "tail": 25}}]}, "add", 1.0, 0),
        }.get(action)
        if not spec:
            raise ValueError(f"unknown pad action {action}")
        d, mode, op, decay = spec
        e.overlays["pad:" + action] = (S(d), mode, op, until, now, decay)
        return action

    # --------------------------------------------------------- visitors
    @property
    def vcfg(self) -> dict:
        return self.cfg.data.setdefault("visitor", {})

    def _visitor_on(self) -> bool:
        return bool(self.engine.live and not self.engine.blackout or not self.vcfg.get("only_when_on", True))

    def visitor_state(self, client: str = "") -> dict:
        v = self.vcfg
        names = {p["id"]: p["name"] for p in self.presets.list()}
        it = self.interrupt or {}
        cur = it.get("visitor") if it else None
        you = None
        if client:                      # only this visitor's own pick: nothing about anyone else
            client = client[:64]
            if cur and cur.get("client") == client:
                you = {"playing": True, "name": cur.get("name")}
            else:
                pos = next((k for k, r in enumerate(self.vqueue) if r.get("client") == client), None)
                if pos is not None:
                    wait = pos * float(v.get("seconds", 60))
                    left = max(0.0, it["until"] - time.time()) if it and it.get("until") else 0.0
                    you = {"position": pos + 1, "name": names.get(self.vqueue[pos]["id"], "")}
                    if left > 6 * 3600:               # a looping show: no end time to count to
                        you.update(wait=None, after_show=True)
                    else:
                        you.update(wait=round(wait + left), after_show=bool(it) and not cur)
        return {"title": v.get("title") or "Pick the lights!", "message": v.get("message", ""),
                "on": self._visitor_on(),
                "presets": [{"id": i, "name": names[i]} for i in v.get("presets", []) if i in names],
                "queue": len(self.vqueue), "sparkle": bool(v.get("sparkle", True)),
                "snow": bool(v.get("snow", True)),
                "colours": [{"name": n, "hex": h} for n, h in VISITOR_COLOURS] if v.get("colours", True) else [],
                "now": cur.get("name") if cur else None, "you": you,
                "busy": bool(it) and not it.get("visitor"), "playing": self._visitor_playing(names)}

    def _visitor_playing(self, names: dict) -> str | None:
        """What's on, for the visitor page, but only names the visitor may see anyway: a
        preset on their list (also inside a playlist) or a built-in show."""
        it = self.interrupt or {}
        show = (it.get("show") or {}).get("name")
        if show:
            return show if show in shows_builtin.BUILTIN_SHOWS else None
        if it:
            return None
        pl = self.playing or {}
        listed = {names[i] for i in self.vcfg.get("presets", []) if i in names}
        for n in (names.get(pl.get("item") or ""), pl.get("name")):
            if n and n in listed:
                return n
        return None

    def visitor_frame(self) -> str:
        """The house's current colours for the visitor page's little live view (cached
        briefly, so many phones polling cost one conversion; empty while it's off)."""
        now = time.time()
        c = self._vframe
        if now - c[0] > 0.2 or now < c[0]:
            c = self._vframe = (now, self.frame_hex() if self._visitor_on() else "")
        return c[1]

    def _vprune(self, now: float):
        if len(self._vlast) > 2000:
            keep = max(float(self.vcfg.get("cooldown", 60)), 30.0)   # longer than any fx limit
            self._vlast = {k: t for k, t in self._vlast.items() if now - t < keep}

    def visitor_pick(self, pid: str, client: str) -> dict:
        with self.lock:
            return self._visitor_pick(pid, client[:64])

    def _visitor_pick(self, pid: str, client: str) -> dict:
        v, now = self.vcfg, time.time()
        self._vprune(now)
        if pid not in v.get("presets", []):
            raise ValueError("that one isn't available")
        if v.get("only_when_on", True) and not self.engine.live or self.engine.blackout:
            raise ValueError("The lights are off right now. Come back this evening!")
        cd = float(v.get("cooldown", 60))
        if now - self._vlast.get(client, 0) < cd:
            raise ValueError(f"You can pick again in {int(cd - (now - self._vlast[client]))} s")
        if len(self.vqueue) >= int(v.get("max_queue", 5)):
            raise ValueError("Lots of requests right now. Try again in a minute!")
        it = self.interrupt
        busy = bool(it) and not it.get("visitor")        # a show/celebration: picks wait for it to end
        if busy and it["until"] - now > 6 * 3600:        # a looping show never ends on its own
            raise ValueError("A light show is on right now. Try again after it!")
        self._vlast[client] = now
        pos = len(self.vqueue) + 1
        wait = (pos - 1) * float(v.get("seconds", 60))
        if it:
            wait += max(0.0, it["until"] - now)
        # A pick still waiting well past its expected turn is stale (its visitor has left).
        self.vqueue.append({"id": pid, "client": client, "expires": now + wait + self.VISITOR_STALE})
        return {"position": pos, "wait": round(wait), "busy": busy}

    def visitor_sparkle(self, client: str) -> str:
        with self.lock:
            return self._visitor_fx("sparkle", client[:64])

    def visitor_snow(self, client: str) -> str:
        with self.lock:
            return self._visitor_fx("snow", client[:64])

    def visitor_colour(self, hexcol: str, client: str) -> str:
        with self.lock:
            return self._visitor_fx("colour", client[:64], hexcol)

    def _visitor_fx(self, kind: str, client: str, hexcol: str = "") -> str:
        """A visitor's moment on the house, as an overlay on whatever is playing. Rate
        limited per visitor and for everyone together, and only fixed looks: the one
        thing a visitor's request carries into a scene is a colour from VISITOR_COLOURS."""
        now = time.time()
        self._vprune(now)
        if not self.vcfg.get({"colour": "colours"}.get(kind, kind), True):
            raise ValueError("not available")
        if kind == "colour":
            hexcol = str(hexcol or "").strip().lower()[:16]
            if hexcol not in {h for _, h in VISITOR_COLOURS}:
                raise ValueError("not available")
        per, every = VISITOR_LIMITS[kind]
        if now - self._vlast.get(f"{kind}:{client}", 0) < per or now - self._vlast.get(f"{kind}:*", 0) < every:
            raise ValueError("Give it a few seconds!")
        if not self.engine.live or self.engine.blackout or self.map_state or self.diag_active:
            raise ValueError("The lights are off right now.")
        it = self.interrupt or {}
        if it and not it.get("visitor"):     # a show, countdown, scoreboard or celebration: hands off
            raise ValueError("A show is on right now: try again after it!")
        n, win = VISITOR_BUDGET
        self._vmoments = [m for m in self._vmoments if 0 <= now - m < win][-n:]
        if len(self._vmoments) >= n:
            raise ValueError("Lots of fun at once! Give it a few seconds.")
        self._vmoments.append(now)
        self._vlast[f"{kind}:{client}"] = self._vlast[f"{kind}:*"] = now
        if kind == "sparkle":
            return self.pad("sparkle")
        seed = int(now * 10) % 9999
        if kind == "snow":
            secs, slots = 6.0, 2
            d = {"layers": [{"effect": "flurry", "params": {"repeat": False, "seconds": secs, "seed": seed}}]}
        else:
            secs, slots = 4.0, 3
            d = {"layers": [{"effect": "colour_ripple",
                             "params": {"repeat": False, "seconds": secs, "color": hexcol, "seed": seed}}]}
        # A few can overlap: each kind takes a free slot, or else the one ending soonest,
        # so a new one never cuts off a running one while a slot is free. Newest on top.
        ov = self.engine.overlays

        def ends(s):
            o = ov.get(f"visit:{kind}{s}")
            return o[3] if o and len(o) > 3 and o[3] > now else 0.0
        key = f"visit:{kind}{min(range(slots), key=ends)}"
        ov.pop(key, None)
        if kind == "snow":          # the scene dims a little so white flakes show over white lights
            self._visitor_veil(now, secs)
        ov[key] = (Scene.from_dict(d), "over", 1.0, now + secs + 0.2, now, 0)
        return kind

    def _visitor_veil(self, now: float, secs: float):
        """One shared dim for every flurry: a new one extends the veil that's on (so
        overlapping flurries never dim the scene below the intended half)."""
        ov, vk, et = self.engine.overlays, "visit:veil", self.engine.now()
        cur = ov.get(vk)
        if cur and len(cur) > 3 and cur[3] > now and hasattr(cur[0], "veil_t0"):
            lay = cur[0].layers[0]
            lay.resolved["seconds"] = max(float(lay.resolved["seconds"]), et - cur[0].veil_t0 + secs)
            ov[vk] = cur[:3] + (max(cur[3], now + secs + 0.2),) + cur[4:]
            return
        sc = Scene.from_dict({"layers": [{"effect": "veil", "params": {"seconds": secs, "depth": 0.5}}]})
        sc.veil_t0 = et
        ov.pop(vk, None)
        ov[vk] = (sc, "multiply", 1.0, now + secs + 0.2, now, 0)

    VISITOR_STALE = 10 * 60.0       # a queued pick this long past its expected turn is dropped
    VISITOR_HOLD = 15.0             # an owner feature blocked by a visitor pick gets this long a turn

    def _visitor_hold(self):
        """An owner feature (schedule, scoreboard, dusk handoff) is waiting only for a
        visitor pick to end: don't start the next pick until it has had its turn."""
        if (self.interrupt or {}).get("visitor"):
            self._vhold_until = time.time() + self.VISITOR_HOLD

    def _visitor_tick(self):
        now = time.time()
        self.vqueue[:] = [r for r in self.vqueue if r.get("expires", now + 1) > now]
        if not self.vqueue or not self.engine.live or self.engine.blackout or self.map_state or self.diag_active:
            return
        it = self.interrupt
        if it and not it.get("visitor"):            # a show, countdown or celebration is on: wait
            return
        if it and now < it["until"]:
            return
        if not it and now < self._vhold_until:      # the owner's schedule/scoreboard goes first
            return
        req = self.vqueue[0]
        try:
            d = self.presets.get(req["id"])
        except (OSError, ValueError):
            self.vqueue.pop(0)                         # preset deleted: drop the request
            return
        try:
            ok = self.start_interrupt(d, float(self.vcfg.get("seconds", 60)), f"Visitor pick: {d.get('name')}",
                                      fade=1.5)
        except (OSError, ValueError, KeyError, TypeError) as e:
            log.warning("visitor pick %s failed: %s", req.get("id"), e)
            self.vqueue.pop(0)                         # a preset that won't build: drop the request
            return
        if ok:
            self.vqueue.pop(0)
            self.interrupt["visitor"] = {"name": d.get("name"), "id": req["id"], "client": req.get("client")}

    # ------------------------------------------------------ self-healing
    def _health_loop(self):
        """Every 20 s while live, ask each box for its status. A box that vanished and
        came back (power blip), or whose state changed behind our back (Govee app,
        colour command), gets realtime re-enabled and its frame resent."""
        while not self._stop.wait(float(self.ecfg.get("heal_interval", 20.0))):
            try:
                e = self.engine
                if (not self.ecfg.get("self_heal", True) or not e.live or self.diag_active or self.map_state):
                    for h in self.health.values():
                        h["base"] = None
                    continue
                now = time.time()
                for mac, dev in list(e.output.devices.items()):
                    if not dev.get("ip"):
                        continue
                    h = self.health.setdefault(mac, {"miss": 0, "base": None, "pending": 0, "reasserts": [],
                                                     "last_enable": now, "seen": None, "paused": False})
                    st = self.lan.status(dev["ip"], timeout=1.5)
                    if st is None:
                        h["miss"] += 1
                        continue
                    h["seen"] = now
                    sig = (st.get("onOff"), st.get("brightness"), json.dumps(st.get("color"), sort_keys=True),
                           st.get("colorTemInKelvin"))
                    back = h["miss"] >= 2
                    h["miss"] = 0
                    if h["base"] is None and not back:
                        h["base"] = sig
                        continue
                    changed = sig != h["base"] or st.get("onOff") == 0
                    h["pending"] = h["pending"] + 1 if changed else 0
                    periodic = float(self.ecfg.get("reenable_min", 0))
                    due = periodic > 0 and now - h["last_enable"] > periodic * 60
                    if back or h["pending"] >= 2 or due:
                        self._reassert(mac, "came back online" if back else "periodic" if due else "state changed")
            except Exception:  # noqa: BLE001
                log.exception("self-heal loop failed")

    def _reassert(self, mac: str, why: str):
        h, now = self.health[mac], time.time()
        h["reasserts"] = [t for t in h["reasserts"] if now - t < 900]
        if h["reasserts"] and now - h["reasserts"][-1] < 120:
            return
        if len(h["reasserts"]) >= 5:                # status keeps changing on its own: stop poking it
            if not h["paused"]:
                log.warning("self-heal paused for %s: its status keeps changing", mac)
            h["paused"] = True
            return
        h["paused"] = False
        log.info("self-heal: re-enabling realtime on %s (%s)", self.cfg.devices.get(mac, {}).get("name", mac), why)
        out = self.engine.output
        try:
            out._streamer(mac).enable()
            out._last.pop(mac, None)                # force the current frame to be resent
        except OSError as e:
            log.warning("self-heal failed for %s: %s", mac, e)
            return
        h["reasserts"].append(now)
        h["last_enable"] = now
        h["base"], h["pending"] = None, 0

    # ---------------------------------------------------------- events
    @property
    def events(self) -> list:
        ev = self.cfg.data.get("events")
        return shows.DEFAULT_EVENTS if ev is None else ev

    def _events_tick(self):
        now = datetime.now().astimezone()
        for ev in self.events:
            if not ev.get("enabled", True):
                continue
            try:
                at = shows.next_occurrence(ev, now)
            except (KeyError, ValueError):
                continue
            key = (ev.get("name"), at.isoformat())
            secs = (at - now).total_seconds()
            if self._show_owns(at.timestamp()):        # e.g. the New Year's show counts down itself
                # Only what the show has actually reached is done: if a celebration (or
                # anything else) cuts the show off before then, the event still happens.
                st = self.interrupt["show"]
                j = st.get("anchor_idx")
                if j is not None and st.get("idx", 0) >= j:
                    self._events_done.add(("cd",) + key)
                if secs <= 0.5:
                    self._events_done.add(("party",) + key)
                continue
            cd = float(ev.get("countdown", 0))
            if cd > 0 and 0 < secs <= cd and ("cd",) + key not in self._events_done:
                self._events_done.add(("cd",) + key)
                if self.start_interrupt(shows.countdown_scene(at.timestamp(), int(cd), int(ev.get("digits", 10))),
                                        secs + 0.5, f"{ev.get('name')} countdown",
                                        force_live=ev.get("lights_on", True)):
                    self.interrupt["event"] = True     # pinned to a moment: lights-off time waits for it
            elif -2 < secs <= 0.5 and ("party",) + key not in self._events_done and float(ev.get("minutes", 0)) > 0:
                self._events_done.add(("party",) + key)
                try:
                    d = self.presets.get(ev.get("celebrate", "fireworks"))
                except (OSError, ValueError):
                    d = shows.celebration("win", ["#ffd700", "#ffffff"])[0]
                if self.start_interrupt(d, float(ev["minutes"]) * 60, ev.get("name", "Celebration"),
                                        force_live=ev.get("lights_on", True)):
                    self.interrupt["event"] = True
            # "days to go" reminders while the lights are on
            days = (at.date() - now.date()).days
            if (ev.get("reminders") and self.engine.live and not self.interrupt
                    and 0 < days <= int(ev.get("remind_from_days", 30))
                    and time.time() - self._last_reminder > float(ev.get("remind_every_min", 15)) * 60):
                self._last_reminder = time.time()
                self.start_interrupt(shows.days_scene(days), 8, f"{days} days to {ev.get('name')}")

    # ------------------------------------------------------- live data
    def _live_loop(self):
        last_weather = 0.0
        next_game = {}
        while not self._stop.wait(2.0):
            try:
                now = time.time()
                w = self.cfg.data.get("weather", {})
                if w.get("enabled") and now - last_weather > 600:
                    last_weather = now
                    sch = self.cfg.data.get("schedule", {})
                    try:
                        self.weather = {**live.parse_weather(live.fetch_json(
                            live.weather_url(float(sch.get("lat", 40.3764)), float(sch.get("lon", -104.6922))))),
                            "at": now}
                    except Exception as e:  # noqa: BLE001
                        self.weather = {**self.weather, "error": str(e)}
                self._apply_weather()
                g = self.cfg.data.get("game", {})
                if g.get("enabled"):
                    for t in g.get("teams", []):
                        key = f"{t.get('league')}:{t.get('team')}"
                        if now >= next_game.get(key, 0):
                            next_game[key] = now + self._poll_game(t, key)
                            # for the scoreboard text effect, held back like the celebrations
                            livedata.update_game(key, self.games.get(key), g.get("delay", 30))
                for item in list(self._pending):
                    if now >= item[0]:
                        self._pending.remove(item)
                        _, kind, colors = item
                        d, secs = shows.celebration(kind, colors)
                        self.start_interrupt(d, secs, d["name"], force_live=bool(g.get("when_lights_off")))
                with self.lock:
                    self._scoreboard_tick(now)
            except Exception:  # noqa: BLE001
                log.exception("live data loop failed")

    def _poll_game(self, t: dict, key: str) -> float:
        """Check one team; returns seconds until the next check."""
        league, team = t.get("league", "nfl"), t.get("team", "")
        try:
            game = live.find_game(live.fetch_json(live.scoreboard_url(league)), team)
        except Exception as e:  # noqa: BLE001
            prev = self.games.get(key) or {}
            self.games[key] = {**prev, "error": str(e)}
            return 30 if prev.get("state") == "in" else 300       # one blip mid-game: retry soon
        prev = self.games.get(key)
        self.games[key] = {**game, "polled": time.time()} if game else {"state": "none", "team": team}
        if not game:
            return 1800
        delay = float(self.cfg.data.get("game", {}).get("delay", 30))
        if prev and prev.get("id") == game["id"] and prev.get("state") in ("in", "post"):
            delta = game["score"] - prev.get("score", 0)
            if delta > 0 and game["state"] == "in":
                self._pending.append((time.time() + delay, live.classify(league, delta), game["colors"]))
            if prev.get("state") == "in" and game["state"] == "post" and game["score"] > game["opp_score"]:
                self._pending.append((time.time() + delay, "win", game["colors"]))
        return 15 if game["state"] == "in" else 300

    SCOREBOARD_FRESH = 120.0        # an in-progress game polled longer ago than this is not shown

    def _scoreboard_tick(self, now: float):
        """While a followed team is playing and the lights are on, show the score on the
        zigzag every game.scoreboard_every minutes for scoreboard_seconds. Waits for (never
        cuts off) shows, celebrations, visitors and countdowns; scoring celebrations still
        cut in over it."""
        g = self.cfg.data.get("game") or {}
        try:
            every = float(g.get("scoreboard_every") or 0) * 60
            secs = max(5.0, min(300.0, float(g.get("scoreboard_seconds") or 20)))
            delay = max(0.0, min(600.0, float(g.get("delay", 30))))
        except (TypeError, ValueError):
            return
        teams = {f"{t.get('league')}:{t.get('team')}" for t in g.get("teams", []) if isinstance(t, dict)}
        game = next((v for k, v in self.games.items()
                     if k in teams and isinstance(v, dict) and v.get("state") == "in" and "error" not in v
                     and now - float(v.get("polled") or 0) < self.SCOREBOARD_FRESH), None)
        if every <= 0 or not g.get("enabled") or game is None:
            if not (every > 0 and g.get("enabled") and any(
                    k in teams and isinstance(v, dict) and v.get("state") == "in" and "error" in v
                    for k, v in self.games.items())):
                self._scoreboard_at = None            # a failed poll mid-game keeps the cadence
            return
        every = max(60.0, every)
        if self._scoreboard_at is None:
            # the first one once the (broadcast-delayed) score has reached the text effect
            self._scoreboard_at = now + min(every, delay + 10)
            return
        if now < self._scoreboard_at:
            return
        e = self.engine
        if (not e.live or e.blackout or self.interrupt is not None or self.map_state or self.diag_active
                or self._pending):
            self._visitor_hold()
            return                                    # busy: try again in a moment
        if self.start_interrupt(shows.scoreboard_scene(game), secs, f"Scoreboard: {game.get('team', '')}".strip(),
                                fade=1.0):
            self.interrupt["scoreboard"] = True
            self._scoreboard_at = now + max(every, secs + 30)

    WEATHER_STALE = 45 * 60.0       # conditions older than this (fetches failing) are not shown

    def _apply_weather(self):
        w = self.cfg.data.get("weather", {})
        now = time.time()
        forced = {k for k, until in self._forced_weather.items() if until > now}
        cond = self.weather if w.get("enabled") else {}
        if now - float(cond.get("at") or 0) > self.WEATHER_STALE:
            cond = {}                                 # fetches failing for a while: drop old snow/storm/wind
        want = {}
        if (w.get("snow", True) and cond.get("snow")) or "snow" in forced:
            want["snow"] = ({"layers": [{"effect": "snowfall", "params": {"density": 0.8}}]}, "lighten", 1.0)
        if (w.get("rain") and cond.get("rain")) or "rain" in forced:
            want["rain"] = ({"layers": [{"effect": "rain"}]}, "lighten", 0.8)
        if (w.get("storm", True) and cond.get("storm")) or "storm" in forced:
            want["storm"] = ({"layers": [{"effect": "lightning", "params": {"interval": 7}}]}, "add", 1.0)
        wind = max(float(cond.get("wind_mph") or 0), float(cond.get("gust_mph") or 0) * 0.7)
        if "wind" in forced:
            wind = 35
        if w.get("wind", True) and wind >= 12 or "wind" in forced:
            depth = min(0.6, (wind - 8) / 40)
            want["wind"] = ({"layers": [{"effect": "solid", "params": {"color": "#ffffff"},
                                         "fx": [{"type": "shimmer", "params": {"depth": depth, "rate": 1 + wind / 8}}]}]},
                            "multiply", 1.0)
        ov = self.engine.overlays
        for k in ("snow", "rain", "storm", "wind"):      # only our own keys: pad hits live here too
            if k in ov and k not in want:
                ov.pop(k, None)
        for k, (d, mode, op) in want.items():
            if k not in ov or getattr(ov[k][0], "_spec", None) != d:
                sc = Scene.from_dict(d)
                sc._spec = d
                ov[k] = (sc, mode, op)

    def test_live(self, kind: str):
        if kind in ("snow", "rain", "storm", "wind"):
            self._forced_weather[kind] = time.time() + 60
            self._apply_weather()
            return f"{kind} overlay for 60 s"
        if kind in ("touchdown", "field goal", "score", "win"):
            g = next(iter(self.games.values()), {}) or {}
            d, secs = shows.celebration(kind, g.get("colors") or ["#fc4c02", "#0a2343"])
            self.start_interrupt(d, min(secs, 20), d["name"], force_live=True)
            return d["name"]
        if kind == "countdown":
            self.start_interrupt(shows.countdown_scene(time.time() + 12, 12, 10), 15, "Countdown test", force_live=True)
            return "countdown"
        if kind == "days":
            self.start_interrupt(shows.days_scene(24), 8, "Days to go test", force_live=True)
            return "days to go"
        raise ValueError(f"unknown test {kind}")

    # --------------------------------------------------------- mapping
    def map_start(self, level: float = 0.35) -> dict:
        from .effects import MAP_BITS, MAP_OFF, MAP_ON, MAP_SLOT, map_cycle
        with self.lock:
            if self.map_state is None:
                it, self.interrupt = self.interrupt, None
                base = it or {"scene": self.scene_dict, "playing": self.playing, "live": self.engine.live}
                self.map_state = {"scene": base["scene"], "playing": base["playing"], "live": base["live"],
                                  "speed": self.engine.master.get("speed", 1.0), "blackout": self.engine.blackout}
            self.engine.master["speed"] = 1.0
            self.engine.blackout = False
            for k in [k for k in self.engine.overlays if k.startswith(("visit:", "pad:"))]:
                self.engine.overlays.pop(k, None)      # nothing timed may spoil the capture
            self.engine.set_scene(Scene.from_dict({"layers": [{"effect": "mapcode",
                                                               "params": {"level": max(0.05, min(1.0, float(level)))}}]}))
            self.playing = {"kind": "event", "name": "Camera mapping pattern"}
        self.engine.set_live(True, release=True, force=True)     # every box must show the pattern
        return {"on": MAP_ON, "off": MAP_OFF, "slot": MAP_SLOT, "bits": MAP_BITS, "cycle": map_cycle(),
                "n": self.engine.layout.n}

    def map_stop(self):
        with self.lock:
            st, self.map_state = self.map_state, None
            if not st:
                return
            self.engine.master["speed"] = st["speed"]
            self.engine.blackout = st["blackout"]
            self.engine.set_scene(Scene.from_dict(st["scene"], self.user_palettes) if st["scene"] else None)
            self.playing = st["playing"]
            if not st["live"]:
                self.engine.set_live(False, release=True)

    def map_apply(self, points: list, target: str = "all") -> dict:
        L = self.engine.layout
        only = None if target in ("all", "", None) else L.indices(target)
        measured = shows.fit_mapping(L, points, only)
        geo = shows.merged_geometry(L, measured)
        with self.cfg.lock:
            for mac, knots in geo.items():
                d = self.cfg.devices[mac]
                d["geometry_prev"] = d.get("geometry", [])
                d["geometry"] = knots
            self.cfg.save()
        self.build_engine()
        return {"mapped": len(measured), "runs": [self.cfg.devices[m].get("name") for m in geo]}

    def map_undo(self) -> int:
        n = 0
        with self.cfg.lock:
            for d in self.cfg.devices.values():
                if "geometry_prev" in d:
                    d["geometry"], n = d.pop("geometry_prev"), n + 1
            self.cfg.save()
        self.build_engine()
        return n

    # ------------------------------------------------------------ WLED
    def add_wled(self, address: str, name: str = "") -> dict:
        """Add a WLED controller by IP (optionally ip:port for its web UI). Its bulb count
        and name come from WLED's own JSON API."""
        host, _, port = address.strip().partition(":")
        import ipaddress
        ipaddress.ip_address(host)                       # must be an IP address
        url = f"http://{host}:{int(port or 80)}/json/info"
        try:
            info = live.fetch_json(url, timeout=4)
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"couldn't reach WLED at {address}: {e}") from None
        count = int((info.get("leds") or {}).get("count") or 0)
        if count <= 0:
            raise ValueError("that doesn't look like a WLED controller (no LED count)")
        dev_id = "wled:" + (info.get("mac") or host)
        with self.cfg.lock:
            d = self.cfg.devices.setdefault(dev_id, {})
            d.update({"kind": "wled", "ip": host, "pixel_count": count, "sku": "WLED",
                      "name": name or d.get("name") or info.get("name") or "WLED",
                      "order": d.get("order", len(self.cfg.devices)), "firmware": {"wled": info.get("ver", "")}})
            for k, v in (("hidden", []), ("geometry", []), ("landmarks", []), ("diag", {})):
                d.setdefault(k, v)
            self.cfg.save()
        self.build_engine()
        return {"id": dev_id, "name": d["name"], "count": count}

    def remove_device(self, dev_id: str):
        with self.cfg.lock:
            self.cfg.devices.pop(dev_id, None)
            self.cfg.save()
        self.build_engine()

    # ------------------------------------------------------ diagnostics
    def enter_diag(self):
        if not self.diag_active:
            self.diag_active = True
            with self.lock:
                self.vqueue.clear()
            self._diag_was_live = self.engine.live
            self.engine.set_live(False, release=False)

    def exit_diag(self):
        if self.diag_active:
            self.diag_active = False
            self.diag.release_all()
            self.build_engine()          # counts / landmarks may have changed
            if getattr(self, "_diag_was_live", False):
                self.set_live(True, manual=False)          # back to how it was before diagnostics
            self._sched_key = "unset"    # let the schedule re-apply

    def resume_schedule(self):
        self.override_until = None
        self._sched_key = "unset"

    # --------------------------------------------------------- ticking
    def _tick_loop(self):
        last_sched = last_scan = 0.0
        while not self._stop.wait(1.0):
            try:
                now = time.time()
                try:
                    self._tick_once(now)
                except Exception:  # noqa: BLE001
                    log.exception("controller tick failed")     # never let it starve the schedule
                if now - last_sched >= 10:
                    last_sched = now
                    self._schedule_tick()
                if now - last_scan >= 300:
                    last_scan = now
                    self.diag.scan()          # pick up DHCP address changes
            except Exception:  # noqa: BLE001
                log.exception("controller tick failed")

    def _tick_once(self, now: float):
        """The once-a-second work (the tests drive this with a fake clock)."""
        with self.lock:
            self._show_tick(now)
            if self.interrupt and now >= self.interrupt["until"]:
                self.end_interrupt()
            self._visitor_tick()
            self._dusk_tick(now)
            if self.playlist and (self.interrupt or self.map_state):
                self.playlist["next_at"] += 1.0          # hold the playlist while interrupted
            elif self.playlist and now >= self.playlist["next_at"]:
                self._advance()
            self._evolve_tick(now)
        self._events_tick()
        self._showtimes_tick()

    def _schedule_tick(self):
        sch = self.cfg.data.get("schedule", {})
        now = datetime.now().astimezone()
        ev = sched.evaluate(now, sch)
        self.sched_status = ev
        self.engine.dim = float(ev.get("dim", 1.0)) if sch.get("enabled") else 1.0
        if not sch.get("enabled") or self.diag_active or self.map_state:
            return
        if self.override_until:
            if now < self.override_until:
                return
            self.override_until = None
            self._sched_key = "unset"
        key = json.dumps(ev["program"], sort_keys=True) if ev["active"] else None
        if key == self._sched_key:
            return
        if self.interrupt is not None:
            if key is not None:
                self._visitor_hold()
                return            # let the show/celebration finish; switch programs afterwards
            owns = ((self.interrupt or {}).get("show") or {}).get("owns")
            if owns and now.timestamp() < owns + self.OWNS_AFTER or self.interrupt.get("event"):
                return            # a show or event pinned to a moment (New Year's) finishes even after lights-off
            with self.lock:
                self.interrupt = None             # lights-off time beats any show still running
        self._sched_key = key
        log.info("schedule: %s", f"on ({ev['rule']}: {ev['program']})" if key else "off")
        with self.lock:
            di, self.dusk_intro = self.dusk_intro, None
        left = self._dusk_intro_left(now, ev, sch) if key else 0.0
        if left > 0 and di and di.get("on") == ev["on"]:
            with self.lock:                       # re-applied mid-intro (e.g. a schedule save): keep the sky
                di["program"] = ev["program"]
                self.dusk_intro = di
            return
        if left > 0 and ev.get("on") == self._dusk_done:
            left = 0.0                            # this evening's intro already ran (or the owner ended it)
        try:
            if left > 0 and self._start_dusk_intro(ev["program"], left, sch, ev.get("on")):
                return
            self.run_program(ev["program"] if ev["active"] else None, manual=False)
        except (OSError, ValueError) as e:
            log.warning("schedule action failed: %s", e)

    # ------------------------------------------------------ dusk intro
    def _dusk_intro_left(self, now: datetime, ev: dict, sch: dict) -> float:
        """Seconds of the dusk intro still to play: schedule.dusk_intro_minutes from the
        moment the evening window opened, when it opens around sunset. After a restart
        mid-window only what is left of it plays."""
        try:
            mins = max(0.0, min(120.0, float(sch.get("dusk_intro_minutes") or 0)))
            if mins <= 0 or not ev.get("active") or ev.get("rule") == "morning":
                return 0.0
            on = datetime.fromisoformat(ev["on"])
            _, sunset = sched.sun_times(on.date(), float(sch.get("lat", 40.3764)), float(sch.get("lon", -104.6922)))
        except (TypeError, ValueError, KeyError):
            return 0.0
        if sunset is None or abs((on - sunset).total_seconds()) > 90 * 60:
            return 0.0                            # lights on at a fixed time far from sunset
        left = mins * 60 - (now - on).total_seconds()
        return left if 30 < left <= mins * 60 + 60 else 0.0

    def _start_dusk_intro(self, prog: dict, left: float, sch: dict, on: str | None = None) -> bool:
        try:
            lat = max(-89.0, min(89.0, float(sch.get("lat", 40.3764))))
            lon = max(-180.0, min(180.0, float(sch.get("lon", -104.6922))))
        except (TypeError, ValueError):
            lat, lon = 40.3764, -104.6922
        d = {"name": "Dusk intro", "layers": [{"effect": "dusk", "target": "all", "params": {
            "lat": lat, "lon": lon, "clouds": 0.4, "sun_glow": 0.7, "stars": 0.12}}]}
        try:
            with self.lock:
                self.play_scene(d, {"kind": "dusk-intro", "name": "Dusk to night"}, fade=3.0, manual=False)
                self.dusk_intro = {"until": time.time() + left, "program": prog, "on": on}
                self._dusk_done = on
        except ValueError as e:                   # e.g. the dusk effect is missing: straight to the program
            log.warning("dusk intro failed: %s", e)
            return False
        self.set_live(True, manual=False)
        log.info("schedule: dusk intro for %.0f s, then %s", left, prog)
        return True

    def _dusk_to_program(self, fade: float = 8.0, swap_only: bool = False):
        """A manual action during the dusk intro: the scheduled program takes the intro's
        place, so whatever comes next (a show, a Live tap) returns to the program and not
        to the dusk sky. swap_only: set the program up but keep the house showing what it
        shows now (the caller is about to replace it). Call with self.lock held."""
        di = self.dusk_intro
        if not di:
            return
        self.dusk_intro = None
        shown, playing, it = self.engine.scene, self.playing, self.interrupt
        try:
            self.run_program(di["program"], manual=False, fade=fade, live=False)
        except (OSError, ValueError) as e:
            log.warning("program after the dusk intro failed: %s", e)
            return
        if it is not None:                        # something plays over the intro: it now returns to the program
            it["scene"], it["playing"] = self.scene_dict, self.playing
            self.playing = playing
            self.engine.set_scene(shown, 0)
        elif swap_only:
            self.engine.set_scene(shown, 0)

    def _dusk_tick(self, now: float):
        di = self.dusk_intro
        if not di or now < di["until"]:
            return
        if self.interrupt is not None or self.map_state or self.diag_active:
            self._visitor_hold()
            return                                # let the show/celebration finish first
        self.dusk_intro = None
        try:
            self.run_program(di["program"], manual=False, fade=8.0)
        except (OSError, ValueError) as e:
            log.warning("program after the dusk intro failed: %s", e)

    # ------------------------------------------------- tonight + calendar
    def tonight(self, password_set: bool | None = None) -> dict:
        """The plan and readiness checks for the next (or current) evening. Cached state only."""
        return tonight.build(self, datetime.now().astimezone(), password_set)

    def _calendar(self) -> tuple[list[str], dict]:
        ids = [p["id"] for p in self.presets.list()]
        return ids, calendar_builtin.build(ids, [s["name"] for s in self.shows_list()])

    def calendar_preview(self) -> dict:
        """What "Use the holiday calendar" would add, in date order from today."""
        ids, cal = self._calendar()
        cfg = self.cfg.data
        sch = cfg.get("schedule") or {}
        today = datetime.now().astimezone().date()
        names = tonight.Names({p["id"]: p["name"] for p in self.presets.list()},
                              {**(cfg.get("playlists") or {}), **cal["playlists"]})
        rules = []
        for k, r in enumerate(cal["rules"]):
            rng = calendar_builtin.next_range(r, today)
            rules.append({"name": r["name"], "from": r["from"], "to": r["to"], "days": r.get("days"),
                          "dates": calendar_builtin.fmt_range(rng, r.get("days")),
                          "start": rng[0].isoformat() if rng else "", "now": bool(rng and rng[0] <= today),
                          "program": r["program"], "program_name": names.program(r["program"]),
                          "program_detail": names.program_detail(r["program"]),
                          "about": cal["notes"].get(r["name"], ""), "off_time": r.get("off_time"), "priority": k})
        rules.sort(key=lambda x: (x["start"] or "9999", x["priority"]))
        sts = [{"show": st["show"], "date": st.get("date", ""), "time": tonight.fmt_time(
                    datetime.combine(today, sched._hm(st["from"]))),
                "dates": calendar_builtin.fmt_range(calendar_builtin.next_range(
                    {"from": st["date"], "to": st["date"]}, today)) if st.get("date") else "every day",
                "about": cal["notes"].get("show:" + st["show"], ""), "forced": not st.get("only_when_on", True)}
               for st in cal["showtimes"]]
        merged = calendar_builtin.merge(cal, sch, cfg.get("playlists") or {}, [], ids)[0]
        return {"rules": rules, "showtimes": sts, "skipped": cal["skipped"],
                "playlists": {n: names.program_detail({"playlist": n}) for n in cal["playlists"]},
                "shadows": calendar_builtin.shadows(sch, cal, today),
                "installed": calendar_builtin.installed(sch), "schedule_enabled": bool(sch.get("enabled")),
                "default": names.program(sch.get("default")), "default_after": names.program(merged.get("default")),
                "default_changes": merged.get("default") != sch.get("default")}

    def calendar_apply(self, disable=None, enable: bool = False) -> dict:
        """Merge the calendar into the schedule (replacing an earlier copy of it)."""
        from .app_server import validate_schedule, validate_showtimes
        ids, cal = self._calendar()
        disable = [int(k) for k in (disable or []) if isinstance(k, (int, float)) and not isinstance(k, bool)]
        with self.cfg.lock:
            d = self.cfg.data
            sch, pls, sts = calendar_builtin.merge(cal, d.get("schedule") or {}, d.get("playlists") or {},
                                                  d.get("showtimes") or [], ids, disable)
            if enable:
                sch["enabled"] = True
            validate_schedule(sch)
            validate_showtimes(sts)
            d["schedule"], d["playlists"], d["showtimes"] = sch, pls, sts
            self.cfg.save()
        self.resume_schedule()
        log.info("holiday calendar: %d rules, %d showtimes, %d skipped", len(cal["rules"]),
                 len(cal["showtimes"]), len(cal["skipped"]))
        return {"rules": len(cal["rules"]), "showtimes": len(cal["showtimes"]), "playlists": len(cal["playlists"]),
                "skipped": cal["skipped"]}

    def calendar_remove(self) -> dict:
        """Take the calendar out again; rules it switched off come back on."""
        with self.cfg.lock:
            d = self.cfg.data
            sch, pls, sts = calendar_builtin.strip(d.get("schedule") or {}, d.get("playlists") or {},
                                                   d.get("showtimes") or [])
            back = calendar_builtin.reenable(sch)
            d["schedule"], d["playlists"], d["showtimes"] = sch, pls, sts
            self.cfg.save()
        self.resume_schedule()
        return {"reenabled": back}

    # ---------------------------------------------------------- status
    def status(self) -> dict:
        e = self.engine
        sch = self.cfg.data.get("schedule", {})
        it, evs, health, di = self.interrupt, self.evolve, dict(self.health), self.dusk_intro
        pl = None
        if self.playlist:
            pl = {"name": self.playlist["name"], "next_in": max(0, round(self.playlist["next_at"] - time.time()))}
        return {
            "live": e.live, "playing": self.playing, "playlist": pl, "diag_active": self.diag_active,
            "fps": e.fps, "gamma": e.gamma, "brightness": e.brightness, "stats": e.stats,
            "master": e.master, "blackout": e.blackout, "dither": e.dither,
            "evolve": ({"next_in": max(0, round(evs["next_at"] - time.time())), "step": evs["step"]} if evs else None),
            "interrupt": ({"label": it.get("label"), "left": max(0, round(it["until"] - time.time()))} if it else None),
            "mapping": bool(self.map_state), "weather": self.weather, "overlays": list(e.overlays),
            "games": self.games, "dim": round(e.dim, 2),
            "show": ((it or {}).get("show") or {}).get("name"),
            "dusk_intro": ({"left": max(0, round(di["until"] - time.time())), "then": di["program"]} if di else None),
            "visitor_queue": len(self.vqueue),
            "health": {self.cfg.devices.get(m, {}).get("name", m): {
                "ok": h["miss"] == 0, "reasserts_15min": len([t for t in h["reasserts"] if time.time() - t < 900]),
                "paused": h["paused"]} for m, h in health.items()},
            "schedule": {"enabled": bool(sch.get("enabled")), **self.sched_status,
                         "override_until": self.override_until.isoformat(timespec="minutes") if self.override_until else None},
            "runs": [{"name": r.name, "mac": r.mac, "count": r.count, "visible": len(r.pixels), "trim": r.trim,
                      "kind": self.cfg.devices.get(r.mac, {}).get("kind", "govee"),
                      "ip": self.cfg.devices[r.mac].get("ip")} for r in e.layout.runs],
            "devices_without_count": [d.get("name") for d in self.cfg.devices.values() if not d.get("pixel_count")],
        }

    def layout_info(self) -> dict:
        L = self.engine.layout
        return {"runs": [r.name for r in L.runs], "targets": L.target_names(), "zones": L.zones,
                "runs_idx": {r.name: [p.i for p in r.pixels] for r in L.runs},
                "pixels": [[round(p.x, 2), round(p.y, 2), L.runs.index(L.run(p.mac))] for p in L.pixels],
                "bounds": [L.minx, L.miny, L.maxx, L.maxy]}

    def frame_hex(self) -> str:
        return "".join(f"{int(c[0] * 255 + .5):02x}{int(c[1] * 255 + .5):02x}{int(c[2] * 255 + .5):02x}"
                       for c in self.engine.last_colors)

    def shutdown(self):
        self._stop.set()
        try:
            self.engine.stop(release=True)
        finally:
            self.diag.release_all()
