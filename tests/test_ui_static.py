"""Static checks of the web pages: every inline script parses (node --check), every element
id the scripts look up exists, inline handlers call functions that exist, and the phone UI
reads/saves the settings and shows features it is meant to expose."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "goveelights" / "web"
PAGES = ["app.html", "visit.html", "diag.html", "login.html"]
SCRIPT_RE = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S | re.I)


def scripts(page: str) -> list[str]:
    return SCRIPT_RE.findall((WEB / page).read_text(encoding="utf-8"))


class TestScriptsParse(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_inline_scripts_are_valid_javascript(self):
        with tempfile.TemporaryDirectory() as td:
            for page in PAGES:
                blocks = scripts(page)
                self.assertTrue(blocks, f"{page}: no inline script found")
                for k, js in enumerate(blocks):
                    f = Path(td) / f"{page}.{k}.js"
                    f.write_text(js, encoding="utf-8")
                    r = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True, timeout=60)
                    self.assertEqual(r.returncode, 0, f"{page} script {k} does not parse:\n{r.stderr}")


class TestIdsAndHandlers(unittest.TestCase):
    def test_looked_up_ids_exist(self):
        for page in PAGES:
            html = (WEB / page).read_text(encoding="utf-8")
            js = "\n".join(scripts(page))
            # ids in the markup and in the HTML the scripts build (template strings)
            have = set(re.findall(r'\bid="([A-Za-z][\w-]*)"', html))
            used = set(re.findall(r"\$\('([A-Za-z][\w-]*)'\)", js)) | set(re.findall(r"getElementById\('([A-Za-z][\w-]*)'\)", js))
            used |= set(re.findall(r"querySelector(?:All)?\('#([A-Za-z][\w-]*)", js))
            missing = sorted(used - have)
            self.assertFalse(missing, f"{page}: scripts look up ids that don't exist: {missing}")

    def test_inline_handlers_call_defined_functions(self):
        browser = {"confirm", "alert", "prompt", "if", "setTimeout", "fetch", "encodeURIComponent", "Number", "String"}
        for page in PAGES:
            html = (WEB / page).read_text(encoding="utf-8")
            js = "\n".join(scripts(page))
            defined = set(re.findall(r"function\s+([A-Za-z_]\w*)", js))
            defined |= set(re.findall(r"(?:const|let|var)\s+([A-Za-z_]\w*)\s*=", js))
            markup = SCRIPT_RE.sub("", html)        # static markup only (template strings are checked by node)
            for handler in re.findall(r'\bon(?:click|change|input)="([^"]*)"', markup):
                code = re.sub(r"'(?:[^'\\]|\\.)*'", "''", handler.replace("&#39;", "'"))     # drop string literals
                for name in re.findall(r"(?<![\w.$])([A-Za-z_]\w*)\s*\(", code):
                    if name not in browser:
                        self.assertIn(name, defined, f"{page}: handler {handler!r} calls undefined {name}()")


class TestAppExposesFeatures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (WEB / "app.html").read_text(encoding="utf-8")
        cls.js = "\n".join(scripts("app.html"))

    def test_featured_strip_lists_installed_presets(self):
        m = re.search(r"const FEATURED = \[(.*?)\];", self.js, re.S)
        self.assertIsNotNone(m, "FEATURED list not found")
        ids = re.findall(r"'([\w-]+)'", m.group(1))
        self.assertIn("christmas-tree", ids)
        have = {p.stem for p in (ROOT / "presets").glob("*.json")}
        self.assertFalse(sorted(set(ids) - have), "featured ids without a preset (the UI hides them, but check the names)")
        self.assertIn("featStrip", self.html)
        self.assertIn("'featured'", self.js)        # presets tagged Featured join the strip

    def test_schedule_and_game_settings_are_read_with_defaults_and_saved(self):
        self.assertIn('id="scDusk"', self.html)
        self.assertRegex(self.js, r"dusk_intro_minutes\s*\|\|\s*0")
        self.assertRegex(self.js, r"dusk_intro_minutes:\s*Math\.max\(0")
        for key, default in (("scoreboard_every", "0"), ("scoreboard_seconds", "20")):
            self.assertRegex(self.js, rf"g\.{key}\s*\|\|\s*{default}")
        self.assertIn('id="gSbEvery"', self.html)
        self.assertIn('id="gSbSecs"', self.html)

    def test_builtin_shows_are_never_saved_into_the_config(self):
        self.assertIn("builtin_shows", self.js)
        self.assertRegex(self.js, r"which === 'shows' \? userShows\(\)")
        self.assertIn("Copy to my shows", self.js)
        self.assertIn("Built-in", self.js)

    def test_header_has_stop_with_confirmation(self):
        self.assertIn('id="stopBtn"', self.html)
        self.assertIn("Tap to stop", self.js)

    def test_presets_dir_is_json(self):      # the strip relies on preset ids = file names
        for p in (ROOT / "presets").glob("*.json"):
            json.loads(p.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
