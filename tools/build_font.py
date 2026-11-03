"""Rasterise a TrueType font into the B22 JVM bitmap font format (MIDP Font sizes).

Called by tools.build_jvm; writes b22/font-s.bin, font-m.bin and font-l.bin
into the class library. Format (big-endian): "BF", version 1, height, ascent, 0,
u16 glyph count, entries {u16 code, u8 advance, u8 width, u32 bitmap offset}
sorted by code, then 1-bit bitmaps of height rows, most significant bit first.
"""
from pathlib import Path
import struct

from PIL import Image, ImageDraw, ImageFont

from emulator.firmware import ROOT

SOURCES = (ROOT/'analysis/donor-fonts/k550/Sans-Serif_Europe_Reg.ttf', Path('C:/Windows/Fonts/tahoma.ttf'),
           Path('C:/Windows/Fonts/arial.ttf'))
SIZES = {'s': 12, 'm': 14, 'l': 16}
CHARACTERS = ([*range(0x20, 0x7f), *range(0xa0, 0x100), *range(0x401, 0x460), 0x2013, 0x2014, 0x2018, 0x2019,
               0x201c, 0x201d, 0x2022, 0x2026, 0x2116, 0x20ac])


def font_source():
    for path in SOURCES:
        if path.exists():
            return path
    raise ValueError('No TrueType font found for the MIDP bitmap font')


def rasterise(path, pixels):
    font = ImageFont.truetype(str(path), pixels)
    ascent, descent = font.getmetrics()
    height = ascent+descent
    entries, bitmaps = [], bytearray()
    for code in CHARACTERS:
        ch = chr(code)
        if code != 0x20 and font.getmask(ch).getbbox() is None:
            continue
        advance = max(1, round(font.getlength(ch)))
        box = font.getbbox(ch)
        width = max(advance, box[2]) if box else advance
        width = min(width, 32)
        image = Image.new('L', (width, height), 0)
        ImageDraw.Draw(image).text((0, 0), ch, font=font, fill=255)
        row_bytes = (width+7)//8
        data = bytearray(row_bytes*height)
        for y in range(height):
            for x in range(width):
                if image.getpixel((x, y)) >= 128:
                    data[y*row_bytes+x//8] |= 0x80 >> (x % 8)
        entries.append((code, advance, width, len(bitmaps)))
        bitmaps += data
    header = b'BF'+bytes([1, height, ascent, 0])+struct.pack('>H', len(entries))
    table = b''.join(struct.pack('>HBBI', *e) for e in entries)
    return header+table+bytes(bitmaps)


def build_fonts(directory):
    path = font_source()
    target = directory/'b22'
    target.mkdir(parents=True, exist_ok=True)
    sizes = {}
    for name, pixels in SIZES.items():
        data = rasterise(path, pixels)
        (target/f'font-{name}.bin').write_bytes(data)
        sizes[name] = len(data)
    return path, sizes
