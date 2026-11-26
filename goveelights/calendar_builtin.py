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
from datetime import date, timedelta

from . import schedule as sched

TAG = "calendar"
OFF_TAG = "calendar_off"                 # a user rule the calendar switched off
DEFAULT_PROGRAM = {"preset": "warm-white"}

# Playlists the calendar brings along: name -> (minutes per item, crossfade s, [candidate lists]).
# Each item is a list of preset ids; the first installed one is used, missing items are dropped.
_PLAYLISTS = {
    "Holiday: Christmas": (8, 4, [["christmas-tree"], ["gift-wrap"], ["starry-night-sleigh"], ["snowy-evening"],
                                  ["icy-tree", "golden-tree"], ["christmas-twinkle"]]),
    "Holiday: Advent": (8, 4, [["christmas-tree"], ["advent-calendar"], ["gift-wrap"], ["starry-night-sleigh"],
                               ["snowy-evening"], ["advent-calendar"], ["icy-tree", "golden-tree"]]),
    "Holiday: Christmas Day": (8, 4, [["christmas-tree"], ["text-merry-christmas"], ["golden-tree"],
                                      ["gift-wrap"], ["christmas-twinkle"], ["starry-night-sleigh"]]),
    "Holiday: New Year": (6, 3, [["champagne-fizz"], ["golden-shimmer"], ["text-happy-new-year"],
                                 ["glitter-ball"], ["winter-sparkle"]]),
    "Holiday: Winter": (10, 6, [["icicles"], ["snowy-evening"], ["snow-piling-up"], ["icy-tree"],
                                ["winter-sparkle"], ["starry-night"]]),
    "Holiday: Halloween": (8, 4, [["halloween*"], ["spooky-tree"], ["halloween"]]),
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
     "program": {"evolve": ["thanksgiving"]}, "about": "autumn colours for the holiday"},
    {"name": "Advent", "from": "12-01", "to": "12-23", "program": {"playlist": "Holiday: Advent"},
     "about": "the Christmas rotation with the advent calendar"},
    {"name": "Christmas season", "from": "thanksgiving", "to": "12-30", "program": {"playlist": "Holiday: Christmas"},
     "about": "tree, gift wrap, sleigh ride, snowy evening"},
    {"name": "Winter", "from": "01-02", "to": "01-31", "program": {"playlist": "Holiday: Winter"},
     "about": "icicles, snow and winter sparkle"},
    {"name": "Valentine's week", "from": "02-08", "to": "02-14", "program": {"evolve": ["valentines"]},
     "about": "hearts and pinks"},
    {"name": "St Patrick's", "from": "03-14", "to": "03-17", "program": {"evolve": ["st-patricks"]},
     "about": "shamrock greens"},
    {"name": "Easter week", "from": "easter-6", "to": "easter", "program": {"evolve": ["easter"]},
     "about": "spring pastels"},
    {"name": "July 4th week", "from": "06-28", "to": "07-04", "program": {"evolve": ["july-4th"]},
     "about": "red, white and blue"},
    {"name": "Halloween fortnight", "from": "10-18", "to": "10-31", "program": {"playlist": "Holiday: Halloween"},
     "about": "pumpkins, ghouls and the spooky tree"},
    {"name": "Broncos Sundays", "from": "labor-day+6", "to": "thanksgiving-1", "days": [6],
     "program": {"evolve": ["broncos-game-day", "game-day", "text-go-broncos"], "minutes": 6},
     "about": "orange and blue on NFL Sundays in the fall"},
]

# Built-in shows the calendar schedules (a show that isn't there is skipped with a note).
_SHOWTIMES = [
    {"show": "Santa's coming", "from": "17:15", "to": "17:15", "every": 60, "date": "12-24", "only_when_on": True,
     "about": "Christmas Eve from 5:15 pm, looping until lights-off"},
    {"show": "New Year's Eve countdown", "from": "23:55", "to": "23:55", "every": 60, "date": "12-31",
     "only_when_on": False, "about": "31 December at 11:55 pm: the countdown hits zero at midnight"},
]


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


def build(preset_ids, show_names) -> dict:
    """The calendar for the installed presets and shows:
    {"rules": [...], "playlists": {...}, "showtimes": [...], "skipped": [{"name", "note"}]}.
    Rules and showtimes carry "about" (a one-line description) only in "notes"."""
    ids, shows = set(preset_ids), set(show_names)
    playlists: dict[str, dict] = {}
    rules, notes, skipped = [], {}, []
    for spec in _RULES:
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
            need = sorted({c for cands in _PLAYLISTS[name][2] for c in cands})
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
        st = {k: v for k, v in spec.items() if k != "about"}
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
    sch["rules"] = sch["rules"] + copy.deepcopy(cal["rules"])
    for name, pl in cal["playlists"].items():
        playlists[name] = copy.deepcopy(pl)
    showtimes = showtimes + copy.deepcopy(cal["showtimes"])
    ids = set(preset_ids)
    if not program_ok(sch.get("default"), ids, playlists) and ("warm-white" in ids or not ids):
        sch["default"] = dict(DEFAULT_PROGRAM)
    return sch, playlists, showtimes


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
    own = strip(sch, {}, [])[0]["rules"]
    cal_rules = cal["rules"]
    count: dict[int, int] = {}
    for k in range(days):
        d = today + timedelta(days=k)
        c = next((r for r in cal_rules if sched.rule_matches(r, d)), None)
        if c is None:
            continue
        for i, r in enumerate(own):
            if isinstance(r, dict) and "program" in r and sched.rule_matches(r, d):
                if r.get("program") != c["program"]:
                    count[i] = count.get(i, 0) + 1
                break
    from .config import DEFAULTS
    factory = [r for r in DEFAULTS["schedule"]["rules"]]
    return [{"index": i, "name": own[i].get("name") or f"Rule {i + 1}", "from": own[i].get("from", ""),
             "to": own[i].get("to", ""), "nights": n, "factory": own[i] in factory}
            for i, n in sorted(count.items())]
