"""The hologram-fan scenes.

Every scene is a pure function of the loop phase t (0 <= t < 1): anything that moves does a
whole number of cycles per loop, so frame N (t = 1) is exactly frame 0 and the clip loops
without a seam. Sizes are in fan radii (R), so a scene looks the same at 240 px or 1080 px.

Made for POV fans: black is "off" (see-through), so everything is bold, bright, saturated
and drawn on pure black; strokes are thick; nothing important sits near the rim, where
the blades move fastest and the picture is thinnest. On a multi-fan "splice" set the main
subject sits in the hero fan and things travel across the others.
"""
from __future__ import annotations

import math
import random
from datetime import date

from .canvas import Canvas, mix, scale
from .layout import FanLayout

TAU = 2 * math.pi
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
SNOW = (235, 245, 255)


def hex_rgb(h: str) -> tuple:
    h = str(h).strip().lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    if len(h) != 6:
        raise ValueError(f"bad colour {h!r}")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def vivid(c, floor: int = 190) -> tuple:
    """Bright enough for a fan: dark colours (a navy team colour, say) are scaled up until
    their strongest channel reaches `floor`; hue is kept. Near-black stays a dim grey-white
    rather than vanishing."""
    m = max(c)
    if m < 8:
        return (floor, floor, floor)
    if m >= floor:
        return tuple(c)
    return scale(c, floor / m)


class Ctx:
    """Everything a scene needs to know about the clip being made."""

    def __init__(self, layout: FanLayout, fps: float, seconds: float, colors=None, team=None,
                 year=None, seed: int = 7):
        self.layout = layout
        self.W, self.H = layout.canvas_size
        self.fans = layout.centres()
        self.R = layout.size / 2 * 0.97            # usable radius
        self.hero = layout.hero()
        self.fps, self.secs = float(fps), float(seconds)
        self.colors = [vivid(hex_rgb(c)) for c in (colors or [])]
        self.team = [vivid(hex_rgb(c), 200) for c in (team or [])][:2]
        y = date.today()
        self.year = int(year) if year else (y.year + 1 if y.month >= 7 else y.year)
        self.seed = seed

    def cyc(self, per_sec: float) -> int:
        """A whole number of cycles per loop, as close as possible to per_sec a second."""
        return max(1, int(round(per_sec * self.secs)))

    def pal(self, default) -> list:
        return self.colors or [hex_rgb(c) for c in default]

    def rnd(self, salt: int = 0) -> random.Random:
        return random.Random(self.seed * 1000 + salt)


class Scene:
    id = ""
    name = ""
    blurb = ""
    seconds = 8.0
    fps = 24
    default_colors: tuple = ()
    palette_role = ""          # what palette colours recolour (shown in the app)
    splice = True              # False: each fan of a set shows its own copy, not a slice of one picture

    def __init__(self, ctx: Ctx):
        self.ctx = ctx
        self.setup()

    def setup(self):
        pass

    def draw(self, cv: Canvas, t: float):
        raise NotImplementedError

    @classmethod
    def info(cls) -> dict:
        return {"id": cls.id, "name": cls.name, "blurb": cls.blurb, "seconds": cls.seconds, "fps": cls.fps,
                "colors": list(cls.default_colors), "palette_role": cls.palette_role,
                "team": cls.id == "football", "splice": cls.splice}


# ---------------------------------------------------------------- shared pieces
class Snowfall:
    """Flakes falling through each fan's circle; each falls a whole number of times a loop."""

    def __init__(self, ctx: Ctx, per_fan: int, salt: int = 1, big: float = 1.0, fans=None):
        r = ctx.rnd(salt)
        R = ctx.R
        self.flakes = []
        for fi, (cx, cy) in enumerate(ctx.fans):
            if fans is not None and fi not in fans:
                continue
            for _ in range(per_fan):
                self.flakes.append((cx + (r.random() * 2 - 1) * R, cy, r.random(), r.choice((1, 1, 2)),
                                    r.choice((1, 2)), r.random(), (0.022 + r.random() * 0.026) * R * big,
                                    0.06 * R * r.random()))
        self.R = R

    def draw(self, cv: Canvas, t: float, color=SNOW):
        R = self.R
        span = 2.3 * R
        for (x, cy, y0, k, j, ph, rad, amp) in self.flakes:
            y = cy - 1.15 * R + ((y0 + k * t) % 1.0) * span
            xx = x + amp * math.sin(TAU * (j * t + ph))
            cv.circle(xx, y, rad, color)


def twinkle_stars(cv: Canvas, ctx: Ctx, t: float, n: int = 10, salt: int = 3, avoid=None, color=(255, 246, 200)):
    r = ctx.rnd(salt)
    R = ctx.R
    for (cx, cy) in ctx.fans:
        for _ in range(n):
            a, d = r.random() * TAU, (0.25 + 0.65 * r.random() ** 0.7) * R
            x, y = cx + d * math.cos(a), cy + d * math.sin(a)
            k, ph = r.choice((1, 2, 3)), r.random()
            if avoid and math.hypot(x - avoid[0], y - avoid[1]) < avoid[2]:
                continue
            b = 0.5 + 0.5 * math.sin(TAU * (k * t + ph))
            if b > 0.15:
                cv.sparkle(x, y, R * (0.05 + 0.05 * b), color, a=b, angle=0.3)


def ghost(cv: Canvas, x, y, s, phase, color, mouth=1.0, glow=True):
    """A friendly sheet ghost: round head, wavy hem, see-through (black) eyes and mouth."""
    if glow:
        cv.glow(x, y + 0.3 * s, 1.9 * s, scale(color, 0.45), steps=4)
    pts = []
    for k in range(13):                             # head: left to right over the top
        th = math.pi + math.pi * k / 12
        pts.append((x + s * math.cos(th), y + s * math.sin(th)))
    hem = y + 1.15 * s
    n = 14
    for k in range(n + 1):                          # hem: right to left, waving
        u = k / n
        xx = x + 1.12 * s - 2.24 * s * u
        pts.append((xx, hem + 0.2 * s * math.sin(TAU * (2 * u) + phase)))
    pts.insert(13, (x + 1.12 * s, hem - 0.2 * s))
    pts.append((x - 1.12 * s, hem - 0.2 * s))
    cv.polygon(pts, color)
    cv.ellipse(x - 0.38 * s, y - 0.1 * s, 0.17 * s, 0.26 * s, BLACK, seg=16)
    cv.ellipse(x + 0.38 * s, y - 0.1 * s, 0.17 * s, 0.26 * s, BLACK, seg=16)
    if mouth > 0.05:
        cv.ellipse(x, y + 0.45 * s, 0.2 * s, 0.25 * s * mouth, BLACK, seg=16)


def bat(cv: Canvas, x, y, s, flap, color):
    """A bat (purple, not black: black doesn't show on a fan)."""
    f = math.cos(flap)
    cv.ellipse(x, y, 0.28 * s, 0.4 * s, color, seg=16)
    cv.circle(x, y - 0.45 * s, 0.24 * s, color)
    cv.polygon([(x - 0.12 * s, y - 0.62 * s), (x - 0.22 * s, y - 0.9 * s), (x - 0.02 * s, y - 0.66 * s)], color)
    cv.polygon([(x + 0.12 * s, y - 0.62 * s), (x + 0.22 * s, y - 0.9 * s), (x + 0.02 * s, y - 0.66 * s)], color)
    for sgn in (-1, 1):
        tip = y - 0.9 * s * f
        pts = [(x + sgn * 0.2 * s, y - 0.2 * s), (x + sgn * 1.0 * s, tip - 0.2 * s), (x + sgn * 1.55 * s, tip + 0.1 * s),
               (x + sgn * 1.2 * s, tip + 0.25 * s * (1 + f) / 2 + 0.2 * s), (x + sgn * 0.85 * s, tip + 0.5 * s),
               (x + sgn * 0.5 * s, y + 0.25 * s), (x + sgn * 0.2 * s, y + 0.2 * s)]
        cv.polygon(pts, color)
    cv.circle(x - 0.09 * s, y - 0.48 * s, 0.06 * s, (255, 230, 60))
    cv.circle(x + 0.09 * s, y - 0.48 * s, 0.06 * s, (255, 230, 60))


def confetti(cv: Canvas, ctx: Ctx, t: float, colors, start: float, length: float, per_fan: int = 26, salt: int = 9):
    """Pieces bursting out and fluttering down, during [start, start + length) of the loop."""
    u = (t - start) % 1.0
    if u >= length:
        return
    v = u / length
    r = ctx.rnd(salt)
    R = ctx.R
    for (cx, cy) in ctx.fans:
        for k in range(per_fan):
            a = r.random() * TAU
            sp = (0.35 + 0.6 * r.random()) * R
            col = colors[k % len(colors)]
            ease = 1 - (1 - min(1.0, v * 2.2)) ** 2
            x = cx + math.cos(a) * sp * ease + 0.05 * R * math.sin(TAU * (3 * v + r.random()))
            y = cy - 0.1 * R + math.sin(a) * sp * ease * 0.7 + 0.9 * R * v * v
            spin = TAU * (2 * v + r.random())
            w = 0.07 * R
            hgt = 0.04 * R * (0.3 + 0.7 * abs(math.cos(spin)))
            fade = 1.0 if v < 0.75 else (1 - v) / 0.25
            cv.ellipse(x, y, w, hgt, scale(col, fade), angle=spin * 0.5, seg=8)


class Xf:
    """Local drawing frame: unit s, origin (ox, oy), rotation ang; optional colour override
    and pixel offset (used to draw a black outline under a figure)."""

    def __init__(self, cv: Canvas, ox, oy, s, ang=0.0, flip=False):
        self.cv, self.ox, self.oy, self.s = cv, ox, oy, s
        self.ca, self.sa, self.fx = math.cos(ang), math.sin(ang), -1 if flip else 1
        self.ang = ang
        self.over, self.dx, self.dy, self.grow = None, 0.0, 0.0, 0.0

    def p(self, x, y):
        x *= self.fx
        return (self.ox + self.dx + (x * self.ca - y * self.sa) * self.s,
                self.oy + self.dy + (x * self.sa + y * self.ca) * self.s)

    def col(self, c):
        return self.over if self.over is not None else c

    def poly(self, pts, c):
        self.cv.polygon([self.p(x, y) for x, y in pts], self.col(c))

    def circle(self, x, y, r, c):
        X, Y = self.p(x, y)
        self.cv.circle(X, Y, r * self.s + self.grow, self.col(c))

    def ellipse(self, x, y, rx, ry, c, ang=0.0):
        X, Y = self.p(x, y)
        self.cv.ellipse(X, Y, rx * self.s + self.grow, ry * self.s + self.grow, self.col(c),
                        angle=self.ang + ang * self.fx, seg=20)

    def line(self, x0, y0, x1, y1, w, c):
        A, B = self.p(x0, y0), self.p(x1, y1)
        self.cv.line(A[0], A[1], B[0], B[1], w * self.s + 2 * self.grow, self.col(c))

    def pline(self, pts, w, c):
        for k in range(len(pts) - 1):
            self.line(pts[k][0], pts[k][1], pts[k + 1][0], pts[k + 1][1], w, c)

    def outlined(self, fn, width):
        """Draw fn() as a black silhouette grown by `width` px, then in colour on top."""
        self.over, self.grow = BLACK, width * 0.7
        for dx, dy in ((width * 0.7, 0), (-width * 0.7, 0), (0, width * 0.7), (0, -width * 0.7)):
            self.dx, self.dy = dx, dy
            fn()
        self.over, self.dx, self.dy, self.grow = None, 0.0, 0.0, 0.0
        fn()


# ---------------------------------------------------------------- scenes
class Sleigh(Scene):
    id = "sleigh"
    name = "Santa's sleigh"
    blurb = "Santa and his reindeer fly across a big moon in falling snow (Rudolph leads)."
    seconds = 8.0
    default_colors = ("#ff1020", "#ffc21a")
    palette_role = "sleigh and trim"

    TAN, TAN2, ANTLER = (214, 128, 56), (236, 168, 96), (250, 214, 150)

    def setup(self):
        c = self.ctx
        self.snow = Snowfall(c, 26, salt=11)
        hx, hy = c.fans[c.hero]
        R = c.R
        self.moon = (hx, hy - 0.14 * R, 0.5 * R)
        r = c.rnd(5)
        self.craters = [(r.uniform(-0.5, 0.5), r.uniform(-0.5, 0.5), r.uniform(0.08, 0.17)) for _ in range(6)]
        p = c.pal(self.default_colors)
        self.body, self.trim = p[0], p[1 % len(p)]
        xs = [x for x, _ in c.fans]
        ys = [y for _, y in c.fans]
        self.s = 0.14 * R
        self.x0 = min(xs) - R - 8.0 * self.s
        self.x1 = max(xs) + R + 1.6 * self.s
        # One pass along each row of fans, bottom row first: a straight diagonal on a 2x2
        # would spend most of the loop in the dark gap between the four circles.
        self.rows = sorted(set(round(y, 3) for y in ys), reverse=True)
        self.gallop = c.cyc(2.0)

    def path(self, t):
        """The sleigh's anchor at loop phase t, and its heading (radians)."""
        R = self.ctx.R
        n = len(self.rows)
        k = min(n - 1, int(t * n))
        u = t * n - k
        y0 = self.rows[k] + 0.28 * R
        dx = self.x1 - self.x0
        x = self.x0 + dx * u
        y = y0 - 0.36 * R * math.sin(math.pi * u)
        ang = math.atan2(-0.36 * R * math.pi * math.cos(math.pi * u), dx)
        return x, y, ang

    def reindeer(self, X: Xf, ox, ph, rudolph):
        bob = 0.06 * math.sin(ph)
        oy = -0.15 + bob
        for (lx, base) in ((0.32, 0.0), (0.18, 0.5), (-0.3, math.pi), (-0.44, math.pi + 0.5)):
            a = 0.65 * math.sin(ph + base)
            X.line(ox + lx, oy + 0.1, ox + lx + 0.5 * math.sin(a), oy + 0.1 + 0.5 * math.cos(a), 0.1, self.TAN)
        X.ellipse(ox, oy, 0.52, 0.22, self.TAN)
        X.line(ox + 0.38, oy - 0.08, ox + 0.62, oy - 0.42, 0.18, self.TAN)
        X.ellipse(ox + 0.76, oy - 0.48, 0.22, 0.12, self.TAN2, ang=0.35)
        X.line(ox + 0.62, oy - 0.58, ox + 0.5, oy - 0.98, 0.07, self.ANTLER)
        X.line(ox + 0.56, oy - 0.8, ox + 0.72, oy - 0.98, 0.06, self.ANTLER)
        X.line(ox + 0.52, oy - 0.9, ox + 0.38, oy - 1.05, 0.06, self.ANTLER)
        X.line(ox - 0.48, oy - 0.08, ox - 0.62, oy - 0.25, 0.07, self.TAN2)        # tail
        if rudolph:
            X.circle(ox + 0.97, oy - 0.42, 0.1, (255, 30, 30))
        else:
            X.circle(ox + 0.95, oy - 0.43, 0.06, (120, 60, 30))

    def figure(self, X: Xf, ph):
        body, trim = self.body, self.trim
        X.line(1.0, -0.25, 1.6 + 3 * 1.35 + 0.3, -0.2, 0.06, trim)                  # reins
        for i in range(4):
            self.reindeer(X, 1.6 + i * 1.35, ph + i * 0.9, i == 3)
        X.circle(-0.85, -0.72, 0.3, (40, 200, 70))                                # toy sack
        X.circle(-0.35, -0.75, 0.32, body)                                        # Santa
        X.circle(-0.3, -1.14, 0.17, (255, 205, 160))
        X.ellipse(-0.22, -1.0, 0.17, 0.14, WHITE)
        X.poly([(-0.5, -1.22), (-0.1, -1.26), (-0.55, -1.58)], body)
        X.circle(-0.58, -1.58, 0.08, WHITE)
        X.line(-0.48, -1.24, -0.1, -1.27, 0.07, WHITE)
        X.line(-0.2, -0.8, 0.6, -0.5, 0.09, body)                                  # arm to the reins
        X.poly([(-1.0, -0.2), (-1.08, -0.8), (-0.78, -0.86), (-0.6, -0.45), (0.45, -0.45),
                (0.74, -0.8), (0.98, -0.74), (0.85, -0.32), (0.55, 0.02), (-0.9, 0.02)], body)
        X.line(-0.95, -0.17, 0.55, -0.17, 0.11, trim)
        X.pline([(-1.15, 0.38), (0.7, 0.38), (0.95, 0.22), (1.0, 0.0)], 0.1, trim)
        X.line(-0.6, 0.0, -0.6, 0.38, 0.08, trim)
        X.line(0.3, 0.0, 0.3, 0.38, 0.08, trim)

    def draw(self, cv: Canvas, t: float):
        c, R = self.ctx, self.ctx.R
        mx, my, mr = self.moon
        cv.glow(mx, my, mr * 1.4, (150, 135, 70), steps=9)
        cv.circle(mx, my, mr, (255, 240, 170))
        for (dx, dy, rr) in self.craters:
            cv.circle(mx + dx * mr, my + dy * mr, rr * mr, (236, 214, 130))
        twinkle_stars(cv, c, t, 8, avoid=(mx, my, mr * 1.3))
        self.snow.draw(cv, t)
        x, y, ang = self.path(t)
        ph = TAU * self.gallop * t
        X = Xf(cv, x, y, self.s, ang)
        X.outlined(lambda: self.figure(X, ph), max(2.0, 0.012 * R))
        nx, ny = X.p(1.6 + 3 * 1.35 + 0.97, -0.15 + 0.06 * math.sin(ph + 2.7) - 0.42)
        cv.glow(nx, ny, 0.09 * R, (255, 40, 40), a=0.8 + 0.2 * math.sin(TAU * c.cyc(3) * t), steps=4)


class Pumpkin(Scene):
    id = "pumpkin"
    name = "Jack-o'-lantern"
    blurb = "A big carved pumpkin with a flickering candle face, ghosts circling round it."
    seconds = 6.0
    default_colors = ("#f4f8ff", "#a030ff")
    palette_role = "ghosts and bats"

    RIBS = ((-0.6, 0.44, 0.7, (255, 96, 0)), (0.6, 0.44, 0.7, (255, 96, 0)),
            (-0.28, 0.42, 0.76, (255, 116, 10)), (0.28, 0.42, 0.76, (255, 116, 10)),
            (0.0, 0.34, 0.79, (255, 132, 24)))

    def setup(self):
        c = self.ctx
        p = c.pal(self.default_colors)
        self.ghost_c = p[0]
        self.bat_c = p[1 % len(p)] if len(p) > 1 else (160, 48, 255)
        self.orbit = c.cyc(0.17)
        self.f1, self.f2, self.f3 = c.cyc(5.0), c.cyc(8.7), c.cyc(14.3)

    def flicker(self, t):
        return 0.78 + 0.13 * math.sin(TAU * self.f1 * t) + 0.06 * math.sin(TAU * self.f2 * t + 1.3) + 0.03 * math.sin(TAU * self.f3 * t + 2.1)

    def lantern(self, cv, cx, cy, s, f):
        for (dx, rx, ry, col) in self.RIBS:
            cv.ellipse(cx + dx * s, cy, (rx + 0.05) * s, (ry + 0.03) * s, (150, 40, 0), seg=40)
            cv.ellipse(cx + dx * s, cy, rx * s, ry * s, col, seg=40)
        cv.polygon([(cx - 0.1 * s, cy - 0.68 * s), (cx + 0.12 * s, cy - 0.68 * s),
                    (cx + 0.24 * s, cy - 1.02 * s), (cx + 0.06 * s, cy - 1.05 * s)], (40, 190, 40))
        cv.line(cx + 0.15 * s, cy - 0.9 * s, cx + 0.45 * s, cy - 0.95 * s, 0.06 * s, (60, 220, 60))
        eye_l = [(-0.58, -0.1), (-0.16, -0.1), (-0.37, -0.46)]
        eye_r = [(x * -1, y) for x, y in eye_l]
        nose = [(-0.11, 0.07), (0.11, 0.07), (0.0, -0.12)]
        mouth = [(-0.64, 0.17), (-0.36, 0.25), (-0.27, 0.15), (-0.16, 0.27), (0.16, 0.27), (0.27, 0.15),
                 (0.36, 0.25), (0.64, 0.17), (0.45, 0.47), (0.24, 0.56), (0.16, 0.44), (0.04, 0.58),
                 (-0.1, 0.58), (-0.2, 0.45), (-0.28, 0.56), (-0.48, 0.47)]
        g = max(0.0, min(1.0, (f - 0.55) / 0.45))      # candle: deep orange to bright yellow
        deep, core = mix((230, 80, 0), (255, 170, 0), g), mix((255, 150, 0), (255, 250, 120), g)
        for shape in (eye_l, eye_r, nose, mouth):
            mxc = sum(x for x, _ in shape) / len(shape)
            myc = sum(y for _, y in shape) / len(shape)
            for k, col in ((1.16, (90, 20, 0)), (1.0, deep), (0.66, core)):
                cv.polygon([(cx + (mxc + (x - mxc) * k) * s, cy + (myc + (y - myc) * k) * s) for x, y in shape], col)

    def draw(self, cv: Canvas, t: float):
        c, R = self.ctx, self.ctx.R
        f = self.flicker(t)
        for fi, (cx, cy) in enumerate(c.fans):
            if fi == c.hero:
                s = 0.58 * R
                gh = []
                for i in range(3):
                    a = TAU * ((self.orbit * t + i / 3) % 1.0)
                    gh.append((math.sin(a), cx + 0.76 * R * math.cos(a), cy + 0.1 * R + 0.56 * R * math.sin(a), i))
                gh.sort()
                for (z, gx, gy, i) in gh:
                    if z < 0:
                        ghost(cv, gx, gy, 0.12 * R * (0.9 + 0.2 * z), TAU * (2 * t + i / 3), scale(self.ghost_c, 0.75 + 0.15 * z))
                self.lantern(cv, cx, cy + 0.14 * R, s, f)
                for (z, gx, gy, i) in gh:
                    if z >= 0:
                        ghost(cv, gx, gy, 0.12 * R * (0.9 + 0.2 * z), TAU * (2 * t + i / 3), self.ghost_c)
            else:
                k = fi - c.hero
                bob = math.sin(TAU * (t + fi * 0.3))
                ghost(cv, cx + 0.18 * R * math.sin(TAU * (t + fi * 0.2)), cy - 0.05 * R + 0.12 * R * bob,
                      0.34 * R, TAU * (2 * t + fi * 0.25), self.ghost_c, mouth=0.6 + 0.4 * abs(bob))
                for b in range(2):
                    a = TAU * (self.orbit * t * (1 if k > 0 else -1) + b / 2 + fi * 0.1)
                    bat(cv, cx + 0.62 * R * math.cos(a), cy + 0.5 * R * math.sin(a), 0.16 * R,
                        TAU * (c.cyc(3.0) * t + b * 0.5), self.bat_c)


class Ghosts(Scene):
    id = "ghosts"
    name = "Ghost swirl"
    blurb = "A ring of ghosts swirling round a purple vortex."
    seconds = 6.0
    default_colors = ("#f4f8ff", "#80ffa8", "#d0a8ff", "#80ecff")
    palette_role = "ghosts"
    splice = False

    def setup(self):
        c = self.ctx
        self.cols = c.pal(self.default_colors)
        self.turn = c.cyc(0.17)
        self.breath = c.cyc(0.34)

    def draw(self, cv: Canvas, t: float):
        c, R = self.ctx, self.ctx.R
        for fi, (cx, cy) in enumerate(c.fans):
            d = -1 if fi % 2 else 1
            for arm in range(3):                       # the vortex
                pts = []
                for k in range(18):
                    u = k / 17
                    a = d * TAU * (self.turn * 2 * t) + arm * TAU / 3 + u * 3.2 * d
                    rr = (0.06 + 0.4 * u) * R
                    pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
                cv.polyline(pts, 0.045 * R, (110, 30, 220))
            cv.glow(cx, cy, 0.25 * R, (150, 60, 255), steps=4)
            gs = []
            for i in range(5):
                a = d * TAU * (self.turn * t + i / 5)
                rr = R * (0.6 + 0.09 * math.sin(TAU * (self.breath * t + i / 5)))
                z = math.sin(TAU * (self.breath * t + i / 5 + 0.25))
                gs.append((z, cx + rr * math.cos(a), cy + rr * math.sin(a) * 0.92, i))
            gs.sort()
            for (z, x, y, i) in gs:
                col = self.cols[(i + fi) % len(self.cols)]
                ghost(cv, x, y - 0.08 * R, 0.155 * R * (1 + 0.18 * z), TAU * (2 * t + i / 5), scale(col, 0.85 + 0.15 * z),
                      mouth=0.5 + 0.5 * abs(z))


class Fireworks(Scene):
    id = "fireworks"
    name = "Fireworks"
    blurb = "Rockets climb and burst into big round shells, staggered across the fans."
    seconds = 8.0
    default_colors = ("#ff2040", "#30ff60", "#3090ff", "#ffd020", "#ff40ff", "#30f0ff")
    palette_role = "shell colours"

    RISE, BURST, FALL = 0.55, 0.45, 1.15

    def setup(self):
        c = self.ctx
        r = c.rnd(21)
        cols = c.pal(self.default_colors)
        per_fan = max(3, int(round(c.secs / 1.35)))
        self.shells = []
        n = 0
        for fi, (cx, cy) in enumerate(c.fans):
            for k in range(per_fan):
                a, d = r.random() * TAU, 0.22 * c.R * r.random() ** 0.5
                bx, by = cx + d * math.cos(a), cy - 0.1 * c.R + d * math.sin(a) * 0.7
                start = (k / per_fan + fi * 0.37 / max(1, len(c.fans)) + r.uniform(-0.04, 0.04)) % 1.0
                col = cols[n % len(cols)]
                col2 = cols[(n + 2) % len(cols)] if r.random() < 0.4 else None
                parts = []
                m = r.choice((18, 22, 26))
                for j in range(m):
                    ang = TAU * j / m + r.uniform(-0.06, 0.06)
                    parts.append((ang, r.uniform(0.85, 1.05), col2 if (col2 and j % 2) else col))
                    if j % 2 == 0:                    # an inner ring fills the shell out
                        parts.append((ang + math.pi / m, r.uniform(0.45, 0.6), col))
                self.shells.append((bx, by, cx, cy, start, r.uniform(0.5, 0.62) * c.R, parts, r.random()))
                n += 1

    def draw(self, cv: Canvas, t: float):
        c, R = self.ctx, self.ctx.R
        total = self.RISE + self.BURST + self.FALL
        for (bx, by, cx, cy, start, size, parts, seed) in self.shells:
            tau = ((t - start) % 1.0) * c.secs
            if tau >= total:
                continue
            if tau < self.RISE:                      # the rocket climbing
                u = tau / self.RISE
                e = 1 - (1 - u) ** 2
                y0 = cy + 0.85 * R
                y = y0 + (by - y0) * e
                x = bx + 0.03 * R * math.sin(seed * 9 + u * 3)
                for k in range(4):
                    yy = y + (k * 0.06 * R) * (1.2 - e)
                    cv.circle(x, yy, 0.03 * R * (1 - k * 0.2), scale((255, 200, 90), 1 - k * 0.22))
                continue
            tb = tau - self.RISE
            life = tb / (self.BURST + self.FALL)
            if tb < 0.18:
                cv.glow(bx, by, (0.25 + tb * 1.5) * R, (255, 255, 220), a=1 - tb / 0.18, steps=4)
            fade = 1.0 if life < 0.55 else max(0.0, (1 - life) / 0.45)

            def pos(tt, ang, sp):
                spread = 1 - math.exp(-tt * 3.6)
                rr = size * sp * spread
                return bx + rr * math.cos(ang), by + rr * math.sin(ang) + 0.42 * R * tt * tt

            for (ang, sp, col) in parts:
                hot = mix(WHITE, col, min(1.0, tb / 0.35))
                trail = [pos(max(0.0, tb - k * 0.08), ang, sp) for k in range(5)]
                for k in range(4):
                    (x0, y0), (x1, y1) = trail[k], trail[k + 1]
                    cv.line(x0, y0, x1, y1, 0.05 * R * (1 - 0.2 * k), scale(hot, fade * (1 - 0.22 * k)), "max")
                if life > 0.6:                         # crackle at the end
                    tw = 0.5 + 0.5 * math.sin(tb * 40 + ang * 7)
                    cv.circle(trail[0][0], trail[0][1], 0.045 * R, scale(WHITE, fade * tw), "max")
                else:
                    cv.circle(trail[0][0], trail[0][1], 0.045 * R, scale(hot, fade), "max")


class SnowGlobe(Scene):
    id = "snowglobe"
    name = "Snow globe"
    blurb = "A glass globe with a twinkling Christmas tree and swirling snow inside."
    seconds = 8.0
    default_colors = ("#ff2030", "#ffd020", "#2a8cff", "#ff40c0")
    palette_role = "ornaments"
    splice = False

    def setup(self):
        c = self.ctx
        self.cols = c.pal(self.default_colors)
        r = c.rnd(31)
        self.flakes = [(r.random(), r.uniform(0.15, 0.92), r.choice((1, 1, 2)) * r.choice((-1, 1)), r.random(),
                        r.uniform(0.022, 0.04), r.choice((1, 2))) for _ in range(46)]
        self.orns = [(-0.2, -0.05), (0.18, -0.12), (-0.05, 0.18), (0.27, 0.22), (-0.3, 0.27), (0.08, -0.3), (-0.1, -0.42)]
        self.tw = [r.random() for _ in self.orns]

    def snow(self, cv, cx, gy, gr, t, front):
        for i, (a0, rr, k, ph, sz, j) in enumerate(self.flakes):
            if (i % 2 == 0) != front:
                continue
            a = TAU * (a0 + k * t)
            rad = gr * rr * (0.85 + 0.15 * math.sin(TAU * (j * t + ph)))
            x, y = cx + rad * math.cos(a), gy + rad * math.sin(a) * 0.8 + 0.08 * gr
            if math.hypot(x - cx, y - gy) < gr * 0.9:
                cv.circle(x, y, sz * gr, SNOW)

    def globe(self, cv, cx, cy, t, k):
        R = self.ctx.R
        gy, gr = cy - 0.1 * R, 0.7 * R
        self.snow(cv, cx, gy, gr, t, False)
        # snowy ground: the bottom of the globe
        pts = [(cx + gr * 0.97 * math.cos(a), gy + gr * 0.97 * math.sin(a))
               for a in [math.pi * (0.1 + 0.8 * i / 20) for i in range(21)]]
        pts += [(cx - gr * 0.75, gy + 0.42 * gr), (cx, gy + 0.36 * gr), (cx + gr * 0.75, gy + 0.42 * gr)]
        pts = [pts[-1]] + pts[:-1]
        cv.polygon(pts, (225, 238, 255))
        s = gr * 1.12
        tx, ty = cx, gy + 0.4 * gr
        cv.rect(tx - 0.07 * s, ty - 0.14 * s, tx + 0.07 * s, ty + 0.02 * s, (150, 80, 30))
        for (w, top, bot, col) in ((0.42, -0.42, -0.1, (20, 170, 50)), (0.34, -0.66, -0.34, (30, 195, 60)),
                                   (0.25, -0.86, -0.58, (40, 215, 70))):
            cv.polygon([(tx - w * s, ty + bot * s), (tx + w * s, ty + bot * s), (tx, ty + top * s)], col)
        for i, (ox, oy) in enumerate(self.orns):
            b = 0.55 + 0.45 * math.sin(TAU * (2 * t + self.tw[i] + k * 0.13))
            col = self.cols[(i + k) % len(self.cols)]
            cv.circle(tx + ox * s, ty - 0.35 * s + oy * s, 0.055 * s, scale(col, 0.55 + 0.45 * b))
        sx, sy = tx, ty - 0.9 * s
        cv.glow(sx, sy, 0.2 * s, (255, 220, 80), steps=3)
        cv.star(sx, sy, 0.13 * s, 0.055 * s, (255, 214, 40), angle=-math.pi / 2 + TAU / 5 * self.ctx.cyc(0.125) * t)
        self.snow(cv, cx, gy, gr, t, True)
        cv.ring(cx, gy, gr * 0.96, gr * 1.02, (150, 220, 255))
        arc = [(cx + gr * 0.84 * math.cos(a), gy + gr * 0.84 * math.sin(a))
               for a in [math.pi * (1.12 + 0.3 * i / 8) for i in range(9)]]
        cv.polyline(arc, 0.05 * R, (235, 250, 255))
        base = [(cx - 0.46 * R, gy + gr * 0.9), (cx + 0.46 * R, gy + gr * 0.9), (cx + 0.56 * R, cy + 0.8 * R),
                (cx - 0.56 * R, cy + 0.8 * R)]
        cv.polygon(base, (220, 20, 40))
        cv.rect(cx - 0.51 * R, cy + 0.68 * R, cx + 0.51 * R, cy + 0.74 * R, (255, 200, 40))

    def draw(self, cv: Canvas, t: float):
        for k, (cx, cy) in enumerate(self.ctx.fans):
            self.globe(cv, cx, cy, t, k)


class Wreath(Scene):
    id = "wreath"
    name = "Star and wreath"
    blurb = "A turning Christmas wreath with a red bow and a spinning gold star in the middle."
    seconds = 6.0
    default_colors = ("#ff1a2e", "#ffd23a")
    palette_role = "berries and star"
    splice = False

    def setup(self):
        c = self.ctx
        p = c.pal(self.default_colors)
        self.berry = p[0]
        self.gold = p[1 % len(p)] if len(p) > 1 else (255, 210, 58)
        self.n = 14
        self.turn = c.cyc(0.15)
        self.spin = c.cyc(0.3)

    def draw(self, cv: Canvas, t: float):
        c, R = self.ctx, self.ctx.R
        for fi, (cx, cy) in enumerate(c.fans):
            d = -1 if fi % 2 else 1
            rot = d * TAU * 2 * self.turn * t / self.n     # whole berry-pairs a loop: seamless
            wr = 0.62 * R
            cv.ring(cx, cy, wr - 0.13 * R, wr + 0.13 * R, (8, 110, 34))
            for k in range(self.n):
                a = rot + TAU * k / self.n
                for j, (off, col) in enumerate(((-0.07, (30, 200, 70)), (0.07, (20, 170, 55)), (0.0, (50, 230, 90)))):
                    rr = wr + off * R
                    x, y = cx + rr * math.cos(a + j * 0.12), cy + rr * math.sin(a + j * 0.12)
                    cv.ellipse(x, y, 0.13 * R, 0.05 * R, col, angle=a + math.pi / 2 + 0.6 * (j - 1), seg=14)
                if k % 2 == 0:
                    for b in range(3):
                        ba = a + 0.1 + 0.07 * b
                        br = wr + (0.03 * (b - 1)) * R
                        cv.circle(cx + br * math.cos(ba), cy + br * math.sin(ba), 0.038 * R, self.berry)
            # bow at the bottom (stays put)
            bx, by = cx, cy + wr
            red = (255, 30, 50)
            cv.polygon([(bx, by), (bx - 0.24 * R, by - 0.13 * R), (bx - 0.24 * R, by + 0.13 * R)], red)
            cv.polygon([(bx, by), (bx + 0.24 * R, by - 0.13 * R), (bx + 0.24 * R, by + 0.13 * R)], red)
            cv.polygon([(bx - 0.03 * R, by), (bx - 0.14 * R, by + 0.28 * R), (bx - 0.05 * R, by + 0.26 * R)], red)
            cv.polygon([(bx + 0.03 * R, by), (bx + 0.14 * R, by + 0.28 * R), (bx + 0.05 * R, by + 0.26 * R)], red)
            cv.circle(bx, by, 0.05 * R, (255, 90, 100))
            # the star: two-tone facets so the spin reads
            sa = -math.pi / 2 + d * TAU / 5 * self.spin * t
            ro, ri = 0.34 * R, 0.14 * R
            pulse = 0.85 + 0.15 * math.sin(TAU * c.cyc(1.0) * t)
            cv.glow(cx, cy, 0.5 * R, scale(self.gold, 0.5 * pulse), steps=5)
            light, dark = mix(self.gold, (255, 245, 150), 0.35), mix(self.gold, (255, 140, 0), 0.45)
            for k in range(5):
                a = sa + TAU * k / 5
                tip = (cx + ro * math.cos(a), cy + ro * math.sin(a))
                l = (cx + ri * math.cos(a - math.pi / 5), cy + ri * math.sin(a - math.pi / 5))
                r_ = (cx + ri * math.cos(a + math.pi / 5), cy + ri * math.sin(a + math.pi / 5))
                cv.polygon([(cx, cy), l, tip], light)
                cv.polygon([(cx, cy), tip, r_], dark)
            for k in range(4):
                a = TAU * (k / 4 + 0.125)
                b = 0.5 + 0.5 * math.sin(TAU * (2 * t + k / 4))
                cv.sparkle(cx + 0.42 * R * math.cos(a), cy + 0.42 * R * math.sin(a), 0.1 * R * b + 0.02 * R, WHITE, a=b)


class NewYear(Scene):
    id = "newyear"
    name = "Happy New Year"
    blurb = "HAPPY NEW YEAR and the year in big letters over a sparkle burst."
    seconds = 6.0
    default_colors = ("#ffd23a", "#ff3df0", "#30e0ff", "#ffffff")
    palette_role = "letters and sparkles"

    def setup(self):
        c = self.ctx
        self.cols = c.pal(self.default_colors)
        yr = str(c.year)
        n = len(c.fans)
        if n == 1:
            self.words = [[("HAPPY", 0.17, -0.42), ("NEW YEAR", 0.15, -0.12), (yr, 0.3, 0.3)]]
        elif n == 2:
            self.words = [[("HAPPY", 0.2, -0.3), ("NEW YEAR", 0.15, 0.08)], [(yr, 0.38, 0.0)]]
        elif n == 3:
            self.words = [[("HAPPY", 0.26, 0.0)], [("NEW", 0.3, -0.22), ("YEAR", 0.3, 0.2)], [(yr, 0.38, 0.0)]]
        else:
            self.words = [[("HAPPY", 0.26, 0.0)], [("NEW", 0.36, 0.0)], [("YEAR", 0.32, 0.0)], [(yr, 0.38, 0.0)]]
        r = c.rnd(41)
        self.sparks = [(r.random() * TAU, r.uniform(0.25, 0.92), r.choice((1, 2, 3)), r.random(), k)
                       for k in range(22)]
        self.bursts = c.cyc(1 / 3)

    def draw(self, cv: Canvas, t: float):
        c, R = self.ctx, self.ctx.R
        for fi, (cx, cy) in enumerate(c.fans):
            u = (t * self.bursts + fi * 0.17) % 1.0
            for k in range(16):                         # the burst: rays racing outward
                a = TAU * k / 16 + fi * 0.2
                r0, r1 = R * (0.1 + 0.85 * u), R * (0.3 + 0.75 * u)
                col = self.cols[(k + fi) % len(self.cols)]
                fade = (1 - u) ** 1.2
                cv.line(cx + r0 * math.cos(a), cy + r0 * math.sin(a), cx + min(r1, 0.95 * R) * math.cos(a),
                        cy + min(r1, 0.95 * R) * math.sin(a), 0.05 * R, scale(col, fade * 0.9), "max")
            for (a, d, k, ph, i) in self.sparks:
                b = 0.5 + 0.5 * math.sin(TAU * (k * t + ph))
                if b > 0.3:
                    cv.sparkle(cx + d * R * math.cos(a + fi), cy + d * R * math.sin(a + fi), 0.09 * R * b,
                               self.cols[i % len(self.cols)], a=b)
            words = self.words[fi % len(self.words)]
            for wi, (txt, hgt, dy) in enumerate(words):
                pulse = 1 + 0.04 * math.sin(TAU * (c.cyc(0.5) * t + wi * 0.2))
                hh = hgt * R * pulse
                from .. import font
                width = font.layout(txt).width * hh / font.ROWS
                if width > 1.7 * R:                      # keep inside the circle
                    hh *= 1.7 * R / width
                    width = 1.7 * R
                col = self.cols[(wi + fi) % len(self.cols)]
                if col == (255, 255, 255) and len(self.cols) > 1:
                    col = self.cols[0]
                o = max(2.0, 0.025 * R)
                ty = cy + dy * R                         # clear the burst out of the letters' box
                cv.rect(cx - width / 2 - o, ty - hh / 2 - o, cx + width / 2 + o, ty + hh / 2 + o, BLACK)
                for ox, oy in ((o, 0), (-o, 0), (0, o), (0, -o), (o, o), (-o, -o), (o, -o), (-o, o)):
                    cv.text(txt, cx + ox, cy + dy * R + oy, hh, BLACK)
                cv.text(txt, cx, cy + dy * R, hh, col)


class Football(Scene):
    id = "football"
    name = "Game day field goal"
    blurb = "A football tumbles through the goal posts, then team-colour confetti. Uses your team colours."
    seconds = 6.0
    default_colors = ("#ffd23a", "#22d34a")        # goal-post gold and field green until a team is set
    palette_role = "team colours"

    BROWN = (196, 98, 36)

    def setup(self):
        c = self.ctx
        team = c.team or c.colors or [vivid(hex_rgb(x), 200) for x in self.default_colors]
        self.c1 = team[0]
        self.c2 = team[1] if len(team) > 1 else WHITE
        n = len(c.fans)
        self.post = n - 1 if c.layout.rows == 1 else 1            # top-right on a 2x2
        # On a 2x2 the kick comes from the fan straight below the posts: a diagonal from
        # bottom-left would cross the dark middle between the four fans.
        self.kick = 0 if c.layout.rows == 1 else 3
        self.spin = c.cyc(1.2)

    def posts(self, cv, cx, cy, s, glow):
        R = self.ctx.R
        w = 0.075 * R * s
        col = self.c1
        if glow > 0:
            col = mix(self.c1, WHITE, 0.35 * glow)
        bar = cy + 0.08 * R
        cv.line(cx, bar, cx, cy + 0.82 * R, w * 1.15, col)
        cv.line(cx - 0.46 * R, bar, cx + 0.46 * R, bar, w, col)
        cv.line(cx - 0.46 * R, bar, cx - 0.46 * R, cy - 0.72 * R, w, col)
        cv.line(cx + 0.46 * R, bar, cx + 0.46 * R, cy - 0.72 * R, w, col)
        for x in (-0.46, 0.46):
            cv.line(cx + x * R, cy - 0.72 * R, cx + x * R, cy - 0.62 * R, w * 1.05, self.c2)

    def ball(self, cv, x, y, s, ang):
        R = self.ctx.R
        rx, ry = 0.19 * R * s, 0.115 * R * s
        cv.ellipse(x, y, rx + 0.02 * R, ry + 0.02 * R, BLACK, angle=ang)
        cv.ellipse(x, y, rx, ry, self.BROWN, angle=ang)
        ca, sa = math.cos(ang), math.sin(ang)

        def p(u, v):
            return (x + u * ca - v * sa, y + u * sa + v * ca)

        for u in (-0.72, 0.72):                      # the white end stripes
            a, b = p(u * rx, -ry * 0.6), p(u * rx, ry * 0.6)
            cv.line(a[0], a[1], b[0], b[1], 0.03 * R * s, WHITE)
        a, b = p(-0.35 * rx, -0.05 * ry), p(0.35 * rx, -0.05 * ry)
        cv.line(a[0], a[1], b[0], b[1], 0.03 * R * s, WHITE)
        for u in (-0.25, 0.0, 0.25):
            a, b = p(u * rx, -0.3 * ry), p(u * rx, 0.2 * ry)
            cv.line(a[0], a[1], b[0], b[1], 0.025 * R * s, WHITE)

    def ball_at(self, u):
        """The ball's centre and size u of the way through its flight (0..1)."""
        c, R = self.ctx, self.ctx.R
        px, py = c.fans[self.post]
        kx, ky = c.fans[self.kick]
        same = self.kick == self.post
        sx, sy = kx - (0.22 * R if same else 0.3 * R), ky + 0.58 * R
        ex, ey = px, py - 0.25 * R
        lift = 0.4 if same else 1.0 if ky == py else 0.25      # a gentler arc on a straight-up kick
        x = sx + (ex - sx) * u
        y = sy + (ey - sy) * u - 0.55 * R * math.sin(math.pi * u) * lift
        return x, y, 1.25 - 0.7 * u

    def draw(self, cv: Canvas, t: float):
        c, R = self.ctx, self.ctx.R
        t %= 1.0                                         # a timeline: t = 1 is t = 0
        px, py = c.fans[self.post]
        FLY = 0.42
        good = (t - FLY) % 1.0
        glow = max(0.0, 1 - good / 0.25) if good < 0.25 else 0.0
        for fi, (cx, cy) in enumerate(c.fans):          # stadium lights chasing round each fan
            for k in range(12):
                a = TAU * k / 12
                on = (k + int(t * c.cyc(2.0) * 12)) % 3 == 0
                col = self.c1 if k % 2 else self.c2
                cv.circle(cx + 0.9 * R * math.cos(a), cy + 0.9 * R * math.sin(a), 0.05 * R, scale(col, 1.0 if on else 0.35))
        self.posts(cv, px, py, 1.0, glow)
        if t < FLY:
            x, y, s = self.ball_at(t / FLY)
            self.ball(cv, x, y, s, TAU * self.spin * t)
        elif good < 0.62:
            hh = 0.3 * R
            a = min(1.0, good / 0.06)
            if a > 0.5:
                o = max(2.0, 0.03 * R)
                for ox, oy in ((o, 0), (-o, 0), (0, o), (0, -o)):
                    cv.text("GOOD!", px + ox, py + 0.45 * R + oy, hh * 0.8, BLACK)
                cv.text("GOOD!", px, py + 0.45 * R, hh * 0.8, self.c2 if max(self.c2) > 120 else WHITE)
        confetti(cv, c, t, [self.c1, self.c2, WHITE], FLY, 0.55)


SCENES: dict[str, type] = {cls.id: cls for cls in (Sleigh, Pumpkin, Ghosts, Fireworks, SnowGlobe, Wreath, NewYear, Football)}
