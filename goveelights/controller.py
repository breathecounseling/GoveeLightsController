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

from . import evolve as evo
from . import live, livedata, shows
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
        self.health: dict[str, dict] = {}           # per-device self-healing state
        self.evolve: dict | None = None             # 'let it evolve' state
        self.vqueue: list[dict] = []                # visitor requests waiting
        self._vlast: dict[str, float] = {}          # visitor cooldowns by client
        self._showtimes_done: set = set()
        self._sched_key = "unset"
        self.sched_status: dict = {}
        self._stop = threading.Event()
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
            devs = {r.mac: self.cfg.devices[r.mac] for r in layout.runs}
            out = GoveeRazerOutput(self.lan, devs, refresh_s=float(self.ecfg.get("refresh_s", 1.0)),
                                   streamers=self.diag.streamers)
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
            self._manual()
        if on and self.diag_active:
            if not manual:
                return            # the schedule never interrupts diagnostics
            self.exit_diag()
        if on and not any(d.get("ip") for d in self.engine.output.devices.values()):
            raise ValueError("no devices with a known IP; scan first")
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
                self.evolve = None
            if not keep_playlist:
                self.playlist = None
            self.scene_dict = scene.to_dict()
            self.playing = label or {"kind": "scene", "name": scene.name}
            self.engine.set_scene(scene, fade, continuous=continuous)

    def play_preset(self, pid: str, fade: float = 1.0, manual: bool = True, keep_playlist: bool = False):
        d = self.presets.get(pid)
        self.play_scene(d, {"kind": "preset", "id": pid, "name": d.get("name", pid)}, fade, manual, keep_playlist)

    def play_evolve(self, pid: str, minutes: float = 3.0, amount: float = 0.2, manual: bool = True):
        """Play a preset, then keep crossfading into fresh variations of it."""
        base = self.presets.get(pid)
        self.play_preset(pid, fade=1.5, manual=manual)
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
        self.engine.set_scene(scene, 8.0)
        self.playing = {"kind": "evolve", "id": ev["id"], "name": ev["base"].get("name", ev["id"])}

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
            self.evolve = None
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
        elif prog.get("evolve"):
            self.play_evolve(prog["evolve"], float(prog.get("minutes", 3)), manual=manual)
        elif prog.get("preset"):
            self.play_preset(prog["preset"], manual=manual)
        self.set_live(True, manual=manual)

    def stop(self, manual: bool = True, release: bool = True):
        with self.lock:
            if manual:
                self._manual()
            self.playlist, self.playing, self.evolve = None, None, None
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
            if self.interrupt is None:
                self.interrupt = {"scene": self.scene_dict, "playing": self.playing, "live": self.engine.live}
            self.interrupt["until"] = time.time() + seconds
            self.interrupt["label"] = label
            self.interrupt.pop("show", None)
            self.interrupt.pop("visitor", None)
            self.engine.set_scene(Scene.from_dict(d, self.user_palettes), fade, transition=transition)
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

    # ----------------------------------------------------------- shows
    def _cue_scene(self, cue: dict) -> dict:
        return self.presets.get(cue["preset"])

    def play_show(self, name: str, manual: bool = True) -> bool:
        show = self.cfg.data.get("shows", {}).get(name)
        cues = [c for c in (show or {}).get("cues", []) if c.get("preset")]
        if not cues:
            raise ValueError(f"show {name!r} has no cues")
        if manual:
            self._manual()
        first = cues[0]
        total = sum(max(1.0, float(c.get("seconds", 20))) for c in cues)
        loop = (show or {}).get("end") == "loop"
        ok = self.start_interrupt(self._cue_scene(first), 10 ** 9 if loop else total + 1, f"Show: {name}",
                                  force_live=manual, fade=float(first.get("fade", 1.5)),
                                  transition=first.get("transition", "crossfade"))
        if ok:
            self.interrupt["show"] = {"name": name, "idx": 0, "loop": loop,
                                      "next_at": time.time() + max(1.0, float(first.get("seconds", 20)))}
        return ok

    def _show_tick(self, now: float):
        it = self.interrupt
        st = it and it.get("show")
        if not st or now < st["next_at"]:
            return
        cues = [c for c in self.cfg.data.get("shows", {}).get(st["name"], {}).get("cues", []) if c.get("preset")]
        st["idx"] += 1
        if st["idx"] >= len(cues):
            if not st["loop"] or not cues:
                self.end_interrupt()
                return
            st["idx"] = 0
        cue = cues[st["idx"]]
        try:
            self.engine.set_scene(Scene.from_dict(self._cue_scene(cue), self.user_palettes),
                                  float(cue.get("fade", 1.5)), transition=cue.get("transition", "crossfade"))
        except (OSError, ValueError) as e:
            log.warning("show cue %s failed: %s", cue, e)
        st["next_at"] = now + max(1.0, float(cue.get("seconds", 20)))

    def _showtimes_tick(self):
        now = datetime.now().astimezone()
        mins = now.hour * 60 + now.minute
        for k, stt in enumerate(self.cfg.data.get("showtimes", [])):
            if not stt.get("enabled", True) or stt.get("days") and now.weekday() not in stt["days"]:
                continue
            try:
                a = sched._hm(stt.get("from", "17:00"))
                b = sched._hm(stt.get("to", "22:00"))
            except ValueError:
                continue
            lo, hi = a.hour * 60 + a.minute, b.hour * 60 + b.minute
            every = max(5, int(stt.get("every", 60)))
            if not (lo <= mins <= hi) or (mins - lo) % every:
                continue
            key = (k, now.date().isoformat(), mins)
            if key in self._showtimes_done:
                continue
            self._showtimes_done.add(key)
            if stt.get("only_when_on", True) and not self.engine.live:
                continue
            if self.interrupt:                      # don't cut off a countdown or a celebration
                continue
            try:
                self.play_show(stt["show"], manual=False)
            except (KeyError, ValueError) as e:
                log.warning("showtime failed: %s", e)

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

    def visitor_state(self) -> dict:
        v = self.vcfg
        names = {p["id"]: p["name"] for p in self.presets.list()}
        it = self.interrupt or {}
        return {"title": v.get("title") or "Pick the lights!", "message": v.get("message", ""),
                "on": self.engine.live and not self.engine.blackout or not v.get("only_when_on", True),
                "presets": [{"id": i, "name": names[i]} for i in v.get("presets", []) if i in names],
                "queue": len(self.vqueue), "sparkle": bool(v.get("sparkle", True)),
                "now": (it.get("visitor") or {}).get("name") if it.get("visitor") else None}

    def visitor_pick(self, pid: str, client: str) -> dict:
        v, now = self.vcfg, time.time()
        if pid not in v.get("presets", []):
            raise ValueError("that one isn't available")
        if v.get("only_when_on", True) and not self.engine.live:
            raise ValueError("The lights are off right now. Come back this evening!")
        cd = float(v.get("cooldown", 60))
        if now - self._vlast.get(client, 0) < cd:
            raise ValueError(f"You can pick again in {int(cd - (now - self._vlast[client]))} s")
        if len(self.vqueue) >= int(v.get("max_queue", 5)):
            raise ValueError("Lots of requests right now. Try again in a minute!")
        self._vlast[client] = now
        if len(self._vlast) > 2000:
            self._vlast = {k: t for k, t in self._vlast.items() if now - t < cd}
        self.vqueue.append({"id": pid, "client": client})
        pos = len(self.vqueue)
        wait = (pos - 1) * float(v.get("seconds", 60))
        if self.interrupt and self.interrupt.get("visitor"):
            wait += max(0.0, self.interrupt["until"] - now)
        return {"position": pos, "wait": round(wait)}

    def visitor_sparkle(self, client: str) -> str:
        now = time.time()
        if not self.vcfg.get("sparkle", True):
            raise ValueError("not available")
        if now - self._vlast.get("sparkle:" + client, 0) < 15 or now - self._vlast.get("sparkle:*", 0) < 3:
            raise ValueError("Give it a few seconds!")
        if not self.engine.live:
            raise ValueError("The lights are off right now.")
        self._vlast["sparkle:" + client] = self._vlast["sparkle:*"] = now
        return self.pad("sparkle")

    def _visitor_tick(self):
        if not self.vqueue or not self.engine.live:
            return
        it = self.interrupt
        if it and not it.get("visitor"):            # a show, countdown or celebration is on: wait
            return
        if it and time.time() < it["until"]:
            return
        req = self.vqueue.pop(0)
        try:
            d = self.presets.get(req["id"])
        except (OSError, ValueError):
            return
        if self.start_interrupt(d, float(self.vcfg.get("seconds", 60)), f"Visitor pick: {d.get('name')}", fade=1.5):
            self.interrupt["visitor"] = {"name": d.get("name"), "id": req["id"]}

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
                            livedata.set_games(self.games)    # for the scoreboard text effect
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
    def map_start(self, level: float = 0.35) -> dict:
        from .effects import MAP_BITS, MAP_OFF, MAP_ON, MAP_SLOT, map_cycle
        with self.lock:
            if self.map_state is None:
                self.interrupt = None
                self.map_state = {"scene": self.scene_dict, "playing": self.playing, "live": self.engine.live,
                                  "speed": self.engine.master.get("speed", 1.0), "blackout": self.engine.blackout}
            self.engine.master["speed"] = 1.0
            self.engine.blackout = False
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

    # ------------------------------------------------------ diagnostics
    def enter_diag(self):
        if not self.diag_active:
            self.diag_active = True
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
                with self.lock:
                    self._show_tick(now)
                    if self.interrupt and now >= self.interrupt["until"]:
                        self.end_interrupt()
                    self._visitor_tick()
                    if self.playlist and (self.interrupt or self.map_state):
                        self.playlist["next_at"] += 1.0          # hold the playlist while interrupted
                    elif self.playlist and now >= self.playlist["next_at"]:
                        self._advance()
                    self._evolve_tick(now)
                self._events_tick()
                self._showtimes_tick()
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
        self.engine.dim = float(ev.get("dim", 1.0)) if sch.get("enabled") else 1.0
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
            "master": e.master, "blackout": e.blackout, "dither": e.dither,
            "evolve": ({"next_in": max(0, round(self.evolve["next_at"] - time.time())), "step": self.evolve["step"]}
                       if self.evolve else None),
            "interrupt": ({"label": self.interrupt.get("label"), "left": max(0, round(self.interrupt["until"] - time.time()))}
                          if self.interrupt else None),
            "mapping": bool(self.map_state), "weather": self.weather, "overlays": list(e.overlays),
            "games": self.games, "dim": round(e.dim, 2),
            "show": (self.interrupt or {}).get("show", {}).get("name") if self.interrupt else None,
            "visitor_queue": len(self.vqueue),
            "health": {self.cfg.devices.get(m, {}).get("name", m): {
                "ok": h["miss"] == 0, "reasserts_15min": len([t for t in h["reasserts"] if time.time() - t < 900]),
                "paused": h["paused"]} for m, h in self.health.items()},
            "schedule": {"enabled": bool(sch.get("enabled")), **self.sched_status,
                         "override_until": self.override_until.isoformat(timespec="minutes") if self.override_until else None},
            "runs": [{"name": r.name, "mac": r.mac, "count": r.count, "visible": len(r.pixels), "trim": r.trim,
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
