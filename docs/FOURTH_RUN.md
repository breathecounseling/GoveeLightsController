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
* **When a preset leaves it dark** (optional): about a third of the built-in presets name
  the original runs (candy cane is "Eaves" + "Accent" + "Peak", the peak and tree ones
  aim at the gable), so they leave a new run black. Pick **Copy** and a run or zone, and
  in any preset whose layers don't reach the new run, it shows that run's or zone's
  colours stretched along its own bulbs. The sheet suggests the zone it would join (or a
  run of its kind of place); "Leave it dark" turns it off. On layouts/house.json this
  takes a second roofline from dark in 18 of 70 presets to 1, and a porch run from 23 to 5.
  Presets that light the new run themselves keep their own look on it, and the other
  runs are byte-for-byte the same either way (the tests check every preset).
* **How it's driven**: realtime over Wi-Fi (like the other runs) or the Govee cloud. After
  a check that found it ignoring commands, the sheet starts on the Govee cloud and warns
  on the Wi-Fi choice (it would stay dark).

What changes and what doesn't: the new run goes **last** in the strip, so the other runs
keep their bulb numbers, and it gets a **straight placeholder line inside the house's
current outline**, so the other runs keep their positions. Presets that light only named
runs and zones (not "all", not the zone you joined) therefore look exactly as before on
the other runs (the tests check this for every built-in preset, with a 50-150 bulb run in
each place). Whole-house layers now include it. Afterwards, **Map with your camera** and
pick the new run under *Apply to*: only that run's shape changes (its placeholder line is
not used as a reference).

## The top of the Peak (the Tip)

On this house the fourth box, named **Tip** (H705E), carries the Peak's zigzag on up to the
point of the gable. Once it answers the check, **Add it to the show** → *Where is it?* →
**Top of the Peak (completes the gable)**:

* **Bulbs**: everything the box drives (the Tip takes frames of up to 108).
* **Bulbs you can see on the gable** (default 12): the first bulbs from bulb 0. The rest
  (coiled up or hidden) stay dark.
* **Which end is bulb 0?** *Where the Peak's top row ends* (the usual way: the zigzag just
  carries on) or *The other end*.
* **Bulbs in each row, from bulb 0** (default `7, 4, 1`): row 7 is bulbs 0-6, row 8 is
  bulbs 7-10, bulb 11 is the very top.

The app works out where those bulbs are from the Peak's own shape: the same bulb spacing
(0.7), the same centre line, the same slope of the sides (each row is as wide as the
triangle is at that height), so the rows land at heights 4.38, 5.01 and 5.64, just under
the Eaves' roofline at the point (Eaves bulbs 42/43, "Peak tip North/South", are that
roofline, not the Tip). It joins every zone that holds the Peak's top row (*Zigzag top
half*, *Whole front gable*, *Whole front*) and becomes part of the Peak: a layer aimed at
"Peak" lights it, the December pictures use it (the wreath hangs from a ribbon with a bow on
the point, the snowman's hat rises to a bobble there, the star and the ornament's hook sit
on the point, JOY's star moves up), the Christmas tree and *Tree lighting ceremony* put
their star on bulb 11, words use it as part of the twinkling border, and the sleigh's
stardust drifts onto it (the sleigh itself stays on the rooflines). The house's outline
doesn't change, so whole-house effects keep their size. **Take it out** puts the zones,
the box and every frame back exactly as before.

Assumption to check from the yard: the owner's landmark "6 = Right (North) corner 2nd row"
is read as the north end of the Tip's *first* row (0-6 one row of 7, then 7-10, then 11).
If the Tip is wired differently, change the row numbers in the sheet (*Change* on its card)
or `TIP_ROWS` in `goveelights/addrun.py`; nothing else depends on them. Camera mapping can
still measure it afterwards like any other run.

Added it earlier as **Somewhere else** (copying the Peak)? Then it lights only in some
looks. Its card shows **Make it the top of the Peak**: one tap moves it there, keeping its
bulb count, name, how it's driven and its colour order (a default name "Run 4" becomes
"Tip"); *Change* adjusts the rows afterwards.

Effects that run along a strip (comets, scanners, mirrors, the glints' glow) also carry on
from Peak bulb 107 into Tip bulb 0, so they climb on up to the point instead of starting
over on the Tip's 12 bulbs.

Second thing to check from the yard: the app sends the Tip one colour per bulb with the
gradient flag off (00), the same as the other boxes. The owner saw 00 give "hard halves"
when only two colours are sent; with a colour for every bulb that should not matter. Run
*Christmas tree* and look at the top: if the Tip shows two blocks of colour instead of a
star on bulb 11, say so, and the Tip gets its own gradient setting.

Previews: `python3 tools/contact_sheet.py peak-star christmas-tree --tip 12 --focus "Whole front
gable" --out /tmp/sheet` (`--tip-flip`, `--tip-rows 6,5,1` try the alternatives).

## Govee cloud fallback (opt-in)

If the box never answers on the LAN, it can still follow the show through Govee's cloud
API (Govee Platform API v2):

1. Get a key in the Govee Home app: Profile (👤) → ⚙ Settings → **Apply for API Key**; it
   arrives by email. Applying again replaces the old key.
2. Paste it in **More → Fourth run → Govee cloud**. It's stored in `govee_cloud.json` next
   to the config (gitignored), never in `govee_config.json`, never logged, and the app
   only ever shows its last four characters. On Linux and macOS the file is made
   owner-only (chmod 600); on Windows that setting does nothing, so the file gets the
   folder's usual permissions: keep the app's folder in your own user profile.
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
pauses until Govee's reset when its `X-RateLimit-Remaining` drops below 300 (at most a
day), and stops (until you paste a key again) when the key is rejected. The card shows
today's count.

A busy preset (twinkles, rainbows, plasma) would hit the 6-a-minute cap the whole time
and spend the 2,000 in about five or six hours, which is less than a winter night from
dusk. So the rounds are **paced**: what is left of today's 2,000 is spread over the hours
until midnight (when the count resets; after midnight, until 6 am), and the card says
when changes are slowed ("a change every 40 s"). With fewer than 150 left only the main
colour is sent (no gradient). If the budget does run out, the run holds its last colour
until midnight, but the **power-off at the end of the show always goes out**, budget or
not, and so does the power-off when you take a lit cloud run out of the show.

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
