"""Porch fireworks and snow that piles up: two particle simulations that use the
real geometry of the house (layout x/y in bulb-spacing units, y up).

fireworks  shells launch from the lower roofline (the porch by default), climb as a
           bright dot with a short trail into the front gable, and burst at apex
           height into sparks with drag and gravity: peonies, rings, gold willows,
           two-tone and crackling shells, with drifting embers and an optional
           grand finale every N seconds.
snowdrift  flakes drift down with a gentle sway and LAND on the first surface below
           them, where the snow piles up bulb by bulb (cool white, slightly blue),
           so over minutes the eaves and the porch line fill with snow; it can melt.

Both are @frame_effect generators: all memory lives in the layer's `state` dict,
randomness comes from a random.Random kept in that state (deterministic for a given
seed and frame sequence), large frames are substepped (and a stall is capped), and
particle counts are bounded so they can run for hours.
Particles are drawn with geometry.splat so they glide between bulbs.
"""
from __future__ import annotations

import math
import random

from ..color import BLACK, fbm2, hash01, lerp, noise2
from ..effects import Param, frame_effect
from ..geometry import near, splat
from ..sparkle import glint

MAX_DT = 0.1                  # seconds: largest physics step (bigger frames are substepped)
MAX_FRAME_DT = 0.5            # seconds: a stalled frame never makes particles teleport


def _layout_key(ctx) -> tuple:
    L, idx = ctx.layout, ctx.idx
    return (id(L), len(L.pixels), len(idx), idx[0] if idx else -1, idx[-1] if idx else -1, id(idx))


def _clamp_frame(frame: list, idx) -> list:
    for i in idx:
        r, g, b = frame[i]
        if r > 1.0 or g > 1.0 or b > 1.0 or r < 0.0 or g < 0.0 or b < 0.0:
            frame[i] = (min(1.0, max(0.0, r)), min(1.0, max(0.0, g)), min(1.0, max(0.0, b)))
    return frame


def _zone(layout, name) -> list[int]:
    try:
        return list(layout.indices(name)) if hasattr(layout, "indices") else []
    except Exception:  # noqa: BLE001 - unknown zone on a different layout
        return []


# ====================================================================== fireworks
LAUNCH_ZONES = ["Porch", "Lower roofline", "Garage", "Garage peak", "Accent", "Flat eaves",
                "Bottom of target"]
STYLES = ["Mixed", "Peony", "Ring", "Willow", "Two-tone", "Crackle"]

# particle kinds
SHELL, SPARK, EMBER, FLASH = 0, 1, 2, 3
GOLD = (1.0, 0.62, 0.18)
HOT = (1.0, 0.93, 0.8)


def _fw_setup(ctx, P, st):
    """(Re)build everything that depends on the layout, the target or the launch zone."""
    L, idx = ctx.layout, ctx.idx
    px = L.pixels
    key = _layout_key(ctx) + (P["launch_zone"],)
    if st.get("key") == key:
        return
    if st.get("key") is None or st["key"][:2] != key[:2]:
        st["parts"] = []              # different layout: particle coordinates are meaningless
    st["key"] = key
    st["allowed"] = set(idx)
    st["glow"] = {}
    pool = idx if idx else list(range(len(px)))
    launch = [] if P["launch_zone"] == "Bottom of target" else _zone(L, P["launch_zone"])
    if not launch and pool:
        lo = min(px[i].y for i in pool)
        launch = [i for i in pool if px[i].y <= lo + 0.5]
    st["launch"] = [(px[i].x, px[i].y) for i in launch]
    # where shells burst: the upper half of the target, near the launch zone
    if pool and launch:
        ys = [px[i].y for i in pool]
        mid = (min(ys) + max(ys)) / 2
        lx = [x for x, _ in st["launch"]]
        a, b = min(lx) - 8, max(lx) + 8
        upper = [i for i in pool if px[i].y >= mid and a <= px[i].x <= b] or \
                [i for i in pool if px[i].y >= mid] or list(pool)
        xs = [px[i].x for i in upper]
        st["bx"] = (min(xs), max(xs))
        st["top"] = max(px[i].y for i in upper)
        # roof profile: highest bulb of the upper area per 1-unit column
        prof: dict[int, float] = {}
        for i in upper:
            k = math.floor(px[i].x)
            prof[k] = max(prof.get(k, -1e9), px[i].y)
        st["prof"] = prof
        st["ylo"] = min(px[i].y for i in pool)
        # burst columns: only where a burst at its typical height has plenty of bulbs to
        # draw on (the front gable, not the empty sky between the gables and the garage)
        dens = {}
        for k in range(math.floor(st["bx"][0]), math.floor(st["bx"][1]) + 1):
            by = _burst_y(st, k + 0.5) - 0.8
            dens[k] = sum(1 for i, _ in near(L, k + 0.5, by, 5.0) if i in st["allowed"])
        best = max(dens.values(), default=0)
        need = 0.5 * best if best < 24 else max(12, 0.5 * best)
        st["cols"] = sorted(k for k, c in dens.items() if c >= need and c > 0) or \
            sorted(dens) or [math.floor(st["bx"][0])]
    else:
        st["bx"], st["top"], st["prof"], st["ylo"], st["cols"] = (0.0, 0.0), 0.0, {}, 0.0, [0]


def _profile(st, x: float) -> float:
    prof, k = st["prof"], math.floor(x)
    best = max((prof[j] for j in (k - 2, k - 1, k, k + 1, k + 2) if j in prof), default=None)
    return st["top"] if best is None else best


def _burst_y(st, x: float) -> float:
    """Typical burst height over column x: between the roof there and the top of the area."""
    return 0.45 * _profile(st, x) + 0.55 * st["top"] - 0.9


def _launch(st, P, rng, big=False):
    if not st["launch"]:
        return
    x0, y0 = rng.choice(st["launch"])
    cols = st["cols"]
    # trim the outermost dense columns a little so bursts stay well inside the area
    m = len(cols) // 8
    cols = cols[m:len(cols) - m] or cols
    bx = cols[rng.randrange(len(cols))] + rng.random()
    by = _burst_y(st, bx) - 1.4 * rng.random()
    by = max(by, y0 + 3.0)
    if big:
        by += 0.6
    cols = P["colors"] or [GOLD]
    col = cols[rng.randrange(len(cols))]
    col2 = cols[rng.randrange(len(cols))] if len(cols) > 1 else lerp(col, (1, 1, 1), 0.6)
    style = P["style"]
    if style == "Mixed":
        r = rng.random()
        style = ("Peony" if r < 0.42 else "Ring" if r < 0.57 else "Willow" if r < 0.7 else "Two-tone"
                 if r < 0.85 else "Crackle")
        if style != "Crackle" and rng.random() < P["crackle"] * 0.35:
            style = "Crackle"
    rise = 1.05 + 0.35 * rng.random() + 0.04 * max(0.0, by - y0 - 8)
    # [kind, x, y, vx, vy, age, life, colour, style, colour2, seed, x0, y0, bx, by, big]
    st["parts"].append([SHELL, x0, y0, 0.0, 0.0, 0.0, rise, col, style, col2, rng.randrange(1 << 30),
                        x0, y0, bx, by, big])
    st["parts"].append([FLASH, x0, y0 + 0.3, 0.0, 0.0, 0.0, 0.22, GOLD, "launch", None, 0, 0, 0, 0, 0, False])


def _burst(st, P, rng, s):
    x, y, col, style, col2, big = s[1], s[2], s[7], s[8], s[9], s[15]
    n = int(P["burst_size"] * (0.85 + 0.3 * rng.random()) * (1.35 if big else 1.0))
    v0 = P["burst_speed"] * (0.9 + 0.2 * rng.random()) * (1.15 if big else 1.0)
    parts = st["parts"]
    if style == "Willow":
        col = col2 = lerp(GOLD, col, 0.15)
    rot = rng.random() * math.tau
    for k in range(n):
        if style == "Ring":
            ang, sp = rot + math.tau * k / n, v0
        else:
            ang = rng.random() * math.tau
            # a sphere seen from the front: more sparks near the rim than the centre
            sp = v0 * math.sqrt(1 - rng.random() ** 2) * (0.85 + 0.15 * rng.random())
        vx, vy = math.cos(ang) * sp, math.sin(ang) * sp * 0.9 + 0.6
        life = (2.8 if style == "Willow" else 1.8) * (0.8 + 0.45 * rng.random())
        parts.append([SPARK, x, y, vx, vy, 0.0, life, col, style, col2, rng.randrange(1 << 30),
                      0, 0, 0, 0, False])
    # embers: a few dim warm sparks that drift down slowly after the burst
    for _ in range(int(n * 0.18 * P["embers"])):
        ang = rng.random() * math.tau
        sp = v0 * 0.45 * rng.random()
        parts.append([EMBER, x, y, math.cos(ang) * sp, math.sin(ang) * sp, -0.25 * rng.random(),
                      2.6 + 2.2 * rng.random(), lerp(col, GOLD, 0.6), style, col2, rng.randrange(1 << 30),
                      0, 0, 0, 0, False])
    parts.append([FLASH, x, y, 0, 0, 0.0, 0.3, lerp(col, (1, 1, 1), 0.35), "burst", None, 0, 0, 0, 0, 0, big])


def _finale_plan(rng, n_shells: int) -> list[float]:
    """Launch times (s after the finale starts): accelerating volleys, then a final salvo."""
    out, t = [], 0.0
    body = max(1, n_shells - 4)
    for k in range(body):
        out.append(t)
        t += max(0.12, 0.55 * (1 - k / body)) * (0.6 + 0.8 * rng.random())
    out += [t + 0.35 + 0.08 * j for j in range(4)]
    return out


def _substeps(dt) -> tuple[float, int]:
    """Split the frame's dt into equal physics steps of at most MAX_DT, so a high master
    speed makes the particles move faster instead of falling behind the clock. A stalled
    frame (dt > MAX_FRAME_DT) is cut short so nothing teleports."""
    total = min(MAX_FRAME_DT, max(0.0, dt or 0.0))
    n = max(1, math.ceil(total / MAX_DT - 1e-9))
    return total / n, n


def _fw_step(st, P, rng, dt, g):
    """Advance every particle by dt: shells climb and burst, sparks and embers fly."""
    alive = []
    ylo = st["ylo"] - 2.0
    for s in st["parts"]:
        kind = s[0]
        s[5] += dt
        age, life = s[5], s[6]
        if kind == SHELL:
            u = min(1.0, age / life)
            e = 1 - (1 - u) ** 2                    # decelerating climb, like a real shell
            s[1] = s[11] + (s[13] - s[11]) * e
            s[2] = s[12] + (s[14] - s[12]) * e
            if u >= 1.0:
                _burst(st, P, rng, s)
                continue
            alive.append(s)
            continue
        if age >= life:
            continue
        if kind == FLASH or age < 0:               # flashes don't move; embers wait to be born
            alive.append(s)
            continue
        if kind == SPARK:
            drag = 2.2 if s[8] == "Willow" else 2.6
            gg = g * (0.55 if s[8] == "Willow" else 1.0)
        else:                                      # ember: heavy drag, sinks slowly
            drag, gg = 2.8, g * 0.45
        k = math.exp(-drag * dt)
        s[3] *= k
        s[4] = s[4] * k - gg * dt
        s[1] += s[3] * dt
        s[2] += s[4] * dt
        if s[2] < ylo:                             # fell past the lowest bulb
            continue
        alive.append(s)
    # (sparks that _burst appends to st["parts"] are visited by this same loop)
    st["parts"] = alive


@frame_effect("fireworks", "Porch fireworks", [
    Param("rate", "number", 22, "Shells per minute", 2, 120, 1,
          help="how often a shell launches from the porch (on average)"),
    Param("burst_size", "int", 38, "Burst size (sparks)", 20, 60, 1,
          help="sparks in each burst"),
    Param("colors", "colors", ["#ff2020", "#20ff40", "#ffc020", "#3a8cff", "#ff40d0", "#ffffff"],
          "Shell colours", help="each shell picks one; follows the layer's palette"),
    Param("style", "choice", "Mixed", "Shell style", options=STYLES,
          help="Mixed picks peonies, rings, gold willows, two-tone and crackle shells at random"),
    Param("gravity", "number", 3.2, "Gravity", 0, 10, 0.1,
          help="how fast sparks fall (bulbs/s²)"),
    Param("burst_speed", "number", 22, "Burst spread", 6, 36, 0.5,
          help="how far the sparks fly out (initial speed, bulbs/s)"),
    Param("crackle", "number", 0.4, "Sparkle / crackle", 0, 1, 0.05,
          help="glitter in the fading sparks and how often a shell crackles"),
    Param("embers", "number", 0.8, "Embers", 0, 2, 0.05,
          help="dim glowing embers drifting down after each burst"),
    Param("trail", "number", 0.14, "Trail length (s)", 0.02, 0.6, 0.01,
          help="afterglow behind moving shells and sparks"),
    Param("finale", "number", 0, "Grand finale every (s)", 0, 600, 5,
          help="every N seconds a rapid volley of many shells ends in a big salvo; 0 = off"),
    Param("finale_shells", "int", 14, "Finale shells", 6, 40, 1,
          help="shells launched in each finale"),
    Param("launch_zone", "choice", "Porch", "Launch from", options=LAUNCH_ZONES,
          help="bulbs the shells launch from (falls back to the bottom of the target)"),
    Param("seed", "int", 7, "Seed", 0, 9999, 1),
], "Fireworks launched from the porch: a bright spark climbs into the front gable, slows and "
   "bursts at apex height into coloured sparks that spread, droop under gravity, glitter and "
   "fade, with gold willows, rings, crackling shells and embers drifting down onto the roofline.",
   group="Physics")
def fireworks(t, ctx, P, st):
    L, idx = ctx.layout, ctx.idx
    n = len(L.pixels)
    frame = [BLACK] * n
    if not idx:
        return frame
    _fw_setup(ctx, P, st)
    if st.get("seed") != P["seed"] or "rng" not in st:
        st["seed"] = P["seed"]
        st["rng"] = random.Random(P["seed"] * 7919 + 17)
    rng = st["rng"]
    dt, steps = _substeps(ctx.dt)
    clock = ctx.clock

    # ---------------------------------------------------------- scheduling
    fin = max(0.0, P["finale"])
    pending = st.setdefault("pending", [])
    if "next" not in st or st["next"] > clock + 120 or st["next"] < clock - 5:
        st["next"] = clock + 0.25
    # finale timer: seconds since the last finale (or since it was switched on). Comparing
    # elapsed time with the current value keeps it working when 'finale' is modulated.
    last_fin = st.get("fin_last")
    if last_fin is None or fin <= 0 or clock < last_fin:
        st["fin_last"] = clock
    elif clock - last_fin >= fin and not pending:
        st["fin_last"] = clock
        pending[:] = [clock + o for o in _finale_plan(rng, P["finale_shells"])]
        st["fin_end"] = pending[-1] + 2.2
    if pending and pending[0] > clock + 60:          # clock jumped back: drop a stale plan
        pending.clear()
        st.pop("fin_end", None)
    if st.get("fin_end", -1) > clock + 60:
        st.pop("fin_end", None)
    in_finale = pending or clock < st.get("fin_end", -1)
    if pending:
        last = len(pending)
        while pending and pending[0] <= clock:
            pending.pop(0)
            _launch(st, P, rng, big=len(pending) < 4 and last <= 4)
    elif not in_finale and clock >= st["next"]:
        _launch(st, P, rng)
        gap = 60.0 / max(0.1, P["rate"])
        st["next"] = clock + gap * (0.35 + 1.3 * rng.random())
    if in_finale:
        st["next"] = max(st["next"], st.get("fin_end", clock) + 1.0)

    # ---------------------------------------------------------- physics (substepped)
    g = P["gravity"]
    for _ in range(steps):
        _fw_step(st, P, rng, dt, g)
    # bound the particle count (keep the newest) so a huge finale can never run away
    if len(st["parts"]) > 900:
        st["parts"] = st["parts"][-900:]

    # ---------------------------------------------------------- render
    crackle = P["crackle"]
    fresh = list(frame)
    allowed = st["allowed"]
    flashes = []
    for s in st["parts"]:
        kind, age, life = s[0], s[5], s[6]
        if kind == SHELL:
            fl = 0.75 + 0.25 * hash01(s[10], int(clock * 30))
            splat(fresh, L, s[1], s[2], (fl, fl * 0.72, fl * 0.35), 1.1, allowed)
            continue
        if kind == FLASH:
            flashes.append(s)
            continue
        if age < 0:                                # ember not born yet
            continue
        u = age / life
        if kind == SPARK:
            col = s[9] if s[8] == "Two-tone" and u > 0.45 else s[7]
            if u < 0.12:                           # white-hot at the moment of the burst
                col = lerp(HOT, col, u / 0.12)
            bright = (1 - u) ** 1.1
            if s[8] == "Willow":
                bright = (1 - u) ** 0.9 * 0.85
            if s[8] == "Crackle" and u > 0.4:
                on = hash01(s[10], int(clock * 18)) < 0.45
                col, bright = ((1.0, 0.95, 0.8), 0.95) if on else (col, bright * 0.15)
            elif crackle > 0 and u > 0.5:
                bright *= 1 - crackle * 0.8 * hash01(s[10], int(clock * 14))
            c = (col[0] * bright, col[1] * bright, col[2] * bright)
            splat(fresh, L, s[1], s[2], c, 0.95, allowed)
        else:
            bright = 0.45 * math.sin(math.pi * min(1.0, u * 1.4)) ** 0.5 * (1 - u) * \
                (0.6 + 0.4 * hash01(s[10], int(clock * 9)))
            c = (s[7][0] * bright, s[7][1] * bright, s[7][2] * bright)
            splat(fresh, L, s[1], s[2], c, 1.0, allowed)
    dt = dt * steps                                # the whole frame, for the afterglow below

    # flashes: the launch puff on the porch and a soft glow lighting the roof at each burst
    px = L.pixels
    for s in flashes:
        u = s[5] / s[6]
        if s[8] == "launch":
            e = (1 - u) ** 2
            splat(fresh, L, s[1], s[2] - 0.3, (s[7][0] * e, s[7][1] * e, s[7][2] * e), 1.6, allowed)
        else:
            e = 0.32 * math.exp(-u * 4) * (1.4 if s[15] else 1.0)
            R = 7.0
            for i, d in near(L, s[1], s[2], R):
                if i in allowed:
                    w = e * (1 - d / R)
                    a = fresh[i]
                    fresh[i] = (a[0] + s[7][0] * w, a[1] + s[7][1] * w, a[2] + s[7][2] * w)

    # afterglow: trails behind everything that moves, independent of frame rate
    glow = st["glow"]
    kd = math.exp(-dt / max(0.02, P["trail"])) if dt > 0 else 1.0
    for i in idx:
        f = fresh[i]
        o = glow.get(i)
        if o is not None:
            r, gg_, b = o[0] * kd, o[1] * kd, o[2] * kd
            c = (f[0] if f[0] > r else r, f[1] if f[1] > gg_ else gg_, f[2] if f[2] > b else b)
        else:
            c = f
        if c[0] + c[1] + c[2] > 0.003:
            glow[i] = c
            frame[i] = c
        elif o is not None:
            del glow[i]
    return _clamp_frame(frame, idx)


# ====================================================================== snowdrift
FRONT_ZONES = ("Lower roofline", "Accent")
TREE_ZONE = "Peak"             # the zigzag rows under the front gable: snow on the boughs
REACH = 0.75                   # how far either side of a bulb a flake still lands on it
TREE_CATCH = 0.16              # chance a flake falling in front of the gable settles on each row
TREE_HOLD = 0.5                # rows hold less snow, so the tree stays readable
TREE_GAIN = 3.0                # a flake settling on the tree is spread over many close bulbs
TREE_SHARE = 1.5               # over the tree, this many times 'front' of the flakes fall in front
SETTLE = 0.03                  # per second: how fast snow evens out between neighbours
FLAKE_WHITE = (0.9, 0.93, 1.0)
SNOW_WHITE = (0.78, 0.86, 1.0)
CLEAR_TIME = 4.0               # seconds a clearing gust takes to sweep across the house


def _bins(items):
    b: dict[int, list] = {}
    for it in items:
        b.setdefault(math.floor(it[1]), []).append(it)
    return b


def _surface(bins, x: float, y: float):
    """(index, x, y) of the highest surface bulb under (x, y) within REACH, or None.
    Same rule as geometry.surface_below, restricted to one depth plane of the target."""
    best = None
    k = math.floor(x)
    for j in (k - 1, k, k + 1):
        for it in bins.get(j, ()):
            if it[2] < y and abs(it[1] - x) <= REACH and (best is None or it[2] > best[2]):
                best = it
    return best


def _sd_setup(ctx, st):
    L, idx = ctx.layout, ctx.idx
    key = _layout_key(ctx)
    if st.get("key") == key:
        return
    px = L.pixels
    same_layout = st.get("key") is not None and st["key"][:2] == key[:2]
    st["key"] = key
    allowed = st["allowed"] = set(idx)
    if not same_layout:
        st["flakes"], st["snow"] = [], {}
    else:                                   # keep the snow that is still on the new target
        st["snow"] = {i: v for i, v in st.get("snow", {}).items() if i in allowed}
    st["dyn"], st["dyn_t"] = {}, None
    # three depth planes: the main rooflines (back), the zigzag tree in front of the gable
    # wall, and the lower roofline (porch / garage) closest to the street
    tree = set(_zone(L, TREE_ZONE)) & allowed
    if len(tree) == len(idx):               # only the tree: its rows are the surfaces
        tree = set()
    front = set()
    for z in FRONT_ZONES:
        front.update(_zone(L, z))
    front = (front & allowed) - tree
    if len(front) == len(idx):              # only the lower roofline: one plane
        front = set()
    back = [i for i in idx if i not in front and i not in tree]
    front_l, tree_l = sorted(front), sorted(tree)
    st["planes"] = [_bins([(i, px[i].x, px[i].y) for i in back]),
                    _bins([(i, px[i].x, px[i].y) for i in front_l])]
    st["tree_bins"] = _bins([(i, px[i].x, px[i].y) for i in tree_l])
    st["has_front"], st["has_tree"] = bool(front), bool(tree)
    # neighbours on the same run and plane (tree: the same row), for spreading and settling
    nb = {}
    for plane, flat in ((back, False), (front_l, False), (tree_l, True)):
        pb = _bins([(i, px[i].x, px[i].y) for i in plane])
        for i in plane:
            p = px[i]
            k = math.floor(p.x)
            nb[i] = [it[0] for j in (k - 2, k - 1, k, k + 1, k + 2) for it in pb.get(j, ())
                     if it[0] != i and px[it[0]].run == p.run and
                     math.hypot(it[1] - p.x, it[2] - p.y) <= 1.3 and
                     (not flat or abs(it[2] - p.y) < 0.3)]
    st["nb"] = nb
    # each settling pair once (i < j), for a conservative exchange between neighbours
    st["pairs"] = [(i, j) for i, ns in nb.items() for j in ns if i < j]
    # how much snow each bulb can hold: less on steep roof edges, uneven like real drifts
    hold = {}
    for i in idx:
        sl = 0.0
        ns = nb.get(i, ())
        if ns:
            p = px[i]
            sl = max(abs(px[j].y - p.y) / max(0.3, abs(px[j].x - p.x)) for j in ns)
        hold[i] = (1 - 0.3 * min(1.0, sl)) * (0.6 + 0.4 * hash01(i, 4242)) * \
            (TREE_HOLD if i in tree else 1.0)
    st["hold"] = hold
    # closely spaced bulbs each catch fewer flakes: scale deposits so every roofline fills
    # at about the same pace; the lower roofline (in front) catches a little extra
    gain = {}
    for i in idx:
        ns = nb.get(i, ())
        sp = min(abs(px[j].x - px[i].x) for j in ns) if ns else 1.0
        gain[i] = (1.0 / min(1.5, max(0.4, sp))) * (1.5 if i in front else 1.0)
    # over the tree most flakes fall in front of the gable: the outline above it and the
    # tree's many rows share them, so both get a boost to keep pace with the other rooflines
    tcols = {math.floor(px[i].x) for i in tree}
    for i in idx:
        if i in tree:
            gain[i] *= TREE_GAIN
        elif i not in front and math.floor(px[i].x) in tcols:
            gain[i] *= 1.0 / max(0.25, 1 - TREE_SHARE * 0.5)
    st["gain"] = gain
    if idx:
        xs = [px[i].x for i in idx]
        ys = [px[i].y for i in idx]
        st["xr"] = (min(xs) - 1.0, max(xs) + 1.0)
        st["ylo"], st["yhi"] = min(ys), max(ys)
        prof: dict[int, float] = {}
        for i in idx:
            k = math.floor(px[i].x)
            prof[k] = max(prof.get(k, -1e9), px[i].y)
        st["prof"] = prof

        def cols(s):
            out: set[int] = set()
            for i in s:
                k = math.floor(px[i].x)
                out.update((k - 1, k, k + 1))
            return out
        st["tree_cols"] = cols(tree)
        st["front_cols"] = cols(front) | st["tree_cols"]


def _dyn_hold(st, ctx, seed: int):
    """Slowly wandering extra capacity per bulb (0.55..1): drifts build in some places and
    thin out in others over the minutes. Refreshed about once a second."""
    clock = ctx.clock
    if st["dyn_t"] is not None and abs(clock - st["dyn_t"]) < 1.0:
        return st["dyn"]
    st["dyn_t"] = clock
    px = ctx.layout.pixels
    tt = clock / 400.0
    dyn = st["dyn"]
    for i in ctx.idx:
        f = fbm2(px[i].x * 0.12, tt + px[i].y * 0.05, 2, seed + 313)
        dyn[i] = 0.55 + 0.45 * min(1.0, max(0.0, 1.8 * (f - 0.5) + 0.5))
    return dyn


def _deposit(st, ctx, i: int, x: float, amount: float, cap: float, dyn):
    """Add snow where a flake landed, shared with the neighbour on the side it fell."""
    px = ctx.layout.pixels
    snow, hold = st["snow"], st["hold"]
    share = [(i, 1.0)]
    for j in st["nb"].get(i, ()):
        d = abs(px[j].x - x)
        if d < 1.2:
            share.append((j, 0.45 * (1 - d / 1.2)))
    tot = sum(w for _, w in share)
    for j, w in share:
        cur = snow.get(j, 0.0)
        hc = cap * hold.get(j, 1.0) * dyn.get(j, 1.0)
        new = cur + amount * w / tot
        snow[j] = new if new <= hc else max(cur, hc)


def _sd_step(st, ctx, P, rng, dt, rate, wind, dyn):
    """Spawn, move and land the flakes for one physics step of dt seconds."""
    flakes = st["flakes"]
    width = st["xr"][1] - st["xr"][0]
    st["spawn"] = st.get("spawn", 0.0) + rate * dt * max(0.2, width / 85.0)
    if st["spawn"] > 50:
        st["spawn"] = 50.0
    while st["spawn"] >= 1.0 and len(flakes) < 400:
        st["spawn"] -= 1.0
        x = st["xr"][0] + width * rng.random() - wind * 2.0
        k = math.floor(x)
        top = max((st["prof"][j] for j in range(k - 3, k + 4) if j in st["prof"]), default=st["yhi"])
        y = top + 0.9 + 1.6 * rng.random()
        share = P["front"] * (TREE_SHARE if k in st["tree_cols"] else 1.0)
        plane = 1 if k in st["front_cols"] and rng.random() < share else 0
        # [x0, y, speed, sway amp, sway freq, phase, age, plane, size, brightness, drift x]
        flakes.append([x, y, P["fall"] * (0.65 + 0.7 * rng.random()), P["sway"] * (0.5 + 0.7 * rng.random()),
                       0.7 + 0.8 * rng.random(), rng.random() * math.tau, 0.0, plane,
                       P["size"] * (0.75 + 0.5 * rng.random()), 0.55 + 0.4 * rng.random(), 0.0])
    if st["spawn"] >= 1.0:
        st["spawn"] = 0.0

    cap = P["max_snow"]
    amount = 0.03 * P["accumulate"]
    gain = st["gain"]
    tree_bins = st["tree_bins"] if st["has_tree"] else None
    alive = []
    ylo = st["ylo"] - 1.5
    for f in flakes:
        f[6] += dt
        x_old = f[0] + f[10] + f[3] * math.sin(f[4] * (f[6] - dt) + f[5])
        y_old = f[1]
        f[10] += wind * dt
        f[1] -= f[2] * dt
        if dt > 0:
            x = f[0] + f[10] + f[3] * math.sin(f[4] * f[6] + f[5])
            if f[7] == 1 and tree_bins is not None:       # passing in front of the tree's rows
                hit = _surface(tree_bins, x_old, y_old + 1e-6)
                if hit is not None and f[1] <= hit[2] and rng.random() < TREE_CATCH:
                    if amount > 0:
                        _deposit(st, ctx, hit[0], x, amount * gain.get(hit[0], 1.0), cap, dyn)
                    continue
            hit = _surface(st["planes"][f[7]], x_old, y_old + 1e-6)
            if hit is not None and f[1] <= hit[2]:
                if amount > 0:
                    _deposit(st, ctx, hit[0], x, amount * gain.get(hit[0], 1.0), cap, dyn)
                continue
        if f[1] < ylo:
            continue
        alive.append(f)
    st["flakes"] = alive


def _sd_settle(st, px, P, dt, cap, dyn, clear_x, clear_dir):
    """Melt, even out between neighbours (conservatively) and apply each bulb's capacity."""
    snow = st["snow"]
    if not snow or dt <= 0:
        return
    # neighbours that both hold snow trade a little towards their mean: nothing is lost,
    # and an isolated pile keeps its snow
    k = SETTLE * dt
    if k > 0:
        delta: dict[int, float] = {}
        for i, j in st["pairs"]:
            a, b = snow.get(i), snow.get(j)
            if a is None or b is None:
                continue
            fl = (b - a) * k
            delta[i] = delta.get(i, 0.0) + fl
            delta[j] = delta.get(j, 0.0) - fl
        for i, d in delta.items():
            snow[i] += d
    # melt: a share of the snow per minute (plus a little, so it does melt away completely)
    m = P["melt"] / 60.0 * dt
    hold = st["hold"]
    relax = 1 - math.exp(-dt / 30.0)
    for i in list(snow):
        v = snow[i]
        if m:
            v -= m * (v + 0.05)
        hc = cap * hold.get(i, 1.0) * dyn.get(i, 1.0)
        if v > cap:
            v = cap
        if m and v > hc:                    # the drift's capacity shrank: it slowly slumps
            v -= (v - hc) * relax           # (with melt off, settled snow simply stays)
        if clear_x is not None and (px[i].x - clear_x) * clear_dir <= 0:
            v *= math.exp(-dt * 4.0)        # the clearing gust has passed this bulb
        if v <= 0.002:
            del snow[i]
            continue
        snow[i] = v


@frame_effect("snowdrift", "Snow that piles up", [
    Param("rate", "number", 9, "Snowfall (flakes/s)", 0, 40, 0.5,
          help="how many flakes start falling each second across the house"),
    Param("size", "number", 1.1, "Flake size", 0.6, 2.5, 0.05,
          help="how many bulbs a flake lights as it drifts past"),
    Param("fall", "number", 1.1, "Fall speed (bulbs/s)", 0.3, 4, 0.05,
          help="average falling speed; each flake varies a little"),
    Param("sway", "number", 0.7, "Sway", 0, 3, 0.05,
          help="how far flakes rock side to side as they fall"),
    Param("wind", "number", 0.25, "Wind", -3, 3, 0.05,
          help="steady sideways drift (negative blows the other way)"),
    Param("gusts", "number", 0.4, "Gusts", 0, 1, 0.05,
          help="the snowfall and wind slowly come and go"),
    Param("front", "number", 0.5, "Snow in front", 0, 1, 0.05,
          help="share of flakes falling in front of the house: past the zigzag tree (where some "
               "settle on its rows) and onto the porch and garage line"),
    Param("accumulate", "number", 1.0, "Pile-up speed", 0, 8, 0.05,
          help="1 = the eaves fill over about eight to ten minutes at the default snowfall"),
    Param("max_snow", "number", 0.8, "Deepest snow (brightness)", 0.1, 1, 0.05,
          help="how bright a bulb gets once it is fully covered"),
    Param("melt", "number", 0.15, "Melt (per minute)", 0, 2, 0.01,
          help="share of the settled snow that melts each minute; the snow levels off where "
               "melting balances the snowfall. 0 = it stays"),
    Param("clear", "number", 0, "Gust clears it every (min)", 0, 120, 1,
          help="every N minutes a gust sweeps the settled snow off and it starts piling up "
               "again; 0 = never"),
    Param("glisten", "number", 0.35, "Glisten", 0, 1, 0.05,
          help="occasional sparkles on the settled snow"),
    Param("color", "color", "#e6eeff", "Flake colour"),
    Param("snow_color", "color", "#c8dcff", "Settled snow colour", help="cool white with a touch of blue"),
    Param("tint", "number", 0.3, "Colour strength", 0, 1, 0.05,
          help="how much the two colours (or a palette) tint the white snow; 1 = use them exactly"),
    Param("seed", "int", 11, "Seed", 0, 9999, 1),
], "Soft white flakes drifting down past the lights with a gentle sway. Each one lands on the "
   "first roofline below it and the snow builds up there, unevenly like real drifts: over "
   "several minutes the eaves, the gable edges, the porch line and the boughs of the zigzag "
   "tree fill with a cool white glow that glistens now and then, settling where melting "
   "balances the snowfall.",
   group="Weather")
def snowdrift(t, ctx, P, st):
    L, idx = ctx.layout, ctx.idx
    n = len(L.pixels)
    frame = [BLACK] * n
    if not idx:
        return frame
    _sd_setup(ctx, st)
    if st.get("seed") != P["seed"] or "rng" not in st:
        st["seed"] = P["seed"]
        st["rng"] = random.Random(P["seed"] * 104729 + 3)
    rng = st["rng"]
    dt, steps = _substeps(ctx.dt)
    clock = ctx.clock
    px = L.pixels
    allowed = st["allowed"]
    dyn = _dyn_hold(st, ctx, P["seed"])

    # gusts: slow noise on the snowfall and wind
    gn = noise2(clock * 0.035, 0.5, P["seed"]) * 2 - 1 if P["gusts"] > 0 else 0.0
    rate = P["rate"] * max(0.0, 1 + P["gusts"] * 0.8 * gn)
    wind = P["wind"] + P["gusts"] * 0.6 * (noise2(clock * 0.05, 7.5, P["seed"]) * 2 - 1)

    # an occasional clearing gust sweeps across in the wind's direction
    clear_x, clear_dir = None, 1.0 if wind >= 0 else -1.0
    period = P["clear"] * 60.0
    last = st.get("clear_last")
    if last is None or period <= 0 or clock < last:
        st["clear_last"], st["sweep"] = clock, None
    elif st.get("sweep") is None and clock - last >= period:
        st["sweep"], st["sweep_dir"] = clock, clear_dir
    if st.get("sweep") is not None:
        u = (clock - st["sweep"]) / CLEAR_TIME
        if u > 1.4 or u < 0:
            st["sweep"], st["clear_last"] = None, clock
        else:
            lo, hi = st["xr"]
            clear_dir = st["sweep_dir"]
            clear_x = lo + (hi - lo) * u if clear_dir > 0 else hi - (hi - lo) * u

    cap = P["max_snow"]
    for _ in range(steps):
        _sd_step(st, ctx, P, rng, dt, rate, wind, dyn)
        _sd_settle(st, px, P, dt, cap, dyn, clear_x, clear_dir)

    # ------------------------------------------------------------ render
    tint = min(1.0, max(0.0, P["tint"]))
    col = lerp(FLAKE_WHITE, P["color"], tint)
    sc = lerp(SNOW_WHITE, P["snow_color"], tint)
    fresh = frame
    for f in st["flakes"]:
        x = f[0] + f[10] + f[3] * math.sin(f[4] * f[6] + f[5])
        b = f[9] * min(1.0, f[6] / 0.6)             # fade in at the top
        splat(fresh, L, x, f[1], (col[0] * b, col[1] * b, col[2] * b), f[8], allowed)
    gl = P["glisten"]
    for i, v in st["snow"].items():
        if i not in allowed:
            continue
        b = v
        if gl > 0 and v > 0.15:
            gv, _ = glint(i, clock, 0.06 * gl, 0.04, 0.35, P["seed"], 1)
            b += gv * 0.45 * v
        a = fresh[i]
        fresh[i] = (a[0] + sc[0] * b, a[1] + sc[1] * b, a[2] + sc[2] * b)
    return _clamp_frame(fresh, idx)
