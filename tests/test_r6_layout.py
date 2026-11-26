"""Regressions for the round-6 camera-mapping / layout review."""
import copy
import json
import math
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path

sys.path.insert(0, "tests")
from test_rack import house  # noqa: E402

from goveelights import shows  # noqa: E402
from goveelights.config import Config  # noqa: E402
from goveelights.controller import Controller  # noqa: E402
from goveelights.layout import Layout, Run  # noqa: E402
from goveelights.scene import Scene  # noqa: E402

HOUSE = json.loads(Path("layouts/house.json").read_text(encoding="utf-8"))


def camera(L, rot_deg=0.0, scale=12.0, pick=None):
    """Image points of a camera that sees exactly the layout L (y down)."""
    th = math.radians(rot_deg)
    co, si = math.cos(th), math.sin(th)
    out = []
    for p in L.pixels:
        if pick is not None and p.i not in pick:
            continue
        x, y = co * p.x - si * p.y, si * p.x + co * p.y
        out.append([p.i, 600 + scale * x, 400 - scale * y, 1])
    return out


def rebuilt(L, geo, extra=()):
    runs = [Run(mac=r.mac, name=r.name, count=r.count, reverse=r.reverse, hidden=set(r.hidden),
                geometry=geo.get(r.mac, r.geometry)) for r in L.runs] + list(extra)
    return Layout(runs, L.zone_specs)


class FitScaleTests(unittest.TestCase):
    def test_partial_map_of_exact_image_changes_nothing(self):
        L = house()
        only = L.indices("Whole front gable")
        m = shows.fit_mapping(L, camera(L), only)
        for i in only:
            self.assertLess(math.dist(m[i], (L.pixels[i].x, L.pixels[i].y)), 1e-6, L.pixels[i])
        L2 = rebuilt(L, shows.merged_geometry(L, m))
        E = L2.run("Eaves").pixels
        self.assertAlmostEqual(math.dist((E[29].x, E[29].y), (E[30].x, E[30].y)), 1.0, places=2)

    def test_peak_only_keeps_its_size(self):
        L = house()
        only = L.indices("Peak")
        m = shows.fit_mapping(L, camera(L, scale=30), only)
        for i in only:
            self.assertLess(math.dist(m[i], (L.pixels[i].x, L.pixels[i].y)), 1e-6)


class UnshapedRunTests(unittest.TestCase):
    def setUp(self):
        H = house()
        self.L = rebuilt(H, {}, [Run(mac="wled:1", name="Strip", count=30)])
        self.S = self.L.run("Strip")
        th = math.radians(15)
        truth = {p.i: (p.x, p.y) for p in self.L.pixels}
        for p in self.S.pixels:
            truth[p.i] = (-60 + p.ri * math.cos(th), -4 + p.ri * math.sin(th))
        self.truth = truth
        self.pts = [[i, 600 + 12 * x, 400 - 12 * y, 1] for i, (x, y) in truth.items()]

    def test_strip_lands_where_the_camera_saw_it(self):
        for only in (self.L.indices("Strip"), None):
            m = shows.fit_mapping(self.L, self.pts, only)
            for p in self.S.pixels:
                self.assertLess(math.dist(m[p.i], self.truth[p.i]), 1e-6)
            if only is None:
                for p in self.L.pixels:
                    if p.run != "Strip":
                        self.assertLess(math.dist(m[p.i], (p.x, p.y)), 1e-6)

    def test_undecoded_strip_bulbs_follow_the_decoded_ones(self):
        first = self.S.pixels[0].i
        pts = [q for q in self.pts if not (first + 10 <= q[0] <= self.S.pixels[-1].i)]
        geo = shows.merged_geometry(self.L, shows.fit_mapping(self.L, pts))
        knots = {k[0]: (k[1], k[2]) for k in geo["wled:1"]}
        for local in (10, 20, 29):
            self.assertLess(math.dist(knots[local], self.truth[first + local]), 0.01)


class FitRobustnessTests(unittest.TestCase):
    def test_rotated_video_is_refused(self):
        L = house()
        for deg in (90, 30):
            with self.assertRaises(ValueError):
                shows.fit_mapping(L, camera(L, rot_deg=deg))
        shows.fit_mapping(L, camera(L, rot_deg=5))            # a slight tilt is fine

    def test_coincident_and_nonfinite_points(self):
        L = house()
        with self.assertRaises(ValueError):
            shows.fit_mapping(L, [[i, 100, 100, 1] for i in range(40)])
        pts = camera(L)
        bad = [[q[0], q[1], float("nan"), 1] for q in pts[:5]] + [[pts[5][0], "inf", 3, 1]] + pts[6:]
        m = shows.fit_mapping(L, bad)
        self.assertTrue(all(math.isfinite(v) for xy in m.values() for v in xy))
        self.assertNotIn(pts[0][0], m)

    def test_duplicate_knot_index(self):
        self.assertEqual(Run("m", "r", 5, geometry=[[3, 0, 0], [3, 1, 1]]).positions()[0], (-2.0, 1.0))
        pos = Run("m", "r", 6, geometry=[[1, 0, 0], [3, 2, 0], [3, 2, 0]]).positions()
        self.assertEqual(pos[5], (4.0, 0.0))


class _Cfg:
    def __init__(self):
        self.devices = copy.deepcopy(HOUSE["devices"])
        self.data = {"zones": HOUSE["zones"], "devices": self.devices}
        self.lock = threading.RLock()

    def save(self):
        pass


class MapUndoTests(unittest.TestCase):
    def test_undo_steps_back_one_apply(self):
        st = types.SimpleNamespace(cfg=_Cfg())
        st.build_engine = lambda: setattr(st, "engine", types.SimpleNamespace(layout=Layout.from_config(st.cfg)))
        st.build_engine()
        orig = {m: copy.deepcopy(d["geometry"]) for m, d in st.cfg.devices.items()}

        def pts():
            return [[p.i, 600 + 12 * p.x, 400 - 13.2 * p.y, 1] for p in st.engine.layout.pixels]
        Controller.map_apply(st, pts(), "Peak")
        after1 = {m: copy.deepcopy(d["geometry"]) for m, d in st.cfg.devices.items()}
        Controller.map_apply(st, pts(), "Accent")
        self.assertEqual(Controller.map_undo(st), 1)
        for m, d in st.cfg.devices.items():
            want = orig[m] if d["name"] == "Accent" else after1[m]
            self.assertEqual(d["geometry"], want, d["name"])

    def test_import_clears_stale_undo(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = Config(Path(tmp) / "c.json")
            c.data["devices"] = {"aa": {"name": "Eaves", "geometry": [[0, 0, 0]], "geometry_prev": [[0, 5, 5]]}}
            f = Path(tmp) / "lay.json"
            f.write_text(json.dumps({"devices": {"aa": {"geometry": [[0, 1, 1]]}}}), encoding="utf-8")
            c.import_layout(f)
            self.assertNotIn("geometry_prev", c.devices["aa"])


class StaleLayoutCacheTests(unittest.TestCase):
    def test_new_layout_drops_geometry_caches(self):
        sc = Scene.from_dict({"layers": [{"effect": "rainbow", "fx": [{"type": "blur", "params": {"radius": 1}}]}]})
        A = house()
        sc.render(0.0, A)
        B = rebuilt(A, {}, [Run(mac="wled:1", name="Strip", count=30)])
        # simulate B being allocated at A's freed address (id() reuse)
        cc = sc._cache["ctx"]
        for k in [k for k in cc if isinstance(k, tuple) and id(A) in k]:
            cc[tuple(id(B) if v == id(A) else v for v in k)] = cc[k]
        sc.layers[0].state["marker"] = 1
        self.assertEqual(len(sc.render(0.05, B)), B.n)
        self.assertNotIn("marker", sc.layers[0].state)
        sc.layers[0].state["marker"] = 1
        sc.render(0.1, B)                                   # same layout: memory kept
        self.assertIn("marker", sc.layers[0].state)


if __name__ == "__main__":
    unittest.main()
