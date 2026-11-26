"""Live data shared with effects. The controller writes here; effects only read.

    LIVE["games"]   {"league:team": game dict from live.find_game, or
                     {"state": "none", "team": ...} / {"error": ...}}
    LIVE["updated"] unix time of the last write

Effects must treat everything here as optional and possibly stale or malformed.
"""
from __future__ import annotations

import time

LIVE: dict = {"games": {}, "updated": 0.0}


def set_games(games: dict):
    """Replace the latest game info (copied, so effects never see a dict mid-update)."""
    LIVE["games"] = {k: dict(v) for k, v in (games or {}).items() if isinstance(v, dict)}
    LIVE["updated"] = time.time()


def current_game() -> dict | None:
    """The first game in progress, else None."""
    games = LIVE.get("games") or {}
    if not isinstance(games, dict):
        return None
    for g in list(games.values()):
        if isinstance(g, dict) and g.get("state") == "in" and g.get("team"):
            return g
    return None
