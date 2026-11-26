# Hologram fans on the porch

Spinning LED "hologram" fans (also sold as 3D hologram projectors or POV fans) are a row
of LEDs on each blade, flashed in time with the spin, so a picture seems to float in the
air. Black pixels are simply LEDs that stay off, so on a dark porch only the bright parts
of the picture show, with the house and the railing visible behind them.

This app can't drive them live (see "What the app can't do" below). What it does is
**make the clips** to load onto them, in the same colours as the house lights:
More → Hologram fans on the phone, or on the PC:

```
python -m goveelights hologram --list
python -m goveelights hologram sleigh --layout 1x3 --size 720 --seconds 8 --out holo
python -m goveelights hologram fireworks --palette "Patriotic"
python -m goveelights hologram football --team "#fc4c02,#0a2343"
```

Scenes: Santa's sleigh over the moon, jack-o'-lantern with ghosts, ghost swirl, fireworks,
snow globe, star and wreath, Happy New Year, game-day field goal (in your team colours).
Every clip loops seamlessly: the last frame runs straight back into the first.

Each run writes, per fan: an animated GIF, an H.264 MP4 (only if `ffmpeg` is on the PC's
PATH; it's skipped otherwise) and, if asked, the PNG frames. Files are named
`<scene>-<size>[-<layout>-fanN]`, fan 1 being the left one (on a 2 × 2 square: top left,
top right, bottom left, bottom right).

## Buying

* **Size.** Consumer fans run from about 42 cm to 65 cm across, with roughly 224 to 1152
  LEDs per blade. Bigger and denser reads better from the street; a 42 cm fan is fine on a
  porch you walk past.
* **Indoor vs outdoor.** Most fans are indoor gadgets with bare blades. For a porch rail,
  buy one sold for outdoor use: an **IP65 rated** model inside a clear **dome or full
  guard**. The dome keeps rain and snow off the motor and fingers off the blades.
* **Splicing sets.** Some fans can be "spliced": several fans mounted side by side play
  one big picture between them. Sets of 2, 3 or 4 with a mounting frame and a group-sync
  feature in the app are the easiest. Pick the matching layout in the app (1 × 2, 1 × 3,
  1 × 4 or 2 × 2), set the fan size and the gap between blade tips as mounted, and load
  each fan's own file.
* **How clips get on.** Through the maker's phone app over Wi-Fi or Bluetooth, or by
  copying files to the fan's SD card. Check the listing says it accepts your own videos
  (MP4 and/or GIF); a few only play the maker's library.
* **The only live option** is a different product class: HDMI hologram fan walls such as
  AIHOLO's, which take a video signal from a PC. Those are pricier, installer-grade kits.

## What the app can't do

No consumer fan takes a live picture from a PC, so the fans can't follow the lights
frame by frame. Switching which stored clip plays is done in the fan's app.

Some people have reverse-engineered the LAN control of particular models (for example
UDP discovery on port 8988 plus TCP 6666 on some 42 cm "PD42"/Holoscope-style fans, or a
TCP port on a 42 cm model's own Wi-Fi access point). These projects are the starting points:

* <https://github.com/AuClark/sektor5> (pull request #106)
* <https://github.com/Michdo93/hologram-fan-projector>
* <https://github.com/zackmcmurrin-dev/holo-fan-linux>
* <https://github.com/leogreal/holofan>

They are **experimental and untested here** (no fan was available). The app ships only the
interface a driver would fill in, `goveelights/hologram/drivers.py`: a `FanDriver` base
class (discover / list clips / play clip / power), a `ManualDriver` (the default: it never
touches the network), a clearly marked `LanProtocolStub` that does nothing, and a `FanHub`
with `switch(clip)` and `cue({"fans": "<clip>"})`, the hook a show cue or schedule rule
could call later. With no driver registered those calls only log.

## Safety

* The blades spin fast and are hard plastic: mount the fan **out of reach** of children,
  pets and passers-by, and keep the guard or dome on.
* Outdoors: IP65 (or better) fan, outdoor-rated plug and extension, drip loop on the cable.
* Wind: bolt it to something solid so a gust can't swing or tip it; take it in for storms.
* Cold: check the maker's lowest operating temperature before leaving it out on winter
  nights; condensation inside a dome on a cold night can fog it.
* Brightness: they're bright at night; aim them at the street, not into a neighbour's window.

## Content tips (what the scenes are built around)

* **Black is transparent.** Use pure black (#000) for "nothing there"; dark greys show as a
  dim haze. The app's art is drawn on pure black and the GIF palette keeps black exact.
* **Square, subject centred** inside the circle; the corners are never shown and the rim,
  where the blade moves fastest, is the thinnest part of the picture.
* **Bold, big, saturated.** Thick strokes and large shapes; no fine text or thin lines.
  Dark colours (a navy team colour) are brightened automatically.
* **Splice sets**: render one wide canvas and cut a square per fan at the fans' centre
  spacing. Whatever falls in the gaps is lost, so key art sits inside one fan and only
  things in motion cross between fans.
* **Loops**: 4 to 10 s at 20 to 30 fps; every moving thing completes whole cycles so the loop
  has no jump.
* **Size**: 720 × 720 is safe for most fan apps; they scale to the fan's own resolution.
