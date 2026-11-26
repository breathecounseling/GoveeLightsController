# Security

GoveeLightsController controls lights on your home network. Treat its web app like any
other device admin page.

## The model

* **Home network:** by default no login is needed on home Wi-Fi. "Home" means the request
  comes from a private address *and* the address bar shows the computer's IP address or a
  home name (`localhost`, `DESKTOP-1234`, `pc.local`, `pc.lan`, or names you list in the
  config under `"server": {"hosts": [...]}`), so a web page that re-points its own domain
  at your computer gets no free pass. You can require the login on home Wi-Fi too
  (More → Security).
* **Remote access is off until you set a password** (More → Security, or
  `python -m goveelights set-password`). Requests arriving through a proxy or tunnel
  (they carry `X-Forwarded-For`) always need the password, even from the same computer.
* Passwords are stored as PBKDF2-SHA256 hashes and sessions as SHA-256 hashes in
  `govee_auth.json` (kept out of git). Sessions are HttpOnly, SameSite cookies.
* 5 wrong passwords from one address lock it out for 10 minutes; when many addresses
  guess at once every try is slowed down instead, so strangers can't lock you out. That
  also means the app alone can't stop slow guessing from many addresses: **put Cloudflare
  Access (or Tailscale) in front of it**, as [REMOTE.md](REMOTE.md) describes.
* Every change request needs a header other websites can't send (CSRF protection); pages
  send a strict Content-Security-Policy and refuse to be framed.
* The diagnostics tool (`python -m goveelights diag`) has no password, so it only answers
  on the home network and refuses anything that comes through a proxy.
* The visitor page (`/visit/<secret>`) is public by design for whoever has the link; it
  can only pick from presets you approve, is rate limited, and the link can be changed.
* The lights' LAN protocol itself has no authentication: anything on your network can
  control them. Keep the lights on a network you trust.

## Do not

* Forward a router port to the app. Use a tunnel with an identity check in front (REMOTE.md).
* Share `govee_config.json` or `govee_auth.json` (location, device ids, login secrets).

## Reporting a vulnerability

Please report security problems privately, using GitHub's *Report a vulnerability*
(Security tab of the repository) rather than a public issue. Include what an attacker
needs (network position, settings) and what they can do. You should get an answer within
a couple of weeks; this is a volunteer project.
