"""Turn a scene + a layout into frames: one wide canvas per frame, cut into one square
clip per fan (FanLayout), or kept as one picture (targets.FrameLayout) or averaged down
to an LED matrix (targets.MatrixLayout)."""
from __future__ import annotations

from .canvas import Canvas
from .layout import FanLayout
from .scenes import SCENES, Ctx

MIN_SECONDS, MAX_SECONDS = 2.0, 12.0          # story scenes allow longer (Scene.max_seconds)
MIN_FPS, MAX_FPS = 10, 30


def max_seconds(scene: str) -> float:
    """The longest loop a scene may be made at (12 s, or longer for a story scene)."""
    return float(getattr(SCENES[scene], "max_seconds", 0) or MAX_SECONDS)


class Clip:
    def __init__(self, scene: str, layout: FanLayout | None = None, seconds: float | None = None,
                 fps: float | None = None, colors=None, team=None, year=None, seed: int = 7):
        if scene not in SCENES:
            raise ValueError(f"unknown scene {scene!r} (one of {', '.join(SCENES)})")
        cls = SCENES[scene]
        self.scene_id = scene
        self.layout = layout or FanLayout()
        self.seconds = float(seconds or cls.seconds)
        top = max_seconds(scene)
        if not MIN_SECONDS <= self.seconds <= top:
            raise ValueError(f"seconds must be {MIN_SECONDS:g} to {top:g}")
        self.fps = float(fps or cls.fps)
        if not MIN_FPS <= self.fps <= MAX_FPS:
            raise ValueError(f"fps must be {MIN_FPS} to {MAX_FPS}")
        self.count = max(2, int(round(self.fps * self.seconds)))
        self.ctx = Ctx(self.layout, self.fps, self.seconds, colors, team, year, seed)
        self.scene = cls(self.ctx)
        self._cv = Canvas(*self.layout.canvas_size)

    def canvas_at(self, t: float) -> Canvas:
        """The whole canvas at loop phase t (0..1). The returned canvas is reused."""
        cv = self._cv
        cv.clear()
        self.scene.draw(cv, t)
        return cv

    def tiles_at(self, t: float, mask: bool = True) -> list[Canvas]:
        return self.layout.tiles(self.canvas_at(t), mask)

    def frame(self, i: int, mask: bool = True) -> list[Canvas]:
        """Frame i's per-fan tiles (i = count gives t = 1.0, which equals frame 0)."""
        return self.tiles_at(i / self.count, mask)

    def frames(self, mask: bool = True):
        for i in range(self.count):
            yield self.frame(i, mask)

    def info(self) -> dict:
        return {"scene": self.scene_id, "layout": self.layout.to_dict(), "seconds": self.seconds,
                "fps": self.fps, "frames": self.count, "year": self.ctx.year}
