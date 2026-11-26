"""Where the fans hang, and how one wide canvas is cut into one square clip per fan.

A "splice" set is several fans mounted side by side that together show one big picture:
every fan plays its own clip, cut from the same big canvas at that fan's centre. The
canvas is laid out to scale: fan diameter D cm and centre-to-centre spacing S cm (S >= D,
the blades must not touch), so the art lines up across the gaps the way it will on the
railing. What falls in a gap between fans is never seen, so scenes keep key art inside
the circles.

Layouts: "1" (single fan), "1x2" ... "1x6" (a row), "2x2" (a square of four), "2x3" and
"2x4" (two rows: a big wall of fans on a roofline).
"""
from __future__ import annotations

from dataclasses import dataclass

from .canvas import Canvas

LAYOUTS = {"1": (1, 1), "1x2": (1, 2), "1x3": (1, 3), "1x4": (1, 4), "2x2": (2, 2),
           "1x5": (1, 5), "1x6": (1, 6), "2x3": (2, 3), "2x4": (2, 4)}
LAYOUT_NAMES = {"1": "Single fan", "1x2": "Row of 2", "1x3": "Row of 3", "1x4": "Row of 4", "2x2": "Square of 4",
                "1x5": "Row of 5", "1x6": "Row of 6", "2x3": "2 rows of 3", "2x4": "2 rows of 4"}


@dataclass
class FanLayout:
    kind = "fans"                # (a class attribute: not a dataclass field)
    name: str = "1"
    size: int = 720              # pixels across one fan's clip (square)
    diameter_cm: float = 42.0    # blade tip to blade tip
    spacing_cm: float = 0.0      # centre to centre; 0 = diameter + 10 %

    def __post_init__(self):
        if self.name not in LAYOUTS:
            raise ValueError(f"layout must be one of {', '.join(LAYOUTS)}")
        self.size = int(self.size)
        if not 32 <= self.size <= 2048:
            raise ValueError("size must be 32 to 2048 pixels")
        self.diameter_cm = float(self.diameter_cm)
        if not 10 <= self.diameter_cm <= 300:
            raise ValueError("fan diameter must be 10 to 300 cm")
        self.spacing_cm = float(self.spacing_cm or 0)
        if not self.spacing_cm:
            self.spacing_cm = round(self.diameter_cm * 1.1, 1)
        if self.spacing_cm < self.diameter_cm:
            raise ValueError("centre spacing must be at least the fan diameter (the blades would hit)")
        if self.spacing_cm > self.diameter_cm * 4:
            raise ValueError("centre spacing is more than 4 fan diameters; the picture would be mostly gaps")

    @property
    def rows(self) -> int:
        return LAYOUTS[self.name][0]

    @property
    def cols(self) -> int:
        return LAYOUTS[self.name][1]

    @property
    def count(self) -> int:
        return self.rows * self.cols

    @property
    def out_size(self) -> tuple[int, int]:
        """Pixels of each output clip (one square per fan)."""
        return (self.size, self.size)

    @property
    def pitch(self) -> int:
        """Centre spacing in canvas pixels."""
        return int(round(self.size * self.spacing_cm / self.diameter_cm))

    @property
    def canvas_size(self) -> tuple[int, int]:
        p = self.pitch
        return ((self.cols - 1) * p + self.size, (self.rows - 1) * p + self.size)

    def origins(self) -> list[tuple[int, int]]:
        """Top-left corner of each fan's square, row by row (left to right, top to bottom)."""
        p = self.pitch
        return [(c * p, r * p) for r in range(self.rows) for c in range(self.cols)]

    def centres(self) -> list[tuple[float, float]]:
        h = self.size / 2
        return [(x + h, y + h) for (x, y) in self.origins()]

    def hero(self) -> int:
        """The fan that gets the main subject: the middle one (left of middle for even rows,
        so fan 2 of 4 in a row). A 2x2 square has no middle fan, and the centre of the
        square is the dark gap between the four, so its hero is fan 2, the top right: the
        moon and the pumpkin sit there and things travel across the other three."""
        return (self.count - 1) // 2

    def tiles(self, cv: Canvas, mask: bool = True) -> list[Canvas]:
        """Cut the canvas into one square per fan, black outside the circle."""
        out = []
        for (x, y) in self.origins():
            t = cv.crop(x, y, self.size)
            if mask:
                t.mask_circle()
            out.append(t)
        return out

    def to_dict(self) -> dict:
        w, h = self.canvas_size
        return {"name": self.name, "rows": self.rows, "cols": self.cols, "size": self.size,
                "diameter_cm": self.diameter_cm, "spacing_cm": self.spacing_cm, "pitch": self.pitch,
                "canvas": [w, h], "origins": self.origins()}
