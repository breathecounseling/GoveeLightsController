"""The one-tap holiday calendar: a built-in set of schedule rules covering the year.

"Use the holiday calendar" (Shows -> Schedule) previews these and, on confirm, merges
them into the schedule *after* the user's own rules (first match wins, so the user's
rules keep priority). Everything it adds carries "calendar": true, so a second tap
replaces only the calendar's own rules, playlists and showtimes.

Presets are referenced defensively: each program lists candidate preset ids and uses
the first one that is installed (a playlist keeps the items that exist). A rule whose
program can't be built is skipped with a note, so the calendar never schedules a
preset that isn't there. Nights no rule claims play the schedule's usual program
(warm white unless the user picked something else).
"""
from __future__ import annotations

import copy
from datetime import date, datetime, timedelta

from . import schedule as sched

TAG = "calendar"
OFF_TAG = "calendar_off"                 # a user rule the calendar switched off
DEFAULT_PROGRAM = {"preset": "warm-white"}

# Playlists the calendar brings along: name -> (minutes per item, crossfade s, [candidate lists]).
# Each item is a list of preset ids; the first installed one is used, missing items are dropped.
_PLAYLISTS = {
    "Holiday: Christmas": (8, 4, [["christmas-tree"], ["sleigh-grand-tour"], ["gift-wrap"], ["starry-night-sleigh"],
                                  ["candy-cane-chase"], ["snowy-evening"], ["falling-star"],
                                  ["icy-tree", "golden-tree"], ["christmas-twinkle"]]),
    "Holiday: Advent": (8, 4, [["christmas-tree"], ["advent-calendar"], ["sleigh-grand-tour"], ["gift-wrap"],
                               ["starry-night-sleigh"], ["candy-cane-chase"], ["snowy-evening"], ["advent-calendar"],
                               ["falling-star"], ["icy-tree", "golden-tree"]]),
    "Holiday: Thanksgiving": (8, 5, [["turkey-fan"], ["thanksgiving-harvest"], ["harvest-sunset"],
                                     ["gratitude-candlelight"], ["text-give-thanks"]]),
    "Holiday: Christmas Day": (8, 4, [["christmas-tree"], ["text-merry-christmas"], ["golden-tree"],
                                      ["gift-wrap"], ["christmas-twinkle"], ["starry-night-sleigh"]]),
    "Holiday: New Year": (6, 3, [["champagne-fizz"], ["golden-shimmer"], ["text-happy-new-year"],
                                 ["glitter-ball"], ["winter-sparkle"]]),
    "Holiday: Winter": (10, 6, [["icicles"], ["snowy-evening"], ["snow-piling-up"], ["icy-tree"],
                                ["winter-sparkle"], ["winter-elegance"], ["starry-night"]]),
    "Holiday: Halloween": (8, 4, [["halloween*"], ["spooky-tree"], ["halloween"]]),
    "Holiday: Valentine's": (8, 4, [["valentines-hearts"], ["valentines-glow"]]),
    "Holiday: July 4th": (6, 3, [["july-4th-flag"], ["july-4th-fireworks"]]),
}

# The rules, in priority order (first match wins). program: {"preset"|"evolve": [candidates]}
# or {"playlist": name from _PLAYLISTS}. min_items: fewest playlist items worth scheduling.
_RULES = [
    {"name": "Christmas Eve", "from": "12-24", "to": "12-24", "program": {"evolve": ["christmas-tree"]},
     "about": "the glowing tree, then the Santa's coming show all evening"},
    {"name": "Christmas Day", "from": "12-25", "to": "12-25", "program": {"playlist": "Holiday: Christmas Day"},
     "about": "tree, MERRY CHRISTMAS, golden tree, gift wrap"},
    {"name": "New Year's Eve", "from": "12-31", "to": "12-31", "off_time": "00:30",
     "program": {"playlist": "Holiday: New Year"},
     "about": "champagne and gold, the midnight countdown show, on until 12:30 am"},
    {"name": "New Year's Day", "from": "01-01", "to": "01-01", "program": {"playlist": "Holiday: New Year"},
     "about": "champagne, gold and HAPPY NEW YEAR"},
    {"name": "Thanksgiving", "from": "thanksgiving-2", "to": "thanksgiving",
     "program": {"playlist": "Holiday: Thanksgiving", "or": ["thanksgiving-harvest", "thanksgiving"]},
     "about": "the turkey fan, harvest glow and sunset, candlelight and GIVE THANKS"},
    {"name": "Advent", "from": "12-01", "to": "12-23", "program": {"playlist": "Holiday: Advent"},
     "about": "the Christmas rotation with the advent calendar"},
    {"name": "Christmas season", "from": "thanksgiving", "to": "12-30", "program": {"playlist": "Holiday: Christmas"},
     "about": "tree, gift wrap, sleigh ride, snowy evening"},
    {"name": "Winter", "from": "01-02", "to": "01-31", "program": {"playlist": "Holiday: Winter"},
     "about": "icicles, snow and winter sparkle"},
    {"name": "Valentine's week", "from": "02-08", "to": "02-14", "program": {"playlist": "Holiday: Valentine's", "or": ["valentines-hearts", "valentines-glow", "valentines"]},
     "about": "hearts and pinks"},
    {"name": "St Patrick's", "from": "03-14", "to": "03-17", "program": {"evolve": ["st-patricks"]},
     "about": "shamrock greens"},
    {"name": "Easter week", "from": "easter-6", "to": "easter", "program": {"evolve": ["easter-pastels", "easter"]},
     "about": "spring pastels"},
    {"name": "July 4th week", "from": "06-28", "to": "07-04", "program": {"playlist": "Holiday: July 4th", "or": ["july-4th-flag", "july-4th-fireworks", "july-4th"]},
     "about": "red, white and blue"},
    {"name": "Halloween fortnight", "from": "10-18", "to": "10-31", "program": {"playlist": "Holiday: Halloween"},
     "about": "pumpkins, ghouls and the spooky tree"},
    {"team": True, "from": "labor-day+6", "to": "thanksgiving-1", "days": [6]},     # see _team_rule
]

# The game-day rule follows the NFL team you follow (Shows -> Game day). Teams with their
# own look ship presets for it; any other team gets the generic game-day look.
TEAM_LOOKS = {
    "DEN": {"name": "Broncos Sundays", "evolve": ["broncos-game-day", "game-day", "text-go-broncos"],
            "about": "orange and blue on NFL Sundays in the fall"},
}


def _team_rule(spec: dict, teams) -> tuple[dict | None, str]:
    """The NFL-Sundays rule for the first NFL team followed, or (None, why not)."""
    team = next((str(t.get("team") or "").strip().upper() for t in teams or []
                 if isinstance(t, dict) and str(t.get("league") or "").lower() == "nfl"
                 and str(t.get("team") or "").strip()), "")
    if not team:
        return None, "follow an NFL team (Shows → Game day) to light up its Sundays"
    look = TEAM_LOOKS.get(team) or {"name": f"{team} Sundays", "evolve": ["game-day"],
                                    "about": "game-day colours on NFL Sundays in the fall"}
    r = {k: v for k, v in spec.items() if k != "team"}
    return {"name": look["name"], **r, "program": {"evolve": list(look["evolve"]), "minutes": 6},
            "about": look["about"]}, ""

# Built-in shows the calendar schedules (a show that isn't there is skipped with a note).
_SHOWTIMES = [
    {"show": "Santa's coming", "from": "17:15", "to": "17:15", "every": 60, "date": "12-24", "only_when_on": True,
     "about": "Christmas Eve from 5:15 pm, looping until lights-off"},
    {"show": "New Year's Eve countdown", "from": "23:55", "to": "23:55", "every": 60, "date": "12-31",
     "only_when_on": False, "about": "31 December at 11:55 pm: the countdown hits zero at midnight"},
    # top of the hour through the evening; "season" spans (holiday names allowed) become the
    # showtime's "dates" for the season that is on now or coming next
    {"show": "Thanksgiving tonight", "from": "18:00", "to": "21:00", "every": 60,
     "season": [("thanksgiving", "thanksgiving")], "only_when_on": True,
     "about": "Thanksgiving evening at the top of every hour, 6 to 9 pm"},
    {"show": "Christmas on the roofline", "from": "18:00", "to": "21:00", "every": 60,
     "season": [("thanksgiving+1", "12-23"), ("12-25", "12-30")], "only_when_on": True,
     "about": "every evening of the season at the top of the hour, 6 to 9 pm "
              "(not Christmas Eve: Santa's coming has the evening)"},
]


def season_dates(spans, today: date) -> list[str]:
    """'MM-DD' for every day of the spans [(from, to)], for the season that is on now or
    comes next (the year of the last span's end)."""
    for year in (today.year, today.year + 1):
        try:
            rng = [(sched.resolve_date(a, year), sched.resolve_date(b, year)) for a, b in spans]
        except (KeyError, ValueError):
            return []
        if max(b for _, b in rng) >= today:
            out = []
            for a, b in rng:
                d = a
                while d <= b:
                    out.append(f"{d:%m-%d}")
                    d += timedelta(days=1)
            return out
    return []


def fmt_dates(dates, today: date) -> str:
    """['11-27', '11-28', ..., '12-23', '12-25'] -> 'Nov 27 – Dec 23, Dec 25'."""
    ds = []
    for s in dates or []:
        try:
            m, d = (int(x) for x in str(s).split("-"))
            dd = date(today.year, m, d)
        except ValueError:
            continue
        if dd < today - timedelta(days=200):          # e.g. January dates seen in December
            dd = dd.replace(year=today.year + 1)
        ds.append(dd)
    ds.sort()
    runs, out = [], []
    for d in ds:
        if runs and (d - runs[-1][1]).days == 1:
            runs[-1][1] = d
        else:
            runs.append([d, d])
    for a, b in runs:
        out.append(fmt_range((a, b)))
    return ", ".join(out)


def _pick(cands: list[str], ids: set[str]) -> list[str]:
    """Installed ids for a candidate list: the first exact match, or every id matching a 'prefix*'."""
    for c in cands:
        if c.endswith("*"):
            hits = sorted(i for i in ids if i.startswith(c[:-1]) and i != c[:-1])
            if hits:
                return hits
        elif c in ids:
            return [c]
    return []


def _playlist(name: str, ids: set[str]) -> dict | None:
    minutes, fade, items = _PLAYLISTS[name]
    out = []
    for cands in items:
        for pid in _pick(cands, ids):
            if not out or out[-1]["preset"] != pid:
                out.append({"preset": pid, "minutes": minutes})
    if len({i["preset"] for i in out}) < 2:
        return None                                   # one look on repeat is not a rotation
    return {"items": out, "fade": fade, "shuffle": False, TAG: True}


def build(preset_ids, show_names, teams=(), today: date | None = None) -> dict:
    """The calendar for the installed presets and shows:
    {"rules": [...], "playlists": {...}, "showtimes": [...], "skipped": [{"name", "note"}]}.
    Rules and showtimes carry "about" (a one-line description) only in "notes".
    teams: the game settings' teams; the first NFL one gets its Sundays.
    today: for showtimes that follow a season (default: today)."""
    ids, shows = set(preset_ids), set(show_names)
    today = today or date.today()
    playlists: dict[str, dict] = {}
    rules, notes, skipped = [], {}, []
    for spec in _RULES:
        if spec.get("team"):
            spec, why = _team_rule(spec, teams)
            if spec is None:
                skipped.append({"name": "Game-day Sundays", "note": why})
                continue
        p = spec["program"]
        prog = None
        if "playlist" in p:
            name = p["playlist"]
            if name not in playlists:
                pl = _playlist(name, ids)
                if pl:
                    playlists[name] = pl
            if name in playlists:
                prog = {"playlist": name}
            elif p.get("or"):                         # too few looks for a rotation: evolve the one there is
                hit = _pick(p["or"], ids)
                if hit:
                    prog = {"evolve": hit[0], "minutes": p.get("minutes", 4)}
            need = p.get("or") or sorted({c for cands in _PLAYLISTS[name][2] for c in cands})
        else:
            kind = "evolve" if "evolve" in p else "preset"
            hit = _pick(p[kind], ids)
            if hit:
                prog = {kind: hit[0]}
                if kind == "evolve":
                    prog["minutes"] = p.get("minutes", 4)
            need = p[kind]
        if prog is None:
            skipped.append({"name": spec["name"], "note": "needs the preset " + " or ".join(
                f"'{n}'" for n in need[:3]) + " (not installed)"})
            continue
        r = {k: copy.deepcopy(v) for k, v in spec.items() if k not in ("program", "about")}
        r["program"] = prog
        r[TAG] = True
        rules.append(r)
        notes[r["name"]] = spec.get("about", "")
    showtimes = []
    for spec in _SHOWTIMES:
        if spec["show"] not in shows:
            skipped.append({"name": spec["show"], "note": f"needs the show '{spec['show']}' (not installed)"})
            continue
        st = {k: v for k, v in spec.items() if k not in ("about", "season")}
        if spec.get("season"):
            st["dates"] = season_dates(spec["season"], today)
            if not st["dates"]:
                continue
        st.update({"enabled": True, TAG: True})
        showtimes.append(st)
        notes["show:" + spec["show"]] = spec.get("about", "")
    return {"rules": rules, "playlists": playlists, "showtimes": showtimes, "skipped": skipped, "notes": notes}


def installed(sch: dict) -> bool:
    return any(isinstance(r, dict) and r.get(TAG) for r in (sch or {}).get("rules") or [])


def strip(sch: dict, playlists: dict, showtimes: list) -> tuple[dict, dict, list]:
    """Copies without anything the calendar added."""
    sch = copy.deepcopy(sch or {})
    sch["rules"] = [r for r in sch.get("rules") or [] if not (isinstance(r, dict) and r.get(TAG))]
    playlists = {n: copy.deepcopy(p) for n, p in (playlists or {}).items() if not (isinstance(p, dict) and p.get(TAG))}
    showtimes = [copy.deepcopy(s) for s in showtimes or [] if not (isinstance(s, dict) and s.get(TAG))]
    return sch, playlists, showtimes


def merge(cal: dict, sch: dict, playlists: dict, showtimes: list, preset_ids=(),
          disable: list[int] | None = None) -> tuple[dict, dict, list]:
    """The schedule, playlists and showtimes with the calendar (re)installed: the user's
    own rules first, then the calendar's. disable: indexes (into the user's own rules)
    to switch off, e.g. an old catch-all Christmas rule that would hide the calendar.
    A missing or empty usual program becomes warm white."""
    sch, playlists, showtimes = strip(sch, playlists, showtimes)
    for k in disable or []:
        if 0 <= k < len(sch["rules"]) and isinstance(sch["rules"][k], dict):
            sch["rules"][k]["enabled"] = False
            sch["rules"][k][OFF_TAG] = True           # so taking the calendar out turns it back on
    cal_rules = copy.deepcopy(cal["rules"])
    for name, pl in cal["playlists"].items():
        mine = name
        k = 1
        while mine in playlists:                      # the user's own playlist of that name stays theirs
            mine = f"{name} (calendar{f' {k}' if k > 1 else ''})"
            k += 1
        playlists[mine] = copy.deepcopy(pl)
        if mine != name:
            for r in cal_rules:
                if r["program"].get("playlist") == name:
                    r["program"]["playlist"] = mine
    late = _after_noon(sch.get("off_time", "23:00"))
    for r in cal_rules:                               # a later usual off time wins (e.g. New Year's Eve)
        if r.get("off_time") and late is not None and late > (_after_noon(r["off_time"]) or 0):
            del r["off_time"]
    sch["rules"] = sch["rules"] + cal_rules
    showtimes = showtimes + [_fit_showtime(copy.deepcopy(st), sch) for st in cal["showtimes"]]
    ids = set(preset_ids)
    if not program_ok(sch.get("default"), ids, playlists) and ("warm-white" in ids or not ids):
        sch["default"] = dict(DEFAULT_PROGRAM)
    return sch, playlists, showtimes


def _after_noon(hm) -> int | None:
    """Minutes after noon for 'HH:MM' (so 00:30 is later than 23:00)."""
    try:
        t = sched._hm(hm)
    except (ValueError, TypeError, AttributeError):
        return None
    return (t.hour * 60 + t.minute - 720) % 1440


def _fit_showtime(st: dict, sch: dict, today: date | None = None) -> dict:
    """A one-shot, only-when-on showtime that would fire before this schedule switches the
    lights on (a fixed on time, a sunset offset) is moved to just after lights-on."""
    if not (st.get("only_when_on", True) and st.get("date") and st.get("from") == st.get("to")):
        return st
    try:
        today = today or date.today()
        d = next_range({"from": st["date"], "to": st["date"]}, today)[0]
        on, off = sched.window(d, sch)
        t = datetime.combine(d, sched._hm(st["from"])).astimezone()
        if t < on and on.date() == d and on.hour < 21:     # a later 'on' is a mis-set clock: leave it
            on += timedelta(minutes=1, seconds=59)
            st["from"] = st["to"] = f"{on.hour:02d}:{on.minute:02d}"
    except Exception:                                 # noqa: BLE001 - keep the time it came with
        pass
    return st


def refit(showtimes: list, sch: dict, today: date | None = None) -> list:
    """The calendar's one-shot showtimes moved again for a changed schedule (a later on
    time would otherwise leave Santa's show due while the lights are still off)."""
    return [_fit_showtime(copy.deepcopy(st), sch, today) if isinstance(st, dict) and st.get(TAG) else st
            for st in showtimes or []]


def reenable(sch: dict) -> list[str]:
    """Switch back on the user's rules that installing the calendar switched off."""
    back = []
    for r in sch.get("rules") or []:
        if isinstance(r, dict) and r.pop(OFF_TAG, None):
            r.pop("enabled", None)                    # back to its default: on
            back.append(r.get("name") or "unnamed")
    return back


def program_ok(prog, ids: set[str], playlists: dict) -> bool:
    """A program that would actually play something (ids empty: presets not checked)."""
    if not isinstance(prog, dict):
        return False
    if prog.get("playlist"):
        pl = playlists.get(prog["playlist"])
        return bool(isinstance(pl, dict) and pl.get("items"))
    pid = prog.get("preset") or prog.get("evolve")
    return bool(pid) and (not ids or pid in ids)


# ------------------------------------------------------------- preview
def next_range(r: dict, today: date) -> tuple[date, date] | None:
    """The coming (or current) occurrence of a dated rule, as dates."""
    if not r.get("from") or not r.get("to"):
        return None
    for year in (today.year - 1, today.year, today.year + 1):
        try:
            a = sched.resolve_date(r["from"], year)
            b = sched.resolve_date(r["to"], year)
            if b < a:
                b = sched.resolve_date(r["to"], year + 1)
        except (KeyError, ValueError):
            continue
        if b >= today:
            return a, b
    return None


def fmt_range(rng: tuple[date, date] | None, days=None) -> str:
    if not rng:
        return ""
    a, b = rng
    f = lambda d: f"{d:%b} {d.day}"                     # noqa: E731
    s = f(a) if a == b else f"{f(a)} – {f(b)}"
    if days:
        names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        s = "/".join(names[d] for d in days if 0 <= d <= 6) + "s, " + s
    return s


def shadows(sch: dict, cal: dict, today: date, days: int = 366) -> list[dict]:
    """The user's own rules that would hide calendar nights over the coming year (they
    come first): [{"index", "name", "from", "to", "nights", "factory"}]."""
    from .config import DEFAULTS
    factory = [r for r in DEFAULTS["schedule"]["rules"]]
    own = strip(sch, {}, [])[0]["rules"]
    is_f = [r in factory for r in own]
    cal_rules = cal["rules"]
    count: dict[int, int] = {}
    # Counted as the preview offers it: the factory rules (pre-ticked to switch off) are
    # taken as off when counting the user's other rules; each factory rule is counted as
    # if it were the only one of them left on.
    for k in range(days):
        d = today + timedelta(days=k)
        c = next((r for r in cal_rules if sched.rule_matches(r, d)), None)
        if c is None:
            continue
        hits = [i for i, r in enumerate(own) if isinstance(r, dict) and "program" in r
                and r.get("enabled", True) is not False and sched.rule_matches(r, d)]
        first_own = next((i for i in hits if not is_f[i]), None)
        for i in hits:
            if is_f[i] and first_own is not None and first_own < i:
                continue                              # a rule of the user's own comes before it anyway
            if not is_f[i] and i != first_own:
                continue
            if own[i].get("program") != c["program"]:
                count[i] = count.get(i, 0) + 1
    return [{"index": i, "name": own[i].get("name") or f"Rule {i + 1}", "from": own[i].get("from", ""),
             "to": own[i].get("to", ""), "nights": n, "factory": own[i] in factory}
            for i, n in sorted(count.items())]
