# Control the lights from anywhere

**Short answer:** something at home has to stay switched on, because the lights get
their colours over your home Wi-Fi, 20 times a second, from the computer running this
app. That computer does **not** have to be your laptop. Put the app on a small box that
lives at home and never leaves (a Raspberry Pi or a cheap mini PC), and your phone and
laptop just open the app's web page, from home or from anywhere.

Pick one setup:

| | Setup | Lights work while your laptop is away? | Use it from anywhere? | Cost | Best for |
|---|---|---|---|---|---|
| **A** | **Always-on box at home + Cloudflare Tunnel** (recommended) | Yes | Yes, at `https://lights.yourdomain.com` on any phone or browser | Box (~$60–150 once) + domain (~$12 a year) | Most people. The lights run themselves, you control them from anywhere |
| **B** | Always-on box (or a PC that stays home) + **Tailscale** | Yes | Yes, on your own devices with the Tailscale app | Free (plus the box) | Only you (and family you add) use it, no domain wanted |
| **C** | The laptop you carry runs the app | **No**: the show stops when the laptop leaves home | No | Free | Trying things out. See [why](#c-why-the-laptop-you-carry-cant-run-the-show) |

Wherever this guide says `yourdomain.com`, use the domain you buy. `lights.yourdomain.com`
is just a suggestion for the address.

---

## A. Always-on box at home + Cloudflare Tunnel (recommended)

```
phone (anywhere) ──https──> Cloudflare (login) ──tunnel──> box at home ──Wi-Fi──> lights
```

The box makes an *outgoing* connection to Cloudflare, so **no port is opened on your
router**. Cloudflare gives your address real HTTPS (so the microphone *Listen* feature
works) and asks for a login (an emailed code) **before** anyone reaches your box. The
app's own password is a second lock behind that. Cloudflare's free plan covers all of it.

### A1. Get the box running the app

* **Raspberry Pi 5 or Pi 4** (2 GB is plenty): follow [docs/RASPBERRY_PI.md](docs/RASPBERRY_PI.md).
  It ends with the app starting by itself at power-on.
* **A used mini PC with Windows** (or an old PC that stays home): install Python and the
  app as in the [README](README.md), then make it start at boot as in
  [docs/SERVICE.md](docs/SERVICE.md) (`python -m goveelights install-service` in an
  administrator PowerShell). Set *Settings → System → Power → put my device to sleep* to
  **Never** (the screen may turn off).

Either way, plug the box in near the router (a network cable is best, Wi-Fi is fine), then
check it from your phone on home Wi-Fi: open `http://<the box's address>:8080`. If the
lights respond, the box is ready. Copy your settings over if you set things up on another
computer first: `govee_config.json` in the project folder holds them.

### A2. Set the app password (required)

Remote requests are refused until a password exists. On home Wi-Fi open the app →
**More → Security → Set password** (10+ characters), or on the box run:

```sh
python -m goveelights set-password
```

### A3. Buy a domain on Cloudflare

You don't need to touch your existing website or wherever its DNS lives. A separate,
cheap domain just for the lights keeps everything simple.

1. Make a free account at **dash.cloudflare.com** (or sign in).
2. In the dashboard: **Domain Registration → Register Domains**. Search for a name,
   pick one (most `.com` names are about $10–12 a year, sold at cost), and pay.
3. That's it: a domain bought this way already uses Cloudflare for DNS. It can take a few
   minutes to show as **Active** under *Websites* (or *Domains*).

Turn on auto-renew (it's on by default) so the address doesn't lapse next year.

### A4. Turn on Zero Trust (free)

1. In the dashboard's left menu open **Zero Trust**.
2. The first time, it asks for a **team name** (any word, e.g. `myhouse`; it becomes
   `myhouse.cloudflareaccess.com`, the login page) and a plan: choose **Free** ($0, up to
   50 people). It may ask for a card even for the free plan; you won't be charged.

### A5. Create the tunnel

1. Zero Trust → **Networks → Tunnels** (newer dashboards: **Networks → Connectors →
   Cloudflare Tunnels**) → **Create a tunnel** → **Cloudflared** → name it `house-lights`
   → Save.
2. The next screen shows how to install the connector (`cloudflared`). Choose the box's
   system:

   **Windows box:** pick *Windows*, download and run the installer it links, then copy the
   command it shows into an **administrator** PowerShell. It looks like:

   ```powershell
   cloudflared.exe service install eyJhIjoi...long-token...
   ```

   **Raspberry Pi / Linux box:** pick *Debian* and the box's type (`arm64` for a Pi 4/5
   running 64-bit Raspberry Pi OS, `64-bit` for a mini PC). Copy the commands it shows into
   a terminal on the box. They add Cloudflare's software source and install it, and look
   like this (use the dashboard's version if it differs):

   ```sh
   sudo mkdir -p --mode=0755 /usr/share/keyrings
   curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
   echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main' | sudo tee /etc/apt/sources.list.d/cloudflared.list
   sudo apt-get update && sudo apt-get install cloudflared
   sudo cloudflared service install eyJhIjoi...long-token...
   ```

   (Instead of the software source you can download the `.deb` file for your box from
   Cloudflare's *cloudflared* releases page and install it with
   `sudo apt install ./cloudflared-linux-arm64.deb`, then run the `service install` line.)

   The token is a password for the tunnel: don't share it or post it anywhere.
   `service install` makes `cloudflared` start by itself at every boot.
3. Back in the dashboard the connector shows **Connected** (or *Healthy*) within a minute.
   Click **Next**.
4. **Public hostname** (newer dashboards: **Published application routes** → *Add*):
   * Subdomain: `lights`
   * Domain: `yourdomain.com` (pick the one you bought)
   * Path: leave empty
   * Service **Type**: `HTTP`, **URL**: `localhost:8080`

   Save. Cloudflare creates the DNS record for `lights.yourdomain.com` itself.

### A6. Lock it with Cloudflare Access (don't skip this)

Zero Trust → **Access → Applications** (newer dashboards: **Access controls →
Applications**) → **Add an application** → **Self-hosted**:

* Application name: `House lights`
* Session duration: e.g. **1 month** (how often you re-enter an emailed code)
* Public hostname / Application domain: subdomain `lights`, domain `yourdomain.com`, path empty
* Add a **policy**: name `Me`, Action **Allow**, *Include* → **Emails** → your email
  address (add family members' addresses too, one per line)
* Login methods: **One-time PIN** (on by default: Cloudflare emails a 6-digit code).
  Google sign-in also works if you add it under *Settings → Authentication*.

Save.

### A7. Let neighbours reach the visitor page (optional)

The visitor page (Shows → Live → Visitor page) lives at `/visit/<secret>` and must be
reachable **without** the Cloudflare login, while everything else stays locked:

1. Access → Applications → **Add an application** → **Self-hosted**, name `Visitor page`.
2. Add the hostname `lights.yourdomain.com` with path `visit`, and a second hostname entry
   (*+ Add public hostname*) `lights.yourdomain.com` with path `api/visit`.
3. Policy: name `Everyone`, Action **Bypass**, *Include* → **Everyone**.

Cloudflare uses the most specific path, so these two paths skip the login and the rest of
the site keeps it. The app still guards them: a wrong secret gets "not found", only the
presets you allow can be picked, each person gets one pick per cooldown, the queue is
capped, and nothing else is reachable. "New link" in the app retires an old QR sign.

### A8. Test it away from home

1. On your phone **turn Wi-Fi off** (use mobile data), and open
   `https://lights.yourdomain.com`.
2. You should see Cloudflare's page asking for your email → a code arrives by email →
   then the app's own sign-in → then the app.
3. **More → Use it away from home** should say *through your Cloudflare tunnel*.
4. Make it an app on your home screen (see [Install it like an app](#install-it-like-an-app)).

If you get a Cloudflare error page instead (502, 1033): the box is off, the app isn't
running, or `cloudflared` isn't connected (step A5.3 shows its state).

---

## B. Tailscale (private, no domain)

Tailscale makes a private network between your own devices, wherever they are. Nothing is
public: only devices signed in to your Tailscale account can reach the app.

1. Make a free account at **tailscale.com**. Install Tailscale on the **box at home** and on
   your **phone** (and laptop), and sign in to the same account on each.
   * Windows box: the installer from tailscale.com.
   * Raspberry Pi / Linux: `curl -fsSL https://tailscale.com/install.sh | sh` then
     `sudo tailscale up` (it prints a link to sign in).
2. Set the app password (step A2). Requests that come over Tailscale always need it.
3. From your phone, anywhere, with Tailscale switched on: open `http://<box-name>:8080`
   (the box's name is in the Tailscale app's device list, e.g. `http://raspberrypi:8080`).

**Get https, so Listen (the microphone) works.** Browsers only allow the microphone on a
secure (https) page. Tailscale can give the box a secure address:

1. In the Tailscale admin console → **DNS**: make sure **MagicDNS** is on, and turn on
   **HTTPS Certificates**.
2. On the box run (Linux: with `sudo`; Windows: in PowerShell):

   ```sh
   tailscale serve --bg 8080
   ```

   It prints an address like `https://raspberrypi.tail1234.ts.net`. Open that on your
   phone (Tailscale on). It keeps working after a reboot. `tailscale serve reset` undoes it.

Only your own Tailscale devices can open these addresses, so you can't share the visitor
page with neighbours this way (use setup A for that).

---

## C. Why the laptop you carry can't run the show

The app doesn't send "turn red" to the lights once. It draws every bulb, 20 times a second,
and sends each picture straight to the four light boxes **over your home Wi-Fi**. That
only works from inside your home network: when the laptop leaves the house, the pictures
stop.

* **Govee's own app and cloud can't take over the show.** From outside the house they can
  only switch the lights on and off, set one colour or brightness, or start one of Govee's
  built-in scenes, and Govee's cloud allows only about 10 commands a minute per box. No
  per-bulb animation, no schedule from this app.
* **Tailscale or a tunnel doesn't fix it either.** They let you *reach* the computer that
  runs the app. If that computer is the laptop in your bag, there's nothing at home left
  to send the pictures.

**What the lights do when the app goes away** (laptop leaves, goes to sleep, or the app
quits): they stop moving and hold the last picture, then about **30–45 seconds** later
the boxes give up on live mode and go back to whatever they were set to in the Govee
app (their last scene or colour, or off). The schedule in this app (on at sunset, off at
bedtime, holiday shows) doesn't run until the app is back. If you sometimes have to rely on
a laptop, set an on/off timer in the Govee app as a backup.

That's why setup A puts the app on a small box that never leaves home.

---

## Install it like an app

The web page installs as an app with its own icon (a house with a string of lights) and
opens full screen, without the browser's address bar.

* **iPhone / iPad:** open the address in **Safari** → Share button → **Add to Home Screen**.
* **Android:** open it in **Chrome** → ⋮ menu → **Install app** (or *Add to Home screen*).
* **Windows / Mac:** in Chrome or Edge, the install icon at the right end of the address bar.

Install it from the address you'll use away from home (`https://lights.yourdomain.com` or
the Tailscale `https://…ts.net` one). An app installed from the home-only address
(`http://192.168…`) stops working when you leave.

Notes for the curious:

* The app's manifest and icons are served without a login: they contain only the app's
  name, colours and the house picture. Every page and every `/api` request still needs the
  login. The manifest is fetched with your login cookies, so installing works behind
  Cloudflare Access. If the installed icon ever shows a plain letter instead of the house,
  add `manifest.webmanifest` and `icons` as two more paths in the visitor *Bypass*
  application (step A7); they hold nothing private.
* There is **no "service worker"** (offline cache). The app is only useful while it can
  reach the box, and a cache could show you an old screen or old light settings. Every page
  and every reply is loaded fresh each time.
* The **visitor page** is not an installable app on purpose: its link changes whenever you
  press "New link", and neighbours only need it for a minute. It still gets the house icon
  if someone bookmarks it.

---

## How the app protects itself

* Anything arriving through the tunnel (or `tailscale serve`) carries proxy headers, so
  the app **always** asks for the password, even though the connector runs on the same box.
* With no password set, tunnel requests are refused outright.
* Passwords are stored as PBKDF2-SHA256 hashes and sessions as SHA-256 hashes, in
  `govee_auth.json`, which is kept out of git.
* 5 wrong passwords from one address lock it out for 10 minutes. When many addresses
  guess at once, every try is slowed down instead of blocked, so strangers can't lock
  *you* out. That also means the app alone can't stop someone with thousands of
  addresses from guessing slowly: **Cloudflare Access (step A6) is what really keeps
  them out**, so don't skip it.
* "No login on home Wi-Fi" only counts when the address bar shows the box's IP address
  or a home name (`localhost`, `DESKTOP-1234`, `raspberrypi`, `pc.local`, `pc.lan`). A web
  page that re-points its own domain at your box gets no free pass. If you reach the box at
  home by some other name, list it in the config under `"server": {"hosts": ["name"]}`.
  Tailscale addresses (`100.x.y.z`) are not home Wi-Fi: they always need the password.
* The stand-alone diagnostic tool (`python -m goveelights diag`) has no password, so it
  only answers on home Wi-Fi and refuses anything that comes through the tunnel.
* Every change request needs a header other websites can't send (CSRF protection), and
  pages refuse to be shown inside another site's frame.
* **More → Security:** change the password, *Sign out all devices*, or require login
  even on home Wi-Fi. **More → Use it away from home** shows how you're connected right
  now (home Wi-Fi, tunnel or Tailscale), never any secrets.
* **Never forward a router port** to the app. The tunnel and Tailscale both work without one.

If you ever put a different reverse proxy (nginx, Caddy…) in front of the app, make sure it
sends `X-Forwarded-For`. All common ones do by default. That header is how the app tells
remote requests apart from home ones.
