"""Command line.

  python -m goveelights                 creator app (phone/PC web UI)  [same as 'serve']
  python -m goveelights diag            Phase 1 diagnostic tool on its own
  python -m goveelights play <preset>   play without the UI
  python -m goveelights effects | layout | set-password
  python -m goveelights install-service | uninstall-service      (Windows, at boot, admin)
  python -m goveelights install-startup | uninstall-startup      (Windows, at login)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading
import time

from .config import Config
from .effects import EFFECTS
from .engine import Engine, to_device_frames
from .layout import Layout
from .output import GoveeRazerOutput, MemoryOutput
from .scene import Scene

log = logging.getLogger("goveelights")


def parse_value(v: str):
    try:
        return json.loads(v)
    except ValueError:
        if "," in v:
            return [x.strip() for x in v.split(",")]
        return v


def load_scene(what: str, sets: list[str]) -> Scene:
    if os.path.exists(what):
        scene = Scene.load(what)
    elif os.path.exists(os.path.join("presets", what + ".json")):
        scene = Scene.load(os.path.join("presets", what + ".json"))
    elif what in EFFECTS:
        scene = Scene.single(what)
    else:
        raise SystemExit(f"'{what}' is not a preset file, a preset name in presets/, or an effect "
                         f"({', '.join(sorted(EFFECTS))})")
    if sets:
        # --set applies to layer 0, or "N.key=value" for layer N
        d = scene.to_dict()
        for s in sets:
            k, _, v = s.partition("=")
            li, _, key = k.rpartition(".")
            d["layers"][int(li) if li else 0]["params"][key] = parse_value(v)
        scene = Scene.from_dict(d)
    return scene


def ansi_line(frame) -> str:
    return "".join(f"\x1b[48;2;{r};{g};{b}m " for r, g, b in frame) + "\x1b[0m"


def cmd_play(a):
    cfg = Config(a.config) if a.config else Config()
    scene = load_scene(a.preset, a.set or [])
    if a.brightness is not None:
        scene.brightness = a.brightness
    eng_cfg = cfg.data.get("engine", {})
    layout = Layout.from_config(cfg)
    if not layout.runs:
        raise SystemExit("No runs with a confirmed pixel_count in the config. Run the diagnostic tool first.")
    if a.dry_run:
        out = MemoryOutput()
    else:
        from .lan import GoveeLan
        lan = GoveeLan(a.bind_ip or cfg.data["server"].get("bind_ip", ""), on_device=cfg.upsert_discovered)
        lan.scan(list(cfg.data.get("manual_ips", [])) + [d["ip"] for d in cfg.devices.values() if d.get("ip")])
        time.sleep(1.5)          # let scan replies refresh IPs (DHCP may have changed them)
        devs = {r.mac: cfg.devices[r.mac] for r in layout.runs}
        missing = [cfg.devices[m]["name"] for m, d in devs.items() if not d.get("ip")]
        if missing:
            raise SystemExit(f"No IP known for: {', '.join(missing)}")
        out = GoveeRazerOutput(lan, devs, refresh_s=float(eng_cfg.get("refresh_s", 1.0)))
    eng = Engine(layout, out, fps=a.fps or float(eng_cfg.get("fps", 20)), gamma=float(eng_cfg.get("gamma", 1.0)))
    eng.set_scene(scene)
    print(f"Playing '{scene.name}' at {eng.fps:g} fps on {', '.join(r.name for r in layout.runs)}"
          f"{' (dry run, no lights)' if a.dry_run else ''}. Ctrl+C to stop and release.", flush=True)
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, lambda *_: stop.set())
    if a.terminal and os.name == "nt":
        os.system("")   # enable ANSI colours in the Windows console
    eng.start()
    t_end = time.time() + a.seconds if a.seconds else None
    try:
        while not stop.is_set() and (t_end is None or time.time() < t_end):
            if a.terminal:
                frames = to_device_frames(layout, eng.last_colors or [(0, 0, 0)] * layout.n)
                lines = [f"{r.name[:8]:<8} {ansi_line(frames[r.mac])}" for r in layout.runs]
                sys.stdout.write("\r" + "\n".join(lines) + f"\n render {eng.stats['render_ms']} ms\x1b[{len(lines)}A")
                sys.stdout.flush()
            stop.wait(0.1)
    finally:
        if a.terminal:
            sys.stdout.write("\n" * (len(layout.runs) + 1))
        print("stopping, releasing lights to the Govee app...")
        eng.stop(release=True)
    return 0


def cmd_effects(a):
    for e in EFFECTS.values():
        print(f"{e.name:<12} {e.label} - {e.description}")
        if a.verbose:
            for p in e.params:
                print(f"    {p.name:<12} {p.kind:<7} default={p.default!r}  {p.help}")
    return 0


def cmd_layout(a):
    cfg = Config(a.config) if a.config else Config()
    if a.action == "import":
        if not a.file:
            raise SystemExit("usage: layout import <file>")
        print("updated:", ", ".join(cfg.import_layout(a.file)))
    print(Layout.from_config(cfg).summary())
    return 0


STARTUP_NAME = "GoveeLights.vbs"


def _startup_file():
    appdata = os.environ.get("APPDATA")
    if os.name != "nt" or not appdata:
        raise SystemExit("install-startup is for Windows. Elsewhere, start 'python -m goveelights' from your login scripts.")
    from pathlib import Path
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / STARTUP_NAME


def cmd_install_startup(uninstall=False):
    """Start the app silently (pythonw, no console) when this user logs in."""
    from pathlib import Path
    f = _startup_file()
    if uninstall:
        if f.exists():
            f.unlink()
            print(f"Removed {f}")
        else:
            print("Not installed.")
        return 0
    root = Path(__file__).resolve().parent.parent
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe") if exe.with_name("pythonw.exe").exists() else exe
    log = root / "goveelights.log"
    f.write_text(
        'Set sh = CreateObject("WScript.Shell")\r\n'
        f'sh.CurrentDirectory = "{root}"\r\n'
        f'sh.Run """{pyw}"" -m goveelights serve --log ""{log}""", 0, False\r\n', encoding="utf-8")
    print(f"Installed: {f}\nThe app will start hidden at your next login (log: {log}).\n"
          f"To start it now without a window, double-click that file.")
    return 0


TASK = "GoveeLightsController"


def cmd_service(uninstall=False):
    """Windows: run at boot (before anyone logs in) via Task Scheduler, restart on failure.
    Needs an administrator PowerShell."""
    import subprocess
    from pathlib import Path
    if os.name != "nt":
        raise SystemExit("install-service is for Windows. On Linux use a systemd unit running 'python -m goveelights'.")
    if uninstall:
        r = subprocess.run(["schtasks", "/Delete", "/TN", TASK, "/F"], capture_output=True, text=True)
        print(r.stdout or r.stderr)
        return r.returncode
    root = Path(__file__).resolve().parent.parent
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe") if exe.with_name("pythonw.exe").exists() else exe
    xml = f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers><BootTrigger><Delay>PT30S</Delay><Enabled>true</Enabled></BootTrigger></Triggers>
  <Principals><Principal id="Author"><UserId>S-1-5-18</UserId><RunLevel>HighestAvailable</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure>
    <StartWhenAvailable>true</StartWhenAvailable><Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author"><Exec><Command>{pyw}</Command><Arguments>"{root / 'run.pyw'}"</Arguments>
    <WorkingDirectory>{root}</WorkingDirectory></Exec></Actions>
</Task>"""
    tmp = root / "govee_task.xml"
    tmp.write_text(xml, encoding="utf-16")
    try:
        r = subprocess.run(["schtasks", "/Create", "/TN", TASK, "/XML", str(tmp), "/F"], capture_output=True, text=True)
    finally:
        tmp.unlink(missing_ok=True)
    if r.returncode:
        raise SystemExit((r.stderr or r.stdout).strip() + "\n(Run this in an administrator PowerShell.)")
    print(f"Installed scheduled task '{TASK}': starts 30 s after boot, no login needed, restarts if it stops.\n"
          f"Start it now: schtasks /Run /TN {TASK}    Log: {root / 'goveelights.log'}\n"
          f"Remove the login-time launcher if you installed it: python -m goveelights uninstall-startup")
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0].startswith("-") or argv[0] == "serve":
        from .app_server import main as app_main
        return app_main(argv[1:] if argv and argv[0] == "serve" else argv)
    if argv[0] == "diag":
        from .diag_server import main as diag_main
        return diag_main(argv[1:])
    if argv[0] == "set-password":
        import getpass
        from pathlib import Path
        from .auth import Auth
        from .config import DEFAULT_PATH
        a = Auth(Path(os.environ.get("GOVEE_AUTH", Path(DEFAULT_PATH).with_name("govee_auth.json"))))
        pw = getpass.getpass("New password (10+ characters): ")
        if pw != getpass.getpass("Again: "):
            raise SystemExit("Passwords don't match.")
        a.set_password(pw)
        print(f"Password set ({a.path}). Remote access is now allowed with this password; all devices were signed out.")
        return 0
    if argv[0] in ("install-service", "uninstall-service"):
        return cmd_service(uninstall=argv[0] == "uninstall-service")
    if argv[0] in ("install-startup", "uninstall-startup"):
        return cmd_install_startup(uninstall=argv[0] == "uninstall-startup")

    ap = argparse.ArgumentParser(prog="python -m goveelights")
    ap.add_argument("--config")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pp = sub.add_parser("play", help="play a preset or effect on the lights")
    pp.add_argument("preset", help="preset file, preset name from presets/, or effect name")
    pp.add_argument("--set", action="append", metavar="KEY=VALUE",
                    help="override a param, e.g. --set speed=5 --set colors=#ff0000,#00ff00 (N.key for layer N)")
    pp.add_argument("--fps", type=float)
    pp.add_argument("--brightness", type=float)
    pp.add_argument("--seconds", type=float, help="stop after this long")
    pp.add_argument("--terminal", action="store_true", help="show a live colour preview in the terminal")
    pp.add_argument("--dry-run", action="store_true", help="don't touch the lights (use with --terminal)")
    pp.add_argument("--bind-ip")
    pe = sub.add_parser("effects", help="list effects (-v for params)")
    pe.add_argument("-v", "--verbose", action="store_true")
    pl = sub.add_parser("layout", help="show the virtual strip, or import a layout file")
    pl.add_argument("action", nargs="?", default="show", choices=["show", "import"])
    pl.add_argument("file", nargs="?")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    return {"play": cmd_play, "effects": cmd_effects, "layout": cmd_layout}[a.cmd](a)
