"""First-run setup: what a new install still needs, and a quick straight-line layout.

The app shows its setup screen while there are no boxes with a bulb count (no layout
yet) or no location. Discovery, renaming and the location reuse the normal endpoints
(diag/scan, diag/add_ip, schedule, game); only the straight-line layout is new here.
"""
from __future__ import annotations

from . import schedule as sched

# A short list for people who would rather not share their exact position. Rounded to
# two decimals (about a kilometre), which is plenty for sunset times.
CITIES = [
    ("Anchorage", 61.22, -149.90), ("Atlanta", 33.75, -84.39), ("Auckland", -36.85, 174.76),
    ("Berlin", 52.52, 13.40), ("Boston", 42.36, -71.06), ("Chicago", 41.88, -87.63),
    ("Dallas", 32.78, -96.80), ("Denver", 39.74, -104.99), ("Dublin", 53.35, -6.26),
    ("Honolulu", 21.31, -157.86), ("Houston", 29.76, -95.37), ("Las Vegas", 36.17, -115.14),
    ("London", 51.51, -0.13), ("Los Angeles", 34.05, -118.24), ("Miami", 25.76, -80.19),
    ("Minneapolis", 44.98, -93.27), ("Montreal", 45.50, -73.57), ("New York", 40.71, -74.01),
    ("Paris", 48.86, 2.35), ("Phoenix", 33.45, -112.07), ("Salt Lake City", 40.76, -111.89),
    ("San Francisco", 37.77, -122.42), ("Seattle", 47.61, -122.33), ("Sydney", -33.87, 151.21),
    ("Toronto", 43.65, -79.38), ("Vancouver", 49.28, -123.12), ("Washington DC", 38.91, -77.04),
]

GAP = 3.0            # bulb spacings between boxes in a quick layout


def state(cfg) -> dict:
    """What the setup screen needs: whether it is needed, the boxes, the location."""
    devs = []
    for mac, d in sorted(cfg.devices.items(), key=lambda kv: kv[1].get("order", 0)):
        devs.append({"mac": mac, "name": d.get("name") or mac, "sku": d.get("sku", ""), "ip": d.get("ip", ""),
                     "kind": d.get("kind", "govee"), "pixel_count": d.get("pixel_count"),
                     "estimated_count": d.get("estimated_count"), "mapped": bool(d.get("geometry")),
                     "enabled": d.get("enabled", True) is not False})
    sch = cfg.data.get("schedule") or {}
    where = sched.location(sch)
    has_layout = any(x["pixel_count"] and x["enabled"] for x in devs)
    game = cfg.data.get("game") or {}
    return {"needed": not has_layout or where is None, "has_layout": has_layout,
            "location": list(where) if where else None, "devices": devs,
            "schedule_enabled": bool(sch.get("enabled")), "teams": list(game.get("teams") or []),
            "cities": [{"name": n, "lat": la, "lon": lo} for n, la, lo in CITIES]}


def straight_layout(cfg, boxes: list) -> list[str]:
    """Name, count and order the given boxes (in the order given) and lay each one out
    as a straight line, side by side. A box that already has a camera map keeps it
    unless the entry says "straight": true. Returns the names set up."""
    if not isinstance(boxes, list) or not boxes:
        raise ValueError("boxes must be a non-empty list")
    seen, clean = set(), []
    for b in boxes:
        if not isinstance(b, dict) or b.get("mac") not in cfg.devices:
            raise ValueError(f"unknown box {b.get('mac') if isinstance(b, dict) else b!r}")
        name = str(b.get("name") or "").strip()[:40] or cfg.devices[b["mac"]].get("name") or b["mac"]
        if name.lower() in seen:
            raise ValueError(f"two boxes are both called '{name}'")
        seen.add(name.lower())
        n = int(b.get("pixel_count") or 0)
        if not 1 <= n <= 255:
            raise ValueError(f"{name}: bulb count must be 1..255")
        # only what the entry says changes: running setup again on a house that is already
        # set up must not flip reversed boxes or switch boxes back on
        keep = {k: bool(b[k]) for k in ("reversed", "enabled") if k in b}
        clean.append((b["mac"], name, n, keep, bool(b.get("straight"))))
    done = []
    with cfg.lock:
        x = 0.0
        for order, (mac, name, n, keep, straight) in enumerate(clean):
            d = cfg.devices[mac]
            d.update({"name": name, "pixel_count": n, "order": order, **keep})
            if straight or not d.get("geometry"):
                d["geometry"] = [[0, x, 0.0], [n - 1, x + n - 1, 0.0]]
                d.pop("geometry_prev", None)
                d.pop("rehung_prev", None)
            x += n - 1 + GAP
            done.append(name)
        cfg.save()
    return done


def is_placeholder(geometry, count=None) -> bool:
    """A box's geometry is the straight line straight_layout gives it (not placed on the
    house yet): two knots at height 0, one bulb spacing apart."""
    try:
        if not isinstance(geometry, list) or len(geometry) != 2:
            return False
        (i0, x0, y0), (i1, x1, y1) = geometry
        n = int(i1) + 1 if count is None else int(count)
        return (int(i0) == 0 and int(i1) == n - 1 and float(y0) == 0.0 == float(y1)
                and abs(float(x1) - float(x0) - (n - 1)) < 1e-9)
    except (TypeError, ValueError):
        return False
