"""Round 13: per-run colour order ("Colours look wrong?").

The owner saw reds as pink and the Broncos' orange and blue as pink and cyan: what a box
that reads the bytes as R-B-G does with them. Each run can carry "color_order" (RGB, RBG,
GRB, GBR, BRG, BGR); it is applied at the very end of every path that sends colours to
that box (realtime frames, the Govee cloud, WLED, the Fourth run card's test colours and
diagnostics) and never to the phone's preview. Default RGB: byte-identical output.
The phone test lights every run with raw byte 0 then byte 1, the owner says what he sees
per run, and the order follows (goveelights/colororder.py)."""
from __future__ import annotations

import copy
import itertools
import json
import shutil
import sys
import tempfile
import threading
import types
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from test_integration import Rig  # noqa: E402

from goveelights import addrun, colororder  # noqa: E402
from goveelights import protocol as p  # noqa: E402
from goveelights.cloud import CloudRunOutput  # noqa: E402
from goveelights.engine import Engine  # noqa: E402
from goveelights.layout import Layout, Run  # noqa: E402
from goveelights.output import GoveeRazerOutput, WledDdpOutput  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ORANGE, BLUE = (255, 74, 0), (32, 80, 255)      # the Broncos preset's #ff4a00 and #2050ff


def box_shows(order: str, sent) -> tuple:
    """What a box wired `order` lights for the bytes `sent`: it reads byte i as the channel
    order[i] names."""
    out = [0, 0, 0]
    for i, ch in enumerate(order):
        out["RGB".index(ch)] = sent[i]
    return tuple(out)


def seen(order: str, sent) -> str:
    """The colour name a pure byte looks like on such a box."""
    return {(255, 0, 0): "red", (0, 255, 0): "green", (0, 0, 255): "blue"}[box_shows(order, sent)]


class _Lan:
    def __init__(self):
        self.razer, self.msgs = [], []

    def send(self, ip, msg):
        self.msgs.append((ip, msg))

    def send_razer(self, ip, pkt):
        self.razer.append((ip, p.decode_razer(pkt)))


# =========================================================== the permutation itself
class Permutation(unittest.TestCase):
    def test_all_six_orders_put_the_right_colour_on_the_bulb(self):
        self.assertEqual(sorted(colororder.ORDERS), sorted("".join(x) for x in itertools.permutations("RGB")))
        for o in colororder.ORDERS:
            for c in (ORANGE, BLUE, (255, 0, 0), (1, 2, 3), (0, 0, 0)):
                sent = colororder.apply_px(o, c)
                self.assertEqual(box_shows(o, sent), c, (o, c))
                self.assertEqual(colororder.apply(o, [c, c]), [sent, sent])
                # raw_for: the test's raw bytes reach the box unchanged whatever is saved
                self.assertEqual(colororder.apply_px(o, colororder.raw_for(o, c)), c)

    def test_the_owners_report_is_a_green_blue_swap(self):
        # sent as is to an R-B-G box: orange looks pink, blue looks cyan-green
        self.assertEqual(box_shows("RBG", ORANGE), (255, 0, 74))
        self.assertEqual(box_shows("RBG", BLUE), (32, 255, 80))
        self.assertEqual(colororder.apply_px("RBG", ORANGE), (255, 0, 74))
        self.assertEqual(colororder.say("RBG"), "green/blue swapped")
        self.assertEqual(colororder.say("GRB"), "red/green swapped")
        self.assertEqual(colororder.say("BGR"), "red/blue swapped")
        self.assertEqual(colororder.say("GBR"), "all three colours rotated")
        self.assertEqual(colororder.label("RBG"), "R-B-G")

    def test_identity_default_and_junk(self):
        px = [(1, 2, 3), (4, 5, 6)]
        self.assertIs(colororder.apply("RGB", px), px)
        for junk in (None, "", "rgbx", 5, "RRG"):
            self.assertEqual(colororder.norm(junk), "RGB")
        self.assertEqual(colororder.norm("r-b-g"), "RBG")
        self.assertEqual(colororder.of({}), "RGB")
        self.assertEqual(colororder.of({"color_order": "bgr"}), "BGR")
        with self.assertRaises(ValueError):
            colororder.check("RGBW")
        d = {"color_order": "RBG"}
        colororder.set_order(d, "RGB")
        self.assertEqual(d, {})                     # the default leaves the entry as it was

    def test_two_answers_give_every_order(self):
        for o in colororder.ORDERS:
            a, b, c = (seen(o, colororder.RAW[s]) for s in colororder.STEPS)
            self.assertEqual(colororder.derive(a, b), o)
            self.assertEqual(colororder.derive(a, b, c), o)
        with self.assertRaises(ValueError):
            colororder.derive("red", "red")
        with self.assertRaises(ValueError) as cm:
            colororder.derive("red", "blue", "blue")
        self.assertIn("doesn't add up", str(cm.exception))
        with self.assertRaises(ValueError):
            colororder.derive("pink", "blue")


# =========================================================== every output path
class OutputPaths(unittest.TestCase):
    def test_razer_frames_are_permuted_last_and_default_is_byte_identical(self):
        for o in colororder.ORDERS:
            lan = _Lan()
            dev = {"ip": "10.0.0.9"} if o == "RGB" else {"ip": "10.0.0.9", "color_order": o}
            out = GoveeRazerOutput(lan, {"M": dev}, streamers={})
            out.streamers["M"] = types.SimpleNamespace(
                ip="", send_frame=lambda px, gradient=False, lan=lan: lan.send_razer("10.0.0.9", p.razer_frame_packet(px)))
            out.send_frame("M", [ORANGE, BLUE])
            sent = lan.razer[-1][1]["colors"]
            self.assertEqual([box_shows(o, c) for c in sent], [ORANGE, BLUE], o)
            if o == "RGB":
                self.assertEqual(sent, [ORANGE, BLUE])

    def test_engine_preview_shows_the_intended_colours(self):
        runs = [Run(mac="A", name="A", count=3), Run(mac="B", name="B", count=3)]
        L = Layout(runs)
        lan = _Lan()
        devs = {"A": {"ip": "1"}, "B": {"ip": "2", "color_order": "RBG"}}
        out = GoveeRazerOutput(lan, devs, streamers={})
        for m in devs:
            out.streamers[m] = types.SimpleNamespace(
                ip="", send_frame=lambda px, gradient=False, m=m: lan.send_razer(m, p.razer_frame_packet(px)))
        e = Engine(L, out)
        e.live = True
        e.dither = False

        class Solid:
            def render(self, t, layout, master=None, audio=None):
                return [(1.0, 74 / 255, 0.0)] * layout.n
        e.scene = Solid()
        e.tick()
        got = {ip: pk["colors"] for ip, pk in lan.razer}
        self.assertEqual(got["A"], [ORANGE] * 3)
        self.assertEqual(got["B"], [(255, 0, 74)] * 3)         # bytes for an R-B-G box
        self.assertEqual([box_shows("RBG", c) for c in got["B"]], [ORANGE] * 3)
        self.assertEqual({tuple(round(v * 255) for v in c) for c in e.last_colors}, {ORANGE})   # the phone

    def test_takeover_frames_go_through_it_too(self):
        L = Layout([Run(mac="B", name="B", count=2)])
        lan = _Lan()
        out = GoveeRazerOutput(lan, {"B": {"ip": "2", "color_order": "GRB"}}, streamers={})
        out.streamers["B"] = types.SimpleNamespace(
            ip="", send_frame=lambda px, gradient=False: lan.send_razer("B", p.razer_frame_packet(px)))
        e = Engine(L, out)
        e.takeover, e.takeover_live = (lambda t: {"B": [(255, 0, 0), (0, 0, 255)]}), True
        e.tick()
        self.assertEqual([box_shows("GRB", c) for c in lan.razer[-1][1]["colors"]], [(255, 0, 0), (0, 0, 255)])

    def test_wled(self):
        out = WledDdpOutput({"W": {"ip": "127.0.0.1", "port": 9, "color_order": "BGR"}})
        sent = []
        out.sock.close()
        out.sock = types.SimpleNamespace(sendto=lambda pkt, addr: sent.append(pkt))
        out.send_frame("W", [ORANGE])
        self.assertEqual(tuple(sent[0][10:13]), (0, 74, 255))
        out.devices["W"].pop("color_order")
        out._last.clear()
        out.send_frame("W", [ORANGE])
        self.assertEqual(tuple(sent[1][10:13]), ORANGE)

    def test_govee_cloud_colour_and_segments(self):
        d = {"cloud": {"enabled": True, "sku": "H705E", "device": "X", "segments": 2}, "color_order": "RBG"}
        out = CloudRunOutput(types.SimpleNamespace(), None, {"X": d}, thread=False)
        out.send_frame("X", [ORANGE, ORANGE, BLUE, BLUE])
        self.assertEqual(out.acc["X"], [[510, 0, 148, 2], [64, 510, 160, 2]])
        d.pop("color_order")
        out.acc.clear()
        out.send_frame("X", [ORANGE, ORANGE, BLUE, BLUE])
        self.assertEqual(out.acc["X"], [[510, 148, 0, 2], [64, 160, 510, 2]])

    def test_fourth_run_test_colours(self):
        shown = []
        s = types.SimpleNamespace(show=lambda cols, gradient=False: shown.append(cols))
        cfg = types.SimpleNamespace(devices={"T": {"color_order": "RBG"}})
        ctl = types.SimpleNamespace(engine=None, cfg=cfg, diag=types.SimpleNamespace(rt=lambda m: s))
        addrun.solid(ctl, "T", 3, "#ff4a00")
        self.assertEqual(shown[-1], [(255, 0, 74)] * 3)
        cfg.devices["T"] = {}
        addrun.solid(ctl, "T", 2, "#ff4a00")
        self.assertEqual(shown[-1], [ORANGE] * 2)

    def test_diagnostics_colour_tests(self):
        from goveelights.diag_server import App
        me = types.SimpleNamespace(cfg=types.SimpleNamespace(devices={"T": {"color_order": "GBR"}}))
        self.assertEqual(box_shows("GBR", App.fix(me, "T", ORANGE)), ORANGE)
        self.assertEqual(App.fix(me, "T", ORANGE, raw=True), ORANGE)
        me.cfg.devices["T"] = {}
        self.assertEqual(App.fix(me, "T", ORANGE), ORANGE)


# =========================================================== the phone test
class ColourTestFlow(unittest.TestCase):
    def rig(self, when=datetime(2026, 11, 26, 17, 0), extra=None):
        r = Rig(self, when, extra or {"schedule": {"enabled": False}})
        return r, r.ctl

    def macs(self, ctl):
        return {r.name: r.mac for r in ctl.engine.layout.runs}

    def wire(self, ctl, wiring):
        """What each run's box shows for the frame the engine sends (the output applies
        the saved order: done here, since the rig's output only records frames)."""
        e = ctl.engine
        e.tick()
        out = {}
        for name, mac in self.macs(ctl).items():
            fr = colororder.apply(colororder.of(ctl.cfg.devices[mac]), e.output.frames[mac])
            d = ctl.cfg.devices[mac]
            lit = [box_shows(wiring.get(name, "RGB"), c) for k, c in enumerate(fr) if k not in set(d.get("hidden") or [])]
            out[name] = lit
        return out

    def test_finds_the_order_saves_it_and_shows_before_and_after(self):
        r, ctl = self.rig()
        ctl.play_preset("warm-white", fade=0)
        ctl.set_live(True)
        e = ctl.engine
        e.tick()
        before_frames = copy.deepcopy(e.output.frames)
        scene = e.scene
        m = self.macs(ctl)
        wiring = {"Peak": "RBG", "Accent": "GRB"}         # what the boxes really do
        v = ctl.colourtest.start()
        self.assertEqual(v["step"], "red")
        self.assertIs(e.scene, scene)                       # the show carries on underneath
        self.assertEqual(ctl.status()["colour_test"]["step"], "red")
        for step in ("red", "green", "blue"):
            if step != "red":
                ctl.colourtest.step(step)
            looks = self.wire(ctl, wiring)
            for name in m:
                cols = set(looks[name])
                self.assertEqual(len(cols), 1, (step, name))
                ans = {(255, 0, 0): "red", (0, 255, 0): "green", (0, 0, 255): "blue"}[cols.pop()]
                ctl.colourtest.answer(m[name], ans)
        v = ctl.colourtest.finish()
        self.assertEqual(v["step"], "compare")
        self.assertEqual(v["result"], {m["Eaves"]: "RGB", m["Peak"]: "RBG", m["Accent"]: "GRB"})
        self.assertEqual(v["problems"], {})
        self.assertNotIn("color_order", ctl.cfg.devices[m["Eaves"]])
        self.assertEqual(ctl.cfg.devices[m["Peak"]]["color_order"], "RBG")
        saved = json.loads(Path(ctl.cfg.path).read_text())
        self.assertEqual(saved["devices"][m["Accent"]]["color_order"], "GRB")
        # after: every box shows true orange and blue stripes
        looks = self.wire(ctl, wiring)
        for name in m:
            self.assertEqual(set(looks[name]), {ORANGE, BLUE}, name)
            self.assertEqual(looks[name][:6], [ORANGE] * 6)
        # before: what the old (normal) order made of them on the house
        ctl.colourtest.show("before")
        looks = self.wire(ctl, wiring)
        self.assertEqual(set(looks["Eaves"]), {ORANGE, BLUE})
        self.assertEqual(set(looks["Peak"]), {box_shows("RBG", ORANGE), box_shows("RBG", BLUE)})
        self.assertEqual(looks["Peak"][0], (255, 0, 74))    # the pink the owner saw
        st = ctl.status()
        self.assertEqual({x["name"]: x["color_order"] for x in st["runs"]}, {"Eaves": "RGB", "Peak": "RBG", "Accent": "GRB"})
        # undo puts the old orders back; Done gives the house back
        ctl.colourtest.undo()
        self.assertNotIn("color_order", ctl.cfg.devices[m["Peak"]])
        ctl.colourtest.finish()                             # (the same answers again)
        ctl.colourtest.stop()
        e.tick()
        self.assertEqual(e.output.frames, before_frames)    # the engine's frames are as they were
        self.assertIsNone(ctl.status()["colour_test"])
        self.assertIsNone(e.takeover)

    def test_other_or_missing_answers_leave_a_run_alone(self):
        r, ctl = self.rig()
        ctl.set_live(True)
        m = self.macs(ctl)
        ctl.cfg.devices[m["Peak"]]["color_order"] = "BGR"
        ctl.colourtest.start()
        e = ctl.engine
        e.tick()
        # the raw test bytes reach the Peak's box even though BGR is saved
        self.assertEqual(colororder.apply("BGR", e.output.frames[m["Peak"]])[0], (255, 0, 0))
        ctl.colourtest.answer(m["Eaves"], "other")
        ctl.colourtest.answer(m["Accent"], "red")
        ctl.colourtest.step("green")
        ctl.colourtest.answer(m["Eaves"], "green")
        ctl.colourtest.answer(m["Accent"], "red")
        v = ctl.colourtest.finish()
        self.assertEqual(v["result"], {})
        self.assertEqual(set(v["problems"]), set(m.values()))
        self.assertIn("can't look red", v["problems"][m["Accent"]])
        self.assertEqual(ctl.cfg.devices[m["Peak"]]["color_order"], "BGR")
        with self.assertRaises(ValueError):
            ctl.colourtest.answer(m["Eaves"], "red")       # the compare picture is showing
        with self.assertRaises(ValueError):
            ctl.colourtest.answer(m["Eaves"], "pink")

    def test_refused_during_a_show_and_ends_like_the_bulb_check(self):
        st = [{"show": "Thanksgiving tonight", "from": "18:00", "to": "21:00", "every": 60}]
        r, ctl = self.rig(datetime(2026, 11, 26, 17, 59, 0), {"schedule": {"enabled": False}, "showtimes": st})
        ctl.play_preset("warm-white", fade=0)
        ctl.set_live(True)
        r.tick(70)
        with self.assertRaises(ValueError) as cm:
            ctl.colourtest.start()
        self.assertIn("show is playing", str(cm.exception))
        self.assertIsNone(ctl.engine.takeover)

    def test_lights_off_stop_pick_and_no_tap_end_it(self):
        r, ctl = self.rig()
        # started in the dark: the boxes switch on for it, and go off again after
        self.assertFalse(ctl.engine.live)
        ctl.colourtest.start()
        self.assertTrue(ctl.engine.output.started)
        ctl.set_live(False)
        self.assertFalse(ctl.colourtest.active)
        self.assertFalse(ctl.engine.output.started)
        ctl.colourtest.start()
        ctl.stop()
        self.assertFalse(ctl.colourtest.active)
        ctl.set_live(True)
        ctl.colourtest.start()
        ctl.play_preset("warm-white", fade=0)
        self.assertFalse(ctl.colourtest.active)
        ctl.colourtest.start()
        r.tick(ctl.colourtest.IDLE_S + 5)
        self.assertFalse(ctl.colourtest.active)
        self.assertIsNone(ctl.engine.takeover)

    def test_set_directly_reset_all_and_add_run_keys(self):
        r, ctl = self.rig()
        m = self.macs(ctl)
        res = ctl.set_color_order(m["Peak"], "r-b-g")
        self.assertEqual((res["order"], res["label"]), ("RBG", "R-B-G"))
        with self.assertRaises(ValueError):
            ctl.set_color_order(m["Peak"], "RGBW")
        with self.assertRaises(ValueError):
            ctl.set_color_order("nope", "RGB")
        ctl.calibrate({"mac": m["Peak"], "nudge": "warmer"})
        ctl.calibrate({"reset": "all"})                     # taste, not wiring: the order stays
        self.assertEqual(ctl.cfg.devices[m["Peak"]]["color_order"], "RBG")
        self.assertIn("color_order", addrun._PREV_KEYS)
        ctl.set_color_order(m["Peak"], "RGB")
        self.assertNotIn("color_order", ctl.cfg.devices[m["Peak"]])


class ColourApi(unittest.TestCase):
    def test_endpoints(self):
        from goveelights.app_server import Server, make_handler
        from goveelights.auth import Auth
        r = Rig(self, datetime(2026, 11, 26, 17, 0), {"schedule": {"enabled": False}})
        ctl = r.ctl
        srv = Server(("127.0.0.1", 0), make_handler(ctl, lambda: None, Auth(Path(r.tmp) / "auth.json")))
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        base = f"http://127.0.0.1:{srv.server_address[1]}/api/"

        def post(a, body):
            req = urllib.request.Request(base + a, json.dumps(body).encode(),
                                         {"Content-Type": "application/json", "X-Requested-With": "lights"})
            try:
                with urllib.request.urlopen(req, timeout=5) as f:
                    return json.loads(f.read())
            except urllib.error.HTTPError as e:
                return json.loads(e.read())

        with urllib.request.urlopen(base + "colours", timeout=5) as f:
            info = json.loads(f.read())
        self.assertEqual([x["name"] for x in info["runs"]], ["Eaves", "Peak", "Accent"])
        self.assertEqual(info["orders"], list(colororder.ORDERS))
        self.assertIsNone(info["test"])
        pk = info["runs"][1]["mac"]
        self.assertEqual(post("colours/test", {"action": "start"})["test"]["step"], "red")
        self.assertEqual(post("colours/test", {"action": "answer", "mac": pk, "saw": "red"})["test"]["answers"][pk],
                         {"red": "red"})
        self.assertEqual(post("colours/test", {"action": "step", "to": "green"})["test"]["step"], "green")
        post("colours/test", {"action": "answer", "mac": pk, "saw": "blue"})
        v = post("colours/test", {"action": "finish"})
        self.assertEqual(v["test"]["result"], {pk: "RBG"})
        self.assertEqual(v["runs"][1]["label"], "R-B-G")
        self.assertEqual(post("colours/test", {"action": "show", "which": "before"})["test"]["showing"], "before")
        self.assertIsNone(post("colours/test", {"action": "stop"})["test"])
        self.assertFalse(post("colours/test", {"action": "finish"})["ok"])
        self.assertEqual(post("colours/order", {"mac": pk, "order": "GRB"})["order"], "GRB")
        self.assertFalse(post("colours/order", {"mac": pk, "order": "XYZ"})["ok"])
        self.assertEqual(ctl.cfg.devices[pk]["color_order"], "GRB")


class KeptApart(unittest.TestCase):
    """Review fixes: the colour order test and the Bulb check both own the boxes, so one
    is refused while the other runs (nobody is ended silently); Match the runs is an
    overlay: either test ends it, and it is refused while one runs. Lights borrowed for
    a forced celebration count as off."""
    def rig(self, when=datetime(2026, 11, 26, 17, 0)):
        r = Rig(self, when, {"schedule": {"enabled": False}})
        return r, r.ctl

    def test_one_test_on_the_lights_at_a_time(self):
        r, ctl = self.rig()
        ctl.set_live(True)
        eaves = ctl.engine.layout.runs[0].mac
        ctl.bulbcheck.start(eaves)
        with self.assertRaises(ValueError) as cm:
            ctl.colourtest.start()
        self.assertIn("bulb check", str(cm.exception).lower())
        self.assertTrue(ctl.bulbcheck.active)
        self.assertEqual(ctl.engine.takeover, ctl.bulbcheck.frames)
        ctl.bulbcheck.stop("done")
        ctl.colourtest.start()
        with self.assertRaises(ValueError) as cm:
            ctl.bulbcheck.start(eaves)
        self.assertIn("colour order test", str(cm.exception).lower())
        with self.assertRaises(ValueError) as cm:
            ctl.match("start", "red")
        self.assertIn("colour order test", str(cm.exception).lower())
        self.assertTrue(ctl.colourtest.active)
        self.assertEqual(ctl.engine.takeover, ctl.colourtest.frames)
        ctl.colourtest.stop()
        ctl.match("start", "red")                           # Match is an overlay: the test ends it
        ctl.colourtest.start()
        self.assertIsNone(ctl.match_state)
        ctl.colourtest.stop()

    def test_visitor_picks_wait_for_it(self):
        r = Rig(self, datetime(2026, 11, 26, 17, 30),
                {"schedule": {"enabled": False}, "visitor": {"enabled": True, "presets": ["candy-cane-chase", "warm-white"]}})
        ctl = r.ctl
        ctl.play_preset("warm-white")
        ctl.set_live(True)
        ctl.colourtest.start()
        ctl.visitor_pick("candy-cane-chase", "phone-A")
        r.tick(5)
        self.assertIsNone(ctl.interrupt)
        self.assertTrue(ctl.colourtest.active)

    def test_lights_borrowed_for_a_forced_celebration_go_off_after(self):
        r, ctl = self.rig(datetime(2026, 11, 29, 14, 0))
        ctl.play_preset("warm-white", manual=False)
        self.assertFalse(ctl.engine.live)
        now = r.clock.t
        ctl._pending.append((now, "touchdown", ["#fc4c02", "#0a2343"]))
        ctl._pending_tick(now, force_live=True)
        self.assertTrue(ctl.engine.live)                    # on only for the celebration
        ctl.colourtest.start()
        self.assertFalse(ctl.colourtest.st["live_before"])
        for _ in range(12):
            r.tick(5)
            ctl.colourtest.answer(ctl.engine.layout.runs[0].mac, "red")
        self.assertIsNone(ctl.interrupt)
        self.assertTrue(ctl.colourtest.active, "the celebration ending is not the owner switching off")
        self.assertTrue(ctl.engine.output.started, "the test still needs the boxes")
        ctl.colourtest.stop()
        self.assertFalse(ctl.engine.live)
        self.assertFalse(ctl.engine.output.started, "borrowed lights go off again")

    def test_rebuild_mid_test_with_the_lights_off_still_ends_dark(self):
        r, ctl = self.rig()
        self.assertFalse(ctl.engine.live)
        ctl.colourtest.start()
        ctl.build_engine()                                  # e.g. the Tip move or Change
        r.tick(1)
        self.assertTrue(ctl.colourtest.active)
        self.assertEqual(ctl.engine.takeover, ctl.colourtest.frames)
        self.assertTrue(ctl.engine.output.started)
        ctl.colourtest.stop()
        self.assertFalse(ctl.engine.output.started)
        self.assertFalse(ctl.engine.live)


class NudgesSetOnSwappedChannels(unittest.TestCase):
    """Match-the-runs gains act before the colour order: a run tuned while its green and
    blue were swapped needs a fresh look once the order is fixed."""
    def test_finish_and_set_directly_flag_a_tuned_run(self):
        r = Rig(self, datetime(2026, 11, 26, 17, 0), {"schedule": {"enabled": False}})
        ctl = r.ctl
        m = {x.name: x.mac for x in ctl.engine.layout.runs}
        ctl.calibrate({"mac": m["Peak"], "nudge": "warmer"})
        ctl.set_live(True)
        ctl.colourtest.start()
        for step, saw in (("red", "red"), ("green", "blue")):
            if step != "red":
                ctl.colourtest.step(step)
            for name, mac in m.items():
                ctl.colourtest.answer(mac, saw if name in ("Peak", "Accent") else step)
        v = ctl.colourtest.finish()
        self.assertEqual(v["result"][m["Peak"]], "RBG")
        self.assertIn(m["Peak"], v["nudges"])
        self.assertIn("Match the runs", v["nudges"][m["Peak"]])
        self.assertNotIn(m["Accent"], v["nudges"])         # changed, but never nudged
        self.assertNotIn(m["Eaves"], v["nudges"])          # nudged? no; unchanged anyway
        ctl.colourtest.stop()
        res = ctl.set_color_order(m["Peak"], "RGB")
        self.assertIn("Match the runs", res["note"])
        self.assertEqual(ctl.set_color_order(m["Peak"], "RGB")["note"], "")   # no change: no note
        self.assertEqual(ctl.set_color_order(m["Accent"], "GRB")["note"], "")  # not tuned


# =========================================================== Take it out / Change
class AddedRunKeepsItsWiring(unittest.TestCase):
    def setUp(self):
        from goveelights.config import Config
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        self.cfg = Config(tmp / "c.json")
        self.cfg.import_layout(ROOT / "layouts" / "house.json", create_missing=True)
        self.cfg.upsert_discovered({"device": "TIP", "sku": "H705E", "ip": "10.0.0.40", "source_ip": "10.0.0.40"})

    def test_change_keeps_it_and_take_it_out_restores(self):
        cfg = self.cfg
        L = Layout.from_config(cfg)
        addrun.add_run(cfg, L, "TIP", 108, "other", name="Tip", follows=L.run("Peak").mac)
        cfg.devices["TIP"]["color_order"] = "RBG"           # fixed after it was added
        L = Layout.from_config(cfg)
        self.assertTrue(addrun.tip_offer(cfg, L, "TIP"))
        res = addrun.to_peak_top(cfg, L, "TIP")              # one tap: the top of the Peak
        self.assertEqual((res["name"], res["tip"]["visible"], res["tip"]["host"]), ("Tip", 12, "Peak"))
        d = cfg.devices["TIP"]
        self.assertEqual((d["location"], d["extends"], d["color_order"]), ("peak top", L.run("Peak").mac, "RBG"))
        self.assertNotIn("follows", d)
        L = Layout.from_config(cfg)
        self.assertFalse(addrun.tip_offer(cfg, L, "TIP"))   # it is the top now
        self.assertEqual(len(L.run("Tip").pixels), 12)
        addrun.remove_run(cfg, "TIP")
        self.assertNotIn("color_order", cfg.devices["TIP"])  # as before it was added
        self.assertIsNone(cfg.devices["TIP"].get("pixel_count"))

    def test_default_name_becomes_tip_and_no_offer_without_a_zigzag(self):
        cfg = self.cfg
        L = Layout.from_config(cfg)
        addrun.add_run(cfg, L, "TIP", 108, "other")
        self.assertEqual(cfg.devices["TIP"]["name"], "Run 4")
        L = Layout.from_config(cfg)
        self.assertEqual(addrun.to_peak_top(cfg, L, "TIP")["name"], "Tip")
        self.assertFalse(addrun.tip_offer(cfg, None, "TIP"))
        L = Layout.from_config(cfg)
        addrun.add_run(cfg, L, "TIP", 50, "porch")
        self.assertFalse(addrun.tip_offer(cfg, Layout.from_config(cfg), "TIP"))   # a porch run: no offer


if __name__ == "__main__":
    unittest.main()
