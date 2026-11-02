"""Rasterize a local narrow font into the four native B220 bitmap sets."""
import argparse
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import struct

from PIL import Image, ImageDraw, ImageFont

from emulator.firmware import ROOT, load_firmware, protect_outputs

DIRECTORY = ROOT/'analysis/firmware-patches/narrow-font'
DEFAULT_SOURCE = ROOT/'analysis/firmware-patches/sd-app/sd-app.bin'
DEFAULT_FONT = Path('C:/Windows/Fonts/ARIALN.TTF')
# The pinned K550 Regular font's monochrome Cyrillic K is one row above its
# Latin K and the shared baseline at these two tested raster sizes. Do not
# bottom-align all glyphs: that would destroy descenders and accents.
BASELINE_SHIFTS = {
    ('a30e2289ea0c694121e211eaf95a4a5f97ce4479825b25393db089d2ec7fceaa', 10, 0x041a): 1,
    ('a30e2289ea0c694121e211eaf95a4a5f97ce4479825b25393db089d2ec7fceaa', 12, 0x041a): 1,
}
LATIN = tuple(c for a, b in ((0x20, 0x7e), (0xa0, 0xbf), (0xd7, 0xd7),
    (0xf7, 0xf7), (0x2013, 0x2014), (0x2018, 0x2019), (0x201c, 0x201d),
    (0x2026, 0x2026), (0x2039, 0x203a), (0x20ac, 0x20ac), (0x2116, 0x2116),
    (0x25cf, 0x25cf), (0x300a, 0x300b)) for c in range(a, b+1))
CYRILLIC = tuple(range(0x400, 0x487)) + tuple(range(0x488, 0x500))


@dataclass(frozen=True)
class FontSet:
    name: str
    widths: int
    offsets: int
    bitmap: int
    end: int
    height: int
    codepoints: tuple
    digest: str


SETS = (
    FontSet('latin16', 0x6eb6f4, 0x6eb784, 0x6eb9c4, 0x6ec20e, 16, LATIN,
            'ea6d22ef92056dcb6676ac27e424feb81330b1dbb86457858cd91d3ec1ba428c'),
    FontSet('latin14', 0x6ec21a, 0x6ec2ac, 0x6ec4ec, 0x6ecba5, 14, LATIN,
            'ca52d29148bb0198f16043390dd74f14d5f3a96444c5ef5c16c9188fbae79e3d'),
    FontSet('cyrillic16', 0x6ecbb0, 0x6eccb0, 0x6ed0b0, 0x6ee378, 16, CYRILLIC,
            'b4e7330fb46160f77720078267d4b1fdf8ec417d92c2f293e4d0a7b1839c4643'),
    FontSet('cyrillic14', 0x6ee38c, 0x6ee48c, 0x6ee88c, 0x6ef758, 14, CYRILLIC,
            '70d94cd81a749fe9f63c9a52e7cebbc4414847ae66b30967c981149ed08643c6'),
)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def glyph(image, fontset, index, height):
    width = image[fontset.widths+index]
    offset = struct.unpack_from('>I', image, fontset.offsets+index*4)[0]
    start = fontset.bitmap+offset
    result = Image.new('1', (width, height))
    for bit in range(width*height):
        if image[start+bit//8] & (0x80 >> (bit % 8)):
            result.putpixel((bit % width, bit//width), 1)
    return result


def pack(bitmap):
    # Native rows continue at the next bit, not the next byte (Pillow tobytes
    # pads every row and therefore cannot be used for this firmware format).
    result = bytearray((bitmap.width*bitmap.height+7)//8)
    for bit, value in enumerate(bitmap.getdata()):
        if value:
            result[bit//8] |= 0x80 >> (bit % 8)
    return bytes(result)


def mask(font, char):
    pixels, offset = font.getmask2(char, mode='1', anchor='ls')
    return Image.frombytes('L', pixels.size, bytes(pixels)), offset


def raster(font, char, height, max_width, missing, original, baseline_shift=0):
    ink, (left, top) = mask(font, char)
    if (ink.size, ink.tobytes()) == missing:
        # Preserve uncommon characters absent from the selected desktop font.
        width = max(1, min(max_width, round(max_width*height/original.height)))
        return original.resize((width, height), Image.Resampling.NEAREST), True
    bounds = ink.getbbox()
    advance = max(1, math.ceil(font.getlength(char)))
    if bounds:
        left = max(0, left+bounds[0])
        top += bounds[1]+height-2+baseline_shift  # shared baseline, two rows for descenders
        ink = ink.crop(bounds).convert('1')
        advance = max(advance, left+ink.width+1)
    width = min(max_width, advance)
    result = Image.new('1', (width, height))
    if bounds:
        # A few accented/extended glyphs exceed the cell. Fit the entire ink,
        # including descenders, rather than dropping rows or writing next glyph.
        left = min(left, max(0, width-2))
        ink = ink.resize((max(1, min(ink.width, width-left-1 if width>1 else 1)),
                          min(ink.height, height)), Image.Resampling.NEAREST)
        top = max(0, min(top, height-ink.height))
        result.paste(ink, (left, top))
    return result, False


def build(image, font_path=DEFAULT_FONT, large_height=14, small_height=12):
    if not (8 <= small_height <= 14 and small_height <= large_height <= 16):
        raise ValueError('Expected 8 <= small height <= 14 and small <= large <= 16')
    output = bytearray(image)
    allowed = bytearray(len(image))
    font_digest = sha(font_path.read_bytes())
    sets = []
    for s in SETS:
        if sha(image[s.widths:s.end]) != s.digest:
            raise ValueError(f'Unsupported or already modified font block: {s.name}')
        height = large_height if s.height == 16 else small_height
        font = ImageFont.truetype(str(font_path), height-2)
        missing_ink, _ = mask(font, '\uffff')
        missing = missing_ink.size, missing_ink.tobytes()
        for required in 'AОЁЙя':
            ink, _ = mask(font, required)
            if (ink.size, ink.tobytes()) == missing:
                raise ValueError('Selected font must contain Latin and Russian letters, including Ё/Й')
        records = []
        for index, cp in enumerate(s.codepoints):
            old = glyph(image, s, index, s.height)
            offset = struct.unpack_from('>I', image, s.offsets+index*4)[0]
            start = s.bitmap+offset
            capacity = (old.width*s.height+7)//8
            end = (s.bitmap+struct.unpack_from('>I', image, s.offsets+(index+1)*4)[0]
                   if index+1 < len(s.codepoints) else s.end)
            if start+capacity != end:
                raise ValueError(f'Unexpected glyph slot in {s.name} U+{cp:04X}')
            baseline_shift = BASELINE_SHIFTS.get((font_digest, height-2, cp), 0)
            bitmap, fallback = (raster(font, chr(cp), height, old.width, missing, old, baseline_shift)
                                if old.width else (Image.new('1', (0, height)), False))
            data = pack(bitmap)
            if len(data)>capacity:
                raise ValueError('Replacement exceeds original glyph slot')
            output[s.widths+index] = bitmap.width
            output[start:end] = data + bytes(capacity-len(data))
            allowed[s.widths+index] = 1
            allowed[start:end] = bytes([1])*capacity
            records.append(dict(codepoint=f'U+{cp:04X}', old_width=old.width,
                                width=bitmap.width, offset=offset, fallback=fallback,
                                baseline_shift=baseline_shift))
        sets.append(dict(name=s.name, old_height=s.height, height=height,
                         raster_size=height-2, glyphs=records))
    for address, before, height in ((0x5b8716, b'\x20\x10', large_height),
                                    (0x5b871e, b'\x20\x0e', small_height)):
        if image[address:address+2] != before:
            raise ValueError('Unexpected native font-height instruction')
        output[address:address+2] = bytes((0x20, height))  # Thumb MOVS r0,#height
        allowed[address:address+2] = b'\x01\x01'
    if len(output) != len(image) or any(a != b and not ok for a,b,ok in zip(image,output,allowed)):
        raise ValueError('Changes outside verified font slots and height immediates')
    manifest = dict(format='b220-narrow-font', version=1, source_sha256=sha(image),
        output_sha256=sha(output), font_path=str(font_path.resolve()),
        font_sha256=font_digest, font_name=font.getname(), sets=sets,
        height_instructions=['0x5b8716', '0x5b871e'], offsets_unchanged=True,
        preview='Host decoding of actual ROM bitmaps; not an emulator screenshot')
    return bytes(output), manifest


def text_bitmap(image, text, height, original_height):
    characters = []
    for char in text:
        for s in SETS:
            if s.height == original_height and ord(char) in s.codepoints:
                characters.append(glyph(image, s, s.codepoints.index(ord(char)), height))
                break
    result = Image.new('1', (max(1, sum(g.width for g in characters)), height))
    x = 0
    for g in characters:
        result.paste(g, (x, 0)); x += g.width
    return result


def preview(before, after, manifest, destination):
    # Display actual repacked ROM glyphs, including their native advance widths.
    canvas = Image.new('RGB', (756, 510), '#20242a')
    draw = ImageDraw.Draw(canvas)
    lines = ('Мультимедиа', 'Видеоплеер', 'FM-радио', 'Галерея', 'Книги',
             'Медиатека', 'ООО ооо 000', 'Ёё Йй Дд Цц Щщ', '0123456789', 'Подтв.     Выход')
    for column, image in enumerate((before, after)):
        x = 14+column*378
        label = ('ORIGINAL 16 / 14' if column == 0 else
                 f"{' '.join(manifest['font_name'])} {manifest['sets'][0]['height']}/{manifest['sets'][1]['height']}")
        draw.text((x, 7), label, fill='white')
        panel = Image.new('RGB', (176, 220), 'black')
        height = 16 if column == 0 else manifest['sets'][0]['height']
        for row, line in enumerate(lines):
            bitmap = text_bitmap(image, line, height, 16)
            panel.paste('white', (3, 3+row*21), bitmap)
        canvas.paste(panel.resize((352, 440), Image.Resampling.NEAREST), (x, 28))
        small = 14 if column == 0 else manifest['sets'][1]['height']
        line = text_bitmap(image, 'Мелкий текст: Ёжик 0123', small, 14)
        canvas.paste('white', (x, 477), line.resize((line.width*2, line.height*2), Image.Resampling.NEAREST))
    canvas.save(destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=DEFAULT_SOURCE)
    parser.add_argument('--directory', type=Path, default=DIRECTORY)
    parser.add_argument('--font', type=Path, default=DEFAULT_FONT)
    parser.add_argument('--large-height', type=int, default=14)
    parser.add_argument('--small-height', type=int, default=12)
    parser.add_argument('--prepare-card', action='store_true')
    args = parser.parse_args()
    try:
        profile = json.loads((ROOT/'emulator/profiles/b220-l7a.json').read_text())
        image, identity = load_firmware(profile, args.source)
        paths = [args.directory/n for n in ('source-copy.bin','b220-narrow.bin','manifest.json','comparison.png')]
        protect_outputs(profile, identity, paths, inputs=(args.font,))
        if len({p.resolve() for p in paths}) != len(paths):
            raise ValueError('Output paths must be distinct')
        output, manifest = build(image, args.font, args.large_height, args.small_height)
        args.directory.mkdir(parents=True, exist_ok=True)
        paths[0].write_bytes(image); paths[1].write_bytes(output)
        preview(image, output, manifest, paths[3])
        paths[2].write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
        if args.source.read_bytes() != image:
            raise ValueError('Source PS changed while preparing experiment')
        if args.prepare_card:
            from .install_sd_app import install
            card = ROOT/'analysis/emulator/experiments'/sha(output)/'sd-card.img'
            for name in ('card_demo.b22asm','card_second.b22asm'):
                install(ROOT/'apps'/name, card, ensure=True)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(dict(image=str(paths[1]), sha256=sha(output), font=manifest['font_name'],
                          heights=[args.large_height,args.small_height], preview=str(paths[3])), indent=2))


if __name__ == '__main__':
    main()
