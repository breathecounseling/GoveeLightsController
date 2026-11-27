# Keeping the app running

The lights only follow the schedule while the app is running, so on the computer that
runs it, start it automatically.

## Windows

In an **administrator** PowerShell, in the project folder:

```powershell
python -m goveelights install-service      # starts 30 s after boot, no login needed, restarts if it stops
schtasks /Run /TN GoveeLightsController    # start it now
```

This also adds firewall rules for UDP 4002 (the lights' replies) and the web port, on
Private networks. To remove it: `python -m goveelights uninstall-service`.

Lighter option (starts when you log in, no admin needed):
`python -m goveelights install-startup` (`uninstall-startup` removes it).

The log is `goveelights.log` in the project folder.

## Linux (systemd), e.g. a Raspberry Pi

A step-by-step guide for setting up a Pi from nothing is in
[RASPBERRY_PI.md](RASPBERRY_PI.md).

As your normal user (not with `sudo` in front), in the project folder:

```sh
python3 -m goveelights install-service --print   # optional: show what it will do, change nothing
python3 -m goveelights install-service           # asks for your password once (sudo)
```

This writes `/etc/systemd/system/goveelights.service` and starts it. The service:

* starts at boot, before anyone logs in, once the network is up;
* runs as you, in the project folder, with the same Python you ran the command with
  (a `.venv` works too);
* restarts 10 seconds after it stops for any reason;
* on stop, hands the lights back to the Govee app (like Ctrl+C).

It prints the commands it runs and where to look afterwards:

```sh
journalctl -u goveelights -f              # watch the log live (Ctrl+C stops watching, not the app)
journalctl -u goveelights --since today   # today's log
systemctl status goveelights              # is it running?
sudo systemctl restart goveelights        # after an update or a settings-file change
python3 -m goveelights uninstall-service  # remove it (stops the app; no longer starts at boot)
```

Running `install-service` again is safe: it rewrites the unit and restarts the app.
`--user NAME` runs it as someone else. If you'd rather write the unit by hand, start from
[examples/goveelights.service](examples/goveelights.service).

* **Time zone:** the schedule (sunset, lights-off time) uses the computer's own clock.
  Check with `timedatectl`; fix with `sudo timedatectl set-timezone America/Chicago`
  (your zone: `timedatectl list-timezones`). Restart the service after changing it.
* **Firewall:** Raspberry Pi OS has none switched on. If you turned on `ufw`, allow the
  lights' replies and the web page: `sudo ufw allow 4002/udp` and `sudo ufw allow 8080/tcp`.
* **Ethernet and Wi-Fi both connected:** the app looks for the lights on both. If it
  still finds none, pin it to one: `--bind-ip <the Pi's address on the lights' network>`
  (or `"bind_ip"` under `"server"` in `govee_config.json`).
* **A slow computer:** if frames take longer than the 50 ms they have at 20 fps, the app
  steps down to 15, then 12 fps by itself and back up when it can (More → Output shows
  it). `python3 tools/bench.py` times every preset on your house.

## macOS (launchd)

1. Copy the example agent and set your folder and Python path in it
   (`which python3` shows the path; `python3 --version` must say 3.10 or newer, which
   the `/usr/bin/python3` from Apple's command line tools often doesn't: install Python
   from python.org or Homebrew):

   ```sh
   cp docs/examples/goveelights.plist ~/Library/LaunchAgents/
   open -e ~/Library/LaunchAgents/goveelights.plist
   ```
2. Load it (it starts now and at every login):

   ```sh
   launchctl load ~/Library/LaunchAgents/goveelights.plist
   ```
3. The first time, macOS asks whether Python may accept incoming network connections:
   allow it, or the lights' replies and your phone can't reach the app. Also keep the Mac
   from sleeping (System Settings → Energy / Battery) or the lights stop at night.

Stop it with `launchctl unload ~/Library/LaunchAgents/goveelights.plist`.

## Any system: by hand

```sh
python3 -m goveelights serve              # Ctrl+C stops it and hands the lights back to the Govee app
python3 -m goveelights serve --port 8090 --config ~/lights/govee_config.json
./start.sh                                # Linux / Pi / macOS: the same as start.bat on Windows
```
