"""Developer tool: regenerate benchforge/font.py, the bitmap font the board animation draws its text with.

Renders DejaVu Sans Mono (Bitstream Vera licence, which allows this) to 1-bit glyphs with Pillow, and
writes them as readable row bitmasks. BenchForge itself needs no Pillow: it only reads the table.

    python tools/make_font.py /usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf
"""

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

CHARS = "".join(chr(c) for c in range(32, 127)) + "°±·µ→✓✗…≈"
SIZES = {"small": 11, "big": 17}


def glyphs(path, px):
    font = ImageFont.truetype(path, px)
    asc, desc = font.getmetrics()
    height = asc + desc
    width = max(round(font.getlength("M")), 1)
    out = {}
    for ch in CHARS:
        img = Image.new("L", (width + 4, height), 0)
        ImageDraw.Draw(img).text((0, 0), ch, font=font, fill=255)
        rows = []
        for y in range(height):
            bits = 0
            for x in range(width + 4):
                if img.getpixel((x, y)) >= 110:
                    bits |= 1 << x
            rows.append(bits)
        out[ch] = rows
    return width, height, out


def main(path):
    lines = ['"""Bitmap glyphs for the board animation, generated from DejaVu Sans Mono by tools/make_font.py.',
             "", "Each glyph is a list of rows; bit x of a row is the pixel x columns from the glyph's left edge.",
             '"""', "", "FONTS = {"]
    for name, px in SIZES.items():
        w, h, g = glyphs(path, px)
        lines.append(f"    {name!r}: {{'w': {w}, 'h': {h}, 'g': {{")
        for ch, rows in g.items():
            lines.append(f"        {ch!r}: ({', '.join(hex(r) for r in rows)}),")
        lines.append("    }},")
    lines.append("}")
    Path(__file__).resolve().parent.parent.joinpath("benchforge", "font.py").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main(sys.argv[1])
