"""Command line.

  python -m goveelights                 creator app (phone/PC web UI)  [same as 'serve']
  python -m goveelights diag            Phase 1 diagnostic tool on its own
  python -m goveelights play <preset>   play without the UI
  python -m goveelights effects | layout | set-password
  python -m goveelights hologram <scene> clips for LED hologram fans (--list, --help)
  python -m goveelights install-service | uninstall-service      (at boot: Windows task / Linux systemd)
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


def not_windows(cmd: str) -> str:
    """What to do instead of a Windows-only command on macOS or Linux."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    py = sys.executable or "python3"
    if sys.platform == "darwin":
        how = (f"On macOS, run it with launchd: copy docs/examples/goveelights.plist to\n"
               f"~/Library/LaunchAgents/, set the paths in it (Python: {py}, folder: {root}),\n"
               f"then: launchctl load ~/Library/LaunchAgents/goveelights.plist")
    else:
        how = ("On Linux (a Raspberry Pi too), start it at boot with:\n"
               "  python3 -m goveelights install-service      (--print shows what it would do)")
    return f"'{cmd}' is for Windows (Task Scheduler / Startup folder).\n{how}\nDetails: docs/SERVICE.md"


def _startup_file():
    appdata = os.environ.get("APPDATA")
    if os.name != "nt" or not appdata:
        raise SystemExit(not_windows("install-startup"))
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
        f'sh.Run """{pyw}"" -m goveelights serve --log ""{log}""", 0, False\r\n',
        encoding="utf-16")      # with a BOM: Windows Script Host reads BOM-less .vbs as ANSI (José → JosÃ©)
    print(f"Installed: {f}\nThe app will start hidden at your next login (log: {log}).\n"
          f"To start it now without a window, double-click that file.")
    return 0


TASK = "GoveeLightsController"
FW_RULES = ("Govee lights (discovery)", "Govee lights (web)")


def firewall_cmds(port: int, add: bool = True) -> list[list[str]]:
    """netsh commands for the boot task's inbound rules. It runs pythonw as SYSTEM, which
    never gets a firewall prompt, so allow the ports (Private networks) instead."""
    base = ["netsh", "advfirewall", "firewall"]
    cmds = [base + ["delete", "rule", f"name={n}"] for n in FW_RULES]
    if add:
        for n, proto, p in zip(FW_RULES, ("UDP", "TCP"), (4002, port)):
            cmds.append(base + ["add", "rule", f"name={n}", "dir=in", "action=allow",
                                f"protocol={proto}", f"localport={p}", "profile=private"])
    return cmds


UNIT = "goveelights"
UNIT_PATH = "/etc/systemd/system/goveelights.service"


def _sd_quote(v: str) -> str:
    """One ExecStart word for systemd: quoted if it has a space or a quote in it."""
    v = str(v)
    if not v or any(c in v for c in " \t\"'\\"):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return v


def systemd_unit(user: str, group: str, root, python: str, extra_args=()) -> str:
    """The systemd unit 'install-service' writes on Linux: a system service (starts at
    boot, nobody needs to log in) running as `user` in the project folder, restarted
    whenever it stops. Its output goes to the journal (journalctl -u goveelights)."""
    cmd = " ".join(_sd_quote(w) for w in [python, "-m", "goveelights", "serve", *extra_args])
    return f"""# Written by 'python -m goveelights install-service'. Remove it with
# 'python -m goveelights uninstall-service'. Logs: journalctl -u {UNIT} -f
[Unit]
Description=Govee lights controller (effects and schedule for Govee lights)
Wants=network-online.target
After=network-online.target time-sync.target
StartLimitIntervalSec=0

[Service]
Type=simple
User={user}
Group={group}
WorkingDirectory={root}
Environment=PYTHONUNBUFFERED=1
ExecStart={cmd}
Restart=always
RestartSec=10
# SIGTERM is the app's Ctrl+C: it hands the lights back to the Govee app, then exits
KillSignal=SIGTERM
TimeoutStopSec=15
# a little ahead of other programs, so the 20 frames a second stay smooth on a Pi
Nice=-5

[Install]
WantedBy=multi-user.target
"""


def _service_user(explicit: str | None, euid: int) -> str:
    """Who the service runs as: --user, else whoever ran sudo, else you."""
    if explicit:
        return explicit
    if euid == 0 and os.environ.get("SUDO_USER") and os.environ["SUDO_USER"] != "root":
        return os.environ["SUDO_USER"]
    import getpass
    return getpass.getuser()


def _primary_group(user: str) -> str:
    try:
        import grp
        import pwd
        return grp.getgrgid(pwd.getpwnam(user).pw_gid).gr_name
    except (ImportError, KeyError):
        return user


def linux_service_plan(uninstall: bool, user: str, root, python: str, euid: int) -> tuple:
    """(unit text or None, the commands to run) for install/uninstall-service on Linux.
    Commands that need root get 'sudo' in front unless we are root already."""
    sudo = [] if euid == 0 else ["sudo"]
    if uninstall:
        return None, [sudo + ["systemctl", "disable", "--now", UNIT],
                      sudo + ["rm", "-f", UNIT_PATH],
                      sudo + ["systemctl", "daemon-reload"]]
    unit = systemd_unit(user, _primary_group(user), root, python)
    return unit, [sudo + ["install", "-m", "0644", UNIT_FILE_SLOT, UNIT_PATH],
                  sudo + ["systemctl", "daemon-reload"],
                  sudo + ["systemctl", "enable", UNIT],
                  sudo + ["systemctl", "restart", UNIT]]


UNIT_FILE_SLOT = "<unit file>"


def _has_systemd() -> bool:
    from pathlib import Path
    return Path("/run/systemd/system").is_dir()


def linux_service(uninstall: bool, argv=(), run=None, euid: int | None = None, write=print) -> int:
    """Linux: install (or remove) the systemd service. --print shows the unit and the
    commands without changing anything; --user NAME runs it as someone else."""
    import argparse
    import shlex
    import subprocess
    import tempfile
    from pathlib import Path
    ap = argparse.ArgumentParser(prog=f"python -m goveelights {'un' if uninstall else ''}install-service")
    ap.add_argument("--print", "--dry-run", dest="dry", action="store_true",
                    help="show the unit file and the commands, change nothing")
    ap.add_argument("--user", help="the user the service runs as (default: you)")
    a = ap.parse_args(list(argv))
    run = run or subprocess.run
    euid = os.geteuid() if euid is None else euid
    root = Path(__file__).resolve().parent.parent
    python = sys.executable or "/usr/bin/python3"
    user = _service_user(a.user, euid)
    as_root = user == "root" and not a.user and not uninstall
    if as_root and not a.dry:
        raise SystemExit("Run this as your normal user (not as root): the service runs as whoever installs it,\n"
                         "and your settings files belong to you. Or pass --user NAME.")
    unit, cmds = linux_service_plan(uninstall, user, root, python, euid)

    def show(c):
        return " ".join(shlex.quote(w) for w in c)
    if a.dry:
        if as_root:
            write("Note: run as root this would install the service as root, which is refused without --print.\n"
                  "Run it as your normal user instead (or pass --user NAME).")
        if unit:
            write(f"--- {UNIT_PATH} (not written: --print) ---\n{unit}--- end ---")
        write("Would run:\n" + "\n".join("  " + show(c) for c in cmds))
        return 0
    if not _has_systemd():
        raise SystemExit("This system doesn't run systemd, so there is no service to "
                         f"{'remove' if uninstall else 'install'}.\n"
                         "Start the app by hand instead: ./start.sh   (details: docs/SERVICE.md)")
    tmp = None
    try:
        if unit:
            fd, tmp = tempfile.mkstemp(prefix="goveelights.", suffix=".service")
            with open(fd, "w", encoding="utf-8") as fh:
                fh.write(unit)
            cmds = [[tmp if w == UNIT_FILE_SLOT else w for w in c] for c in cmds]
        for c in cmds:
            write("+ " + show(c))
            rc = getattr(run(c), "returncode", 0)
            if rc and not (uninstall and "disable" in c):     # not installed: carry on removing
                raise SystemExit(f"That failed (exit {rc}). Nothing after it was run.")
    finally:
        if tmp:
            Path(tmp).unlink(missing_ok=True)
    if uninstall:
        write(f"Removed the '{UNIT}' service: the app was stopped and no longer starts at boot.")
        return 0
    port = 8080
    try:
        port = int(Config().data["server"].get("port", 8080))
    except Exception:  # noqa: BLE001 - only for the message
        pass
    from .diag_server import lan_ips
    urls = "\n".join(f"  http://{ip}:{port}/" for ip in lan_ips())
    write(f"""
Installed the '{UNIT}' service ({UNIT_PATH}):
  runs as {user}, in {root}
  with {python} -m goveelights serve
  starts at boot (no login needed), and again 10 s after it stops for any reason.
It is running now. Open it from a phone or computer at home:
{urls}

  See what it's doing:        journalctl -u {UNIT} -f     (Ctrl+C stops watching, not the app)
  Today's log:                journalctl -u {UNIT} --since today
  Is it running?              systemctl status {UNIT}
  Restart (after an update):  sudo systemctl restart {UNIT}
  Stop until the next boot:   sudo systemctl stop {UNIT}
  Remove it:                  python3 -m goveelights uninstall-service
The schedule uses this computer's clock and time zone: check them with 'timedatectl'.""")
    return 0


def cmd_service(uninstall=False, argv=()):
    """Windows: run at boot (before anyone logs in) via Task Scheduler, restart on failure.
    Needs an administrator PowerShell. Linux: a systemd service (linux_service)."""
    import subprocess
    from pathlib import Path
    if os.name != "nt":
        if sys.platform.startswith("linux"):
            return linux_service(uninstall, argv)
        raise SystemExit(not_windows("install-service" if not uninstall else "uninstall-service"))
    if uninstall:
        r = subprocess.run(["schtasks", "/Delete", "/TN", TASK, "/F"], capture_output=True, text=True)
        print(r.stdout or r.stderr)
        for c in firewall_cmds(0, add=False):
            subprocess.run(c, capture_output=True, text=True)
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
    from .config import Config
    port = int(Config().data["server"].get("port", 8080))
    fw_ok = True
    for c in firewall_cmds(port):              # delete (so a re-install doesn't pile up rules), then add
        rc = subprocess.run(c, capture_output=True, text=True).returncode
        fw_ok = fw_ok and (rc == 0 or "delete" in c)
    print("Firewall: allowed UDP 4002 and TCP %d in on Private networks." % port if fw_ok else
          "Firewall rules could not be added; see README (Phone page never loads).")
    print(f"Installed scheduled task '{TASK}': starts 30 s after boot, no login needed, restarts if it stops.\n"
          f"Start it now: schtasks /Run /TN {TASK}    Log: {root / 'goveelights.log'}\n"
          f"Remove the login-time launcher if you installed it: python -m goveelights uninstall-startup")
    return 0


SUBCOMMANDS = ("play", "effects", "layout")
COMMANDS_HELP = """commands (python -m goveelights <command> --help for each):
  serve (the default)        the app: phone page, schedule, shows
  play <preset|effect>       play a preset or effect from the command line
  effects [-v]               list the effects
  layout [import <file>]     show the virtual strip, or import a layout file
  diag                       the diagnostics page on its own
  set-password               allow remote access with a password
  install-service / uninstall-service   start at boot (Windows task, Linux systemd; --print)
  install-startup / uninstall-startup   start at login (Windows)
Options such as --config may come before or after the command."""


def _leading_options_then_subcommand(argv) -> bool:
    """`--config X layout import F`: options first, then one of SUBCOMMANDS."""
    k = 0
    while k < len(argv) and argv[k].startswith("-"):
        k += 1 if argv[k] in ("-v", "--verbose") or "=" in argv[k] else 2
    return k < len(argv) and argv[k] in SUBCOMMANDS


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or (argv[0].startswith("-") and not _leading_options_then_subcommand(argv)) or argv[0] == "serve":
        from .app_server import main as app_main
        return app_main(argv[1:] if argv and argv[0] == "serve" else argv)
    if argv[0] == "hologram":
        from .hologram.cli import main as holo_main
        return holo_main(argv[1:])
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
        return cmd_service(uninstall=argv[0] == "uninstall-service", argv=argv[1:])
    if argv[0] in ("install-startup", "uninstall-startup"):
        return cmd_install_startup(uninstall=argv[0] == "uninstall-startup")

    ap = argparse.ArgumentParser(prog="python -m goveelights", epilog=COMMANDS_HELP,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config")
    ap.add_argument("-v", "--verbose", action="store_true")
    common = argparse.ArgumentParser(add_help=False)          # the same options after the command
    common.add_argument("--config", default=argparse.SUPPRESS, help="config file (default govee_config.json)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pp = sub.add_parser("play", help="play a preset or effect on the lights", parents=[common])
    pp.add_argument("preset", help="preset file, preset name from presets/, or effect name")
    pp.add_argument("--set", action="append", metavar="KEY=VALUE",
                    help="override a param, e.g. --set speed=5 --set colors=#ff0000,#00ff00 (N.key for layer N)")
    pp.add_argument("--fps", type=float)
    pp.add_argument("--brightness", type=float)
    pp.add_argument("--seconds", type=float, help="stop after this long")
    pp.add_argument("--terminal", action="store_true", help="show a live colour preview in the terminal")
    pp.add_argument("--dry-run", action="store_true", help="don't touch the lights (use with --terminal)")
    pp.add_argument("--bind-ip")
    pe = sub.add_parser("effects", help="list effects (-v for params)", parents=[common])
    pe.add_argument("-v", "--verbose", action="store_true")
    pl = sub.add_parser("layout", help="show the virtual strip, or import a layout file", parents=[common])
    pl.add_argument("action", nargs="?", default="show", choices=["show", "import"])
    pl.add_argument("file", nargs="?")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    return {"play": cmd_play, "effects": cmd_effects, "layout": cmd_layout}[a.cmd](a)
