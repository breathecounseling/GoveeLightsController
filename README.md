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

**Run in the background on Windows:** `python -m goveelights install-startup` adds a hidden
launcher to your Startup folder. The app then starts at login with no window and logs to
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
