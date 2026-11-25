import base64
import unittest

from goveelights import protocol as p


class RazerTests(unittest.TestCase):
    def test_enable_disable_match_published_bytes(self):
        self.assertEqual(p.razer_enable_packet(True).hex(" "), "bb 00 01 b1 01 0a")
        self.assertEqual(p.razer_enable_packet(False).hex(" "), "bb 00 01 b1 00 0b")

    def test_frame_layout(self):
        pkt = p.razer_frame_packet([(1, 2, 3), (4, 5, 6)])
        self.assertEqual(pkt[:6].hex(" "), "bb 00 08 b0 00 02")  # len = 3*2+2
        self.assertEqual(pkt[6:12], bytes([1, 2, 3, 4, 5, 6]))
        self.assertEqual(pkt[-1], p.xor_bytes(pkt[:-1]))

    def test_large_frame_len_and_roundtrip(self):
        cols = [(i, 255 - i, 7) for i in range(255)]
        pkt = p.razer_frame_packet(cols, gradient=True)
        self.assertEqual((pkt[1] << 8) | pkt[2], 3 * 255 + 2)
        d = p.decode_razer(pkt)
        self.assertTrue(d["gradient"])
        self.assertEqual(d["colors"], cols)

    def test_bounds(self):
        with self.assertRaises(ValueError):
            p.razer_frame_packet([])
        with self.assertRaises(ValueError):
            p.razer_frame_packet([(0, 0, 0)] * 256)

    def test_msg_envelope(self):
        m = p.razer_msg(p.razer_enable_packet())
        self.assertEqual(m["msg"]["cmd"], "razer")
        self.assertEqual(base64.b64decode(m["msg"]["data"]["pt"]).hex(), "bb0001b1010a")


class PtRealTests(unittest.TestCase):
    def test_finger_sketch_matches_reference_capture_first_packet(self):
        # From homebridge-govee-pixel-zones finger-sketch.test.ts (first packet):
        # a3 00 01 04 | 03 09 00 01 01 01 01 01 30 ff 98 29 00 01 02 | d0
        groups = [((255, 152, 41), list(range(48)))]
        pkts = p.finger_sketch_packets(groups, background=(1, 1, 1), speed=1)
        self.assertEqual(pkts[0].hex(), "a3000104030900010101010130ff98290001" "02d0")
        self.assertEqual(len(pkts), 4)
        self.assertEqual([x[1] for x in pkts[1:]], [1, 2, 0xFF])
        for x in pkts:
            self.assertEqual(len(x), 20)
            self.assertEqual(x[19], p.xor_bytes(x[:19]))


if __name__ == "__main__":
    unittest.main()
