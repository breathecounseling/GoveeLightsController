"""Regression tests for the round-3 web fixes: showtime dates / 'only when on', blank cue
fields, the schedule default that isn't in the dropdown, the Create tab not starting a scene,
and the visitor page's status line and pick replies while a show is on."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "goveelights" / "web"
sys.path.insert(0, str(Path(__file__).resolve().parent))
SCRIPT_RE = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S | re.I)


def page_js(page: str) -> str:
    return "\n".join(SCRIPT_RE.findall((WEB / page).read_text(encoding="utf-8")))


def extract(js: str, name: str) -> str:
    """The source of one top-level `function name(...) {...}` or `const name = ...;` definition."""
    m = re.search(r"^(?:async\s+)?function\s+%s\s*\(" % re.escape(name), js, re.M)
    if m:
        i = js.index("{", js.index(")", m.end()))
        depth = 0
        for j in range(i, len(js)):
            depth += {"{": 1, "}": -1}.get(js[j], 0)
            if depth == 0:
                return js[m.start():j + 1]
        raise AssertionError(f"unbalanced function {name}")
    m = re.search(r"^const\s+%s\s*=" % re.escape(name), js, re.M)
    if not m:
        raise AssertionError(f"{name} not found")
    depth, j = 0, m.end()
    while j < len(js):
        ch = js[j]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch in "\n;" and depth == 0:
            return js[m.start():j] + ";"
        j += 1
    return js[m.start():]


def run_node(code: str):
    r = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=60)
    if r.returncode:
        raise AssertionError(r.stderr)
    return json.loads(r.stdout.strip().splitlines()[-1])


APP = page_js("app.html")
VISIT = page_js("visit.html")


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestAppFunctions(unittest.TestCase):
    def defs(self, *names):
        return "\n".join(extract(APP, n) for n in names)

    def test_showtime_dates_parse_and_display(self):
        out = run_node(self.defs("stDates", "parseStDates") + """
console.log(JSON.stringify([parseStDates('12-31'), parseStDates(' 1-1, 12-31 '), parseStDates(''), parseStDates('13-01'),
  parseStDates('dec 31'), stDates({date:'12-31'}), stDates({dates:['12-24','12-25']}), stDates({})]))""")
        self.assertEqual(out, [["12-31"], ["01-01", "12-31"], [], None, None, ["12-31"], ["12-24", "12-25"], []])

    def test_showtime_editor_has_date_and_only_when_on(self):
        src = extract(APP, "drawShowtimes")
        self.assertIn('data-f="date"', src)
        self.assertIn('data-f="only_when_on"', src)
        self.assertIn("st.only_when_on!==false", src)     # unset means on (the controller's default)
        self.assertIn("delete st.date", src)              # an empty date clears it

    def test_cue_secs_matches_the_server(self):
        from goveelights.controller import Controller
        cases = [{}, {"seconds": 0}, {"seconds": 45}, {"seconds": ""}, {"seconds": None}, {"seconds": 0.5}]
        out = run_node(self.defs("cueSecs") + f"\nconsole.log(JSON.stringify({json.dumps(cases)}.map(cueSecs)))")
        self.assertEqual(out, [Controller._cue_secs(c) for c in cases])

    def test_cue_blank_fields_are_not_saved(self):
        src = extract(APP, "shEditor")
        self.assertIn("f === 'countdown_to' && !i.value", src)       # a cleared countdown time is refused
        self.assertIn("delete c[f]", src)                            # a blank number goes back to the default
        self.assertNotIn("+i.value : i.value", src)                  # the old '' -> 0 path is gone
        out = run_node(self.defs("cuesBad") + """
console.log(JSON.stringify([cuesBad({cues:[{preset:'a'},{countdown_to:'00:00'}]}), cuesBad({cues:[{preset:'a'},{countdown_to:'', seconds:60}]})]))""")
        self.assertEqual(out, [-1, 1])
        self.assertIn("cuesBad(sh)", extract(APP, "saveExtras"))

    def test_schedule_default_missing_and_extra_keys_survive(self):
        harness = """
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const opt = (v, l, s) => `<option value="${esc(v)}" ${s?'selected':''}>${esc(l)}</option>`;
let PL = {Fall: ['a']}, PRESETS = [{id:'warm-white', name:'Warm white'}];
""" + self.defs("progKey", "progOpts", "progVal", "keepProg") + """
const sel = h => (h.match(/<option value="([^"]*)" selected/) || [])[1];
const miss = progOpts({playlist:'Christmas'}), ok = progOpts({playlist:'Fall'});
const ev = {evolve:'warm-white', minutes:10};
console.log(JSON.stringify([sel(miss), /missing: Christmas/.test(miss), sel(ok), /missing/.test(ok),
  keepProg({playlist:'Christmas'}, sel(miss)), keepProg(ev, 'evolve:warm-white'), keepProg(ev, 'preset:warm-white'), keepProg(ev, '')]))"""
        out = run_node(harness)
        self.assertEqual(out, ["playlist:Christmas", True, "playlist:Fall", False,
                               {"playlist": "Christmas"}, {"evolve": "warm-white", "minutes": 10},
                               {"preset": "warm-white"}, None])
        self.assertIn("keepProg(SCH.default", extract(APP, "saveSchedule"))

    def test_opening_create_with_nothing_playing_does_not_push(self):
        harness = """
let draft = null, draftId = null, sel = 0, pushed = 0, tab = null;
const get = async () => ({scene: null}), push = () => pushed++, blankLayer = () => ({effect:'gradient'});
const norm = () => {}, resetHistory = () => {}, renderEditor = () => {}, tabTo = t => tab = t;
""" + self.defs("editCurrent") + """
(async () => { await editCurrent(true); await editCurrent(false); console.log(JSON.stringify([pushed, draft.name, tab])) })();"""
        self.assertEqual(run_node(harness), [0, "New scene", "create"])


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestVisitPage(unittest.TestCase):
    def test_status_line_never_goes_stale(self):
        out = run_node("\n".join(extract(VISIT, n) for n in ("statusLine",)) + """
console.log(JSON.stringify([statusLine({on:true, now:'Candy cane', queue:2}), statusLine({on:true, now:null, queue:0}),
  statusLine({on:true, now:null, queue:1}), statusLine({on:false, now:null, queue:0}), statusLine({on:true, now:null, busy:true, queue:1})]))""")
        self.assertEqual(out[0], "Now playing: Candy cane · 2 waiting")
        self.assertEqual(out[1], "")
        self.assertEqual(out[2], "1 waiting")
        self.assertEqual(out[3], "")
        self.assertIn("light show is on", out[4])
        self.assertIn("say(statusLine(S), true)", extract(VISIT, "refresh"))

    def test_pick_during_a_show_is_not_coming_up_now(self):
        out = run_node(extract(VISIT, "pickLine") + """
console.log(JSON.stringify([pickLine({position:1, wait:0, busy:false}, 'Candy cane'), pickLine({position:1, wait:300, busy:true}, 'Candy cane')]))""")
        self.assertIn("coming up now", out[0])
        self.assertNotIn("coming up now", out[1])
        self.assertIn("light show is on", out[1])


class TestVisitorPickDuringShow(unittest.TestCase):
    def rig(self):
        from test_integration import Rig
        r = Rig(self, datetime(2026, 12, 20, 19, 0, 0).astimezone(),
                {"visitor": {"enabled": True, "presets": ["candy-cane", "comets"], "seconds": 20}})
        r.ctl.play_preset("warm-white")
        r.ctl.set_live(True)
        return r

    def test_loop_show_refuses_picks(self):
        r = self.rig()
        self.assertTrue(r.ctl.play_show("Winter wonderland loop"))
        self.assertTrue(r.ctl.visitor_state()["busy"])
        with self.assertRaisesRegex(ValueError, "light show"):
            r.ctl.visitor_pick("candy-cane", "a")
        self.assertEqual(r.ctl.vqueue, [])

    def test_finite_show_reports_its_wait(self):
        r = self.rig()
        from goveelights.shows_builtin import BUILTIN_SHOWS
        name = next(n for n, d in BUILTIN_SHOWS.items() if d.get("end") != "loop" and not any(c.get("countdown_to") for c in d["cues"]))
        self.assertTrue(r.ctl.play_show(name))
        res = r.ctl.visitor_pick("candy-cane", "a")
        self.assertTrue(res["busy"])
        self.assertGreater(res["wait"], 20)

    def test_no_show_is_not_busy(self):
        r = self.rig()
        res = r.ctl.visitor_pick("candy-cane", "a")
        self.assertEqual(res, {"position": 1, "wait": 0, "busy": False})
        self.assertFalse(r.ctl.visitor_state()["busy"])


if __name__ == "__main__":
    unittest.main()
