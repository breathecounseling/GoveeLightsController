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
from datetime import datetime
from pathlib import Path

from . import live, shows
from . import schedule as sched
from .config import Config
from .diag_server import App as DiagApp
from .engine import Engine
from .layout import Layout
from .output import GoveeRazerOutput
from .scene import Scene

log = logging.getLogger(__name__)
PRESETS_DIR = Path(os.environ.get("GOVEE_PRESETS", Path(__file__).resolve().parent.parent / "presets"))
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s[:64] or "preset"


class PresetStore:
    def __init__(self, folder: Path = PRESETS_DIR):
        self.dir = Path(folder)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, pid: str) -> Path:
        if not _ID.match(pid or ""):
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
        self._sched_key = "unset"
        self.sched_status: dict = {}
        self._stop = threading.Event()
        self.build_engine()
        self.diag.scan()
        threading.Thread(target=self._tick_loop, name="controller", daemon=True).start()
        threading.Thread(target=self._live_loop, name="live-data", daemon=True).start()

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
            devs = {r.mac: self.cfg.devices[r.mac] for r in layout.runs}
            out = GoveeRazerOutput(self.lan, devs, refresh_s=float(self.ecfg.get("refresh_s", 1.0)),
                                   streamers=self.diag.streamers)
            if old:
                old.stop(release=False)
            e = Engine(layout, out, fps=float(self.ecfg.get("fps", 20)), gamma=float(self.ecfg.get("gamma", 1.0)),
                       brightness=float(self.ecfg.get("brightness", 1.0)))
            e.master.update(self.cfg.data.get("master", {}))
            self._thumbs = {}
            if old and old.scene:
                e.set_scene(old.scene)
            e.start(live=False)
            self.engine = e
            if was_live:
                self.set_live(True, manual=False)

    def set_live(self, on: bool, manual: bool = True):
        if manual:
            self._manual()
        if on and self.diag_active:
            if not manual:
                return            # the schedule never interrupts diagnostics
            self.exit_diag()
        if on and not any(d.get("ip") for d in self.engine.output.devices.values()):
            raise ValueError("no devices with a known IP; scan first")
        self.engine.set_live(on, release=True)

    def settings(self, b: dict):
        e = self.engine
        with self.cfg.lock:
            if "fps" in b:
                e.fps = self.ecfg["fps"] = max(1.0, min(60.0, float(b["fps"])))
            if "gamma" in b:
                e.gamma = self.ecfg["gamma"] = max(0.5, min(3.0, float(b["gamma"])))
            if "brightness" in b:
                e.brightness = self.ecfg["brightness"] = max(0.0, min(1.0, float(b["brightness"])))
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
        for k in range(frames):
            cols = sc.render(2.0 + k / fps, L, look)
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
            if not keep_playlist:
                self.playlist = None
            self.scene_dict = scene.to_dict()
            self.playing = label or {"kind": "scene", "name": scene.name}
            self.engine.set_scene(scene, fade, continuous=continuous)

    def play_preset(self, pid: str, fade: float = 1.0, manual: bool = True, keep_playlist: bool = False):
        d = self.presets.get(pid)
        self.play_scene(d, {"kind": "preset", "id": pid, "name": d.get("name", pid)}, fade, manual, keep_playlist)

    def play_playlist(self, name: str, manual: bool = True):
        pl = self.cfg.data.get("playlists", {}).get(name)
        if not pl or not pl.get("items"):
            raise ValueError(f"playlist {name!r} is empty or missing")
        order = list(range(len(pl["items"])))
        if pl.get("shuffle"):
            random.shuffle(order)
        with self.lock:
            if manual:
                self._manual()
            self.playlist = {"name": name, "order": order, "idx": -1, "next_at": 0.0, "fade": float(pl.get("fade", 2))}
            self._advance(first=True)

    def _advance(self, first=False):
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
            self.play_preset(item["preset"], fade=0 if first else st["fade"], manual=False, keep_playlist=True)
        except (OSError, ValueError, KeyError) as e:
            log.warning("playlist item %s failed: %s", item, e)
        self.playing = {"kind": "playlist", "name": st["name"], "item": item.get("preset"),
                        "item_name": (self.playing or {}).get("name")}

    def run_program(self, prog: dict | None, manual: bool = False):
        if not prog:
            self.stop(manual=manual)
            return
        if prog.get("playlist"):
            self.play_playlist(prog["playlist"], manual=manual)
        elif prog.get("preset"):
            self.play_preset(prog["preset"], manual=manual)
        self.set_live(True, manual=manual)

    def stop(self, manual: bool = True, release: bool = True):
        with self.lock:
            if manual:
                self._manual()
            self.playlist, self.playing = None, None
            self.engine.set_scene(None)
            self.engine.set_live(False, release=release)

    # ---------------------------------------------------- interruptions
    def start_interrupt(self, d: dict, seconds: float, label: str, force_live: bool = False) -> bool:
        """Play a scene for a while (celebration, countdown), then put back what was playing."""
        with self.lock:
            if self.diag_active or self.map_state:
                return False
            if not self.engine.live and not force_live:
                return False
            if self.interrupt is None:
                self.interrupt = {"scene": self.scene_dict, "playing": self.playing, "live": self.engine.live}
            self.interrupt["until"] = time.time() + seconds
            self.interrupt["label"] = label
            self.engine.set_scene(Scene.from_dict(d, self.user_palettes), 0.3)
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
                self.engine.set_live(False, release=True)

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
            cd = float(ev.get("countdown", 0))
            if cd > 0 and 0 < secs <= cd and ("cd",) + key not in self._events_done:
                self._events_done.add(("cd",) + key)
                self.start_interrupt(shows.countdown_scene(at.timestamp(), int(cd), int(ev.get("digits", 10))),
                                     secs + 0.5, f"{ev.get('name')} countdown", force_live=ev.get("lights_on", True))
            elif -2 < secs <= 0.5 and ("party",) + key not in self._events_done and float(ev.get("minutes", 0)) > 0:
                self._events_done.add(("party",) + key)
                try:
                    d = self.presets.get(ev.get("celebrate", "fireworks"))
                except (OSError, ValueError):
                    d = shows.celebration("win", ["#ffd700", "#ffffff"])[0]
                self.start_interrupt(d, float(ev["minutes"]) * 60, ev.get("name", "Celebration"),
                                     force_live=ev.get("lights_on", True))
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
                for item in list(self._pending):
                    if now >= item[0]:
                        self._pending.remove(item)
                        _, kind, colors = item
                        d, secs = shows.celebration(kind, colors)
                        self.start_interrupt(d, secs, d["name"], force_live=bool(g.get("when_lights_off")))
            except Exception:  # noqa: BLE001
                log.exception("live data loop failed")

    def _poll_game(self, t: dict, key: str) -> float:
        """Check one team; returns seconds until the next check."""
        league, team = t.get("league", "nfl"), t.get("team", "")
        try:
            game = live.find_game(live.fetch_json(live.scoreboard_url(league)), team)
        except Exception as e:  # noqa: BLE001
            self.games[key] = {**self.games.get(key, {}), "error": str(e)}
            return 300
        prev = self.games.get(key)
        self.games[key] = game or {"state": "none", "team": team}
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

    def _apply_weather(self):
        w = self.cfg.data.get("weather", {})
        now = time.time()
        forced = {k for k, until in self._forced_weather.items() if until > now}
        cond = self.weather if w.get("enabled") else {}
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
        for k in list(ov):
            if k not in want:
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
    def map_start(self) -> dict:
        from .effects import MAP_BITS, MAP_OFF, MAP_ON, MAP_SLOT, map_cycle
        with self.lock:
            if self.map_state is None:
                self.interrupt = None
                self.map_state = {"scene": self.scene_dict, "playing": self.playing, "live": self.engine.live,
                                  "speed": self.engine.master.get("speed", 1.0), "blackout": self.engine.blackout}
            self.engine.master["speed"] = 1.0
            self.engine.blackout = False
            self.engine.set_scene(Scene.from_dict({"layers": [{"effect": "mapcode"}]}))
            self.playing = {"kind": "event", "name": "Camera mapping pattern"}
        self.set_live(True, manual=False)
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

    # ------------------------------------------------------ diagnostics
    def enter_diag(self):
        if not self.diag_active:
            self.diag_active = True
            self.engine.set_live(False, release=False)

    def exit_diag(self):
        if self.diag_active:
            self.diag_active = False
            self.diag.release_all()
            self.build_engine()          # counts / landmarks may have changed
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
                with self.lock:
                    if self.interrupt and now >= self.interrupt["until"]:
                        self.end_interrupt()
                    if self.playlist and (self.interrupt or self.map_state):
                        self.playlist["next_at"] += 1.0          # hold the playlist while interrupted
                    elif self.playlist and now >= self.playlist["next_at"]:
                        self._advance()
                self._events_tick()
                if now - last_sched >= 10:
                    last_sched = now
                    self._schedule_tick()
                if now - last_scan >= 300:
                    last_scan = now
                    self.diag.scan()          # pick up DHCP address changes
            except Exception:  # noqa: BLE001
                log.exception("controller tick failed")

    def _schedule_tick(self):
        sch = self.cfg.data.get("schedule", {})
        now = datetime.now().astimezone()
        ev = sched.evaluate(now, sch)
        self.sched_status = ev
        if not sch.get("enabled") or self.diag_active or self.map_state or self.interrupt:
            return
        if self.override_until:
            if now < self.override_until:
                return
            self.override_until = None
            self._sched_key = "unset"
        key = json.dumps(ev["program"], sort_keys=True) if ev["active"] else None
        if key == self._sched_key:
            return
        self._sched_key = key
        log.info("schedule: %s", f"on ({ev['rule']}: {ev['program']})" if key else "off")
        try:
            self.run_program(ev["program"] if ev["active"] else None, manual=False)
        except (OSError, ValueError) as e:
            log.warning("schedule action failed: %s", e)

    # ---------------------------------------------------------- status
    def status(self) -> dict:
        e = self.engine
        sch = self.cfg.data.get("schedule", {})
        pl = None
        if self.playlist:
            pl = {"name": self.playlist["name"], "next_in": max(0, round(self.playlist["next_at"] - time.time()))}
        return {
            "live": e.live, "playing": self.playing, "playlist": pl, "diag_active": self.diag_active,
            "fps": e.fps, "gamma": e.gamma, "brightness": e.brightness, "stats": e.stats,
            "master": e.master, "blackout": e.blackout,
            "interrupt": ({"label": self.interrupt.get("label"), "left": max(0, round(self.interrupt["until"] - time.time()))}
                          if self.interrupt else None),
            "mapping": bool(self.map_state), "weather": self.weather, "overlays": list(e.overlays),
            "games": self.games,
            "schedule": {"enabled": bool(sch.get("enabled")), **self.sched_status,
                         "override_until": self.override_until.isoformat(timespec="minutes") if self.override_until else None},
            "runs": [{"name": r.name, "mac": r.mac, "count": r.count, "visible": len(r.pixels),
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
