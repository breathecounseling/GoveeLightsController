"""Add a run to a house that already has a show, without disturbing it.

* add_run(): a box (a discovered one that has no bulb count yet, or a Govee-cloud
  device) becomes a new run: its bulb count, where it is on the house (which gives it a
  role, so presets aimed at that kind of place use it), optionally joined to one existing
  zone, and, for a box that ignores LAN commands, the slow cloud output.
  It goes last in the strip (the other runs keep their bulb numbers) and gets a
  placeholder shape inside the house's current outline (so the other runs keep their
  positions too) until camera mapping measures it. Presets that light only named runs
  and zones therefore look exactly as before on the other runs; only "all" layers,
  the zone it joined and its role's stand-ins change. Optionally (follows) it copies
  another run or zone in any preset that leaves it dark (Scene.render), which changes
  nothing on the other runs either. remove_run() undoes it.
* answer_check(): "Is it answering now?" in plain words: the discovery scan, the status
  command and a realtime (razer) test frame, plus the Govee cloud when a key is saved.
"""
from __future__ import annotations

import copy
import logging
import threading
import time

from . import protocol as p

log = logging.getLogger(__name__)

# where the run is -> its role, a default name, a placeholder line inside the house's
# outline (fractions of its width, height) and words to suggest a zone to join
LOCATIONS = {
    "porch": {"label": "Porch", "role": "porch", "name": "Porch run", "x": (0.35, 0.65), "y": 0.0,
              "zones": ("porch", "door", "entry")},
    "garage": {"label": "Garage", "role": "porch", "name": "Garage run", "x": (0.7, 1.0), "y": 0.0,
               "zones": ("garage",)},
    "second roofline": {"label": "Second roofline", "role": "roofline", "name": "Second roofline",
                        "x": (0.0, 1.0), "y": 0.55, "zones": ("lower roofline", "roofline", "roof")},
    "left eave": {"label": "Left eave", "role": "roofline", "name": "Left eave", "x": (0.0, 0.4), "y": 0.45,
                  "zones": ("flat eaves", "eave")},
    "right eave": {"label": "Right eave", "role": "roofline", "name": "Right eave", "x": (0.6, 1.0), "y": 0.45,
                   "zones": ("flat eaves", "eave")},
    "other": {"label": "Somewhere else", "role": "accent", "name": "Run 4", "x": (0.0, 1.0), "y": 0.0,
              "zones": ()},
}
_PREV_KEYS = ("name", "pixel_count", "order", "geometry", "geometry_guess", "hidden", "enabled", "role",
              "location", "cloud", "reversed", "follows", "dead", "rehung")

def locations() -> list[dict]:
    return [{"id": k, "label": v["label"], "role": v["role"], "name": v["name"]} for k, v in LOCATIONS.items()]

def suggest_zone(zones: list[str], where: str) -> str | None:
    """The existing zone a run at `where` would most naturally join (or None)."""
    for w in LOCATIONS.get(where, {}).get("zones", ()):
        for z in zones:
            if w in z.lower():
                return z
    return None

def suggest_follow(layout, zones: list[str], where: str) -> str | None:
    """What a run at `where` would most naturally copy in a preset that leaves it dark:
    the zone it would join, else a run of its kind of place, else the first run. Run ids
    (not names) so a rename doesn't break it."""
    z = suggest_zone(zones, where)
    if z:
        return z
    runs = [r for r in getattr(layout, "runs", ()) if not r.role]       # the original runs
    if not runs:
        return None
    role = LOCATIONS.get(where, {}).get("role")
    for nm in layout._by_role(role) if role else []:
        r = layout.run(nm)
        if r in runs:
            return r.mac
    return runs[0].mac

def guess_geometry(layout, where: str, count: int) -> list[list[float]]:
    """A straight placeholder line for the new run inside the house's current outline."""
    if layout is None or not layout.n:
        return []
    loc = LOCATIONS.get(where) or LOCATIONS["other"]
    w, h = layout.maxx - layout.minx, layout.maxy - layout.miny
    x0 = layout.minx + loc["x"][0] * w
    x1 = layout.minx + loc["x"][1] * w
    y = layout.miny + loc["y"] * h
    r = lambda v: round(v, 3)  # noqa: E731
    if count <= 1:
        return [[0, r((x0 + x1) / 2), r(y)]]
    return [[0, r(x0), r(y)], [count - 1, r(x1), r(y)]]

def _unique(name: str, taken: set[str], own: str | None = None) -> str:
    name = (name or "").strip()[:40] or "Run 4"
    if name == own or name not in taken:
        return name
    k = 2
    while f"{name} {k}" in taken:
        k += 1
    return f"{name} {k}"

def candidates(cfg, layout=None) -> list[dict]:
    """Boxes that could become a new run: configured, but not in the show yet (no bulb
    count, or switched off), plus runs this flow added (so they can be changed or
    taken out again)."""
    in_show = {r.mac for r in layout.runs} if layout is not None else set()
    out = []
    for mac, d in sorted(cfg.devices.items(), key=lambda kv: kv[1].get("order", 0)):
        if d.get("kind", "govee") != "govee":
            continue
        added = bool(d.get("added_run"))
        if mac in in_show and not added:
            continue
        fw = d.get("firmware") or {}
        out.append({"mac": mac, "name": d.get("name") or mac, "sku": d.get("sku") or "", "ip": d.get("ip") or "",
                    "wifi_fw": fw.get("wifiVersionSoft") or "", "pixel_count": d.get("pixel_count"),
                    "estimated_count": d.get("estimated_count"), "in_show": mac in in_show, "added": added,
                    "where": d.get("location") or "", "cloud": bool((d.get("cloud") or {}).get("enabled")),
                    "joined": (d.get("added_run") or {}).get("joined"), "follows": d.get("follows"),
                    "last_check": ((d.get("diag") or {}).get("answer_check") or {}).get("value")})
    return out

def add_run(cfg, layout, mac: str, count, where: str, name: str = "", join: str | None = None,
            output: str = "lan", cloud: dict | None = None, follows: str | None = None) -> dict:
    """Make `mac` a new run (see the module doc). Returns {"name", "count", "role", "joined"}.
    follows (opt-in): another run (its id) or a zone whose colours this run copies in
    any preset that leaves it dark; the other runs are not changed either way."""
    if where not in LOCATIONS:
        raise ValueError(f"where must be one of: {', '.join(LOCATIONS)}")
    count = int(count)
    if not 1 <= count <= 255:
        raise ValueError("bulb count must be 1..255")
    if output not in ("lan", "cloud"):
        raise ValueError("output must be 'lan' or 'cloud'")
    if output == "cloud" and not ((cloud or {}).get("device") and (cloud or {}).get("sku")):
        raise ValueError("pick the light from your Govee account first")
    if output == "cloud":
        try:
            segs = max(0, min(15, int((cloud or {}).get("segments") or 0)))
        except (TypeError, ValueError):
            raise ValueError("segments must be a number") from None
    loc = LOCATIONS[where]
    with cfg.lock:
        zones = [z for z in cfg.data.get("zones") or [] if isinstance(z, dict) and z.get("name")]
        if join and join not in {z["name"] for z in zones}:
            raise ValueError(f"no zone called {join!r}")     # (checked before anything changes)
        if follows and (follows == mac or (follows not in {z["name"] for z in zones} and not (
                follows in cfg.devices and cfg.devices[follows].get("pixel_count")))):
            raise ValueError("it can follow one of the other runs or a zone")
        d = cfg.devices.get(mac)
        if d is None:
            if output != "cloud" or not cloud:
                raise KeyError(f"unknown device {mac}")
            from .config import DEVICE_DEFAULTS
            d = cfg.devices[mac] = copy.deepcopy(DEVICE_DEFAULTS)
            d["sku"] = str(cloud.get("sku") or "")
        if d.get("kind", "govee") != "govee":
            raise ValueError("only Govee boxes are added here (WLED has its own button)")
        others = {m: x for m, x in cfg.devices.items() if m != mac}
        if layout is not None and mac in {r.mac for r in layout.runs} and not d.get("added_run"):
            raise ValueError(f"{d.get('name') or mac} is already part of the show")
        taken = {x.get("name") for x in others.values() if x.get("name")} | {z["name"] for z in zones}
        if d.get("added_run"):                      # changing an earlier add: start from before it
            _undo(cfg, mac, d, save=False)
        prev = {k: copy.deepcopy(d[k]) for k in _PREV_KEYS if k in d}
        auto = cfg.auto_name(mac, d) or not d.get("name")
        nm = _unique(name or (loc["name"] if auto else d.get("name")), taken)
        # the layout without this box: its outline and the end of the strip
        base = layout
        if base is not None and mac in {r.mac for r in base.runs}:
            from .layout import Layout
            base = Layout([r for r in base.runs if r.mac != mac])
        d.update({"name": nm, "pixel_count": count, "enabled": True, "role": loc["role"], "location": where,
                  "order": max([int(x.get("order", 0) or 0) for x in others.values()] + [-1]) + 1,
                  "geometry": guess_geometry(base, where, count), "hidden": [], "reversed": False})
        d.pop("dead", None)                         # broken-bulb marks were for the old count
        d.pop("rehung", None)
        d["geometry_guess"] = bool(d["geometry"])
        if output == "cloud":
            c = cloud or {}
            d["cloud"] = {"enabled": True, "sku": str(c["sku"]), "device": str(c["device"]),
                          "segments": segs, "gradient": True}
        else:
            d.pop("cloud", None)
        if follows:
            d["follows"] = str(follows)
        else:
            d.pop("follows", None)
        if join:
            for z in zones:
                if z["name"] == join:
                    z.setdefault("parts", []).append({"run": nm, "from": 0, "to": count - 1})
        d["added_run"] = {"prev": prev, "joined": join, "at": time.strftime("%Y-%m-%d %H:%M")}
        cfg.save()
    log.info("added run %s (%d bulbs, %s%s%s)", nm, count, where, f", joined {join}" if join else "",
             ", Govee cloud" if output == "cloud" else "")
    return {"name": nm, "count": count, "role": loc["role"], "joined": join, "output": output,
            "follows": follows or None}

def _undo(cfg, mac: str, d: dict, save: bool = True):
    info = d.get("added_run") or {}
    nm = d.get("name")
    for z in cfg.data.get("zones") or []:
        if isinstance(z, dict) and z.get("parts"):
            z["parts"] = [pt for pt in z["parts"] if not (isinstance(pt, dict) and pt.get("run") in (nm, mac))]
    prev = info.get("prev") or {}
    for k in _PREV_KEYS:
        if k in prev:
            d[k] = copy.deepcopy(prev[k])
        else:
            d.pop(k, None)
    d.setdefault("pixel_count", None)
    d.pop("added_run", None)
    if save:
        cfg.save()

def remove_run(cfg, mac: str) -> str:
    """Take a run added with add_run out of the show again (as it was before)."""
    with cfg.lock:
        d = cfg.device(mac)
        if not d.get("added_run"):
            raise ValueError("only runs added with 'Add it to the show' can be taken out here")
        nm = d.get("name") or mac
        _undo(cfg, mac, d)
    return nm

# ---------------------------------------------------------------- is it answering?
def _say(steps: list[dict], scan_ok: bool, status_ok: bool, fw: str) -> str:
    if status_ok:
        return ("It answers commands now. A green test colour was sent: look at the run. "
                "If it turned green, realtime works and it can join the show like the others.")
    if scan_ok:
        return ("It is on the Wi-Fi and answers the 'who is there?' scan, but ignores commands. "
                "Usually LAN Control is off for it in the Govee app, or its firmware is too old"
                + (f" (it reports Wi-Fi firmware {fw})" if fw else "") +
                ". Turn LAN Control on, update the firmware, power-cycle it, then check again. "
                "If it still ignores commands, it can join the show through the Govee cloud (slow).")
    return ("It is not answering on the network at all. It may be off, out of Wi-Fi range, or on another "
            "network (the PC and the lights must share one router). Check that the Govee app shows it "
            "online, power-cycle it, then check again.")

def answer_check(ctl, mac: str, scan_wait: float = 3.0, test_s: float = 4.0, sleep=time.sleep) -> dict:
    """Discovery + status + a realtime test frame for one box, reported in words. Only
    that box is touched: the show on the other runs keeps playing."""
    cfg, diag = ctl.cfg, ctl.diag
    d = cfg.device(mac)
    steps = []
    t0 = time.time()
    seen0 = diag.seen.get(mac)
    try:
        diag.scan([d["ip"]] if d.get("ip") else None)
    except OSError as e:
        log.warning("scan failed: %s", e)
    waited = 0.0
    while waited < scan_wait and not (diag.seen.get(mac) and diag.seen[mac] >= t0):
        sleep(0.25)
        waited += 0.25
    seen = diag.seen.get(mac)
    scan_ok = bool(seen and seen >= t0)
    d = cfg.device(mac)                            # the scan may have updated its address
    fw = (d.get("firmware") or {}).get("wifiVersionSoft") or ""
    steps.append({"step": "Discovery scan", "ok": scan_ok,
                  "say": (f"answered from {d.get('ip')}" + (f", Wi-Fi firmware {fw}" if fw else "")) if scan_ok else
                  ("no answer (it answered an earlier scan)" if seen0 else "no answer")})
    replies, st = 0, None
    if d.get("ip"):
        for _ in range(3):
            r = ctl.lan.status(d["ip"], timeout=1.2)
            if r:
                replies, st = replies + 1, r
    status_ok = replies > 0
    steps.append({"step": "Status command", "ok": status_ok,
                  "say": (f"{replies} of 3 answered" + (f", it says it is {'on' if st.get('onOff') else 'off'}"
                                                         if st and 'onOff' in st else "")) if d.get("ip")
                  else "no address yet (it has never answered a scan)"})
    rt = {"step": "Realtime test (green)", "ok": None, "say": ""}
    e = ctl.engine
    in_show = e is not None and mac in getattr(e.output, "devices", {}) and e.live
    if in_show:
        rt["say"] = "skipped: it is in the show right now (the show is driving it)"
    elif not d.get("ip"):
        rt["say"] = "skipped: no address"
    else:
        try:
            s = diag.streamer(mac)
            s.enable()
            s.show([(0, 255, 0)], gradient=False)
            rt["say"] = ("sent: is the run green now? (answer below)" if status_ok else
                         "sent, but a box that ignores the status command ignores this too")
            rt["ok"] = None if status_ok else False

            def _release(s=s):
                time.sleep(test_s)
                try:
                    if not (ctl.engine and ctl.engine.live and mac in getattr(ctl.engine.output, "devices", {})):
                        s.disable()
                except OSError:
                    pass
            _spawn(_release)
        except (OSError, ValueError) as ex:
            rt["ok"], rt["say"] = False, f"could not send ({ex})"
    steps.append(rt)
    cl = getattr(ctl, "cloud_check", None)
    if cl:
        c = cl(mac)
        if c:
            steps.append(c)
    verdict = _say(steps, scan_ok, status_ok, fw)
    res = {"mac": mac, "name": d.get("name"), "scan": scan_ok, "status": status_ok, "steps": steps,
           "verdict": verdict, "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    try:
        diag.note(mac, "answer_check", {"scan": scan_ok, "status": f"{replies}/3", "wifi_fw": fw})
    except (OSError, KeyError):
        pass
    return res

def _spawn(fn):
    threading.Thread(target=fn, daemon=True).start()


def razer_answer(ctl, mac: str, saw: bool) -> str:
    """The owner says whether the test turned the run green."""
    ctl.diag.note(mac, "razer_works", bool(saw))
    if saw:
        return "Realtime works. Add it to the show (below) and count its bulbs."
    return ("It answers but did not show the test colour. Update its firmware in the Govee app and "
            "check again; meanwhile it can join through the Govee cloud (slow).")

def solid(ctl, mac: str, n: int, color: str) -> dict:
    """Count finder: n copies of one colour on this box only (see diag: a frame with more
    colours than bulbs is ignored, so the answer bisects the bulb count)."""
    e = ctl.engine
    if e is not None and e.live and mac in getattr(e.output, "devices", {}):
        raise ValueError("it is in the show right now; take it out of the show first, or use diagnostics")
    n = max(1, min(p.MAX_RAZER_COLORS, int(n)))
    s = ctl.diag.rt(mac)
    s.show([p.hex_rgb(color)] * n, gradient=False)
    return {"n": n}

def release(ctl, mac: str):
    e = ctl.engine
    if e is not None and e.live and mac in getattr(e.output, "devices", {}):
        return
    s = ctl.diag.streamers.get(mac)
    if s is not None and s.realtime:
        s.disable()
