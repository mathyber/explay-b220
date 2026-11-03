"""MB and ABMP images of the B220 firmware: decode to RGBA and encode from RGBA.

MB (record type 0x1): BMP-like header with big-endian fields and signature
'MB', RGB565, rows bottom-up, 0xF81F transparent. Compression 0x1003 is row RLE:
packets (repeat, literal count, literal pixels, repeat colour) and 00 00 at the
end of a row; firmware images always have repeat >= 1, so the encoder does too.
Compression 3 stores rows uncompressed, aligned to 4 bytes.

ABMP (record type 0x30c): palette with alpha; see CLOCK_DIGITS_RU.md. Index 0
is transparent, 1..opaque-1 opaque colours, then colour + alpha pairs. Indices
of the non-empty rectangle are packed into big-endian u16 words, least
significant bit first.
"""
import struct

from PIL import Image

TRANSPARENT = 0xf81f
MB_HEADER = 66


def rgb(value):
    return ((value >> 11) * 255 // 31, (value >> 5 & 63) * 255 // 63, (value & 31) * 255 // 31)


def rgb565(r, g, b):
    return (r * 31 + 127) // 255 << 11 | (g * 63 + 127) // 255 << 5 | (b * 31 + 127) // 255


def decode_mb(data, address=0):
    if data[address:address+2] != b'MB':
        raise ValueError('MB signature missing')
    size, = struct.unpack_from('>I', data, address+2)
    data_at, = struct.unpack_from('>I', data, address+10)
    width, height = struct.unpack_from('>ii', data, address+18)
    bpp, compression = struct.unpack_from('>HI', data, address+28)
    if bpp != 16 or compression not in (3, 0x1003) or not (0 < width <= 1024 and 0 < abs(height) <= 1024):
        raise ValueError(f'Unsupported MB: {bpp} bpp, compression {compression:#x}')
    at, end = address+data_at, address+size
    rows = []
    stride = (width*2+3)//4*4
    for _ in range(abs(height) if compression == 3 else 0):
        if at+stride > end:
            raise ValueError('MB data ends inside a row')
        rows.append(list(struct.unpack_from(f'>{width}H', data, at)))
        at += stride
    for _ in range(abs(height) if compression == 0x1003 else 0):
        row = []
        while True:
            if at+2 > end:
                raise ValueError('MB data ends inside a row')
            repeat, literal = data[at], data[at+1]
            at += 2
            if not repeat and not literal:
                break
            row += struct.unpack_from(f'>{literal}H', data, at)
            at += 2*literal
            if repeat:
                row += [struct.unpack_from('>H', data, at)[0]]*repeat
                at += 2
        if len(row) != width:
            raise ValueError(f'MB row of {len(row)} pixels, expected {width}')
        rows.append(row)
    if height > 0:
        rows.reverse()
    picture = Image.new('RGBA', (width, abs(height)))
    picture.putdata([(0, 0, 0, 0) if p == TRANSPARENT else (*rgb(p), 255) for row in rows for p in row])
    return picture


def mb_pixels(picture):
    """RGB565 rows top-down; alpha below 128 is the transparent key, opaque 0xF81F is nudged off it."""
    result = []
    for r, g, b, a in picture.convert('RGBA').getdata():
        if a < 128:
            result.append(TRANSPARENT)
        else:
            value = rgb565(r, g, b)
            result.append(0xf83f if value == TRANSPARENT else value)
    width = picture.width
    return [result[y*width:(y+1)*width] for y in range(picture.height)]


def rle_row(row):
    """Literal pixels gather until a run of 2+, the row end or 255 literals; that pixel (run) closes the packet."""
    out = bytearray()
    literals = []
    i = 0
    while i < len(row):
        run = 1
        while i+run < len(row) and row[i+run] == row[i] and run < 255:
            run += 1
        if run >= 2 or i+run == len(row) or len(literals) == 255:
            out += bytes((run, len(literals)))
            out += struct.pack(f'>{len(literals)}H', *literals)
            out += struct.pack('>H', row[i])
            literals = []
        else:
            literals.append(row[i])
        i += run
    return bytes(out+b'\0\0')


def encode_mb(picture):
    """RLE (0x1003) or, when smaller, uncompressed rows (3); the firmware has both."""
    rows = mb_pixels(picture)
    body, compression = b''.join(rle_row(row) for row in reversed(rows)), 0x1003
    pad = b'\0'*((picture.width*2+3)//4*4-picture.width*2)
    raw = b''.join(struct.pack(f'>{len(row)}H', *row)+pad for row in reversed(rows))
    if len(raw) < len(body):
        body, compression = raw, 3
    header = bytearray(MB_HEADER)
    header[0:2] = b'MB'
    struct.pack_into('>IIIIiiHHII', header, 2, MB_HEADER+len(body), 0, MB_HEADER, 0x28,
                     picture.width, picture.height, 1, 16, compression, len(body))
    struct.pack_into('>II', header, 38, 0xb12, 0xb12)
    struct.pack_into('>III', header, 54, 0xf800, 0x07e0, 0x001f)
    return bytes(header+body)


def decode_abmp(data, address=0):
    if data[address:address+4] != b'ABMP':
        raise ValueError('ABMP signature missing')
    width, height, opaque, partial = struct.unpack_from('>4H', data, address+4)
    top, left, bottom, right = data[address+12:address+16]
    if not (0 < width <= 2048 and 0 < height <= 2048 and opaque > 0 and left+right < width and top+bottom < height):
        raise ValueError('Invalid ABMP geometry/palette')
    count = opaque+partial-1
    bits = count.bit_length()
    palette_at = address+16
    alpha_at = palette_at+count*2
    data_at = alpha_at+(partial+1)//2*2
    active_width, active_height = width-left-right, height-top-bottom
    byte_count = (active_width*active_height*bits+15)//16*2
    palette = struct.unpack_from(f'>{count}H', data, palette_at)
    alpha = data[alpha_at:alpha_at+partial]
    words = struct.unpack_from(f'>{byte_count//2}H', data, data_at)
    pixels = [(0, 0, 0, 0)]*(width*height)
    for k in range(active_width*active_height):
        index = 0
        for shift in range(bits):
            bit = k*bits+shift
            index |= (words[bit//16] >> (bit % 16) & 1) << shift
        if not index:
            continue
        if index > count:
            raise ValueError('ABMP palette index outside table')
        opacity = 255 if index < opaque else alpha[index-opaque]
        pixels[(top+k//active_width)*width+left+k % active_width] = (*rgb(palette[index-1]), opacity)
    picture = Image.new('RGBA', (width, height))
    picture.putdata(pixels)
    return picture


def encode_abmp(picture):
    picture = picture.convert('RGBA')
    width, height = picture.size
    pixels = list(picture.getdata())
    keys = [None if a == 0 else (rgb565(r, g, b), a) for r, g, b, a in pixels]
    rows = [y for y in range(height) if any(keys[y*width:(y+1)*width])]
    columns = [x for x in range(width) if any(keys[y*width+x] for y in range(height))]
    if not rows:
        rows, columns = [0], [0]  # fully transparent: one empty pixel
    top, bottom = rows[0], height-1-rows[-1]
    left, right = columns[0], width-1-columns[-1]
    opaque_colours = sorted({k[0] for k in keys if k and k[1] == 255})
    partials = sorted({k for k in keys if k and k[1] < 255})
    opaque = len(opaque_colours)+1
    count = opaque+len(partials)-1
    if count >= 65536:
        raise ValueError('Too many colours for ABMP')
    index = {(c, 255): i+1 for i, c in enumerate(opaque_colours)}
    index.update({k: opaque+i for i, k in enumerate(partials)})
    bits = max(1, count.bit_length())
    active = [keys[y*width+x] for y in range(top, height-bottom) for x in range(left, width-right)]
    words = [0]*((len(active)*bits+15)//16)
    for k, key in enumerate(active):
        value = index[key] if key else 0
        for shift in range(bits):
            if value >> shift & 1:
                bit = k*bits+shift
                words[bit//16] |= 1 << (bit % 16)
    out = bytearray(b'ABMP')
    out += struct.pack('>4H', width, height, opaque, len(partials))
    out += bytes((top, left, bottom, right))
    out += struct.pack(f'>{count}H', *(opaque_colours+[c for c, _ in partials]))
    alpha = bytes(a for _, a in partials)
    out += alpha+b'\0'*(len(alpha) % 2)
    out += struct.pack(f'>{len(words)}H', *words)
    return bytes(out)


DECODERS = {'0x1': decode_mb, '0x30c': decode_abmp}
ENCODERS = {'0x1': encode_mb, '0x30c': encode_abmp}


def encode(picture, kind):
    """Bytes of picture in the firmware format of record type kind ('0x1' or '0x30c')."""
    return ENCODERS[kind](picture)
