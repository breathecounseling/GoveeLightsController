"""Round 4 visitor fixes: IPv6 visitors see their own pick, no picks into a blackout,
the queue doesn't outlive the evening, a broken listed preset can't wedge the house,
owner features get a turn between back-to-back picks, and a queued pick's wait counts
a show that is on."""
import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, "tests")
from test_integration import Rig  # noqa: E402
from test_visitor import EVENING  # noqa: E402


def rig(test, visitor=None):
    r = Rig(test, EVENING, {"events": [], "visitor": {"enabled": True, "token": "tok-123",
                                        "presets": ["candy-cane", "warm-white"], **(visitor or {})}})
    r.ctl.play_preset("warm-white")
    r.ctl.engine.set_live(True)
    return r


class ControllerFixes(unittest.TestCase):
    def test_no_pick_during_blackout_and_queued_picks_wait(self):
        r = rig(self)
        ctl = r.ctl
        ctl.visitor_pick("candy-cane", "a")
        ctl.engine.blackout = True
        with self.assertRaises(ValueError):
            ctl.visitor_pick("warm-white", "b")
        r.tick(3, schedule=False)
        self.assertIsNone(ctl.interrupt)                # waits for the lights to come back
        self.assertEqual(len(ctl.vqueue), 1)
        ctl.engine.blackout = False
        r.tick(2, schedule=False)
        self.assertTrue((ctl.interrupt or {}).get("visitor"))

    def test_stop_live_off_and_diag_clear_the_queue(self):
        for off in (lambda c: c.stop(), lambda c: c.set_live(False), lambda c: c.enter_diag(),
                    lambda c: c.run_program(None)):
            r = rig(self, {"cooldown": 0})
            ctl = r.ctl
            for k in range(3):
                ctl.visitor_pick("candy-cane", f"v{k}")
            r.tick(2, schedule=False)
            off(ctl)
            self.assertEqual(ctl.vqueue, [])
            if ctl.diag_active:
                ctl.exit_diag()
            ctl.play_preset("warm-white")
            ctl.engine.set_live(True)
            r.tick(3, schedule=False)
            self.assertIsNone(ctl.interrupt)
            self.assertNotIn("Visitor", str(ctl.playing))

    def test_stale_picks_expire(self):
        r = rig(self, {"cooldown": 0})
        ctl = r.ctl
        ctl.visitor_pick("candy-cane", "a")
        ctl.engine.live = False                         # e.g. off behind our back, no stop()
        r.tick(ctl.VISITOR_STALE + 5, step=5, schedule=False)
        ctl.engine.live = True
        r.tick(2, schedule=False)
        self.assertEqual(ctl.vqueue, [])
        self.assertIsNone(ctl.interrupt)

    def test_broken_preset_leaves_no_phantom_interrupt(self):
        r = rig(self, {"presets": ["broken", "candy-cane"], "cooldown": 0})
        ctl = r.ctl
        real = ctl.presets.get

        def get(pid):
            if pid == "broken":
                return {"name": "Broken", "layers": [{"effect": "no-such-effect"}]}
            return real(pid)
        ctl.presets.get = get
        before = ctl.engine.scene
        ctl.visitor_pick("broken", "a")
        ctl.visitor_pick("candy-cane", "b")
        r.tick(1, schedule=False)
        self.assertIsNone(ctl.interrupt)                # nothing half-started
        self.assertIs(ctl.engine.scene, before)
        self.assertEqual([q["id"] for q in ctl.vqueue], ["candy-cane"])
        r.tick(1, schedule=False)
        self.assertEqual(ctl.interrupt["visitor"]["id"], "candy-cane")
        self.assertEqual(ctl.visitor_snow("c"), "snow")

    def test_start_interrupt_failure_leaves_no_state(self):
        ctl = rig(self).ctl
        with self.assertRaises(ValueError):
            ctl.start_interrupt({"layers": [{"effect": "no-such-effect"}]}, 30, "x")
        self.assertIsNone(ctl.interrupt)

    def test_dusk_handoff_gets_a_turn_between_picks(self):
        r = rig(self, {"cooldown": 0, "seconds": 20})
        ctl = r.ctl
        for k in range(3):
            ctl.visitor_pick("warm-white", f"v{k}")
        r.tick(2, schedule=False)
        self.assertTrue(ctl.interrupt.get("visitor"))
        ctl.dusk_intro = {"until": r.clock.t, "program": {"preset": "candy-cane"}, "on": None}
        seen_free = False
        for _ in range(25):
            r.tick(1, schedule=False)
            if ctl.interrupt is None:
                seen_free = True
                break
        self.assertTrue(seen_free)
        self.assertIsNone(ctl.dusk_intro)               # the program took over in the gap
        self.assertEqual((ctl.playing or {}).get("id"), "candy-cane")
        r.tick(20, schedule=False)                      # then the picks resume over the program
        self.assertTrue((ctl.interrupt or {}).get("visitor"))
        self.assertEqual(ctl.interrupt["playing"].get("id"), "candy-cane")

    def test_schedule_change_blocked_by_a_pick_holds_the_next_one(self):
        r = rig(self, {"cooldown": 0, "seconds": 20})
        ctl = r.ctl
        for k in range(2):
            ctl.visitor_pick("warm-white", f"v{k}")
        r.tick(2, schedule=False)
        ctl.interrupt["until"] = r.clock.t + 3
        ctl._visitor_hold()                             # what a blocked schedule check does
        r.tick(5, schedule=False)
        self.assertIsNone(ctl.interrupt)                # the next pick waits for the owner's turn
        r.tick(ctl.VISITOR_HOLD, schedule=False)
        self.assertTrue((ctl.interrupt or {}).get("visitor"))

    def test_wait_during_a_show_counts_the_show(self):
        r = rig(self)
        ctl = r.ctl
        ctl.play_show("Christmas Eve spectacular")
        self.assertTrue(ctl.interrupt)
        res = ctl.visitor_pick("candy-cane", "1.1.1.1")
        you = ctl.visitor_state("1.1.1.1")["you"]
        self.assertGreater(res["wait"], 60)
        self.assertEqual(you["position"], 1)
        self.assertAlmostEqual(you["wait"], res["wait"], delta=2)


class Ipv6Http(unittest.TestCase):
    def setUp(self):
        from goveelights import app_server
        from goveelights.auth import Auth
        self.r = rig(self)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        H = app_server.make_handler(self.r.ctl, lambda: None, Auth(Path(tmp.name) / "auth.json"))
        self.srv = app_server.Server(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    def req(self, method, path, ip, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1], timeout=10)
        h = {"X-Requested-With": "lights", "Content-Type": "application/json", "Cf-Connecting-IP": ip}
        c.request(method, path, json.dumps(body).encode() if body is not None else None, h)
        res = c.getresponse()
        out = res.status, json.loads(res.read())
        c.close()
        return out

    def test_ipv6_visitor_sees_their_pick(self):
        T = "/api/visit/tok-123/"
        s, b = self.req("POST", T + "pick", "2001:db8::1234", {"id": "candy-cane"})
        self.assertEqual(s, 200, b)
        s, st = self.req("GET", T + "state", "2001:db8::1234")
        self.assertEqual(st["you"]["position"], 1)
        s, st = self.req("GET", T + "state", "2001:db8::99")      # same /64: same visitor
        self.assertEqual(st["you"]["position"], 1)
        s, st = self.req("GET", T + "state", "2001:db8:1::1")     # another network: not theirs
        self.assertIsNone(st["you"])
        self.r.tick(2, schedule=False)
        s, st = self.req("GET", T + "state", "2001:db8::1234")
        self.assertTrue(st["you"]["playing"])


if __name__ == "__main__":
    unittest.main()
