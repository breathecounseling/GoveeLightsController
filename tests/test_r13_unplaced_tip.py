"""A box that got its bulb count in diagnostics (not "Add it to the show") is in the show
but has no place on the house: the Fourth run card lists it and offers the top of the Peak."""
import shutil
import tempfile
import unittest
from pathlib import Path

from goveelights import addrun
from goveelights.layout import Layout

ROOT = Path(__file__).resolve().parents[1]


class UnplacedTip(unittest.TestCase):
    def setUp(self):
        from goveelights.config import Config
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        self.cfg = Config(tmp / "c.json")
        self.cfg.import_layout(ROOT / "layouts" / "house.json", create_missing=True)
        self.cfg.upsert_discovered({"device": "TIP", "sku": "H705E", "ip": "10.0.0.40", "source_ip": "10.0.0.40"})
        d = self.cfg.devices["TIP"]
        d["name"], d["pixel_count"] = "Tip", 108        # counted in diagnostics, never placed
        d.pop("geometry", None)

    def test_listed_offered_and_moved(self):
        cfg = self.cfg
        L = Layout.from_config(cfg)
        self.assertIn("TIP", {r.mac for r in L.runs})             # in the show (a line under the house)
        boxes = {b["mac"]: b for b in addrun.candidates(cfg, L)}
        self.assertTrue(boxes["TIP"]["unplaced"] and boxes["TIP"]["tip_offer"])
        others = [b for m, b in boxes.items() if m != "TIP"]
        self.assertFalse(any(b["unplaced"] for b in others), "the placed runs are not listed as loose")
        res = addrun.to_peak_top(cfg, L, "TIP")
        self.assertEqual((res["name"], res["tip"]["visible"], res["tip"]["host"]), ("Tip", 12, "Peak"))
        L = Layout.from_config(cfg)
        self.assertEqual(len(L.run("Tip").pixels), 12)
        self.assertFalse(addrun.tip_offer(cfg, L, "TIP"))
        addrun.remove_run(cfg, "TIP")                               # back as it was: counted, unplaced
        self.assertEqual(cfg.devices["TIP"].get("pixel_count"), 108)
        self.assertTrue(addrun.unplaced(cfg.devices["TIP"]))

    def test_placed_runs_never_offered(self):
        cfg = self.cfg
        L = Layout.from_config(cfg)
        for r in L.runs:
            if r.mac != "TIP":
                self.assertFalse(addrun.tip_offer(cfg, L, r.mac), r.name)


if __name__ == "__main__":
    unittest.main()


class HouseRunLostItsShape(unittest.TestCase):
    """The owner's Accent: one of the house's own runs, its shape missing from the config.
    The card puts it back from layouts/house.json instead of asking where it is."""
    def setUp(self):
        from goveelights.config import Config
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        self.cfg = Config(tmp / "c.json")
        self.cfg.import_layout(ROOT / "layouts" / "house.json", create_missing=True)
        self.acc = next(m for m, d in self.cfg.devices.items() if d.get("name") == "Accent")
        self.want = self.cfg.devices[self.acc]["geometry"]
        self.cfg.devices[self.acc].pop("geometry")

    def test_put_back_from_the_house_layout(self):
        cfg = self.cfg
        L = Layout.from_config(cfg)
        b = {x["mac"]: x for x in addrun.candidates(cfg, L)}[self.acc]
        self.assertTrue(b["unplaced"] and b["house_shape"])
        self.assertFalse(b["tip_offer"], "the Accent is never offered as the top of the Peak")
        addrun.use_house_shape(cfg, self.acc)
        self.assertEqual(cfg.devices[self.acc]["geometry"], self.want)
        L = Layout.from_config(cfg)
        self.assertNotIn(self.acc, {x["mac"] for x in addrun.candidates(cfg, L)})   # placed again
        self.assertEqual(len(L.run("Accent").pixels), 60)

    def test_a_box_not_in_the_house_file_has_no_house_shape(self):
        self.cfg.upsert_discovered({"device": "TIP", "sku": "H705E", "ip": "10.0.0.40", "source_ip": "10.0.0.40"})
        self.cfg.devices["TIP"]["pixel_count"] = 108
        self.assertIsNone(addrun.house_entry(self.cfg, "TIP"))
        with self.assertRaises(ValueError):
            addrun.use_house_shape(self.cfg, "TIP")
