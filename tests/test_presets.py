import tempfile
import unittest

from goveelights.controller import PresetStore, slug


class PresetStoreTests(unittest.TestCase):
    def test_roundtrip_and_validation(self):
        with tempfile.TemporaryDirectory() as d:
            st = PresetStore(d)
            pid = st.save({"name": "My Scene!", "layers": [{"effect": "solid", "params": {"color": "#ff0000"}}]})
            self.assertEqual(pid, "my-scene")
            self.assertEqual([(p["id"], p["name"]) for p in st.list()], [("my-scene", "My Scene!")])
            st.set_meta("my-scene", favorite=True, tags=["Holiday"])
            self.assertTrue(st.list()[0]["favorite"])
            self.assertEqual(st.get("my-scene")["layers"][0]["effect"], "solid")
            with self.assertRaises(ValueError):
                st.save({"name": "bad", "layers": [{"effect": "nope"}]})
            with self.assertRaises(ValueError):
                st.get("../govee_config")
            st.delete("my-scene")
            self.assertEqual(st.list(), [])

    def test_slug(self):
        self.assertEqual(slug("  Fire / Peak  "), "fire-peak")
        self.assertEqual(slug("!!!"), "preset")


if __name__ == "__main__":
    unittest.main()


class ConfigTests(unittest.TestCase):
    def test_deleted_defaults_stay_deleted(self):
        import json, os
        from goveelights.config import Config
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "c.json")
            c = Config(p)
            self.assertIn("Christmas", c.data["playlists"])
            c.data["playlists"] = {}
            c.data["engine"].pop("fps")
            c.save()
            c2 = Config(p)
            self.assertEqual(c2.data["playlists"], {})
            self.assertEqual(c2.data["engine"]["fps"], 20)        # settings blocks still get new defaults
