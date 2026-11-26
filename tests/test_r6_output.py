"""Round-6 output fixes: a release can't be undone by an enable already in flight, a box
that reboots between two health polls is re-taken, diag scans keep the source IP, two
status() calls to one box both get the reply, a frame the protocol rejects only drops
that box, and the release is sent a second time."""
import base64
import logging
import threading
import time
import types
import unittest

from goveelights import controller as C
from goveelights import protocol as p
from goveelights.diag_server import App
from goveelights.lan import GoveeLan
from goveelights.output import GoveeRazerOutput
from goveelights.streamer import Streamer


class FakeLan:
    def __init__(self):
        self.sent, self.fail = [], set()
        self.lock = threading.Lock()

    def send(self, ip, msg):
        if ip in self.fail:
            raise OSError("unreachable")
        with self.lock:
            self.sent.append((time.monotonic(), ip, msg))

    def send_razer(self, ip, pkt):
        self.send(ip, p.razer_msg(pkt))

    def razer_after(self, t0):
        """'on'/'off' for every razer enable/disable packet sent after t0."""
        out = []
        for t, _ip, m in list(self.sent):
            if t <= t0 or m["msg"]["cmd"] != "razer":
                continue
            d = p.decode_razer(base64.b64decode(m["msg"]["data"]["pt"]))
            if "on" in d:
                out.append("on" if d["on"] else "off")
        return out


class StopDuringRetryTests(unittest.TestCase):
    def test_stop_cancels_an_enable_retry_in_flight(self):
        lan = FakeLan()
        o = GoveeRazerOutput(lan, {"A": {"ip": "1"}})
        o.retry_s, o.release_retry_s = 0, 0
        lan.fail = {"1"}
        o.start()                                  # box unreachable: retry scheduled
        lan.fail = set()
        o.send_frame("A", [(9, 9, 9)] * 10)        # starts the retry thread (~1.3 s)
        time.sleep(0.2)
        t_stop = time.monotonic()
        o.stop()
        time.sleep(1.6)
        after = lan.razer_after(t_stop)
        self.assertNotIn("on", after, after)
        self.assertFalse(o.streamers["A"].realtime)

    def test_retry_still_works_while_live(self):
        lan = FakeLan()
        o = GoveeRazerOutput(lan, {"A": {"ip": "1"}})
        o.retry_s = 0
        lan.fail = {"1"}
        o.start()
        lan.fail = set()
        o.send_frame("A", [(9, 9, 9)] * 10)
        time.sleep(1.6)
        self.assertTrue(o.streamers["A"].realtime)

    def test_reassert_skips_when_released_during_the_poll(self):
        enabled = []
        eng = types.SimpleNamespace(live=False, _live_lock=threading.Lock(),
                                    output=types.SimpleNamespace(
                                        _streamer=lambda mac: types.SimpleNamespace(enable=lambda: enabled.append(mac)),
                                        _last={}))
        fake = types.SimpleNamespace(engine=eng, diag_active=False, map_state=None, cfg=types.SimpleNamespace(devices={}),
                                     health={"A": {"reasserts": [], "paused": False, "last_enable": 0,
                                                   "base": None, "pending": 0}})
        C.Controller._reassert(fake, "A", "came back online")
        self.assertEqual(enabled, [])
        eng.live = True
        C.Controller._reassert(fake, "A", "came back online")
        self.assertEqual(enabled, ["A"])


class _Stop:
    def __init__(self, n):
        self.n = n

    def wait(self, _):
        self.n -= 1
        return self.n < 0


class HealOneMissTests(unittest.TestCase):
    def test_one_missed_poll_then_same_status_reasserts(self):
        ok = {"onOff": 1, "brightness": 100, "color": {"r": 0, "g": 0, "b": 0}, "colorTemInKelvin": 0}
        seq = [ok, None, ok, ok, ok]
        it = iter(seq)
        why = []
        fake = types.SimpleNamespace(
            ecfg={}, diag_active=False, map_state=None, health={},
            engine=types.SimpleNamespace(live=True, output=types.SimpleNamespace(devices={"A": {"ip": "1"}})),
            lan=types.SimpleNamespace(status=lambda ip, timeout=1.5: next(it)),
            _reassert=lambda mac, w: why.append(w), _stop=_Stop(len(seq)))
        C.Controller._health_loop(fake)
        self.assertEqual(why, ["came back online"])


class DiagIpTests(unittest.TestCase):
    def test_scan_reply_points_streamer_at_source_ip(self):
        devs = {"A": {"ip": "192.168.1.50"}}

        def upsert(info):
            devs["A"]["ip"] = info["source_ip"]
            return False
        fake = types.SimpleNamespace(seen={}, lock=threading.RLock(),
                                     streamers={"A": Streamer(FakeLan(), "192.168.1.50")},
                                     cfg=types.SimpleNamespace(upsert_discovered=upsert, devices=devs))
        App._on_device(fake, {"device": "A", "sku": "H805A", "ip": "192.168.1.23", "source_ip": "192.168.1.77"})
        self.assertEqual(fake.streamers["A"].ip, "192.168.1.77")
        App._on_device(fake, {"device": "A", "sku": "H805A", "source_ip": "192.168.1.78"})   # no "ip" key
        self.assertEqual(fake.streamers["A"].ip, "192.168.1.78")
        fake.streamers["A"].close()

    def test_device_rejects_counts_a_frame_cannot_carry(self):
        saved = []
        dev = {"pixel_count": 93}
        fake = types.SimpleNamespace(cfg=types.SimpleNamespace(lock=threading.RLock(), device=lambda mac: dev,
                                                               save=lambda: saved.append(1)))
        for bad in (300, -5, 0, "x"):
            with self.assertRaises(ValueError):
                App.handle(fake, "device", {"mac": "A", "pixel_count": bad, "name": "Eaves2"})
        self.assertEqual(dev, {"pixel_count": 93})
        App.handle(fake, "device", {"mac": "A", "pixel_count": "108"})
        self.assertEqual(dev["pixel_count"], 108)


class ConcurrentStatusTests(unittest.TestCase):
    def test_two_waiters_on_one_ip_both_get_the_reply(self):
        g = object.__new__(GoveeLan)
        g._status, g._status_ev, g._lock = {}, {}, threading.Lock()

        def send(ip, msg):
            threading.Timer(0.05, lambda: g._handle(
                p.encode({"msg": {"cmd": "devStatus", "data": {"onOff": 1}}}), ip)).start()
        g.send = send
        res = {}
        th = threading.Thread(target=lambda: res.__setitem__("a", g.status("1", timeout=1.0)))
        th.start()
        time.sleep(0.01)
        res["b"] = g.status("1", timeout=1.0)
        th.join()
        self.assertIsNotNone(res["a"])
        self.assertIsNotNone(res["b"])
        self.assertEqual(g._status_ev, {})


class BadFrameTests(unittest.TestCase):
    def test_rejected_frame_does_not_raise(self):
        lan = FakeLan()
        o = GoveeRazerOutput(lan, {"A": {"ip": "1"}, "B": {"ip": "2"}})
        o.send_frame("A", [(1, 2, 3)] * 300)       # razer frames carry 1..255
        o.send_frame("B", [(1, 2, 3)] * 93)
        self.assertEqual([ip for _t, ip, _m in lan.sent], ["2"])

    def test_keepalive_survives_a_rejected_frame(self):
        s = Streamer(FakeLan(), "1")
        s.realtime, s.frame = True, [(1, 2, 3)] * 300
        s.set_keepalive(0.01)
        time.sleep(0.2)
        self.assertEqual(s.lan.sent, [])
        s.frame = [(1, 2, 3)] * 3                  # thread must still be alive to send this
        s.set_keepalive(0.01)
        time.sleep(0.2)
        self.assertGreater(len(s.lan.sent), 0)
        s.close()


class SecondReleaseTests(unittest.TestCase):
    def test_disable_is_sent_again(self):
        lan = FakeLan()
        o = GoveeRazerOutput(lan, {"A": {"ip": "1"}})
        o.release_retry_s = 0.2
        t0 = time.monotonic()
        o.stop()
        self.assertEqual(lan.razer_after(t0), ["off"] * 3)
        time.sleep(0.5)
        self.assertEqual(lan.razer_after(t0), ["off"] * 6)

    def test_second_release_skipped_if_taken_again(self):
        lan = FakeLan()
        o = GoveeRazerOutput(lan, {"A": {"ip": "1"}})
        o.release_retry_s = 0.2
        t0 = time.monotonic()
        o.stop()
        o.start()                                  # live again before the second pass
        time.sleep(1.6)
        self.assertEqual(lan.razer_after(t0), ["off"] * 3 + ["on"] * 3)


# These tests make the app log errors on purpose (unreachable boxes, broken requests,
# bad presets); keep those expected messages out of the test run's output.
_QUIET = logging.NullHandler()


def setUpModule():
    logging.getLogger("goveelights").addHandler(_QUIET)
    logging.getLogger("goveelights").propagate = False


def tearDownModule():
    logging.getLogger("goveelights").removeHandler(_QUIET)
    logging.getLogger("goveelights").propagate = True


if __name__ == "__main__":
    unittest.main()
