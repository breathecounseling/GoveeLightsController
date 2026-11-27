"""How long each preset takes to draw, and will it hold 20 fps on a Raspberry Pi?

    python3 tools/bench.py                      every preset on the house layout
    python3 tools/bench.py --tip                ... and again with the Tip added
    python3 tools/bench.py --seconds 1 --only warm-white,fireworks
    python3 tools/bench.py --layout layouts/examples/gable-house.json

For each preset it runs the real render path (scene, calibration, dithering, the razer
packets the boxes get; no network) for --seconds of show time at 20 fps and prints the
average and the slowest 5% of frames, in milliseconds.

The Pi columns are an ESTIMATE, not a measurement: they multiply this computer's numbers
by --pi5 (default 2.5) and --pi4 (default 5). Those factors assume this computer is a
typical recent desktop or laptop core; single-thread Python on a Pi 5 is roughly 2-3x
slower than that, and a Pi 4 roughly 4-6x. For real numbers, run this on the Pi itself
with --pi5 1 --pi4 1 (or just read the "this computer" columns there).

Verdicts (20 fps gives each frame 50 ms; the app also serves the phone page, so only
part of that is free for drawing):
  20 fps         average under 60% of 50 ms
  20 fps (tight) average under 90% of 50 ms
  15 fps / 12 fps the app's automatic frame-rate governor would settle there
  < 12 fps       too slow for this preset on that machine
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from goveelights import protocol as p  # noqa: E402
from goveelights.engine import Engine, FrameGovernor  # noqa: E402
from goveelights.scene import Scene  # noqa: E402

from preview import default_layout_file, house_layout  # noqa: E402

FPS = 20.0
HIGH, LOW = FrameGovernor().high, 0.6


class PacketOutput:
    """Builds the exact UDP payloads the boxes would get, and drops them."""

    def __init__(self):
        self.bytes = 0

    def start(self, force: bool = False):
        pass

    def send_frame(self, run_id, pixels):
        self.bytes += len(p.encode(p.razer_msg(p.razer_frame_packet(pixels))))

    def stop(self):
        pass


def verdict(ms: float) -> str:
    """What rate a machine with this average cost per frame would run at."""
    if ms < LOW * 1000 / FPS:
        return "20 fps"
    if ms < HIGH * 1000 / FPS:
        return "20 fps (tight)"
    for f in FrameGovernor.STEPS[1:]:
        if ms < HIGH * 1000 / f:
            return f"{f:g} fps"
    return "< 12 fps"


def bench_scene(scene: Scene, layout, seconds: float = 3.0, fps: float = FPS) -> dict:
    """Render `seconds` of show time through Engine.tick (clock stepped, not slept)."""
    eng = Engine(layout, PacketOutput(), fps=fps)
    eng.live = True
    eng.set_scene(scene)
    n = max(2, int(seconds * fps))
    t = [0.0]
    eng.now = lambda: t[0]                   # step show time by one frame each tick
    times = []
    for k in range(n + 2):
        t[0] = k / fps
        a = time.perf_counter()
        eng.tick()
        if k >= 2:                           # the first frames warm caches up
            times.append((time.perf_counter() - a) * 1000)
    times.sort()
    return {"mean_ms": statistics.fmean(times), "p95_ms": times[min(len(times) - 1, int(len(times) * 0.95))],
            "max_ms": times[-1], "frames": len(times)}


def presets(only=None) -> list[tuple[str, Scene]]:
    out = []
    for f in sorted((ROOT / "presets").glob("*.json")):
        if only and f.stem not in only:
            continue
        out.append((f.stem, Scene.from_dict(json.loads(f.read_text(encoding="utf-8")))))
    return out


def run(layout, label: str, items, seconds: float, pi5: float, pi4: float, write=print) -> list[dict]:
    write(f"\n{label}: {layout.n} bulbs on {len(layout.runs)} runs, {seconds:g} s of show at {FPS:g} fps each")
    head = f"{'preset':<28}{'avg ms':>8}{'p95 ms':>8}   {'Pi 5 est.':<22}{'Pi 4 est.':<22}"
    write(head)
    write("-" * len(head))
    rows = []
    for pid, sc in items:
        r = bench_scene(sc, layout, seconds)
        r.update(preset=pid, pi5=verdict(r["mean_ms"] * pi5), pi4=verdict(r["mean_ms"] * pi4))
        rows.append(r)
        write(f"{pid[:27]:<28}{r['mean_ms']:>8.2f}{r['p95_ms']:>8.2f}   "
              f"{r['mean_ms'] * pi5:>6.1f} ms {r['pi5']:<12}{r['mean_ms'] * pi4:>6.1f} ms {r['pi4']:<12}")
    if rows:
        worst = max(rows, key=lambda r: r["mean_ms"])
        avg = statistics.fmean(r["mean_ms"] for r in rows)
        write("-" * len(head))
        write(f"average {avg:.2f} ms, slowest {worst['preset']} {worst['mean_ms']:.2f} ms")
        for name, k, fac in (("Pi 5", "pi5", pi5), ("Pi 4", "pi4", pi4)):
            full = sum(1 for r in rows if r[k].startswith("20 fps"))
            write(f"{name} (x{fac:g}): {full}/{len(rows)} presets hold 20 fps; "
                  f"slowest would run at {verdict(worst['mean_ms'] * fac)}")
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--layout", help="layout file (default layouts/house.json, else the example house)")
    ap.add_argument("--tip", action="store_true", help="also time the layout with the Tip added")
    ap.add_argument("--seconds", type=float, default=3.0, help="show time per preset (default 3)")
    ap.add_argument("--only", help="comma-separated preset ids")
    ap.add_argument("--pi5", type=float, default=2.5, help="how many times slower a Pi 5 is (default 2.5)")
    ap.add_argument("--pi4", type=float, default=5.0, help="how many times slower a Pi 4 is (default 5)")
    a = ap.parse_args(argv)
    lf = Path(a.layout) if a.layout else default_layout_file()
    layout = house_layout(lf)
    items = presets(set(a.only.split(",")) if a.only else None)
    print(f"This computer: {platform.machine()} {platform.processor() or ''}, Python {platform.python_version()}, "
          f"{os.cpu_count()} cores (the render loop uses one).")
    print(f"Pi columns assume a Pi 5 is {a.pi5:g}x and a Pi 4 {a.pi4:g}x slower than this computer "
          f"(an estimate; run this on the Pi for real numbers).")
    rows = run(layout, lf.name, items, a.seconds, a.pi5, a.pi4)
    if a.tip:
        try:
            from goveelights.addrun import layout_with_tip
            rows += run(layout_with_tip(layout), lf.name + " + Tip", items, a.seconds, a.pi5, a.pi4)
        except Exception as e:  # noqa: BLE001 - a layout the Tip can't go on
            print(f"\n(no Tip layout: {e})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
