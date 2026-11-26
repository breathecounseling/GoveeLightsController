"""EXPERIMENTAL: switching clips on hologram fans from the PC. Nothing here talks to a fan.

Consumer hologram fans are filled with clips through their own phone app (or an SD
card) and then play them on their own. None of them takes a live picture from a PC
(the only "live" option is an HDMI fan wall such as AIHOLO's, a different product). A
few models have community, reverse-engineered LAN protocols for picking which stored
clip plays (see docs/HOLOGRAM_FANS.md for how to find them). Those were not verified here - there
was no fan to test against - so this module only defines the shape a driver would
take, plus the hook a show cue or schedule rule could call later:

    hub = FanHub()                       # no drivers: every call is a logged no-op
    hub.switch("sleigh")                 # -> [{"driver": ..., "ok": False, "note": ...}]

A real driver subclasses FanDriver, is tested against real hardware, and is added with
hub.add(driver). Until then the honest answer is "load the clip in the fan's app".
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

log = logging.getLogger("goveelights")


@dataclass
class Fan:
    """One fan a driver found."""
    id: str
    name: str = ""
    address: str = ""                     # IP or similar, if the fan is on the network
    model: str = ""
    extra: dict = field(default_factory=dict)


class FanDriver:
    """What a hologram-fan driver provides. Every method may raise NotImplementedError;
    callers treat that as "this fan can't do that from here". Drivers must be safe to
    call from the lights' engine thread: short timeouts, no blocking for seconds."""

    name = "base"
    experimental = True

    def discover(self, timeout: float = 2.0) -> list[Fan]:
        """Fans of this kind on the network."""
        raise NotImplementedError

    def list_clips(self, fan: Fan) -> list[str]:
        """Names (or numbers) of the clips stored on the fan, in its own order."""
        raise NotImplementedError

    def play(self, fan: Fan, clip: str) -> None:
        """Switch the fan to a stored clip (by the name/number list_clips gives)."""
        raise NotImplementedError

    def power(self, fan: Fan, on: bool) -> None:
        """Spin the fan up (and light it) or stop it."""
        raise NotImplementedError


class ManualDriver(FanDriver):
    """The default: the fans are run by hand. It never touches the network and never
    pretends: play() just says which clip to pick in the fan's app."""

    name = "manual"
    experimental = False

    def discover(self, timeout: float = 2.0) -> list[Fan]:
        return []

    def list_clips(self, fan: Fan) -> list[str]:
        return []

    def play(self, fan: Fan, clip: str) -> None:
        raise NotImplementedError(f"switch the fans to '{clip}' in the fan's own app")

    def power(self, fan: Fan, on: bool) -> None:
        raise NotImplementedError("use the fan's remote, app or a smart plug")


class LanProtocolStub(FanDriver):
    """Placeholder for the community LAN protocols (typically a discovery broadcast plus
    a TCP command port, or a port on the fan's own Wi-Fi access point). Deliberately NOT
    implemented: the protocols are reverse-engineered, differ between models and firmware,
    and could not be checked without a fan. Port a project that matches your model (see
    docs/HOLOGRAM_FANS.md) here, test it on your fan, then register it with FanHub.add."""

    name = "lan-stub"

    def __init__(self, model: str = ""):
        self.model = model

    def _no(self, what: str):
        raise NotImplementedError(f"{what}: the experimental LAN protocol isn't implemented (see docs/HOLOGRAM_FANS.md)")

    def discover(self, timeout: float = 2.0) -> list[Fan]:
        self._no("discover")

    def list_clips(self, fan: Fan) -> list[str]:
        self._no("list clips")

    def play(self, fan: Fan, clip: str) -> None:
        self._no("play")

    def power(self, fan: Fan, on: bool) -> None:
        self._no("power")


class FanHub:
    """The hook for shows and schedules: "switch the fans to clip X". With no driver (the
    default) it does nothing but log, so a cue that names a clip is always harmless."""

    def __init__(self):
        self.drivers: list[FanDriver] = []
        self.fans: dict[str, tuple[FanDriver, Fan]] = {}

    def add(self, driver: FanDriver, fans: list[Fan] | None = None):
        self.drivers.append(driver)
        for f in fans or []:
            self.fans[f.id] = (driver, f)

    def switch(self, clip: str, fan_ids: list[str] | None = None) -> list[dict]:
        """Ask every known fan (or the listed ones) to play `clip`. Never raises."""
        out = []
        targets = [self.fans[i] for i in (fan_ids or list(self.fans)) if i in self.fans]
        if not targets:
            log.info("hologram fans: no fan driver set up; switch to '%s' in the fan's app", clip)
            return [{"driver": "manual", "ok": False, "note": f"no fan driver: pick '{clip}' in the fan's app"}]
        for drv, fan in targets:
            try:
                drv.play(fan, clip)
                out.append({"driver": drv.name, "fan": fan.id, "ok": True})
            except Exception as e:             # noqa: BLE001 - a fan must never break a show
                out.append({"driver": drv.name, "fan": fan.id, "ok": False, "note": str(e)[:200]})
        return out

    def cue(self, cue: dict) -> list[dict] | None:
        """A show cue / schedule rule hook: {"fans": "<clip>", "fan_ids": [...]} switches
        the fans; anything without "fans" is ignored (returns None)."""
        clip = cue.get("fans") if isinstance(cue, dict) else None
        if not isinstance(clip, str) or not clip:
            return None
        ids = cue.get("fan_ids")
        return self.switch(clip, ids if isinstance(ids, list) else None)
