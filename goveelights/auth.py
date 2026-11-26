"""Login for remote access.

Rules (default deny):
  * Requests that arrive through a proxy or tunnel (they carry headers such as
    Cf-Connecting-IP / X-Forwarded-For) always need a login.
  * Direct requests from your home network (private addresses, or this PC
    itself) may skip the login ("lan_without_login", default on), but only when the
    Host header names the PC by an address or home-network name, so a web page that
    re-points its own domain at the PC (DNS rebinding) gets no such trust.
  * With no password set, remote requests are refused outright.
  * Every POST must carry the header X-Requested-With: lights. Browsers will
    not let another website add that header, which blocks cross-site request
    forgery even from inside the home network.

Secrets live in govee_auth.json (next to the config, not in it, so the config
can be shared or backed up safely). Passwords are PBKDF2-SHA256 hashed;
session tokens are stored only as SHA-256 hashes.
"""
from __future__ import annotations

import functools
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import socket
import threading
import time
from collections import deque
from pathlib import Path

from .config import write_json

PROXY_HEADERS = ("cf-connecting-ip", "x-forwarded-for", "forwarded", "x-real-ip", "cf-ray")
COOKIE = "lights_session"
ITER = 240_000
PRIVATE = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8",
                                              "fd00::/8", "::1/128")]


# Names a stranger on the internet can't point at this PC (DNS rebinding needs a domain
# the attacker controls, so it never arrives with one of these in the Host header).
LOCAL_SUFFIXES = (".local", ".lan", ".home", ".home.arpa", ".internal", ".localdomain")


def local_host(host: str | None, extra=()) -> bool:
    """True if a Host header names this PC the way someone at home would: an IP address,
    a bare machine name (localhost, DESKTOP-1234), a home-network name such as pc.local,
    or a name listed in extra. A web page whose own domain has been re-pointed at the PC
    (DNS rebinding) carries its domain here instead, so it gets no LAN trust."""
    if host is None:                      # not a browser (every browser sends Host)
        return True
    h = host.strip().lower()
    if h.startswith("["):                 # [::1]:8080
        h = h[1:h.find("]")] if "]" in h else h[1:]
    elif h.count(":") == 1:
        h = h.rsplit(":", 1)[0]
    h = h.rstrip(".")
    if not h:
        return False
    try:
        ipaddress.ip_address(h)
        return True
    except ValueError:
        pass
    if "." not in h or h.endswith(LOCAL_SUFFIXES):
        return True
    names = {str(x).strip().lower().rstrip(".") for x in extra}
    return h in names or h == _fqdn()


@functools.lru_cache(maxsize=1)
def _fqdn() -> str:
    try:
        return socket.getfqdn().lower().rstrip(".")
    except OSError:
        return ""


def _h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Auth:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self._mtime = None
        self.data = {}
        self.fails: dict[str, deque] = {}
        self.all_fails: deque = deque()
        self.hosts: list[str] = []        # extra Host names that count as home (server.hosts)
        self._load()

    # ------------------------------------------------------------ storage
    def _load(self):
        try:
            m = self.path.stat().st_mtime
        except OSError:
            self.data = {"password": None, "sessions": {}, "lan_without_login": True, "session_days": 30}
            self._mtime = None
            return
        if m != self._mtime:
            with open(self.path, encoding="utf-8-sig") as f:
                self.data = json.load(f)
            self.data.setdefault("sessions", {})
            self.data.setdefault("lan_without_login", True)
            self.data.setdefault("session_days", 30)
            self._mtime = m

    def _save(self):
        write_json(self.path, self.data, mode=0o600, indent=1)
        self._mtime = self.path.stat().st_mtime

    def refresh(self):
        with self.lock:
            self._load()          # picks up a password set from the command line

    # ----------------------------------------------------------- password
    @property
    def password_set(self) -> bool:
        return bool(self.data.get("password"))

    def set_password(self, pw: str):
        if len(pw) < 10:
            raise ValueError("use at least 10 characters")
        salt = secrets.token_bytes(16)
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, ITER)
        with self.lock:
            self._load()
            self.data["password"] = {"salt": salt.hex(), "hash": dk.hex(), "iter": ITER}
            self.data["sessions"] = {}            # a new password logs everyone out
            self._save()

    def check_password(self, pw: str) -> bool:
        p = self.data.get("password")
        if not p:
            return False
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(p["salt"]), int(p["iter"]))
        return hmac.compare_digest(dk.hex(), p["hash"])

    # ----------------------------------------------------------- sessions
    def new_session(self) -> tuple[str, int]:
        token = secrets.token_urlsafe(32)
        ttl = int(self.data.get("session_days", 30)) * 86400
        with self.lock:
            self._load()
            now = time.time()
            s = {k: v for k, v in self.data["sessions"].items() if v > now}
            if len(s) >= 50:                      # keep the newest 49
                s = dict(sorted(s.items(), key=lambda kv: kv[1])[-49:])
            s[_h(token)] = now + ttl
            self.data["sessions"] = s
            self._save()
        return token, ttl

    def session_ok(self, token: str | None) -> bool:
        if not token:
            return False
        exp = self.data.get("sessions", {}).get(_h(token))
        return bool(exp and exp > time.time())

    def end_session(self, token: str | None):
        if token:
            with self.lock:
                self._load()
                self.data["sessions"].pop(_h(token), None)
                self._save()

    def revoke_all(self):
        with self.lock:
            self._load()
            self.data["sessions"] = {}
            self._save()

    # ------------------------------------------------------------ requests
    @staticmethod
    def client_ip(handler) -> str:
        """The real visitor address. Cf-Connecting-IP is only believed when the request came
        from this PC itself (the Cloudflare tunnel); anyone else could just make it up."""
        peer = handler.client_address[0]
        cf = (handler.headers.get("Cf-Connecting-IP") or "").strip()
        if cf:
            try:
                if ipaddress.ip_address(peer).is_loopback:
                    return str(ipaddress.ip_address(cf))
            except ValueError:
                pass
        return peer

    @staticmethod
    def rate_key(ip: str) -> str:
        """Key for per-client limits. One IPv6 user typically holds a whole /64, so
        rotating the low bits must not count as a new client."""
        try:
            a = ipaddress.ip_address(ip)
        except ValueError:
            return ip
        if a.version == 6 and a.ipv4_mapped:
            return str(a.ipv4_mapped)
        if a.version == 6:
            return str(ipaddress.ip_network(f"{a}/64", strict=False))
        return str(a)

    @staticmethod
    def proxied(handler) -> bool:
        """True if this request, or any earlier request on the same connection, came
        through a proxy/tunnel. A connection that has carried a tunnelled request is
        never trusted again, so bytes smuggled inside it can't pass as a LAN request."""
        hit = any(h in handler.headers for h in PROXY_HEADERS) or \
            any(k.lower() in PROXY_HEADERS for k in handler.headers.keys())
        if hit:
            try:
                handler._via_proxy = True
            except AttributeError:
                pass
        return hit or bool(getattr(handler, "_via_proxy", False))

    def trusted(self, handler) -> bool:
        """Direct request from the home network (never through a proxy/tunnel)."""
        return bool(self.data.get("lan_without_login", True)) and self.direct_lan(handler, self.hosts)

    @classmethod
    def direct_lan(cls, handler, hosts=()) -> bool:
        """Came straight from a home-network address, not through a proxy, addressed to
        this PC by a home-network name (see local_host)."""
        if cls.proxied(handler) or not local_host(handler.headers.get("Host"), hosts):
            return False
        try:
            ip = ipaddress.ip_address(handler.client_address[0])
        except ValueError:
            return False
        if ip.version == 6 and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        return any(ip in n for n in PRIVATE)

    @staticmethod
    def cookie(handler) -> str | None:
        raw = handler.headers.get("Cookie", "")
        for part in raw.split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE:
                return v
        return None

    def authorized(self, handler) -> bool:
        return self.trusted(handler) or self.session_ok(self.cookie(handler))

    # ---------------------------------------------------------- lockout
    def _prune(self, now: float):
        while self.all_fails and self.all_fails[0] < now - 600:
            self.all_fails.popleft()
        for k in [k for k, q in self.fails.items() if not q or q[-1] < now - 600]:
            del self.fails[k]

    GLOBAL_SLOW = 30          # failures from everyone in 10 minutes before each try is slowed
    SLOW_DELAY = 3.0

    def allow_attempt(self, ip: str) -> bool:
        """Per-client limit only: 5 failures per 10 minutes. There is deliberately no hard
        global limit, or a handful of addresses could lock the owner out (see begin_attempt)."""
        now = time.time()
        with self.lock:
            self._prune(now)
            q = self.fails.get(self.rate_key(ip))
            while q and q[0] < now - 600:
                q.popleft()
            return len(q or ()) < 5

    def begin_attempt(self, ip: str):
        """Check and count a password try in one step, so a parallel burst can't have many
        tries checked before the first failure is recorded. Returns None when the client is
        over its limit, else (stamp, delay): wait delay seconds before checking the password
        (non-zero while many failures arrive from everywhere), and pass stamp to
        attempt_ok() if the password was right."""
        with self.lock:
            if not self.allow_attempt(ip):
                return None
            now = time.time()
            delay = self.SLOW_DELAY if len(self.all_fails) >= self.GLOBAL_SLOW else 0.0
            self.record_fail(ip, now)
            return now, delay

    def attempt_ok(self, ip: str, stamp: float):
        """The try begun at stamp was right: it no longer counts as a failure."""
        with self.lock:
            for q in (self.fails.get(self.rate_key(ip)), self.all_fails):
                try:
                    q.remove(stamp)
                except (AttributeError, ValueError):
                    pass

    def record_fail(self, ip: str, now: float | None = None):
        now = time.time() if now is None else now
        with self.lock:
            self.fails.setdefault(self.rate_key(ip), deque()).append(now)
            self.all_fails.append(now)
            if len(self.fails) > 2000:
                self._prune(now)

    def status(self, handler) -> dict:
        if not self.authorized(handler):          # strangers learn only what the login page needs
            return {"password_set": self.password_set, "trusted": False, "logged_in": False}
        return {"password_set": self.password_set, "trusted": self.trusted(handler),
                "logged_in": self.session_ok(self.cookie(handler)), "proxied": self.proxied(handler),
                "lan_without_login": bool(self.data.get("lan_without_login", True)),
                "sessions": len([v for v in self.data.get("sessions", {}).values() if v > time.time()])}
