import json
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "tests")
from test_rack import house  # noqa: E402

from goveelights import live, shows  # noqa: E402
from goveelights.engine import Engine  # noqa: E402
from goveelights.output import MemoryOutput  # noqa: E402
from goveelights.scene import Scene  # noqa: E402

BOARD = {"events": [{"id": "1", "date": "2026-11-27T20:00Z", "competitions": [{
    "status": {"type": {"state": "in", "shortDetail": "Q3 5:32"}},
    "competitors": [{"homeAway": "home", "score": "17", "team": {"abbreviation": "DEN", "color": "0a2343", "alternateColor": "fc4c02"}},
                    {"homeAway": "away", "score": "10", "team": {"abbreviation": "PIT", "color": "000000", "alternateColor": "ffb612"}}]}]}]}


class LiveTests(unittest.TestCase):
    def test_weather_parse(self):
        w = live.parse_weather({"current": {"weather_code": 73, "temperature_2m": 28.4, "wind_speed_10m": 14, "snowfall": 0.2}})
        self.assertTrue(w["snow"]) ; self.assertFalse(w["rain"]); self.assertEqual(w["text"], "Snow")
        w = live.parse_weather({"current": {"weather_code": 95}})
        self.assertTrue(w["storm"]); self.assertFalse(w["rain"])
        self.assertFalse(live.parse_weather({})["snow"])

    def test_scores(self):
        g = live.find_game(BOARD, "den")
        self.assertEqual((g["score"], g["opp_score"], g["opponent"], g["state"]), (17, 10, "PIT", "in"))
        self.assertIsNone(live.find_game(BOARD, "KC"))
        self.assertIsNone(live.find_game({"events": [{"broken": True}]}, "DEN"))
        self.assertEqual(live.classify("nfl", 7), "touchdown")
        self.assertEqual(live.classify("nfl", 3), "field goal")
        self.assertEqual(live.classify("nhl", 1), "score")

    def test_team_colours_made_bright(self):
        self.assertNotEqual(shows.led_bright("#0a2343"), "#0a2343")
        r, g, b = (int(shows.led_bright("#0a2343")[i:i + 2], 16) for i in (1, 3, 5))
        self.assertGreater(max(r, g, b), 200)


class ShowTests(unittest.TestCase):
    def test_celebrations_render(self):
        L = house()
        for kind in ("touchdown", "field goal", "win"):
            d, secs = shows.celebration(kind, ["#0a2343", "#fc4c02"])
            self.assertGreater(secs, 5)
            Scene.from_dict(d).render(1.0, L)
        Scene.from_dict(shows.countdown_scene(0, 30, 10)).render(3.0, L)
        Scene.from_dict(shows.days_scene(24)).render(1.0, L)

    def test_next_occurrence(self):
        tz = timezone(timedelta(hours=-7))
        ev = {"month": 1, "day": 1, "time": "00:00", "minutes": 10}
        now = datetime(2026, 12, 31, 23, 0, tzinfo=tz)
        self.assertEqual(shows.next_occurrence(ev, now), datetime(2027, 1, 1, 0, 0, tzinfo=tz))
        during = datetime(2027, 1, 1, 0, 5, tzinfo=tz)                 # still inside the party window
        self.assertEqual(shows.next_occurrence(ev, during).year, 2027)

    def test_overlay_blends_on_top(self):
        L = house()
        e = Engine(L, MemoryOutput())
        e.set_scene(Scene.from_dict({"layers": [{"effect": "solid", "params": {"color": "#200000"}}]}))
        base = e.render(1.0)
        e.overlays["snow"] = (Scene.from_dict({"layers": [{"effect": "solid", "params": {"color": "#ffffff"}}]}), "lighten", 1.0)
        self.assertEqual(e.render(1.1)[0], (1.0, 1.0, 1.0))
        e.overlays.clear()
        self.assertEqual(e.render(1.2)[0], base[0])

    def test_mapping_fit(self):
        L = house()
        zone = L.indices("Whole front gable")
        # fake camera: zigzag 1.3x wider than estimated, image y down, 30 px per bulb
        pts = []
        for i in zone:
            p = L.pixels[i]
            x = (p.x + 40) * (1.3 if p.run == "Peak" else 1.0)
            pts.append([i, 600 + x * 30, 500 - p.y * 30, 0.9])
        meas = shows.fit_mapping(L, pts, zone)
        geo = shows.merged_geometry(L, meas)
        peak = next(r for r in L.runs if r.name == "Peak")
        row1 = [k for k in geo[peak.mac] if k[0] <= 26]
        width = max(k[1] for k in row1) - min(k[1] for k in row1)
        self.assertGreater(width, 18.2 * 1.15)          # wider than the old estimate
        with self.assertRaises(ValueError):
            shows.fit_mapping(L, pts[:3])


if __name__ == "__main__":
    unittest.main()
