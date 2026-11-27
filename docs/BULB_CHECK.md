# Bulb check

For after a repair, a storm or a ladder day: find the bulbs that are dark, stuck or the
wrong colour, keep the show looking right around them, and re-map just the part you
re-hung. In the app: **More → Bulb check**.

## Walk a run

Tap **Walk it** next to a run. The lights show:

* ten bulbs in **bright white**: the group you're looking at,
* the bulbs before them in **dim green** (already checked),
* the bulbs after them in **dim white** (still to come),
* every other run dark, so you know which run you're on.

**Next** and **Back** move ten bulbs at a time. Bulb 1 is the one next to the box, and the
numbers count along the run from there (hidden bulbs are skipped).

See a dark one, a flickering one, or one that isn't white? Tap **One of these is dark or
wrong**. Now just one bulb is bright (its neighbours glow dim). Step to it with Next and
Back and tap **This one is dark or wrong**: it's marked, and the check moves on to the next
bulb. **Back to groups of 10** carries on from there.

A bulb you marked by mistake lights up during the check like any other: step to it and
tap **It works now: clear the mark**, or tap its chip under the run (*Bulb 12 ✕*).

## Colour test

**Colour test** (one run) or **Colour test: all runs** turns every bulb red, green, blue,
then white. A bulb with a broken colour channel stands out: dark in one colour, pinkish or
yellowish in white, or the wrong colour altogether. Then walk that run to get its number.

A **whole run** the wrong colour (every red pinkish, orange pink, blue green or cyan) is not
a bulb: it is the box's colour order. Tap **Colours look wrong?** at the bottom of the
card (it opens *Match the runs*): two questions per run from the street fix it; see the
README, *Colours look wrong? (colour order)*.

## What happens to the show

* While the check runs it has the lights; everything else (the schedule, the rotation, a
  show, visitors' picks) carries on underneath, out of sight. **Done: give the house back**
  puts it straight back on the lights, at the point it has reached. Nothing restarts.
* It gives the house back by itself after **4 minutes without a tap** (the phone went in a
  pocket), after 30 minutes in all, when **a scheduled show is due** (the show always wins),
  when the lights are switched off or stopped (also *Turn lights off* and *Release to Govee
  app*), when you play something (a look, a list or a show), or when camera mapping or
  diagnostics start. **Blackout** keeps the house dark during the check too.
* It won't start while a show is playing: try again after the show ends.
* If the lights were off when you started, they go off again afterwards.
* **Done** at the top of the sheet ends the check too. Tapping outside the sheet only hides
  it: a banner at the top then says the check is on, with **Done**.

## Bulbs marked broken

A marked bulb is kept **dark** and taken out of the strip that effects draw on:

* **Motion along the roofline** (chases, candy canes, the sleigh, comets, marquee text on a
  straight run) goes straight past it. The chase steps over the gap instead of disappearing
  into it for a moment, which is what you'd see if the bulb were only painted black.
* **Pictures and scrolling words** on the zigzag keep their exact shape and size: they are
  drawn by where each bulb hangs, and the broken bulb still counts for the house's outline.
  The one bulb is dark; nothing else moves. (Shifting letters over to fill the gap would
  bend them, which reads worse from the street than one dark dot.)
* Twinkles and glints pick their bulbs by number, so a different (equally random) set of
  bulbs twinkles after a marked one.
* With nothing marked, every look plays exactly as before.
* Marking even one bulb moves every later bulb (on later runs too) one step along the
  strip, so the shows look very slightly different afterwards. If you want tonight's show
  exactly as tested, do the marking tomorrow.

Clear the mark once the bulb is replaced. More than half a run's bulbs (the ones you can
see) can't be marked: that's the
box, a connector or the power, not bulbs (see FOURTH_RUN.md's ladder checklist).

In the config file, a run's entry gets `"dead": [11, 56]` (bulb numbers minus one, counted
from the box). You can edit that by hand too; restart the app afterwards.

## Re-hung bulbs and the gap check

Bulbs that came off the house and were clipped back on rarely land exactly where the
camera map put them. Mark them: in the walk, **I re-hung these** marks the group on the
lights; or under the run, *Type bulb numbers → Re-hung from … to … → Mark*. The run then
lists *Re-hung: bulbs 41–50* with:

* **Gap check**: the re-hung bulbs alternate white and amber, with a few neighbours on each
  side dim. Two of the same colour side by side, a dark spot, an uneven gap or a bulb out
  of line means a bulb is loose, missing or clipped in the wrong place (or swapped).
* **Re-map it**: opens camera mapping with *Re-hung: …* already picked as the part to
  update. Record the blink pattern as usual (stand where you can see that part and some
  of the house around it). Only those bulbs and four neighbours on each side get new
  positions; the rest of the house is used to line them up and keeps its shape. The mark
  clears itself once the camera has seen most of the section. **Undo last** on the mapping
  card puts the old positions back.
* **Clear**: forget the mark without re-mapping.

From a computer the same is possible through the API:
`POST /api/map/apply {"points": [...], "section": {"mac": "<run id>", "from": 40, "to": 49}}`.
