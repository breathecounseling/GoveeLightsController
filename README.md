# Govee Permanent Lights: Custom Effects Creator

Local-only controller for Govee permanent outdoor lights (H705E / H805A), using the
LAN API over UDP. No cloud, no API key, no dependencies beyond Python 3.10+.

**Status: Phase 1 (diagnostic + phone mapping tool).** Phases 2 and 3 (effects engine,
creator UI, scheduler) come after the hardware results are in.

## Run it (Windows)

1. In the Govee app, turn on **LAN Control** for each of the 4 boxes.
2. Install Python 3.10+ from python.org (tick "Add python to PATH").
3. Double-click `start.bat`, or run `python -m goveelights` in this folder.
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

## Phase 1 test checklist (on the phone page)

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
python tools/fake_govee.py --devices 2 --count 74 --timeout 8   # simulated boxes on 127.0.0.2/.3
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
goveelights/web/diag.html   phone page
tools/fake_govee.py         device simulator
```
