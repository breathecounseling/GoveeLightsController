# GoveeLightsController

Custom animated effects, shows and a sunset schedule for **Govee permanent outdoor lights**,
run from your own computer over your home network. A phone-friendly web app lets you play
and design effects (per bulb, using the shape of your house), build playlists and shows,
schedule the year, sync to music, and share a visitor page with the neighbours.

No cloud, no account, no API key, and no dependencies beyond Python 3.10+.

> **Unofficial project.** Not affiliated with, endorsed by or supported by Govee. "Govee"
> and product names are trademarks of their owner and are used only to say which lights
> this works with. Per-bulb effects use **undocumented LAN commands** found by the
> community; a firmware update could change or block them. Use at your own risk (see the
> [licence](LICENSE): no warranty). Keep your lights' Govee app: this tool hands the lights
> back to it whenever it stops.

## Does it work with my lights?

| Model | Per-bulb effects | Notes |
|---|---|---|
| **H805A** (permanent outdoor lights 2) | yes, tested | realtime per-bulb frames at 10-25 fps; RGB only (no white diode in realtime) |
| **H705E** | no (one unit tested) | Wi-Fi firmware 1.01.17 did not answer LAN commands at all; newer firmware may. More → *Fourth run* checks it and can add it through the Govee cloud (slow): [docs/FOURTH_RUN.md](docs/FOURTH_RUN.md) |
| Other Govee lights with **LAN Control** | likely | anything that shows a *LAN Control* switch in the Govee app may work. Please [report your model](.github/ISSUE_TEMPLATE/model-report.md), working or not |

WLED controllers (ESP32 with DIY pixels) can be added alongside Govee boxes.

## Requirements

* Govee lights with **LAN Control** turned on in the Govee app (device → settings → LAN
  Control), for every box.
* A computer that stays on in the evening (Windows, macOS, Linux, a Raspberry Pi), on the
  **same network and subnet** as the lights. Phones and tablets on the same Wi-Fi use the app.
* **Python 3.10 or newer.** Nothing else to install (no pip packages).
* The firewall must let in UDP port 4002 (the lights' replies) and the web port (8080).

## Quick start

Download the project (green *Code* button → *Download ZIP*, or `git clone`), then:

**Windows**
1. Install Python from python.org (tick *Add python.exe to PATH*).
2. Double-click `start.bat` (or run `python -m goveelights` in the folder).
3. When Windows Firewall asks, allow Python on **Private networks**.

**macOS**
1. Python 3: `python3 --version` (if missing or older than 3.10, install it from python.org
   or with Homebrew).
2. In Terminal, in the project folder: `python3 -m goveelights`
3. Allow incoming connections for Python when macOS asks.

**Linux / Raspberry Pi**
1. `python3 --version` (3.10+; Raspberry Pi OS Bookworm has 3.11).
2. In the project folder: `python3 -m goveelights`
3. With a firewall on: `sudo ufw allow 4002/udp && sudo ufw allow 8080/tcp`.

Then open the address it prints (for example `http://192.168.1.50:8080/`) on your phone.
To keep it running after a reboot, see [docs/SERVICE.md](docs/SERVICE.md) (Windows task,
systemd unit, launchd agent). Optional: `pip install -e .` in the folder adds a
`goveelights` command (same as `python -m goveelights`; the folder stays where presets
and settings live).

## First-run setup

The first time you open the app it walks you through:

1. **Find your light boxes:** *Search* finds boxes that have LAN Control on (or add one by
   IP address). Name each one (e.g. *Roof*, *Gable*, *Porch*), enter its number of bulbs
   (shown in the Govee app) and put them in the order they run along the house. *Blink*
   makes a box flash so you can tell them apart. Each box becomes a straight line in the
   app; map the real shape later (below).
2. **Where is the house:** *Use this phone's location* (needs an `https://` or `localhost`
   page; the position is rounded to about 1 km), pick a nearby city, or type latitude and
   longitude. Used only for sunset times and the sky colours, on your computer. Tick the box
   to switch on at sunset and off at 11 pm.
3. **Follow a team (optional):** for game-day celebrations and the holiday calendar's
   game-day look.

Run it again any time from **More → App**. Everything lives in one file,
`govee_config.json`, next to `start.bat` (not in git).

## Mapping your house

Effects know where every bulb is, so ripples, sweeps, pictures, fire and snow follow the
roofline. After setup each box is a straight line. To get the real shape:

* **Camera mapping** (More → *Map the bulbs with your camera*): the lights blink a code,
  you film them for about 30 seconds from the street, and the phone works out where every
  bulb is. Nothing is uploaded. Undo restores the previous shape.
* **Zones** are named parts of the house (for example *Porch* = bulbs 0-40 of *Roof*).
  Layers can target a run or a zone. A layout file can set zones, hidden bulbs and shapes
  in one go: see [`layouts/examples/gable-house.json`](layouts/examples/gable-house.json)
  for the format, and import your own file with `python -m goveelights layout import <file>`.
  The example is a reference for one particular gable house, not something to import onto
  a different one. A layout made for other boxes is mapped onto yours by run name, then by
  order; it never adds boxes that weren't found on your network, a box whose bulb count
  differs from the file keeps its own shape and hidden bulbs, and zones for runs you don't
  have (or named like one of your runs) are skipped.
* Preview any preset on a layout file without lights:
  `python3 tools/preview.py christmas-tree --layout layouts/examples/gable-house.json`.

## Match the runs (colour and brightness balance)

Runs of different ages or batches can show the same "warm white" or "red" a little
differently, and a long run can be dimmer at its far end. More → **Match the runs**:

1. Tap a colour (warm white, cool white, red, green, blue, gold). Every run shows it at
   once, over whatever is playing. If the lights were off, they switch on for the test.
2. Look from the street. On any run that looks off, tap **A bit warmer / cooler /
   brighter / dimmer**. Each tap shows on the lights straight away. "A bit brighter" on a
   run that is already at full makes the other runs a little dimmer instead (a run can't
   go brighter than its bulbs). The app tells you when that happens, and it never takes the
   other runs below 60% this way.
3. **Fine-tune** has sliders for brightness, red, green, blue, mid-tones, and **Far end**:
   if the far end of a run looks dimmer than the start, slide it up a little. The start
   of the run then dims gently to match the far end.
4. **Stop the test**. What was playing comes back (and lights that were off go off
   again). The test also ends by itself after 3 minutes without the app open, when you
   leave the More tab, or when a show or celebration starts. It won't start while a show
   is playing.

The settings are kept per run in the config (`"calibration": {"brightness", "gain":
[r, g, b], "gamma", "fade"}` under the box) and apply to every look, show and picture at
the very last step before the frame is sent, after the global brightness and late-night
dimming. A run you never touched sends exactly what it did before. **Reset all runs**
puts everything back. An older `"trim"` setting is read as the gain.

## Presets on your house

The built-in presets were designed on a house with runs and zones called *Peak* (a zigzag
up the front gable), *Eaves*, *Accent*, *Porch*, *Lower roofline*, *Whole front gable* and
so on. On your house, a layer aimed at a name you don't have plays on a stand-in:

1. your own alias for it, from `"target_aliases"` in `govee_config.json`;
2. otherwise a run or zone of the same role: *peak* (names with peak, gable, apex, tree,
   zigzag), *roofline* (roof, eave, gutter, front), *porch* (porch, garage, door, entry),
   *accent* (accent, window, trim);
3. otherwise the whole house (or `"*"` in your aliases).

```json
"target_aliases": {"Peak": "Gable", "porch": "Front door", "Accent": ["Garage", "Shed"], "*": "all"}
```

The Tonight checklist (Shows) lists presets that aim at parts your layout doesn't have
and where they play instead; `GET /api/targets` has the same list. Naming your runs or
zones after the presets' names (or adding aliases) makes them look as designed.

## Shows, playlists and the schedule

* **Playlists** rotate presets with crossfades. **Shows** are cue lists with transitions
  (wipe, rise, iris, flash...); **showtimes** start them automatically.
* **Schedule** (Shows → Schedule): on at sunset (or a time), off at a time, with rules by
  date, holiday (`thanksgiving+1`, `easter`...) or weekday, late-night dimming and a morning
  window. Without a location the lights come on at the fixed on time.
* **Holiday calendar:** one tap adds a ready-made year (Valentine's, St Patrick's,
  Easter, July 4th, Halloween, Thanksgiving, Christmas, New Year, January winter scenes,
  and NFL Sundays in the fall for the team you follow), after your own rules.
* **Tonight** (top of Shows): tonight's plan and a checklist with one-tap fixes.

## Remote access

On home Wi-Fi the app needs no login. To use it away from home, set a password
(More → Security) and put it behind Cloudflare Tunnel + Access or Tailscale, ideally on
a small always-on box at home (a laptop you take with you can't run the show): see
[REMOTE.md](REMOTE.md) and [SECURITY.md](SECURITY.md). Never forward a router port to it.
The page installs like an app (Safari → Share → Add to Home Screen, or Chrome → Install
app); the icons come from `tools/make_icons.py`.

## Troubleshooting

**No boxes found.** Check *LAN Control* is on for each box in the Govee app (it can take a
minute after switching it on), and that the computer is on the same network (not a guest
network, and not a different subnet behind a mesh or second router). Allow UDP 4002 in the
firewall. With several network adapters (VPN, Hyper-V, Docker), start with
`--bind-ip <this computer's LAN address>`. You can also add a box by IP address.

**A box is found but nothing lights up.** Open diagnostics (More → *Open diagnostics*),
tap *Check* on the box: "ignores commands" means it answers discovery but not control
(seen on an H705E with older firmware). Power-cycle the box and try *Enable realtime*
twice. Please report the model and firmware. More → *Fourth run* has a phone checklist
(LAN Control switch, firmware, Wi-Fi, power-cycle), a one-tap *Is it answering now?* and,
as a last resort, a slow Govee-cloud output: [docs/FOURTH_RUN.md](docs/FOURTH_RUN.md).

**A few bulbs are dark, stuck or the wrong colour.** More → *Bulb check* walks a run with a
bright marker and a colour test, and keeps the bulbs you mark dark so chases and words run
past them; re-hung bulbs can be re-mapped on their own: [docs/BULB_CHECK.md](docs/BULB_CHECK.md).

**The phone page never loads.** The computer is dropping the connection: on Windows the
network is probably marked *Public*. See *Windows firewall* below. Some routers isolate
Wi-Fi clients (AP/client isolation); turn that off for your home network.

**Sunset times are hours off.** The computer's time zone must be the house's time zone;
the Tonight checklist warns when they don't match.

**Effects stutter.** Lower the frame rate (More → Output; 20 fps is a good default) and
keep the computer on a wired or strong Wi-Fi connection.

**The Govee app shows the lights as busy.** The app holds the lights while it is live.
Stop, or quit the app (Ctrl+C), and the lights go back to the Govee app.

**The microphone (Listen) is greyed out.** Browsers only allow it on `https://` or
`http://localhost` pages, so use the tunnel address from REMOTE.md.

### Windows firewall

If the phone page spins or "connection aborted", in an **admin** PowerShell:

```powershell
Get-NetConnectionProfile            # check NetworkCategory for the lights network
New-NetFirewallRule -DisplayName "Govee tool (web)" -Direction Inbound -Protocol TCP -LocalPort 8080 -Action Allow -Profile Any
New-NetFirewallRule -DisplayName "Govee tool (discovery)" -Direction Inbound -Protocol UDP -LocalPort 4002 -Action Allow -Profile Any
```

Or mark the network Private: `Set-NetConnectionProfile -InterfaceAlias "Wi-Fi" -NetworkCategory Private`
(use the alias shown by `Get-NetConnectionProfile`).

### Options

`--port 8080`, `--bind-ip <LAN IP>`, `--config path.json`, `--log file`, `-v`.
Ctrl+C (or closing the window) hands every box back to the Govee app; the app also has a
*Release to Govee app* button.

### Updating

`git pull` (or download again and keep your `govee_config.json`, `govee_auth.json` and
`user_presets/` folder). Your own presets and favourites live in `user_presets/` (not in
git), so updates never touch them.

If a pull stops with "Your local changes ... would be overwritten" in `presets/`: copy the
`presets` folder somewhere safe, run `git checkout -- presets` and `git clean -f presets`,
`git pull` again, then copy your own preset files from the copy into `user_presets/`.

---

# Feature tour


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
* **Sky at dusk:** colours the house like the sky at this moment (the real sun position at
  your location), then settles into night colours. **Depth cascade** FX makes any layer fall from the roof to the porch or
  ripple from the apex. **Mirror at the apex** FX mirrors any layer around the gable peak.
* **Evolve** (Play → ✨ Evolve next to what's playing, or as a schedule program): plays a preset and every few minutes crossfades into a fresh
  variation of it (small, related changes), so the house never looks quite the same twice.
* **Smoothness:** motion is anti-aliased between bulbs, dark fades are dithered so they don't
  step, and each run can be balanced against the others (More → *Match the runs*, below)
  so all your boxes show the same white.

* **Santa's sleigh:** a sleigh led by reindeer (red nose in front) flies over both gables
  with a glittering trail. **Dripping icicles:** drops slide down the slopes, fall off the
  eaves and splash on the porch line. **New Year's ball drop:** the ball leaves the apex
  star and drops down the zigzag with the last seconds as big digits, then bursts. Use
  *Ball drop at midnight* (clock mode, lands at local midnight: the computer's clock and time
  zone must be right) or *Ball drop (preview loop)* to try it.

* **Starry night:** a deep-blue sky with twinkling stars and shooting stars streaking along
  the gables. **Gift wrap:** the front is wrapped like a present: ribbon through the apex,
  a bow on top, and it ties itself (and can unwrap into sparkles and rewrap).
  **Advent calendar:** one more door lights each night in December; today's door sparkles.

**The nightly experience:**
* **Dusk intro** (Shows → Schedule → *Dusk intro*, minutes): when the lights come on at
  sunset, the house plays *Sky at dusk* for that long, then crossfades into the usual
  program. Anything you play by hand ends it.
* **Scoreboard during games** (Live → Game day → *Scoreboard every*): while your team is
  playing, the score scrolls on the zigzag in team colours every N minutes for a few
  seconds. Touchdown celebrations still take priority.
* **Built-in shows:** *Christmas Eve spectacular* (about 9 min), *New Year's Eve countdown*
  (ends exactly at midnight when played within 20 minutes of it), *Winter wonderland
  loop* and *Santa's coming* (a looping Christmas Eve rotation with the sleigh). Play them as they are or copy one and edit the copy. To run the New Year's show
  automatically, add a showtime at 23:55 with date `12-31` and "only when on" unticked
  (it switches the lights on for the show if needed). See `goveelights/shows_builtin.py`.
* **Featured tonight** (top of Play): the big scenes in one swipeable row.
* **Tonight card** (top of Shows): the evening's plan at a glance, with on and off times
  and why, the program (and which rule picked it), showtimes, countdowns, games, weather
  and the visitor page. Below that is a checklist (boxes answering, schedule on, bulb
  counts, showtimes that would fire before lights-on...). Each warning has a one-line fix
  and a button that opens the right screen. The same data is at `GET /api/tonight`.
* **Holiday calendar** (Shows → Schedule → *Use the holiday calendar*): one tap adds a
  ready-made year, placed after your own rules so yours still win:
  - Valentine's week, St Patrick's, Easter week, July 4th, the Halloween fortnight,
    Thanksgiving, and game-day Sundays in the fall for the NFL team you follow.
  - The Christmas season, then Advent with the advent calendar.
  - Christmas Eve (with the *Santa's coming* show), Christmas Day, and New Year's Eve
    (on until 12:30 am, with the countdown show at 23:55), then New Year's Day.
  - January winter scenes.

  The preview lists any of your own rules that would hide calendar nights, so you can
  switch them off. Tap again later to refresh it, or remove it.

**Year-round looks:**
* *Valentine's hearts* (hearts rise up the gable and pop at the apex) and *Valentine's glow*.
* *St Patrick's Day* (rainbow rows).
* *Easter pastels* (striped eggs float up).
* *Fourth of July flag* (stripes with a starry blue canton, rippling) and *Fourth of July
  fireworks*.
* *Haunted house* (ghosts with dark eyes drift across) and *Halloween pumpkin* (a
  jack-o'-lantern face with candle flicker).
* *Thanksgiving harvest*, and *Game day* in white and gold for any team (*Broncos game day*
  is one team's version: copy it and set your team's colours).
* *Winter elegance*, *Spring garden* and *Summer evening*.

Preview any effect or preset on the house without lights:
`python tools/preview.py christmas-tree --seconds 8 --out preview` writes `preview.html`/`.png`
(`--layout <file>` picks the layout).

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
sign. Neighbours see a live view of the house, pick from presets you approve (queued, one
pick per cooldown, and they see their place in line), hit "Make it sparkle", **shake their
phone for a snow flurry**, or **send one of eight colours** rippling out from the apex.
Each of these can be switched off; all are rate limited. See REMOTE.md for making it public.

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

**Listen to the music (beat sync):** Play → **🎤 Listen** under the tempo. The phone's
microphone hears the music and the house keeps time with it:
* The phone does the listening (Web Audio, nothing is recorded or uploaded): it finds the
  kicks from the low end (40-180 Hz spectral flux against an adaptive threshold), works out
  the tempo from them (60-180 BPM; missed and extra hits don't throw it, syncopated kicks
  settle on the beat, a slow song counts double time and a very fast one half time), locks
  onto the beat (an off-beat bass line doesn't pull it), and follows the loudness and bass.
  About 11 small updates a second go to the PC (`POST /api/beat`), with the time since the
  last kick and the measured network delay; the PC phase-locks its own beat grid to them,
  so jitter doesn't reach the lights.
* **Sync offset** (under Listen, saved): the lights run this far ahead of the music to make
  up for the microphone, a frame of rendering and the Govee controllers (default +70 ms).
  If the house flashes after the kick, move it right; before the kick, left.
* The detected tempo becomes the master tempo (the one *Tap tempo* sets; never saved, so
  your own tap tempo comes back after a restart), and every tempo-synced LFO glides onto
  the music's beat, whatever the master Speed.
* Effects see the music for about 2 seconds after the last update, then it fades out in a
  second, so nothing sticks if the phone locks. In a quiet room or between songs the
  effects go back to the master tempo. The screen is kept on while listening (where the
  browser allows it; otherwise the app asks you to keep it on); listening stops by itself
  when the screen goes off or you switch apps (battery); tap Listen again to restart.
  Several phones can listen at once: the first one leads until it stops.
* **Presets:** *Music pulse* (the gable zigzag is a bass meter: rows light from the bottom
  up on every kick, flash and change colour on the beat, the apex flashes white) and
  *Party beat* (dots that jump ahead on every beat and turn round every 8 beats). Both keep
  time with the master tempo when no music is playing. *Plasma party* and *Glitter ball*
  speed up with the loudness and pulse on the beat when music is heard (unchanged
  otherwise).
* **Make anything dance:** in Create, the ∿ on any slider can follow **Loudness**, **Bass**
  or **Beat** instead of an LFO, and the **Beat & loudness (music)** FX makes any layer's
  brightness pulse on the beat or follow the loudness (with an optional white flash).
* **The microphone needs a secure page.** Phones only allow it on `https://` (your
  Cloudflare tunnel address from [REMOTE.md](REMOTE.md)) or on `http://localhost`, not on
  the home address `http://192.168.x.x:8080`. On the home address the app says so and
  links the tunnel address once you have opened the app through it. If you refused the
  microphone, allow it for the site in the browser's settings.
* `python3 tools/preview.py music-pulse --audio 120` previews a preset with a made-up
  120 BPM song.

**Hologram fans** (More → Hologram fans): makes looping clips for spinning LED hologram
fans on the porch rail (Santa's sleigh, jack-o'-lantern, fireworks, snow globe...) in the
house's colours, for one fan or a spliced row / square of fans, as GIF, MP4 (if ffmpeg is
installed) and, if asked, PNG frames. Full-size clips are made in a separate low-priority process, so the
lights keep running smoothly. CLI: `python -m goveelights hologram --list`. Buying,
safety and loading tips: [docs/HOLOGRAM_FANS.md](docs/HOLOGRAM_FANS.md). Fans can't be
driven live; the files are loaded in the fan's own app.


## Diagnostics (More → Open diagnostics, or `python -m goveelights diag`)

| Tab | What to do | What it tells us |
|---|---|---|
| Devices | Scan, then **Identify** each box (it blinks off/on 3x) and rename it (e.g. "Front"). | Which box is which run; IP/MAC/SKU/firmware |
| 1 Razer | Enable realtime, tap a colour. Record *whole run changed / partly / nothing*. Also try the gradient check and query status. | Whether per-pixel realtime works on H705E/H805A |
| 2 Count | Guided bisection: each test sends n copies of a colour; answer *lit* or *went dark*. ~8 answers. | Bulb count, readable from anywhere along the run, plus what oversize frames do |
| 3 Walk & map | One lit bulb; -10/-1/+1/+10/jump. Check it's a single bulb, then walk the house and **Mark** landmarks (corners, peaks, end). | 1-bulb-per-colour confirmation, index 0 location, roofline map |
| 4 FPS | Chase at 5/10/15/20/30 fps; rate each *smooth / stutter / freezes*. The page shows the actual send rate and the worst gap. | Max stable fps |
| 5 Timeout | Send one frame, then silence; tap *It reverted now* when the lights go back. Try keepalive. | Whether a keepalive is needed, and how often |
| 6 White | Compare RGB white, warm RGB, official colorwc 2700 K, and a speculative RGBW frame. | Whether the W diode is reachable in realtime |
| Report | Free-text notes, then **Copy** the JSON report (paste it into a model report or bug report). | Everything above, for a bug report or model report |

The count test relies on behaviour seen on an H706B (frames with more colours than bulbs
are rejected and the run goes dark). If your boxes do something else, tap *Stayed old
colour* or *Something else*. That gets recorded, and you then find the count with the
walker instead (step until the bulb falls off the end: last lit index = count - 1).

If razer does nothing, try the **Finger Sketch (ptReal)** fallback at the bottom of the
Razer tab and note what happens.

## Under the hood: the effects engine

```
python -m goveelights layout import my-house.json           # geometry, hidden bulbs, zones
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
  showing the house unrolled as seen from outside. camera mapping fills it in; layout
  files (`layouts/examples/`) can set it too.
* **Effects** (`goveelights/effects.py`) are pure functions `(t, pixel, params) -> colour`
  (motion reads the engine-integrated distance `P["_travel"]`). FX (`fx.py`) are frame
  processors with state; LFOs live in `modulation.py`; palettes in `palettes.py`. Zones
  are in the config (`zones`), set by camera mapping or imported from a layout file. Original list:
  solid, chase, comet, wipe, wave, twinkle, sparkle, candy_cane, fire, breathe, rainbow,
  gradient, alternate. Positional effects take `axis`: `strip`, `run`, `x` or `y`. With `y`,
  an effect works bottom to top across the zigzag (fills, rising fire).
* **Scenes/presets** (`presets/*.json`) stack layers with blend modes `normal`, `add`,
  `multiply`, `screen`, `lighten` and `mask`, plus `opacity` and `target` (a run or zone name; see *Presets on your house*).
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
python3 tools/fake_govee.py --counts 96,108                     # simulated boxes on 127.0.0.2/.3
python3 -m goveelights                                          # then "Add by IP" 127.0.0.2
python3 -m unittest discover -s tests
```

On macOS only `127.0.0.1` exists on the loopback interface, so add one alias per fake box
first: `sudo ifconfig lo0 alias 127.0.0.2 up` and `sudo ifconfig lo0 alias 127.0.0.3 up`
(they go away at the next restart). On Windows use `py` or `python` in place of `python3`.

## Layout

```
goveelights/protocol.py     packet builders (pure, unit-tested)
goveelights/lan.py          UDP discovery / send / devStatus
goveelights/streamer.py     per-device realtime streamer (frames, fps, keepalive, stats)
goveelights/config.py       single JSON config file
goveelights/diag_server.py  diagnostics web server + API
goveelights/layout.py       virtual strip + geometry
goveelights/effects.py      effect library (pure functions + param schemas)
goveelights/scene.py        layers (source → FX chain → blend), modulation, palettes, master
goveelights/fx.py           frame FX rack units
goveelights/modulation.py   LFOs
goveelights/palettes.py     palette library
goveelights/engine.py       render loop, crossfades
goveelights/output.py       output interface + Govee razer output
goveelights/cloud.py        opt-in Govee cloud output (slow, rate-limited) + API key file
goveelights/addrun.py       add a run to an existing house; "Is it answering now?"
goveelights/cli.py          command line
goveelights/controller.py   app state: presets, playlists, schedule, live/preview
goveelights/schedule.py     sunset maths + schedule rules
goveelights/app_server.py   creator app server (API, preview stream)
goveelights/web/app.html    creator app page
layouts/examples/            example layout files (geometry + zones)
presets/                    built-in presets (yours go in user_presets/, not in git)
goveelights/web/diag.html   phone page
tools/fake_govee.py         device simulator (tools/fake_wled.py: a WLED one)
tools/preview.py            render a preset on a layout to HTML/PNG
goveelights/legacy_defaults.py  defaults older versions relied on (kept for existing configs)
goveelights/firstrun.py     first-run setup (straight-line layout, city list)
docs/                       running as a service, examples
```

## Contributing, security, licence

* Model reports, bugs and presets are welcome: see [CONTRIBUTING.md](CONTRIBUTING.md).
* Security issues: see [SECURITY.md](SECURITY.md).
* MIT licence ([LICENSE](LICENSE)); third-party parts are listed in
  [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
