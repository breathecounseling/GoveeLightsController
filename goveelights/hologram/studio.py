"""The app side of the hologram fan studio: what the phone page asks for.

    Studio(out_dir).info(ctl)            scenes, layouts, colour sources, ffmpeg, last job
    Studio.preview(ctl, body)            small looping GIFs (data: URLs), one per fan
    Studio.start(ctl, body) / .job()     full-size files, made in a separate low-priority
                                         process so the lights' engine never waits on it
                                         (previews are small, cached and made in the server)
    Studio.file(name)                    a finished file, for download

Nothing here touches the lights or the config: settings (layout, fan size...) live on
the phone, and files go to a folder next to the config (hologram_clips/).
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections import OrderedDict
from pathlib import Path

from . import LAYOUT_NAMES, SCENES, Clip, FanLayout, export

log = logging.getLogger("goveelights")
SIZES = (240, 360, 480, 720, 1080)
PREVIEW_FPS = 12
JOB_TIMEOUT = 20 * 60
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_PKG_ROOT = Path(__file__).resolve().parent.parent.parent
RESULT = "result.json"      # the last finished job, so its files survive an app restart
SCENE_FLOOR = 100           # a light scene's colours dimmer than this are base washes, not its look


def _colours_in(obj, pals: dict, out: list, floor: int = 48):
    """Every #rrggbb (and named palette) in a scene dict, in order."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "palette" and isinstance(v, str) and v in pals:
                _colours_in(pals[v], pals, out, floor)
            elif k not in ("background",):
                _colours_in(v, pals, out, floor)
    elif isinstance(obj, list):
        for v in obj:
            _colours_in(v, pals, out, floor)
    elif isinstance(obj, str) and _HEX.match(obj):
        c = obj.lower()
        r, g, b = int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)
        if max(r, g, b) >= floor and c not in out:       # black is "off" on a fan
            out.append(c)


def _scene_colours(scene, pals: dict, out: list):
    """What a light scene (preset) looks like, as a few bright colours: the layers drawn
    on top (lighten / add / screen...) first, then the base layers; a layer that names no
    colours adds its effect's own default colours; dim base washes are dropped."""
    layers = scene.get("layers") if isinstance(scene, dict) else None
    if not isinstance(layers, list):
        _colours_in(scene, pals, out, SCENE_FLOOR)
        return
    try:
        from ..effects import EFFECTS
    except Exception:                        # noqa: BLE001 - explicit colours still work
        EFFECTS = {}
    layers = [ly for ly in layers if isinstance(ly, dict)]
    for ly in sorted(layers, key=lambda ly: str(ly.get("blend") or "normal") == "normal"):
        params = ly.get("params")
        n = len(out)
        _colours_in(params if isinstance(params, (dict, list)) else {}, pals, out, SCENE_FLOOR)
        named = isinstance(params, dict) and any(k in params for k in ("color", "colors", "palette"))
        if len(out) == n and not named:
            eff = EFFECTS.get(ly.get("effect")) if isinstance(ly.get("effect"), str) else None
            for prm in getattr(eff, "params", None) or []:
                if getattr(prm, "kind", "") in ("color", "colors"):
                    _colours_in(prm.default, pals, out, SCENE_FLOOR)


def resolve_colours(ctl, choice: str) -> list[str] | None:
    """The colours a clip should use: "" = the scene's own; "house" = what the lights are
    playing now (the master palette if one is set, else the playing scene's colours);
    "pal:<name>" = a palette; "preset:<id>" = a saved preset's colours."""
    from ..palettes import all_palettes
    choice = str(choice or "")
    if not choice:
        return None
    pals = all_palettes(getattr(ctl, "user_palettes", None) or {})
    out: list = []
    if choice == "house":
        m = getattr(getattr(ctl, "engine", None), "master", {}) or {}
        if m.get("palette") and m["palette"] in pals:
            out = [c.lower() for c in pals[m["palette"]]]
        else:
            _scene_colours(getattr(ctl, "scene_dict", None) or {}, pals, out)
    elif choice.startswith("pal:"):
        name = choice[4:]
        if name not in pals:
            raise ValueError(f"no palette called '{name}'")
        out = [c.lower() for c in pals[name]]
    elif choice.startswith("preset:"):
        pid = choice[7:]
        try:
            preset = ctl.presets.get(pid)
        except (OSError, KeyError, ValueError):          # never echo the server's file paths
            raise ValueError(f"no preset called '{pid[:60]}'") from None
        _scene_colours(preset, pals, out)
    else:
        raise ValueError("colours must be '', 'house', 'pal:<name>' or 'preset:<id>'")
    out = [c for c in out if _HEX.match(c)]
    return out[:8] or None


def team_colours(ctl) -> list[str]:
    """The team colours the game tracker last saw (else the scene's neutral gold and green)."""
    for g in (getattr(ctl, "games", None) or {}).values():
        cols = (g or {}).get("colors") or []
        if len(cols) >= 2 and all(isinstance(c, str) and _HEX.match(c) for c in cols[:2]):
            return [c.lower() for c in cols[:2]]
    return list(SCENES["football"].default_colors)


def _num(b: dict, key: str, lo: float, hi: float, default):
    v = b.get(key, default)
    if v in (None, ""):
        return default
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        raise ValueError(f"{key} must be a number")
    v = float(v)
    if not lo <= v <= hi:
        raise ValueError(f"{key} must be {lo:g} to {hi:g}")
    return v


def parse(ctl, b: dict, preview: bool) -> dict:
    """Validate a request body into Clip arguments."""
    scene = str(b.get("scene", ""))
    if scene not in SCENES:
        raise ValueError("pick a scene")
    lay = str(b.get("layout", "1"))
    if lay not in LAYOUT_NAMES:
        raise ValueError("pick a layout")
    diameter = _num(b, "diameter", 10, 300, 42.0)
    spacing = _num(b, "spacing", 0, 1200, 0.0)
    seconds = _num(b, "seconds", 2, 12, SCENES[scene].seconds)
    fps = _num(b, "fps", 10, 30, SCENES[scene].fps)
    count = FanLayout(lay).count
    if preview:
        size = 240 if count == 1 else 140 if count <= 3 else 112
        fps = PREVIEW_FPS
    else:
        size = int(_num(b, "size", 64, 1080, 480))
        if size not in SIZES:
            raise ValueError(f"size must be one of {', '.join(map(str, SIZES))}")
    team = b.get("team") or None
    if team is not None:
        if not isinstance(team, list) or not 1 <= len(team) <= 2 or not all(isinstance(c, str) and _HEX.match(c) for c in team):
            raise ValueError("team colours must be one or two #rrggbb colours")
    elif scene == "football":
        team = team_colours(ctl)
    year = b.get("year")
    if year not in (None, ""):
        year = int(_num(b, "year", 2000, 2200, 0))
    else:
        year = None
    return {"scene": scene, "layout": FanLayout(lay, size=size, diameter_cm=diameter, spacing_cm=spacing),
            "seconds": seconds, "fps": fps, "colors": resolve_colours(ctl, b.get("colors", "")),
            "team": team, "year": year}


def _kill_tree(proc):
    """Stop a clip job and the ffmpeg encoders it started, so none keeps writing a half
    file after the job reports cancelled."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=15,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            os.killpg(proc.pid, signal.SIGKILL)          # it was started in its own session
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        proc.kill()
    except OSError:
        pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


class Studio:
    def __init__(self, out_dir):
        self.out = Path(out_dir).resolve()       # the job runs from another folder
        self.lock = threading.Lock()
        self._cache: OrderedDict = OrderedDict()
        self._job: dict = {"state": "idle"}
        self._proc = None
        self._load_result()

    def _load_result(self):
        """The last finished job's files, if they are still on disk (after a restart)."""
        try:
            j = json.loads((self.out / RESULT).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(j, dict) or j.get("state") != "done" or not isinstance(j.get("files"), list):
            return
        files = [f for f in j["files"] if isinstance(f, str) and export.safe_name(f) and (self.out / f).is_file()]
        if files:
            keep = {k: j[k] for k in ("scene", "layout", "size", "started", "total", "seconds") if k in j}
            sk = j.get("skipped")
            self._job = {**keep, "state": "done", "files": files, "done": keep.get("total", 0),
                         "skipped": sk if isinstance(sk, dict) else {}}

    def _save_result(self, job: dict):
        try:
            tmp = self.out / (RESULT + ".tmp")
            tmp.write_text(json.dumps(job), encoding="utf-8")
            tmp.replace(self.out / RESULT)
        except (OSError, TypeError, ValueError) as e:
            log.info("could not save the clip list: %s", e)

    # ------------------------------------------------------------ info
    def info(self, ctl) -> dict:
        from ..palettes import all_palettes
        try:
            presets = [{"id": p["id"], "name": p.get("name", p["id"])} for p in ctl.presets.list()]
        except Exception:                    # noqa: BLE001 - the page works without them
            presets = []
        ff = export.ffmpeg_info()
        return {"scenes": [cls.info() for cls in SCENES.values()],
                "layouts": LAYOUT_NAMES, "sizes": list(SIZES),
                "palettes": sorted(all_palettes(getattr(ctl, "user_palettes", None) or {})),
                "presets": presets, "team": team_colours(ctl),
                "mp4": bool(ff), "encoder": ff.get("encoder", ""), "job": self.job()}

    # ------------------------------------------------------------ preview
    def preview(self, ctl, b: dict) -> dict:
        args = parse(ctl, b, preview=True)
        lay = args["layout"]
        key = json.dumps({**{k: v for k, v in args.items() if k != "layout"}, "layout": lay.to_dict()}, sort_keys=True)
        with self.lock:
            hit = self._cache.get(key)
            if hit:
                self._cache.move_to_end(key)
                return hit
        t0 = time.time()
        clip = Clip(args["scene"], lay, args["seconds"], args["fps"], args["colors"], args["team"], args["year"])
        gifs = export.preview_gifs(clip)
        res = {"ok": True, "tiles": ["data:image/gif;base64," + base64.b64encode(g).decode() for g in gifs],
               "layout": lay.to_dict(), "seconds": clip.seconds, "colors": args["colors"] or [],
               "team": args["team"] or [], "year": clip.ctx.year, "ms": int((time.time() - t0) * 1000)}
        with self.lock:
            self._cache[key] = res
            while len(self._cache) > 12:
                self._cache.popitem(last=False)
        return res

    # ------------------------------------------------------------ full-size files
    def _clean(self):
        """Clear the previous job's files (the folder only ever holds one set)."""
        if not self.out.is_dir():
            return
        for p in self.out.iterdir():
            if p.is_file() and p.suffix in (".gif", ".mp4", ".zip", ".png", ".json", ".tmp") and export.safe_name(p.name):
                try:
                    p.unlink()
                except OSError:
                    pass

    def start(self, ctl, b: dict) -> dict:
        args = parse(ctl, b, preview=False)
        fmts = b.get("formats") or list(export.DEFAULT_FORMATS)
        if not isinstance(fmts, list) or not all(f in export.FORMATS for f in fmts):
            raise ValueError("formats must be a list of gif, png, mp4")
        lay = args["layout"]
        with self.lock:
            if self._proc is not None and self._proc.poll() is None:
                raise ValueError("already making files; wait for that to finish (or cancel it)")
            self.out.mkdir(parents=True, exist_ok=True)
            self._clean()
            prog = self.out / "progress.json"
            argv = [sys.executable, "-m", "goveelights", "hologram", args["scene"], "--layout", lay.name,
                    "--size", str(lay.size), "--seconds", f"{args['seconds']:g}", "--fps", f"{args['fps']:g}",
                    "--diameter", f"{lay.diameter_cm:g}", "--spacing", f"{lay.spacing_cm:g}",
                    "--formats", ",".join(fmts), "--zip-png", "--json", "--out", str(self.out),
                    "--progress-file", str(prog)]
            if args["colors"]:
                argv += ["--palette", ",".join(args["colors"])]
            if args["team"]:
                argv += ["--team", ",".join(args["team"])]
            if args["year"]:
                argv += ["--year", str(args["year"])]
            env = dict(os.environ)
            env["PYTHONPATH"] = str(_PKG_ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
            flags, extra = 0, {}
            if os.name == "nt":            # low priority, no console window
                flags = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
            else:                          # its own process group, so cancel also stops its ffmpegs
                extra["start_new_session"] = True
                if shutil.which("nice"):
                    argv = [shutil.which("nice"), "-n", "10"] + argv
            self._proc = subprocess.Popen(argv, cwd=str(_PKG_ROOT), env=env, stdin=subprocess.DEVNULL,
                                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags, **extra)
            self._job = {"state": "running", "scene": args["scene"], "layout": lay.name, "size": lay.size,
                         "started": time.time(), "done": 0, "total": int(round(args["seconds"] * args["fps"])),
                         "files": [], "skipped": {}}
            threading.Thread(target=self._wait, args=(self._proc, self._job), daemon=True).start()
            return dict(self._job)

    def _wait(self, proc, job):
        try:
            out, err = proc.communicate(timeout=JOB_TIMEOUT)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            out, err = proc.communicate()
            with self.lock:
                job.update(state="failed", error="took too long and was stopped")
            return
        with self.lock:
            if job.get("state") == "cancelled":
                return
            if proc.returncode == 0:
                try:
                    res = json.loads(out.decode("utf-8", "replace").strip().splitlines()[-1])
                    job.update(state="done", files=[f for f in res["files"] if export.safe_name(f)],
                               skipped=res.get("skipped", {}), seconds=res.get("seconds"), done=job["total"])
                    self._save_result(job)
                except (ValueError, KeyError, IndexError):
                    job.update(state="failed", error="the clip maker gave no result")
            else:
                msg = err.decode("utf-8", "replace").strip().splitlines()
                log.warning("hologram job failed: %s", " | ".join(msg[-3:]))
                job.update(state="failed", error=(msg[-1] if msg else f"exit {proc.returncode}")[:200])

    def job(self) -> dict:
        with self.lock:
            j = dict(self._job)
        if j.get("state") == "running":
            try:
                p = json.loads((self.out / "progress.json").read_text())
                j["done"], j["total"] = int(p["done"]), int(p["total"])
            except (OSError, ValueError, KeyError):
                pass
        return j

    def cancel(self) -> dict:
        with self.lock:
            if self._proc is not None and self._proc.poll() is None:
                self._job.update(state="cancelled")
                _kill_tree(self._proc)
        return self.job()

    def stop(self):
        try:
            self.cancel()
        except Exception:                     # noqa: BLE001 - shutting down anyway
            pass

    def file(self, name: str) -> Path:
        """A finished file from the last job (only names that job reported)."""
        if not export.safe_name(name):
            raise KeyError(name)
        with self.lock:
            ok = self._job.get("state") == "done" and name in self._job.get("files", [])
        p = self.out / name
        if not ok or not p.is_file():
            raise KeyError(name)
        return p
