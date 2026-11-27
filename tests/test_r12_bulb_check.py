"""Bulb check (More -> Bulb check): the walk test takes over the lights and gives the house
back exactly as it was; broken bulbs marked in the config stay dark and are skipped by 1D
motion without moving 2D pictures or letters; a re-hung section can be re-mapped on its
own. Nothing changes when no bulb is marked."""
import copy
import hashlib
import json
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from test_integration import Rig  # noqa: E402
from test_rack import LAYOUT_FILE, house  # noqa: E402

from goveelights import bulbcheck, shows  # noqa: E402
from goveelights import controller as C  # noqa: E402
from goveelights.config import Config  # noqa: E402
from goveelights.engine import to_device_frames  # noqa: E402
from goveelights.layout import Layout, Run, dead_bulbs  # noqa: E402
from goveelights.scene import Scene  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OWNER_LAYOUT = ROOT / "layouts" / "house.json"


def preset(pid):
    return json.loads((ROOT / "presets" / f"{pid}.json").read_text(encoding="utf-8"))


def layout_with(dead: dict | None = None, path=LAYOUT_FILE) -> Layout:
    data = json.loads((ROOT / path).read_text(encoding="utf-8"))
    devs = data["devices"]
    runs = [Run(mac=m, name=d["name"], count=d["pixel_count"], reverse=d.get("reversed", False),
                hidden=set(d.get("hidden", [])), geometry=d.get("geometry", []),
                dead=set((dead or {}).get(d["name"], ()))) for m, d in devs.items()]
    return Layout(sorted(runs, key=lambda r: devs[r.mac].get("order", 0)), data.get("zones"))


def by_phys(L, cols):
    """{(run, physical index): colour}"""
    return {(p.run, p.local): c for p, c in zip(L.pixels, cols)}


def frame_hash(L, d, n=20):
    sc = Scene.from_dict(copy.deepcopy(d))
    hh = hashlib.sha256()
    for k in range(n):
        hh.update(bytes(min(255, max(0, int(c * 255 + .5))) for px in sc.render(k / 10, L) for c in px))
    return hh.hexdigest()


# =========================================================== dead bulbs in the layout
class DeadBulbLayout(unittest.TestCase):
    def test_nothing_marked_changes_nothing(self):
        a, b = house(), layout_with({})
        for pa, pb in zip(a.pixels, b.pixels):
            self.assertEqual((pa.i, pa.local, pa.x, pa.y, pa.nx, pa.ny, pa.u, pa.ru, pa.rnx, pa.rny),
                             (pb.i, pb.local, pb.x, pb.y, pb.nx, pb.ny, pb.u, pb.ru, pb.rnx, pb.rny))
        for pid in ("text-give-thanks", "thanksgiving-harvest", "candy-cane-chase"):
            self.assertEqual(frame_hash(a, preset(pid)), frame_hash(b, preset(pid)), pid)

    def test_config_without_or_with_empty_dead_is_the_same(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        c = Config(tmp / "c.json")
        c.import_layout(ROOT / LAYOUT_FILE, create_missing=True)
        a = Layout.from_config(c)
        for d in c.devices.values():
            d["dead"] = []
        b = Layout.from_config(c)
        self.assertEqual([(p.x, p.y, p.nx, p.ny, p.u) for p in a.pixels], [(p.x, p.y, p.nx, p.ny, p.u) for p in b.pixels])

    def test_dead_bulbs_are_dark_and_skipped(self):
        L0, L = layout_with({}), layout_with({"Eaves": {10, 11}})
        r = L.run("Eaves")
        self.assertEqual(len(r.pixels), len(L0.run("Eaves").pixels) - 2)
        self.assertNotIn(10, [p.local for p in r.pixels])
        self.assertEqual([p.ri for p in r.pixels], list(range(len(r.pixels))))     # continuous: no hole in a chase
        frames = to_device_frames(L, [(1.0, 1.0, 1.0)] * L.n)
        self.assertEqual(frames[r.mac][10], (0, 0, 0))
        self.assertEqual(frames[r.mac][12], (255, 255, 255))

    def test_dead_bulb_at_the_edge_keeps_the_house_frame(self):
        L0 = layout_with({})
        r0 = L0.run("Eaves")
        edge = min(r0.pixels, key=lambda p: p.x).local          # the house's left-most bulb
        L = layout_with({"Eaves": {edge}})
        self.assertEqual((L.minx, L.maxx, L.miny, L.maxy), (L0.minx, L0.maxx, L0.miny, L0.maxy))
        old = {(p.run, p.local): (p.nx, p.ny, p.rnx, p.rny) for p in L0.pixels}
        for p in L.pixels:
            self.assertEqual((p.nx, p.ny, p.rnx, p.rny), old[(p.run, p.local)])

    def test_letters_do_not_shift_round_a_dead_bulb(self):
        L0 = layout_with({})
        peak = L0.run("Peak")
        k = peak.pixels[len(peak.pixels) // 2].local
        L = layout_with({"Peak": {k}})
        d = preset("text-give-thanks")
        d["layers"] = [ly for ly in d["layers"] if ly["effect"] in ("text", "peak_text")]
        self.assertEqual(d["layers"][0]["effect"], "peak_text")      # the honeycomb font (no glints)
        a, b = Scene.from_dict(copy.deepcopy(d)), Scene.from_dict(copy.deepcopy(d))
        for step in range(15):
            ca, cb = by_phys(L0, a.render(step / 5, L0)), by_phys(L, b.render(step / 5, L))
            ca.pop(("Peak", k))
            peak_a = {key: v for key, v in ca.items() if key[0] == "Peak"}
            peak_b = {key: v for key, v in cb.items() if key[0] == "Peak"}
            self.assertEqual(peak_a, peak_b)

    def test_dead_list_is_cleaned(self):
        self.assertEqual(dead_bulbs({"pixel_count": 10, "dead": [3, "4", -1, 10, "x", None]}), {3, 4})
        self.assertEqual(dead_bulbs({}), set())

    @unittest.skipUnless(OWNER_LAYOUT.exists(), "the owner's layout is not in this copy")
    def test_owner_layout_has_no_marks(self):
        data = json.loads(OWNER_LAYOUT.read_text(encoding="utf-8"))
        self.assertFalse(any(d.get("dead") or d.get("rehung") for d in data["devices"].values()))


# =========================================================== the walk test
class WalkTest(unittest.TestCase):
    def rig(self, when=datetime(2026, 11, 26, 17, 0), extra=None):
        r = Rig(self, when, extra or {"schedule": {"enabled": False}})
        return r, r.ctl

    def macs(self, ctl):
        return {r.name: r.mac for r in ctl.engine.layout.runs}

    def test_takes_over_and_gives_back_exactly(self):
        r, ctl = self.rig()
        ctl.play_preset("warm-white", fade=0)
        ctl.set_live(True)
        e = ctl.engine
        e.tick()
        before = copy.deepcopy(e.output.frames)
        scene, playing = e.scene, dict(ctl.playing)
        m = self.macs(ctl)["Eaves"]
        v = ctl.bulbcheck.start(m)
        self.assertEqual((v["mode"], v["first"], v["last"], v["group"]), ("groups", 0, 9, 1))
        e.tick()
        fr = e.output.frames[m]
        self.assertEqual(fr[:10], [bulbcheck.WHITE] * 10)
        self.assertEqual(fr[10], bulbcheck.TODO)
        self.assertEqual(fr[95], (0, 0, 0))                       # a hidden bulb stays dark
        self.assertTrue(all(c == (0, 0, 0) for c in e.output.frames[self.macs(ctl)["Peak"]]))
        self.assertIs(e.scene, scene)                             # the show carries on underneath
        self.assertEqual(ctl.status()["bulb_check"]["name"], "Eaves")
        ctl.bulbcheck.act("next")
        e.tick()
        self.assertEqual(e.output.frames[m][:10], [bulbcheck.DONE] * 10)
        ctl.bulbcheck.stop()
        e.tick()
        self.assertEqual(e.output.frames, before)
        self.assertEqual(ctl.playing, playing)
        self.assertIsNone(ctl.status()["bulb_check"])

    def test_single_bulb_and_marking(self):
        r, ctl = self.rig()
        ctl.set_live(True)
        m = self.macs(ctl)["Eaves"]
        ctl.bulbcheck.start(m)
        ctl.bulbcheck.act("next")
        v = ctl.bulbcheck.act("mode", mode="single")
        self.assertEqual((v["mode"], v["bulb"]), ("single", 10))
        n0 = len(ctl.engine.layout.run(m).pixels)
        v = ctl.bulbcheck.act("dark")
        self.assertEqual(v["bulb"], 11)                           # on to the next one
        self.assertEqual(ctl.cfg.devices[m]["dead"], [10])
        self.assertEqual(len(ctl.engine.layout.run(m).pixels), n0)   # rebuilt after the check, not mid-walk
        ctl.engine.tick()
        self.assertEqual(ctl.engine.output.frames[m][10], bulbcheck.NEAR)   # still lit for the check
        ctl.bulbcheck.act("back")
        v = ctl.bulbcheck.act("ok")
        self.assertNotIn("dead", ctl.cfg.devices[m])
        ctl.bulbcheck.act("dark")
        ctl.bulbcheck.stop()
        self.assertEqual(len(ctl.engine.layout.run(m).pixels), n0 - 1)
        ctl.play_preset("warm-white", fade=0)
        ctl.engine.tick()
        self.assertEqual(ctl.engine.output.frames[m][10], (0, 0, 0))
        saved = json.loads(Path(ctl.cfg.path).read_text(encoding="utf-8"))
        self.assertEqual(saved["devices"][m]["dead"], [10])

    def test_idle_timeout_and_time_limit(self):
        r, ctl = self.rig()
        ctl.set_live(True)
        m = self.macs(ctl)["Peak"]
        ctl.bulbcheck.start(m)
        r.tick(bulbcheck.BulbCheck.IDLE_S - 10)
        self.assertTrue(ctl.bulbcheck.active)
        ctl.bulbcheck.act("next")
        r.tick(bulbcheck.BulbCheck.IDLE_S - 10)
        self.assertTrue(ctl.bulbcheck.active)
        r.tick(20)
        self.assertFalse(ctl.bulbcheck.active)
        self.assertIsNone(ctl.engine.takeover)
        ctl.bulbcheck.start(m)
        for _ in range(int(bulbcheck.BulbCheck.MAX_S // 100) + 1):
            ctl.bulbcheck.act("next")
            r.tick(100)
        self.assertFalse(ctl.bulbcheck.active)

    def test_lights_off_before_check_are_released_after(self):
        r, ctl = self.rig()
        e = ctl.engine
        self.assertFalse(e.live)
        ctl.bulbcheck.start(self.macs(ctl)["Accent"], "sweep", color="green")
        self.assertTrue(e.output.started)
        self.assertFalse(e.live)                                  # the rest of the app still sees "off"
        e.tick()
        acc = e.output.frames[self.macs(ctl)["Accent"]]
        self.assertIn((0, 255, 0), acc)
        ctl.bulbcheck.act("next")
        e.tick()
        self.assertIn((0, 0, 255), e.output.frames[self.macs(ctl)["Accent"]])
        ctl.bulbcheck.stop()
        self.assertFalse(e.output.started)

    def test_lights_switched_off_ends_it(self):
        r, ctl = self.rig()
        ctl.set_live(True)
        ctl.bulbcheck.start("all", "sweep")
        ctl.set_live(False)
        r.tick(1)
        self.assertFalse(ctl.bulbcheck.active)

    def test_showtime_ends_the_check_and_the_show_plays(self):
        st = [{"show": "Thanksgiving tonight", "from": "18:00", "to": "21:00", "every": 60}]
        r, ctl = self.rig(datetime(2026, 11, 26, 17, 59, 0), {"schedule": {"enabled": False}, "showtimes": st})
        ctl.play_preset("warm-white", fade=0)
        ctl.set_live(True)
        ctl.bulbcheck.start(self.macs(ctl)["Eaves"])
        r.tick(50)
        self.assertTrue(ctl.bulbcheck.active)
        ctl.bulbcheck.act("next")
        r.tick(20)
        self.assertFalse(ctl.bulbcheck.active)
        self.assertEqual(ctl.status()["show"], "Thanksgiving tonight")
        self.assertIsNone(ctl.engine.takeover)

    def test_survives_an_engine_rebuild(self):
        r, ctl = self.rig()
        ctl.set_live(True)
        m = self.macs(ctl)["Eaves"]
        ctl.bulbcheck.start(m)
        ctl.build_engine()
        ctl.engine.tick()
        self.assertEqual(ctl.engine.output.frames[m][:10], [bulbcheck.WHITE] * 10)
        ctl.bulbcheck.stop()
        self.assertIsNone(ctl.engine.takeover)

    def test_mapping_and_diagnostics_end_it(self):
        r, ctl = self.rig()
        ctl.set_live(True)
        ctl.bulbcheck.start(self.macs(ctl)["Eaves"])
        ctl.map_start()
        self.assertFalse(ctl.bulbcheck.active)
        with self.assertRaises(ValueError):
            ctl.bulbcheck.start(self.macs(ctl)["Eaves"])
        ctl.map_stop()
        ctl.bulbcheck.start(self.macs(ctl)["Eaves"])
        ctl.enter_diag()
        self.assertFalse(ctl.bulbcheck.active)
        ctl.exit_diag()

    def test_guards(self):
        r, ctl = self.rig()
        m = self.macs(ctl)["Eaves"]
        with self.assertRaises(ValueError):
            ctl.bulbcheck.act("next")                          # not running
        with self.assertRaises(ValueError):
            ctl.bulbcheck.start("nope")
        with self.assertRaises(ValueError):
            ctl.bulbcheck.start(m, "bogus")
        self.assertFalse(ctl.bulbcheck.active)                 # a bad start leaves nothing behind
        self.assertIsNone(ctl.engine.takeover)
        with self.assertRaises(ValueError):
            ctl.bulbcheck.set_dead(m, range(60))               # more than half the run: not bulbs
        self.assertEqual(ctl.bulbcheck.set_dead(m, [5, 5, 200]), [5])

    def test_gap_check_and_rehung_marks(self):
        r, ctl = self.rig()
        ctl.set_live(True)
        m = self.macs(ctl)["Eaves"]
        bc = ctl.bulbcheck
        self.assertEqual(bc.mark_rehung(m, 40, 49, True), [[40, 49]])
        self.assertEqual(bc.mark_rehung(m, 50, 52, True), [[40, 52]])     # touching: joined
        self.assertEqual(bc.mark_rehung(m, 70, 70, True), [[40, 52], [70, 70]])
        self.assertEqual(bc.mark_rehung(m, 70, 70, False), [[40, 52]])
        bc.start(m, "gap", **{"from": 40, "to": 45})
        ctl.engine.tick()
        fr = ctl.engine.output.frames[m]
        self.assertEqual(fr[40:46], [bulbcheck.WHITE, bulbcheck.GAP_B] * 3)
        self.assertEqual(fr[38], bulbcheck.NEAR)
        self.assertEqual(fr[20], bulbcheck.TODO)
        bc.start(m, "groups", at=33)
        v = bc.act("rehung")
        self.assertEqual((v["first"], v["last"]), (30, 39))
        self.assertEqual(rehung_of(ctl, m), [[30, 52]])
        bc.stop()


def rehung_of(ctl, mac):
    return bulbcheck.rehung_ranges(ctl.cfg.devices[mac])


# =========================================================== re-map a re-hung section
class SectionRemap(unittest.TestCase):
    def test_only_the_section_moves(self):
        r = Rig(self, datetime(2026, 11, 26, 12, 0), {"schedule": {"enabled": False}})
        ctl = r.ctl
        L = ctl.engine.layout
        eaves = L.run("Eaves")
        m = eaves.mac
        ctl.bulbcheck.mark_rehung(m, 40, 49, True)
        old = {(p.run, p.local): (p.x, p.y) for p in L.pixels}
        # the camera sees the house as it is, except bulbs 40-49 now hang 2 units lower
        pts = []
        for p in L.pixels:
            y = p.y - (2.0 if p.run == "Eaves" and 40 <= p.local <= 49 else 0.0)
            pts.append([p.i, 100 + p.x * 12.0, 500 - y * 12.0, 1.0])
        res = ctl.map_apply(pts, section={"mac": m, "from": 40, "to": 49})
        self.assertGreater(res["mapped"], 10)
        L2 = ctl.engine.layout
        for p in L2.pixels:
            ox, oy = old[(p.run, p.local)]
            if p.run == "Eaves" and 40 <= p.local <= 49:
                self.assertAlmostEqual(p.y, oy - 2.0, delta=0.05)
                self.assertAlmostEqual(p.x, ox, delta=0.05)
            else:
                self.assertAlmostEqual(p.x, ox, delta=0.05, msg=(p.run, p.local))
                self.assertAlmostEqual(p.y, oy, delta=0.05, msg=(p.run, p.local))
        self.assertEqual(rehung_of(ctl, m), [])               # done: the mark is cleared
        self.assertEqual(ctl.map_undo(), 1)

    def test_fit_without_moved_is_unchanged(self):
        L = house()
        pts = [[p.i, p.x * 10, -p.y * 10] for p in L.pixels]
        only = L.indices("Eaves")
        self.assertEqual(shows.fit_mapping(L, pts, only), shows.fit_mapping(L, pts, only, moved=None))


# =========================================================== the API
class BulbApi(unittest.TestCase):
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

        with urllib.request.urlopen(base + "bulbs", timeout=5) as f:
            info = json.loads(f.read())
        self.assertEqual([x["name"] for x in info["runs"]], ["Eaves", "Peak", "Accent"])
        self.assertIsNone(info["check"])
        m = info["runs"][0]["mac"]
        self.assertEqual(post("bulbs/check", {"action": "start", "mac": m})["check"]["first"], 0)
        self.assertEqual(post("bulbs/check", {"action": "next"})["check"]["group"], 2)
        self.assertEqual(post("bulbs/check", {"action": "mode", "mode": "single"})["check"]["bulb"], 10)
        self.assertEqual(post("bulbs/check", {"action": "dark"})["check"]["bulb"], 11)
        self.assertEqual(post("bulbs/check", {"action": "color", "color": "blue"})["check"]["mode"], "sweep")
        self.assertIsNone(post("bulbs/check", {"action": "stop"})["check"])
        self.assertFalse(post("bulbs/check", {"action": "next"})["ok"])
        self.assertEqual(post("bulbs/dead", {"mac": m, "dead": [3, 10]})["dead"], [3, 10])
        self.assertEqual(post("bulbs/rehung", {"mac": m, "from": 5, "to": 9})["rehung"], [[5, 9]])
        self.assertEqual(post("bulbs/rehung", {"mac": m, "from": 5, "to": 9, "on": False})["rehung"], [])
        self.assertEqual(len(ctl.engine.layout.run(m).pixels), 93 - 2)


if __name__ == "__main__":
    unittest.main()


# =========================================================== review fixes
class ReviewFixes(unittest.TestCase):
    """The check never hides a show, always lets go when the owner says off or picks
    something, honours Blackout, and the half-the-run guard counts visible bulbs."""

    def rig(self, when=datetime(2026, 11, 26, 17, 0), extra=None):
        r = Rig(self, when, extra or {"schedule": {"enabled": False}})
        return r, r.ctl

    def macs(self, ctl):
        return {r.name: r.mac for r in ctl.engine.layout.runs}

    def test_no_check_while_a_show_is_playing(self):
        st = [{"show": "Thanksgiving tonight", "from": "18:00", "to": "21:00", "every": 60}]
        r, ctl = self.rig(datetime(2026, 11, 26, 17, 59, 0), {"schedule": {"enabled": False}, "showtimes": st})
        ctl.play_preset("warm-white", fade=0)
        ctl.set_live(True)
        r.tick(70)
        self.assertEqual(ctl.status()["show"], "Thanksgiving tonight")
        with self.assertRaises(ValueError) as cm:
            ctl.bulbcheck.start(self.macs(ctl)["Eaves"], "groups")
        self.assertIn("show is playing", str(cm.exception))
        self.assertFalse(ctl.bulbcheck.active)
        self.assertIsNone(ctl.engine.takeover)

    def _started_dark(self):
        r, ctl = self.rig()
        self.assertFalse(ctl.engine.live)
        ctl.bulbcheck.start(self.macs(ctl)["Peak"])
        self.assertTrue(ctl.engine.output.started)
        return r, ctl

    def test_lights_off_ends_a_check_started_in_the_dark(self):
        r, ctl = self._started_dark()
        ctl.set_live(False)
        self.assertFalse(ctl.bulbcheck.active)
        self.assertIsNone(ctl.engine.takeover)
        self.assertFalse(ctl.engine.output.started)

    def test_stop_ends_a_check_started_in_the_dark(self):
        r, ctl = self._started_dark()
        ctl.stop()
        self.assertFalse(ctl.bulbcheck.active)
        self.assertFalse(ctl.engine.output.started)
        self.assertIsNone(ctl.status()["bulb_check"])

    def test_picking_something_ends_the_check(self):
        r, ctl = self.rig()
        ctl.set_live(True)
        m = self.macs(ctl)["Eaves"]
        ctl.bulbcheck.start(m)
        ctl.play_preset("warm-white", fade=0)
        self.assertFalse(ctl.bulbcheck.active)
        self.assertIsNone(ctl.engine.takeover)
        ctl.bulbcheck.start(m)
        ctl.play_evolve("warm-white")
        self.assertFalse(ctl.bulbcheck.active)
        ctl.bulbcheck.start(m)
        ctl.play_show("Thanksgiving tonight")
        self.assertFalse(ctl.bulbcheck.active)
        ctl.stop()
        ctl.set_live(True)
        ctl.cfg.data["playlists"] = {"P": {"items": [{"preset": "warm-white", "minutes": 5}]}}
        ctl.bulbcheck.start(m)
        ctl.play_playlist("P")
        self.assertFalse(ctl.bulbcheck.active)

    def test_the_schedule_moving_on_does_not_end_it(self):
        r, ctl = self.rig()
        ctl.set_live(True)
        ctl.bulbcheck.start(self.macs(ctl)["Eaves"])
        ctl.play_preset("warm-white", fade=0, manual=False)     # e.g. a playlist step
        self.assertTrue(ctl.bulbcheck.active)
        ctl.bulbcheck.stop()

    def test_blackout_wins_over_the_check(self):
        r, ctl = self.rig()
        ctl.set_live(True)
        e = ctl.engine
        ctl.bulbcheck.start("all", "sweep", color="white")
        e.tick()
        self.assertIn(bulbcheck.WHITE, e.output.frames[self.macs(ctl)["Peak"]])
        e.blackout = True
        e.tick()
        for fr in e.output.frames.values():
            self.assertTrue(all(c == (0, 0, 0) for c in fr))
        self.assertTrue(all(c == (0.0, 0.0, 0.0) for c in e.last_colors))
        e.blackout = False
        ctl.bulbcheck.stop()

    def test_half_the_run_counts_visible_bulbs(self):
        r, ctl = self.rig()
        m = self.macs(ctl)["Accent"]                            # 108 bulbs, 60 visible
        vis = ctl.bulbcheck.bulbs(ctl.cfg.devices[m])
        self.assertEqual(len(vis), 60)
        with self.assertRaises(ValueError):
            ctl.bulbcheck.set_dead(m, vis[:31])
        self.assertEqual(len(ctl.bulbcheck.set_dead(m, vis[:30])), 30)
        ctl.bulbcheck.set_dead(m, [])

    def test_tonight_count_leaves_out_broken_bulbs(self):
        from goveelights import tonight
        r, ctl = self.rig()
        m = self.macs(ctl)["Eaves"]
        ctl.bulbcheck.set_dead(m, [10, 11])
        c = {c["id"]: c for c in tonight.build(ctl, datetime(2026, 11, 26, 17, 0).astimezone())["checks"]}
        self.assertIn("Eaves 91", c["counts"]["detail"])
        self.assertIn("2 marked broken", c["counts"]["detail"])

    def test_taking_an_added_run_out_restores_its_marks(self):
        from goveelights import addrun
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        c = Config(tmp / "c.json")
        c.import_layout(ROOT / LAYOUT_FILE, create_missing=True)
        new = "AA:BB:CC:DD:EE:FF:01:04"
        c.upsert_discovered({"device": new, "sku": "H705E", "ip": "10.0.0.44", "source_ip": "10.0.0.44"})
        before = copy.deepcopy(c.data)
        addrun.add_run(c, Layout.from_config(c), new, 60, "garage")
        c.device(new)["dead"] = [3]
        c.device(new)["rehung"] = [[5, 9]]
        addrun.remove_run(c, new)
        self.assertEqual(c.data, before)
