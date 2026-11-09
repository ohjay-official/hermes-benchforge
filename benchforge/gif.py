"""A tiny raster canvas and animated-GIF writer in plain Python (no Pillow), for the board animation.

Frames are palette-indexed bytearrays. Each frame after the first stores only the rectangle that
changed, so a mostly still board animates in a few hundred kilobytes.
"""

from __future__ import annotations

import math
import struct

from .font import FONTS


class Canvas:
    def __init__(self, w: int, h: int, bg: int = 0):
        self.w, self.h = w, h
        self.px = bytearray([bg]) * (w * h)

    def copy(self) -> "Canvas":
        c = Canvas.__new__(Canvas)
        c.w, c.h, c.px = self.w, self.h, bytearray(self.px)
        return c

    def rect(self, x: int, y: int, w: int, h: int, color: int) -> None:
        x0, y0 = max(0, int(x)), max(0, int(y))
        x1, y1 = min(self.w, int(x + w)), min(self.h, int(y + h))
        if x1 <= x0 or y1 <= y0:
            return
        row = bytes([color]) * (x1 - x0)
        for yy in range(y0, y1):
            i = yy * self.w
            self.px[i + x0:i + x1] = row

    def frame(self, x: int, y: int, w: int, h: int, color: int, t: int = 1) -> None:
        self.rect(x, y, w, t, color)
        self.rect(x, y + h - t, w, t, color)
        self.rect(x, y, t, h, color)
        self.rect(x + w - t, y, t, h, color)

    def round_rect(self, x: int, y: int, w: int, h: int, r: int, color: int) -> None:
        for yy in range(int(h)):
            inset = 0
            if yy < r:
                inset = r - int(math.sqrt(max(0, r * r - (r - yy - 0.5) ** 2)))
            elif yy >= h - r:
                d = yy - (h - r) + 0.5
                inset = r - int(math.sqrt(max(0, r * r - d * d)))
            self.rect(x + inset, y + yy, w - 2 * inset, 1, color)

    def circle(self, cx: float, cy: float, r: float, color: int) -> None:
        for yy in range(int(cy - r), int(cy + r) + 1):
            dy = yy + 0.5 - cy
            if abs(dy) > r:
                continue
            dx = math.sqrt(r * r - dy * dy)
            self.rect(round(cx - dx), yy, round(2 * dx), 1, color)

    def ring(self, cx: float, cy: float, r: float, color: int, t: float = 2) -> None:
        for yy in range(int(cy - r), int(cy + r) + 1):
            for xx in range(int(cx - r), int(cx + r) + 1):
                d = math.hypot(xx + 0.5 - cx, yy + 0.5 - cy)
                if r - t <= d <= r:
                    self.set(xx, yy, color)

    def set(self, x: int, y: int, color: int) -> None:
        if 0 <= x < self.w and 0 <= y < self.h:
            self.px[y * self.w + x] = color

    def line(self, x0: float, y0: float, x1: float, y1: float, color: int, t: int = 1) -> None:
        n = int(max(abs(x1 - x0), abs(y1 - y0))) + 1
        for i in range(n + 1):
            f = i / n
            x, y = x0 + (x1 - x0) * f, y0 + (y1 - y0) * f
            if t == 1:
                self.set(round(x), round(y), color)
            else:
                self.rect(round(x - t / 2), round(y - t / 2), t, t, color)

    def text(self, x: int, y: int, s: str, color: int, size: str = "small", scale: int = 1) -> int:
        """Draw text with its top-left at (x, y); returns the x after the last glyph."""
        f = FONTS[size]
        g, w = f["g"], f["w"]
        for ch in s:
            rows = g.get(ch) or g.get("?")
            for yy, bits in enumerate(rows):
                if not bits:
                    continue
                for xx in range(w + 2):
                    if bits >> xx & 1:
                        if scale == 1:
                            self.set(x + xx, y + yy, color)
                        else:
                            self.rect(x + xx * scale, y + yy * scale, scale, scale, color)
            x += w * scale
        return x

    @staticmethod
    def text_width(s: str, size: str = "small", scale: int = 1) -> int:
        return len(s) * FONTS[size]["w"] * scale


class HiCanvas:
    """A Canvas drawn at ``k`` times the resolution of the coordinates it is given. Layout stays in 1x
    units; shapes and text are rasterised at full size (text from the double-size font), so curves and
    letters come out smooth instead of as doubled pixels."""

    def __init__(self, w: int, h: int, bg: int = 0, k: int = 2, raw: "Canvas | None" = None):
        self.k, self.w, self.h = k, w, h
        self.raw = raw if raw is not None else Canvas(w * k, h * k, bg)

    @property
    def px(self) -> bytearray:
        return self.raw.px

    def copy(self) -> "HiCanvas":
        return HiCanvas(self.w, self.h, k=self.k, raw=self.raw.copy())

    def rect(self, x, y, w, h, color) -> None:
        k = self.k
        self.raw.rect(round(x * k), round(y * k), round(w * k), round(h * k), color)

    def frame(self, x, y, w, h, color, t=1) -> None:
        k = self.k
        self.raw.frame(round(x * k), round(y * k), round(w * k), round(h * k), color, t * k)

    def round_rect(self, x, y, w, h, r, color) -> None:
        k = self.k
        self.raw.round_rect(round(x * k), round(y * k), round(w * k), round(h * k), r * k, color)

    def circle(self, cx, cy, r, color) -> None:
        self.raw.circle(cx * self.k, cy * self.k, r * self.k, color)

    def ring(self, cx, cy, r, color, t=2) -> None:
        self.raw.ring(cx * self.k, cy * self.k, r * self.k, color, t * self.k)

    def set(self, x, y, color) -> None:
        self.raw.rect(round(x * self.k), round(y * self.k), self.k, self.k, color)

    def line(self, x0, y0, x1, y1, color, t=1) -> None:
        k = self.k
        self.raw.line(x0 * k, y0 * k, x1 * k, y1 * k, color, max(1, round(t * k)))

    def text(self, x, y, s, color, size="small", scale=1) -> int:
        k = self.k
        hi = f"{size}{k}" if f"{size}{k}" in FONTS else None
        if hi:
            end = self.raw.text(round(x * k), round(y * k), s, color, hi)
        else:
            end = self.raw.text(round(x * k), round(y * k), s, color, size, scale * k)
        return round(end / k)

    @staticmethod
    def text_width(s: str, size: str = "small", scale: int = 1) -> int:
        return Canvas.text_width(s, size, scale)


# ---------------------------------------------------------------- GIF

def _lzw(indices: bytes, min_size: int) -> bytes:
    clear, end = 1 << min_size, (1 << min_size) + 1
    table = {bytes([i]): i for i in range(clear)}
    size = min_size + 1
    nxt = end + 1
    out = bytearray()
    buf = bits = 0

    def emit(code):
        nonlocal buf, bits
        buf |= code << bits
        bits += size
        while bits >= 8:
            out.append(buf & 0xFF)
            buf >>= 8
            bits -= 8

    emit(clear)
    w = b""
    for b in indices:
        wc = w + bytes([b])
        if wc in table:
            w = wc
            continue
        emit(table[w])
        table[wc] = nxt
        if nxt == (1 << size) and size < 12:
            size += 1
        nxt += 1
        if nxt == 4096:                                   # table full: start over
            emit(clear)
            table = {bytes([i]): i for i in range(clear)}
            size, nxt = min_size + 1, end + 1
        w = bytes([b])
    if w:
        emit(table[w])
    emit(end)
    if bits:
        out.append(buf & 0xFF)
    return bytes(out)


def _sub_blocks(data: bytes) -> bytes:
    return b"".join(bytes([len(data[i:i + 255])]) + data[i:i + 255] for i in range(0, len(data), 255)) + b"\x00"


def write_gif(frames: list, palette: list, delays_cs: list, w: int, h: int) -> bytes:
    """frames: list of bytearrays (w*h palette indices); palette: up to 256 (r, g, b)."""
    bits = max(1, math.ceil(math.log2(max(2, len(palette)))))
    pal = list(palette) + [(0, 0, 0)] * ((1 << bits) - len(palette))
    out = bytearray(b"GIF89a")
    out += struct.pack("<HHBBB", w, h, 0x80 | (bits - 1), 0, 0)
    for r, g, b in pal:
        out += bytes((r, g, b))
    out += b"\x21\xff\x0bNETSCAPE2.0\x03\x01\x00\x00\x00"          # loop forever
    min_size = max(2, bits)
    prev = None
    for frame, delay in zip(frames, delays_cs):
        if prev is None:
            x0, y0, x1, y1 = 0, 0, w, h
        else:
            box = _changed_box(prev, frame, w, h)
            if box is None:                                    # identical: extend nothing, keep 1x1
                box = (0, 0, 1, 1)
            x0, y0, x1, y1 = box
        sub = b"".join(bytes(frame[y * w + x0:y * w + x1]) for y in range(y0, y1))
        out += b"\x21\xf9\x04" + struct.pack("<BHBB", 0x04, max(2, int(delay)), 0, 0)
        out += b"\x2c" + struct.pack("<HHHHB", x0, y0, x1 - x0, y1 - y0, 0)
        out += bytes([min_size]) + _sub_blocks(_lzw(sub, min_size))
        prev = frame
    out += b"\x3b"
    return bytes(out)


def _changed_box(a: bytearray, b: bytearray, w: int, h: int):
    if a == b:
        return None
    rows = [y for y in range(h) if a[y * w:(y + 1) * w] != b[y * w:(y + 1) * w]]
    y0, y1 = rows[0], rows[-1] + 1
    x0, x1 = w, 0
    for y in range(y0, y1):
        ra, rb = a[y * w:(y + 1) * w], b[y * w:(y + 1) * w]
        if ra == rb:
            continue
        i = 0
        while ra[i] == rb[i]:
            i += 1
        j = w - 1
        while ra[j] == rb[j]:
            j -= 1
        x0, x1 = min(x0, i), max(x1, j + 1)
    return x0, y0, x1, y1
