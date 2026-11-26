"""The app side of the hologram fan studio: what the phone page asks for.

    Studio(out_dir).info(ctl)            scenes, layouts, colour sources, ffmpeg, last job
    Studio.preview(ctl, body)            small looping GIFs (data: URLs), one per fan
    Studio.start(ctl, body) / .job()     full-size files, made in a separate low-priority
                                         process so the lights' engine never waits on it
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


def _colours_in(obj, pals: dict, out: list):
    """Every #rrggbb (and named palette) in a scene dict, in order."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "palette" and isinstance(v, str) and v in pals:
                _colours_in(pals[v], pals, out)
            elif k not in ("background",):
                _colours_in(v, pals, out)
    elif isinstance(obj, list):
        for v in obj:
            _colours_in(v, pals, out)
    elif isinstance(obj, str) and _HEX.match(obj):
        c = obj.lower()
        r, g, b = int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)
        if max(r, g, b) >= 48 and c not in out:          # black is "off" on a fan
            out.append(c)


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
            _colours_in(getattr(ctl, "scene_dict", None) or {}, pals, out)
    elif choice.startswith("pal:"):
        name = choice[4:]
        if name not in pals:
            raise ValueError(f"no palette called '{name}'")
        out = [c.lower() for c in pals[name]]
    elif choice.startswith("preset:"):
        _colours_in(ctl.presets.get(choice[7:]), pals, out)
    else:
        raise ValueError("colours must be '', 'house', 'pal:<name>' or 'preset:<id>'")
    out = [c for c in out if _HEX.match(c)]
    return out[:8] or None


def team_colours(ctl) -> list[str]:
    """The team colours the game tracker last saw (else a generic orange and navy)."""
    for g in (getattr(ctl, "games", None) or {}).values():
        cols = (g or {}).get("colors") or []
        if len(cols) >= 2 and all(isinstance(c, str) and _HEX.match(c) for c in cols[:2]):
            return [c.lower() for c in cols[:2]]
    return ["#fc4c02", "#0a2343"]


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


class Studio:
    def __init__(self, out_dir):
        self.out = Path(out_dir)
        self.lock = threading.Lock()
        self._cache: OrderedDict = OrderedDict()
        self._job: dict = {"state": "idle"}
        self._proc = None

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
        fmts = b.get("formats") or ["gif", "png", "mp4"]
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
            flags = 0
            if os.name == "nt":            # low priority, no console window
                flags = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
            elif shutil.which("nice"):
                argv = [shutil.which("nice"), "-n", "10"] + argv
            self._proc = subprocess.Popen(argv, cwd=str(_PKG_ROOT), env=env, stdin=subprocess.DEVNULL,
                                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags)
            self._job = {"state": "running", "scene": args["scene"], "layout": lay.name, "size": lay.size,
                         "started": time.time(), "done": 0, "total": int(round(args["seconds"] * args["fps"])),
                         "files": [], "skipped": {}}
            threading.Thread(target=self._wait, args=(self._proc, self._job), daemon=True).start()
            return dict(self._job)

    def _wait(self, proc, job):
        try:
            out, err = proc.communicate(timeout=JOB_TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.kill()
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
                self._proc.kill()
                self._job.update(state="cancelled")
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
