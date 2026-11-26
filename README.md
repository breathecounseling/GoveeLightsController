# Govee Permanent Lights: Custom Effects Creator

Local-only controller for Govee permanent outdoor lights (H705E / H805A), using the
LAN API over UDP. No cloud, no API key, no dependencies beyond Python 3.10+.

**Status:** Phase 1 (diagnostics), Phase 2 (effects engine) and Phase 3 (creator app,
playlists, scheduler) are done.

## Creator app

`start.bat` (or `python -m goveelights`) starts the app. Open the printed address on your
phone; the app is designed phone-first.

* **Play:** animated preset cards rendered by the real engine, favourites (★) and tag
  filters. Quick knobs for brightness, **master speed**, **hue** and **tap tempo**, plus
  one-tap **palette restyle** of whatever is playing. Long-press a card to edit it.
* **Create:** a rack per layer: **Source** (20 generators, including 2D ones that use the
  house shape: ripple, sweep, plasma, aurora, bursts), then an **FX** chain (trails, blur,
  glow, mirror, kaleidoscope, echo, strobe, hue shift, adjust, colorize, pixelate, invert,
  gate), then **Mix** (blend mode, opacity, and where: runs or zones such as "North wall"
  or "Whole front gable", outlined on the preview).
  * **∿ on any slider** adds an LFO: sine/triangle/saw/square/random/step, free-running or
    synced to the tempo in beats, with depth and phase.
  * **Palettes** restyle a layer in one tap. Edit or create palettes under More.
  * **Undo/redo, 🎲 randomize, ✨ mutate,** duplicate layers, and save with tags.
  * Speed changes and LFOs never make motion jump: the engine integrates speed over time.
* **Shows:** playlists (timed rotation with crossfades) and the schedule (sunset or a
  fixed time, off time, and date-range rules).
* **More:** palettes, fps, gamma, devices/diagnostics, quit.
* **⏻ menu:** Go live / preview, blackout, stop, release to the Govee app.

**Sparkle family:** generators *Glitter, Shimmer, Sequins, Champagne fizz, Fireflies*,
plus *Glitter* and *Shimmer* FX that make **any** layer sparkle. Glints have a fast attack
and exponential decay, run white-hot and cool into the bulb's own colour, and bloom into
neighbouring bulbs; the base shimmers underneath. **62 palettes** in categories (Classic,
Warm, Cool, Nature, Neon, Elegant, Soft, Holidays), tuned for LEDs.

**House scenes** (picker group *Scenes*, *Physics*, *Weather*, *Text*). These effects know the
shape of the house (`goveelights/geometry.py`: zigzag rows, peaks, the gable apex, slopes,
what is below what). They are plug-ins in `goveelights/effects_ext/`:
* **Christmas tree:** the zigzag is the tree and the gable apex is its star. A garland winds
  along the wiring order, ornaments glint, the star pulses and bursts. A *lighting* intro
  lights the tree from the trunk up, then the star, and can repeat every few minutes.
  Presets: Christmas tree, Golden tree, Icy tree, Tree lighting ceremony, Spooky tree, Presents.
* **Roller coaster:** balls of light ride the roofline under real gravity. They speed up
  downhill, crest the gables, and a chain lift keeps them going.
  **Sparks & embers:** sparks pour off the apex, slide down the slopes, fall through the air
  and land glowing on the porch line.
* **Porch fireworks:** shells rise from the porch, burst in the gable and fall with drag and
  crackle. Includes a finale mode.
  **Snow that piles up:** flakes land on whatever is below them, so the porch and flat eaves
  slowly fill with snow (and melt if you want).
* **Scrolling text** on the zigzag (5-row pixel font, anti-aliased scrolling). The source can
  be your own text, the clock, the live game score or "N days to Christmas".
* **Sky at dusk:** the house faces east, so the sunset is behind the roof as seen from the
  street. This colours the house like the sky at this moment (real sun position), then settles
  into night colours. **Depth cascade** FX makes any layer fall from the roof to the porch or
  ripple from the apex. **Mirror at the apex** FX mirrors any layer around the gable peak.
* **Evolve** (Play → ⟳ on a card): plays a preset and every few minutes crossfades into a fresh
  variation of it (small, related changes), so the house never looks quite the same twice.
* **Smoothness:** motion is anti-aliased between bulbs, dark fades are dithered so they don't
  step, and each run has a colour-match trim (More → Output → Colour match, with a white test)
  so the three boxes show the same white.

Preview any effect or preset on the house without lights:
`python tools/preview.py christmas-tree --seconds 8 --out preview` writes `preview.html`/`.png`.

**Camera mapping** (More → Map the bulbs with your camera): the lights blink a binary ID
code (10 bits + parity, each bit shown with its inverse, so ambient light and auto-exposure
don't matter; one cycle is 13.5 s). Pick **Dim** blink brightness so neighbouring bulbs
don't glow together on camera. You record ~30 s with the phone's normal camera, and the page
decodes the video on the phone. Nothing is uploaded. Measured positions are fitted into the layout
(scale from the real bulb spacing, rotation and offset fitted), short gaps are
interpolated, and only the area you pick is updated. Undo restores the previous shape.

**Paint mode** (🖌 in Create): paint bulbs by dragging on the live preview, plus pictures
on the zigzag (tree, star, snowflake, heart, pumpkin, present, smile).

**Live** (Shows → Live):
* **Countdowns/events:** big digits on the zigzag with the eaves filling up, then a
  celebration preset, plus optional "days to go" reminders. New Year and Christmas are
  built in; add birthdays etc.
* **Game day:** ESPN's public scoreboard (NFL, college, NBA, NHL, MLB, MLS; any team).
  Touchdown, field goal/score and win celebrations in the team's own colours (brightened
  for LEDs), with a delay setting to match the TV.
* **Weather:** Open-Meteo for your location. Snowfall, rain, lightning and wind-shimmer
  overlays layered on whatever is playing.

Every feature has test buttons so you don't have to wait for snow or a touchdown.

**Shows** (Shows → Shows): cue lists of presets with transitions: cut, crossfade, wipe
across the house (either way), rise up the gable, iris from the apex, flash, and fade through
black. A show takes over and then hands back to whatever was playing (or loops).
**Showtimes** run a show automatically, e.g. every hour 5–10 pm on chosen days, while the
lights are on.

**Performance pad** (Play): flash, strobe (hold), blackout (hold), sparkle, fireworks,
comets, hue sweep and colour wash, layered over whatever is playing.

**Visitor page** (Shows → Live): a public page at `/visit/<secret>` with a printable QR
sign. Neighbours pick from presets you approve (queued, one pick per cooldown), or hit
"Make it sparkle". See REMOTE.md for making it public.

**Schedule extras:** rules by date (MM-DD or holidays such as `thanksgiving+1` and
`easter`), by weekday, or both, with their own on/off times (e.g. stay on until 12:30 on
Fri/Sat). Also late-night dimming and an optional morning window (on at 6:00, off at
sunrise).

**Self-healing:** while Live, each box's status is checked every 20 s. If a box drops off
the network and comes back, or its state is changed behind the app's back (Govee app,
colour command), realtime is re-enabled and the frame resent, at most once every 2
minutes per box. It pauses itself if a box keeps changing on its own. Optional periodic
re-enable is in More → Output.

**WLED (DIY pixels):** Settings → Devices → *Add a WLED controller* with its IP address.
The pixel count is read from WLED, frames are streamed over DDP (UDP 4048) at the engine's
frame rate, and the new run joins the virtual strip next to the Govee runs. Place it with
camera mapping. `tools/fake_wled.py` simulates one for testing.

**Remote access and login:** see [REMOTE.md](REMOTE.md) (Cloudflare Tunnel + Access on
your own subdomain; the app adds its own password, lockout and CSRF protection). Set the
password under More → Security, or with `python -m goveelights set-password`.

**Run in the background on Windows:** for always-on, `python -m goveelights install-service`
(admin PowerShell; starts at boot with no login, restarts if it stops). The lighter option is `python -m goveelights install-startup`, which adds a hidden launcher to your Startup folder. The app then starts at login with no window and logs to
`goveelights.log`. `python -m goveelights uninstall-startup` removes it.

## Run it (Windows)

1. In the Govee app, turn on **LAN Control** for each of the 4 boxes.
2. Install Python 3.10+ from python.org (tick "Add python to PATH").
3. Double-click `start.bat`, or run `python -m goveelights` in this folder. (The Phase 1
   tool alone: `python -m goveelights diag`.)
4. When Windows Firewall asks, allow Python on **Private networks**. Discovery replies
   arrive on UDP 4002, so blocking them means no devices show up.
5. Open the URL it prints (e.g. `http://192.168.1.50:8080/`) on your phone, on the same network as the PC.

### Phone page never loads ("connection aborted" / spins for minutes)

Windows is dropping the incoming connection. This happens when the network is marked
**Public** (the default for a newly joined network). In an **admin** PowerShell:

```powershell
Get-NetConnectionProfile            # check NetworkCategory for the lights network
New-NetFirewallRule -DisplayName "Govee tool (web)" -Direction Inbound -Protocol TCP -LocalPort 8080 -Action Allow -Profile Any
New-NetFirewallRule -DisplayName "Govee tool (discovery)" -Direction Inbound -Protocol UDP -LocalPort 4002 -Action Allow -Profile Any
```

Or mark the network Private: `Set-NetConnectionProfile -InterfaceAlias "Wi-Fi" -NetworkCategory Private`
(use the alias shown by `Get-NetConnectionProfile`). If it still fails, the router may have
client/AP isolation turned on for that network, which blocks phone-to-PC traffic.

Ctrl+C (or closing the window) sends the realtime-disable packet to every device, so the
Govee app takes back control. The page also has a **Release to Govee app** button.

Options: `--port 8080`, `--bind-ip <PC's LAN IP>` (use this if discovery finds nothing on a
PC with several network adapters, e.g. VPN or Hyper-V), `--config path.json`, `-v`.

All settings, names, bulb counts, landmarks and test results live in one file:
`govee_config.json` (created on first run, next to `start.bat`).

## Phase 1 test checklist (Settings → Open diagnostics, or `python -m goveelights diag`)

| Tab | What to do | What it tells us |
|---|---|---|
| Devices | Scan, then **Identify** each box (it blinks off/on 3x) and rename it (e.g. "Front"). | Which box is which run; IP/MAC/SKU/firmware |
| 1 Razer | Enable realtime, tap a colour. Record *whole run changed / partly / nothing*. Also try the gradient check and query status. | Whether per-pixel realtime works on H705E/H805A |
| 2 Count | Guided bisection: each test sends n copies of a colour; answer *lit* or *went dark*. ~8 answers. | Bulb count, readable from anywhere along the run, plus what oversize frames do |
| 3 Walk & map | One lit bulb; -10/-1/+1/+10/jump. Check it's a single bulb, then walk the house and **Mark** landmarks (corners, peaks, end). | 1-bulb-per-colour confirmation, index 0 location, roofline map |
| 4 FPS | Chase at 5/10/15/20/30 fps; rate each *smooth / stutter / freezes*. The page shows the actual send rate and the worst gap. | Max stable fps |
| 5 Timeout | Send one frame, then silence; tap *It reverted now* when the lights go back. Try keepalive. | Whether a keepalive is needed, and how often |
| 6 White | Compare RGB white, warm RGB, official colorwc 2700 K, and a speculative RGBW frame. | Whether the W diode is reachable in realtime |
| Report | Free-text notes, then **Copy** the JSON report and paste it back. | Everything above, for Phase 2 |

The count test relies on behaviour seen on an H706B (frames with more colours than bulbs
are rejected and the run goes dark). If your boxes do something else, tap *Stayed old
colour* or *Something else*. That gets recorded, and you then find the count with the
walker instead (step until the bulb falls off the end: last lit index = count - 1).

If razer does nothing, try the **Finger Sketch (ptReal)** fallback at the bottom of the
Razer tab and note what happens.

## Hardware findings (Phase 1, Nov 2026)

| | H805A "Eaves" | H805A "Peak" | H705E |
|---|---|---|---|
| razer per-bulb realtime | works | works | no response to any command (Wi-Fi fw 1.01.17 vs 1.02.06 on the others) |
| bulbs (one colour per bulb at n = count) | 96 (93-95 hidden) | 108 (6-row zigzag) | - |
| frame with n > bulbs | ignored, previous frame stays | same | - |
| frame rate | 10-25 fps smooth, 30 stutters | same | - |
| realtime timeout | none seen in 45 s without frames | - | - |
| white diode | not reachable: 4-byte frames are read as RGB triplets, so realtime is RGB only | same | - |

## Phase 2: effects engine

```
python -m goveelights layout import layouts/house.json   # once: geometry + hidden bulbs
python -m goveelights layout                              # show the virtual strip
python -m goveelights effects -v                          # list effects and their params
python -m goveelights play candy-cane                     # a preset from presets/
python -m goveelights play rainbow --set speed=5          # a single effect with overrides
python -m goveelights play presets/peak-fire.json --terminal   # with a live preview in the console
python -m goveelights play comets --dry-run --terminal    # preview only, lights untouched
```

Ctrl+C stops playback and hands the lights back to the Govee app.

* **Virtual strip:** all runs end to end (config `order`, `reversed`, `hidden`), so effects
  travel continuously around the roofline. Runs without a confirmed `pixel_count` are left out.
* **Geometry:** each run's `geometry` is `[[index, x, y], ...]` knots in bulb-spacing units,
  showing the house unrolled as seen from outside. `layouts/house.json` holds an
  approximate shape built from the landmarks. Phase 3's preview will make it editable.
* **Effects** (`goveelights/effects.py`) are pure functions `(t, pixel, params) -> colour`
  (motion reads the engine-integrated distance `P["_travel"]`). FX (`fx.py`) are frame
  processors with state; LFOs live in `modulation.py`; palettes in `palettes.py`. Zones
  are in the config (`zones`), imported from `layouts/house.json`. Original list:
  solid, chase, comet, wipe, wave, twinkle, sparkle, candy_cane, fire, breathe, rainbow,
  gradient, alternate. Positional effects take `axis`: `strip`, `run`, `x` or `y`. With `y`,
  an effect works bottom to top across the zigzag (fills, rising fire).
* **Scenes/presets** (`presets/*.json`) stack layers with blend modes `normal`, `add`,
  `multiply`, `screen`, `lighten` and `mask`, plus `opacity` and `target` (a run name).
* **Output** is behind `Output.send_frame(run_id, pixels)` (`goveelights/output.py`), so a
  WLED output can replace the Govee razer one without touching effects. Unchanged frames
  are resent once a second, so the lights recover if a UDP packet is lost.
* Engine settings live in the config's `engine` block: `fps` (default 20) and `gamma`
  (default 1.0, i.e. off).

## Protocol notes (verified against sources and checked in unit tests)

* `razer` packet: `BB <len_hi> <len_lo> <cmd> <payload> <xor>`. `len` = payload bytes
  after the command byte; xor covers every byte before it, including `BB`. This
  reproduces the published enable `BB 00 01 B1 01 0A` / disable `BB 00 01 B1 00 0B`
  exactly. Frame: cmd `B0`, payload `<gradient> <n> (R G B)*n`, so `len = 3n + 2`.
  Source: govee-local-api issue #404 (H706B: n=79 gives 1 colour per bulb; n=80 is rejected).
* A single enable right after power-on is sometimes ignored (per the issue), so the tool
  sends it 3 times, 0.5 s apart. Expect a blink each time.
* `ptReal` Finger Sketch: 0xA3 multi-packet framing, ported from
  homebridge-govee-pixel-zones (MIT). The test suite reproduces that repo's reference
  capture byte for byte. The activation packet `33 05 0A 20 03` is a best guess from the
  razer issue; that repo captures the real one from the Govee app.

## Testing without hardware

```
python tools/fake_govee.py --counts 96,108                      # simulated boxes on 127.0.0.2/.3
python -m goveelights                                           # then "Add by IP" 127.0.0.2
python -m unittest discover -s tests
```

## Layout

```
goveelights/protocol.py     packet builders (pure, unit-tested)
goveelights/lan.py          UDP discovery / send / devStatus
goveelights/streamer.py     per-device realtime streamer (frames, fps, keepalive, stats)
goveelights/config.py       single JSON config file
goveelights/diag_server.py  Phase 1 web server + API
goveelights/layout.py       virtual strip + geometry
goveelights/effects.py      effect library (pure functions + param schemas)
goveelights/scene.py        layers (source → FX chain → blend), modulation, palettes, master
goveelights/fx.py           frame FX rack units
goveelights/modulation.py   LFOs
goveelights/palettes.py     palette library
goveelights/engine.py       render loop, crossfades
goveelights/output.py       output interface + Govee razer output
goveelights/cli.py          command line
goveelights/controller.py   app state: presets, playlists, schedule, live/preview
goveelights/schedule.py     sunset maths + schedule rules
goveelights/app_server.py   creator app server (API, preview stream)
goveelights/web/app.html    creator app page
layouts/house.json          this house's geometry
presets/                    example presets
goveelights/web/diag.html   phone page
tools/fake_govee.py         device simulator
```
