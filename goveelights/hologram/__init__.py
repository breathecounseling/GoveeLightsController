"""Hologram fan studio: looping clips for spinning LED "hologram" (POV) fans.

Consumer hologram fans play clips you load through their own app or an SD card; none of
them can be driven live from a PC. So this package makes the clips: pure-Python 2D scenes
(Santa's sleigh, a jack-o'-lantern, fireworks...) drawn for a POV fan (pure black =
see-through, bold saturated shapes, seamless loops), laid out for one fan or a "splice"
set of several fans showing one big picture, and written out as GIF / PNG frames / MP4.

    from goveelights.hologram import Clip, FanLayout, export
    clip = Clip("sleigh", FanLayout("1x3", size=720), seconds=8)
    export.export(clip, "out/")

CLI: python -m goveelights hologram sleigh --layout 1x3 --size 720 --seconds 8 --out out/
See docs/HOLOGRAM_FANS.md for buying, mounting and loading the clips.
"""
from __future__ import annotations

from .layout import LAYOUT_NAMES, LAYOUTS, FanLayout
from .render import Clip
from .scenes import SCENES

__all__ = ["Clip", "FanLayout", "LAYOUTS", "LAYOUT_NAMES", "SCENES", "scene_list"]


def scene_list() -> list[dict]:
    return [cls.info() for cls in SCENES.values()]
