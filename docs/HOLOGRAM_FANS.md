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
python -m goveelights hologram football --team "#c8102e,#ffffff" --formats gif,mp4,png
```

Scenes: Santa's sleigh over the moon, **Santa lands** (a 24 s story, below), jack-o'-lantern
with ghosts, ghost swirl, fireworks, snow globe, star and wreath, Happy New Year, game-day
field goal (in your team colours). Every clip loops seamlessly: the last frame runs
straight back into the first.

### Santa lands (santa-landing)

A little film, about 24 s a loop (12 to 40 s with `--seconds`): night sky, moon and snow
over a snowy roof with twinkling string lights; the sleigh and reindeer swoop down in an
arc from the top left, touch down with a bounce and a puff of snow; Santa crouches, hops
out, turns to face the street and waves while "HO HO HO!" and "MERRY CHRISTMAS!" pop up;
he hops back in, the reindeer run along the roof and the sleigh takes off past the moon
in a trail of sparkles that fades before the loop starts again. Rudolph's nose glows the
whole time. A palette recolours the sleigh and the roof lights ("Match the house" makes
the roof lights the colours the real lights are playing).

On one fan or a row the whole story plays in the middle fan of the row. On two rows
(2 × 2, 2 × 3, 2 × 4) Santa is drawn twice as big in the middle bottom fan, the sleigh
and reindeer run on into the next fan, the words appear in the fan above him and the moon
sits in the top right fan, so a big wall reads from across the street.

```
python -m goveelights hologram santa-landing --layout 2x3 --size 480 --diameter 65
```

## Other big displays: a projector, a screen, an LED matrix

The same scenes can be made for hardware that is much cheaper per square metre than a
wall of fans:

```
python -m goveelights hologram santa-landing --frame 1920x1080                 # projector
python -m goveelights hologram santa-landing --frame 16:9 --size 1080 --background night   # TV
python -m goveelights hologram santa-landing --matrix 64x32 --preview          # see it in the terminal
python -m goveelights hologram santa-landing --matrix 64x32                    # GIF + raw RGB + LED-look GIF
python -m goveelights hologram santa-landing --matrix 64x32 --live 192.168.1.60   # EXPERIMENTAL, WLED over DDP
```

In the app: More → Hologram fans → Display: *Hologram fans*, *Projector or screen* or
*LED matrix*.

* **Frame** (`--frame WxH` or an aspect like `16:9` at `--size` pixels tall): one
  rectangular picture, any shape; MP4 / GIF / PNG. `--background black` (the default) keeps
  black as "no light", which is what you want for a projector aimed at the house wall or a
  white sheet hung in a window (the classic "window ghost" trick); `night` adds a deep-blue
  sky for a TV. The story scene uses the whole width; the other scenes are laid out across
  it as a row of overlapping virtual fans, so snow and flights fill the picture.
* **Matrix** (`--matrix WxH`, 8 to 256 a side): the scene is drawn several times bigger and
  each LED gets the average of the patch it covers (area-averaged downsampling), so thin
  lines become dim LEDs instead of flickering in and out; the words are drawn one LED per
  font cell so they stay sharp. `--gamma 2.2` darkens mid-tones for raw LEDs (leave it at
  1 if WLED's own gamma correction is on). Files: a native-size GIF (recent WLED builds with
  GIF support can play it from the device with the Image effect), `.rgb` (W × H × 3 bytes a
  frame, frames back to back, rows from the top left, or zig-zag with `--serpentine`) plus a
  `.json` note of the size and frame rate, and `-view.gif`, a blown-up "what the LEDs look
  like" GIF for checking the art.
* **Live on WLED (experimental)**: `--live IP[:PORT]` renders the matrix frames and streams
  them with the same DDP packets the app uses for WLED strips (UDP 4048), looping until
  Ctrl-C (or `--loops N`). Set the panel up in WLED (Config → 2D) first. Don't point it at a
  WLED device the house layout also drives. It is a command-line tool only; the app and the
  schedule never start it. Untested against a real panel here.

## Are big fan walls DIY-able for less?

Roughly (2026 prices, check before buying):

* **Off-the-shelf fan walls** (5 to 6 ft splice sets with a frame) cost a lot because each
  fan is a sealed, synced, rain-rated product with its own controller. Several separate
  42 to 65 cm outdoor fans plus a home-made frame are cheaper than a kit, at the cost of
  syncing them by hand.
* **DIY POV fans** (an ESP32 and an LED strip on a spinning bar) are a known hobby project,
  but a 1.5 m blade spinning outdoors needs serious balancing, a strong motor, slip-ring or
  inductive power and a solid guard. It is not a weekend build, and the safety risk is real.
* **Cheaper big pictures**: a used 1080p projector aimed at the wall or at a white sheet in a
  window gives the biggest picture for the money (`--frame`); an LED matrix from P10 / P5
  panels or WS2812 grids driven by WLED gives a bright, weatherproofable sign for less than
  a fan wall (`--matrix`), at a much lower resolution. Both play this app's scenes.

Each run writes, per fan: an animated GIF and an H.264 MP4 (only if `ffmpeg` is on the
PC's PATH; it's skipped otherwise). PNG frames are only written if asked: tick "PNG
frames" in the app, or add `png` to `--formats` on the PC (the default is `gif,mp4`).
`--palette` takes a built-in palette, one you made in the app, or `#hex,#hex,...`. Files
are named `<scene>-<size>[-<layout>-fanN]`, fan 1 being the left one (on a 2 × 2 square:
top left, top right, bottom left, bottom right).

On a splice set the main subject (the moon, the pumpkin, the goal posts) sits in one
fan: the middle one of a row (fan 2 of a row of 4), and on a 2 × 2 square fan 2, the top
right (the middle of a square is the gap between the fans). The ghost swirl, snow globe
and wreath don't span the fans: each fan plays its own copy.

The phone's small previews are drawn inside the app (they're tiny and cached); the
full-size files are made in a separate, low-priority process so the lights never wait.

## Buying

* **Size.** Consumer fans run from about 42 cm to 65 cm across, with roughly 224 to 1152
  LEDs per blade. Bigger and denser reads better from the street; a 42 cm fan is fine on a
  porch you walk past.
* **Indoor vs outdoor.** Most fans are indoor gadgets with bare blades. For a porch rail,
  buy one sold for outdoor use: an **IP65 rated** model inside a clear **dome or full
  guard**. The dome keeps rain and snow off the motor and fingers off the blades.
* **Splicing sets.** Some fans can be "spliced": several fans mounted side by side play
  one big picture between them. Sets of 2, 3 or 4 with a mounting frame and a group-sync
  feature in the app are the easiest. Pick the matching layout in the app (a row of 2 to
  6, or 2 × 2, 2 × 3, 2 × 4), set the fan size and the gap between blade tips as mounted, and load
  each fan's own file.
* **How clips get on.** Through the maker's phone app over Wi-Fi or Bluetooth, or by
  copying files to the fan's SD card. Check the listing says it accepts your own videos
  (MP4 and/or GIF); a few only play the maker's library.
* **The only live option** is a different product class: HDMI hologram fan walls such as
  AIHOLO's, which take a video signal from a PC. Those are pricier, installer-grade kits.

## What the app can't do

No consumer fan takes a live picture from a PC, so the fans can't follow the lights
frame by frame. Switching which stored clip plays is done in the fan's app.

Some people have reverse-engineered the LAN or Wi-Fi control of particular fan models
(typically a discovery broadcast plus a TCP command port, or the fan's own Wi-Fi access
point). To find that work, search GitHub for "hologram fan" together with your fan's
model number or app name; check the project matches your exact model before trying it.

Any such protocol is **experimental and untested here** (no fan was available). The app ships only the
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
  has no jump. A story (Santa lands) runs longer and is a timeline that starts and ends on
  the same empty sky.
* **Size**: 720 × 720 is safe for most fan apps; they scale to the fan's own resolution.
