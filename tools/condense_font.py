"""Experiment: the stock bitmap font made narrower, run as python -m tools.condense_font.

Inside each glyph's ink, columns equal to their left neighbour are removed (nearest
the middle first) until the ink is RATIO of its width: stems stay one pixel and
sharp, the empty column after the ink (letter spacing) stays. Blank glyphs (space)
shrink by the same ratio. The narrower glyph is repacked into its own slot and
the width table follows; offsets and heights stay. The stock 16/14 heights make
tools.build_cut smooth these bitmaps (Scale2x edges).
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct

from PIL import Image

from emulator.firmware import ROOT, load_firmware, protect_outputs
from .build_font_patch import SETS, glyph, pack

DIRECTORY = ROOT/'analysis/firmware-patches/condensed-font'
DEFAULT_SOURCE = ROOT/'analysis/firmware-patches/sd-app/sd-app.bin'
RATIO = 0.8


def sha(data):
    return hashlib.sha256(data).hexdigest()


def condense(bits, ratio):
    w, h = bits.size
    columns = [tuple(bits.getpixel((x, y)) for y in range(h)) for x in range(w)]
    ink = [x for x in range(w) if any(columns[x])]
    if not ink:
        width = max(1, round(w*ratio)) if w else 0
        return Image.new('1', (width, h))
    low, high = ink[0], ink[-1]
    want = max(1, round((high-low+1)*ratio))
    while high-low+1 > want:
        middle = (low+high)/2
        same = [x for x in range(low+1, high+1) if columns[x] == columns[x-1]]
        if not same:
            break
        del columns[min(same, key=lambda x: abs(x-middle))]
        high -= 1
    result = Image.new('1', (len(columns), h))
    for x, column in enumerate(columns):
        for y, value in enumerate(column):
            if value:
                result.putpixel((x, y), 1)
    return result


def build(image, ratio=RATIO):
    if image[0x5b8716:0x5b8718] != b'\x20\x10' or image[0x5b871e:0x5b8720] != b'\x20\x0e':
        raise ValueError('Expected the stock 16/14 font heights')
    output = bytearray(image)
    sets = []
    for s in SETS:
        if sha(image[s.widths:s.end]) != s.digest:
            raise ValueError(f'Unsupported or already modified font block: {s.name}')
        before = after = 0
        for index in range(len(s.codepoints)):
            width = image[s.widths+index]
            if not width:
                continue
            offset = struct.unpack_from('>I', image, s.offsets+index*4)[0]
            start = s.bitmap+offset
            capacity = (width*s.height+7)//8
            narrow = condense(glyph(image, s, index, s.height), ratio)
            data = pack(narrow)
            output[s.widths+index] = narrow.width
            output[start:start+capacity] = data+bytes(capacity-len(data))
            before += width; after += narrow.width
        sets.append(dict(name=s.name, widths_before=before, widths_after=after))
    return bytes(output), dict(format='b220-condensed-font', version=1, ratio=ratio, source_sha256=sha(image),
                               output_sha256=sha(output), sets=sets)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=DEFAULT_SOURCE)
    parser.add_argument('--directory', type=Path, default=DIRECTORY)
    parser.add_argument('--ratio', type=float, default=RATIO)
    args = parser.parse_args()
    try:
        profile = json.loads((ROOT/'emulator/profiles/b220-l7a.json').read_text())
        image, identity = load_firmware(profile, args.source)
        paths = [args.directory/n for n in ('b220-condensed.bin', 'manifest.json')]
        protect_outputs(profile, identity, paths)
        output, manifest = build(image, args.ratio)
        args.directory.mkdir(parents=True, exist_ok=True)
        paths[0].write_bytes(output)
        paths[1].write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(dict(image=str(paths[0]), sha256=manifest['output_sha256'], sets=manifest['sets']), indent=2))


if __name__ == '__main__':
    main()
