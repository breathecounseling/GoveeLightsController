"""The pad strobe: a tap is a short fast burst; a latched (held) strobe flashes slowly."""
import unittest
from unittest import mock

from goveelights import controller as C


class StrobeLimit(unittest.TestCase):
    def _spec(self, on):
        ctl = mock.MagicMock()
        ctl.engine.overlays = {}
        C.Controller.pad(ctl, "strobe", on=on)
        scene, mode, op, until, start, decay = ctl.engine.overlays["pad:strobe"]
        return scene, until - start

    def _rate(self, scene):
        d = scene.to_dict() if hasattr(scene, "to_dict") else scene
        return d["layers"][0]["fx"][0]["params"]["rate"]

    def test_a_tap_is_a_three_second_fast_burst(self):
        scene, length = self._spec(None)
        self.assertLessEqual(length, 3.5)
        self.assertEqual(self._rate(scene), 12)

    def test_a_latched_strobe_is_slow(self):
        scene, length = self._spec(True)
        self.assertGreater(length, 60)
        self.assertLessEqual(self._rate(scene), 3)


if __name__ == "__main__":
    unittest.main()
