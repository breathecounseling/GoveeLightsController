import unittest

from goveelights.output import MemoryOutput, MultiOutput, WledDdpOutput


class WledTests(unittest.TestCase):
    def test_ddp_packets(self):
        px = [(i % 256, 0, 255) for i in range(600)]             # 1800 bytes -> 2 packets
        pk = WledDdpOutput.packets(px, 3)
        self.assertEqual(len(pk), 2)
        self.assertEqual(pk[0][0], 0x40)                         # version 1, no push yet
        self.assertEqual(pk[1][0], 0x41)                         # push on the last packet
        self.assertEqual(pk[0][2], 0x0B)                         # RGB, 8 bits per channel
        self.assertEqual(int.from_bytes(pk[0][8:10], "big"), 1440)
        self.assertEqual(int.from_bytes(pk[1][4:8], "big"), 1440)
        self.assertEqual(int.from_bytes(pk[1][8:10], "big"), 360)
        self.assertEqual(bytes(pk[0][10:13]), bytes([0, 0, 255]))

    def test_multi_output_routes(self):
        class G(MemoryOutput):
            devices = {}
        g, w = G(), MemoryOutput()
        m = MultiOutput(g, {"wled:1": w})
        m.start(force=True)
        m.send_frame("govee-mac", [(1, 2, 3)])
        m.send_frame("wled:1", [(4, 5, 6)])
        self.assertIn("govee-mac", g.frames)
        self.assertIn("wled:1", w.frames)
        self.assertNotIn("wled:1", g.frames)


if __name__ == "__main__":
    unittest.main()
