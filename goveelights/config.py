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
    "devices": {},
}

DEVICE_DEFAULTS = {
    "name": "",
    "ip": "",
    "sku": "",
    "firmware": {},
    "estimated_count": 72,     # 30 m / 0.417 m spacing
    "pixel_count": None,       # confirmed in Phase 1
    "reversed": False,
    "order": 0,
    "landmarks": [],           # [{"index": int, "label": str}]
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
            d["ip"] = info.get("ip", d["ip"])
            d["sku"] = info.get("sku", d["sku"])
            d["firmware"] = {k: info[k] for k in
                             ("bleVersionHard", "bleVersionSoft", "wifiVersionHard", "wifiVersionSoft")
                             if k in info}
            self.save()
            return new
