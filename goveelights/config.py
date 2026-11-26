"""Single JSON config file: devices, layout (landmarks), diagnostic results."""
from __future__ import annotations

import copy
import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)
HOUSE_LAYOUT = Path(__file__).resolve().parent.parent / "layouts" / "house.json"

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
    "game": {"enabled": False, "teams": [], "delay": 30,      # teams: [{"league": "nfl", "team": "KC"}]
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
                "seconds": 60, "cooldown": 90, "max_queue": 5, "sparkle": True,
                "title": "Pick the lights!", "message": "Happy holidays!"},
    "zones": [],
    # what a preset's layer target means on this house when the layout has no run or zone
    # of that name: {"Peak": "Gable", "porch": "Front door", "*": "all"} (a name, a list of
    # names, or a role: peak, roofline, porch, accent; "*" = anything else). See layout.py.
    "target_aliases": {},
    "playlists": {
        "Christmas": {"items": [{"preset": "candy-cane", "minutes": 5},
                                {"preset": "christmas-twinkle", "minutes": 10},
                                {"preset": "comets", "minutes": 3}], "fade": 3, "shuffle": False},
    },
    "schedule": {
        "enabled": False, "lat": None, "lon": None,  # None: not set yet (first-run setup asks)
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

REPLACE = {"playlists", "shows", "palettes", "devices", "target_aliases"}

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


def migrate_legacy_defaults(loaded: dict, legacy: dict | None = None) -> list[str]:
    """An existing config that lacks a key whose built-in default has since become neutral
    gets the old default written in (see legacy_defaults.py), so that house keeps behaving
    exactly as before. Changes `loaded` in place; returns the dotted keys it filled."""
    if legacy is None:
        from .legacy_defaults import LEGACY_DEFAULTS as legacy
    done = []
    if not isinstance(loaded, dict):
        return done
    for dotted, value in legacy.items():
        if value is None:
            continue
        *parents, key = dotted.split(".")
        node = loaded
        for part in parents:
            node = node.setdefault(part, {}) if isinstance(node, dict) else None
        if isinstance(node, dict) and key not in node:
            node[key] = copy.deepcopy(value)
            done.append(dotted)
    return done


class Config:
    def __init__(self, path: Path | str = DEFAULT_PATH):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.data = copy.deepcopy(DEFAULTS)
        self.migrated: list[str] = []
        if self.path.exists():
            with open(self.path, encoding="utf-8") as f:
                loaded = json.load(f)
            self.migrated = migrate_legacy_defaults(loaded)
            if self.migrated:              # written once: the file now holds what it relied on
                try:
                    write_json(self.path, loaded, indent=2, sort_keys=False)
                    log.info("config: kept the earlier built-in defaults for %s", ", ".join(self.migrated))
                except OSError as e:
                    log.warning("config: could not save the earlier defaults (%s); using them anyway", e)
            for k, v in loaded.items():
                # collections the user edits as a whole replace the defaults (so deletions stick);
                # settings blocks are merged so new options get their defaults
                if isinstance(v, dict) and isinstance(self.data.get(k), dict) and k not in REPLACE:
                    self.data[k].update(v)
                else:
                    self.data[k] = v
        for mac, d in self.data["devices"].items():
            self.data["devices"][mac] = {**copy.deepcopy(DEVICE_DEFAULTS), **d}
        v = self.data.get("visitor")
        if isinstance(v, dict) and "only_when_on" in v:         # retired: picks only play while the lights are on
            if v.pop("only_when_on") is False:
                log.info("visitor 'only_when_on: false' is no longer supported: visitor picks "
                         "only play while the lights are on")

    @staticmethod
    def auto_name(mac: str, d: dict) -> bool:
        """True if a box still has the placeholder name discovery gave it ("H805A 2E80")."""
        return d.get("name", "") == f"{d.get('sku') or 'Govee'} {mac.replace(':', '')[-4:]}"

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

    def _match_layout(self, lay: dict, create_missing: bool) -> tuple[dict, list[str]]:
        """{layout id: config id} for a layout file's boxes, plus notes. A layout id this
        config knows maps to itself. Otherwise (an example layout, or a layout from another
        house) it goes to one of your boxes the layout doesn't mention: one with the same
        run name first, then by order. A box is never added for an id that wasn't
        discovered, unless create_missing (tests and tools that build a config from a file)."""
        target = {m: m for m in lay if m in self.devices or create_missing}
        notes = []
        free = [m for m, _ in sorted(self.devices.items(), key=lambda kv: kv[1].get("order", 0)) if m not in lay]
        left = sorted((m for m in lay if m not in target), key=lambda m: lay[m].get("order", 0))
        for m in list(left):
            nm = lay[m].get("name")
            hit = next((f for f in free if nm and self.devices[f].get("name") == nm), None)
            if hit:
                target[m] = hit
                free.remove(hit)
                left.remove(m)
        for m in left:
            nm = lay[m].get("name") or m
            if free:
                target[m] = free.pop(0)
                d = self.devices[target[m]]
                notes.append(f"layout run '{nm}' -> your box {d.get('name') or target[m]}")
            else:
                notes.append(f"skipped layout run '{nm}' (no discovered box left for it)")
        return target, notes

    def import_layout(self, path, create_missing: bool = False) -> list[str]:
        """Merge a layout file ({"devices": {id: {...}}, "zones": [...]}) into this config.
        Boxes are matched by id; a layout made for other boxes (layouts/examples/) is
        mapped onto yours by run name, then by order (see _match_layout)."""
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        lay = {m: e for m, e in (data.get("devices") or {}).items() if isinstance(e, dict)}
        changed = []
        with self.lock:
            target, notes = self._match_layout(lay, create_missing)
            if any("geometry" in lay[m] for m in target):
                for d in self.devices.values():      # an older map undo must not undo the import
                    d.pop("geometry_prev", None)
            ren = {}
            for src, mac in target.items():
                entry = lay[src]
                d = self.devices.setdefault(mac, copy.deepcopy(DEVICE_DEFAULTS))
                for k in ("name", "order", "reversed", "hidden", "pixel_count", "geometry", "enabled"):
                    if k not in entry or (k == "name" and d.get("name") and not self.auto_name(mac, d)):
                        continue
                    if k == "pixel_count" and src != mac and d.get("pixel_count"):
                        continue                     # another house's box: your bulb count stays
                    d[k] = entry[k]
                if src != mac and entry.get("name") and d.get("name") != entry["name"]:
                    ren[entry["name"]] = d.get("name")
                changed.append(d.get("name") or mac)
            if data.get("zones"):
                new = {z["name"]: copy.deepcopy(z) for z in data["zones"]}
                for z in new.values():
                    for part in z.get("parts", []):
                        part["run"] = ren.get(part.get("run"), part.get("run"))    # your name for that box
                self.data["zones"] = [z for z in self.data.get("zones", []) if z.get("name") not in new] + list(new.values())
                changed.append(f"{len(new)} zones")
            self.save()
        return changed + notes

    def merge_house_layout(self, path=HOUSE_LAYOUT) -> list[str]:
        """Bring in what a layout file gained since it was imported: zones whose names this
        config doesn't have (never touching yours), and the layout's run names for boxes
        still carrying discovery's placeholder name. Only when the boxes match by MAC."""
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return []
        lay = {m: e for m, e in (data.get("devices") or {}).items() if isinstance(e, dict)}
        changed = []
        with self.lock:
            if not lay or not set(lay) <= set(self.devices):
                return []
            for mac, e in lay.items():
                d = self.devices[mac]
                if e.get("name") and self.auto_name(mac, d):
                    d["name"] = e["name"]
                    changed.append(e["name"])
            ren = {e.get("name"): self.devices[m].get("name") for m, e in lay.items() if e.get("name")}
            zones = self.data.get("zones")
            zones = zones if isinstance(zones, list) else []
            have = {z.get("name") for z in zones if isinstance(z, dict)}
            add = []
            for z in data.get("zones") or []:
                if not isinstance(z, dict) or not z.get("name") or z["name"] in have:
                    continue
                z = copy.deepcopy(z)
                for part in z.get("parts", []):
                    part["run"] = ren.get(part.get("run"), part.get("run"))   # your name for that box
                add.append(z)
            if add:
                self.data["zones"] = zones + add
                changed.append(f"{len(add)} zones")
            if changed:
                log.info("house layout: added %s", ", ".join(changed))
                self.save()
        return changed
