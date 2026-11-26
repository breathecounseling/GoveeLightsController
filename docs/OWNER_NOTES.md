# Owner notes (this house only; not part of the public export)

`tools/export_public.py` leaves this file and `layouts/house.json` out of the public copy.

## This house

* Four boxes on the LAN: three H805A (the runs *Eaves*, *Peak*, *Accent*) and one H705E
  that does not respond to LAN commands (see below).
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
| frame rate | 10-25 fps smooth, 30 stutters | same | - |
| realtime timeout | none seen in 45 s without frames | - | - |
| white diode | not reachable: 4-byte frames are read as RGB triplets, so realtime is RGB only | same | - |

## Project diary

* Phase 1: diagnostics (`python -m goveelights diag`): discovery, bulb counts, walker and
  landmarks, frame rate, realtime timeout, white diode.
* Phase 2: effects engine (virtual strip, geometry, effects, scenes, output).
* Phase 3: the creator app, playlists, scheduler; then shows, the visitor page, beat
  sync, camera mapping, the holiday calendar and the Tonight checklist.
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
