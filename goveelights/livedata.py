"""Live data shared with effects. The controller writes here; effects only read.

    LIVE["games"]   {"league:team": game dict from live.find_game, or
                     {"state": "none", "team": ...} / {..., "error": ...}}, each stamped
                    with "polled" (when it was last fetched successfully, on the delayed
                    clock) and, for finished games, "ended" (when we first saw it final)
    LIVE["updated"] unix time of the last publish
    LIVE["home"]    (lat, lon) of the house from the schedule settings, or None (see home())

Game updates can be held back by a delay (the controller's game.delay, default 30 s)
so the house never shows a score before the TV broadcast does: update_game() queues
the new info and it is published once the delay has passed.

Effects must treat everything here as optional and possibly stale or malformed.
"""
from __future__ import annotations

import threading
import time

LIVE: dict = {"games": {}, "updated": 0.0, "home": None}

STALE_IN = 360.0          # an in-progress game not refreshed for this long is not shown as live
STALE_POST = 2400.0       # ... a finished game (polled every 5-30 minutes)
FINAL_FOR = 7200.0        # show a final score for this long after the game ends
KICKOFF_MAX = 6 * 3600.0  # ... but never for a game that kicked off longer ago than this
ENDED_KEEP = 24 * 3600.0   # how long a finished game's "first seen final" time is remembered

_lock = threading.Lock()
_queue: list = []          # [(due, key, entry)] waiting for the broadcast delay
_polled: dict = {}         # key -> last successful poll (delayed clock)
_ended: dict = {}          # (key, game id) -> when we first saw it final (delayed clock)


def _stamp(key: str, g: dict, at: float) -> dict:
    g = dict(g)
    if "error" in g:                       # failed fetch: keep the age of the last good data
        g["polled"] = _polled.get(key, 0.0)
    else:
        g["polled"] = _polled[key] = at
    if g.get("state") == "post":
        g["ended"] = _ended.setdefault((key, g.get("id")), at)
    cur = (key, g.get("id"))
    for k in [k for k, t in _ended.items() if k[0] == key and k != cur and at - t > ENDED_KEEP]:
        del _ended[k]                      # this team's earlier games, long over
    return g


def set_games(games: dict):
    """Replace all game info right away (copied, so effects never see a dict mid-update)."""
    now = time.time()
    with _lock:
        _queue.clear()
        LIVE["games"] = {k: _stamp(k, v, now) for k, v in (games or {}).items() if isinstance(v, dict)}
        LIVE["updated"] = now


def update_game(key: str, game: dict | None, delay: float = 0.0):
    """Publish one team's latest info after `delay` seconds (the broadcast delay)."""
    if not isinstance(game, dict):
        return
    try:
        delay = max(0.0, min(600.0, float(delay)))
    except (TypeError, ValueError):
        delay = 0.0
    now = time.time()
    with _lock:
        _queue.append((now + delay, str(key), _stamp(str(key), game, now + delay)))
    _promote(now)


def _promote(now: float):
    with _lock:
        due = [q for q in _queue if q[0] <= now]
        if not due:
            return
        _queue[:] = [q for q in _queue if q[0] > now]
        games = dict(LIVE.get("games") or {})
        for _, key, g in sorted(due, key=lambda q: q[0]):
            games[key] = g
        LIVE["games"] = games                  # swapped whole: readers never see it mid-update
        LIVE["updated"] = now


def _kickoff(g: dict) -> float | None:
    try:
        from datetime import datetime
        s = str(g.get("date") or "").replace("Z", "+00:00")
        return datetime.fromisoformat(s).timestamp() if s else None
    except (TypeError, ValueError):
        return None


def current_game(now: float | None = None) -> dict | None:
    """The first game in progress (fresh data only); else a game that finished within
    the last couple of hours (its "state" is "post"); else None."""
    now = time.time() if now is None else now
    _promote(now)
    games = LIVE.get("games") or {}
    if not isinstance(games, dict):
        return None
    final = None
    for g in list(games.values()):
        if not isinstance(g, dict) or not g.get("team"):
            continue
        try:
            age = now - float(g.get("polled", now))
        except (TypeError, ValueError):
            continue
        if g.get("state") == "in" and age <= STALE_IN:
            return g
        if g.get("state") == "post" and final is None and age <= STALE_POST:
            try:
                ended = float(g.get("ended", now))
            except (TypeError, ValueError):
                continue
            ko = _kickoff(g)
            if now - ended <= FINAL_FOR and (ko is None or now - ko <= KICKOFF_MAX):
                final = g
    return final


# ------------------------------------------------------------ the house's location
def set_home(where) -> None:
    """The controller publishes the schedule's (lat, lon) here (None: not set yet), so
    effects that follow the real sky (dusk) use the house's location by default."""
    try:
        LIVE["home"] = (float(where[0]), float(where[1])) if where else None
    except (TypeError, ValueError, IndexError):
        LIVE["home"] = None


def home() -> tuple[float, float]:
    """The house's (lat, lon); with no location set, a rough guess from the PC's time zone
    (a mid-northern latitude on the zone's central meridian), good enough for a sky."""
    h = LIVE.get("home")
    if h:
        return h
    return 40.0, max(-180.0, min(180.0, -time.timezone / 3600.0 * 15.0))
