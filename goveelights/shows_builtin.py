"""Built-in shows: ready-made cue lists that appear alongside the user's own shows.

They live here (not in the config) so they can't be deleted or overwritten by saving
the shows list. A user show with the same name wins, so "copy, then edit" is the way to
change one. Every cue names a preset in presets/; tests check that they all exist.

Cue keys (the same as user shows): preset, seconds (hold), transition (one of
Engine.TRANSITIONS), fade (seconds the transition takes). A show's "end" is "resume"
(go back to what was playing) or "loop".

One extra cue kind, used by the New Year's show: {"countdown_to": "HH:MM", "seconds": 60}
counts down on the house (eaves fill up, big digits on the zigzag for the last 10 s)
to the next time it is HH:MM. When the show is played within 20 minutes of that time,
the countdown is pinned to the clock: the cues before it repeat until exactly
`seconds` before HH:MM, then the countdown runs and the following cues start at HH:MM.
Played at any other time (to try it out) it is an ordinary 'seconds'-long countdown.

New Year's Eve: schedule it with a showtime that fires once at 23:55 on 31 December:
    "showtimes": [{"show": "New Year's Eve countdown", "from": "23:55", "to": "23:55",
                   "every": 60, "date": "12-31", "only_when_on": false, "enabled": true}]
("only_when_on": false switches the lights on for it even if the schedule turned them
off earlier; they go back off when the show ends.) While the show is running it owns
midnight: the built-in "New Year" countdown event stands aside instead of cutting in.
"""
from __future__ import annotations

import copy

BUILTIN_SHOWS: dict[str, dict] = {
    "Christmas Eve spectacular": {
        "builtin": True,
        "description": "About 9 minutes: the tree is lit with a ceremony, snow piles up on the "
                       "roof, embers fall, fireworks from the porch build to a grand finale, "
                       "then MERRY CHRISTMAS and back to the glowing tree.",
        "end": "resume",
        "cues": [
            {"preset": "tree-lighting-ceremony", "seconds": 45, "transition": "black", "fade": 3},
            {"preset": "snow-piling-up", "seconds": 75, "transition": "crossfade", "fade": 4},
            {"preset": "christmas-tree", "seconds": 60, "transition": "rise", "fade": 3},
            {"preset": "ember-falls", "seconds": 50, "transition": "crossfade", "fade": 3},
            {"preset": "porch-fireworks", "seconds": 60, "transition": "black", "fade": 2},
            {"preset": "grand-finale", "seconds": 60, "transition": "flash", "fade": 1},
            {"preset": "text-merry-christmas", "seconds": 75, "transition": "iris", "fade": 2.5},
            {"preset": "christmas-tree", "seconds": 90, "transition": "crossfade", "fade": 4},
        ],
    },
    "New Year's Eve countdown": {
        "builtin": True,
        "description": "The last five minutes of the year: champagne and gold building up, a "
                       "one-minute countdown that hits zero at midnight, then the grand finale "
                       "and HAPPY NEW YEAR. Schedule it at 23:55 on 31 December.",
        "end": "resume",
        "cues": [
            {"preset": "champagne-fizz", "seconds": 60, "transition": "crossfade", "fade": 3},
            {"preset": "golden-shimmer", "seconds": 50, "transition": "crossfade", "fade": 3},
            {"preset": "winter-sparkle", "seconds": 40, "transition": "wipe", "fade": 2},
            {"preset": "paparazzi", "seconds": 30, "transition": "crossfade", "fade": 1.5},
            {"preset": "porch-fireworks", "seconds": 60, "transition": "black", "fade": 2},
            {"countdown_to": "00:00", "seconds": 60, "transition": "crossfade", "fade": 1},
            {"preset": "grand-finale", "seconds": 90, "transition": "flash", "fade": 1},
            {"preset": "text-happy-new-year", "seconds": 90, "transition": "iris", "fade": 2},
            {"preset": "grand-finale", "seconds": 45, "transition": "flash", "fade": 1},
        ],
    },
    "Winter wonderland loop": {
        "builtin": True,
        "description": "A gentle evening rotation that loops: snowy evening, the icy tree, "
                       "falling waves, the sky from dusk to night, snow piling up and winter "
                       "sparkle, each for a few minutes with slow crossfades.",
        "end": "loop",
        "cues": [
            {"preset": "snowy-evening", "seconds": 240, "transition": "crossfade", "fade": 6},
            {"preset": "icy-tree", "seconds": 180, "transition": "crossfade", "fade": 6},
            {"preset": "falling-waves", "seconds": 180, "transition": "crossfade", "fade": 6},
            {"preset": "dusk-demo", "seconds": 162, "transition": "crossfade", "fade": 8},   # one full demo cycle
            {"preset": "snow-piling-up", "seconds": 240, "transition": "crossfade", "fade": 6},
            {"preset": "winter-sparkle", "seconds": 150, "transition": "crossfade", "fade": 6},
        ],
    },
    "Santa's coming": {
        "builtin": True,
        "description": "A six-minute Christmas Eve loop: a starry night with shooting stars, the "
                       "advent calendar opening its doors, Santa's sleigh flying over the stars, "
                       "dripping icicles, the house wrapped like a present (it unwraps in a "
                       "burst of sparkles), then the lit Christmas tree. Loops all evening.",
        "end": "loop",
        "cues": [
            {"preset": "starry-night", "seconds": 60, "transition": "black", "fade": 3},
            {"preset": "advent-calendar", "seconds": 45, "transition": "crossfade", "fade": 3},
            {"preset": "starry-night-sleigh", "seconds": 75, "transition": "crossfade", "fade": 3},
            {"preset": "icicles", "seconds": 45, "transition": "crossfade", "fade": 3},
            {"preset": "gift-wrap", "seconds": 60, "transition": "wipe", "fade": 2},
            {"preset": "christmas-tree", "seconds": 75, "transition": "rise", "fade": 3},
        ],
    },
}


def builtin_shows() -> dict[str, dict]:
    """A copy callers may change freely."""
    return copy.deepcopy(BUILTIN_SHOWS)


def cue_ok(cue: dict) -> bool:
    return bool(isinstance(cue, dict) and (cue.get("preset") or cue.get("countdown_to")))


def without_builtin_copies(user: dict) -> dict:
    """User shows minus untouched copies of built-ins (so a UI that lists both and saves the
    lot doesn't freeze today's built-ins into the config)."""
    out = {}
    for name, sh in (user or {}).items():
        b = BUILTIN_SHOWS.get(name)
        if b is not None and _same(sh, b):
            continue
        out[name] = sh
    return out


def _same(a, b) -> bool:
    strip = lambda d: {k: v for k, v in (d or {}).items() if k not in ("builtin", "description")}  # noqa: E731
    return isinstance(a, dict) and strip(a) == strip(b)
