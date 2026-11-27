# Reaching the lights from anywhere (your own subdomain)

The app runs on the home PC. To use it away from home at something like
`https://lights.yourdomain.com` **without opening any port on your router**, use a
**Cloudflare Tunnel**. The PC makes an outbound connection to Cloudflare, and Cloudflare
serves your subdomain with HTTPS. Then put **Cloudflare Access** in front of it, so
visitors must prove who they are at Cloudflare *before* they reach the PC. The app's own
password is a second lock behind that.

```
phone ──HTTPS──> Cloudflare (Access login) ──tunnel──> cloudflared on the PC ──> app (password) ──> lights
```

Cloudflare's free plan covers all of this.

The computer running the app and the tunnel has to stay at home and switched on. If
yours is a laptop that travels, run both on a small always-on box instead (a Raspberry
Pi or a used mini PC): [docs/RASPBERRY_PI.md](docs/RASPBERRY_PI.md). Your laptop then
opens the page like your phone does, from anywhere.

## 0. Before you start

* Your domain's DNS must be on Cloudflare. If your website's DNS is somewhere else,
  add the domain to Cloudflare (free) and switch the nameservers at your registrar.
  Cloudflare copies your existing records, so the website keeps working. If you'd
  rather not move DNS, see "Alternative: Tailscale" at the end.
* Keep the PC awake: Settings → System → Power → *When plugged in, put my device to
  sleep after* → **Never**. (Turning the screen off is fine.)

## 1. Set the app password (required)

Remote requests are refused until a password exists. On home Wi-Fi open the app →
**More → Security → Set password** (10+ characters), or on the PC:

```powershell
python -m goveelights set-password
```

## 2. Keep the app running from boot

In an **administrator** PowerShell, in the project folder:

```powershell
python -m goveelights install-service      # starts 30 s after boot, no login needed, auto-restart
schtasks /Run /TN GoveeLightsController    # start it now
```

The boot task runs `pythonw.exe` with no window, so Windows won't show a firewall prompt
for it. Allow the app's ports explicitly (same admin PowerShell). The UDP rule lets the
lights' replies in, and the TCP rule lets your phone reach the page on home Wi-Fi:

```powershell
New-NetFirewallRule -DisplayName "Govee lights (web)" -Direction Inbound -Protocol TCP -LocalPort 8080 -Action Allow -Profile Any
New-NetFirewallRule -DisplayName "Govee lights (discovery)" -Direction Inbound -Protocol UDP -LocalPort 4002 -Action Allow -Profile Any
```

(If you used `install-startup` before, remove it: `python -m goveelights uninstall-startup`.)
Check `goveelights.log` in the project folder if anything seems off.

## 3. Create the tunnel

1. Cloudflare dashboard → **Zero Trust** → **Networks → Tunnels** → **Create a tunnel** →
   *Cloudflared* → name it `house-lights`.
2. Choose **Windows**. Download and install `cloudflared` as shown, then run the
   `cloudflared.exe service install <token>` command it gives you, in an
   **administrator** PowerShell. It runs as a Windows service and starts at boot.
3. **Public hostname:** subdomain `lights`, your domain, **Type** `HTTP`, **URL**
   `localhost:8080`. Save.

## 4. Lock it with Cloudflare Access

Zero Trust → **Access → Applications → Add an application → Self-hosted**:

* Application domain: `lights.yourdomain.com`
* Session duration: e.g. 1 month
* Policy: **Allow**, *Include* → **Emails** → your email address(es)
* Login method: **One-time PIN** (default; Cloudflare emails you a code). Google login
  also works if you add it.

## 4b. Let neighbours reach the visitor page (optional)

The visitor page (Shows → Live → Visitor page) lives at `/visit/<secret>` and must be
reachable **without** the Cloudflare login, while everything else stays locked:

1. Zero Trust → **Access → Applications → Add an application → Self-hosted**.
2. Application domain: `lights.yourdomain.com`, path `visit`. Add a second entry with
   path `api/visit`.
3. Policy: **Bypass**, *Include* → **Everyone**.

Cloudflare uses the most specific path, so these two paths skip the login and the rest of
the site keeps it. The app still guards them: a wrong secret gets "not found", only the
presets you allow can be picked, each person gets one pick per cooldown, the queue is
capped, and nothing else is reachable. "New link" in the app retires an old QR sign.

## 5. Test

Turn off Wi-Fi on your phone (use mobile data) and open `https://lights.yourdomain.com`.
You should see Cloudflare's login, then the app's sign-in, then the app. Add it to your
home screen.

## How the app protects itself

* Anything arriving through the tunnel carries proxy headers, so the app **always**
  asks for the password, even though cloudflared connects from the same PC.
* With no password set, tunnel requests are refused outright.
* Passwords are stored as PBKDF2-SHA256 hashes and sessions as SHA-256 hashes, in
  `govee_auth.json`, which is kept out of git.
* 5 wrong passwords from one address lock it out for 10 minutes. When many addresses
  guess at once, every try is slowed down instead of blocked, so strangers can't lock
  *you* out. That also means the app alone can't stop someone with thousands of
  addresses from guessing slowly: **Cloudflare Access (step 4) is what really keeps
  them out**, so don't skip it.
* "No login on home Wi-Fi" only counts when the address bar shows the PC's IP address
  or a home name (`localhost`, `DESKTOP-1234`, `pc.local`, `pc.lan`). A web page that
  re-points its own domain at your PC gets no free pass. If you reach the PC at home by
  some other name, list it in the config under `"server": {"hosts": ["name"]}`.
* The stand-alone diagnostic tool (`python -m goveelights diag`) has no password, so it
  only answers on home Wi-Fi and refuses anything that comes through the tunnel.
* Every change request needs a header other websites can't send (CSRF protection), and
  pages refuse to be framed.
* **More → Security:** change the password, *Sign out all devices*, or require login
  even on home Wi-Fi.

If you ever put a different reverse proxy (nginx, Caddy…) on this PC, make sure it sends
`X-Forwarded-For`. All common ones do by default. That header is how the app tells
remote requests apart from home ones.

## Alternative: Tailscale (no domain needed)

Install Tailscale on the PC and your phone. The app is then reachable at
`http://<pc-name>:8080` from your phone anywhere, privately, with nothing exposed to the
internet. You don't get a public subdomain, but it's the simplest secure option.
