import tempfile
import unittest

from goveelights.controller import PresetStore, slug


class PresetStoreTests(unittest.TestCase):
    def test_roundtrip_and_validation(self):
        with tempfile.TemporaryDirectory() as d:
            st = PresetStore(d)
            pid = st.save({"name": "My Scene!", "layers": [{"effect": "solid", "params": {"color": "#ff0000"}}]})
            self.assertEqual(pid, "my-scene")
            self.assertEqual(st.list(), [{"id": "my-scene", "name": "My Scene!"}])
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
