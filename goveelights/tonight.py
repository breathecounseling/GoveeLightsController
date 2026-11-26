"""'Tonight': a plain-language plan and readiness checklist for the next evening
(or the current one, while the lights are on).

Built only from what the controller already has (config, presets, cached weather and
scores, device health): no network calls, so the page can ask for it any time.
Every part is defensive: a malformed showtime or event is left out of the plan,
never an error.
"""
from __future__ import annotations

import logging
import math
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path

from . import schedule as sched
from . import shows, shows_builtin

OK, WARN, FAIL = "ok", "warn", "fail"
SEEN_FRESH_S = 11 * 60          # two discovery scans (every 5 min) without a reply = not answering
LOG_WARN_MB, LOG_FAIL_MB = 100, 1000
DISK_WARN_MB, DISK_FAIL_MB = 1000, 200
NEXT_GAME_DAYS = 3              # a followed team's next game is mentioned this many days ahead


def fmt_time(dt: datetime) -> str:
    """'4:41 pm', 'midnight', 'noon'."""
    if dt.hour == 0 and dt.minute == 0:
        return "midnight"
    if dt.hour == 12 and dt.minute == 0:
        return "noon"
    return f"{dt.hour % 12 or 12}:{dt.minute:02d} {'am' if dt.hour < 12 else 'pm'}"


def fmt_day(d: date, today: date) -> str:
    if d == today:
        return "Tonight"
    if d == today + timedelta(days=1):
        return "Tomorrow"
    if d == today - timedelta(days=1):
        return "Last night"
    return f"{d:%A}"


# ------------------------------------------------------------ the evening
def evening(now: datetime, sch: dict) -> tuple[date, datetime, datetime, bool]:
    """(date, on, off, happening now) for the evening that is on now, else the next one."""
    today = now.date()
    for d in (today - timedelta(days=1), today):
        on, off = sched.window(d, sch)
        if on <= now < off:
            return d, on, off, True
    on, off = sched.window(today, sch)
    if now < on:
        return today, on, off, False
    d = today + timedelta(days=1)
    on, off = sched.window(d, sch)
    return d, on, off, False


def _times_rule(d: date, sch: dict) -> dict | None:
    return next((r for r in sch.get("rules", []) if isinstance(r, dict) and sched.rule_matches(r, d)
                 and (r.get("on_time") or r.get("off_time"))), None)


def _program_rule(d: date, sch: dict) -> dict | None:
    return next((r for r in sch.get("rules", []) if isinstance(r, dict) and sched.rule_matches(r, d)
                 and "program" in r), None)


def on_reason(d: date, sch: dict) -> str:
    r = _times_rule(d, sch)
    if r and r.get("on_time"):
        return f"rule: {r.get('name') or 'unnamed'}"
    if sch.get("on", "sunset") == "sunset":
        try:
            off = round(float(sch.get("on_offset_min", 0)))
        except (TypeError, ValueError):
            off = 0
        if off == 0:
            return "sunset"
        return f"{abs(off)} min {'after' if off > 0 else 'before'} sunset"
    return "fixed time"


def off_reason(d: date, sch: dict) -> str:
    r = _times_rule(d, sch)
    return f"rule: {r.get('name') or 'unnamed'}" if r and r.get("off_time") else ""


class Names:
    """Display names for programs, from the installed presets and playlists."""

    def __init__(self, presets: dict[str, str], playlists: dict):
        self.presets, self.playlists = presets, playlists if isinstance(playlists, dict) else {}

    def preset(self, pid) -> str:
        return self.presets.get(pid) or str(pid)

    def program(self, prog) -> str:
        if not prog:
            return "lights off"
        if not isinstance(prog, dict):
            return str(prog)
        if prog.get("playlist"):
            return str(prog["playlist"]).removeprefix("Holiday: ") + (
                " rotation" if str(prog["playlist"]).startswith("Holiday: ") else "")
        if prog.get("evolve"):
            return f"{self.preset(prog['evolve'])} (evolving)"
        if prog.get("preset"):
            return self.preset(prog["preset"])
        return "nothing"

    def program_detail(self, prog) -> str:
        if isinstance(prog, dict) and prog.get("playlist"):
            pl = self.playlists.get(prog["playlist"]) or {}
            items = [i.get("preset") for i in pl.get("items") or [] if isinstance(i, dict)]
            if not items:
                return "playlist (empty)"
            names = []
            for i in items:
                n = self.preset(i)
                if n not in names:
                    names.append(n)
            more = f" +{len(names) - 4} more" if len(names) > 4 else ""
            return "playlist: " + ", ".join(names[:4]) + more
        if isinstance(prog, dict) and prog.get("evolve"):
            return f"a fresh variation every {prog.get('minutes', 3):g} min"
        return ""

    def problem(self, prog) -> tuple[str, str] | None:
        """(level, text) if a program can't play as intended."""
        if not prog or not isinstance(prog, dict):
            return None
        if prog.get("playlist"):
            pl = self.playlists.get(prog["playlist"])
            if not isinstance(pl, dict) or not pl.get("items"):
                return FAIL, f"playlist '{prog['playlist']}' is missing or empty"
            missing = sorted({str(i.get("preset")) for i in pl["items"]
                              if not isinstance(i, dict) or i.get("preset") not in self.presets})
            if len(missing) == len(pl["items"]):
                return FAIL, f"none of playlist '{prog['playlist']}' presets are installed"
            if missing:
                return WARN, f"playlist '{prog['playlist']}' skips missing presets: {', '.join(missing[:3])}"
            return None
        pid = prog.get("preset") or prog.get("evolve")
        if pid and pid not in self.presets:
            return FAIL, f"preset '{pid}' is not installed"
        return None


# ------------------------------------------------------------ showtimes
def showtime_times(st: dict, start: datetime, end: datetime) -> list[datetime]:
    """When a showtime fires between start and end (local, aware), honouring its
    from/to/every, days and dates exactly as the controller's showtime check does."""
    if not isinstance(st, dict) or not st.get("enabled", True):
        return []
    try:
        a, b = sched._hm(st.get("from", "17:00")), sched._hm(st.get("to", "22:00"))
        every = max(5, int(st.get("every", 60)))
    except (ValueError, TypeError, AttributeError):
        return []
    lo, hi = a.hour * 60 + a.minute, b.hour * 60 + b.minute
    mins = [(lo + k * every) % 1440 for k in range(1440 // every + 1)
            if k * every <= ((hi - lo) % 1440)]
    out = []
    d = start.date() - timedelta(days=1)              # d is the evening each firing belongs to
    while d <= end.date():
        if sched.showtime_on_night(st, d):
            for m in sorted(set(mins)):
                day = d + timedelta(days=1) if lo > hi and m < lo else d   # after midnight: the next day
                t = datetime(day.year, day.month, day.day, m // 60, m % 60).astimezone()
                if start <= t < end:
                    out.append(t)
        d += timedelta(days=1)
    return sorted(set(out))


def describe_times(ts: list[datetime]) -> str:
    if not ts:
        return ""
    if len(ts) == 1:
        return f"at {fmt_time(ts[0])}"
    if len(ts) <= 3:
        return "at " + ", ".join(fmt_time(t) for t in ts)
    step = round((ts[1] - ts[0]).total_seconds() / 60)
    each = "every hour" if step == 60 else f"every {step} min" if step < 60 else f"every {step / 60:g} hours"
    return f"{each}, {fmt_time(ts[0])} – {fmt_time(ts[-1])} ({len(ts)}×)"


# ------------------------------------------------------------ time zone
# Contiguous US time zones by longitude (west edge), standard UTC offset. The real lines
# wander, so a house within BORDER degrees of one may be on either side.
US_ZONES = [(-125.0, "Pacific", -8), (-114.5, "Mountain", -7), (-102.0, "Central", -6), (-87.5, "Eastern", -5)]
BORDER = 1.2


def us_dst(d: date) -> bool:
    """US daylight saving: second Sunday of March to first Sunday of November."""
    a = sched._nth_weekday(d.year, 3, 6, 2)
    b = sched._nth_weekday(d.year, 11, 6, 1)
    return a <= d < b


def expected_offsets(lat: float, lon: float, d: date) -> tuple[str, set[float], float | None]:
    """(zone name, UTC offsets in hours that are plausible on date d, the likely one)."""
    if 24 <= lat <= 50 and -125 <= lon <= -66:
        dst = us_dst(d)
        if 31 <= lat <= 37 and -114.8 <= lon <= -109.05:          # Arizona: no daylight saving
            return "Arizona (Mountain, no daylight saving)", {-7.0}, -7.0
        zone = next(z for z in reversed(US_ZONES) if lon >= z[0])
        ok = {zone[2] + (1 if dst else 0)}
        for w, _, std in US_ZONES:
            if abs(lon - w) <= BORDER and w > -125:                # near a zone line: either side
                ok |= {std + (1 if dst else 0), std - 1 + (1 if dst else 0)}
        return f"{zone[1]} time", {float(x) for x in ok}, float(zone[2] + (1 if dst else 0))
    nominal = lon / 15.0
    lo, hi = nominal - 1.5, nominal + 2.5            # local zones run a little east; plus summer time
    return "around UTC%+.0f" % nominal, {x / 4 for x in range(int(lo * 4) - 1, int(hi * 4) + 2)
                                         if lo <= x / 4 <= hi}, None


def _hours(off: float) -> str:
    h, m = int(abs(off)), round(abs(off) * 60) % 60
    return f"UTC{'-' if off < 0 else '+'}{h}" + (f":{m:02d}" if m else "")


def tz_check(lat: float, lon: float, at: datetime) -> tuple[str, str, str]:
    """(level, title, detail) comparing the PC's UTC offset at `at` with the house's zone."""
    off = at.utcoffset().total_seconds() / 3600 if at.utcoffset() is not None else 0.0
    name, ok, likely = expected_offsets(lat, lon, at.date())
    if any(abs(off - x) < 0.01 for x in ok):
        return OK, "PC clock time zone", f"{_hours(off)}, right for {name}"
    want = f" ({_hours(likely)} on this date)" if likely is not None else ""
    return WARN, "PC clock time zone", (f"This PC is on {_hours(off)} but the house uses {name}{want}: "
                                        f"sunset and on/off times would be off by hours")


# ------------------------------------------------------------ the plan
def _check(cid, level, title, detail="", fix="", jump=""):
    return {"id": cid, "level": level, "title": title, "detail": detail, "fix": fix, "jump": jump}


def _log_file() -> Path | None:
    for h in logging.getLogger().handlers:
        fn = getattr(h, "baseFilename", None)
        if fn:
            return Path(fn)
    p = Path(__file__).resolve().parent.parent / "goveelights.log"
    return p if p.exists() else None


def build(ctl, now: datetime | None = None, password_set: bool | None = None) -> dict:
    now = now or datetime.now().astimezone()
    if now.tzinfo is None:
        now = now.astimezone()
    try:
        return _build(ctl, now, password_set)
    except Exception as e:                    # noqa: BLE001 - the page must always get an answer
        logging.getLogger(__name__).exception("tonight plan failed")
        c = _check("plan", FAIL, "Tonight's plan can't be worked out", str(e) or type(e).__name__,
                   "Check the schedule settings", "sched")
        return {"date": now.date().isoformat(), "day": "Tonight", "current": False, "enabled": False,
                "headline": "Something in the settings stops the plan being worked out", "timeline": [],
                "showtimes": [], "events": [], "games": [], "weather": {"enabled": False},
                "visitor": {"enabled": False}, "dim": None, "checks": [c],
                "summary": {OK: 0, WARN: 0, FAIL: 1}, "generated": now.isoformat(timespec="seconds")}


def _build(ctl, now: datetime, password_set) -> dict:
    cfg = ctl.cfg.data
    sch = cfg.get("schedule") or {}
    sch = sch if isinstance(sch, dict) else {}
    try:
        preset_list = ctl.presets.list()
    except OSError:
        preset_list = []
    names = Names({p["id"]: p["name"] for p in preset_list}, cfg.get("playlists") or {})
    checks: list[dict] = []
    timeline: list[dict] = []

    def at(dt, label, kind, detail=""):
        timeline.append({"at": dt.isoformat(timespec="minutes"), "time": fmt_time(dt), "label": label,
                         "kind": kind, "detail": detail})

    enabled = bool(sch.get("enabled"))
    failed = False
    try:
        d, on, off, current = evening(now, sch)
        prog, rule_name = sched.program_for(d, sch) if on != off else (None, "")
        prule = _program_rule(d, sch)
        why_prog = ("holiday calendar: " if prule.get("calendar") else "rule: ") + str(
            prule.get("name") or "unnamed") if prule else "usual program"
        on_why, off_why = on_reason(d, sch), off_reason(d, sch)
    except Exception as e:                    # noqa: BLE001 - a broken schedule is a check, not a crash
        failed = True
        d, on, off, current = now.date(), now, now, False
        prog, rule_name, why_prog, on_why, off_why = None, "", "schedule error", "", ""
        checks.append(_check("schedule", FAIL, "Schedule can't be worked out", str(e) or type(e).__name__,
                             "Open the schedule and fix the times", "sched"))
    morning_off = None
    if enabled and not failed:
        try:
            ev = sched.evaluate(now, sch)
            if ev.get("rule") == "morning" and ev.get("active"):
                morning_off = datetime.fromisoformat(ev["off"])
        except Exception:                     # noqa: BLE001
            morning_off = None
    plan = {"date": d.isoformat(), "day": fmt_day(d, now.date()), "current": current, "enabled": enabled,
            "on": on.isoformat(timespec="minutes"), "off": off.isoformat(timespec="minutes"),
            "on_text": fmt_time(on), "off_text": fmt_time(off), "on_why": on_why,
            "off_why": off_why, "program": prog, "program_name": names.program(prog),
            "morning_now": morning_off is not None,
            "program_detail": names.program_detail(prog), "program_why": why_prog, "rule": rule_name}

    # --- what happens through the evening
    dusk_min = 0.0
    try:
        dusk_min = max(0.0, min(120.0, float(sch.get("dusk_intro_minutes") or 0)))
        _, sunset = sched.sun_times(d, float(sch.get("lat", 40.3764)), float(sch.get("lon", -104.6922)))
    except (TypeError, ValueError):
        sunset = None
    dusk = bool(prog and dusk_min > 0 and sunset is not None and abs((on - sunset).total_seconds()) <= 90 * 60)
    plan["dusk_intro_min"] = round(dusk_min) if dusk else 0
    if prog:
        if dusk:
            at(on, "Lights on: the dusk sky", "on", f"Sky for {dusk_min:g} min ({plan['on_why']})")
            at(on + timedelta(minutes=dusk_min), names.program(prog), "program", plan["program_detail"])
        else:
            at(on, f"Lights on: {names.program(prog)}", "on", "; ".join(
                x for x in (plan["on_why"], plan["program_detail"]) if x))
    span_start = now if current else on
    st_out, missed = [], []
    for k, st in enumerate(cfg.get("showtimes") or []):
        if not isinstance(st, dict) or not st.get("enabled", True):
            continue
        when_on = st.get("only_when_on", True)
        name = str(st.get("show", ""))
        if when_on:
            if not prog:                              # schedule off: what it would do once it's on
                continue
            ts = showtime_times(st, span_start, off)
            if not ts and enabled:                    # would it have fired tonight, outside the lit hours?
                noon = datetime(d.year, d.month, d.day, 12, 0).astimezone()
                day_ts = showtime_times(st, noon, noon + timedelta(days=1))
                if day_ts and all(t < on or t >= off for t in day_ts):
                    t0 = day_ts[0]
                    why = (f"before the lights come on at {fmt_time(on)}" if t0 < on
                           else f"after the lights go off at {fmt_time(off)}")
                    missed.append(f"{name} at {fmt_time(t0)} is {why}")
        else:                                         # switches the lights on for itself
            ts = showtime_times(st, max(now, on - timedelta(hours=6)), off + timedelta(hours=6))
        if not ts:
            continue
        sh = ctl.show_def(name)
        exists = sh is not None
        loops = bool(isinstance(sh, dict) and sh.get("end") == "loop")
        st_out.append({"index": k, "show": name, "times": [t.isoformat(timespec="minutes") for t in ts[:48]],
                       "text": describe_times(ts), "forced": not when_on, "exists": exists, "loops": loops})
        note = "; ".join(x for x in ("switches the lights on for it" if not when_on else "",
                                     "loops until lights-off (replaces the program)" if loops else "") if x)
        if len(ts) <= 3:
            for t in ts:
                at(t, f"Show: {name}", "show", note)
        else:                                         # a repeating showtime is one line, not dozens
            at(ts[0], f"Show: {name}", "show", "; ".join(x for x in (describe_times(ts), note) if x))
    plan["showtimes"] = st_out

    ev_out = []
    for ev in ctl.events or []:
        if not isinstance(ev, dict) or not ev.get("enabled", True):
            continue
        try:
            when = shows.next_occurrence(ev, now)
        except (KeyError, ValueError, TypeError):
            continue
        if not (now - timedelta(minutes=1) <= when <= now + timedelta(hours=24)):
            continue
        cd, mins = float(ev.get("countdown") or 0), float(ev.get("minutes") or 0)
        if cd <= 0 and mins <= 0:
            continue
        what = ", ".join(x for x in (f"{cd:g} s countdown" if cd > 0 else "",
                                     f"{mins:g} min celebration" if mins > 0 else "") if x)
        ev_out.append({"name": ev.get("name", "Event"), "at": when.isoformat(timespec="minutes"),
                       "time": fmt_time(when), "what": what, "lights_on": bool(ev.get("lights_on", True))})
        at(when, str(ev.get("name", "Event")), "event", what)
    plan["events"] = ev_out

    g = cfg.get("game") or {}
    games_out = []
    if g.get("enabled"):
        teams = [f"{t.get('league')}:{t.get('team')}" for t in g.get("teams") or [] if isinstance(t, dict)]
        for key in teams:
            v = (ctl.games or {}).get(key)
            team = key.split(":", 1)[-1]
            if not isinstance(v, dict):
                games_out.append({"team": team, "text": "no game data yet", "state": "unknown"})
                continue
            kick = None
            try:
                if v.get("date"):
                    kick = datetime.fromisoformat(str(v["date"]).replace("Z", "+00:00")).astimezone()
            except ValueError:
                kick = None
            if v.get("state") == "in":
                games_out.append({"team": v.get("team", team), "state": "in", "text":
                                  f"playing now: {v.get('team', team)} {v.get('score', 0)} – "
                                  f"{v.get('opponent', '?')} {v.get('opp_score', 0)} ({v.get('detail', '')})"})
            elif kick and kick.date() in (now.date(), d) and v.get("state") in ("pre", "post"):
                txt = (f"{v.get('team', team)} vs {v.get('opponent', '?')}, kickoff {fmt_time(kick)}"
                       if v.get("state") == "pre" else
                       f"final: {v.get('team', team)} {v.get('score', 0)} – {v.get('opponent', '?')} {v.get('opp_score', 0)}")
                games_out.append({"team": v.get("team", team), "state": v.get("state"), "text": txt,
                                  "kickoff": kick.isoformat(timespec="minutes")})
                if v.get("state") == "pre" and kick >= now:
                    at(kick, f"Kickoff: {v.get('team', team)} vs {v.get('opponent', '?')}", "game",
                       "celebrations when they score")
            elif v.get("error") and not v.get("id"):
                games_out.append({"team": team, "state": "error", "text": "couldn't reach the scores"})
            elif (kick and v.get("state") == "pre" and now < kick
                  and kick.date() <= now.date() + timedelta(days=NEXT_GAME_DAYS)):
                games_out.append({"team": v.get("team", team), "state": "next", "text":
                                  f"next game: {v.get('team', team)} vs {v.get('opponent', '?')}, "
                                  f"{kick:%a} {fmt_time(kick)}", "kickoff": kick.isoformat(timespec="minutes")})
            else:
                games_out.append({"team": team, "state": "none", "text": "no game today"})
    plan["games"] = games_out

    w = cfg.get("weather") or {}
    wx = ctl.weather or {}
    if w.get("enabled"):
        kinds = [k for k in ("snow", "rain", "storm", "wind") if w.get(k, k != "rain")]
        age = now.timestamp() - float(wx.get("at") or 0) if wx.get("at") else None
        cond = (f"now {wx.get('text')}" + (f", {round(wx['temp_f'])}°F" if isinstance(wx.get("temp_f"), (int, float)) else "")
                if wx.get("text") else "no forecast yet")
        on_now = list(getattr(ctl.engine, "overlays", {}) or {})
        on_now = [k for k in on_now if k in ("snow", "rain", "storm", "wind")]
        plan["weather"] = {"enabled": True, "kinds": kinds, "text": cond, "showing": on_now,
                           "error": wx.get("error"), "age_min": round(age / 60) if age is not None else None}
    else:
        plan["weather"] = {"enabled": False}
    v = getattr(ctl, "vcfg", None) or cfg.get("visitor") or {}
    plan["visitor"] = {"enabled": bool(v.get("enabled"))}

    dm = sch.get("dim") or {}
    plan["dim"] = None
    if dm.get("enabled") and prog:
        try:
            t = sched._hm(dm.get("from", "22:00"))
            start = datetime.combine(on.date(), t).astimezone()
            if start < on - timedelta(hours=12):
                start = datetime.combine(on.date() + timedelta(days=1), t).astimezone()
            level = round(max(0.05, min(1.0, float(dm.get("level", 0.5)))) * 100)
            if start < off:
                plan["dim"] = {"at": start.isoformat(timespec="minutes"), "time": fmt_time(max(start, on)),
                               "level": level}
                at(max(start, on), f"Dim to {level}%", "dim", f"over {float(dm.get('fade_min', 30)):g} min")
        except (ValueError, TypeError, AttributeError):
            pass
    if prog:
        at(off, "Lights off", "off", plan["off_why"])
    try:
        mw = sched.morning_window(d + timedelta(days=1), sch)
    except (ValueError, TypeError, AttributeError):
        mw = None
    if mw and enabled:
        at(mw[0], "Morning: lights on", "on", f"until {fmt_time(mw[1])}")
    timeline.sort(key=lambda x: x["at"])
    if current:                                       # what's already happened isn't news
        timeline = [x for x in timeline if x["at"] >= now.isoformat(timespec="minutes") or x["kind"] == "on"]
    plan["timeline"] = timeline

    # --- headline
    if not enabled:
        head = "Schedule is off · the lights won't come on by themselves"
    elif failed:
        head = "Schedule has an error · open the schedule to fix it"
    elif not prog:
        head = f"{plan['day']}: lights stay off ({why_prog})"
    elif current:
        head = f"On now · {names.program(prog)} · off at {fmt_time(off)}"
    else:
        lead = "On" if plan["day"] == "Tonight" else f"{plan['day']}: on"
        head = f"{lead} at {fmt_time(on)} ({plan['on_why']}) · {names.program(prog)} · off at {fmt_time(off)}"
    if morning_off is not None and not current:
        head = f"On now (morning) · off at {fmt_time(morning_off)} · " + (
            head.replace("On at", "Tonight: on at", 1) if head.startswith("On at") else head)
    plan["headline"] = head

    # --- checks
    checks += _device_checks(ctl, now)
    if not enabled:
        checks.append(_check("schedule", FAIL, "Schedule is off", "The lights won't come on or off by themselves.",
                             "Tick 'Run on a schedule' and save", "sched"))
    else:
        ou = getattr(ctl, "override_until", None)
        if ou and ou > now:
            checks.append(_check("schedule", WARN, "Schedule paused",
                                 f"A manual change paused it until {fmt_time(ou)}.", "Tap Resume in the schedule", "sched"))
        elif not failed:
            checks.append(_check("schedule", OK, "Schedule on", f"{plan['day']}: {fmt_time(on)} – {fmt_time(off)}"))
    if not failed:
        checks += _program_checks(sch, d, prog, names)
    try:
        checks += _target_checks(ctl, prog if not failed else None, names, cfg, preset_list)
    except Exception:                         # noqa: BLE001 - a check must not break the plan
        logging.getLogger(__name__).exception("target check failed")
    checks += _show_checks(ctl, cfg, st_out, names)
    if missed:
        checks.append(_check("showtimes", WARN, "A showtime won't play tonight", "; ".join(missed[:3]),
                             "Move the showtime into the lit hours", "showtimes"))
    try:
        checks.append(_check("clock", *tz_check(float(sch.get("lat", 40.3764)), float(sch.get("lon", -104.6922)), on),
                             fix="Set the PC's time zone in Windows Settings → Time & language", jump=""))
        if checks[-1]["level"] == OK:
            checks[-1]["fix"] = ""
    except (TypeError, ValueError):
        pass
    if now.year < 2024:
        checks.append(_check("clock", FAIL, "PC clock", f"The PC thinks it is {now:%Y}.",
                             "Set the PC's date and time"))
    checks += _disk_checks(ctl)
    checks += _remote_checks(cfg, v, password_set, names)
    if plan["weather"].get("enabled"):
        wxe = plan["weather"]
        if wxe.get("error") and (wxe.get("age_min") is None or wxe["age_min"] > 45):
            checks.append(_check("weather", WARN, "Weather overlays", f"Couldn't reach the forecast: {wxe['error']}",
                                 "Check the PC's internet connection", "weather"))
        else:
            checks.append(_check("weather", OK, "Weather overlays", wxe["text"] + (
                f" · showing {', '.join(wxe['showing'])}" if wxe["showing"] else "")))
    if getattr(ctl, "diag_active", False):
        checks.append(_check("diag", WARN, "Diagnostics running", "Effects are paused until diagnostics ends.",
                             "Resume effects", "devices"))
    order = {FAIL: 0, WARN: 1, OK: 2}
    checks.sort(key=lambda c: order.get(c["level"], 3))
    plan["checks"] = checks
    plan["summary"] = {k: sum(1 for c in checks if c["level"] == k) for k in (OK, WARN, FAIL)}
    plan["generated"] = now.isoformat(timespec="seconds")
    return plan


def _device_checks(ctl, now: datetime) -> list[dict]:
    out = []
    devs = {m: dv for m, dv in ctl.cfg.devices.items() if isinstance(dv, dict) and dv.get("enabled", True) is not False}
    if not devs:
        return [_check("boxes", FAIL, "No light boxes found", "Discovery hasn't found any Govee boxes yet.",
                       "Open diagnostics and scan", "devices")]
    seen = getattr(ctl.diag, "seen", {}) or {}
    t = now.timestamp()
    up = t - float(getattr(ctl, "started", t) or t)
    govee = {m: dv for m, dv in devs.items() if dv.get("kind", "govee") != "wled"}
    wled = [dv.get("name") or m for m, dv in devs.items() if dv.get("kind") == "wled"]
    dead, quiet = [], []
    for mac, dv in govee.items():
        name = dv.get("name") or mac
        h = (ctl.health or {}).get(mac) or {}
        last_disc, last_poll = float(seen.get(mac) or 0), float(h.get("seen") or 0)
        last = max(last_disc, last_poll)
        # missed status polls only count if nothing newer has been heard: the health loop
        # runs only while the lights are live, so an old miss count lingers after lights-off
        heard_since = last_disc > last_poll and t - last_disc <= SEEN_FRESH_S
        if not dv.get("ip"):
            dead.append(f"{name} (no address)")
        elif h.get("miss", 0) >= 2 and not heard_since:
            dead.append(f"{name} (not answering)")
        elif not last:
            (quiet if up < 90 else dead).append(name)
        elif t - last > SEEN_FRESH_S:
            dead.append(f"{name} (last seen {round((t - last) / 60)} min ago)")
    n = len(govee)
    if govee and dead:
        out.append(_check("boxes", FAIL, f"{len(dead)} of {n} box{'es' if n != 1 else ''} not responding",
                          ", ".join(dead), "Check power and Wi-Fi, then Diagnostics → Scan", "devices"))
    elif govee and quiet:
        out.append(_check("boxes", WARN, "Waiting for the boxes to answer", ", ".join(quiet),
                          "Give it a minute after starting the app", "devices"))
    elif govee:
        out.append(_check("boxes", OK, f"All {n} box{'es' if n != 1 else ''} responding"))
    if wled:
        out.append(_check("wled", OK, "WLED lights (not checked here)", ", ".join(wled)
                          + ": the app can't poll WLED for health; check them in Devices if they stay dark"))
    nocount = [dv.get("name") or m for m, dv in devs.items() if not dv.get("pixel_count")]
    runs = list(getattr(ctl.engine.layout, "runs", []) or [])
    if nocount:
        out.append(_check("counts", FAIL, "Bulb count unknown", ", ".join(nocount) + " won't light up",
                          "Count the bulbs in diagnostics", "devices"))
    else:
        out.append(_check("counts", OK, "Bulb counts known", " · ".join(
            f"{r.name} {r.count - len([k for k in r.hidden if 0 <= k < r.count])}" for r in runs) + " bulbs"
            if runs else ""))
    flat = [r.name for r in runs if not r.geometry]
    if flat:
        out.append(_check("layout", WARN, "Not placed on the house", ", ".join(flat) +
                          ": 2D effects (tree, text, ripples) won't line up", "Map the bulbs with your camera", "map"))
    elif runs:
        out.append(_check("layout", OK, "Layout mapped", f"{len(runs)} runs placed on the house"))
    odd = []
    for r in runs:
        gap = _median_gap(r)
        if gap is not None and not (GAP_OK[0] <= gap <= GAP_OK[1]):
            odd.append(f"{r.name}: neighbouring bulbs {gap:.2g} apart (about 1 expected)")
    if odd:
        out.append(_check("geometry", WARN, "The map looks stretched or squashed", "; ".join(odd) +
                          ": an older camera map can be off; shapes won't line up",
                          "Map that run again, or re-import layouts/house.json", "map"))
    return out


GAP_OK = (0.5, 2.0)                       # median spacing between neighbouring bulbs, in bulb gaps


def _median_gap(r) -> float | None:
    """Median distance between neighbouring visible bulbs of a mapped run (1.0 when the
    map's scale matches the bulb spacing), or None if it isn't mapped."""
    if not getattr(r, "geometry", None):
        return None
    try:
        pos = r.positions()
    except Exception:                     # noqa: BLE001 - a bad map is reported elsewhere
        return None
    hid = set(r.hidden or [])
    gaps = sorted(math.dist(pos[k], pos[k + 1]) for k in range(len(pos) - 1)
                  if k not in hid and k + 1 not in hid)
    return gaps[len(gaps) // 2] if gaps else None


def _program_presets(prog, playlists: dict) -> set:
    if not isinstance(prog, dict):
        return set()
    if prog.get("playlist"):
        pl = (playlists or {}).get(prog["playlist"])
        items = pl.get("items", []) if isinstance(pl, dict) else []
        return {i.get("preset") for i in items if isinstance(i, dict) and i.get("preset")}
    return {x for x in (prog.get("preset"), prog.get("evolve")) if x}


def _dark_targets(ctl, layout, preset_list) -> dict[str, list[str]]:
    """{preset id: [layer targets that light no bulbs]} for this layout (cached)."""
    key = (id(layout), tuple((p["id"], p.get("mtime")) for p in preset_list))
    hit = getattr(ctl, "_dark_cache", None)
    if hit and hit[0] == key:
        return hit[1]
    out = {}
    for p in preset_list:
        try:
            d = ctl.presets.get(p["id"])
        except (OSError, ValueError):
            continue
        bad = []
        for L in d.get("layers", []) if isinstance(d, dict) else []:
            if not isinstance(L, dict) or L.get("enabled") is False:
                continue
            tg = L.get("target", "all")
            if not layout.indices(tg):
                bad += [t for t in (tg if isinstance(tg, list) else [tg]) if t not in bad]
        if bad:
            out[p["id"]] = [str(t) for t in bad]
    try:
        ctl._dark_cache = (key, out)
    except AttributeError:
        pass
    return out


def _target_checks(ctl, prog, names: Names, cfg: dict, preset_list) -> list[dict]:
    """Presets whose layers aim at a run or zone this layout doesn't have light nothing there
    (zones added to the house layout after it was imported, or a box renamed)."""
    out = []
    layout = getattr(getattr(ctl, "engine", None), "layout", None)
    if not layout or not layout.n:
        return out
    runs = {r.name for r in layout.runs} | {r.mac for r in layout.runs}
    lost = []
    for z in layout.zone_specs:
        if isinstance(z, dict):
            miss = sorted({str(pt.get("run")) for pt in z.get("parts", []) if isinstance(pt, dict)
                           and pt.get("run") not in runs})
            if miss:
                lost.append(f"{z.get('name')} (needs {', '.join(miss)})")
    if lost:
        out.append(_check("zones", WARN, f"{len(lost)} zone{'s' if len(lost) > 1 else ''} can't find their lights",
                          "; ".join(lost[:4]) + ": no light box has that name",
                          "Tap to re-apply the house layout's names and zones", "house"))
    dark = _dark_targets(ctl, layout, preset_list)
    if dark:
        miss = sorted({t for ts in dark.values() for t in ts})
        tn = sorted(_program_presets(prog, cfg.get("playlists") or {}) & set(dark))
        lvl, head = (FAIL, f"Tonight's program lights nothing on part of the house: "
                           f"{', '.join(names.preset(x) for x in tn[:3])}") if tn else (
            WARN, f"{len(dark)} preset{'s' if len(dark) > 1 else ''} light nothing on part of the house")
        out.append(_check("targets", lvl, head,
                          f"They aim at {', '.join(miss[:6])}, which your layout doesn't have"
                          + ("" if tn else f" ({', '.join(names.preset(x) for x in sorted(dark)[:4])}"
                             + ("…" if len(dark) > 4 else "") + ")"),
                          "Tap to re-add the house layout's zones", "house"))
    broken = list(getattr(ctl.presets, "broken", []) or [])
    if broken:
        out.append(_check("presets", WARN, f"{len(broken)} preset file{'s' if len(broken) > 1 else ''} can't be read",
                          ", ".join(broken[:4]) + " (damaged or not a preset); they don't show in the app",
                          "Fix or delete the file in the presets folders"))
    return out


def _program_checks(sch: dict, d: date, prog, names: Names) -> list[dict]:
    out = []
    p = names.problem(prog)
    if not prog:
        out.append(_check("program", OK, "No program tonight", "a rule keeps the lights off"))
    elif p:
        out.append(_check("program", p[0], f"Tonight's program: {names.program(prog)}", p[1],
                          "Pick another program for this rule", "sched"))
    else:
        out.append(_check("program", OK, "Tonight's program installed", names.program(prog)))
    bad = []
    for r in [{"name": "usual program", "program": sch.get("default")}] + list(sch.get("rules") or []):
        if not isinstance(r, dict) or r.get("enabled") is False or not r.get("program") or r.get("program") == prog:
            continue
        q = names.problem(r["program"])
        if q:
            bad.append(f"{r.get('name') or 'unnamed'}: {q[1]}")
    if bad:
        out.append(_check("programs", WARN, f"{len(bad)} other schedule program{'s' if len(bad) > 1 else ''} broken",
                          "; ".join(bad[:3]), "Fix the rule's program", "sched"))
    return out


def _show_checks(ctl, cfg: dict, tonight_sts: list[dict], names: Names) -> list[dict]:
    out = []
    tonight = {s["show"] for s in tonight_sts}
    bad_t, bad_o = [], []
    for st in cfg.get("showtimes") or []:
        if not isinstance(st, dict) or not st.get("enabled", True):
            continue
        name = str(st.get("show", ""))
        sh = ctl.show_def(name)
        if sh is None:
            (bad_t if name in tonight else bad_o).append(f"'{name}' doesn't exist")
            continue
        miss = sorted({c.get("preset") for c in ctl._show_cues(sh) if c.get("preset") and c["preset"] not in names.presets})
        if miss:
            (bad_t if name in tonight else bad_o).append(f"'{name}' needs {', '.join(miss[:3])}")
    if bad_t:
        out.append(_check("showtimes", FAIL, "Tonight's showtime can't play", "; ".join(bad_t),
                          "Pick another show for the showtime", "showtimes"))
    if bad_o:
        out.append(_check("showtimes", WARN, "A showtime's show is broken", "; ".join(bad_o[:3]),
                          "Pick another show for the showtime", "showtimes"))
    if not bad_t and not bad_o and tonight_sts:
        out.append(_check("showtimes", OK, "Showtime shows ready", ", ".join(sorted(tonight))))
    miss = {}
    for n, sh in shows_builtin.BUILTIN_SHOWS.items():
        for c in sh.get("cues", []):
            if c.get("preset") and c["preset"] not in names.presets:
                miss.setdefault(n, set()).add(c["preset"])
    if miss:
        out.append(_check("builtin", WARN if not (set(miss) & tonight) else FAIL, "Built-in shows missing presets",
                          "; ".join(f"{n}: {', '.join(sorted(m)[:3])}" for n, m in miss.items()),
                          "Restore the presets folder", "shows"))
    else:
        out.append(_check("builtin", OK, "Built-in shows complete", f"{len(shows_builtin.BUILTIN_SHOWS)} shows"))
    return out


def _disk_checks(ctl) -> list[dict]:
    out = []
    lf = _log_file()
    try:
        mb = lf.stat().st_size / 1e6 if lf and lf.exists() else 0.0
    except OSError:
        mb = 0.0
    try:
        free = shutil.disk_usage(Path(ctl.cfg.path).resolve().parent).free / 1e6
    except (OSError, AttributeError, TypeError):
        free = None
    if free is not None and free < DISK_FAIL_MB:
        out.append(_check("disk", FAIL, "Disk almost full", f"{free:.0f} MB free: settings may fail to save",
                          "Free some space on the PC"))
    elif free is not None and free < DISK_WARN_MB:
        out.append(_check("disk", WARN, "Disk space low", f"{free:.0f} MB free", "Free some space on the PC"))
    if mb >= LOG_FAIL_MB:
        out.append(_check("log", FAIL, "Log file huge", f"{lf.name} is {mb / 1000:.1f} GB",
                          f"Delete {lf} (the app makes a new one)"))
    elif mb >= LOG_WARN_MB:
        out.append(_check("log", WARN, "Log file large", f"{lf.name} is {mb:.0f} MB",
                          f"Delete {lf} while the app is stopped"))
    if not out:
        out.append(_check("disk", OK, "Disk and log fine", (f"{free / 1000:.0f} GB free" if free is not None else "")
                          + (f" · log {mb:.1f} MB" if lf else "")))
    return out


def _remote_checks(cfg: dict, v: dict, password_set, names: Names) -> list[dict]:
    if not v.get("enabled"):
        return [_check("visitor", OK, "Visitor page off")]
    out = []
    if not v.get("token"):
        out.append(_check("visitor", FAIL, "Visitor page has no link", "Save the visitor settings to make one.",
                          "Save the visitor page", "visitor"))
    picks = [p for p in v.get("presets") or [] if p in names.presets]
    if not picks:
        out.append(_check("visitor", WARN, "Visitor page has nothing to pick", "None of its presets are installed.",
                          "Choose presets visitors can pick", "visitor"))
    if password_set is False:
        out.append(_check("remote", WARN, "No password set",
                          "The visitor page works, but you can't manage the lights away from home.",
                          "Set a password in More → Security", "security"))
    if not out:
        out.append(_check("visitor", OK, "Visitor page on", f"{len(picks)} presets to pick"
                          + (" · password set" if password_set else "")))
    return out
