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
* "Top of the Peak" (where = "peak top"): a box whose bulbs carry the Peak's zigzag on up
  to the gable apex (this house's "Tip", an H705E). Its visible bulbs get rows above the
  Peak's top row worked out from the Peak's own geometry (row spacing, centre line, bulb
  spacing and the slope of its sides), the rest of its bulbs are hidden, it joins every
  zone that holds the Peak's top row, and it "extends" the Peak (layout.py): every
  layer aimed at the Peak, the zigzag pictures, the tree's star, text and the sleigh's
  stardust treat the two as one triangle. tip_geometry() is the maths; TIP_ROWS the
  assumed rows.
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
    "peak top": {"label": "Top of the Peak (completes the gable)", "role": "peak", "name": "Tip",
                 "x": (0.45, 0.55), "y": 1.0, "zones": (), "tip": True},
    "other": {"label": "Somewhere else", "role": "accent", "name": "Run 4", "x": (0.0, 1.0), "y": 0.0,
              "zones": ()},
}
_PREV_KEYS = ("name", "pixel_count", "order", "geometry", "geometry_guess", "hidden", "enabled", "role",
              "location", "cloud", "reversed", "follows", "dead", "rehung", "extends", "tip")

# The Tip's rows, from its bulb 0: what the owner's landmarks give (Nov 2026). Bulb 0 is
# "Left (South) corner bottom row", 6 "Right (North) corner 2nd row" (read as the NORTH
# END OF ITS FIRST ROW: 0-6 is one row of 7, south to north), 7 "Right corner 2nd row"
# and 10 "Left corner 2nd row" (7-10, north to south) and 11 "Tip" (the apex). If the
# Tip turns out to be wired differently, change these numbers (or type other rows in
# the Add a run sheet): nothing else depends on them.
TIP_ROWS = (7, 4, 1)
TIP_VISIBLE = 12

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

def zigzag_host(layout):
    """The run whose zigzag the Tip continues: the original run that is wired as a
    zigzag (rows that reverse), the one with most bulbs; None if the house has none."""
    if layout is None:
        return None
    best, size = None, 0
    for r in layout.runs:
        if r.role or r.extends or not r.geometry:
            continue
        rows = _host_rows(r)
        n = sum(x["n"] for x in rows)
        if rows and all(x["n"] >= 2 for x in rows) and n > size:
            best, size = r, n
    return best


def copy_runs(layout, leave_out=()) -> list:
    """Fresh Run objects for a layout's runs (a Layout fills its runs' pixel lists, so a
    new Layout must not reuse another one's Run objects)."""
    from .layout import Run
    return [Run(mac=r.mac, name=r.name, count=r.count, reverse=r.reverse, hidden=set(r.hidden),
                geometry=r.geometry, trim=r.trim, bright=r.bright, rgamma=r.rgamma, fade=r.fade, role=r.role,
                guess=r.guess, follows=r.follows, dead=set(r.dead), extends=r.extends)
            for r in layout.runs if r.mac not in leave_out]


def _host_rows(r) -> list[dict]:
    """The host's rows in wiring order (physical bulb order, hidden bulbs left out):
    [{"y", "x0", "x1", "n", "dir"}], x0 where the row starts and dir +1/-1 its way."""
    pos = r.positions()
    order = [k for k in range(r.count) if k not in r.hidden]
    if len(order) < 2:
        return []
    rows, cur, direction = [], [order[0]], 0
    for a, b in zip(order, order[1:]):
        dx = pos[b][0] - pos[a][0]
        d = 1 if dx > 0.2 else -1 if dx < -0.2 else 0
        if d and direction and d != direction and len(cur) >= 2:
            rows.append((cur, direction))
            cur = []
        if d:
            direction = d
        cur.append(b)
    rows.append((cur, direction))
    out = []
    for ks, dr in rows:
        ys = [pos[k][1] for k in ks]
        out.append({"y": sum(ys) / len(ys), "x0": pos[ks[0]][0], "x1": pos[ks[-1]][0], "n": len(ks), "dir": dr,
                    "pitch": abs(pos[ks[-1]][0] - pos[ks[0]][0]) / max(1, len(ks) - 1)})
    return out if len(out) >= 3 else []


def parse_rows(rows) -> tuple[int, ...]:
    """'7, 4, 1' (or a list) -> (7, 4, 1); ValueError on anything else."""
    if rows is None or rows == "":
        return TIP_ROWS
    if isinstance(rows, str):
        rows = [v for v in rows.replace("+", ",").replace(" ", ",").split(",") if v.strip()]
    try:
        out = tuple(int(v) for v in rows)
    except (TypeError, ValueError):
        raise ValueError("bulbs per row: whole numbers, e.g. 7, 4, 1") from None
    if not out or len(out) > 12 or any(not 1 <= v <= 60 for v in out):
        raise ValueError("bulbs per row: 1 to 12 rows of 1 to 60 bulbs, e.g. 7, 4, 1")
    return out


def tip_geometry(host, visible: int = TIP_VISIBLE, rows=TIP_ROWS, flip: bool = False) -> list[list[float]]:
    """Knots [[bulb, x, y], ...] for a run that carries the host's zigzag on up to its apex.

    From the host's own rows: the bulb spacing (median along its rows), the centre line
    (the middle of its rows) and the slope of its sides (a straight-line fit of each
    row's half-width against its height). A Tip row of n bulbs is (n - 1) bulb spacings
    wide and sits at the height where the sides are that far apart, so the rows narrow
    to a point exactly as the Peak's do; a row is never closer than half the host's
    usual row spacing to the one below. Bulb 0 starts at the end where the host's top
    row finished (the zigzag carries on), or at the other end with flip. Only the first
    `visible` bulbs are placed (rows fill in order)."""
    hr = _host_rows(host)
    if not hr:
        return []
    visible = max(1, int(visible))
    pitches = sorted(r["pitch"] for r in hr if r["n"] > 1)
    pitch = pitches[len(pitches) // 2] if pitches else 1.0
    cx = sum((r["x0"] + r["x1"]) / 2 for r in hr) / len(hr)
    ys = [r["y"] for r in hr]
    hws = [abs(r["x1"] - r["x0"]) / 2 for r in hr]
    my, mh = sum(ys) / len(ys), sum(hws) / len(hws)
    var = sum((y - my) ** 2 for y in ys)
    slope = sum((y - my) * (h - mh) for y, h in zip(ys, hws)) / var if var > 1e-9 else 0.0
    gaps = sorted(abs(b - a) for a, b in zip(ys, ys[1:]))
    gap = gaps[len(gaps) // 2] if gaps else 1.0
    top = max(hr, key=lambda r: r["y"])
    last = hr[-1]
    # which way the first Tip row runs: back from where the host's last row ended
    d = -last["dir"] if last["dir"] else 1
    if flip:
        d = -d
    out, k, y_prev = [], 0, top["y"]
    for n in rows:
        if k >= visible:
            break
        m = min(n, visible - k)                     # bulbs of this row that are placed
        hw = (n - 1) * pitch / 2                    # the whole row decides where it sits
        y = (hw - mh) / slope + my if slope < -1e-6 else y_prev + gap
        y = max(y, y_prev + gap * 0.5)
        x0 = cx - d * hw
        x1 = x0 + d * (m - 1) * pitch
        r3 = lambda v: round(v, 3)  # noqa: E731
        out.append([k, r3(x0), r3(y)])
        if m > 1:
            out.append([k + m - 1, r3(x1), r3(y)])
        k += m
        d = -d
        y_prev = y
    return out


def layout_with_tip(layout, count: int = 108, visible: int = TIP_VISIBLE, flip: bool = False, rows=TIP_ROWS,
                    name: str = "Tip", mac: str = "TIP"):
    """A copy of `layout` with a Tip added the way 'Add it to the show' -> 'Top of the
    Peak' adds one (for previews, contact sheets and tests; nothing is saved)."""
    import copy as _copy

    from .layout import Layout, Run
    host = zigzag_host(layout)
    if host is None:
        raise ValueError("this layout has no zigzag (Peak) for a Tip to continue")
    runs = copy_runs(layout)
    runs.append(Run(mac=mac, name=name, count=int(count), hidden=set(range(int(visible), int(count))),
                    geometry=tip_geometry(host, visible, rows, flip), role="peak", guess=True, extends=host.mac))
    zones = _copy.deepcopy(layout.zone_specs)
    for z in zones:
        if z.get("name") in gable_zones(zones, host):
            z.setdefault("parts", []).append({"run": name, "from": 0, "to": int(visible) - 1})
    return Layout(runs, zones, layout.aliases)


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
                    "tip": d.get("tip") if d.get("added_run") else None,
                    "last_check": ((d.get("diag") or {}).get("answer_check") or {}).get("value")})
    return out

def add_run(cfg, layout, mac: str, count, where: str, name: str = "", join: str | None = None,
            output: str = "lan", cloud: dict | None = None, follows: str | None = None,
            visible=None, flip: bool = False, rows=None) -> dict:
    """Make `mac` a new run (see the module doc). Returns {"name", "count", "role", "joined"}.
    follows (opt-in): another run (its id) or a zone whose colours this run copies in
    any preset that leaves it dark; the other runs are not changed either way.
    For where = "peak top": visible (how many of its bulbs are on the gable, default 12;
    the rest are hidden), flip (bulb 0 is at the north end instead of where the Peak's
    top row finished) and rows (bulbs per row from bulb 0, default TIP_ROWS)."""
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
    tip = None
    if loc.get("tip"):
        rws = parse_rows(rows)
        try:
            vis = int(visible) if visible not in (None, "") else min(TIP_VISIBLE, count)
        except (TypeError, ValueError):
            raise ValueError("bulbs on the gable must be a number") from None
        if not 1 <= vis <= count:
            raise ValueError(f"bulbs on the gable: 1 to {count} (the box's bulb count)")
        if vis > sum(rws):
            raise ValueError(f"the rows ({' + '.join(map(str, rws))}) hold {sum(rws)} bulbs: "
                             f"add a row or show fewer than {vis}")
        tip = {"visible": vis, "flip": bool(flip), "rows": list(rws)}
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
        # the layout without this box: its outline and the end of the strip
        base = layout
        if base is not None and mac in {r.mac for r in base.runs}:
            from .layout import Layout
            base = Layout(copy_runs(base, {mac}))
        host = zigzag_host(base) if tip else None
        if tip and host is None:                    # (checked before an earlier add is undone)
            raise ValueError("this house has no zigzag (Peak) for it to continue")
        if d.get("added_run"):                      # changing an earlier add: start from before it
            _undo(cfg, mac, d, save=False)
        prev = {k: copy.deepcopy(d[k]) for k in _PREV_KEYS if k in d}
        auto = cfg.auto_name(mac, d) or not d.get("name")
        nm = _unique(name or (loc["name"] if auto else d.get("name")), taken)
        d.update({"name": nm, "pixel_count": count, "enabled": True, "role": loc["role"], "location": where,
                  "order": max([int(x.get("order", 0) or 0) for x in others.values()] + [-1]) + 1,
                  "geometry": guess_geometry(base, where, count), "hidden": [], "reversed": False})
        d.pop("extends", None)
        d.pop("tip", None)
        joins = [join] if join else []
        if tip:
            d["geometry"] = tip_geometry(host, tip["visible"], tip["rows"], tip["flip"])
            d["hidden"] = list(range(tip["visible"], count))
            d["extends"] = host.mac
            d["tip"] = dict(tip, host=host.name)
            follows = None                      # it lights with the Peak in every preset
            joins = gable_zones(zones, host)
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
        top = (tip["visible"] if tip else count) - 1
        for z in zones:
            if z["name"] in joins:
                z.setdefault("parts", []).append({"run": nm, "from": 0, "to": top})
        joined = ", ".join(joins) or None
        d["added_run"] = {"prev": prev, "joined": joined, "at": time.strftime("%Y-%m-%d %H:%M")}
        cfg.save()
    log.info("added run %s (%d bulbs, %s%s%s)", nm, count, where, f", joined {joined}" if joined else "",
             ", Govee cloud" if output == "cloud" else "")
    out = {"name": nm, "count": count, "role": loc["role"], "joined": joined, "output": output,
           "follows": follows or None}
    if tip:
        out["tip"] = {"visible": tip["visible"], "flip": tip["flip"], "rows": tip["rows"], "host": host.name}
    return out


def gable_zones(zones: list[dict], host) -> list[str]:
    """The zones the Tip joins: every zone that holds the top of the host's zigzag (on
    this house 'Zigzag top half', 'Whole front gable' and 'Whole front')."""
    vis = [k for k in range(host.count) if k not in host.hidden]
    if not vis:
        return []
    last = vis[-1]
    out = []
    for z in zones:
        for pt in z.get("parts") or []:
            if not isinstance(pt, dict) or pt.get("run") not in (host.name, host.mac):
                continue
            try:
                lo, hi = int(pt.get("from", 0)), int(pt.get("to", host.count - 1))
            except (TypeError, ValueError):
                continue
            if lo <= last <= hi:
                out.append(z["name"])
                break
    return out

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
    in_show = _driven(e, mac)
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
                    if not _driven(ctl.engine, mac):
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
    if _driven(e, mac):
        raise ValueError("it is in the show right now; take it out of the show first, or use diagnostics")
    n = max(1, min(p.MAX_RAZER_COLORS, int(n)))
    s = ctl.diag.rt(mac)
    s.show([p.hex_rgb(color)] * n, gradient=False)
    return {"n": n}

def _driven(e, mac: str) -> bool:
    """The engine is sending to this box: the show, or a bulb check with the lights off."""
    return (e is not None and mac in getattr(e.output, "devices", {})
            and (e.live or (e.takeover is not None and e.takeover_live)))


def release(ctl, mac: str):
    e = ctl.engine
    if _driven(e, mac):
        return
    s = ctl.diag.streamers.get(mac)
    if s is not None and s.realtime:
        s.disable()
