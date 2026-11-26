"""Defaults that older versions used without storing them in govee_config.json.

New installs start neutral: no location (the first-run setup asks for one) and no
team followed. A config file that already exists but lacks one of these keys was
relying on the old built-in value, so Config writes that value into it once on load
(see config.migrate_legacy_defaults), and that house keeps behaving exactly as before.

Each entry is a dotted config path -> the value older versions assumed. A value of
None means "nothing to migrate". tools/export_public.py replaces this whole file
with the neutral version (every value None) when building a public copy.
"""

LEGACY_DEFAULTS = {
    "schedule.lat": 40.3764,
    "schedule.lon": -104.6922,
    "game.teams": [{"league": "nfl", "team": "DEN"}],
}
