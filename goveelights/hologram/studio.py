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

from . import LAYOUT_NAMES, SCENES, Clip, FanLayout, export, targets
from .render import max_seconds

log = logging.getLogger("goveelights")
SIZES = (240, 360, 480, 720, 1080)
PREVIEW_FPS = 12
PREVIEW_FPS_LONG = 10       # a 24 s story at 12 fps would be a slow, heavy preview
DISPLAYS = ("fans", "frame", "matrix")
FRAMES = ("1280x720", "1920x1080", "3840x2160", "1024x768", "1080x1920")     # the app's choices (any WxH works)
MATRICES = ("16x16", "32x8", "32x16", "32x32", "64x32", "64x64", "128x64")
JOB_TIMEOUT = 20 * 60
PREVIEW_INLINE_FRAMES = 12 * PREVIEW_FPS    # up to a 12 s loop: made in the server (as before)
PREVIEW_TIMEOUT = 120
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
    seconds = _num(b, "seconds", 2, max_seconds(scene), SCENES[scene].seconds)
    fps = _num(b, "fps", 10, 30, SCENES[scene].fps)
    display = str(b.get("display") or "fans")
    if display not in DISPLAYS:
        raise ValueError("display must be fans, frame or matrix")
    count = FanLayout(lay).count
    if preview:
        size = 240 if count == 1 else 140 if count <= 3 else 112 if count <= 4 else 96
        fps = PREVIEW_FPS if seconds <= 12 else PREVIEW_FPS_LONG
    else:
        size = int(_num(b, "size", 64, 1080, 480))
        if size not in SIZES:
            raise ValueError(f"size must be one of {', '.join(map(str, SIZES))}")
    background = str(b.get("background") or "black")
    if background not in targets.BACKGROUNDS:
        raise ValueError("background must be black or night")
    extra = {"display": display}
    if display == "frame":
        w, h = targets.parse_wh(b.get("frame") or "1280x720", "frame")
        targets.FrameLayout(w, h, background)                         # checks the size
        extra.update(dims=(w, h), background=background)
        if preview:                                                   # small, same shape
            k = 320 / max(w, h)
            w, h = max(64, int(round(w * k / 2)) * 2), max(64, int(round(h * k / 2)) * 2)
        layout = targets.FrameLayout(w, h, background)
    elif display == "matrix":
        w, h = targets.parse_wh(b.get("matrix") or "64x32", "matrix")
        gamma = _num(b, "gamma", 0.5, 3.5, 1.0)
        serp = bool(b.get("serpentine"))
        layout = targets.MatrixLayout(w, h, gamma, serp, background)
        extra.update(dims=(w, h), background=background, gamma=gamma, serpentine=serp)
    else:
        layout = FanLayout(lay, size=size, diameter_cm=diameter, spacing_cm=spacing)
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
    return {"scene": scene, "layout": layout,
            "seconds": seconds, "fps": fps, "colors": resolve_colours(ctl, b.get("colors", "")),
            "team": team, "year": year, "extra": extra}


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


def _low_priority(argv: list) -> tuple:
    """argv, env, creationflags and Popen extras for a low-priority helper process."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_PKG_ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    flags, extra = 0, {}
    if os.name == "nt":            # low priority, no console window
        flags = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:                          # its own process group, so cancel also stops its ffmpegs
        extra["start_new_session"] = True
        if shutil.which("nice"):
            argv = [shutil.which("nice"), "-n", "10"] + argv
    return argv, env, flags, extra


def _layout_spec(lay) -> dict:
    kind = getattr(lay, "kind", "fans")
    if kind == "frame":
        return {"kind": kind, "w": lay.w, "h": lay.h, "background": lay.background}
    if kind == "matrix":
        return {"kind": kind, "w": lay.w, "h": lay.h, "gamma": lay.gamma, "serpentine": lay.serpentine,
                "background": lay.frame.background}
    return {"kind": "fans", "name": lay.name, "size": lay.size, "diameter": lay.diameter_cm, "spacing": lay.spacing_cm}


def _layout_from(d: dict):
    if d["kind"] == "frame":
        return targets.FrameLayout(d["w"], d["h"], d["background"])
    if d["kind"] == "matrix":
        return targets.MatrixLayout(d["w"], d["h"], d["gamma"], d["serpentine"], d["background"])
    return FanLayout(d["name"], size=d["size"], diameter_cm=d["diameter"], spacing_cm=d["spacing"])


def render_preview(spec: dict) -> dict:
    """The preview GIFs (base64) for a spec made by Studio.preview."""
    lay = _layout_from(spec["layout"])
    clip = Clip(spec["scene"], lay, spec["seconds"], spec["fps"], spec["colors"], spec["team"], spec["year"])
    led = max(2, min(10, 320 // max(lay.w, lay.h))) if spec["layout"]["kind"] == "matrix" else 0
    gifs = export.preview_gifs(clip, led_scale=led)
    return {"tiles": [base64.b64encode(g).decode() for g in gifs], "seconds": clip.seconds, "year": clip.ctx.year}


def _preview_main():
    """The preview helper process: a spec on stdin, the result as JSON on stdout."""
    spec = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    sys.stdout.write(json.dumps(render_preview(spec)))
    sys.stdout.flush()


class Studio:
    def __init__(self, out_dir):
        self.out = Path(out_dir).resolve()       # the job runs from another folder
        self.lock = threading.Lock()
        self._preview_slot = threading.Lock()   # one out-of-process preview at a time
        self._preview_gen = 0                   # long previews asked for so far
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
            keep = {k: j[k] for k in ("scene", "layout", "size", "started", "total", "seconds", "display", "label") if k in j}
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
                "layouts": LAYOUT_NAMES, "sizes": list(SIZES), "frames": list(FRAMES), "matrices": list(MATRICES),
                "palettes": sorted(all_palettes(getattr(ctl, "user_palettes", None) or {})),
                "presets": presets, "team": team_colours(ctl),
                "mp4": bool(ff), "encoder": ff.get("encoder", ""), "job": self.job()}

    # ------------------------------------------------------------ preview
    def preview(self, ctl, b: dict) -> dict:
        args = parse(ctl, b, preview=True)
        lay = args["layout"]
        key = json.dumps({**{k: v for k, v in args.items() if k != "layout"}, "layout": lay.to_dict()}, sort_keys=True,
                         default=list)
        with self.lock:
            hit = self._cache.get(key)
            if hit:
                self._cache.move_to_end(key)
                return hit
        t0 = time.time()
        spec = {"scene": args["scene"], "layout": _layout_spec(lay), "seconds": args["seconds"], "fps": args["fps"],
                "colors": args["colors"], "team": args["team"], "year": args["year"]}
        if args["seconds"] * args["fps"] > PREVIEW_INLINE_FRAMES:
            # a long story preview is several seconds of CPU: make it in a low-priority
            # process (one at a time) so the lights' engine in this server never waits on it.
            # One still waiting when a newer one arrives (a slider dragged) is dropped, so
            # the newest is made next instead of after every earlier one.
            with self.lock:
                self._preview_gen += 1
                gen = self._preview_gen
            with self._preview_slot:
                if not self._newest(gen):
                    return {"ok": True, "superseded": True}
                out = self._preview_proc(spec)
        else:
            out = render_preview(spec)
        res = {"ok": True, "tiles": ["data:image/gif;base64," + g for g in out["tiles"]],
               "display": args["extra"]["display"], "out": list(getattr(lay, "out_size", (lay.size, lay.size))),
               "layout": lay.to_dict(), "seconds": out["seconds"], "colors": args["colors"] or [],
               "team": args["team"] or [], "year": out["year"], "ms": int((time.time() - t0) * 1000)}
        with self.lock:
            self._cache[key] = res
            while len(self._cache) > 12:
                self._cache.popitem(last=False)
        return res

    def _newest(self, gen: int) -> bool:
        with self.lock:
            return gen == self._preview_gen

    def _preview_proc(self, spec: dict) -> dict:
        argv = [sys.executable, "-c", "from goveelights.hologram.studio import _preview_main; _preview_main()"]
        argv, env, flags, extra = _low_priority(argv)
        try:
            proc = subprocess.Popen(argv, cwd=str(_PKG_ROOT), env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, creationflags=flags, **extra)
        except OSError as e:
            log.warning("hologram preview: no helper process (%s); making it here", e)
            return render_preview(spec)
        try:
            out, err = proc.communicate(json.dumps(spec).encode(), timeout=PREVIEW_TIMEOUT)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            raise ValueError("the preview took too long") from None
        if proc.returncode != 0:
            msg = err.decode("utf-8", "replace").strip().splitlines()
            log.warning("hologram preview failed: %s", " | ".join(msg[-3:]))
            raise ValueError("the preview could not be made")
        return json.loads(out.decode("utf-8"))

    # ------------------------------------------------------------ full-size files
    def _clean(self):
        """Clear the previous job's files (the folder only ever holds one set)."""
        if not self.out.is_dir():
            return
        for p in self.out.iterdir():
            if p.is_file() and p.suffix in (".gif", ".mp4", ".zip", ".png", ".json", ".tmp", ".rgb") and export.safe_name(p.name):
                try:
                    p.unlink()
                except OSError:
                    pass

    def start(self, ctl, b: dict) -> dict:
        args = parse(ctl, b, preview=False)
        ex = args["extra"]
        fmts = b.get("formats") or (["gif", "rgb", "view"] if ex["display"] == "matrix" else list(export.DEFAULT_FORMATS))
        if not isinstance(fmts, list) or not all(f in export.FORMATS for f in fmts):
            raise ValueError("formats must be a list of gif, png, mp4 (a matrix also rgb, view)")
        if ex["display"] != "matrix" and any(f in export.MATRIX_ONLY for f in fmts):
            raise ValueError("rgb and view are only for an LED matrix")
        lay = args["layout"]
        with self.lock:
            if self._proc is not None and self._proc.poll() is None:
                raise ValueError("already making files; wait for that to finish (or cancel it)")
            self.out.mkdir(parents=True, exist_ok=True)
            self._clean()
            prog = self.out / "progress.json"
            argv = [sys.executable, "-m", "goveelights", "hologram", args["scene"],
                    "--seconds", f"{args['seconds']:g}", "--fps", f"{args['fps']:g}",
                    "--formats", ",".join(fmts), "--zip-png", "--json", "--out", str(self.out),
                    "--progress-file", str(prog)]
            if ex["display"] == "frame":
                argv += ["--frame", "%dx%d" % ex["dims"], "--background", ex["background"]]
                label, size = "%d × %d picture" % ex["dims"], ex["dims"][1]
            elif ex["display"] == "matrix":
                argv += ["--matrix", "%dx%d" % ex["dims"], "--gamma", f"{ex['gamma']:g}", "--background", ex["background"]]
                if ex["serpentine"]:
                    argv.append("--serpentine")
                label, size = "%d × %d LED matrix" % ex["dims"], ex["dims"][1]
            else:
                argv += ["--layout", lay.name, "--size", str(lay.size),
                         "--diameter", f"{lay.diameter_cm:g}", "--spacing", f"{lay.spacing_cm:g}"]
                label, size = ("1 fan" if lay.name == "1" else lay.name) + f", {lay.size} px", lay.size
            if args["colors"]:
                argv += ["--palette", ",".join(args["colors"])]
            if args["team"]:
                argv += ["--team", ",".join(args["team"])]
            if args["year"]:
                argv += ["--year", str(args["year"])]
            argv, env, flags, extra = _low_priority(argv)
            self._proc = subprocess.Popen(argv, cwd=str(_PKG_ROOT), env=env, stdin=subprocess.DEVNULL,
                                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags, **extra)
            self._job = {"state": "running", "scene": args["scene"], "layout": lay.name, "size": size,
                         "display": ex["display"], "label": label,
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
