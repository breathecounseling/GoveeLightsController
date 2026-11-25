"""Sunset maths and schedule evaluation (no network, no dependencies).

Schedule config (govee_config.json "schedule"):
{
  "enabled": true,
  "lat": 40.3764, "lon": -104.6922,          # Evans, CO
  "on": "sunset" | "time", "on_offset_min": 0, "on_time": "17:00",
  "off_time": "23:00",                         # may be after midnight, e.g. "01:00"
  "default": {"preset": "warm-white"} | {"playlist": "..."} | null,
  "rules": [{"name": "Christmas", "from": "11-25", "to": "01-06",
             "program": {"playlist": "Christmas"}}]      # first match wins; ranges may wrap the year
}
Times are the PC's local time (the PC is in the house's time zone)."""
from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta, timezone


def sun_times(d: date, lat: float, lon: float) -> tuple[datetime | None, datetime | None]:
    """Sunrise and sunset (UTC, aware) for a date. lon east-positive.
    Standard 'sunrise equation' (NOAA-style), accurate to about a minute."""
    n = (d - date(2000, 1, 1)).days + 0.0008
    jstar = n - lon / 360.0
    M = (357.5291 + 0.98560028 * jstar) % 360
    Mr = math.radians(M)
    C = 1.9148 * math.sin(Mr) + 0.0200 * math.sin(2 * Mr) + 0.0003 * math.sin(3 * Mr)
    lam = math.radians((M + C + 180 + 102.9372) % 360)
    jtransit = 2451545.0 + jstar + 0.0053 * math.sin(Mr) - 0.0069 * math.sin(2 * lam)
    dec = math.asin(math.sin(lam) * math.sin(math.radians(23.4397)))
    phi = math.radians(lat)
    cosw = (math.sin(math.radians(-0.833)) - math.sin(phi) * math.sin(dec)) / (math.cos(phi) * math.cos(dec))
    if not -1 <= cosw <= 1:
        return None, None
    w = math.degrees(math.acos(cosw))

    def to_dt(j):
        return datetime.fromtimestamp((j - 2440587.5) * 86400, tz=timezone.utc)
    return to_dt(jtransit - w / 360), to_dt(jtransit + w / 360)


def _hm(s: str) -> time:
    h, m = s.strip().split(":")
    return time(int(h), int(m))


def _local(dt_naive: datetime) -> datetime:
    return dt_naive.astimezone()      # attach the PC's local zone


def window(d: date, sch: dict) -> tuple[datetime, datetime]:
    """(on, off) local aware datetimes for the evening that starts on date d."""
    if sch.get("on", "sunset") == "sunset":
        _, sunset = sun_times(d, float(sch.get("lat", 40.3764)), float(sch.get("lon", -104.6922)))
        on = (sunset or datetime.combine(d, time(17, 0)).astimezone()).astimezone()
        on += timedelta(minutes=float(sch.get("on_offset_min", 0)))
    else:
        on = _local(datetime.combine(d, _hm(sch.get("on_time", "17:00"))))
    off = _local(datetime.combine(d, _hm(sch.get("off_time", "23:00"))))
    if off <= on:
        off += timedelta(days=1)
    return on, off


def _md(s: str) -> tuple[int, int]:
    m, d = s.split("-")
    return int(m), int(d)


def program_for(d: date, sch: dict) -> tuple[dict | None, str]:
    """(program, rule name) for an evening's date."""
    key = (d.month, d.day)
    for r in sch.get("rules", []):
        try:
            a, b = _md(r["from"]), _md(r["to"])
        except (KeyError, ValueError):
            continue
        inside = a <= key <= b if a <= b else (key >= a or key <= b)
        if inside and r.get("program"):
            return r["program"], r.get("name", "")
    return sch.get("default"), "default"


def evaluate(now: datetime, sch: dict) -> dict:
    """What should be happening now, and when it next changes."""
    today = now.date()
    for d in (today - timedelta(days=1), today):     # yesterday's window may run past midnight
        on, off = window(d, sch)
        if on <= now < off:
            prog, rule = program_for(d, sch)
            return {"active": prog is not None, "program": prog, "rule": rule,
                    "on": on.isoformat(timespec="minutes"), "off": off.isoformat(timespec="minutes"),
                    "next_change": off.isoformat(timespec="seconds")}
    on, off = window(today, sch)
    if now >= on:   # today's window is over: next is tomorrow
        on, off = window(today + timedelta(days=1), sch)
    prog, rule = program_for(on.date(), sch)
    return {"active": False, "program": None, "rule": rule, "next_program": prog,
            "on": on.isoformat(timespec="minutes"), "off": off.isoformat(timespec="minutes"),
            "next_change": on.isoformat(timespec="seconds")}
