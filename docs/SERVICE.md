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

1. Copy the example unit and edit `User`, `WorkingDirectory` and the paths in
   `ExecStart`:

   ```sh
   sudo cp docs/examples/goveelights.service /etc/systemd/system/
   sudo nano /etc/systemd/system/goveelights.service
   ```
2. Start it and enable it at boot:

   ```sh
   sudo systemctl daemon-reload
   sudo systemctl enable --now goveelights
   journalctl -u goveelights -f        # watch it start
   ```
3. If a firewall is on (`ufw`), allow the lights' replies and the web page from your
   home network: `sudo ufw allow 4002/udp` and `sudo ufw allow 8080/tcp`.

## macOS (launchd)

1. Copy the example agent and set your folder and Python path in it
   (`which python3` shows the path):

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
```
