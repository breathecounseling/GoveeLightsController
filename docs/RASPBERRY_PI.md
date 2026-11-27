# Running the lights on a Raspberry Pi (or a small always-on PC)

## Why a little box at home?

The app draws every bulb 20 times a second and sends those frames straight to the light
boxes over your **home network**. That can't go through the internet: Govee's cloud only
accepts about 10 commands a minute per box, nowhere near enough for an effect. So
**something at home has to be switched on and running the app**, and that's where the
schedule (on at sunset, off at night) lives too.

If that something is your laptop, the lights stop when the laptop leaves the house or
goes to sleep. A small box that stays at home fixes that:

```
your phone or laptop, anywhere ──HTTPS──> Cloudflare ──tunnel──> the box at home ──> the lights
```

* The box runs the app and the Cloudflare tunnel, day and night, and starts by itself
  after a power cut.
* Your phone and laptop just open the web page (`https://lights.your-new-domain.com`),
  at home or away. Nothing needs installing on them, and the laptop can go anywhere.
* A Raspberry Pi uses about as much power as a phone charger (3 to 5 watts, a few dollars
  a year).

There are two good choices. Pick one:

| | Raspberry Pi 5 | Used mini PC with Windows |
|---|---|---|
| Cost | about $95 for everything below | about $60 to $120 used |
| Setup | new to you (Linux), but every command is below | exactly what you do on your PC today |
| Power | 3 to 5 W | 10 to 20 W |
| Speed | enough for every preset (see "Will it keep up?") | more than enough |

## Shopping list

**Raspberry Pi route**

* **Raspberry Pi 5, 4 GB.** (2 GB is fine too. A Pi 4 you already own also works; see
  "Will it keep up?".)
* **The official Raspberry Pi 27 W USB-C power supply.** Phone chargers often can't give
  a Pi 5 enough power and it then misbehaves in odd ways.
* **A case with a fan**, for example the official Pi 5 case (it has one built in), or
  the official Active Cooler in any case. It runs all night, every night.
* **A 32 GB microSD card**, a known brand, "A2" or "Application class 2" on the label.
* **An Ethernet cable** long enough to reach your router or a network switch. Wired is
  strongly recommended: Wi-Fi drops cause stutters in the lights.
* If your Windows PC has no SD card slot, a cheap USB card reader.

**Mini PC route (no Linux)**

Any used small office PC is plenty, for example a Dell OptiPlex Micro, Lenovo
ThinkCentre Tiny or HP EliteDesk Mini, with Windows 10 or 11 and an SSD. Then:

1. In its BIOS/UEFI setup, set **"After power loss" (or "AC recovery") to "Power on"**,
   so it comes back after a power cut.
2. Windows: Settings → System → Power → *Sleep* → **Never**.
3. Install Python, copy the project and your settings exactly as in step 5 below (a USB
   stick is fine), then follow [SERVICE.md](SERVICE.md) (Windows) and
   [REMOTE.md](../REMOTE.md) as you did on your PC.
4. Do step 4 below (stop the app on your old PC) first.

The rest of this page is the Raspberry Pi route.

## 1. Put Raspberry Pi OS on the card (on your Windows PC)

1. Install **Raspberry Pi Imager** from raspberrypi.com/software and open it.
2. **Device:** Raspberry Pi 5 (or 4). **Operating system:** *Raspberry Pi OS (other)* →
   **Raspberry Pi OS Lite (64-bit)**. (Lite has no desktop. You don't need one: you
   control it from your PC.) **Storage:** your SD card.
3. Click **Next**. Imager then lets you customise the system before it writes the card.
   Newer versions walk you through it one page at a time; older ones show an
   **Edit settings** button with tabs. Either way, set these:
   * **Hostname** (the computer's name): `lights` (you'll reach it as `lights.local`).
   * **Username and password:** pick your own and write them down.
   * **Wi-Fi** (may be called *Wireless LAN*): your Wi-Fi name and password, and your
     country. (A backup in case the cable isn't plugged in. Use the cable.)
   * **Localisation** (or *Locale settings*): your **time zone**. This matters: the
     schedule uses the Pi's clock.
   * **SSH** (under *Remote access*, or the *Services* tab): turn it on, with password
     authentication.
4. Finish (**Save** / **Write**, then **Yes**) and let it write and check the card (a few
   minutes).

## 2. Start the Pi

1. Put the card in the Pi. Plug the Ethernet cable into your router (the same network as
   the lights), then plug in the power.
2. Wait about 3 minutes the first time.
3. On your Windows PC, open **PowerShell** and connect (use your username):

   ```powershell
   ssh yourname@lights.local
   ```

   Type `yes` the first time, then your password. You're now typing commands on the Pi.
   If `lights.local` isn't found, look up the Pi's address in your router's app (it's
   called `lights`) and use `ssh yourname@192.168.x.y` instead.

4. Bring it up to date and install git (takes a few minutes):

   ```sh
   sudo apt update && sudo apt full-upgrade -y
   sudo apt install -y git
   python3 --version        # 3.11 or newer is good (current Pi OS has 3.13)
   ```

## 3. Get the project onto the Pi

The best way is a fresh `git clone`, so updates later are one command. Use the same
address you cloned from on your PC (on GitHub: the green **Code** button):

```sh
cd ~
git clone https://github.com/YOUR-ACCOUNT/GoveeLightsController.git
cd GoveeLightsController
```

If the repository is private, GitHub asks for a username and a password: the "password"
is a personal access token (GitHub → Settings → Developer settings → Personal access
tokens). Or skip git: on your PC download the ZIP, unzip it, and copy the folder over
from PowerShell with `scp -r .\GoveeLightsController yourname@lights.local:~/`.

## 4. Stop the app on your Windows PC

Only **one** copy of the app may drive the lights at a time. Two copies fight and the
lights flicker. On the PC, in the project folder (administrator PowerShell for the first
line if you installed the service):

```powershell
python -m goveelights uninstall-service
python -m goveelights uninstall-startup
```

Then close any app window that's still open (Ctrl+C in it). If the Cloudflare tunnel
runs on the PC, leave it for now; step 9 moves it.

## 5. Copy your settings from the PC

Your bulb counts, house mapping, schedule, password and own presets live in a few files
that aren't in git. Copy them over: on the **PC**, in PowerShell, in the project folder
(change `yourname`):

```powershell
scp govee_config.json govee_auth.json yourname@lights.local:~/GoveeLightsController/
scp -r user_presets yourname@lights.local:~/GoveeLightsController/
```

If you set up the cloud box (More → Fourth run), copy its key file too:

```powershell
scp govee_cloud.json yourname@lights.local:~/GoveeLightsController/
```

A file that doesn't exist just gives "No such file": that's fine, you never set that up.

**One setting to check:** if you ever pinned the PC's network address (`"bind_ip"` under
`"server"` in `govee_config.json`), that address belongs to the PC, not the Pi. On the
Pi, open the file (`nano govee_config.json`) and set it back to `"bind_ip": ""` (or to
the Pi's own address, from `hostname -I`). The app ignores an address that isn't the
Pi's and says so in the log, but it's tidier to fix it.
(The app makes the password and key files private to your user on the Pi by itself.)

## 6. Check the time zone

Back in the Pi window (`ssh`):

```sh
timedatectl
```

"Time zone" should be yours, and "System clock synchronized: yes". If the zone is
wrong:

```sh
timedatectl list-timezones | grep America      # find yours
sudo timedatectl set-timezone America/Denver   # for example
```

## 7. Try it

```sh
cd ~/GoveeLightsController
./start.sh
```

It prints addresses. On your phone (on home Wi-Fi) open `http://lights.local:8080/` (or
the `http://192.168...:8080/` address it printed). Your house, presets and schedule
should all be there. Play something and check the lights. Then press **Ctrl+C** in the
Pi window to stop it.

## 8. Start it at every boot

```sh
python3 -m goveelights install-service
```

It asks for your password once, prints each step, and starts the app in the background.
From now on the app starts whenever the Pi powers on, with nobody logged in, and
restarts by itself if it ever stops. You can close the PowerShell window.

Handy commands (in an `ssh` window):

| To... | type |
|---|---|
| watch the log live | `journalctl -u goveelights -f` (Ctrl+C stops watching, not the app) |
| see today's log | `journalctl -u goveelights --since today` |
| check it's running | `systemctl status goveelights` |
| restart it | `sudo systemctl restart goveelights` |
| stop it until the next boot | `sudo systemctl stop goveelights` |
| remove the service | `python3 -m goveelights uninstall-service` |
| shut the Pi down safely | `sudo poweroff` (wait 10 s before unplugging) |

## 9. Reach it from anywhere (Cloudflare)

The tunnel now runs on the Pi instead of the PC. Follow [REMOTE.md](../REMOTE.md), with
these differences:

* **Step 1, password:** already done if you copied `govee_auth.json`. Otherwise run
  `python3 -m goveelights set-password` on the Pi.
* **Step 2:** done (step 8 above).
* **Step 3, the tunnel:** in Cloudflare → Zero Trust → Networks → Tunnels (newer
  dashboards may put this under Networks → **Connectors**):
  * If you already made a tunnel for the PC, open it. Otherwise create one.
  * Under the install instructions choose **Debian** and **arm64-bit** (64-bit ARM).
    Copy the commands it shows into the Pi window, one at a time. The last one looks
    like `sudo cloudflared service install eyJ...`. That starts the tunnel at every boot.
  * **Public hostname** (newer dashboards: **Published application routes** → **Add**):
    `lights` on your new domain, Type **HTTP**, URL `localhost:8080`.
  * If the tunnel used to run on the PC, remove it there so Cloudflare only sends visits
    to the Pi: in an administrator PowerShell, `cloudflared.exe service uninstall`. In the
    tunnel's page in Cloudflare, only the Pi should be listed as a connector.
* **Step 4, Access** (the email login in front of the page) stays exactly as it is.

Now open `https://lights.your-new-domain.com` on your phone with Wi-Fi off. On your
laptop, use the same address from anywhere. Bookmark it; the laptop needs nothing else.

## Updating

When there's a new version, in an `ssh` window:

```sh
cd ~/GoveeLightsController
git pull
sudo systemctl restart goveelights
```

Your settings and own presets aren't touched by an update. If you copied the folder
instead of cloning, copy the new version over the old one the same way, but keep your
settings files.

**Backups:** now and then, copy your settings back to the PC (in PowerShell on the PC):

```powershell
scp yourname@lights.local:~/GoveeLightsController/govee_config.json .
scp -r yourname@lights.local:~/GoveeLightsController/user_presets .
```

## Will it keep up?

Every preset was timed on the house layout, with and without the Tip, on the development
computer (a 2.1 GHz server core; one core does the drawing). Results from
`python3 tools/bench.py --tip`:

* Average preset: about 2.5 ms per frame. Slowest (Diamond glitter, Northern lights):
  about 7.6 ms.
* A frame has 50 ms at 20 fps.
* **Estimate**, assuming a Pi 5 is about 2.5 times slower and a Pi 4 about 5 times
  slower than that computer: slowest preset about 19 ms on a Pi 5 (plenty of room) and
  about 38 ms on a Pi 4 (fits, but tight).

That's an estimate, not a measurement. On the Pi itself, see the real numbers with:

```sh
python3 tools/bench.py --pi5 1 --pi4 1
```

If a frame ever takes longer than the computer has (a very heavy show, an old Pi), the
app doesn't stutter: it steps down to 15 frames a second, then 12, and goes back up to
20 when it can. The look and the timing of a show stay the same; only the in-between
frames get fewer. A banner on the Play page says when it's doing this, and More → Output
shows the frame times and has a switch to turn it off.

## If something's wrong

* **The page won't load at home.** Is the app running? `systemctl status goveelights`.
  Try the Pi's number address (`http://192.168.x.y:8080/`) instead of `lights.local`.
* **It can't find the lights.** The Pi must be on the same network as the lights (the
  cable to the same router). LAN Control must be on for each box in the Govee app. If
  the Pi has both a cable and Wi-Fi connected and still finds nothing, tell it which
  address to use: in `govee_config.json` (`nano govee_config.json`), under `"server"`,
  set `"bind_ip": "192.168.x.y"` (the Pi's address on the lights' network, shown by
  `hostname -I`), save, then `sudo systemctl restart goveelights`.
* **It found the lights over the cable but not over Wi-Fi (or the other way round).**
  The app checks which networks the Pi is on once, when it starts. If one connected
  later (say the Wi-Fi came up after the app), restart it so it looks again:
  `sudo systemctl restart goveelights`.
* **The schedule is an hour off, or starts at the wrong time.** Time zone: step 6.
* **The lights flicker or fight.** Another copy of the app is running somewhere (your
  PC). Stop it (step 4).
* **After a power cut.** Nothing to do: the Pi starts, the app starts, and the schedule
  carries on. It may take a minute or two.
* **The log says "Cannot bind UDP port 4002".** Something else on the Pi listens for
  Govee lights (Home Assistant, Homebridge, or a second copy of the app started by hand
  while the service runs). Stop that one.
