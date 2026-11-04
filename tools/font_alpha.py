"""Antialiased (4bpp) copies of the four native B220 bitmap font sets.

The K550 font patch (tools.build_font_patch) rasterizes a TTF into the 1bpp
slots. Here the same TTF is rendered with grey levels at the same place in the
same cell: one alpha nibble per pixel, high nibble first, pixels continue across
rows like the 1bpp bits. A glyph's alpha slot is four times its 1bpp slot, so
alpha address = alpha base + 4*(1bpp glyph address - 1bpp base).

A glyph the patch did not rasterize exactly from the TTF (a fallback, a fitted
one) keeps its 1bpp shape: alpha 15 where the bit is set.
"""
import struct
from pathlib import Path

from PIL import Image, ImageFont

from emulator.firmware import ROOT
from .build_font_patch import BASELINE_SHIFTS, SETS, glyph, mask, pack, raster, sha

FONT = ROOT/'analysis/donor-fonts/k550/Sans-Serif_Europe_Reg.ttf'
HEIGHTS = {16: 0x5b8716, 14: 0x5b871e}   # MOVS r0,#height of the large and small sets
SCALE = 8      # supersampling of the outline
GAIN = 1.35    # thin stems of a 10-12 px font come out pale in a box filter


def heights(image):
    result = {}
    for original, address in HEIGHTS.items():
        if image[address] != 0x20:
            raise ValueError('Unexpected native font-height instruction')
        result[original] = image[address+1]
    return result


def grey(font, char, height, width, baseline_shift):
    """The glyph with grey levels at the place tools.build_font_patch.raster puts its 1bpp ink,
    or None when that raster was fitted (resized) into the cell."""
    ink, (left, top) = mask(font, char)
    bounds = ink.getbbox()
    if not bounds:
        return Image.new('L', (width, height))
    x = max(0, left+bounds[0])
    y = top+bounds[1]+height-2+baseline_shift
    w, h = bounds[2]-bounds[0], bounds[3]-bounds[1]
    x = min(x, max(0, width-2))
    if w > (width-x-1 if width > 1 else 1) or h > height:
        return None
    y = max(0, min(y, height-h))
    # The outline at SCALE times the size, box-filtered down into exactly the 1bpp
    # ink box: grey edges, the same advance, place and spacing as the 1bpp text.
    big = ImageFont.truetype(font.path, font.size*SCALE)
    pixels, _ = big.getmask2(char, mode='L', anchor='ls')
    level = Image.frombytes('L', pixels.size, bytes(pixels))
    level = level.crop(level.getbbox()).resize((w, h), Image.Resampling.BOX)
    level = level.point(lambda v: min(255, round(v*GAIN)))
    cell = Image.new('L', (width, height))
    cell.paste(level, (x, y))
    return cell


def nibbles(cell, size):
    result = bytearray(size)
    for i, value in enumerate(cell.getdata()):
        a = (value*15+127)//255
        result[i//2] |= a << 4 if i % 2 == 0 else a
    return result


def alpha_sets(image, font_path=FONT):
    """[(FontSet, alpha bytes, grey glyph count)] for the patched image."""
    size = heights(image)
    digest = sha(font_path.read_bytes())
    result = []
    for s in SETS:
        height = size[s.height]
        font = ImageFont.truetype(str(font_path), height-2)
        missing_ink, _ = mask(font, '￿')
        missing = missing_ink.size, missing_ink.tobytes()
        table = bytearray(4*(s.end-s.bitmap))
        grey_count = 0
        for index, cp in enumerate(s.codepoints):
            width = image[s.widths+index]
            offset = struct.unpack_from('>I', image, s.offsets+index*4)[0]
            start = s.bitmap+offset
            bits = glyph(image, s, index, height)
            capacity = (width*height+7)//8
            cell = None
            if width:
                shift = BASELINE_SHIFTS.get((digest, height-2, cp), 0)
                again, fallback = raster(font, chr(cp), height, width, missing, bits, shift)
                # Only a glyph this TTF reproduces bit for bit (same cell width) gets grey levels.
                if not fallback and again.width == width and pack(again) == image[start:start+capacity]:
                    cell = grey(font, chr(cp), height, width, shift)
            if cell is None:
                cell = bits.convert('L')
            else:
                grey_count += 1
            data = nibbles(cell, (width*height+1)//2)
            table[4*offset:4*offset+len(data)] = data
        result.append((s, bytes(table), grey_count))
    return result


def render(image, alpha, text, original_height, fg=(255, 255, 255), bg=(0, 0, 0)):
    """Host preview of a text line with the alpha tables (RGB image)."""
    size = heights(image)
    height = size[original_height]
    cells = []
    for char in text:
        for s, table, _ in alpha:
            if s.height == original_height and ord(char) in s.codepoints:
                index = s.codepoints.index(ord(char))
                width = image[s.widths+index]
                offset = struct.unpack_from('>I', image, s.offsets+index*4)[0]
                values = [(table[4*offset+i//2] >> (4 if i % 2 == 0 else 0) & 15)*17 for i in range(width*height)]
                cell = Image.new('L', (width, height)); cell.putdata(values)
                cells.append(cell)
                break
    line = Image.new('RGB', (max(1, sum(c.width for c in cells)), height), bg)
    x = 0
    for cell in cells:
        line.paste(Image.new('RGB', cell.size, fg), (x, 0), cell); x += cell.width
    return line


if __name__ == '__main__':
    import sys
    image = Path(sys.argv[1]).read_bytes()
    alpha = alpha_sets(image)
    for s, table, count in alpha:
        print(s.name, len(table), 'bytes', count, 'grey of', len(s.codepoints))
