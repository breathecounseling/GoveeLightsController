# Owner notes (this house only; not part of the public export)

`tools/export_public.py` leaves this file and `layouts/house.json` out of the public copy.

## This house

* Four boxes on the LAN: three H805A (the runs *Eaves*, *Peak*, *Accent*) and one H705E,
  the *Tip* at the point of the front gable, which answers LAN commands since late Nov 2026
  (see below).
* The house faces east, so the sunset is behind the roof as seen from the street: *Sky at
  dusk* puts the afterglow on the side where the sun went down.
* The PC runs on Mountain time (America/Denver); clock-mode presets such as *Ball drop at
  midnight* land at local midnight only if the PC's time zone is right.
* `layouts/house.json` is this house's geometry and zones (approximate until camera
  mapped). Re-import after editing it:
  `python -m goveelights layout import layouts/house.json`.
  At start-up, zones added to it since the last import are merged into the config (yours
  and your box names are kept).
* The location and the followed team that older versions assumed without storing them
  are kept in `goveelights/legacy_defaults.py`. A config file written by an older version
  gets them filled in once at start-up, so nothing changes here after an update.

## Hardware findings (Phase 1, Nov 2026)

| | H805A "Eaves" | H805A "Peak" | H705E |
|---|---|---|---|
| razer per-bulb realtime | works | works | no response to any command (Wi-Fi fw 1.01.17 vs 1.02.06 on the others) |
| bulbs (one colour per bulb at n = count) | 96 (93-95 hidden) | 108 (6-row zigzag) | - |
| frame with n > bulbs | ignored, previous frame stays | same | - |
| frame rate | 10-25 fps smooth, 30 stutters | same | 30 fps smooth (late Nov 2026, as "Tip") |
| realtime timeout | none seen in 45 s without frames | - | - |
| white diode | not reachable: 4-byte frames are read as RGB triplets, so realtime is RGB only | same | - |

Update (late Nov 2026): the H705E, named **Tip** (Wi-Fi fw 1.01.17), now answers LAN
commands: razer works, frames of up to 108 colours (bigger ones are ignored), smooth at
30 fps; gradient flag 01 blends, 00 gives hard halves. Landmarks: 0 "Left (South) corner
bottom row", 6 "Right (North) corner 2nd row", 7 "Right corner 2nd row", 10 "Left corner
2nd row", 11 "Tip". The Peak (108): 0 first bulb, 26 North bottom corner, 49 2nd row south
corner, 69 3rd row North corner, 85 4th row south corner, 97 5th row North corner, 107 6th
row south corner. So the Tip continues the Peak's zigzag: row 7 = 0-6 (south to north),
row 8 = 7-10 (north to south), 11 = the apex; 12 and up are hidden. "6 = 2nd row" is read
as the north end of row 7 (see FOURTH_RUN.md, *The top of the Peak*, to change it).
Not yet checked on the box: realtime frames go out with the gradient flag 00 and one colour
per bulb; confirm the Tip shows each bulb's own colour that way (the star on bulb 11 in
*Christmas tree*), otherwise give it a per-box gradient flag.

## The fourth run (the H705E)

More → *Fourth run* in the app (details in [FOURTH_RUN.md](FOURTH_RUN.md)): it lists the
H705E, checks it in one tap, and adds it as a run without touching Eaves, Peak, Accent or
their zones. For the Tip pick **Top of the Peak (completes the gable)**: it then completes the
gable's triangle in every Peak preset, picture, word and tree (details in FOURTH_RUN.md).
`layouts/house.json` has no Tip entry on purpose: tonight's frames stay exactly the same
until you add it in the app (the with-Tip frames are checked in tests/test_r13_tip.py). If it never answers on the LAN, paste a Govee API key there and add it with
*Govee cloud (slow)*. Nothing changes for the show until you add it.

## After a repair: Bulb check

More → *Bulb check* in the app (details in [BULB_CHECK.md](BULB_CHECK.md)): walk each run
ten bulbs at a time from the yard, then one at a time, and tap *This one is dark or wrong*;
a red/green/blue/white colour test shows bulbs with a bad colour. Marked bulbs stay dark and
the chases, words and the sleigh run straight past them. Sections you re-hung can be marked,
gap-checked and re-mapped with the camera on their own. The check gives the house back by
itself after 4 minutes without a tap, and always before a scheduled show (so a check at
5:55 pm on Thanksgiving can't hold up the 6 pm show); it can't be started while a show
plays. Nothing is marked until you mark it. Marking a bulb shifts the later bulbs one step,
so tonight's show would look very slightly different: mark bulbs after Thanksgiving night.

## Project diary

* Phase 1: diagnostics (`python -m goveelights diag`): discovery, bulb counts, walker and
  landmarks, frame rate, realtime timeout, white diode.
* Phase 2: effects engine (virtual strip, geometry, effects, scenes, output).
* Phase 3: the creator app, playlists, scheduler; then shows, the visitor page, beat
  sync, camera mapping, the holiday calendar and the Tonight checklist.
* Showpieces (Nov 2026): the zigzag as a picture (turkey fan, harvest sunset, peppermint /
  orange-and-blue sunbursts), a falling star across all runs, the sleigh's grand tour via
  the Accent, candlelight; shows *Thanksgiving tonight*, *Christmas on the roofline* and
  *Broncos touchdown*. Re-tap "Use the holiday calendar" once to get the first two at the
  top of the hour; they follow Thanksgiving's date by themselves in later years.
  The Tonight card says so ("The holiday calendar has an update") until you do.
  Thanksgiving night 2026 step by step: [TONIGHT.md](TONIGHT.md).
  A fourth run joins the whole-house looks once it has a bulb count.
* Peak pictures (Dec 2026): eight little December pictures on the zigzag, each with its
  own trim on the eaves and porch: wreath, snowman, gingerbread house, a present that
  opens, a winter star, a swinging ornament, Rudolph and JOY. Presets *Peak picture: …*,
  the show *Peak pictures* (about 4 minutes), and a few of them in the Christmas, Advent
  and Christmas Day rotations from 27 November (Thanksgiving is unchanged). After the
  update the Tonight card says "The holiday calendar has an update" once more: tap it
  to add the pictures to the rotations.
* Open-source prep: neutral defaults for new installs with the one-time migration above,
  target aliases, first-run setup, example layout, docs and `tools/export_public.py`.

## Publishing a public copy

```sh
python3 tools/export_public.py /tmp/public-export --force
```

It needs `tools/private_denylist.txt` (gitignored, local only; it stops without one unless
you pass `--no-denylist`) and fails if any private detail is left. Built in, with or
without the denylist: the earlier default location (from `legacy_defaults.py`), fragments
of the device ids in `layouts/house.json`, the home subnet and session links. Then create a NEW repository from the export (single commit); never push
this repository's history.
