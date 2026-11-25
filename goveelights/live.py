"""Live data: weather (Open-Meteo, free, no key) and sports scores (ESPN's public
scoreboard JSON, unofficial). Parsing is defensive: a format change or an outage
just means no live reaction, never a crash."""
from __future__ import annotations

import json
import logging
import urllib.request

log = logging.getLogger(__name__)
UA = {"User-Agent": "house-lights/1.0"}

LEAGUES = {
    "nfl": ("football", "nfl", "NFL"),
    "college-football": ("football", "college-football", "College football"),
    "nba": ("basketball", "nba", "NBA"),
    "mens-college-basketball": ("basketball", "mens-college-basketball", "College basketball"),
    "nhl": ("hockey", "nhl", "NHL"),
    "mlb": ("baseball", "mlb", "MLB"),
    "mls": ("soccer", "usa.1", "MLS"),
}


def fetch_json(url: str, timeout: float = 8.0):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


# ------------------------------------------------------------------ weather
SNOW = {71, 73, 75, 77, 85, 86}
RAIN = {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82}
STORM = {95, 96, 99}
WMO_TEXT = {0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast", 45: "Fog", 48: "Fog",
            51: "Drizzle", 53: "Drizzle", 55: "Drizzle", 61: "Rain", 63: "Rain", 65: "Heavy rain",
            71: "Snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains", 80: "Showers", 81: "Showers",
            82: "Heavy showers", 85: "Snow showers", 86: "Snow showers", 95: "Thunderstorm",
            96: "Thunderstorm, hail", 99: "Thunderstorm, hail"}


def weather_url(lat: float, lon: float) -> str:
    return ("https://api.open-meteo.com/v1/forecast?latitude=%.4f&longitude=%.4f"
            "&current=temperature_2m,weather_code,wind_speed_10m,wind_gusts_10m,precipitation,snowfall"
            "&wind_speed_unit=mph&temperature_unit=fahrenheit&timezone=auto" % (lat, lon))


def parse_weather(d: dict) -> dict:
    c = (d or {}).get("current") or {}
    code = int(c.get("weather_code") or 0)
    snow = code in SNOW or float(c.get("snowfall") or 0) > 0
    storm = code in STORM
    rain = (code in RAIN or (float(c.get("precipitation") or 0) > 0 and not snow)) and not storm
    return {"code": code, "text": WMO_TEXT.get(code, f"code {code}"),
            "temp_f": c.get("temperature_2m"), "wind_mph": float(c.get("wind_speed_10m") or 0),
            "gust_mph": float(c.get("wind_gusts_10m") or 0),
            "snow": snow, "rain": rain, "storm": storm}


# ------------------------------------------------------------------- scores
def scoreboard_url(league: str) -> str:
    sport, path, _ = LEAGUES[league]
    return f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{path}/scoreboard"


def find_game(board: dict, team: str) -> dict | None:
    """Our team's game on this scoreboard, normalised."""
    team = team.upper()
    for ev in (board or {}).get("events", []):
        try:
            comp = ev["competitions"][0]
            sides = comp["competitors"]
            ours = next((c for c in sides if c["team"]["abbreviation"].upper() == team), None)
            if not ours:
                continue
            them = next(c for c in sides if c is not ours)
            st = (comp.get("status") or ev.get("status") or {}).get("type", {})
            return {
                "id": ev.get("id"), "state": st.get("state", "pre"), "detail": st.get("shortDetail", ""),
                "team": ours["team"]["abbreviation"], "opponent": them["team"]["abbreviation"],
                "score": int(float(ours.get("score") or 0)), "opp_score": int(float(them.get("score") or 0)),
                "colors": ["#" + ours["team"].get("color", "ff4f10"), "#" + ours["team"].get("alternateColor", "0030c0")],
                "date": ev.get("date", ""),
            }
        except (KeyError, StopIteration, TypeError, ValueError, IndexError):
            continue
    return None


def classify(league: str, delta: int) -> str:
    if league in ("nfl", "college-football"):
        return "touchdown" if delta >= 6 else "field goal" if delta == 3 else "score"
    return "score"
