"""The fourth run: "Is it answering now?", adding a box as a new run without disturbing the
other three, roles, camera mapping of just the new run, and the opt-in Govee cloud
fallback (rate-limited, own thread, key in its own file). No real network: the LAN is a
fake and the cloud's HTTP is a scripted transport."""
import copy
import json
import os
import shutil
import stat
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
from test_integration import Rig  # noqa: E402
from test_rack import LAYOUT_FILE  # noqa: E402

from goveelights import addrun, cloud, shows  # noqa: E402
from goveelights import controller as C  # noqa: E402
from goveelights.cloud import CloudError, CloudKey, CloudRunOutput, GoveeCloud  # noqa: E402
from goveelights.config import Config  # noqa: E402
from goveelights.layout import Layout, Run  # noqa: E402
from goveelights.scene import Scene  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OWNER_LAYOUT = ROOT / "layouts" / "house.json"
NEW = "AA:BB:CC:DD:EE:FF:01:04"
PRESET_FILES = sorted((ROOT / "presets").glob("*.json"))


def _tmp(test) -> Path:
    d = Path(tempfile.mkdtemp())
    test.addCleanup(shutil.rmtree, d, True)
    return d


def house_cfg(test, layout_file=LAYOUT_FILE) -> Config:
    """A config with the three runs of a layout file plus a fourth box that a scan found
    but that has no bulb count (as an H705E that ignores LAN commands would be)."""
    c = Config(_tmp(test) / "govee_config.json")
    c.import_layout(ROOT / layout_file, create_missing=True)
    for k, d in enumerate(c.devices.values()):
        d["ip"] = f"10.0.0.{k + 10}"
    c.upsert_discovered({"device": NEW, "sku": "H705E", "ip": "10.0.0.44", "source_ip": "10.0.0.44",
                         "wifiVersionSoft": "1.01.17"})
    return c


def frames(d, L, n=10, step=0.1):
    sc = Scene.from_dict(copy.deepcopy(d))
    return [sc.render(k * step, L) for k in range(n)]


def targets(d) -> list:
    out = []
    for layer in d.get("layers") or []:
        t = layer.get("target", "all")
        out += t if isinstance(t, list) else [t]
    return out


def named_params(d) -> set:
    """Zone or run names a layer's params (defaults included) point at, e.g. fireworks'
    launch zone (the porch)."""
    out = set()
    for layer in Scene.from_dict(copy.deepcopy(d)).layers:
        for v in (layer.resolved or {}).values():
            out |= {x for x in (v if isinstance(v, list) else [v]) if isinstance(x, str) and not x.startswith("#")}
    return out


# ================================================================ add a run
class AddRunTests(unittest.TestCase):
    def test_candidates_list_the_box_without_a_count(self):
        c = house_cfg(self)
        L = Layout.from_config(c)
        boxes = addrun.candidates(c, L)
        self.assertEqual([b["mac"] for b in boxes], [NEW])
        self.assertEqual(boxes[0]["sku"], "H705E")
        self.assertEqual(boxes[0]["wifi_fw"], "1.01.17")
        self.assertFalse(boxes[0]["in_show"])

    def test_add_puts_it_last_inside_the_outline_with_its_role(self):
        c = house_cfg(self)
        L3 = Layout.from_config(c)
        res = addrun.add_run(c, L3, NEW, 80, "porch", join="Porch")
        self.assertEqual(res, {"name": "Porch run", "count": 80, "role": "porch", "joined": "Porch", "output": "lan",
                               "follows": None})
        d = c.device(NEW)
        self.assertEqual(d["pixel_count"], 80)
        self.assertGreater(d["order"], max(x["order"] for m, x in c.devices.items() if m != NEW))
        self.assertTrue(d["geometry_guess"])
        L4 = Layout.from_config(c)
        r = L4.run(NEW)
        self.assertEqual((r.role, r.guess, len(r.pixels)), ("porch", True, 80))
        self.assertEqual((L4.minx, L4.maxx, L4.miny, L4.maxy), (L3.minx, L3.maxx, L3.miny, L3.maxy))
        self.assertEqual([p.i for p in L4.pixels[:L3.n]], list(range(L3.n)))      # the others keep their numbers
        self.assertEqual(set(L4.indices("Porch")), set(L3.indices("Porch")) | {p.i for p in r.pixels})
        saved = json.loads(c.path.read_text())
        self.assertEqual(saved["devices"][NEW]["role"], "porch")

    def test_role_stand_ins_use_the_new_run(self):
        c = house_cfg(self)
        addrun.add_run(c, Layout.from_config(c), NEW, 60, "second roofline")
        L = Layout.from_config(c)
        new = {p.i for p in L.run(NEW).pixels}
        # a preset written for another house: "roof" is not a name here, its role is roofline
        got = set(L.layer_indices("Roof"))
        self.assertTrue(new <= got)
        self.assertTrue(set(L.indices("Eaves")) <= got)                     # the name match stays first
        self.assertEqual(L.layer_indices("Eaves"), L.indices("Eaves"))      # names it has: never looked up
        self.assertIn("Second roofline", L.plays_on("Roof"))

    def test_names_are_unique_and_yours_is_kept(self):
        c = house_cfg(self)
        res = addrun.add_run(c, Layout.from_config(c), NEW, 50, "other", name="Porch")   # a zone has that name
        self.assertEqual(res["name"], "Porch 2")
        addrun.remove_run(c, NEW)
        c.device(NEW)["name"] = "Shed"                    # the owner named the box
        self.assertEqual(addrun.add_run(c, Layout.from_config(c), NEW, 50, "garage")["name"], "Shed")

    def test_remove_restores_the_config_exactly(self):
        c = house_cfg(self)
        before = copy.deepcopy(c.data)
        addrun.add_run(c, Layout.from_config(c), NEW, 70, "garage", join="Garage")
        addrun.add_run(c, Layout.from_config(c), NEW, 90, "left eave", join="Flat eaves")    # change it
        z = {x["name"]: x for x in c.data["zones"]}
        self.assertFalse(any(pt["run"] == "Garage run" for pt in z["Garage"]["parts"]))     # the old join is gone
        self.assertEqual(c.device(NEW)["pixel_count"], 90)
        addrun.remove_run(c, NEW)
        self.assertEqual(c.data, before)
        with self.assertRaises(ValueError):
            addrun.remove_run(c, NEW)

    def test_bad_input(self):
        c = house_cfg(self)
        L = Layout.from_config(c)
        for kw in ({"count": 0, "where": "porch"}, {"count": 300, "where": "porch"}, {"count": 50, "where": "moon"},
                   {"count": 50, "where": "porch", "join": "Nope"}, {"count": 50, "where": "porch", "output": "cloud"}):
            with self.assertRaises(ValueError, msg=kw):
                addrun.add_run(c, L, NEW, **kw)
        eaves = next(m for m, d in c.devices.items() if d["name"] == "Eaves")
        with self.assertRaises(ValueError):
            addrun.add_run(c, L, eaves, 50, "porch")          # already a run
        self.assertIsNone(c.device(NEW)["pixel_count"])

    def test_cloud_only_light_from_the_account(self):
        c = house_cfg(self)
        cloud_id = ":".join(["11", "22", "33", "44", "55", "66", "77", "88"])   # built here: no id in the file
        res = addrun.add_run(c, Layout.from_config(c), cloud_id, 40, "porch", output="cloud",
                             cloud={"sku": "H705E", "device": cloud_id, "segments": 15})
        d = c.device(cloud_id)
        self.assertEqual(d["cloud"], {"enabled": True, "sku": "H705E", "device": cloud_id,
                                      "segments": 15, "gradient": True})
        self.assertEqual(res["output"], "cloud")
        self.assertIn(cloud_id, {r.mac for r in Layout.from_config(c).runs})

    def test_suggested_zones(self):
        zones = ["North wall", "Front eave", "Flat eaves", "Porch", "Garage", "Garage peak", "Lower roofline"]
        self.assertEqual(addrun.suggest_zone(zones, "porch"), "Porch")
        self.assertEqual(addrun.suggest_zone(zones, "garage"), "Garage")
        self.assertEqual(addrun.suggest_zone(zones, "second roofline"), "Lower roofline")
        self.assertEqual(addrun.suggest_zone(zones, "left eave"), "Flat eaves")
        self.assertIsNone(addrun.suggest_zone(zones, "other"))


class EveryPresetWithAFourthRun(unittest.TestCase):
    """Every preset renders with a 50-150 bulb fourth run in each place, and a preset
    that lights only named runs and zones the new run didn't join looks exactly the same
    on the other three runs."""

    def check_house(self, layout_file):
        presets = [(f.stem, json.loads(f.read_text())) for f in PRESET_FILES]
        base = house_cfg(self, layout_file)
        L3 = Layout.from_config(base)
        zones = [z["name"] for z in base.data["zones"]]
        n3 = L3.n
        ref = {}
        for k, where in enumerate(addrun.LOCATIONS):
            count = 50 + 20 * k                               # 50, 70 ... 150
            for join in (None, addrun.suggest_zone(zones, where)):
                if k % 2 and join is None:
                    continue                                  # keep the run time sensible
                c = copy.deepcopy(base.data)
                cfg = Config(_tmp(self) / "c.json")
                cfg.data = c
                addrun.add_run(cfg, L3, NEW, count, where, join=join)
                L4 = Layout.from_config(cfg)
                self.assertEqual(L4.n, n3 + count)
                for pid, d in presets:
                    with self.subTest(where=where, join=join, preset=pid):
                        got = frames(d, L4, 6, 0.25)
                        for f in got:
                            self.assertEqual(len(f), L4.n)
                            self.assertTrue(all(0.0 <= v <= 1.0 for px in f for v in px))
                        ts = targets(d)
                        if "all" in ts or join in ts or join in named_params(d) or not all(L3.has(t) for t in ts):
                            continue
                        if pid not in ref:
                            ref[pid] = frames(d, L3, 6, 0.25)
                        self.assertEqual([f[:n3] for f in got], ref[pid])
        return ref

    def test_example_house(self):
        ref = self.check_house(LAYOUT_FILE)
        self.assertGreater(len(ref), 25)                    # most presets aim at named parts

    @unittest.skipUnless(OWNER_LAYOUT.exists(), "the owner's layout is not in this copy")
    def test_owner_house(self):
        self.check_house(OWNER_LAYOUT.relative_to(ROOT))


class FollowAnotherRun(unittest.TestCase):
    """Opt-in: a new run copies a run or zone in presets that leave it dark."""

    def setup(self, follows):
        cfg = house_cfg(self)
        L3 = Layout.from_config(cfg)
        zones = [z["name"] for z in cfg.data["zones"]]
        if follows == "suggest":
            follows = addrun.suggest_follow(L3, zones, "second roofline")
        addrun.add_run(cfg, L3, NEW, 60, "second roofline", follows=follows)
        return cfg, L3, Layout.from_config(cfg)

    def test_presets_that_leave_it_dark_copy_the_run_it_follows(self):
        cfg, L3, L4 = self.setup("suggest")
        self.assertTrue(L4.run(NEW).follows)
        run = L4.run(NEW)
        n3 = L3.n
        lit_before = lit_after = 0
        for f in PRESET_FILES:
            d = json.loads(f.read_text())
            if f.stem == "dusk-to-night":
                continue                                     # reads the clock: not repeatable
            with self.subTest(preset=f.stem):
                plain = frames(d, Layout.from_config(_without_follow(cfg)), 4, 0.5)
                got = frames(d, L4, 4, 0.5)
                # the other runs are exactly as without the setting
                self.assertEqual([x[:n3] for x in got], [x[:n3] for x in plain])
                lit_before += any(max(x[p.i]) > 0.02 for x in plain for p in run.pixels)
                lit_after += any(max(x[p.i]) > 0.02 for x in got for p in run.pixels)
                # a preset that lights the new run itself keeps its own look there
                if any(max(x[p.i]) > 0 for x in plain for p in run.pixels):
                    self.assertEqual(got, plain)
        self.assertGreater(lit_after, lit_before + 5)

    def test_bad_follow_and_undo(self):
        cfg = house_cfg(self)
        L3 = Layout.from_config(cfg)
        before = json.dumps(cfg.data, sort_keys=True)
        for bad in ("Nowhere", NEW):
            with self.assertRaises(ValueError):
                addrun.add_run(cfg, L3, NEW, 60, "porch", follows=bad)
        self.assertEqual(json.dumps(cfg.data, sort_keys=True), before)
        addrun.add_run(cfg, L3, NEW, 60, "porch", follows=L3.runs[0].mac)
        self.assertEqual(Layout.from_config(cfg).run(NEW).follows, L3.runs[0].mac)
        addrun.remove_run(cfg, NEW)
        self.assertNotIn("follows", cfg.devices[NEW])


def _without_follow(cfg):
    c = copy.copy(cfg)
    c.data = copy.deepcopy(cfg.data)
    c.data["devices"][NEW].pop("follows", None)
    return c


class MapOnlyTheNewRun(unittest.TestCase):
    def test_a_guessed_shape_is_not_a_reference_and_gets_replaced(self):
        c = house_cfg(self)
        addrun.add_run(c, Layout.from_config(c), NEW, 40, "porch")
        L = Layout.from_config(c)
        r = L.run(NEW)
        # the camera saw the new run in a slanted line; the rest of the house is unchanged
        pts = [[p.i, 100 + 10 * p.ri, -(50 + 3 * p.ri)] for p in r.pixels]
        for q in L.run("Eaves").pixels[:30]:
            pts.append([q.i, (q.x - L.minx) * 10, -(q.y * 10)])
        meas = shows.fit_mapping(L, pts, L.indices("Porch run"))
        geo = shows.merged_geometry(L, meas)
        self.assertEqual(list(geo), [NEW])                  # only the new run changes
        ys = [k[2] for k in geo[NEW]]
        self.assertNotAlmostEqual(ys[0], ys[-1], places=1)  # its measured slant, not the flat guess


# ================================================================ cloud fallback
class FakeHTTP:
    """Scripted Govee cloud: records every request; replies come from `replies` (a list of
    (status, headers, body) consumed in order, then `default`)."""

    def __init__(self, devices=None):
        self.calls = []
        self.replies = []
        self.devices = devices or []
        self.threads = set()

    def __call__(self, method, url, headers, body, timeout):
        self.threads.add(threading.current_thread().name)
        self.calls.append((method, url, dict(headers), json.loads(body) if body else None))
        if self.replies:
            return self.replies.pop(0)
        if url.endswith("/user/devices"):
            return 200, {}, json.dumps({"code": 200, "message": "success", "data": self.devices}).encode()
        if url.endswith("/device/state"):
            return 200, {}, json.dumps({"code": 200, "payload": {"capabilities": [
                {"type": "devices.capabilities.online", "instance": "online", "state": {"value": True}}]}}).encode()
        return 200, {"x-ratelimit-remaining": "9000"}, b'{"code": 200, "msg": "success"}'

    def controls(self):
        return [c[3]["payload"]["capability"] for c in self.calls if c[1].endswith("/device/control")]


SEG_CAP = {"type": "devices.capabilities.segment_color_setting", "instance": "segmentedColorRgb",
           "parameters": {"dataType": "STRUCT", "fields": [
               {"fieldName": "segment", "dataType": "Array", "options": [{"value": i} for i in range(15)], "required": True},
               {"fieldName": "rgb", "dataType": "INTEGER", "range": {"min": 0, "max": 16777215}}]}}
DEVS = [{"sku": "H705E", "device": NEW, "deviceName": "Fourth run",
         "capabilities": [{"type": "devices.capabilities.on_off", "instance": "powerSwitch"}, SEG_CAP]}]


class CloudKeyTests(unittest.TestCase):
    def test_key_file_is_private_and_masked(self):
        k = CloudKey(_tmp(self) / "govee_cloud.json")
        self.assertFalse(k.present)
        with self.assertRaises(ValueError):
            k.set("not a key!")
        k.set("  abcd1234-ef56-7890-abcd-ef1234567890 ")
        self.assertEqual(k.masked(), "••••••••7890")
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(os.stat(k.path).st_mode), 0o600)
        self.assertEqual(CloudKey(k.path).key, "abcd1234-ef56-7890-abcd-ef1234567890")
        k.clear()
        self.assertFalse(CloudKey(k.path).present)

    def test_usage_resets_each_day(self):
        k = CloudKey(_tmp(self) / "govee_cloud.json")
        k.save_usage({"day": "2000-01-01", "sent": 99})
        self.assertEqual(k.usage()["sent"], 0)


class CloudClientTests(unittest.TestCase):
    KEY = "abcd1234-ef56-7890-abcd-ef1234567890"

    def test_devices_and_control_requests(self):
        http = FakeHTTP(DEVS)
        api = GoveeCloud(lambda: self.KEY, transport=http)
        devs = api.devices()
        self.assertEqual(devs[0]["segments"], 15)
        self.assertEqual(devs[0]["name"], "Fourth run")
        api.control("H705E", NEW, cloud.T_COLOUR, 0xFF0000)
        m, url, hdr, body = http.calls[-1]
        self.assertEqual((m, url), ("POST", "https://openapi.api.govee.com/router/api/v1/device/control"))
        self.assertEqual(hdr["Govee-API-Key"], self.KEY)
        self.assertEqual(body["payload"]["capability"],
                         {"type": "devices.capabilities.color_setting", "instance": "colorRgb", "value": 16711680})
        self.assertTrue(body["requestId"])
        self.assertTrue(api.state("H705E", NEW)["online"])

    def test_errors_never_carry_the_key(self):
        http = FakeHTTP()
        api = GoveeCloud(lambda: self.KEY, transport=http)
        http.replies = [(429, {"retry-after": "75"}, b'{"code": 429}'), (401, {}, b'{"message": "bad key"}'),
                        (500, {}, b'{"message": "boom"}')]
        with self.assertRaises(CloudError) as e:
            api.control("H705E", NEW, cloud.T_POWER, 1)
        self.assertEqual((e.exception.status, e.exception.retry_after), (429, 75.0))
        for status in (401, 500):
            with self.assertRaises(CloudError) as e:
                api.devices()
            self.assertEqual(e.exception.status, status)
            self.assertNotIn(self.KEY, str(e.exception))
        with self.assertRaises(CloudError):
            GoveeCloud(lambda: "", transport=http).devices()     # no key: no request at all
        self.assertEqual(len(http.calls), 3)

    def test_segment_count_variants(self):
        self.assertEqual(cloud.segment_count([SEG_CAP]), 15)
        self.assertEqual(cloud.segment_count([{"instance": "segmentedColorRgb", "parameters": {"fields": [
            {"fieldName": "segment", "elementRange": {"min": 0, "max": 9}}]}}]), 10)
        self.assertEqual(cloud.segment_count([{"instance": "colorRgb"}]), 0)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class CloudOutputTests(unittest.TestCase):
    KEY = "abcd1234-ef56-7890-abcd-ef1234567890"

    def out(self, segments=0):
        self.http = FakeHTTP()
        self.clock = Clock()
        key = CloudKey(_tmp(self) / "govee_cloud.json")
        key.set(self.KEY)
        o = CloudRunOutput(GoveeCloud(lambda: key.key, transport=self.http), key,
                           {NEW: {"name": "Fourth", "hidden": [], "cloud": {"enabled": True, "sku": "H705E",
                                                                            "device": NEW, "segments": segments}}},
                           thread=False, clock=self.clock, horizon=lambda: 600.0)
        o.start()
        return o

    def feed(self, o, colour, n=60, times=20):
        for _ in range(times):
            o.send_frame(NEW, [colour] * n)

    def test_follows_the_colour_and_sends_only_on_change(self):
        o = self.out()
        self.feed(o, (255, 0, 0))
        self.assertEqual(self.http.calls, [])                 # the render thread never sends
        o.pump()
        self.assertEqual([c["instance"] for c in self.http.controls()], ["powerSwitch", "brightness", "colorRgb"])
        self.assertEqual(self.http.controls()[-1]["value"], 0xFF0000)
        self.assertEqual(self.http.controls()[1]["value"], 100)
        self.clock.t += 30
        self.feed(o, (254, 2, 1))                              # the same, as far as a coarse box goes
        o.pump()
        self.assertEqual(len(self.http.controls()), 3)
        self.clock.t += 30
        self.feed(o, (0, 0, 128))                              # dimmer blue
        o.pump()
        sent = self.http.controls()[3:]
        self.assertEqual([(c["instance"], c["value"]) for c in sent], [("brightness", 50), ("colorRgb", 0x0000FF)])

    def test_rounds_are_spaced_out(self):
        o = self.out()
        self.feed(o, (255, 0, 0))
        o.pump()
        n = len(self.http.calls)
        self.clock.t += 5
        self.feed(o, (0, 255, 0))
        o.pump()
        self.assertEqual(len(self.http.calls), n)              # too soon
        self.clock.t += cloud.MIN_ROUND_S
        self.feed(o, (0, 255, 0))
        o.pump()
        self.assertGreater(len(self.http.calls), n)

    def test_coarse_gradient_on_a_segmented_box(self):
        o = self.out(segments=15)
        o.send_frame(NEW, [(255, 0, 0)] * 32 + [(0, 0, 255)] * 28)     # 4 bulbs per segment
        o.pump()
        segs = [c["value"] for c in self.http.controls() if c["instance"] == "segmentedColorRgb"]
        self.assertEqual(len(segs), 2)
        self.assertEqual(sorted(s["rgb"] for s in segs), [0x0000FF, 0xFF0000])
        self.assertEqual(sorted(i for s in segs for i in s["segment"]), list(range(15)))
        # a rainbow is too many colours for a coarse box: at most MAX_COLOURS commands
        self.clock.t += 60
        rb = [(255, 0, 0), (255, 128, 0), (255, 255, 0), (0, 255, 0), (0, 255, 255), (0, 0, 255), (128, 0, 255), (255, 0, 255)]
        o.send_frame(NEW, [rb[i * 8 // 60] for i in range(60)])
        before = len(self.http.controls())
        o.pump()
        self.assertLessEqual(len(self.http.controls()) - before, cloud.MAX_COLOURS + 1)

    def test_per_minute_and_daily_caps(self):
        o = self.out(segments=15)
        o.min_round_s = 0
        cols = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]
        for k in range(40):                                  # a fast-changing show, one pump a second
            self.clock.t += 1
            o.send_frame(NEW, [cols[(k + i // 15) % 4] for i in range(60)])
            o.pump()
            recent = [t for t in o.recent[NEW] if self.clock.t - t < 60]
            self.assertLessEqual(len(recent), cloud.CONTROL_PER_MIN)
        self.assertLess(cloud.CONTROL_PER_MIN, cloud.GOVEE_CONTROL_PER_MIN)
        o.daily_budget = o.usage["sent"]                     # today's budget used up
        self.clock.t += 120
        n = len(self.http.calls)
        self.feed(o, (10, 200, 30))
        o.pump()
        self.assertEqual(len(self.http.calls), n)
        self.assertLess(cloud.DAILY_BUDGET, cloud.GOVEE_DAILY_LIMIT // 2)

    def test_backs_off_on_429_and_stops_on_a_bad_key(self):
        o = self.out()
        self.http.replies = [(429, {"retry-after": "120"}, b"{}")]
        self.feed(o, (255, 0, 0))
        o.pump()
        self.assertEqual(o.status()["paused_for"], 120)
        n = len(self.http.calls)
        for _ in range(10):
            self.clock.t += 10
            self.feed(o, (255, 0, 0))
            o.pump()
        self.assertEqual(len(self.http.calls), n)             # nothing while paused
        self.clock.t += 30
        self.feed(o, (255, 0, 0))
        o.pump()
        self.assertGreater(len(self.http.calls), n)
        self.http.replies = [(401, {}, b"{}")]
        self.clock.t += 60
        self.feed(o, (0, 0, 255))
        o.pump()
        n = len(self.http.calls)
        self.clock.t += 600
        self.feed(o, (0, 255, 0))
        o.pump()
        self.assertEqual(len(self.http.calls), n)
        self.assertIn("rejected", o.status()["stopped"])
        o.key_changed()                                      # a new key pasted: try again
        self.feed(o, (0, 255, 0))
        o.pump()
        self.assertGreater(len(self.http.calls), n)

    def test_low_daily_remaining_waits(self):
        o = self.out()
        self.http.replies = [(200, {"x-ratelimit-remaining": "50"}, b'{"code": 200}')]
        self.feed(o, (255, 0, 0))
        o.pump()
        n = len(self.http.calls)
        self.clock.t += 30
        self.feed(o, (0, 255, 0))
        o.pump()
        self.assertEqual(len(self.http.calls), n)
        self.assertGreaterEqual(o.status()["paused_for"], 600)

    def test_off_when_released_and_dark_frames(self):
        o = self.out()
        self.feed(o, (255, 0, 0))
        o.pump()
        o.stop()
        self.clock.t += 1
        o.pump()
        self.assertEqual(self.http.controls()[-1], {"type": "devices.capabilities.on_off", "instance": "powerSwitch", "value": 0})
        o.start()
        self.clock.t += 30
        self.feed(o, (0, 0, 0))                              # the show is dark there
        o.pump()
        self.assertEqual(self.http.controls()[-1]["value"], 0)

    def test_blink_then_back_to_the_show(self):
        o = self.out()
        self.feed(o, (255, 0, 0))
        o.pump()
        o.blink(NEW)
        self.clock.t += 1
        o.pump()
        self.assertEqual(self.http.controls()[-1]["value"], 0xFFFFFF)
        self.clock.t += 61                                   # (6 commands a minute: the next minute)
        self.feed(o, (255, 0, 0))
        o.pump()
        self.assertEqual(self.http.controls()[-1]["value"], 0xFF0000)

    def test_power_off_goes_out_after_the_daily_budget_is_spent(self):
        o = self.out()
        self.feed(o, (255, 0, 0))
        o.pump()
        o.usage["sent"] = o.daily_budget                     # a busy night used it all
        self.clock.t += 120
        self.feed(o, (0, 255, 0))
        o.pump()
        n = len(self.http.controls())
        self.assertEqual(self.http.controls()[-1]["value"], 0xFF0000)      # colours freeze ...
        o.stop()
        self.clock.t += 1
        o.pump()
        self.assertEqual([(c["instance"], c["value"]) for c in self.http.controls()[n:]], [("powerSwitch", 0)])

    def test_power_off_goes_out_while_waiting_for_govees_reset(self):
        o = self.out()
        self.http.replies = [(200, {"x-ratelimit-remaining": "50"}, b'{"code": 200}')]
        self.feed(o, (255, 0, 0))
        o.pump()
        self.clock.t += 30
        self.feed(o, (0, 255, 0))
        o.pump()
        n = len(self.http.controls())
        o.stop()
        self.clock.t += 1
        o.pump()
        self.assertEqual([(c["instance"], c["value"]) for c in self.http.controls()[n:]], [("powerSwitch", 0)])

    def test_a_busy_show_is_paced_to_last_the_night(self):
        o = self.out(segments=15)
        end = self.clock.t + 7 * 3600.0                      # dusk to midnight
        o.horizon = lambda: max(60.0, end - self.clock.t)
        o.daily_budget = 1000                                # (at full speed this lasts ~4 h)
        cols = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]
        paces, hourly = [], []
        for k in range(7 * 3600 // 5):                       # a fast rainbow, a pump every 5 s
            self.clock.t += 5
            o.send_frame(NEW, [cols[(k // 9 + i // 15) % 4] for i in range(60)])     # moves every 45 s
            o.pump()
            if k % 720 == 719:                               # each hour
                paces.append(o.status()["every_s"])
                hourly.append(o.usage["sent"])
        self.assertLessEqual(o.usage["sent"], o.daily_budget)
        self.assertGreater(o.usage["sent"], o.daily_budget * 0.6)   # but it does use most of it
        self.assertGreater(paces[0], cloud.MIN_ROUND_S)             # slower than full speed
        # every hour, the last one too, still changed colour (no freezing at 5.5 h)
        per_hour = [b - a for a, b in zip([0] + hourly, hourly)]
        self.assertTrue(all(n >= 60 for n in per_hour), per_hour)

    def test_low_budget_sends_the_main_colour_only(self):
        o = self.out(segments=15)
        o.usage["sent"] = o.daily_budget - 50
        o.send_frame(NEW, [(255, 0, 0)] * 32 + [(0, 0, 255)] * 28)
        o.pump()
        self.assertNotIn("segmentedColorRgb", [c["instance"] for c in self.http.controls()])
        self.assertIn("colorRgb", [c["instance"] for c in self.http.controls()])

    def test_reset_header_in_milliseconds_and_long_waits_are_capped(self):
        o = self.out()
        far = (time.time() + 3600) * 1000
        self.http.replies = [(200, {"x-ratelimit-remaining": "50", "x-ratelimit-reset": str(far)}, b'{"code": 200}')]
        self.feed(o, (255, 0, 0))
        o.pump()
        self.assertLess(abs(o.cloud.day_reset - far / 1000), 1)
        self.clock.t += 30
        self.feed(o, (0, 255, 0))
        o.pump()
        self.assertLessEqual(o.status()["paused_for"], 3700)
        o.cloud.day_reset, o.cloud.day_remaining, o.backoff_until = time.time() + 1e9, 10, 0
        self.clock.t += 1
        o.pump()
        self.assertLessEqual(o.status()["paused_for"], cloud.MAX_WAIT_S)

    def test_hidden_bulbs_do_not_darken_it(self):
        o = self.out()
        o.devices[NEW]["hidden"] = list(range(30, 60))
        o.set_devices(dict(o.devices))
        o.send_frame(NEW, [(255, 0, 0)] * 30 + [(0, 0, 0)] * 30)
        o.pump()
        self.assertIn(("brightness", 100), [(c["instance"], c["value"]) for c in self.http.controls()])

    def test_own_thread(self):
        http = FakeHTTP()
        key = CloudKey(_tmp(self) / "k.json")
        key.set(self.KEY)
        o = CloudRunOutput(GoveeCloud(lambda: key.key, transport=http), key,
                           {NEW: {"cloud": {"enabled": True, "sku": "H705E", "device": NEW}}})
        o.start()
        o.send_frame(NEW, [(255, 0, 0)] * 10)
        t0 = time.time()
        while not http.controls() and time.time() - t0 < 5:
            time.sleep(0.05)
        o.stop()
        o.close(timeout=3)
        self.assertEqual(http.threads, {"govee-cloud"})
        self.assertEqual(http.controls()[-1]["value"], 0)    # released: switched off on the way out


# ================================================================ controller and server
class _Seen(dict):
    pass


class _Streamer:
    def __init__(self):
        self.realtime, self.frames, self.enabled = False, [], 0

    def enable(self, *a, **k):
        self.realtime = True
        self.enabled += 1
        return True

    def disable(self, *a, **k):
        self.realtime = False

    def show(self, colors, gradient=False):
        self.frames.append(list(colors))

    send_frame = show


class FakeLan:
    def __init__(self):
        self.answers = {}            # ip -> status reply or None

    def status(self, ip, timeout=1.0):
        return self.answers.get(ip)

    def scan(self, ips):
        pass


class FakeDiag:
    """Diagnostics stand-in: a scan 'reply' marks the box seen when deaf is False."""
    reply_scan = True

    def __init__(self, cfg, bind_ip=""):
        self.cfg = cfg
        self.lan = FakeLan()
        self.streamers = {}
        self.seen = _Seen()
        self.notes = {}

    def scan(self, extra=None):
        if self.reply_scan:
            self.seen[NEW] = time.time()

    def release_all(self):
        pass

    def streamer(self, mac):
        return self.streamers.setdefault(mac, _Streamer())

    def rt(self, mac):
        s = self.streamer(mac)
        s.enable()
        return s

    def note(self, mac, key, value):
        self.notes[(mac, key)] = value


class ControllerFourthRun(unittest.TestCase):
    def rig(self):
        tmp = _tmp(self)
        c = house_cfg(self)
        shutil.copy(c.path, tmp / "cfg.json")
        saved = C.DiagApp
        self.addCleanup(setattr, C, "DiagApp", saved)
        r = Rig(self, datetime(2026, 12, 10, 19, 0).astimezone(), {"schedule": {"enabled": False}}, tmp=str(tmp))
        fake = C.GoveeRazerOutput                            # Rig's memory output, knowing its boxes

        class Out(fake):
            def __init__(self, lan=None, devices=None, *a, **k):
                super().__init__()
                self.devices = devices or {}
        C.GoveeRazerOutput = Out                             # (Rig's cleanup restores the real one)
        r.ctl.build_engine()
        return r

    def setUp(self):
        p = mock.patch.object(C.Controller, "CLOUD_THREAD", False)
        p.start()
        self.addCleanup(p.stop)
        q = mock.patch.object(addrun, "_spawn")     # the test frame's release timer
        q.start()
        self.addCleanup(q.stop)

    def make(self):
        r = self.rig()
        ctl = r.ctl
        ctl.diag = FakeDiag(ctl.cfg)
        ctl.lan = ctl.diag.lan
        return r, ctl

    def test_check_words_for_a_deaf_box(self):
        r, ctl = self.make()
        res = addrun.answer_check(ctl, NEW, scan_wait=0.5, sleep=lambda s: None)
        self.assertTrue(res["scan"])
        self.assertFalse(res["status"])
        self.assertIn("ignores commands", res["verdict"])
        self.assertIn("LAN Control", res["verdict"])
        self.assertIn("1.01.17", res["verdict"])
        FakeDiag.reply_scan = False
        ctl.diag.seen.clear()
        try:
            res = addrun.answer_check(ctl, NEW, scan_wait=0.5, sleep=lambda s: None)
        finally:
            FakeDiag.reply_scan = True
        self.assertIn("not answering on the network at all", res["verdict"])

    def test_check_when_it_answers_sends_a_green_test(self):
        r, ctl = self.make()
        ctl.diag.lan.answers["10.0.0.44"] = {"onOff": 1}
        res = addrun.answer_check(ctl, NEW, scan_wait=0.5, sleep=lambda s: None)
        self.assertTrue(res["status"])
        self.assertIn("answers commands", res["verdict"])
        self.assertEqual(ctl.diag.streamers[NEW].frames[-1], [(0, 255, 0)])
        self.assertEqual(ctl.diag.notes[(NEW, "answer_check")]["status"], "3/3")
        self.assertIn("Realtime works", addrun.razer_answer(ctl, NEW, True))
        self.assertTrue(ctl.diag.notes[(NEW, "razer_works")])

    def test_add_lan_run_and_blink_it_alone(self):
        r, ctl = self.make()
        ctl.play_preset("warm-white")
        ctl.set_live(True)
        res = ctl.add_run({"mac": NEW, "count": 75, "where": "garage", "join": "Garage"})
        self.assertEqual(res["name"], "Garage run")
        e = ctl.engine
        self.assertIn(NEW, [x.mac for x in e.layout.runs])
        self.assertTrue(e.live)
        self.assertIsNone(ctl.cloud_out)                     # no cloud unless asked for
        self.assertIn("blinking", ctl.blink_run(NEW))
        ov = e.overlays["pad:blink"]
        self.assertEqual(ov[1], "add")                       # adds light on that run only
        self.assertEqual([x.target for x in ov[0].layers], ["Garage run"])
        st = ctl.status()
        self.assertTrue(next(x for x in st["runs"] if x["mac"] == NEW)["added"])
        self.assertEqual(ctl.remove_run(NEW), "Garage run")
        self.assertIsNone(ctl.engine.layout.run(NEW))

    def test_cloud_run_routes_to_the_cloud_output(self):
        r, ctl = self.make()
        http = FakeHTTP(DEVS)
        ctl.cloud_api.transport = http
        ctl.cloud_set_key(CloudClientTests.KEY)
        devs = ctl.cloud_devices()
        self.assertEqual(devs[0]["matches"], NEW)
        ctl.add_run({"mac": NEW, "count": 50, "where": "porch", "output": "cloud",
                     "cloud": {"sku": devs[0]["sku"], "device": devs[0]["device"], "segments": devs[0]["segments"]}})
        self.assertIsNotNone(ctl.cloud_out)
        out = ctl.engine.output
        self.assertIs(out.others[NEW], ctl.cloud_out)
        self.assertNotIn(NEW, out.devices)                   # not a realtime (or self-heal) box
        ctl.play_preset("warm-white")
        ctl.set_live(True)
        ctl.engine.tick()
        ctl.cloud_out.pump()
        self.assertIn("colorRgb", [c["instance"] for c in http.controls()])     # warm white: one colour
        st = ctl.cloud_state()
        self.assertTrue(st["key_set"])
        self.assertNotIn(CloudClientTests.KEY, json.dumps(st))
        self.assertNotIn(CloudClientTests.KEY, ctl.cfg.path.read_text())
        self.assertEqual(ctl.cloud_check(NEW)["ok"], True)
        cid = id(ctl.cloud_out)
        ctl.build_engine()
        self.assertEqual(id(ctl.cloud_out), cid)             # one cloud thread for the app's lifetime

    def _cloud_live(self):
        r, ctl = self.make()
        http = FakeHTTP(DEVS)
        ctl.cloud_api.transport = http
        ctl.cloud_set_key(CloudClientTests.KEY)
        d = ctl.cloud_devices()[0]
        ctl.add_run({"mac": NEW, "count": 50, "where": "porch", "output": "cloud",
                     "cloud": {"sku": d["sku"], "device": d["device"], "segments": d["segments"]}})
        ctl.play_preset("warm-white")
        ctl.set_live(True)
        ctl.engine.tick()
        ctl.cloud_out.pump()
        return r, ctl, http

    def test_taking_a_lit_cloud_run_out_switches_it_off(self):
        r, ctl, http = self._cloud_live()
        n0 = len(http.controls())
        ctl.remove_run(NEW)
        ctl.cloud_out.pump(now=ctl.cloud_out.clock() + 100)
        ctl.set_live(False)
        ctl.cloud_out.pump(now=ctl.cloud_out.clock() + 200)
        new = [(c["instance"], c["value"]) for c in http.controls()[n0:]]
        self.assertEqual(new, [("powerSwitch", 0)])
        self.assertEqual(ctl.cloud_out.retiring, {})

    def test_a_cloud_run_never_lit_is_not_touched_when_taken_out(self):
        r, ctl = self.make()
        http = FakeHTTP(DEVS)
        ctl.cloud_api.transport = http
        ctl.cloud_set_key(CloudClientTests.KEY)
        d = ctl.cloud_devices()[0]
        ctl.add_run({"mac": NEW, "count": 50, "where": "porch", "output": "cloud",
                     "cloud": {"sku": d["sku"], "device": d["device"], "segments": d["segments"]}})
        ctl.remove_run(NEW)
        ctl.cloud_out.pump()
        self.assertEqual(http.controls(), [])

    def test_a_failed_change_keeps_the_run(self):
        r, ctl = self.make()
        ctl.add_run({"mac": NEW, "count": 75, "where": "garage"})
        before = json.dumps(ctl.cfg.data, sort_keys=True)
        for bad in ({"join": "Nope"}, {"count": 0}, {"where": "moon"}, {"output": "cloud", "cloud": {}},
                    {"output": "cloud", "cloud": {"sku": "H705E", "device": NEW, "segments": "x"}}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                ctl.add_run({"mac": NEW, "count": 75, "where": "garage", **bad})
            self.assertEqual(json.dumps(ctl.cfg.data, sort_keys=True), before)
        self.assertEqual(ctl.cfg.devices[NEW]["pixel_count"], 75)
        self.assertTrue(ctl.cfg.devices[NEW].get("added_run"))

    def test_server_endpoints(self):
        from goveelights.app_server import Server, make_handler
        from goveelights.auth import Auth
        r, ctl = self.make()
        ctl.cloud_api.transport = FakeHTTP(DEVS)
        auth = Auth(Path(r.tmp) / "auth.json")
        srv = Server(("127.0.0.1", 0), make_handler(ctl, lambda: None, auth))
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        base = f"http://127.0.0.1:{srv.server_address[1]}/api/"

        def call(path, body=None):
            req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                         headers={"X-Requested-With": "lights", "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=5) as resp:
                    return resp.status, json.loads(resp.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read())
        code, st = call("fourth")
        self.assertEqual(code, 200)
        self.assertEqual(st["boxes"][0]["mac"], NEW)
        self.assertEqual(st["suggest"]["porch"], "Porch")
        code, res = call("cloud/key", {"key": CloudClientTests.KEY})
        self.assertEqual(code, 200)
        self.assertNotIn(CloudClientTests.KEY, json.dumps(res))
        self.assertTrue(res["cloud"]["key"].endswith("7890"))
        code, _ = call("fourth")
        self.assertNotIn(CloudClientTests.KEY, json.dumps(_))
        code, res = call("cloud/devices", {})
        self.assertEqual(res["devices"][0]["matches"], NEW)
        code, res = call("run/add", {"mac": NEW, "count": 60, "where": "second roofline", "join": "Lower roofline"})
        self.assertEqual((code, res["name"]), (200, "Second roofline"))
        code, res = call("run/add", {"mac": NEW, "count": 999, "where": "porch"})
        self.assertEqual(code, 400)
        code, res = call("run/blink", {"mac": NEW})
        self.assertEqual(code, 200)
        code, res = call("run/remove", {"mac": NEW})
        self.assertEqual((code, res["name"]), (200, "Second roofline"))
        code, res = call("fourth/solid", {"mac": NEW, "n": 40, "color": "#ff0000"})
        self.assertEqual((code, res["n"]), (200, 40))
        self.assertEqual(ctl.diag.streamers[NEW].frames[-1], [(255, 0, 0)] * 40)
        code, res = call("cloud/key/clear", {})
        self.assertFalse(res["cloud"]["key_set"])


class AppUiStatic(unittest.TestCase):
    def test_card_and_endpoints_are_wired(self):
        html = (ROOT / "goveelights" / "web" / "app.html").read_text(encoding="utf-8")
        for s in ("id=\"fourthCard\"", "Is it answering now?", "'fourth/check'", "'run/add'", "'cloud/key'",
                  "Ladder checklist", "LAN Control", "slow, Govee cloud", "type=\"password\" id=\"frKey\""):
            self.assertIn(s, html)
        self.assertIn("docs/FOURTH_RUN.md", (ROOT / "README.md").read_text(encoding="utf-8"))
        self.assertTrue((ROOT / "docs" / "FOURTH_RUN.md").exists())

    def test_secret_file_is_gitignored(self):
        self.assertIn("govee_cloud.json", (ROOT / ".gitignore").read_text().split())


if __name__ == "__main__":
    unittest.main()
