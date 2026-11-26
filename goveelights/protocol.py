"""Pure packet builders for the Govee LAN API (no I/O here).

Official, documented commands (UDP JSON to <ip>:4003): scan, turn,
brightness, devStatus, colorwc.

Undocumented, community reverse-engineered commands:

* ``razer`` - the realtime/DreamView channel. Source:
  https://github.com/Galorhallen/govee-local-api/issues/404
  Packet: ``BB <len_hi> <len_lo> <cmd> <payload...> <xor>`` where ``len`` is
  the number of payload bytes after the command byte (excluding checksum) and
  ``xor`` is the XOR of every preceding byte including ``BB``.
  This reproduces the published enable/disable packets exactly
  (``BB 00 01 B1 01 0A`` / ``BB 00 01 B1 00 0B``) and the frame rule
  ``len = 3n + 2``.

* ``ptReal`` - raw 20-byte BLE-style packets (19 bytes + XOR). Finger Sketch
  framing ported from https://github.com/rileysheehan/homebridge-govee-pixel-zones
  (MIT licence, Copyright (c) 2026 Riley Sheehan; its protocol packet and Finger
  Sketch code). See THIRD_PARTY_NOTICES.md for the licence text.
"""
from __future__ import annotations

import base64
import json
from typing import Iterable, Sequence

RGB = tuple[int, int, int]

SCAN_GROUP = "239.255.255.250"
SCAN_PORT = 4001      # devices listen for scan here
LISTEN_PORT = 4002    # devices reply here
CONTROL_PORT = 4003   # devices accept commands here

MAX_RAZER_COLORS = 255  # n is a single byte


def xor_bytes(data: Iterable[int]) -> int:
    c = 0
    for b in data:
        c ^= b
    return c


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def encode(msg: dict) -> bytes:
    return json.dumps(msg, separators=(",", ":")).encode("utf-8")


# ---------------------------------------------------------------- official
def scan_msg() -> dict:
    return {"msg": {"cmd": "scan", "data": {"account_topic": "reserve"}}}


def turn_msg(on: bool) -> dict:
    return {"msg": {"cmd": "turn", "data": {"value": 1 if on else 0}}}


def brightness_msg(value: int) -> dict:
    return {"msg": {"cmd": "brightness", "data": {"value": max(1, min(100, int(value)))}}}


def status_msg() -> dict:
    return {"msg": {"cmd": "devStatus", "data": {}}}


def colorwc_msg(rgb: RGB = (0, 0, 0), kelvin: int = 0) -> dict:
    r, g, b = (_clamp(v) for v in rgb)
    return {"msg": {"cmd": "colorwc", "data": {"color": {"r": r, "g": g, "b": b},
                                              "colorTemInKelvin": int(kelvin)}}}


# ------------------------------------------------------------------ razer
RAZER_CMD_FRAME = 0xB0
RAZER_CMD_MODE = 0xB1


def razer_packet(cmd: int, payload: bytes) -> bytes:
    n = len(payload)
    body = bytes([0xBB, (n >> 8) & 0xFF, n & 0xFF, cmd & 0xFF]) + bytes(payload)
    return body + bytes([xor_bytes(body)])


def razer_enable_packet(on: bool = True) -> bytes:
    return razer_packet(RAZER_CMD_MODE, bytes([1 if on else 0]))


def razer_frame_packet(colors: Sequence[RGB], gradient: bool = False) -> bytes:
    """One frame. gradient=False: the n colors are spread evenly along the
    string (n == bulb count gives one colour per bulb). gradient=True: the
    device interpolates between them."""
    n = len(colors)
    if not 1 <= n <= MAX_RAZER_COLORS:
        raise ValueError(f"razer frame needs 1..{MAX_RAZER_COLORS} colours, got {n}")
    payload = bytearray([1 if gradient else 0, n])
    for r, g, b in colors:
        payload += bytes((_clamp(r), _clamp(g), _clamp(b)))
    return razer_packet(RAZER_CMD_FRAME, bytes(payload))


def razer_rgbw_probe_packet(colors: Sequence[tuple[int, int, int, int]]) -> bytes:
    """EXPERIMENTAL / speculative: 4 bytes per colour. Not documented anywhere;
    used only to see whether the device rejects it (expected) or uses W."""
    n = len(colors)
    payload = bytearray([0, n])
    for c in colors:
        payload += bytes(_clamp(v) for v in c)
    return razer_packet(RAZER_CMD_FRAME, bytes(payload))


def razer_msg(packet: bytes) -> dict:
    return {"msg": {"cmd": "razer", "data": {"pt": b64(packet)}}}


# ------------------------------------------------------- ptReal / BLE-style
PACKET_LEN = 20


def ble_packet(payload: Iterable[int]) -> bytes:
    vals = list(payload)
    if len(vals) > PACKET_LEN - 1:
        raise ValueError("ble packet payload exceeds 19 bytes")
    body = bytes(vals) + bytes(PACKET_LEN - 1 - len(vals))
    return body + bytes([xor_bytes(body)])


def multi_packet(payload: bytes) -> list[bytes]:
    """0xA3 multi-packet framing (port of encodeMultiPacket)."""
    first, rest = payload[:15], payload[15:]
    cont = max(1, -(-len(rest) // 17))
    out = [ble_packet([0xA3, 0x00, 0x01, 1 + cont, *first])]
    for i in range(cont):
        seq = 0xFF if i == cont - 1 else i + 1
        out.append(ble_packet([0xA3, seq, *rest[i * 17:(i + 1) * 17]]))
    return out


def finger_sketch_packets(groups: Sequence[tuple[RGB, Sequence[int]]],
                          background: RGB = (0, 0, 0), speed: int = 1) -> list[bytes]:
    """Finger Sketch frame body: 03 09 00 speed bgR bgG bgB G {n r g b ids...}*G"""
    body = bytearray([0x03, 0x09, 0x00, speed & 0xFF, *map(_clamp, background), len(groups)])
    for color, pixels in groups:
        if len(set(pixels)) != len(pixels):
            raise ValueError("duplicate pixel ids in group")
        body += bytes([len(pixels), *map(_clamp, color), *(p & 0xFF for p in pixels)])
    return multi_packet(bytes(body))


# Activation packet as reported in the razer issue (Finger Sketch on H706B):
# "33 05 0a 20 03". The pixel-zones repo captures the real one from the app;
# this hand-built version is a best guess.
FINGER_SKETCH_ACTIVATE = ble_packet([0x33, 0x05, 0x0A, 0x20, 0x03])


def ptreal_msg(packets: Sequence[bytes]) -> dict:
    return {"msg": {"cmd": "ptReal", "data": {"command": [b64(p) for p in packets]}}}


# ------------------------------------------------------------------ utils
def _clamp(v) -> int:
    return max(0, min(255, int(v)))


def hex_rgb(s: str) -> RGB:
    s = s.lstrip("#")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


def decode_razer(packet: bytes) -> dict:
    """Parse a razer packet (used by tests and the fake device)."""
    if len(packet) < 5 or packet[0] != 0xBB:
        raise ValueError("not a razer packet")
    ln = (packet[1] << 8) | packet[2]
    if len(packet) != 4 + ln + 1:
        raise ValueError("length mismatch")
    if xor_bytes(packet[:-1]) != packet[-1]:
        raise ValueError("bad checksum")
    cmd, payload = packet[3], packet[4:-1]
    out = {"cmd": cmd, "payload": payload}
    if cmd == RAZER_CMD_FRAME:
        n = payload[1]
        if len(payload) != 2 + 3 * n:
            raise ValueError("frame colour count mismatch")
        out["gradient"] = bool(payload[0])
        out["colors"] = [tuple(payload[2 + 3 * i: 5 + 3 * i]) for i in range(n)]
    elif cmd == RAZER_CMD_MODE:
        out["on"] = bool(payload[0])
    return out
