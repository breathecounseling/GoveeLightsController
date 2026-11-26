"""Animated GIF in pure Python: colour quantisation, LZW, and a small decoder for checks.

Quantisation is tuned for hologram-fan art: a black background (black is always palette
entry 0, so "transparent" on a fan stays exactly #000000), a few bold saturated colours,
and glows. Colours are first reduced to 15 bits (32 levels a channel, plenty for an LED
fan); if a clip has 255 or fewer such colours they are kept exactly, otherwise a median
cut picks 255, weighting rare colours up so a small bright spark isn't merged away into a
big flat area. There is no dithering: on a spinning fan dither reads as sparkle noise.

    w = GifWriter(fileobj, width, height, fps)          # loops forever by default
    w.set_palette(build_palette([keys(frame) ...]))     # global palette (optional:
    w.add(frame); ...; w.close()                        #   built from the first frame)
    GifWriter(..., local=True) gives every frame its own palette instead.

Frames are anything with .w, .h and .p (three planes R, G, B) - a canvas.Canvas.
"""
from __future__ import annotations

import re
import struct
import sys
from collections import Counter

_TR = bytes(v >> 3 for v in range(256))                  # 8 -> 5 bits
_T_B = _TR
_T_GLO = bytes(((v >> 3) & 7) << 5 for v in range(256))  # low 3 bits of green, in the low byte
_T_GHI = bytes(v >> 6 for v in range(256))               # high 2 bits of green, in the high byte
_T_R = bytes((v >> 3) << 2 for v in range(256))
_LITTLE = sys.byteorder == "little"


def _add_bytes(a: bytes, b: bytes) -> bytes:
    """Element-wise a + b for byte strings whose sums never carry (disjoint bit fields):
    done as one big-integer addition, so it runs in C."""
    n = len(a)
    return (int.from_bytes(a, "little") + int.from_bytes(b, "little")).to_bytes(n, "little")


def keys(frame) -> memoryview:
    """Per pixel, the 15-bit colour key r5<<10 | g5<<5 | b5, as a memoryview of uint16."""
    r, g, b = frame.p
    lo = _add_bytes(g.translate(_T_GLO), b.translate(_T_B))
    hi = _add_bytes(r.translate(_T_R), g.translate(_T_GHI))
    buf = bytearray(2 * len(lo))
    if _LITTLE:
        buf[0::2], buf[1::2] = lo, hi
    else:
        buf[0::2], buf[1::2] = hi, lo
    return memoryview(buf).cast("H")


def key_rgb(k: int) -> tuple:
    r, g, b = (k >> 10) & 31, (k >> 5) & 31, k & 31
    return (r << 3 | r >> 2, g << 3 | g >> 2, b << 3 | b >> 2)


def build_palette(key_sets, max_colors: int = 256) -> list:
    """A palette (list of (r, g, b), black first) for the given frames' keys. key_sets is an
    iterable of memoryviews from keys() (a sample of frames is enough)."""
    hist: Counter = Counter()
    for ks in key_sets:
        hist.update(ks)
    hist.pop(0, None)
    room = max_colors - 1
    if len(hist) <= room:
        return [(0, 0, 0)] + [key_rgb(k) for k in sorted(hist)]
    items = []
    for k, n in hist.items():
        r, g, b = key_rgb(k)
        sat = (max(r, g, b) - min(r, g, b)) / 255
        items.append((r, g, b, (n ** 0.5) * (1 + 2 * sat)))
    boxes = [items]
    while len(boxes) < room:
        best, score, ch = -1, 0.0, 0
        for bi, bx in enumerate(boxes):
            if len(bx) < 2:
                continue
            w = sum(it[3] for it in bx)
            for c in range(3):
                vs = [it[c] for it in bx]
                s = (max(vs) - min(vs)) * (w ** 0.5)
                if s > score:
                    best, score, ch = bi, s, c
        if best < 0:
            break
        bx = sorted(boxes.pop(best), key=lambda it: it[ch])
        half, acc, cut = sum(it[3] for it in bx) / 2, 0.0, 1
        for i, it in enumerate(bx):
            acc += it[3]
            if acc >= half:
                cut = max(1, min(len(bx) - 1, i + 1))
                break
        boxes += [bx[:cut], bx[cut:]]
    pal = [(0, 0, 0)]
    for bx in boxes:
        w = sum(it[3] for it in bx) or 1
        # the box's colour: the weighted mean (boxes are small, and the rare-colour
        # weighting above keeps vivid sparks in boxes of their own)
        pal.append(tuple(int(sum(it[c] * it[3] for it in bx) / w + 0.5) for c in range(3)))
    return pal


def _nearest(rgb, pal) -> int:
    r, g, b = rgb
    if r == g == b == 0:
        return 0
    best, bd = 0, 1 << 30
    for i, (pr, pg, pb) in enumerate(pal):
        dr, dg, db = r - pr, g - pg, b - pb
        rm = (r + pr) >> 1
        d = (((512 + rm) * dr * dr) >> 8) + 4 * dg * dg + (((767 - rm) * db * db) >> 8)
        if d < bd:
            best, bd = i, d
            if d == 0:
                break
    return best


class Mapper:
    """Colour key -> palette index, worked out lazily for the keys a frame actually uses."""

    def __init__(self, pal):
        self.pal = list(pal)
        self.lut = bytearray(32768)
        self.known = bytearray(32768)
        self.known[0] = 1
        self.lut[0] = 0 if self.pal and self.pal[0] == (0, 0, 0) else _nearest((0, 0, 0), self.pal)

    def indices(self, ks: memoryview) -> bytes:
        known, lut, pal = self.known, self.lut, self.pal
        for k in set(ks):
            if not known[k]:
                lut[k] = _nearest(key_rgb(k), pal)
                known[k] = 1
        return bytes(map(lut.__getitem__, ks))


_RUN = [re.compile(re.escape(bytes((b,))) + b"+").match for b in range(256)]


def lzw_encode(data: bytes, mcs: int = 8) -> bytes:
    """GIF-flavoured LZW (variable code width up to 12 bits, LSB-first packing).

    Plain greedy LZW, with one shortcut that gives identical output: a run of one byte
    (the black background) is consumed a whole dictionary chain at a time instead of a
    byte at a time, which roughly halves the time on fan art."""
    clear = 1 << mcs
    eoi = clear + 1
    cs = mcs + 1
    nxt = eoi + 1
    table = {}
    get = table.get
    out = bytearray()
    buf, nb = clear, cs
    n = len(data)
    if not n:
        buf |= eoi << nb
        nb += cs
        while nb > 0:
            out.append(buf & 255)
            buf >>= 8
            nb -= 8
        return bytes(out)
    chains = {}
    w = data[0]
    i = 1
    s = 0                     # where the current string started
    while True:
        # at a string start s with w = data[s] (one byte), i = s + 1
        if i < n and data[i] == w:
            k = w
            L = _RUN[k](data, s).end() - s
            ch = chains.get(k)
            if ch is None:
                ch = chains[k] = [k]
            c2 = get((ch[-1] << 8) | k)
            while c2 is not None:
                ch.append(c2)
                c2 = get((c2 << 8) | k)
            m = len(ch)
            while L > m:
                code = ch[-1]
                buf |= code << nb
                nb += cs
                while nb >= 8:
                    out.append(buf & 255)
                    buf >>= 8
                    nb -= 8
                if nxt < 4095:
                    table[(code << 8) | k] = nxt
                    ch.append(nxt)
                    nxt += 1
                    if nxt > (1 << cs) and cs < 12:
                        cs += 1
                else:
                    buf |= clear << nb
                    nb += cs
                    table.clear()
                    chains.clear()
                    ch = chains[k] = [k]
                    cs = mcs + 1
                    nxt = eoi + 1
                s += m
                L -= m
                m = len(ch)
            i = s + 1
        # the ordinary greedy walk until the next miss
        while i < n:
            k = data[i]
            c = get((w << 8) | k)
            if c is None:
                break
            w = c
            i += 1
        else:
            break
        buf |= w << nb
        nb += cs
        while nb >= 8:
            out.append(buf & 255)
            buf >>= 8
            nb -= 8
        if nxt < 4095:
            table[(w << 8) | k] = nxt
            nxt += 1
            if nxt > (1 << cs) and cs < 12:
                cs += 1
        else:
            buf |= clear << nb
            nb += cs
            table.clear()
            chains.clear()
            cs = mcs + 1
            nxt = eoi + 1
        w = k
        s = i
        i += 1
    buf |= w << nb
    nb += cs
    buf |= eoi << nb
    nb += cs
    while nb > 0:
        out.append(buf & 255)
        buf >>= 8
        nb -= 8
    return bytes(out)


def _blocks(data: bytes) -> bytes:
    out = bytearray()
    for i in range(0, len(data), 255):
        chunk = data[i:i + 255]
        out.append(len(chunk))
        out += chunk
    out.append(0)
    return bytes(out)


def _ctable(pal) -> tuple[bytes, int]:
    """Colour table padded to a power of two, and its size field (n = 2**(field+1))."""
    n = max(2, len(pal))
    bits = max(1, (n - 1).bit_length())
    raw = bytearray()
    for i in range(1 << bits):
        raw += bytes(pal[i]) if i < len(pal) else b"\0\0\0"
    return bytes(raw), bits - 1


class GifWriter:
    def __init__(self, f, width: int, height: int, fps: float, loop: int = 0, local: bool = False):
        self.f, self.w, self.h, self.fps, self.loop, self.local = f, int(width), int(height), float(fps), loop, local
        self.pal = None
        self.mapper = None
        self.count = 0
        self._started = False

    def set_palette(self, pal):
        if self._started:
            raise RuntimeError("palette must be set before the first frame")
        self.pal = list(pal)[:256]
        self.mapper = Mapper(self.pal)

    def _start(self, first_keys):
        if not self.local and self.pal is None:
            self.set_palette(build_palette([first_keys]))
        f = self.f
        f.write(b"GIF89a")
        if self.local:
            f.write(struct.pack("<HHBBB", self.w, self.h, 0, 0, 0))
        else:
            tab, field = _ctable(self.pal)
            f.write(struct.pack("<HHBBB", self.w, self.h, 0x80 | 0x70 | field, 0, 0))
            f.write(tab)
        if self.loop is not None:
            f.write(b"\x21\xff\x0bNETSCAPE2.0\x03\x01" + struct.pack("<H", self.loop) + b"\x00")
        self._started = True

    def _delay(self, k: int) -> int:
        return max(2, round((k + 1) * 100 / self.fps) - round(k * 100 / self.fps))

    def add(self, frame):
        if frame.w != self.w or frame.h != self.h:
            raise ValueError("frame size doesn't match the GIF")
        ks = keys(frame)
        if not self._started:
            self._start(ks)
        f = self.f
        f.write(b"\x21\xf9\x04" + struct.pack("<BHBB", 0x04, self._delay(self.count), 0, 0))
        if self.local:
            pal = build_palette([ks])
            idx = Mapper(pal).indices(ks)
            tab, field = _ctable(pal)
            f.write(b"\x2c" + struct.pack("<HHHHB", 0, 0, self.w, self.h, 0x80 | field) + tab)
        else:
            idx = self.mapper.indices(ks)
            f.write(b"\x2c" + struct.pack("<HHHHB", 0, 0, self.w, self.h, 0))
        f.write(b"\x08")
        f.write(_blocks(lzw_encode(idx, 8)))
        self.count += 1

    def close(self):
        if not self._started:
            raise ValueError("a GIF needs at least one frame")
        self.f.write(b"\x3b")


# ---------------------------------------------------------------- decoder (for checks)
def lzw_decode(data: bytes, min_code_size: int) -> bytes:
    clear = 1 << min_code_size
    eoi = clear + 1
    cs = min_code_size + 1
    tab = [bytes((i,)) for i in range(clear)] + [b"", b""]
    out = bytearray()
    prev = None
    bitpos, total = 0, len(data) * 8
    val = int.from_bytes(data, "little")
    while bitpos + cs <= total:
        code = (val >> bitpos) & ((1 << cs) - 1)
        bitpos += cs
        if code == clear:
            cs = min_code_size + 1
            tab = tab[:clear + 2]
            prev = None
            continue
        if code == eoi:
            break
        if prev is None:
            entry = tab[code]
        elif code < len(tab):
            entry = tab[code]
            if len(tab) < 4096:
                tab.append(prev + entry[:1])
        else:
            entry = prev + prev[:1]
            if len(tab) < 4096:
                tab.append(entry)
        out += entry
        prev = entry
        if len(tab) == (1 << cs) and cs < 12:
            cs += 1
    return bytes(out)


def decode(data: bytes) -> dict:
    """Parse a GIF into {"w", "h", "loop", "frames": [{"rgb": bytes, "delay": cs}]} (full
    frames, no partial-frame compositing beyond what this module writes)."""
    if data[:6] not in (b"GIF87a", b"GIF89a"):
        raise ValueError("not a GIF")
    w, h, flags, _bg, _asp = struct.unpack("<HHBBB", data[6:13])
    pos = 13
    gct = None
    if flags & 0x80:
        n = 2 << (flags & 7)
        gct = [tuple(data[pos + 3 * i: pos + 3 * i + 3]) for i in range(n)]
        pos += 3 * n
    res = {"w": w, "h": h, "loop": None, "frames": []}
    delay = 0
    while pos < len(data):
        b = data[pos]
        pos += 1
        if b == 0x3B:
            break
        if b == 0x21:
            label = data[pos]
            pos += 1
            sub = bytearray()
            while data[pos]:
                sub += data[pos + 1: pos + 1 + data[pos]]
                pos += 1 + data[pos]
            pos += 1
            if label == 0xF9:
                delay = struct.unpack("<H", bytes(sub[1:3]))[0]
            elif label == 0xFF and sub[:11] == b"NETSCAPE2.0":
                res["loop"] = struct.unpack("<H", bytes(sub[12:14]))[0]
            continue
        if b != 0x2C:
            raise ValueError("bad GIF block")
        x, y, fw, fh, fl = struct.unpack("<HHHHB", data[pos:pos + 9])
        pos += 9
        ct = gct
        if fl & 0x80:
            n = 2 << (fl & 7)
            ct = [tuple(data[pos + 3 * i: pos + 3 * i + 3]) for i in range(n)]
            pos += 3 * n
        mcs = data[pos]
        pos += 1
        raw = bytearray()
        while data[pos]:
            raw += data[pos + 1: pos + 1 + data[pos]]
            pos += 1 + data[pos]
        pos += 1
        idx = lzw_decode(bytes(raw), mcs)
        if (x, y, fw, fh) != (0, 0, w, h) or len(idx) != w * h:
            raise ValueError("partial frames aren't supported by this checker")
        rgb = bytearray()
        for i in idx:
            rgb += bytes(ct[i])
        res["frames"].append({"rgb": bytes(rgb), "delay": delay, "index": idx, "palette": ct})
    return res
