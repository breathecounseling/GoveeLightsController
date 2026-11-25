"""Single JSON config file: devices, layout (landmarks), diagnostic results."""
from __future__ import annotations

import copy
import json
import os
import threading
from pathlib import Path

DEFAULT_PATH = Path(os.environ.get("GOVEE_CONFIG", Path(__file__).resolve().parent.parent / "govee_config.json"))

DEFAULTS = {
    "server": {"host": "0.0.0.0", "port": 8080, "bind_ip": ""},
    "manual_ips": [],
    "engine": {"fps": 20, "gamma": 1.0, "refresh_s": 1.0},
    "devices": {},
}

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
                if isinstance(v, dict) and isinstance(self.data.get(k), dict):
                    self.data[k].update(v)
                else:
                    self.data[k] = v
        for mac, d in self.data["devices"].items():
            self.data["devices"][mac] = {**copy.deepcopy(DEVICE_DEFAULTS), **d}

    def save(self):
        with self.lock:
            tmp = self.path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, sort_keys=False)
            os.replace(tmp, self.path)

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
            self.save()
        return changed
