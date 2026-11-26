# Contributing

Thanks for helping! This is a small hobby project; reports from other houses are the most
useful thing you can send.

## Report your lights (works or not)

Open an issue with the **Model report** template. The diagnostics page
(More → *Open diagnostics*) gives you everything it asks for: the model (SKU), firmware,
whether per-bulb realtime works, the bulb count, and the JSON report (*Report* tab →
*Copy*). Please remove anything you'd rather not share (IP addresses are private to your
network, but you can blank them).

## Bugs

Use the **Bug** template: what you did, what happened, what you expected, your operating
system and Python version, and the last lines of `goveelights.log` if the app runs in the
background. Never paste `govee_config.json` or `govee_auth.json` whole: they hold your
location, device ids and login secrets.

## Code

* Python 3.10+, standard library only: no pip dependencies, please.
* Run the tests before sending a change: `python -m unittest discover -s tests`. Tests that
  need `node` skip themselves when it isn't installed.
* Test without lights using the simulators: `python tools/fake_govee.py --counts 96,108`
  (then *Add by IP* 127.0.0.2 in setup) and `python tools/fake_wled.py`.
* `python tools/preview.py <preset> --layout layouts/examples/gable-house.json` renders a
  preset to an HTML page so you can see it without lights.
* Keep changes backwards compatible with existing `govee_config.json` files: a new
  setting needs a default in `config.DEFAULTS`, and a changed default must not change an
  existing house (see `goveelights/legacy_defaults.py`).
* The web app is one file per page (`goveelights/web/*.html`), no build step.

## Presets

Presets are JSON files in `presets/`. Aim layers at generic names (`all`, or the role
names *Peak*, *Eaves*, *Porch*, *Accent*) so they work on other houses through target
aliases (README: *Presets on your house*). Check yours with `tools/preview.py` on the
example layout.

## Licence

By contributing you agree that your contribution is released under the project's MIT
licence.
