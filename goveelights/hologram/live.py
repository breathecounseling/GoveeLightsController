"""EXPERIMENTAL: play a scene live on a WLED pixel matrix over DDP (UDP port 4048).

    python -m goveelights hologram santa-landing --matrix 64x32 --live 192.168.1.60

The frames are rendered once up front (a matrix is small, so that takes seconds), then
sent at the clip's frame rate with the same DDP packets the lights' engine uses for WLED
strips (output.WledDdpOutput.packets). WLED shows realtime data while it keeps arriving
and goes back to its own effect a couple of seconds after it stops.

Set the matrix up in WLED first (Config -> 2D Configuration), so WLED maps the pixels to
the panel's wiring; then send rows from the top left (the default). If the picture comes
out zig-zagged, the panel is serpentine and WLED isn't mapping it: add --serpentine.

Don't point it at a WLED device the lights' engine is also driving (a run in the house
layout): the two streams would fight. Nothing here is started by the app or a schedule.
"""
from __future__ import annotations

import socket
import time

from ..output import WledDdpOutput

DEFAULT_PORT = WledDdpOutput.PORT


def parse_host(text: str) -> tuple[str, int]:
    host, _, port = str(text or "").strip().partition(":")
    if not host:
        raise ValueError("--live needs the WLED device's IP address (or IP:port)")
    p = int(port) if port else DEFAULT_PORT
    if not 1 <= p <= 65535:
        raise ValueError("bad port")
    return host, p


def render(clip) -> list[bytes]:
    """Every frame of a matrix clip as wire-order RGB bytes."""
    lay = clip.layout
    if getattr(lay, "kind", "") != "matrix":
        raise ValueError("live play needs an LED matrix (--matrix WxH)")
    return [lay.wire_order(clip.frame(i)[0]) for i in range(clip.count)]


def stream(frames: list[bytes], host: str, port: int, fps: float, loops: int = 0, stop=None,
           sock=None, clock=time.monotonic, sleep=time.sleep) -> int:
    """Send the frames to host:port, looping `loops` times (0 = until stop() is true or
    Ctrl-C). Returns the number of frames sent."""
    own = sock is None
    if own:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sent, seq = 0, 0
    period = 1.0 / fps
    t_next = clock()
    try:
        loop = 0
        while loops <= 0 or loop < loops:
            for data in frames:
                if stop is not None and stop():
                    return sent
                seq = seq % 15 + 1
                px = [data[i:i + 3] for i in range(0, len(data), 3)]
                for pkt in WledDdpOutput.packets(px, seq):
                    sock.sendto(pkt, (host, port))
                sent += 1
                t_next += period
                d = t_next - clock()
                if d > 0:
                    sleep(d)
                elif d < -1.0:                        # fell far behind (a stall): don't race to catch up
                    t_next = clock()
            loop += 1
    except KeyboardInterrupt:
        pass
    finally:
        if own:
            sock.close()
    return sent
