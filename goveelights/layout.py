"""Virtual pixel strip: every visible bulb of every run, in roofline order,
with 1D and 2D coordinates for effects and the preview.

Per-device layout lives in the device's config entry:
  order     position of this run in the virtual strip
  reverse   traverse the run from its last bulb to its first
  hidden    physical bulb indexes that are not visible (kept dark, skipped)
  geometry  [[index, x, y], ...] polyline knots in bulb-spacing units, as seen
            from outside with the house "unrolled"; bulbs between knots are
            interpolated. Missing -> a straight horizontal line.
  enabled   include in the virtual strip (default true)
"""
from __future__ import annotations

from dataclasses import dataclass, field


class Pixel:
    __slots__ = ("i", "n", "u", "run", "mac", "local", "ri", "rn", "ru",
                 "x", "y", "sx", "sy", "spanx", "spany", "nx", "ny", "rny", "rnx")

    def __repr__(self):
        return f"Pixel(i={self.i}, run={self.run!r}, local={self.local}, x={self.x:.1f}, y={self.y:.1f})"


@dataclass
class Run:
    mac: str
    name: str
    count: int
    reverse: bool = False
    hidden: set[int] = field(default_factory=set)
    geometry: list[list[float]] = field(default_factory=list)
    trim: tuple | None = None            # per-run colour gains (r, g, b), e.g. to warm up a bluish run
    pixels: list[Pixel] = field(default_factory=list)   # visible, virtual order

    def positions(self) -> list[tuple[float, float]]:
        """(x, y) for every physical bulb index, interpolated from knots."""
        knots = {}
        for k in self.geometry:                    # one knot per index (a hand-edited file may repeat one)
            knots[int(k[0])] = (int(k[0]), float(k[1]), float(k[2]))
        knots = sorted(knots.values())
        if not knots:
            return [(float(i), 0.0) for i in range(self.count)]
        out = []
        for i in range(self.count):
            if i <= knots[0][0]:
                a = b = knots[0]
            elif i >= knots[-1][0]:
                a = b = knots[-1]
            else:
                a = max((k for k in knots if k[0] <= i), key=lambda k: k[0])
                b = min((k for k in knots if k[0] > i), key=lambda k: k[0])
            if a is b or b[0] == a[0]:
                # extrapolate along the nearest segment so ends keep spacing
                if len(knots) >= 2 and i < knots[0][0]:
                    a, b = knots[0], knots[1]
                elif len(knots) >= 2 and i > knots[-1][0]:
                    a, b = knots[-2], knots[-1]
                else:
                    out.append((a[1] + (i - a[0]), a[2]))
                    continue
            f = (i - a[0]) / (b[0] - a[0])
            out.append((a[1] + f * (b[1] - a[1]), a[2] + f * (b[2] - a[2])))
        return out


# Generic roles for layer targets, so presets written for one house play sensibly on
# another. A layer target the layout has no run or zone for is looked up as:
#   1. target_aliases[name] from the config ({"Peak": "Gable"}; a name or a list of names)
#   2. its role (ROLE_OF, or the name itself when it is a role): target_aliases[role],
#      then the first run/zone whose name contains one of that role's ROLE_WORDS
#   3. nothing; if the whole target then lights nothing: target_aliases["*"], else "all"
# Targets the layout does have are never looked up, so a house that has every name a
# preset uses renders exactly as before.
ROLES = ("peak", "roofline", "porch", "accent")
ROLE_OF = {
    "peak": "peak", "both peaks": "peak", "whole front gable": "peak", "front gable outline": "peak",
    "south gable": "peak", "garage peak": "peak", "zigzag top half": "peak", "zigzag bottom half": "peak",
    "tree": "peak", "gable": "peak", "apex": "peak",
    "eaves": "roofline", "lower roofline": "roofline", "front eave": "roofline", "flat eaves": "roofline",
    "north wall": "roofline", "south wall": "roofline", "whole front": "roofline", "roof": "roofline",
    "porch": "porch", "garage": "porch", "door": "porch",
    "accent": "accent",
}
ROLE_WORDS = {
    "peak": ("peak", "gable", "apex", "tree", "zigzag"),
    "roofline": ("roof", "eave", "gutter", "front"),
    "porch": ("porch", "garage", "door", "entry"),
    "accent": ("accent", "window", "trim"),
}


def role_of(name) -> str | None:
    return ROLE_OF.get(str(name).strip().lower()) if isinstance(name, str) else None


class Layout:
    def __init__(self, runs: list[Run], zones: list[dict] | None = None, aliases: dict | None = None):
        self.runs = runs
        self.zone_specs = zones or []
        self.aliases = {str(k): v for k, v in aliases.items()} if isinstance(aliases, dict) else {}
        self._targets: dict = {}
        self._layer_targets: dict = {}
        self.pixels: list[Pixel] = []
        pos_all = {r.mac: r.positions() for r in runs}
        # runs without a mapped shape (e.g. a new WLED strip) go on their own line below
        # the house in the preview until camera mapping places them
        shaped = [pos_all[r.mac][k] for r in runs if r.geometry
                  for k in range(r.count) if k not in r.hidden]     # visible bulbs only
        if shaped:
            bx = min(x for x, _ in shaped)
            by = min(y for _, y in shaped)
            k = 0
            for r in runs:
                if not r.geometry:
                    k += 1
                    pos_all[r.mac] = [(bx + i * 0.5, by - 3.0 * k) for i in range(r.count)]
        for r in runs:
            order = range(r.count - 1, -1, -1) if r.reverse else range(r.count)
            vis = [k for k in order if k not in r.hidden]
            for ri, local in enumerate(vis):
                p = Pixel()
                p.run, p.mac, p.local, p.ri, p.rn = r.name, r.mac, local, ri, len(vis)
                p.ru = ri / max(1, len(vis) - 1)
                p.x, p.y = pos_all[r.mac][local]
                r.pixels.append(p)
                self.pixels.append(p)
        n = len(self.pixels)
        xs = [p.x for p in self.pixels] or [0.0]
        ys = [p.y for p in self.pixels] or [0.0]
        self.minx, self.maxx, self.miny, self.maxy = min(xs), max(xs), min(ys), max(ys)
        spanx, spany = max(1e-6, self.maxx - self.minx), max(1e-6, self.maxy - self.miny)
        for i, p in enumerate(self.pixels):
            p.i, p.n, p.u = i, n, i / max(1, n - 1)
            p.sx, p.sy, p.spanx, p.spany = p.x - self.minx, p.y - self.miny, spanx, spany
            p.nx, p.ny = p.sx / spanx, p.sy / spany
        for r in runs:
            ys = [p.y for p in r.pixels] or [0.0]
            xs = [p.x for p in r.pixels] or [0.0]
            lo, hi = min(ys), max(ys)
            xlo, xhi = min(xs), max(xs)
            for p in r.pixels:
                p.rny = (p.y - lo) / (hi - lo) if hi > lo else 0.0
                p.rnx = (p.x - xlo) / (xhi - xlo) if xhi > xlo else 0.5

        # zones: named groups of bulbs, e.g. {"name": "North wall",
        #   "parts": [{"run": "Eaves", "from": 0, "to": 30}]}   (physical indexes, inclusive)
        self.zones: dict[str, list[int]] = {}
        for z in self.zone_specs:
            members = set()
            for part in z.get("parts", []):
                r = self.run(part.get("run", ""))
                if not r:
                    continue
                lo, hi = int(part.get("from", 0)), int(part.get("to", r.count - 1))
                members |= {p.i for p in r.pixels if lo <= p.local <= hi}
            if members and z.get("name"):
                self.zones[z["name"]] = sorted(members)

    @property
    def n(self) -> int:
        return len(self.pixels)

    def target_names(self) -> list[str]:
        return [r.name for r in self.runs] + [z for z in self.zones if z not in {r.name for r in self.runs}]

    def indices(self, target) -> list[int]:
        """Virtual indexes for a layer target: "all", a run name, a zone name, or a list of those."""
        key = tuple(target) if isinstance(target, list) else target
        hit = self._targets.get(key)
        if hit is not None:
            return hit
        names = target if isinstance(target, list) else [target]
        if not names or "all" in names or "" in names or None in names:
            out = list(range(self.n))
        else:
            s = set()
            for nm in names:
                if nm in self.zones:
                    s.update(self.zones[nm])
                else:
                    r = self.run(nm)
                    if r:
                        s.update(p.i for p in r.pixels)
            out = sorted(s)
        self._targets[key] = out
        return out

    def has(self, name) -> bool:
        """A run or zone of this name exists ("all" always does)."""
        if name in ("all", "", None):
            return True
        return isinstance(name, str) and (name in self.zones or self.run(name) is not None)

    def missing(self, target) -> list[str]:
        """The names in a layer target that this layout has no run or zone for."""
        names = target if isinstance(target, list) else [target]
        return [str(n) for n in names if not self.has(n)]

    def _alias(self, key, depth=0) -> list[str]:
        """Names this layout has for an alias entry (a name or a list; aliases may chain)."""
        v = self.aliases.get(key)
        out = []
        for nm in (v if isinstance(v, list) else [v] if v else []):
            if self.has(nm):
                out.append(nm)
            elif depth < 3 and isinstance(nm, str):
                out += self._alias(nm, depth + 1) or self._by_role(nm, depth + 1)
        return out

    def _by_role(self, name, depth=0) -> list[str]:
        role = name.strip().lower() if isinstance(name, str) and name.strip().lower() in ROLES else role_of(name)
        if not role:
            return []
        if role in self.aliases and depth < 3:
            hit = self._alias(role, depth + 1)
            if hit:
                return hit
        for nm in [r.name for r in self.runs] + list(self.zones):
            low = nm.lower()
            if any(w in low for w in ROLE_WORDS[role]):
                return [nm]
        return []

    def substitute(self, name) -> list[str]:
        """What plays for a target name this layout doesn't have (see ROLES above); []
        when nothing fits (the layer then falls back to "*" or the whole house)."""
        if self.has(name):
            return [name]
        return self._alias(name) or self._by_role(name)

    def fallback(self) -> list[str]:
        hit = self._alias("*")
        return hit or ["all"]

    def layer_indices(self, target) -> list[int]:
        """Indexes for a scene layer's target: like indices(), but a name this layout
        doesn't have plays on its stand-in (an alias, a run or zone of the same role),
        and a target that would light nothing at all plays on the fallback (normally the
        whole house) instead of staying dark."""
        key = tuple(target) if isinstance(target, list) else target
        hit = self._layer_targets.get(key)
        if hit is not None:
            return hit
        out = self.indices(target)
        miss = self.missing(target)
        if miss:
            names = target if isinstance(target, list) else [target]
            sub = [s for nm in names for s in self.substitute(nm)]
            out = self.indices(sub) if sub else []
            if not out:
                out = self.indices(self.fallback())
        self._layer_targets[key] = out
        return out

    def plays_on(self, name) -> str:
        """Human-readable: where a missing target name plays instead."""
        sub = self.substitute(name)
        return ", ".join(sub) if sub else "the " + ("whole house" if self.fallback() == ["all"] else
                                                   ", ".join(self.fallback()))

    def run(self, name_or_mac: str) -> Run | None:
        for r in self.runs:
            if name_or_mac in (r.name, r.mac):
                return r
        return None

    @classmethod
    def from_config(cls, cfg) -> "Layout":
        runs = []
        for mac, d in sorted(cfg.devices.items(), key=lambda kv: kv[1].get("order", 0)):
            if d.get("enabled", True) is False:
                continue
            count = d.get("pixel_count")
            if not count:
                continue   # count not confirmed yet: not part of the strip
            runs.append(Run(mac=mac, name=d.get("name") or mac, count=int(count),
                            reverse=bool(d.get("reversed")), hidden=set(d.get("hidden") or []),
                            geometry=d.get("geometry") or [],
                            trim=tuple(float(v) for v in d["trim"]) if d.get("trim") else None))
        return cls(runs, cfg.data.get("zones", []), cfg.data.get("target_aliases"))

    def summary(self) -> str:
        lines = [f"virtual strip: {self.n} visible bulbs, x {self.minx:.1f}..{self.maxx:.1f}, y {self.miny:.1f}..{self.maxy:.1f}"]
        for r in self.runs:
            if r.pixels:
                lines.append(f"  {r.name:<12} {r.mac}  bulbs {r.count}, visible {len(r.pixels)}, hidden {sorted(r.hidden)}"
                             f", reverse={r.reverse}, virtual {r.pixels[0].i}..{r.pixels[-1].i}")
        for z, idx in self.zones.items():
            lines.append(f"  zone {z!r}: {len(idx)} bulbs")
        return "\n".join(lines)
