# Getting a box that won't answer into the show

For a Govee box that the app finds on the network but that ignores commands, or one that
has no bulb count yet (in this project the case seen was an H705E on Wi-Fi firmware 1.01.17
next to H805A boxes on 1.02.06). In the app: **More → Fourth run**.

The card lists every box that isn't in the show yet. For each one:

* **Is it answering now?** sends a discovery scan, asks the box for its status three
  times and, if it answers, shows a green test frame (realtime, the same command the show
  uses). The answer is in words: *not on the network at all*, *on the Wi-Fi but ignores
  commands*, or *answers commands: is it green?*. Only that box is touched; the other runs
  keep playing. With a Govee API key saved it also asks the Govee cloud whether the light
  is online.
* **Add it to the show** makes it a new run (see *Adding a run* below).
* **Blink it** (once added) flashes just that run, so you can see it's the right one.
* **Take it out** puts everything back as it was before you added it.

## Ladder checklist

Before you climb:

1. Govee Home app → the light → ⚙ settings → **LAN Control**: on. The switch can take
   several minutes to appear after the box comes online; if it never appears, re-pair the
   box to Wi-Fi or update its firmware first.
2. Same page (or Profile → ⚙ → Firmware update): install any **firmware update**, and
   leave the app open until it finishes. LAN control (and the realtime command this app
   uses) depends on the box's firmware.
3. **Same router** as the PC: 2.4 GHz, no guest network, no Wi-Fi extender or second
   router in between. A corner of the house with a weak signal can be enough to lose
   commands while discovery still works.
4. Tap **Is it answering now?**: if it answers, you may not need the ladder.

Up the ladder:

5. **Power-cycle** the controller box: unplugged (or outlet off) for 30 seconds, then wait
   a minute after plugging it back in.
6. Check every **connector** between the box and the first bulb, and between strings:
   fully pushed in, nut tight, no water inside.
7. **Box placement**: not inside a metal gutter, behind a downspout or a metal fascia;
   as high and open as the cable allows.
8. Take a **photo of the label** on the box (model/SKU and MAC), for support questions.

Back on the ground:

9. **Is it answering now?** again. Answers and turns green: **Add it to the show**.
10. Still ignores commands: add it through the **Govee cloud** (slow, see below).

## Adding a run

* **Where is it?** Porch, Garage, Second roofline, Left eave, Right eave or Somewhere else.
  This sets its *role* (porch, roofline, accent; see "Presets on your house" in the README):
  a preset aimed at a place this house has no run or zone for plays on the closest match,
  and now also on the new run of that role.
* **Bulbs**: type the count, or **Find the count**: the box is sent n copies of one colour;
  a frame with more colours than the box has bulbs is ignored, so each "did it change?"
  halves the range (about 8 questions). Needs the box to answer realtime.
* **Join a zone** (optional): e.g. a second roofline joined to *Lower roofline*. Every
  preset layer aimed at that zone then runs across both.
* **How it's driven**: realtime over Wi-Fi (like the other runs) or the Govee cloud.

What changes and what doesn't: the new run goes **last** in the strip, so the other runs
keep their bulb numbers, and it gets a **straight placeholder line inside the house's
current outline**, so the other runs keep their positions. Presets that light only named
runs and zones (not "all", not the zone you joined) therefore look exactly as before on
the other runs (the tests check this for every built-in preset, with a 50-150 bulb run in
each place). Whole-house layers now include it. Afterwards, **Map with your camera** and
pick the new run under *Apply to*: only that run's shape changes (its placeholder line is
not used as a reference).

## Govee cloud fallback (opt-in)

If the box never answers on the LAN, it can still follow the show through Govee's cloud
API (Govee Platform API v2):

1. Get a key in the Govee Home app: Profile (👤) → ⚙ Settings → **Apply for API Key**; it
   arrives by email. Applying again replaces the old key.
2. Paste it in **More → Fourth run → Govee cloud**. It's stored in `govee_cloud.json` next
   to the config (owner-only permissions, gitignored), never in `govee_config.json`, never
   logged, and the app only ever shows its last four characters.
3. **Add it to the show** → *Govee cloud (slow)* → *Pick the light in your Govee account*.

What it does: the show renders the run's bulbs like any other run; a separate output, on
its own thread (the render loop never waits for the internet), averages what the run
showed since the last update and sends **only what changed**: on/off, brightness, the main
colour, and on a box with segment colours a coarse gradient of up to four colours (one
request per colour). Fast effects become a slowly changing wash. Camera mapping can't
see it (the blink code is far too fast for the cloud).

Rate limits. Govee publishes: 10,000 requests per day per account, 10 control commands per
minute per device, 10 device-list requests per minute ([rate limiting][rate]). This app
stays well under: at most **6 commands a minute**, one round of changes every **20 s**,
and **2,000 a day**; it pauses on HTTP 429 for the `Retry-After` time (or 1, 2, 4… minutes),
pauses until Govee's reset when its `X-RateLimit-Remaining` drops below 300, and stops
(until you paste a key again) when the key is rejected. The card shows today's count.

## Research notes (Nov 2026)

* **LAN API**: discovery is a `scan` sent to multicast 239.255.255.250 port 4001; boxes
  reply to port 4002 of the sender; commands (`turn`, `brightness`, `colorwc`,
  `devStatus`) go to the box's port 4003. LAN control is off until switched on per device
  in the Govee app, and the switch "may take several(!) minutes" to show up
  ([openHAB Govee binding][oh], [Govee LAN API guide][lan]).
* **H705E and LAN**: listed as LAN-capable ("H705E Permanent Outdoor Lights #2 30M") in
  the openHAB binding (not tested by its author) and in homebridge-govee's LAN model list
  ([homebridge-govee changelog][hb]). So the model itself supports LAN control; a box that
  answers the scan but not commands points at the LAN Control switch, the firmware or the
  Wi-Fi link.
* **No LAN Control switch / devices not found**: Govee's own FAQ says to check *LAN
  Control* in Device Settings, and if it is missing, reconnect the device to Wi-Fi or
  rebind it; upgrade the device firmware; keep the device and the computer on one router
  and one subnet, without repeaters or bridges ([Govee Desktop FAQ][faq]). Users report
  that the switch is what makes boxes appear ([Home Assistant forum][ha1]).
* **razer / ptReal (per-bulb realtime)**: undocumented; it is what Govee Desktop's
  DreamView / Razer Chroma features use, for devices on Govee's "supports PC" list, and
  needs a working LAN connection ([Govee Desktop user guide][dt]). No public source says
  whether the H705E takes it; the in-app check tests it directly (green test frame).
* **Cloud API v2** (`https://openapi.api.govee.com/router/api/v1`, header
  `Govee-API-Key`): `GET /user/devices` lists the account's devices with their
  capabilities; `POST /device/control` with `{"requestId", "payload": {"sku", "device",
  "capability": {"type", "instance", "value"}}}`; `powerSwitch` (1/0), `brightness`
  (1-100), `colorRgb` (0-16777215), `segmentedColorRgb` (`{"segment": [0, 1, …], "rgb"}`,
  segments 0-14 on the documented example), `segmentedBrightness`; `POST /device/state`
  ([control][ctl], [device list][devs]). Key: [applying for an API key][key].

[rate]: https://govee.readme.io/reference/rate-limiting
[oh]: https://www.openhab.org/addons/bindings/govee/
[lan]: https://app-h5.govee.com/user-manual/wlan-guide
[hb]: https://unpkg.com/homebridge-govee@10.15.0/CHANGELOG.md
[faq]: https://desktop.govee.com/user-manual/faq
[ha1]: https://community.home-assistant.io/t/having-problems-getting-the-govee-lights-local-integration-working/775190?page=2
[dt]: https://desktop.govee.com/user-manual/user-guide
[ctl]: https://developer.govee.com/reference/control-you-devices
[devs]: https://developer.govee.com/reference/get-you-devices
[key]: https://govee.readme.io/reference/applying-for-an-api-key
