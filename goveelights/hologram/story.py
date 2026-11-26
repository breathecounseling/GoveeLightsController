"""Story scenes: a little film that plays one way through each loop.

"Santa lands" (santa-landing), about 24 s:

    0.00  night sky, moon, snow, a snowy roof with twinkling string lights
    0.06  the sleigh and reindeer swoop down in an arc from the top left...
    0.27  ...and touch down on the roof: a bounce, a squash, a puff of snow
    0.35  Santa crouches, hops out (a jump arc) and lands with a squash
    0.42  he turns to face the street and WAVES; "HO HO HO!", then "MERRY CHRISTMAS!"
    0.67  he turns back, hops in, the reindeer run along the roof...
    0.82  ...and the sleigh takes off up and away to the right, past the moon,
          leaving a trail of sparkles that has faded out before the loop starts again

The beats are fractions of the loop, so a longer or shorter loop plays the same story
slower or faster, and t = 1 is the same picture as t = 0 (the sky is empty then: snow
and twinkles do whole cycles, nothing else is on screen) - a seamless loop.

Where it happens: on fans, the sleigh lands in the middle fan of the bottom row (the
"stage" fan) and the moon sits in the top right fan; on a frame (projector, screen,
LED matrix) it lands in the middle and uses the whole width. Everything is sized from
the stage fan's radius R (one unit u = R / 10), so it reads the same at 240 px or 4K.

Sprites are drawn from simple shapes through a small transform (position, unit, squash
x / y, rotation, mirror) so the cartoon tricks are cheap: squash and stretch around the
feet, anticipation crouches, a turn that squeezes the side view to nothing and opens up
the front view. A black outline (the sprite drawn in black, shifted four ways, under the
colour) keeps red Santa readable against the red sleigh - and black is see-through on a
fan, so the outline costs nothing there.
"""
from __future__ import annotations

import math

from .canvas import Canvas, scale
from .scenes import BLACK, SNOW, TAU, WHITE, Scene, Snowfall, twinkle_stars

# ---------------------------------------------------------------- the beats (loop fractions)
T_ENTER, T_TOUCH, T_SETTLE = 0.06, 0.27, 0.33
T_CROUCH, T_JUMP, T_LAND, T_STAND = 0.345, 0.365, 0.405, 0.42
T_TURN, T_WAVE0, T_WAVE1, T_TURNB = 0.42, 0.445, 0.665, 0.69
T_HO0, T_HO1, T_MC0, T_MC1 = 0.455, 0.56, 0.56, 0.665
T_CROUCH2, T_JUMP2, T_SEAT, T_SEATED = 0.69, 0.71, 0.745, 0.765
T_RUN, T_LIFT, T_GONE = 0.765, 0.82, 0.93
TRAIL_LIFE = 0.05
WAVES = 5                         # times the hand goes back and forth

# ---------------------------------------------------------------- colours
COAT = (232, 24, 36)
COAT_DK = (170, 12, 24)
FUR = WHITE
SKIN = (255, 202, 162)
CHEEK = (255, 128, 128)
NOSE = (255, 150, 130)
BOOT = (96, 62, 44)
BELT = (64, 30, 22)
GOLD = (255, 196, 30)
TAN, TAN2, TAN_DK = (204, 122, 54), (238, 170, 100), (150, 84, 36)
ANTLER = (250, 220, 160)
RUDOLPH = (255, 26, 26)
SACK = (36, 190, 72)
ICE = (170, 220, 255)
BRICK, MORTAR = (190, 52, 34), (90, 26, 18)
BULBS = ("#ff2a2a", "#22e06a", "#ffc21a", "#3aa0ff", "#ff7ad9")


def clamp01(v: float) -> float:
    return 0.0 if v <= 0 else 1.0 if v >= 1 else v


def seg(t: float, a: float, b: float) -> float:
    """Progress 0..1 of t through the beat [a, b)."""
    return clamp01((t - a) / (b - a))


def smooth(v: float) -> float:
    v = clamp01(v)
    return v * v * (3 - 2 * v)


def ease_out(v: float, p: float = 2.0) -> float:
    return 1 - (1 - clamp01(v)) ** p


def ease_in(v: float, p: float = 2.0) -> float:
    return clamp01(v) ** p


def back_out(v: float, k: float = 1.7) -> float:
    """Overshoots a little, then settles (a pop)."""
    v = clamp01(v) - 1
    return 1 + (k + 1) * v ** 3 + k * v ** 2


def bez(p0, p1, p2, p3, b):
    m = 1 - b
    x = m ** 3 * p0[0] + 3 * m * m * b * p1[0] + 3 * m * b * b * p2[0] + b ** 3 * p3[0]
    y = m ** 3 * p0[1] + 3 * m * m * b * p1[1] + 3 * m * b * b * p2[1] + b ** 3 * p3[1]
    dx = 3 * m * m * (p1[0] - p0[0]) + 6 * m * b * (p2[0] - p1[0]) + 3 * b * b * (p3[0] - p2[0])
    dy = 3 * m * m * (p1[1] - p0[1]) + 6 * m * b * (p2[1] - p1[1]) + 3 * b * b * (p3[1] - p2[1])
    return x, y, dx, dy


class Tf:
    """A sprite's drawing frame: local units of u px, origin (ox, oy) (a figure's feet),
    squash sx / sy around the origin, then rotation ang; mirror flips left / right.
    Every shape becomes a polygon, so squash and rotation apply to all of it."""

    def __init__(self, cv: Canvas, ox, oy, u, sx=1.0, sy=1.0, ang=0.0, mirror=False):
        self.cv, self.ox, self.oy, self.u = cv, ox, oy, u
        self.sx, self.sy = sx * (-1 if mirror else 1), sy
        self.ca, self.sa = math.cos(ang), math.sin(ang)
        self.over, self.dx, self.dy = None, 0.0, 0.0

    def p(self, x, y):
        x, y = x * self.sx * self.u, y * self.sy * self.u
        return (self.ox + self.dx + x * self.ca - y * self.sa, self.oy + self.dy + x * self.sa + y * self.ca)

    def _c(self, c):
        return self.over if self.over is not None else c

    def poly(self, pts, c):
        self.cv.polygon([self.p(x, y) for x, y in pts], self._c(c))

    def ellipse(self, x, y, rx, ry, c, ang=0.0, n=18):
        ca, sa = math.cos(ang), math.sin(ang)
        pts = []
        for k in range(n):
            a = TAU * k / n
            ex, ey = rx * math.cos(a), ry * math.sin(a)
            pts.append((x + ex * ca - ey * sa, y + ex * sa + ey * ca))
        self.poly(pts, c)

    def circle(self, x, y, r, c, n=16):
        self.ellipse(x, y, r, r, c, 0.0, n)

    def line(self, x0, y0, x1, y1, w, c):
        """A capsule from (x0, y0) to (x1, y1), w units wide."""
        dx, dy = x1 - x0, y1 - y0
        L = math.hypot(dx, dy)
        r = w / 2
        if L < 1e-6:
            return self.circle(x0, y0, r, c, 10)
        a = math.atan2(dy, dx)
        pts = []
        for k in range(6):
            q = a - math.pi / 2 + math.pi * k / 5
            pts.append((x1 + r * math.cos(q), y1 + r * math.sin(q)))
        for k in range(6):
            q = a + math.pi / 2 + math.pi * k / 5
            pts.append((x0 + r * math.cos(q), y0 + r * math.sin(q)))
        self.poly(pts, c)

    def pline(self, pts, w, c):
        for k in range(len(pts) - 1):
            self.line(pts[k][0], pts[k][1], pts[k + 1][0], pts[k + 1][1], w, c)

    def outlined(self, fn, px):
        """fn() as a black silhouette shifted px four ways, then in colour on top."""
        if px >= 1:
            self.over = BLACK
            for dx, dy in ((px, 0), (-px, 0), (0, px), (0, -px)):
                self.dx, self.dy = dx, dy
                fn()
            self.over, self.dx, self.dy = None, 0.0, 0.0
        fn()


# ---------------------------------------------------------------- sprites
def santa_front(T: Tf, wave: float, wave_ph: float, laugh: float, blink: bool):
    """Santa facing the viewer, feet at (0, 0), 6.4 units tall. wave 0..1 raises his
    right hand (on the viewer's left); wave_ph swings it; laugh opens his mouth."""
    jig = 0.06 * laugh
    # boots and legs
    for sx in (-1, 1):
        T.ellipse(sx * 0.55, -0.28, 0.55, 0.32, BOOT)
        T.poly([(sx * 0.2, -0.45), (sx * 0.9, -0.45), (sx * 0.85, -1.7), (sx * 0.15, -1.7)], COAT_DK)
        T.line(sx * 0.2, -0.55, sx * 0.88, -0.55, 0.3, FUR)
    # coat: shoulders to a flared hem, round belly
    T.poly([(-0.95, -4.15), (0.95, -4.15), (1.5, -1.65), (-1.5, -1.65)], COAT)
    T.ellipse(0, -2.75, 1.42 + jig, 1.25 + jig, COAT)
    T.line(-1.45, -1.72, 1.45, -1.72, 0.42, FUR)                         # fur hem
    T.line(0, -1.8, 0, -4.0, 0.34, FUR)                                  # fur down the front
    T.line(-1.4, -2.55, 1.4, -2.55, 0.42, BELT)
    T.poly([(-0.36, -2.25), (0.36, -2.25), (0.36, -2.85), (-0.36, -2.85)], GOLD)
    T.poly([(-0.18, -2.42), (0.18, -2.42), (0.18, -2.68), (-0.18, -2.68)], BELT)
    # resting arm (viewer's right)
    T.line(1.05, -3.85, 1.62, -2.5, 0.62, COAT)
    T.line(1.55, -2.62, 1.68, -2.36, 0.5, FUR)
    T.circle(1.72, -2.16, 0.3, FUR)
    # waving arm (viewer's left): hangs at wave = 0, up and swinging at wave = 1
    sh = (-1.05, -3.85)
    a1 = math.radians(115 + 125 * wave)                                  # 115 deg = down and out, 240 = up and out
    e = (sh[0] + 1.15 * math.cos(a1), sh[1] + 1.15 * math.sin(a1))
    a2 = a1 + wave * (math.radians(30) + 0.5 * math.sin(wave_ph))
    h = (e[0] + 1.05 * math.cos(a2), e[1] + 1.05 * math.sin(a2))
    T.line(sh[0], sh[1], e[0], e[1], 0.62, COAT)
    T.line(e[0], e[1], h[0], h[1], 0.58, COAT)
    c0 = (e[0] + 0.82 * math.cos(a2), e[1] + 0.82 * math.sin(a2))
    T.line(c0[0], c0[1], h[0], h[1], 0.52, FUR)
    T.circle(h[0] + 0.2 * math.cos(a2), h[1] + 0.2 * math.sin(a2), 0.33, FUR)
    # head
    T.circle(0, -4.8, 0.74, SKIN)
    T.ellipse(0, -4.12, 0.95, 0.78, FUR)                                 # beard
    for sx in (-1, 1):
        T.circle(sx * 0.62, -4.25, 0.32, FUR)
    T.circle(0, -3.55, 0.36, FUR)
    T.circle(-0.42, -4.66, 0.17, CHEEK)
    T.circle(0.42, -4.66, 0.17, CHEEK)
    if blink:
        T.line(-0.36, -4.98, -0.14, -4.98, 0.1, BLACK)
        T.line(0.14, -4.98, 0.36, -4.98, 0.1, BLACK)
    else:
        T.circle(-0.25, -4.98, 0.1, BLACK, 8)
        T.circle(0.25, -4.98, 0.1, BLACK, 8)
    if laugh > 0.05:
        T.ellipse(0, -4.18, 0.24, 0.2 * laugh + 0.05, (130, 16, 30))
    T.ellipse(-0.27, -4.5, 0.32, 0.14, FUR, 0.15)                        # moustache
    T.ellipse(0.27, -4.5, 0.32, 0.14, FUR, -0.15)
    T.circle(0, -4.68, 0.17, NOSE)
    # hat: a cone flopping to the viewer's left, white brim and pom-pom
    T.poly([(-0.78, -5.3), (0.78, -5.3), (0.25, -6.25), (-0.55, -6.55), (-1.1, -6.05), (-0.45, -5.95)], COAT)
    T.line(-0.82, -5.3, 0.82, -5.3, 0.42, FUR)
    T.circle(-1.15, -6.0, 0.3, FUR)


def santa_side(T: Tf, arm: float, stride: float = 0.0):
    """Santa in profile facing right (mirror the Tf to face left), feet at (0, 0).
    arm 0..1 lifts his arm (up for joy in a jump); stride spreads his feet."""
    T.ellipse(-0.2 - 0.25 * stride, -0.28, 0.55, 0.3, scale(BOOT, 0.75))
    T.poly([(-0.55 - 0.2 * stride, -0.45), (0.05 - 0.2 * stride, -0.45), (0.15, -1.7), (-0.5, -1.7)], scale(COAT_DK, 0.8))
    T.ellipse(0.3 + 0.25 * stride, -0.28, 0.6, 0.32, BOOT)
    T.poly([(-0.25 + 0.2 * stride, -0.45), (0.4 + 0.2 * stride, -0.45), (0.45, -1.7), (-0.2, -1.7)], COAT_DK)
    T.line(-0.25 + 0.2 * stride, -0.55, 0.4 + 0.2 * stride, -0.55, 0.3, FUR)
    T.poly([(-0.75, -4.1), (0.6, -4.1), (1.2, -1.65), (-1.05, -1.65)], COAT)
    T.ellipse(0.35, -2.75, 1.25, 1.2, COAT)
    T.line(-1.05, -1.72, 1.35, -1.72, 0.42, FUR)
    T.line(-0.95, -2.55, 1.5, -2.55, 0.42, BELT)
    T.poly([(1.28, -2.25), (1.62, -2.25), (1.62, -2.85), (1.28, -2.85)], GOLD)
    # head in profile
    T.circle(0.1, -4.8, 0.7, SKIN)
    T.ellipse(0.42, -4.15, 0.62, 0.72, FUR, 0.3)
    T.circle(0.75, -4.48, 0.26, FUR)
    T.circle(0.3, -3.55, 0.32, FUR)
    T.circle(0.32, -4.7, 0.15, CHEEK)
    T.circle(0.48, -5.0, 0.1, BLACK, 8)
    T.circle(0.82, -4.72, 0.17, NOSE)
    T.poly([(-0.72, -5.3), (0.85, -5.3), (0.25, -6.2), (-0.6, -6.5), (-1.25, -5.95), (-0.55, -5.9)], COAT)
    T.line(-0.76, -5.3, 0.88, -5.3, 0.42, FUR)
    T.circle(-1.3, -5.9, 0.3, FUR)
    # the near arm, in front of everything: hangs forward at arm = 0, reaches out at 1
    a = math.radians(70 - 85 * arm)
    sh = (0.0, -3.85)
    h = (sh[0] + 1.6 * math.cos(a), sh[1] + 1.6 * math.sin(a))
    T.line(sh[0], sh[1], h[0], h[1], 0.62, COAT)
    T.line(sh[0] + 1.25 * math.cos(a), sh[1] + 1.25 * math.sin(a), h[0], h[1], 0.52, FUR)
    T.circle(h[0] + 0.2 * math.cos(a), h[1] + 0.2 * math.sin(a), 0.32, FUR)


def reindeer(T: Tf, x0: float, ph: float, run: float, nose, far: bool = False, nod: float = 0.0):
    """A reindeer facing right with its feet on y = 0 at x0. ph is the gallop phase,
    run 0..1 how hard it gallops (0 = standing), nose its nose colour, nod lowers the
    head (units). Returns where the nose is (local units)."""
    k = 0.72 if far else 1.0
    tan, tan2, dk, ant = (scale(c, k) for c in (TAN, TAN2, TAN_DK, ANTLER))
    bob = -0.22 * run * abs(math.sin(ph))
    by = -2.05 + bob
    # legs: front pair and back pair swing in opposition (a gallop); knees fold when lifted
    for (lx, off, near) in ((0.75, 0.0, False), (0.55, 0.55, True), (-0.75, math.pi, False), (-0.95, math.pi + 0.55, True)):
        sw = run * 0.75 * math.sin(ph + off)
        lift = run * max(0.0, math.cos(ph + off)) * 0.9
        hx, hpy = x0 + lx, by + 0.3
        kx, ky = hx + 0.85 * math.sin(sw), hpy + 0.85 * math.cos(sw)
        fa = sw - (lift if lx > 0 else -lift)
        fx, fy = kx + 0.85 * math.sin(fa), ky + 0.85 * math.cos(fa)
        col = tan if near else dk
        T.line(hx, hpy, kx, ky, 0.32, col)
        T.line(kx, ky, fx, fy, 0.26, col)
        T.circle(fx, fy, 0.16, scale(BOOT, k))
    T.ellipse(x0, by, 1.2, 0.58, tan)                                   # body
    T.circle(x0 - 1.15, by - 0.25, 0.26, scale(WHITE, k))               # tail
    hy = by + nod
    T.line(x0 + 0.75, by - 0.15, x0 + 1.3, hy - 1.05, 0.62, tan)        # neck
    T.ellipse(x0 + 1.62, hy - 1.2, 0.55, 0.32, tan2, 0.35)              # head
    T.ellipse(x0 + 1.15, hy - 1.5, 0.28, 0.13, tan, -0.6)               # ear
    T.circle(x0 + 1.6, hy - 1.35, 0.09, BLACK, 8)
    T.line(x0 + 1.3, hy - 1.55, x0 + 1.0, hy - 2.55, 0.16, ant)         # antlers
    T.line(x0 + 1.12, hy - 2.05, x0 + 1.45, hy - 2.5, 0.13, ant)
    T.line(x0 + 1.05, hy - 2.35, x0 + 0.7, hy - 2.7, 0.13, ant)
    T.line(x0 + 1.5, hy - 1.5, x0 + 1.55, hy - 2.35, 0.14, ant)
    T.line(x0 + 1.53, hy - 2.0, x0 + 1.85, hy - 2.25, 0.12, ant)
    T.line(x0 - 0.1, by - 0.55, x0 - 0.1, by + 0.5, 0.2, scale(COAT, k))  # harness and a bell
    T.circle(x0 + 0.95, by - 0.55, 0.17, scale(GOLD, k))
    T.circle(x0 + 2.12, hy - 1.02, 0.2 if nose is RUDOLPH else 0.12, nose)
    return (x0 + 2.12, hy - 1.02)


def sleigh(T: Tf, body, trim):
    """The sleigh, runners on y = 0, facing right; about 5.4 units long."""
    T.pline([(-2.5, -0.05), (2.3, -0.05), (2.75, -0.35), (2.85, -0.85), (2.55, -1.1)], 0.26, trim)
    T.line(-1.5, -0.1, -1.5, -0.7, 0.22, trim)
    T.line(1.3, -0.1, 1.3, -0.7, 0.22, trim)
    T.poly([(-2.55, -0.65), (-2.75, -2.7), (-2.4, -3.15), (-1.95, -3.0), (-1.75, -2.0), (1.35, -1.9),
            (1.9, -2.55), (2.35, -2.5), (2.2, -1.3), (1.75, -0.65)], body)
    T.pline([(-2.6, -0.75), (1.7, -0.75)], 0.18, trim)
    T.pline([(-1.75, -1.95), (1.35, -1.85), (1.9, -2.5)], 0.16, trim)
    T.circle(-2.38, -2.85, 0.2, trim)
    T.circle(-0.2, -1.32, 0.26, trim)


def sack(T: Tf):
    T.ellipse(-2.0, -3.1, 0.75, 0.7, SACK)
    T.poly([(-2.5, -3.6), (-2.0, -4.25), (-1.65, -3.65)], (70, 140, 255))   # a present peeking out
    T.poly([(-1.85, -3.7), (-1.45, -4.35), (-1.1, -3.8), (-1.5, -3.4)], GOLD)
    T.line(-2.12, -3.55, -2.12, -4.05, 0.12, WHITE)


# ---------------------------------------------------------------- the scene
class SantaLanding(Scene):
    id = "santa-landing"
    name = "Santa lands"
    blurb = ("A little story: the sleigh swoops down and lands on a snowy roof, Santa hops out, "
             "waves \"Ho ho ho!\", hops back in and takes off past the moon in a trail of sparkles.")
    seconds = 24.0
    max_seconds = 40.0
    fps = 24
    story = True
    default_colors = ("#e8182a", "#ffc41e")
    palette_role = "sleigh and the roof lights"

    def setup(self):
        c = self.ctx
        R = self.R = c.R
        lay = c.layout
        self.kind = getattr(lay, "kind", "fans")
        k = 1.0                                       # sprite scale (1: Santa is ~2/3 of a fan's radius)
        ground = 0.5                                  # the roof line, fan radii below the stage centre
        if self.kind == "fans":
            fans = c.fans
            by = max(y for _, y in fans)
            ty = min(y for _, y in fans)
            bottom = sorted((f for f in fans if abs(f[1] - by) < 1), key=lambda f: f[0])
            self.stage = sx, sy = bottom[(len(bottom) - 1) // 2]
            self.left, self.right = min(x for x, _ in fans) - R, max(x for x, _ in fans) + R
            self.top = ty - R
            top_row = sorted((f for f in fans if abs(f[1] - ty) < 1), key=lambda f: f[0])
            mx, my = top_row[-1]
            self.ground_x = (self.left, self.right)
            self.text = (sx - 0.15 * R, sy - 0.5 * R, 1.0)
            lefts = [f for f in bottom if f[0] < sx - 1]
            self.chimney = lefts[-1][0] if lefts else sx - 1.05 * R           # in the next fan to the left
            if len(fans) == 1:
                self.moon = (mx + 0.47 * R, my - 0.47 * R, 0.2 * R)
                spot_x = sx - 0.6 * R
            elif abs(ty - by) < 1:                   # one row: moon high in the right-hand fan
                self.moon = (mx + 0.08 * R, my - 0.36 * R, 0.27 * R)
                spot_x = sx - 0.6 * R
            else:
                # two rows: a big Santa (twice the size) in the middle of the stage fan, the
                # sleigh beside him running into the next fan; the words in the fan above
                # him and the moon in the top right fan
                k = 1.8
                self.moon = (mx + 0.05 * R, my - 0.02 * R, 0.42 * R)
                above = min((f for f in top_row), key=lambda f: abs(f[0] - sx))
                self.text = (above[0], above[1] + 0.05 * R, 1.3)
                spot_x = sx - 0.38 * R
                ground = 0.58
        else:
            W, H = c.W, c.H
            self.stage = sx, sy = (W / 2, H / 2)
            self.left, self.right, self.top = 0.0, float(W), 0.0
            self.ground_x = (0.0, float(W))
            if self.kind == "matrix":                # few pixels: bigger figures, plainer sky
                k = 1.35
            mr = 0.24 * R
            self.moon = (max(W / 2 + 0.5 * R, min(W - 1.6 * mr, W / 2 + 1.3 * R)), H / 2 - 0.5 * R, mr)
            spot_x = sx - 0.6 * R * k
            self.chimney = spot_x - 0.6 * R * k
            self.text = (sx - 0.15 * R, sy - 0.5 * R - 0.1 * R * (k - 1), 1.0 * k)
        self.k = k
        self.u = u = R * k / 10
        self.Rk = R * k
        self.G = sy + ground * R                      # the roof the sleigh lands on
        self.spot = (spot_x, self.G)                  # where Santa stands to wave
        self.A = (spot_x + 4.8 * u, self.G)           # where the sleigh's runners stop
        p = c.pal(self.default_colors)
        self.body, self.trim = p[0], p[1 % len(p)]
        from .scenes import hex_rgb
        self.bulbs = list(c.colors) if len(c.colors) >= 2 else [hex_rgb(h) for h in BULBS]
        self.snow = Snowfall(c, 18 if self.kind != "matrix" else 8, salt=21, big=0.9 if self.kind != "matrix" else 1.6)
        r = c.rnd(31)
        self.craters = [(r.uniform(-0.45, 0.45), r.uniform(-0.45, 0.45), r.uniform(0.08, 0.16)) for _ in range(5)]
        self.gallop = max(1, int(round(2.2 * c.secs)))        # whole gallops a loop: the legs loop too
        self.twk = max(1, int(round(0.6 * c.secs)))
        self.nods = max(1, int(round(0.3 * c.secs)))
        # the swoop in: from above the top left corner, steep at first, level at touchdown
        p0 = (self.left - 0.55 * R * k, self.top - 0.3 * R * k)
        if self.kind == "fans" and len(c.fans) == 1:
            p0 = (sx - 1.55 * R, sy - 1.25 * R)
        dx, dy = self.A[0] - p0[0], self.A[1] - p0[1]
        self.swoop = (p0, (p0[0] + 0.18 * dx, p0[1] + 0.92 * dy), (p0[0] + 0.55 * dx, self.A[1]), self.A)
        # the take-off: a short run along the roof, then up and away over the top right
        self.T0 = (self.A[0] + 3.0 * u, self.G)
        ex = self.right + 1.2 * R * k
        ey = self.top - 0.6 * R * k
        mx, my, _ = self.moon
        self.lift = (self.T0, (self.T0[0] + 0.35 * (mx - self.T0[0]), self.G - 0.05 * R),
                     (mx - 0.2 * R, my + 0.35 * R), (ex, ey))
        self.puffs = self._puffs(r)
        self.ol = max(1.0, 0.11 * u)

    def _puffs(self, r):
        """Snow-puff particles (direction, speed, size), made once so frames agree."""
        return [(r.uniform(math.pi * 1.05, math.pi * 1.95), r.uniform(0.4, 1.0), r.uniform(0.6, 1.2)) for _ in range(12)]

    # ------------------------------------------------------------ where the sleigh is
    def group_at(self, t):
        """(x, y, heading, squash, run) of the sleigh's anchor (runners' middle), or None
        when it is off stage."""
        R = self.R
        if t < T_ENTER or t >= T_GONE:
            return None
        if t < T_TOUCH:
            v = seg(t, T_ENTER, T_TOUCH)
            b = ease_out(v, 1.8)
            x, y, dx, dy = bez(*self.swoop, b)
            ang = max(-0.55, min(0.55, math.atan2(dy, dx)))
            flare = 0.3 * math.sin(math.pi * seg(v, 0.78, 1.0))        # nose up just before touching
            ang -= flare
            ang *= 1 - smooth(seg(v, 0.9, 1.0))
            return x, y, ang, 0.0, 0.9
        if t < T_RUN:
            v = seg(t, T_TOUCH, T_SETTLE)
            # two bounces, each smaller, with a squash on each contact
            hop = 0.05 * self.Rk * abs(math.sin(math.pi * 2 * v)) * (1 - v) ** 2
            sq = 0.22 * math.exp(-5 * v) * math.cos(TAU * 2 * v) if v < 1 else 0.0
            if T_SEAT <= t < T_SEATED:                                   # Santa drops back into his seat
                w = seg(t, T_SEAT, T_SEATED)
                sq += 0.14 * math.sin(math.pi * w) * (1 - w)
            if T_CROUCH <= t < T_JUMP:                                   # he pushes off: the sleigh sinks
                sq += 0.08 * math.sin(math.pi * seg(t, T_CROUCH, T_JUMP))
            run = 0.9 * (1 - smooth(seg(t, T_TOUCH, T_TOUCH + 0.025)))          # the reindeer pull up
            return self.A[0], self.A[1] - hop, 0.0, sq, run
        if t < T_LIFT:
            v = seg(t, T_RUN, T_LIFT)
            x = self.A[0] + (self.T0[0] - self.A[0]) * ease_in(v, 2.2)
            return x, self.G, -0.05 * smooth(v), 0.0, smooth(seg(v, 0.0, 0.35))
        v = seg(t, T_LIFT, T_GONE)
        b = ease_in(v, 1.6)
        x, y, dx, dy = bez(*self.lift, b)
        ang = max(-0.6, min(0.3, math.atan2(dy, dx)))
        return x, y, ang, 0.0, 1.0

    def santa_at(self, t):
        """None (sitting in the sleigh) or (x, y, pose, facing, sx, sy, extra)."""
        if t < T_CROUCH or t >= T_SEAT:
            return None
        seat = (self.A[0] - 0.75 * self.u, self.A[1] - 0.6 * self.u)
        gx, gy = self.spot
        if t < T_JUMP:                                   # crouch in the seat (anticipation)
            q = math.sin(math.pi * 0.5 * seg(t, T_CROUCH, T_JUMP))
            return (seat[0], seat[1], "seat", -1, 1 + 0.12 * q, 1 - 0.18 * q, 0.0)
        if t < T_LAND:                                   # the jump out
            v = seg(t, T_JUMP, T_LAND)
            x = seat[0] + (gx - seat[0]) * smooth(v)
            y = seat[1] + (gy - seat[1]) * v - 3.4 * self.u * 4 * v * (1 - v)
            st = 1 + 0.16 * math.sin(math.pi * min(1.0, v * 1.6))      # stretched in the air
            return (x, y, "jump", -1, 1 / st, st, v)
        if t < T_STAND:                                  # landed: squash and recover
            v = seg(t, T_LAND, T_STAND)
            q = math.sin(math.pi * v) * (1 - 0.4 * v)
            return (gx, gy, "stand", -1, 1 + 0.2 * q, 1 - 0.26 * q, 0.0)
        if t < T_WAVE0:                                  # turn to face the street
            v = seg(t, T_TURN, T_WAVE0)
            return (gx, gy - 0.5 * self.u * math.sin(math.pi * v), "turn", -1, 0.3 + 0.7 * abs(math.cos(math.pi * v)),
                    1 + 0.06 * math.sin(math.pi * v), v)
        if t < T_WAVE1:
            return (gx, gy, "wave", 0, 1, 1, seg(t, T_WAVE0, T_WAVE1))
        if t < T_TURNB:                                  # turn back towards the sleigh
            v = seg(t, T_WAVE1, T_TURNB)
            return (gx, gy - 0.5 * self.u * math.sin(math.pi * v), "turnb", 1, 0.3 + 0.7 * abs(math.cos(math.pi * v)),
                    1 + 0.06 * math.sin(math.pi * v), v)
        if t < T_JUMP2:                                  # crouch to jump
            q = math.sin(math.pi * 0.5 * seg(t, T_CROUCH2, T_JUMP2))
            return (gx, gy, "stand", 1, 1 + 0.16 * q, 1 - 0.22 * q, 0.0)
        v = seg(t, T_JUMP2, T_SEAT)                      # the jump back in
        x = gx + (seat[0] - gx) * smooth(v)
        y = gy + (seat[1] - gy) * v - 3.6 * self.u * 4 * v * (1 - v)
        st = 1 + 0.16 * math.sin(math.pi * min(1.0, v * 1.6))
        return (x, y, "jump", 1, 1 / st, st, 1 - v)

    # ------------------------------------------------------------ drawing
    def draw(self, cv: Canvas, t: float):
        t %= 1.0
        c, R, u = self.ctx, self.R, self.u
        self.sky(cv, t)
        self.snow.draw(cv, t)
        self.trail(cv, t)
        g = self.group_at(t)
        st = self.santa_at(t)
        if g is not None:
            gx, gy, ang, sq, run = g
            ph = TAU * self.gallop * t
            T = Tf(cv, gx, gy, u, 1 + sq * 0.6, 1 - sq, ang)
            behind = st is not None and (st[2] == "seat" or (st[2] == "jump" and st[6] < 0.3))
            seated = st is None

            def group():
                sack(T)
                if seated:
                    santa_side(self._shadow(T, -0.75, -0.6, ang), 0.0)
                sleigh(T, self.body, self.trim)
                T.line(2.4, -2.3, 3.6, -2.5, 0.12, self.trim)                       # reins
                T.line(2.4, -2.3, 6.4, -2.5, 0.1, self.trim)
                idle = 1 - clamp01(run / 0.9)
                reindeer(T, 4.9, ph + 0.5, run, (120, 60, 30), far=True, nod=idle * self.nod(t, 0.5))
                reindeer(T, 4.5, ph, run, (110, 56, 28), nod=idle * self.nod(t, 0.0))
                self._nose = reindeer(T, 7.4, ph + 1.1, run, RUDOLPH, nod=idle * self.nod(t, 0.27))

            if behind:
                self.draw_santa(cv, st, t)
            T.outlined(group, self.ol)
            nx, ny = T.p(*self._nose)
            pulse = 0.75 + 0.25 * math.sin(TAU * max(1, int(round(1.5 * c.secs))) * t)
            cv.glow(nx, ny, 0.09 * self.Rk, (255, 40, 40), a=pulse, steps=4)
            if st is not None and not behind:
                self.draw_santa(cv, st, t)
        self.puff(cv, t, T_TOUCH, self.A[0] - 2.0 * u, 1.0)
        self.puff(cv, t, T_TOUCH, self.A[0] + 2.2 * u, 1.0, salt=1)
        self.puff(cv, t, T_LAND, self.spot[0], 0.6, salt=2)
        self.puff(cv, t, T_LIFT, self.T0[0] - 2.0 * u, 0.8, salt=3)
        self.words(cv, t)

    def nod(self, t: float, off: float) -> float:
        """A standing reindeer now and then dips its head (whole nods a loop)."""
        return 0.3 * max(0.0, math.sin(TAU * (self.nods * t + off))) ** 3

    def _shadow(self, T: Tf, x, y, ang):
        """A copy of T at local (x, y) for a nested sprite, drawn in T's outline pass."""
        S = Tf(T.cv, *T.p(x, y), T.u, T.sx, T.sy, ang)
        S.over, S.dx, S.dy = T.over, 0.0, 0.0
        return S

    def draw_santa(self, cv, st, t):
        x, y, pose, facing, sx, sy, extra = st
        u = self.u
        T = Tf(cv, x, y, u, sx, sy, 0.0, mirror=facing < 0)
        if pose in ("seat", "stand", "jump"):
            arm = 0.85 * math.sin(math.pi * clamp01(extra)) if pose == "jump" else 0.0
            T.outlined(lambda: santa_side(T, arm, 0.6 if pose == "jump" else 0.0), self.ol)
            return
        if pose in ("turn", "turnb"):
            side = (extra < 0.5) if pose == "turn" else (extra >= 0.5)
            if side:
                T.outlined(lambda: santa_side(T, 0.0), self.ol)
            else:
                T2 = Tf(cv, x, y, u, sx, sy)
                T2.outlined(lambda: santa_front(T2, 0.0, 0.0, 0.0, False), self.ol)
            return
        v = extra                                        # waving
        raise_ = smooth(seg(v, 0.0, 0.08)) * (1 - smooth(seg(v, 0.92, 1.0)))
        ph = TAU * WAVES * v
        laugh = 0.0
        for k in range(3):                               # "ho" x 3: mouth opens, belly jiggles
            w = seg(t, T_HO0 + k * 0.022, T_HO0 + k * 0.022 + 0.02)
            laugh = max(laugh, math.sin(math.pi * w))
        blink = 0.3 < v < 0.32 or 0.75 < v < 0.77
        bob = 0.04 * laugh
        T2 = Tf(cv, x, y, u, 1 + bob, 1 - bob, 0.03 * math.sin(TAU * 2 * v) * raise_)
        T2.outlined(lambda: santa_front(T2, raise_, ph, laugh, blink), self.ol)

    def sky(self, cv: Canvas, t: float):
        c, R = self.ctx, self.R
        mx, my, mr = self.moon
        cv.glow(mx, my, mr * 1.5, (150, 135, 70), steps=8)
        cv.circle(mx, my, mr, (255, 240, 170))
        for (dx, dy, rr) in self.craters:
            cv.circle(mx + dx * mr, my + dy * mr, rr * mr, (236, 214, 130))
        twinkle_stars(cv, c, t, 7, salt=23, avoid=(mx, my, mr * 1.4))
        self.roof(cv, t)

    def roof(self, cv: Canvas, t: float):
        R, u, G = self.R, self.u, self.G
        x0, x1 = self.ground_x
        sx = self.stage[0]
        # a chimney off to the left (on wide pictures; a single fan crops it)
        cx = self.chimney
        if x0 < cx - 1.5 * u:
            for row in range(4):
                yy = G - (row + 1) * 0.75 * u
                off = 0.45 * u if row % 2 else 0
                cv.rect(cx - 1.3 * u, yy, cx + 1.3 * u, yy + 0.75 * u, MORTAR)
                for k in range(-2, 3):
                    bx = cx + k * 0.9 * u + off
                    a, b = max(cx - 1.3 * u, bx - 0.4 * u), min(cx + 1.3 * u, bx + 0.4 * u)
                    if b > a:
                        cv.rect(a, yy + 0.08 * u, b, yy + 0.67 * u, BRICK)
            cv.ellipse(cx, G - 3.0 * u, 1.65 * u, 0.42 * u, SNOW, seg=20)
        # snow on the roof: a lumpy white band, its top edge the ground line
        pts = []
        n = max(8, int((x1 - x0) / (0.25 * R)))
        for k in range(n + 1):
            x = x0 + (x1 - x0) * k / n
            pts.append((x, G + 0.015 * R * math.sin(k * 2.3) - 0.01 * R))
        pts += [(x1, G + 0.075 * R), (x0, G + 0.075 * R)]
        cv.polygon(pts, SNOW)
        cv.rect(x0, G + 0.075 * R, x1, G + 0.1 * R, (120, 150, 190))        # the eave under the snow
        # icicles and a sagging string of lights under the eave
        hook = 0.32 * R
        k0 = int(math.floor((x0 - sx) / hook)) - 1
        k1 = int(math.ceil((x1 - sx) / hook)) + 1
        for k in range(k0, k1):
            ha = sx + k * hook
            for j in range(4):
                f = (j + 0.5) / 4
                bx = ha + hook * f
                by = G + 0.12 * R + 0.06 * R * math.sin(math.pi * f)
                col = self.bulbs[(k * 4 + j) % len(self.bulbs)]
                tw = 0.55 + 0.45 * math.sin(TAU * (self.twk * t + ((k * 4 + j) * 0.37) % 1.0))
                cv.glow(bx, by + 0.02 * R, 0.05 * R, scale(col, 0.7 * tw), steps=3)
                cv.ellipse(bx, by + 0.02 * R, 0.018 * R, 0.028 * R, scale(col, 0.45 + 0.55 * tw), seg=10)
            cv.polyline([(ha + hook * f / 6, G + 0.105 * R + 0.06 * R * math.sin(math.pi * f / 6)) for f in range(7)],
                        max(1.0, 0.006 * R), (60, 90, 60))
            ix = ha + hook * 0.5
            cv.polygon([(ix - 0.012 * R, G + 0.1 * R), (ix + 0.012 * R, G + 0.1 * R), (ix, G + 0.16 * R)], ICE)

    def puff(self, cv: Canvas, t: float, t0: float, x: float, size: float, salt: int = 0):
        """A burst of snow kicked up at loop time t0 from (x, ground)."""
        v = (t - t0) / 0.045
        if not 0 <= v < 1:
            return
        R = self.Rk
        for i, (a, sp, sz) in enumerate(self.puffs):
            a = a + salt * 0.37
            d = 0.22 * R * size * sp * ease_out(v, 2.5)
            px = x + math.cos(a) * d * 1.3
            py = self.G + math.sin(a) * d * 0.6 + 0.12 * self.R * v * v
            rad = 0.03 * R * size * sz * (1 - 0.6 * v)
            cv.circle(px, py, rad, scale(SNOW, 1 - v * v))

    def trail(self, cv: Canvas, t: float):
        """Sparkles shed from the back of the sleigh as it climbs away; each fades out."""
        if not T_LIFT <= t < T_GONE + TRAIL_LIFE:
            return
        R = self.Rk
        n = 46
        cols = (GOLD, WHITE, ICE, (255, 230, 140))
        for j in range(n):
            tj = T_LIFT + (T_GONE - T_LIFT) * j / n
            age = (t - tj) / TRAIL_LIFE
            if age < 0 or age >= 1:
                continue
            g = self.group_at(tj)
            if g is None:
                continue
            gx, gy, ang, _, _ = g
            ca, sa = math.cos(ang), math.sin(ang)
            lx, ly = -2.6 * self.u, -0.3 * self.u
            x = gx + lx * ca - ly * sa + 0.04 * R * math.sin(j * 1.7)
            y = gy + lx * sa + ly * ca + 0.08 * R * age + 0.03 * R * math.cos(j * 2.3)
            b = (1 - age) ** 1.2
            r = R * (0.05 + 0.04 * ((j * 7) % 5) / 4) * (0.5 + 0.5 * b)
            cv.glow(x, y, r * 0.8, scale(cols[j % 4], 0.6), a=b, steps=3)
            cv.sparkle(x, y, r, cols[j % 4], a=b, angle=0.4 + 0.5 * j + 3 * age)

    def words(self, cv: Canvas, t: float):
        R = self.R
        tx, ty, k = self.text
        if T_HO0 <= t < T_HO1:
            v = seg(t, T_HO0, T_HO1)
            fade = 1 - smooth(seg(v, 0.85, 1.0))
            n = 1 + min(2, int(seg(t, T_HO0, T_HO0 + 0.066) * 3))
            txt = " ".join(["HO"] * n) + ("!" if n == 3 else "")
            pop = back_out(seg(v, 0.0, 0.12))
            self.say(cv, txt, tx, ty, 0.17 * R * k * pop, GOLD, fade)
        if T_MC0 <= t < T_MC1:
            v = seg(t, T_MC0, T_MC1)
            fade = 1 - smooth(seg(v, 0.85, 1.0))
            p1 = back_out(seg(v, 0.0, 0.14))
            p2 = back_out(seg(v, 0.08, 0.22))
            gap = 3.2 * self.ctx.layout.k if self.kind == "matrix" else 0.11 * R * k
            self.say(cv, "MERRY", tx + 0.03 * R, ty - gap, 0.15 * R * k * p1, (255, 60, 60), fade)
            self.say(cv, "CHRISTMAS!", tx + 0.03 * R, ty + gap, 0.13 * R * k * p2, (60, 235, 110), fade)
            if v < 0.3:                                      # a sparkle burst as the words pop
                w = v / 0.3
                for j in range(8):
                    a = TAU * j / 8 + 0.2
                    d = R * k * (0.25 + 0.4 * ease_out(w))
                    cv.sparkle(tx + math.cos(a) * d * 1.3, ty + math.sin(a) * d * 0.5,
                               0.05 * R * k * (1 - w), GOLD, a=1 - w)

    def say(self, cv: Canvas, s: str, x: float, y: float, h: float, col, fade: float):
        if h <= 1 or fade <= 0.01:
            return
        if self.kind == "matrix":                    # one LED per font cell, on the LED grid: crisp
            from .. import font
            cell = self.ctx.layout.k
            w = font.layout(s).width * cell
            x = round((x - w / 2) / cell) * cell + w / 2
            y = round((y - 2.5 * cell) / cell) * cell + 2.5 * cell
            cv.text(s, x, y, 5 * cell, scale(col, fade))
            return
        o = max(1.0 if self.kind == "matrix" else 2.0, 0.018 * self.R * self.text[2])
        for ox, oy in ((o, 0), (-o, 0), (0, o), (0, -o), (o, o), (-o, -o), (o, -o), (-o, o)):
            cv.text(s, x + ox, y + oy, h, BLACK)
        cv.text(s, x, y, h, scale(col, fade))
