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
        self._sched_key = "unset"
        self.sched_status: dict = {}
        self._stop = threading.Event()
        self.build_engine()
        self.diag.scan()
        threading.Thread(target=self._tick_loop, name="controller", daemon=True).start()

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
        if key is not None and key in self._thumbs:
            return self._thumbs[key]
        sc = Scene.from_dict(scene_dict, self.user_palettes)
        L = self.engine.layout
        out = []
        for k in range(frames):
            cols = sc.render(2.0 + k / fps, L)
            out.append("".join(f"{int(c[0] * 255 + .5):02x}{int(c[1] * 255 + .5):02x}{int(c[2] * 255 + .5):02x}"
                               for c in cols))
        res = {"fps": fps, "frames": out}
        if key is not None:
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
                    if self.playlist and now >= self.playlist["next_at"]:
                        self._advance()
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
        if not sch.get("enabled") or self.diag_active:
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
