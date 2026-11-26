# Thanksgiving night checklist (Thursday 26 November 2026)

Owner notes, not part of the public export. Times are Mountain time. Do the update
before 5 pm: the lights come on at 4:26 pm, and the first show is at 6:00 pm.

## 1. Before you update (2 minutes)

In the project folder (PowerShell or Command Prompt):

```
git rev-parse --short HEAD
```

Write that number down. It's the version you have now, and you need it if you roll back.

## 2. Update

1. Stop the app:
   * If it runs in a window (`start.bat`): close the window, or press Ctrl+C in it.
   * If it runs at boot (install-service): in an **administrator** PowerShell (right-click
     PowerShell, *Run as administrator*): `schtasks /End /TN GoveeLightsController`.
     A normal window answers "Access is denied".
   * If it starts hidden when you sign in (install-startup, no window): open Task Manager,
     end **pythonw.exe**, or sign out and back in after the pull. If you skip this, the new
     copy stops with "Port … is busy", the old version keeps running, and the calendar
     update in step 3 never shows up.
2. `git pull`
3. If the pull stops with **"untracked working tree files would be overwritten"** and lists
   `presets\something.json`: that file is a preset of yours with the same name as a new
   built-in one. Move each file it lists into `user_presets\`, then run `git pull` again.
   Your copy still wins over the built-in one. (If a file with that name is already in
   `user_presets\`, move it to the desktop instead.)
4. Start the app again: double-click `start.bat`, or (administrator PowerShell)
   `schtasks /Run /TN GoveeLightsController`.

Your settings (`govee_config.json`), password and visitor link (`govee_auth.json`,
config), your presets (`user_presets\`), shows and playlists aren't touched by the
update. The setup screen shouldn't appear. If it does, close it: **don't** run setup again.

## 3. In the app (phone): Shows tab, Tonight card

1. You should see **"The holiday calendar has an update"** (yellow). Tap it, check that
   *Thanksgiving tonight* is listed under Shows, then tap **Update the holiday calendar**.
   This is the step that schedules tonight's show. It only changes the calendar's own
   rules; your own rules, shows and playlists stay as they are. (Changes you made to the
   calendar's own entries are replaced.)
2. The Tonight card should now read:
   * **4:26 pm** lights on: the dusk sky (10 minutes before sunset)
   * **4:36 pm** Thanksgiving rotation (turkey fan, harvest glow, harvest sunset,
     candlelight, GIVE THANKS)
   * **6:00 pm** Show: Thanksgiving tonight, every hour, 6:00 pm to 9:00 pm (4 times,
     about 5 minutes each). After each one it goes back to the rotation.
   * **11:00 pm** lights off
3. Checks: "All 3 boxes responding" (or "All 4", with the H705E on the network), "PC clock time zone: right for Mountain time",
   "Schedule on". A weather warning ("couldn't reach the forecast") is harmless.
4. If the H705E is on the network, you'll see "A box isn't in the show yet" (yellow). The
   show still plays on Eaves, Peak and Accent. Only try **More → Fourth run** today if you
   have time before 5 pm. Nothing about the show changes until you tap *Add it to the show*.
   If instead you see a red "Peak won't light up" (or Eaves, Accent): that run lost its bulb
   count. **Don't** use Fourth run for it; open Diagnostics, or re-import your layout file
   (`python -m goveelights layout import layouts/house.json`).

## 4. What happens when

| Time | What |
|---|---|
| 4:26 pm | Lights on with the dusk sky |
| 4:36 pm | Thanksgiving rotation (a new look every 8 minutes) |
| 6:00, 7:00, 8:00, 9:00 pm | Thanksgiving tonight show (about 5 min), then back to the rotation |
| 11:00 pm | Lights off |
| Fri 27 Nov | Christmas season starts: the Christmas rotation, and *Christmas on the roofline* at the top of each hour, 6 to 9 pm |

## 5. If something looks wrong

* **A box stays dark**: wait a minute (the app heals boxes by itself). Then More → Devices.
  If it's still dark, power-cycle that box (unplugged for 30 s).
* **The show didn't start at 6:00**: Shows tab → Tonight. Is the schedule paused because
  you played something by hand? Tap Resume in the schedule. You can always start it
  yourself: Shows → Shows → *Thanksgiving tonight* → Play.
* **Wrong times** (an hour off): the PC's time zone isn't Mountain time. Fix it in Windows
  Settings → Time & language, then restart the app.
* **The phone page doesn't load**: is the app running on the PC? Is the phone on the home
  Wi-Fi, not mobile data?
* **"The holiday calendar has an update" shows up again** (yellow) after you already
  updated: that's the December pictures for the Christmas rotation. It doesn't change
  tonight. Tap update or leave it for tomorrow; both are fine.
* **Anything else, and it's close to 6 pm**: roll back (below). The old version still
  plays the Thanksgiving look; it just doesn't have the hourly show.

## 6. Roll back to the version you had

1. Stop the app (step 2.1).
2. `git checkout <the number you wrote down>`
3. Start the app again (step 2.4).
4. If you had already tapped *Update the holiday calendar*: Shows → Schedule →
   **📅 Use the holiday calendar** → **Update the holiday calendar**. This puts back the
   older calendar (the evolving Thanksgiving harvest look, no hourly show). Without this
   step the Tonight card shows red "showtime can't play" / "program broken" checks,
   because the older version doesn't have the new shows and presets.

Your settings and presets keep working with the older version. (It shows a red "Bulb
count unknown" for the H705E if that box is on the network. Ignore it: the three runs
still light up.) To go forward again
later: `git checkout claude/govee-lights-effects-creator-n2w51x`, then restart.
