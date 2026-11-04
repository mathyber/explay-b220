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
from .build_font_patch import BASELINE_SHIFTS, SETS, baseline_row, glyph, layout, mask, pack, raster, sha

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


def grey(font, char, height, width, baseline_shift, baseline):
    """The glyph with grey levels laid out like tools.build_font_patch.raster lays out its
    1bpp ink (same box, baseline and shrunk parts): the outline at SCALE times the size,
    each part box-filtered down into its rows."""
    width, ink, x, w, pieces = layout(font, char, height, width, baseline, baseline_shift)
    cell = Image.new('L', (width, height))
    if ink is None:
        return cell
    big = ImageFont.truetype(font.path, font.size*SCALE)
    pixels, (_, top) = big.getmask2(char, mode='L', anchor='ls')
    level = Image.frombytes('L', pixels.size, bytes(pixels))
    bounds = level.getbbox()
    level = level.crop(bounds)
    split = max(0, min(level.height, -(top+bounds[1])))   # the baseline row in the big ink
    parts = [(0, split), (split, level.height)] if len(pieces) == 2 else             [(0, split) if pieces[0][0] < 0 else (split, level.height)]
    for (start, stop), (_, _, row, rows) in zip(parts, pieces):
        if stop <= start:
            start, stop = (0, level.height)
        part = level.crop((0, start, level.width, stop)).resize((w, rows), Image.Resampling.BOX)
        cell.paste(part.point(lambda v: min(255, round(v*GAIN))), (x, row))
    return cell


def scale2x(cell):
    """EPX/Scale2x of a two-level image: corners follow matching neighbours (diagonals)."""
    w, h = cell.size
    p = cell.load()
    out = Image.new('L', (2*w, 2*h))
    q = out.load()

    def at(x, y):
        return p[x, y] if 0 <= x < w and 0 <= y < h else 0
    for y in range(h):
        for x in range(w):
            P, A, B, C, D = p[x, y], at(x, y-1), at(x+1, y), at(x-1, y), at(x, y+1)
            q[2*x, 2*y] = A if C == A and C != D and A != B else P
            q[2*x+1, 2*y] = B if A == B and A != C and B != D else P
            q[2*x, 2*y+1] = C if D == C and D != B and C != A else P
            q[2*x+1, 2*y+1] = D if B == D and B != A and D != C else P
    return out


def smooth(bits):
    """Grey edges for a bitmap glyph without an outline: Scale2x twice, then a 4x4 box."""
    big = scale2x(scale2x(bits.convert('L')))
    return big.resize(bits.size, Image.Resampling.BOX)


def nibbles(cell, size):
    result = bytearray(size)
    for i, value in enumerate(cell.getdata()):
        a = (value*15+127)//255
        result[i//2] |= a << 4 if i % 2 == 0 else a
    return result


def detect_font(image, sample='AaBbEeHhOoSsxАаБбВвЖжОоЯя'):
    """The donor TTF (analysis/donor-fonts) the font patch rasterized into this image:
    the one reproducing most sample glyphs bit for bit; None for none (stock font)."""
    size = heights(image)
    best, score = None, 0
    for path in [FONT]+sorted(p for p in (ROOT/'analysis/donor-fonts').rglob('*.ttf') if p != FONT):
        if not path.exists():
            continue
        found = 0
        for fontset in (SETS[0], SETS[2]):
            height = size[fontset.height]
            font = ImageFont.truetype(str(path), height-2)
            missing_ink, _ = mask(font, '￿')
            missing = missing_ink.size, missing_ink.tobytes()
            digest = sha(path.read_bytes())
            for char in sample:
                if ord(char) not in fontset.codepoints:
                    continue
                index = fontset.codepoints.index(ord(char))
                width = image[fontset.widths+index]
                start = fontset.bitmap+struct.unpack_from('>I', image, fontset.offsets+index*4)[0]
                bits = glyph(image, fontset, index, height)
                again, fallback = raster(font, char, height, width, missing, bits,
                                         BASELINE_SHIFTS.get((digest, height-2, ord(char)), 0))
                found += not fallback and pack(again) == image[start:start+(width*height+7)//8]
        if found > score:
            best, score = path, found
    return best if score*2 > len(sample) else None


def alpha_sets(image, font_path=None, smooth_bitmaps=False):
    """[(FontSet, alpha bytes, grey glyph count, wider)] for the patched image.
    wider: [(index, width, 1bpp bytes, alpha bytes)] of TTF glyphs the font patch had to
    squeeze into their slots, at their own width (tools.build_cut moves them to free ROM).
    font_path: the donor TTF, None: detect_font. smooth_bitmaps: glyphs not drawn
    from the TTF get Scale2x grey edges instead of their 1bpp shape."""
    size = heights(image)
    font_path = font_path or detect_font(image) or FONT
    digest = sha(font_path.read_bytes())
    result = []
    for s in SETS:
        height = size[s.height]
        font = ImageFont.truetype(str(font_path), height-2)
        baseline = baseline_row(font, height)
        missing_ink, _ = mask(font, '￿')
        missing = missing_ink.size, missing_ink.tobytes()
        table = bytearray(4*(s.end-s.bitmap))
        grey_count = 0
        wider = []
        for index, cp in enumerate(s.codepoints):
            width = image[s.widths+index]
            offset = struct.unpack_from('>I', image, s.offsets+index*4)[0]
            start = s.bitmap+offset
            bits = glyph(image, s, index, height)
            capacity = (width*height+7)//8
            cell = None
            if width:
                shift = BASELINE_SHIFTS.get((digest, height-2, cp), 0)
                again, fallback = raster(font, chr(cp), height, width, missing, bits, shift, baseline)
                # Only a glyph this TTF reproduces bit for bit (same cell width) gets grey levels.
                if not fallback and again.width == width and pack(again) == image[start:start+capacity]:
                    cell = grey(font, chr(cp), height, width, shift, baseline)
                    own, _ = raster(font, chr(cp), height, 255, missing, bits, shift, baseline)
                    if own.width > width:
                        wide = grey(font, chr(cp), height, own.width, shift, baseline)
                        wider.append((index, own.width, pack(own), bytes(nibbles(wide, (own.width*height+1)//2))))
            if cell is None and smooth_bitmaps and width:
                cell = smooth(bits)
                grey_count += 1
            elif cell is None:
                cell = bits.convert('L')
            else:
                grey_count += 1
            data = nibbles(cell, (width*height+1)//2)
            table[4*offset:4*offset+len(data)] = data
        result.append((s, bytes(table), grey_count, wider))
    return result


def render(image, alpha, text, original_height, fg=(255, 255, 255), bg=(0, 0, 0)):
    """Host preview of a text line with the alpha tables (RGB image)."""
    size = heights(image)
    height = size[original_height]
    cells = []
    for char in text:
        for s, table, *_ in alpha:
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
