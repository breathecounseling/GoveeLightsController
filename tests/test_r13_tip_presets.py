"""Round 13: "not all the effects are using the tip box". With the Tip added as the top of
the Peak (Add a run -> Top of the Peak, layout 'extends'), every built-in preset that lights
the Peak also lights the Tip's visible bulbs. (Added the old way, "Somewhere else" copying
the Peak, it only borrows the Peak's colours in presets that leave it dark and sits on a
placeholder line, which is what the owner saw: the Fourth run card now offers a one-tap
"Make it the top of the Peak".)"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from goveelights import addrun  # noqa: E402
from goveelights.scene import Scene  # noqa: E402

from preview import house_layout  # noqa: E402

OWNER = ROOT / "layouts" / "house.json"
LIT = 0.02


def dark_tip_presets(L, tip_name="Tip", frames=30, step=0.4) -> list[str]:
    """Presets where the Peak lights up at some point but the Tip's visible bulbs never do."""
    peak = L.run("Peak")
    tip = L.run(tip_name)
    pk = [p.i for p in peak.pixels]
    tp = [p.i for p in tip.pixels][:addrun.TIP_VISIBLE]
    out = []
    for f in sorted((ROOT / "presets").glob("*.json")):
        sc = Scene.from_dict(json.loads(f.read_text(encoding="utf-8")))
        pl = tl = 0.0
        for k in range(frames):
            cols = sc.render(k * step, L)
            pl = max(pl, max(max(cols[i]) for i in pk))
            tl = max(tl, max(max(cols[i]) for i in tp))
            if tl > LIT:
                break
        if pl > LIT and tl <= LIT:
            out.append(f.stem)
    return out


@unittest.skipUnless(OWNER.exists(), "the owner's layout is not in this copy")
class EveryPeakPresetLightsTheTip(unittest.TestCase):
    def test_as_the_top_of_the_peak(self):
        L = addrun.layout_with_tip(house_layout(OWNER))
        self.assertGreater(len(list((ROOT / "presets").glob("*.json"))), 80)
        self.assertEqual(dark_tip_presets(L), [])

    def test_the_old_way_is_what_the_owner_saw(self):
        """'Somewhere else' copying the Peak: a placeholder line at the bottom of the house,
        lit by whole-house layers there (not as part of the gable) and dark in some."""
        from goveelights.layout import Layout, Run
        H = house_layout(OWNER)
        runs = addrun.copy_runs(H)
        runs.append(Run(mac="TIP", name="Run 4", count=108, geometry=addrun.guess_geometry(H, "other", 108),
                        role="accent", guess=True, follows=H.run("Peak").mac))
        L = Layout(runs, H.zone_specs, H.aliases)
        self.assertIn("july-4th-fireworks", dark_tip_presets(L, "Run 4"))
        self.assertNotIn("TIP", {L.pixels[i].mac for i in L.indices("Peak")})

    def test_the_gable_zones_include_it(self):
        L = addrun.layout_with_tip(house_layout(OWNER))
        tip = {p.i for p in L.run("Tip").pixels}
        for z in ("Zigzag top half", "Whole front gable", "Whole front"):
            self.assertTrue(tip <= set(L.indices(z)), z)
        self.assertTrue(tip <= set(L.indices("Peak")))      # a layer aimed at the Peak reaches it


if __name__ == "__main__":
    unittest.main()
