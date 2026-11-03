"""Extract every image of the MMI resource modules: PNG files, a JSON list and contact sheets.

Run as python -m tools.extract_images [--firmware PS]. Writes to
analysis/firmware-resources/images/: <module>/<index>.png, images.json and
sheet-<module>.png. Reads the PS only.

Module layout: header (u32 0x60, image count, ...), 12-byte records from +0x68
(u16 width, u16 height, u16 type, u16 0, u32 size) and at header +0x64 the
offset of a u32 table of image offsets, all relative to the module start.
Types: 0x1 MB (BMP-like header, big-endian fields, RGB565, compression 0x1003
row RLE, 0xF81F transparent), 0x30c ABMP (palette with alpha, see
CLOCK_DIGITS_RU.md), 0x20a SJPG (JPEG without headers), others are listed only.
"""
import argparse
import json
import struct
from pathlib import Path

from PIL import Image, ImageDraw

from emulator.firmware import ROOT
from .map_resources import decode_sjpg, modules
from .image_codec import decode_abmp, decode_mb

DIRECTORY = ROOT/'analysis/firmware-resources/images'
FIRMWARE = ROOT/'analysis/firmware-patches/cut/b220-cut.bin'
# Icon groups of the main module: index ranges (inclusive) found by image size
# and checked on the contact sheets; ICONS_RU.md describes them.
GROUPS = {
    'status-bar': (0, 176),
    'dialer-keys': (194, 237),
    'submenu-29': (242, 294),
    'clock-digits': (311, 323),
    'main-menu-45x40': (339, 359),
    'audio-player': (363, 420),
    'dialogs': (528, 537),
    'list-16': (543, 652),
}


def module_name(entry, index):
    return (entry['label'] or f'module-{index}').split(':')[0].replace(',', '').replace(' ', '-')


def catalog(image):
    """Every image record: module, index, address, width, height, type, size and the icon group, if any."""
    found = []
    for module_index, module in enumerate(modules(image)):
        start = int(module['start'], 16)
        name = module_name(module, module_index)
        table = start+struct.unpack_from('>I', image, start+0x64)[0]
        for index, record in enumerate(module['images']):
            address = start+struct.unpack_from('>I', image, table+index*4)[0]
            entry = dict(module=name, index=index, address=hex(address), width=record['width'],
                         height=record['height'], type=record['type'], size=record['size'])
            group = next((g for g, (first, last) in GROUPS.items() if name == 'main' and first <= index <= last), None)
            if group:
                entry['group'] = group
            found.append(entry)
    return found


def decode(image, entry):
    """RGBA picture of a catalog entry, None for formats not decoded yet."""
    address = int(entry['address'], 16)
    if entry['type'] == '0x1':
        picture = decode_mb(image, address)
    elif entry['type'] == '0x30c':
        picture = decode_abmp(image, address)
    elif entry['type'] == '0x20a':
        picture = decode_sjpg(image, address, entry['size']).convert('RGBA')
    else:
        return None
    if picture.size != (entry['width'], entry['height']):
        raise ValueError(f'decoded {picture.size}, table says {entry["width"]}x{entry["height"]}')
    return picture


def extract(firmware, directory):
    image = firmware.read_bytes()
    found = catalog(image)
    for entry in found:
        try:
            picture = decode(image, entry)
            if picture is None:
                entry['note'] = 'format not decoded'
                continue
            folder = directory/entry['module']
            folder.mkdir(parents=True, exist_ok=True)
            picture.save(folder/f'{entry["index"]:03d}.png')
            entry['png'] = f'{entry["module"]}/{entry["index"]:03d}.png'
        except (ValueError, struct.error) as error:
            entry['error'] = str(error)
    for name in dict.fromkeys(e['module'] for e in found):
        sheet(directory, name, [e for e in found if e['module'] == name])
    for group in GROUPS:
        sheet(directory, f'main-{group}', [e for e in found if e.get('group') == group])
    (directory/'images.json').write_text(json.dumps(found, indent=1, ensure_ascii=False), encoding='utf-8')
    return found


def sheet(directory, name, entries, columns=10, cell=64):
    """Contact sheet: each image scaled to fit a cell (never enlarged above 2x), index below."""
    shown = [e for e in entries if 'png' in e]
    if not shown:
        return
    rows = (len(shown)+columns-1)//columns
    board = Image.new('RGB', (columns*cell, rows*(cell+12)), (60, 60, 60))
    draw = ImageDraw.Draw(board)
    for n, entry in enumerate(shown):
        picture = Image.open(directory/entry['png'])
        scale = min(2, (cell-4)/picture.width, (cell-4)/picture.height)
        if scale != 1:
            picture = picture.resize((max(1, int(picture.width*scale)), max(1, int(picture.height*scale))), Image.NEAREST)
        x, y = n % columns*cell, n//columns*(cell+12)
        draw.rectangle((x+1, y+1, x+cell-2, y+cell-2), fill=(30, 30, 30))
        board.paste(picture, (x+(cell-picture.width)//2, y+(cell-picture.height)//2), picture)
        draw.text((x+2, y+cell), f"{entry['index']} {entry['width']}x{entry['height']}", fill=(220, 220, 220))
    board.save(directory/f'sheet-{name}.png')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--firmware', type=Path, default=FIRMWARE)
    parser.add_argument('--directory', type=Path, default=DIRECTORY)
    args = parser.parse_args()
    found = extract(args.firmware, args.directory)
    decoded = sum('png' in e for e in found)
    errors = [e for e in found if 'error' in e]
    print(f'{len(found)} images, {decoded} decoded, {len(errors)} errors -> {args.directory}')
    for e in errors[:10]:
        print(' ', e['module'], e['index'], e['type'], e['error'])


if __name__ == '__main__':
    main()
