"""Round-5 UX fixes: a tap after Stop lights the house (unless the owner chose phone only),
plain power words, a date-aware Featured strip, an honest Tonight title, readable links,
Security above the palettes, the holiday calendar's single "now", header and Create names
that fit, tap targets, playlist units, dark native controls, the next game in local time,
the schedule-off "what if" showtimes, the collapsing preview, and the visitor page's
footer, lights-off line and single pick confirmation."""
from __future__ import annotations

import os
import re
import shutil
import sys
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_integration import SCH, Rig  # noqa: E402
from test_r3_web import APP, VISIT, extract, run_node  # noqa: E402

from goveelights import tonight  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "goveelights" / "web" / "app.html").read_text(encoding="utf-8")
CSS = HTML[HTML.index("<style>"):HTML.index("</style>")]
QUICK = {"Quick": {"end": "resume", "cues": [{"preset": "candy-cane", "seconds": 5}]}}


class _Denver:
    def setUp(self):
        self._tz = os.environ.get("TZ")
        os.environ["TZ"] = "America/Denver"
        if hasattr(time, "tzset"):
            time.tzset()

    def tearDown(self):
        if self._tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self._tz
        if hasattr(time, "tzset"):
            time.tzset()


# ------------------------------------------------------------------ 1: Stop, then a tap
class PhoneOnlyTests(_Denver, unittest.TestCase):
    def rig(self):
        r = Rig(self, datetime(2026, 11, 26, 18, 40).astimezone(), {"schedule": {**SCH, "enabled": False}})
        r.ctl.play_preset("warm-white")
        r.ctl.set_live(True)
        return r

    def test_stop_is_lights_off_not_phone_only(self):
        ctl = self.rig().ctl
        ctl.stop()
        st = ctl.status()
        self.assertFalse(st["live"])
        self.assertFalse(st["phone_only"])

    def test_phone_only_is_the_owners_choice_and_ends_with_live(self):
        ctl = self.rig().ctl
        ctl.set_live(False)                           # "Show on phone only"
        self.assertTrue(ctl.status()["phone_only"])
        ctl.stop()                                    # "Turn lights off": the next tap lights the house
        self.assertFalse(ctl.status()["phone_only"])
        ctl.set_live(False)
        ctl.set_live(True, manual=False)              # the schedule turned the lights on
        self.assertFalse(ctl.status()["phone_only"])


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TapGoesLiveTests(unittest.TestCase):
    def test_tap_after_stop_goes_live(self):
        out = run_node(extract(APP, "tapGoesLive") + """
const runs = [{name:'Eaves'}];
console.log(JSON.stringify([tapGoesLive({live:false, phone_only:false, runs}), tapGoesLive({live:true, runs}),
  tapGoesLive({live:false, phone_only:true, runs}), tapGoesLive({live:false, diag_active:true, runs}),
  tapGoesLive({live:false, mapping:true, runs}), tapGoesLive({live:false, runs:[]}), tapGoesLive({})]))""")
        self.assertEqual(out, [True, False, False, False, False, False, False])

    def test_preset_and_playlist_taps_send_live(self):
        self.assertIn("tapGoesLive(ST)", extract(APP, "playPreset"))
        self.assertIn("tapGoesLive(ST)", extract(APP, "playlistChips"))

    def test_plain_words(self):
        for gone in ("Switch to preview", "Stop playback", "tap Preview to go Live", ">Preview<"):
            self.assertNotIn(gone, HTML)
        for word in ("Go live", "Show on phone only", "Turn lights off"):
            self.assertIn(word, HTML)
        out = run_node("""const els = {liveBtn: {cls: new Set(), attrs: {}, lt: {textContent: 'Go live'},
  querySelector(){ return this.lt }, classList: {toggle(c, on){ on ? els.liveBtn.cls.add(c) : els.liveBtn.cls.delete(c) }},
  setAttribute(k, v){ this.attrs[k] = v }}};
const $ = id => els[id];
""" + extract(APP, "liveBtnUI") + """
liveBtnUI({live:true}); const a = [els.liveBtn.lt.textContent, els.liveBtn.cls.has('on')];
liveBtnUI({live:false}); console.log(JSON.stringify([...a, els.liveBtn.lt.textContent, els.liveBtn.cls.has('on')]))""")
        self.assertEqual(out, ["Live", True, "Go live", False])


# ------------------------------------------------------------------ 2: Featured follows tonight
@unittest.skipUnless(shutil.which("node"), "node is not installed")
class FeaturedTests(unittest.TestCase):
    def run_feat(self, schedule):
        return run_node(extract(APP, "FEATURED") + "\n" + extract(APP, "tonightIds") + "\n" + extract(APP, "featuredPresets") + """
const PRESETS = ['christmas-tree','thanksgiving','harvest-moon','grand-finale','warm-white'].map(id => ({id, name:id, tags:[]}));
const PL = {'Holiday: Thanksgiving': {items:[{preset:'thanksgiving'},{preset:'harvest-moon'},{preset:'thanksgiving'}]}};
const ST = {schedule: %s};
console.log(JSON.stringify(featuredPresets().map(p => p.id)))""" % schedule)

    def test_tonights_program_comes_first(self):
        ids = self.run_feat("{active:false, next_program:{playlist:'Holiday: Thanksgiving'}}")
        self.assertEqual(ids[:3], ["thanksgiving", "harvest-moon", "christmas-tree"])
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(self.run_feat("{active:true, program:{evolve:'warm-white'}}")[0], "warm-white")
        self.assertEqual(self.run_feat("{}")[0], "christmas-tree")

    def test_heading_says_tonight_only_when_it_is(self):
        self.assertIn('id="featT">Featured<', HTML)
        self.assertIn("'Featured tonight' : 'Featured'", extract(APP, "drawFeatured"))
        self.assertIn("drawFeatured()", extract(APP, "refresh"))


# ------------------------------------------------------------------ 3: Tonight title
@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TonightTitleTests(unittest.TestCase):
    def test_on_now_only_while_lit(self):
        out = run_node(extract(APP, "tnTitle") + """
const p = {date:'2026-11-26', current:true, enabled:true, day:'Tonight', on_text:'4:36 pm', off_text:'11:00 pm'};
console.log(JSON.stringify([tnTitle(p, {live:true}), tnTitle({...p, enabled:false}, {live:true}), tnTitle(p, {live:false}),
  tnTitle(p, {live:true, blackout:true}), tnTitle({...p, current:false, day:'Tomorrow'}, {live:false})]))""")
        self.assertTrue(out[0].startswith("Tonight · on now · "))
        for t in out[1:4]:
            self.assertTrue(t.startswith("Tonight · 4:36 pm–11:00 pm · "), t)
            self.assertNotIn("on now", t)
        self.assertTrue(out[4].startswith("Tomorrow · "))


# ------------------------------------------------------------------ 4, 5, 8, 9, 10, 11, 14: page polish
class PageStaticTests(unittest.TestCase):
    def test_links_and_buttons_in_links_are_readable(self):
        self.assertIn("a{color:var(--blue)}", CSS)
        self.assertIn("a button{color:var(--text)}", CSS)

    def test_security_and_devices_come_before_the_palettes(self):
        more = HTML[HTML.index('<section id="s-more">'):]
        self.assertLess(more.index('id="secCard"'), more.index('id="palList"'))
        self.assertLess(more.index('id="devInfo"'), more.index('id="palList"'))
        self.assertIn("<details", extract(APP, "drawPalList"))

    def test_dark_native_controls(self):
        self.assertIn('<meta name="color-scheme" content="dark">', HTML)
        self.assertRegex(CSS, r":root\{color-scheme:dark;")

    def test_tap_targets(self):
        self.assertRegex(CSS, r"button\.sm\{[^}]*min-height:40px;min-width:40px")
        self.assertRegex(CSS, r"\.seg button\{[^}]*min-height:40px")
        self.assertRegex(CSS, r"\.days button\{[^}]*min-width:40px;min-height:40px")
        self.assertRegex(CSS, r"(?m)^summary\{[^}]*min-height:40px")
        x = re.search(r"\.cols \.c \.x\{([^}]*)\}", CSS).group(1)
        self.assertGreaterEqual(int(re.search(r"width:(\d+)px", x).group(1)), 32)
        self.assertLessEqual(int(re.search(r"right:(-?\d+)px", x).group(1)), -20)      # mostly off the swatch
        for cid in ("gEn", "wEn", "vEn"):
            self.assertRegex(HTML, r'<label class="hck"><span>[^<]+</span><input type="checkbox" id="%s"' % cid)

    def test_create_name_has_its_own_row_and_header_name_wraps(self):
        create = HTML[HTML.index('<section id="s-create">'):HTML.index('<div id="layerList">')]
        self.assertLess(create.index('id="nameBtn"'), create.index('class="tb"'))
        self.assertRegex(CSS, r"\.hd h1 span\{[^}]*-webkit-line-clamp:2")
        self.assertIn(".hd.stopping .live.on .lt{display:none}", CSS)

    def test_playlist_minutes_show_their_unit(self):
        self.assertIn('data-m="${k}" title="minutes" aria-label="minutes"><span class="dim" style="font-size:13px">min</span>',
                      extract(APP, "plEditor"))

    def test_calendar_dates_have_room(self):
        self.assertIn(".calr{display:grid;grid-template-columns:112px 1fr;", CSS)

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_preview_collapses_on_scroll_but_not_while_painting(self):
        out = run_node("""const cls = new Set(); const hd = {classList: {contains: c => cls.has(c), toggle(c, on){ on ? cls.add(c) : cls.delete(c) }}};
const document = {querySelector: () => hd}, window = {scrollY: 0}; let painting = false;
""" + extract(APP, "headerMini") + """
const seen = []; for (const [y, p] of [[100,false],[200,false],[100,false],[20,false],[300,true]]){ window.scrollY = y; painting = p; headerMini(); seen.push(cls.has('mini')) }
console.log(JSON.stringify(seen))""")
        self.assertEqual(out, [False, True, True, False, False])
        self.assertIn("header.mini #pv{height:44px!important;object-fit:contain}", CSS)
        self.assertIn("headerMini()", extract(APP, "togglePaint"))


# ------------------------------------------------------------------ 12: the next game
@unittest.skipUnless(shutil.which("node"), "node is not installed")
class GameStatusTests(unittest.TestCase):
    def test_pre_game_shows_when_not_a_score(self):
        out = run_node("""const els = {gStatus: {innerHTML: ''}, wStatus: {innerHTML: ''}}; const $ = id => els[id];
const esc = s => String(s ?? ''); let EXTRAS = {game:{enabled:true}};
let ST = {games: {'nfl:DEN': {id:'1', state:'pre', team:'DEN', opponent:'PIT', score:0, opp_score:0, detail:'11/27 - 3:00 PM EST', date:'2026-11-27T20:00Z'}}};
""" + extract(APP, "tfmt") + "\n" + extract(APP, "liveStatus") + """
liveStatus(); console.log(JSON.stringify(els.gStatus.innerHTML))""")
        self.assertTrue(out.startswith("Next: <b>DEN vs PIT</b>, "), out)
        self.assertNotIn("0 –", out)
        self.assertNotIn("EST", out)


class NextGameTonightTests(_Denver, unittest.TestCase):
    def test_tomorrows_game_is_mentioned(self):
        r = Rig(self, datetime(2026, 11, 26, 18, 40).astimezone(),
                {"schedule": {**SCH, "rules": []}, "game": {"enabled": True, "teams": [{"league": "nfl", "team": "DEN"}]}})
        kick = datetime(2026, 11, 27, 13, 0).astimezone()
        r.ctl.games["nfl:DEN"] = {"id": "1", "state": "pre", "team": "DEN", "opponent": "PIT", "score": 0, "opp_score": 0,
                                  "date": kick.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")}
        p = tonight.build(r.ctl, datetime(2026, 11, 26, 18, 40).astimezone())
        self.assertEqual(p["games"][0]["text"], "next game: DEN vs PIT, Fri 1:00 pm")
        self.assertEqual(p["games"][0]["state"], "next")
        r.ctl.games["nfl:DEN"]["date"] = (kick + timedelta(days=6)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
        self.assertEqual(tonight.build(r.ctl, datetime(2026, 11, 26, 18, 40).astimezone())["games"][0]["text"], "no game today")


# ------------------------------------------------------------------ 13: "if it were on" includes showtimes
class ScheduleOffShowtimeTests(_Denver, unittest.TestCase):
    def test_only_when_on_showtimes_are_in_the_what_if(self):
        st = [{"show": "Quick", "from": "17:00", "to": "22:00", "every": 15, "only_when_on": True}]
        now = datetime(2026, 11, 26, 14, 0).astimezone()
        off = Rig(self, now, {"schedule": {**SCH, "enabled": False}, "showtimes": st, "shows": QUICK})
        on = Rig(self, now, {"schedule": {**SCH, "enabled": True}, "showtimes": st, "shows": QUICK})
        p_off, p_on = tonight.build(off.ctl, now), tonight.build(on.ctl, now)
        self.assertFalse(p_off["enabled"])
        lab = [x for x in p_off["timeline"] if x["label"] == "Show: Quick"]
        self.assertEqual(len(lab), 1)
        self.assertEqual(lab, [x for x in p_on["timeline"] if x["label"] == "Show: Quick"])
        self.assertIn("every 15 min", lab[0]["detail"])


# ------------------------------------------------------------------ 7: one "now" on the calendar
class CalendarNowTests(_Denver, unittest.TestCase):
    def test_only_the_winning_rule_is_now(self):
        r = Rig(self, datetime(2026, 11, 26, 12, 0).astimezone(), {"schedule": {**SCH, "enabled": False}})
        now = [x["name"] for x in r.ctl.calendar_preview()["rules"] if x["now"]]
        self.assertEqual(now, ["Thanksgiving"])


# ------------------------------------------------------------------ 6: the visitor page
class VisitorStateTests(_Denver, unittest.TestCase):
    def test_seconds_and_next_on(self):
        r = Rig(self, datetime(2026, 11, 26, 12, 0).astimezone(),
                {"schedule": {**SCH, "rules": []}, "visitor": {"enabled": True, "presets": ["candy-cane"], "seconds": 30}})
        r.ctl._schedule_tick()
        st = r.ctl.visitor_state()
        self.assertEqual(st["seconds"], 30)
        self.assertFalse(st["on"])
        self.assertRegex(st["next_on"], r"^at \d{1,2}:\d\d pm$")
        r.ctl.cfg.data["schedule"]["enabled"] = False
        self.assertIsNone(r.ctl.visitor_state()["next_on"])
        r.ctl.cfg.data["schedule"]["enabled"] = True
        r.ctl.play_preset("candy-cane")
        r.ctl.set_live(True)
        self.assertIsNone(r.ctl.visitor_state()["next_on"])         # lights on: nothing to wait for


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class VisitPageTests(unittest.TestCase):
    def test_footer_follows_the_setting(self):
        out = run_node(extract(VISIT, "footLine") + """
console.log(JSON.stringify([footLine({seconds:30}), footLine({seconds:180}), footLine({})]))""")
        self.assertIn("for 30 seconds", out[0])
        self.assertIn("about 3 minutes", out[1])
        self.assertIn("for 60 seconds", out[2])
        self.assertNotIn("about a minute", VISIT)
        self.assertIn('id="foot"', (ROOT / "goveelights" / "web" / "visit.html").read_text(encoding="utf-8"))

    def test_lights_off_line(self):
        out = run_node("const esc = s => String(s);\n" + extract(VISIT, "offLine") + """
console.log(JSON.stringify([offLine({next_on:'at 4:36 pm'}), offLine({next_on:'tomorrow at 4:37 pm'}), offLine({})]))""")
        self.assertEqual(out[0], "They come on at 4:36 pm. See you then! ✨")
        self.assertIn("tomorrow at 4:37 pm", out[1])
        self.assertEqual(out[2], "Check back later! ✨")
        self.assertNotIn("Come back this evening", VISIT)

    def test_one_confirmation_for_a_pick(self):
        out = run_node(extract(VISIT, "statusLine") + """
console.log(JSON.stringify([statusLine({on:true, now:'Candy cane', queue:0, you:{playing:true}}),
  statusLine({on:true, now:'Candy cane', queue:2, you:{playing:true}}), statusLine({on:true, now:'Candy cane', queue:0, you:null})]))""")
        self.assertEqual(out, ["", "2 waiting", "Now playing: Candy cane"])
        self.assertIn("S && S.on && S.you ? '' : pickLine(r, p.name)", extract(VISIT, "refresh"))


if __name__ == "__main__":
    unittest.main()
