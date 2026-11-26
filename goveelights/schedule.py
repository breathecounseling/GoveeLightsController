"""Sunset maths and schedule evaluation (no network, no dependencies).

Schedule config (govee_config.json "schedule"). Rule dates are MM-DD or holiday names
with offsets (thanksgiving, easter, mothers-day, fathers-day, memorial-day, labor-day,
mlk-day, presidents-day; e.g. "thanksgiving+1"). Rules may also have "days" (weekday
numbers, Mon=0), "on_time"/"off_time" overrides, and no program (times only). Optional
"morning": {"enabled", "on_time", "off": "sunrise"|"time", "off_time", "program"} and
"dim": {"enabled", "from": "22:00", "level": 0.5, "fade_min": 30}. "dusk_intro_minutes" (default 0 =
off): when the evening starts around sunset, the controller first plays the live dusk sky
(effect "dusk") for that many minutes, then crossfades into the evening's program.
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
    """(on, off) local aware datetimes for the evening that starts on date d.
    A matching rule may override on_time / off_time (e.g. stay on later at weekends)."""
    r = next((r for r in sch.get("rules", []) if rule_matches(r, d) and (r.get("on_time") or r.get("off_time"))), None)
    if r:
        sch = {**sch, **({"on": "time", "on_time": r["on_time"]} if r.get("on_time") else {}),
               **({"off_time": r["off_time"]} if r.get("off_time") else {})}
    if sch.get("on", "sunset") == "sunset":
        _, sunset = sun_times(d, float(sch.get("lat", 40.3764)), float(sch.get("lon", -104.6922)))
        on = (sunset or datetime.combine(d, time(17, 0)).astimezone()).astimezone()
        on += timedelta(minutes=float(sch.get("on_offset_min", 0)))
    else:
        on = _local(datetime.combine(d, _hm(sch.get("on_time", "17:00"))))
    off_t = _hm(sch.get("off_time", "23:00"))
    off = _local(datetime.combine(d, off_t))
    if off <= on:                     # after midnight: build it on the next date (correct across DST)
        off = _local(datetime.combine(d + timedelta(days=1), off_t))
    return on, off


# ------------------------------------------------------------- holidays
def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """n-th (1-based; -1 = last) weekday (Mon=0) of a month."""
    if n > 0:
        d = date(year, month, 1)
        d += timedelta(days=(weekday - d.weekday()) % 7)
        return d + timedelta(weeks=n - 1)
    d = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def easter(year: int) -> date:
    """Western Easter (anonymous Gregorian algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return date(year, month, day)


HOLIDAYS = {
    "mlk-day": lambda y: _nth_weekday(y, 1, 0, 3),
    "presidents-day": lambda y: _nth_weekday(y, 2, 0, 3),
    "easter": easter,
    "mothers-day": lambda y: _nth_weekday(y, 5, 6, 2),
    "memorial-day": lambda y: _nth_weekday(y, 5, 0, -1),
    "fathers-day": lambda y: _nth_weekday(y, 6, 6, 3),
    "labor-day": lambda y: _nth_weekday(y, 9, 0, 1),
    "thanksgiving": lambda y: _nth_weekday(y, 11, 3, 4),
}


def resolve_date(token: str, year: int) -> date:
    """'MM-DD', or a holiday name with an optional day offset: 'thanksgiving+1', 'easter-2'."""
    t = token.strip().lower()
    for name, fn in HOLIDAYS.items():
        if t.startswith(name):
            rest = t[len(name):]
            return fn(year) + timedelta(days=int(rest) if rest else 0)
    m, d = t.split("-")
    return date(year, int(m), int(d))


def _md(s: str) -> tuple[int, int]:
    m, d = s.split("-")
    return int(m), int(d)


def rule_matches(r: dict, d: date) -> bool:
    if r.get("enabled") is False:
        return False
    days = r.get("days")
    if days and d.weekday() not in days:
        return False
    if not r.get("from") or not r.get("to"):
        return bool(days)                            # weekday-only rule
    for year in (d.year - 1, d.year):                # ranges may wrap the new year
        try:
            a = resolve_date(r["from"], year)
            b = resolve_date(r["to"], year)
            if b < a:
                b = resolve_date(r["to"], year + 1)
        except KeyError:
            return False
        except ValueError:            # e.g. 02-29 in a non-leap year: try the other year
            continue
        if a <= d <= b:
            return True
    return False


def showtime_on_night(st: dict, night: date) -> bool:
    """Does a showtime run on the evening that began on `night`? Its date (an "MM-DD"
    or a list) wins over its weekdays (the UI hides the weekdays once a date is set).
    The after-midnight part of a window that crosses midnight belongs to that evening."""
    dates = st.get("date") or st.get("dates")
    dates = [dates] if isinstance(dates, str) else dates if isinstance(dates, list) else None
    if dates:
        return night.strftime("%m-%d") in dates
    days = st.get("days") if isinstance(st.get("days"), list) else None
    return not days or night.weekday() in days


def matching_rule(d: date, sch: dict) -> dict | None:
    return next((r for r in sch.get("rules", []) if rule_matches(r, d)), None)


def program_for(d: date, sch: dict) -> tuple[dict | None, str]:
    """(program, rule name) for an evening's date. First matching rule with a
    program wins; rules without a program only change times."""
    for r in sch.get("rules", []):
        if rule_matches(r, d) and "program" in r:     # program None = lights off that night
            return r["program"], r.get("name", "")
    return sch.get("default"), "default"


def morning_window(d: date, sch: dict) -> tuple[datetime, datetime] | None:
    m = sch.get("morning") or {}
    if not m.get("enabled"):
        return None
    on = _local(datetime.combine(d, _hm(m.get("on_time", "06:00"))))
    if m.get("off", "sunrise") == "sunrise":
        rise, _ = sun_times(d, float(sch.get("lat", 40.3764)), float(sch.get("lon", -104.6922)))
        off = (rise or on + timedelta(hours=1)).astimezone() + timedelta(minutes=float(m.get("off_offset_min", 0)))
    else:
        off = _local(datetime.combine(d, _hm(m.get("off_time", "07:30"))))
    return (on, off) if off > on else None


def dim_factor(now: datetime, sch: dict, on: datetime | None = None) -> float:
    """Late-night dimming: brightness multiplier ramping down from `from` over `fade_min`."""
    dm = sch.get("dim") or {}
    if not dm.get("enabled"):
        return 1.0
    t = _hm(dm.get("from", "22:00"))
    if on is not None:
        # the dim time belongs to the evening that started at `on`: the first occurrence of it
        # no earlier than 12 h before `on` (a dim time before the on time dims from the start)
        start = _local(datetime.combine(on.date(), t))
        if start < on - timedelta(hours=12):
            start = _local(datetime.combine(on.date() + timedelta(days=1), t))
    else:
        start = _local(datetime.combine(now.date(), t))
        if now < start and now.hour < 12 and (start - now) > timedelta(hours=12):
            start = _local(datetime.combine(now.date() - timedelta(days=1), t))
    if now < start:
        return 1.0
    level = max(0.05, min(1.0, float(dm.get("level", 0.5))))
    f = min(1.0, (now - start).total_seconds() / 60 / max(1.0, float(dm.get("fade_min", 30))))
    return 1.0 - (1.0 - level) * f


def evaluate(now: datetime, sch: dict) -> dict:
    """What should be happening now, and when it next changes."""
    ev = _evaluate_evening(now, sch)
    mw = morning_window(now.date(), sch)
    if mw and mw[0] <= now < mw[1]:
        prog = (sch.get("morning") or {}).get("program") or program_for(now.date(), sch)[0]
        return {"active": prog is not None, "program": prog, "rule": "morning",
                "on": mw[0].isoformat(timespec="minutes"), "off": mw[1].isoformat(timespec="minutes"),
                "next_change": mw[1].isoformat(timespec="seconds"), "dim": 1.0}
    if not ev["active"]:
        nxt = datetime.fromisoformat(ev["next_change"])
        for d in (now.date(), now.date() + timedelta(days=1)):      # today's or tomorrow's morning
            m = morning_window(d, sch)
            if m and now < m[0] < nxt:
                ev = {**ev, "next_change": m[0].isoformat(timespec="seconds"), "on": m[0].isoformat(timespec="minutes"),
                      "next_program": (sch.get("morning") or {}).get("program") or ev.get("next_program"), "rule": "morning"}
                break
    ev["dim"] = dim_factor(now, sch, datetime.fromisoformat(ev["on"])) if ev["active"] else 1.0
    return ev


def _evaluate_evening(now: datetime, sch: dict) -> dict:
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
