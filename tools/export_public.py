"""Build a clean, publishable copy of this project (no history, no personal details).

    python3 tools/export_public.py <empty target dir> [--denylist tools/private_denylist.txt]
                                   [--force] [--no-tests]

What it does:
  1. Extracts the git-tracked files at HEAD (uncommitted changes and untracked files,
     such as govee_config.json, are never included).
  2. Drops the owner-only files (DROP below): docs/OWNER_NOTES.md, layouts/house.json.
  3. Replaces goveelights/legacy_defaults.py with the neutral version (no earlier
     defaults to migrate), so a public install starts with no location and no team.
  4. Scrubs the denylist: each line of the (gitignored) denylist file is either
         some text => replacement      (replaced everywhere, case-insensitively)
         some text                     (must simply not appear: fails if it does)
         re:<regex>                    (a pattern that must not appear)
     Blank lines and lines starting with # are ignored.
  5. FAILS if any denylist entry, or any MAC-like device id other than the obvious
     placeholders (AA:BB:CC:DD:EE:FF:.., 00:00:..), is still found in any file, text or
     binary (HTML, images and .pyc included).
  6. Runs the test suite in the exported tree (skip with --no-tests).
  7. Prints the next steps: a brand-new repository with a single commit. Never push this
     repository's history, which still holds everything the export leaves out.
"""
from __future__ import annotations

import argparse
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DENYLIST = ROOT / "tools" / "private_denylist.txt"
DROP = ["docs/OWNER_NOTES.md", "layouts/house.json", "tools/private_denylist.txt"]
LEGACY_FILE = "goveelights/legacy_defaults.py"
NEUTRAL_LEGACY = '''"""Defaults that older versions used without storing them in govee_config.json.

This is the public copy: there are no earlier defaults to keep, so nothing is migrated
(see config.migrate_legacy_defaults). A value of None means "nothing to migrate".
"""

LEGACY_DEFAULTS = {
    "schedule.lat": None,
    "schedule.lon": None,
    "game.teams": None,
}
'''

# 6- or 8-byte ids written with colons or dashes (MAC addresses, Govee device ids)
MAC_RE = re.compile(rb"(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}(?:[:-][0-9A-Fa-f]{2}){0,2}(?![0-9A-Fa-f:])")
PLACEHOLDER_RE = re.compile(rb"^(?:AA[:-]BB[:-]CC[:-]DD[:-]EE[:-]FF|00[:-]00[:-]00[:-]00[:-]00[:-]00)", re.I)


def read_denylist(path: Path) -> tuple[list[tuple[str, str]], list[str], list[str]]:
    """(replacements [(text, replacement)], texts that must not appear, regexes)."""
    repl, plain, regexes = [], [], []
    if not path.exists():
        return repl, plain, regexes
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("re:"):
            regexes.append(line[3:])
        elif "=>" in line:
            a, b = (s.strip() for s in line.split("=>", 1))
            if a:
                repl.append((a, b))
        else:
            plain.append(line)
    return repl, plain, regexes


def scrub_text(text: str, repl: list[tuple[str, str]]) -> str:
    for a, b in repl:
        text = re.sub(re.escape(a), lambda _m, b=b: b, text, flags=re.I)
    return text


def _files(root: Path):
    for p in sorted(root.rglob("*")):
        if p.is_file() and ".git" not in p.relative_to(root).parts:
            yield p


def scan(root: Path, literals: list[str], regexes: list[str] = ()) -> list[tuple[str, int, str]]:
    """[(relative path, line number, what)] for every denylist hit and MAC-like id, in any
    file, read as bytes (so HTML, binary and compiled files are covered too)."""
    pats = [re.compile(re.escape(s.encode("utf-8")), re.I) for s in literals if s]
    pats += [re.compile(r.encode("utf-8"), re.I) for r in regexes]
    hits = []
    for p in _files(root):
        data = p.read_bytes()
        rel = p.relative_to(root).as_posix()
        for pat in pats:
            for m in pat.finditer(data):
                hits.append((rel, data.count(b"\n", 0, m.start()) + 1, "denylist: " + pat.pattern.decode("utf-8", "replace")))
        for m in MAC_RE.finditer(data):
            if not PLACEHOLDER_RE.match(m.group(0)):
                hits.append((rel, data.count(b"\n", 0, m.start()) + 1, "device id: " + m.group(0).decode("ascii", "replace")))
    return hits


def extract_head(target: Path):
    tar = subprocess.run(["git", "-C", str(ROOT), "archive", "--format=tar", "HEAD"],
                         capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(tar)) as tf:
        try:
            tf.extractall(target, filter="data")
        except TypeError:                              # Python < 3.12
            tf.extractall(target)


def build(target: Path, denylist: Path) -> list[str]:
    """Extract, drop, neutralise and scrub. Returns notes; raises SystemExit on a leak."""
    notes = []
    extract_head(target)
    for rel in DROP:
        p = target / rel
        if p.exists():
            p.unlink()
            notes.append(f"dropped {rel}")
    (target / LEGACY_FILE).write_text(NEUTRAL_LEGACY, encoding="utf-8")
    notes.append(f"neutral {LEGACY_FILE}")
    repl, plain, regexes = read_denylist(denylist)
    if not denylist.exists():
        notes.append(f"WARNING: no denylist at {denylist}: only device ids are checked")
    changed = 0
    for p in _files(target):
        data = p.read_bytes()
        if b"\0" in data:
            continue                                   # binary: checked below, never edited
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        new = scrub_text(text, repl)
        if new != text:
            p.write_bytes(new.encode("utf-8"))
            changed += 1
    notes.append(f"scrubbed {changed} file(s) with {len(repl)} replacement(s)")
    hits = scan(target, [a for a, _ in repl] + plain, regexes)
    if hits:
        for rel, line, what in hits[:50]:
            print(f"  LEAK {rel}:{line}: {what}", file=sys.stderr)
        raise SystemExit(f"export FAILED: {len(hits)} private detail(s) remain in {target} (see above)")
    notes.append("no denylist hits, no device ids")
    return notes


def run_tests(target: Path) -> int:
    env = {k: v for k, v in os.environ.items() if k not in ("GOVEE_CONFIG", "GOVEE_AUTH")}
    r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], cwd=target, env=env)
    for cache in target.rglob("__pycache__"):          # compiled files are not part of the release
        shutil.rmtree(cache, ignore_errors=True)
    return r.returncode


NEXT = """
Next steps (a NEW public repository; never push this repository's history):
  cd {t}
  git init -b main
  git add -A
  git -c user.name="<name you choose>" -c user.email="<email you choose>" commit -m "Initial public release"
  # create an empty public repository on GitHub, then:
  git remote add origin https://github.com/<you>/<new-repo>.git
  git push -u origin main
Before pushing, skim README.md, LICENSE (copyright line) and SECURITY.md (contact).
"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("target", help="directory to create (must be empty or missing; see --force)")
    ap.add_argument("--denylist", default=str(DENYLIST))
    ap.add_argument("--force", action="store_true", help="delete the target directory first")
    ap.add_argument("--no-tests", action="store_true")
    a = ap.parse_args(argv)
    target = Path(a.target).expanduser().resolve()
    if target == ROOT or ROOT in target.parents:
        raise SystemExit("the target must be outside this repository")
    if target.exists() and any(target.iterdir()):
        if not a.force:
            raise SystemExit(f"{target} is not empty (use --force to replace it)")
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    for n in build(target, Path(a.denylist)):
        print(" ", n)
    if not a.no_tests:
        print("running the test suite in the export...")
        if run_tests(target):
            raise SystemExit("export FAILED: the test suite does not pass in the exported tree")
        hits = scan(target, [x for x, _ in read_denylist(Path(a.denylist))[0]] + read_denylist(Path(a.denylist))[1])
        if hits:
            raise SystemExit(f"export FAILED: the test run left private details behind: {hits[:5]}")
    print(f"export OK: {target}")
    print(NEXT.format(t=target))
    return 0


if __name__ == "__main__":
    sys.exit(main())
