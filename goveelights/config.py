"""Single JSON config file: devices, layout (landmarks), diagnostic results."""
from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
import time
from pathlib import Path

DEFAULT_PATH = Path(os.environ.get("GOVEE_CONFIG", Path(__file__).resolve().parent.parent / "govee_config.json"))

def write_json(path: Path, data, mode: int = 0o644, retries: int = 10, **dump_kw):
    """Write JSON atomically. A unique temp file, so a stale one held open elsewhere can't
    block saves; and on Windows os.replace fails with PermissionError while a virus
    scanner, OneDrive or an editor briefly has either file open, so retry a little."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with open(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, **dump_kw)
        os.chmod(tmp, mode)                     # mkstemp makes it owner-only
        for i in range(retries):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if i == retries - 1:
                    raise
                time.sleep(0.05)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


DEFAULTS = {
    "server": {"host": "0.0.0.0", "port": 8080, "bind_ip": ""},
    "manual_ips": [],
    "engine": {"fps": 20, "gamma": 1.0, "refresh_s": 1.0, "brightness": 1.0},
    "master": {"speed": 1.0, "hue": 0.0, "palette": "", "bpm": 120},
    "palettes": {},
    "events": None,                                  # None -> built-in New Year / Christmas
    "game": {"enabled": False, "teams": [{"league": "nfl", "team": "DEN"}], "delay": 30,
             "when_lights_off": False,
             "scoreboard_every": 0, "scoreboard_seconds": 20},   # minutes between in-game scoreboards (0 = off)
    "weather": {"enabled": False, "snow": True, "rain": False, "storm": True, "wind": True},
    "shows": {
        "Christmas show": {"end": "resume", "cues": [
            {"preset": "festive-glitter", "seconds": 25, "transition": "flash", "fade": 1.0},
            {"preset": "candy-cane", "seconds": 20, "transition": "wipe", "fade": 2.0},
            {"preset": "fill-the-peak", "seconds": 20, "transition": "rise", "fade": 2.0},
            {"preset": "breathing-comets", "seconds": 25, "transition": "crossfade", "fade": 2.0},
            {"preset": "fireworks", "seconds": 20, "transition": "black", "fade": 1.5}]},
    },
    "showtimes": [],
    "visitor": {"enabled": False, "presets": ["festive-glitter", "candy-cane", "fireworks", "winter-sparkle",
                                             "rainbow-roofline", "glitter-ball"],
                "seconds": 60, "cooldown": 90, "max_queue": 5, "sparkle": True, "only_when_on": True,
                "title": "Pick the lights!", "message": "Happy holidays!"},
    "zones": [],
    "playlists": {
        "Christmas": {"items": [{"preset": "candy-cane", "minutes": 5},
                                {"preset": "christmas-twinkle", "minutes": 10},
                                {"preset": "comets", "minutes": 3}], "fade": 3, "shuffle": False},
    },
    "schedule": {
        "enabled": False, "lat": 40.3764, "lon": -104.6922,
        "on": "sunset", "on_offset_min": 0, "on_time": "17:00", "off_time": "23:00",
        "dusk_intro_minutes": 0,                     # play the dusk sky this long at lights-on (0 = off)
        "default": {"preset": "warm-white"},
        "rules": [
            {"name": "Christmas", "from": "11-25", "to": "01-06", "program": {"playlist": "Christmas"}},
            {"name": "Halloween", "from": "10-01", "to": "10-31", "program": {"preset": "halloween"}},
        ],
    },
    "devices": {},
}

REPLACE = {"playlists", "shows", "palettes", "devices"}

DEVICE_DEFAULTS = {
    "name": "",
    "ip": "",
    "sku": "",
    "firmware": {},
    "reported_ip": None,       # set if the scan reply's "ip" differs from its source
    "estimated_count": 72,     # 30 m / 0.417 m spacing
    "pixel_count": None,       # confirmed in Phase 1
    "reversed": False,
    "order": 0,
    "landmarks": [],           # [{"index": int, "label": str}]
    "hidden": [],              # bulb indexes that are not visible
    "geometry": [],            # [[index, x, y], ...] knots, see layout.py
    "enabled": True,
    "diag": {},                # Phase 1 test results / observations
}


class Config:
    def __init__(self, path: Path | str = DEFAULT_PATH):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.data = copy.deepcopy(DEFAULTS)
        if self.path.exists():
            with open(self.path, encoding="utf-8") as f:
                loaded = json.load(f)
            for k, v in loaded.items():
                # collections the user edits as a whole replace the defaults (so deletions stick);
                # settings blocks are merged so new options get their defaults
                if isinstance(v, dict) and isinstance(self.data.get(k), dict) and k not in REPLACE:
                    self.data[k].update(v)
                else:
                    self.data[k] = v
        for mac, d in self.data["devices"].items():
            self.data["devices"][mac] = {**copy.deepcopy(DEVICE_DEFAULTS), **d}

    def save(self):
        with self.lock:
            write_json(self.path, self.data, indent=2, sort_keys=False)

    @property
    def devices(self) -> dict:
        return self.data["devices"]

    def device(self, mac: str) -> dict:
        try:
            return self.devices[mac]
        except KeyError:
            raise KeyError(f"unknown device {mac}") from None

    def upsert_discovered(self, info: dict) -> bool:
        """Merge a scan reply. Returns True if the device is new."""
        mac = info["device"]
        with self.lock:
            new = mac not in self.devices
            before = copy.deepcopy(self.devices.get(mac))
            d = self.devices.setdefault(mac, copy.deepcopy(DEVICE_DEFAULTS))
            if new:
                d["name"] = f"{info.get('sku', 'Govee')} {mac.replace(':', '')[-4:]}"
                d["order"] = len(self.devices) - 1
            # Send to the address the reply actually came from. The "ip" field the
            # device reports can be stale; keep it for diagnosis when it differs.
            src, reported = info.get("source_ip"), info.get("ip")
            d["ip"] = src or reported or d["ip"]
            d["reported_ip"] = reported if reported and reported != d["ip"] else None
            d["sku"] = info.get("sku", d["sku"])
            d["firmware"] = {k: info[k] for k in
                             ("bleVersionHard", "bleVersionSoft", "wifiVersionHard", "wifiVersionSoft")
                             if k in info}
            if new or d != before:              # every scan answers again: skip identical saves
                self.save()
            return new

    def import_layout(self, path) -> list[str]:
        """Merge a layout file ({"devices": {mac: {...}}}) into this config."""
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        changed = []
        with self.lock:
            for mac, entry in data.get("devices", {}).items():
                d = self.devices.setdefault(mac, copy.deepcopy(DEVICE_DEFAULTS))
                for k in ("name", "order", "reversed", "hidden", "pixel_count", "geometry", "enabled"):
                    if k in entry and not (k == "name" and d.get("name")):
                        d[k] = entry[k]
                changed.append(d.get("name") or mac)
            if data.get("zones"):
                new = {z["name"]: z for z in data["zones"]}
                self.data["zones"] = [z for z in self.data.get("zones", []) if z.get("name") not in new] + list(new.values())
                changed.append(f"{len(new)} zones")
            self.save()
        return changed
