"""Map MMI resource modules, image tables, SJPG wallpapers and the cut-list sizes of the original PS.

Run as python -m tools.map_resources. Reads only the original PS; writes JSON and
PNG previews to analysis/firmware-resources/. SJPG previews use synthesized JPEG
headers (standard Huffman tables, approximate quantization): geometry is exact,
colors are approximate.
"""
import argparse
import io
import json
import re
import struct
from pathlib import Path

from PIL import Image, ImageDraw

from emulator.firmware import ROOT, load_firmware, protect_outputs
from emulator.jpeg import segment, standard_huffman

DIRECTORY = ROOT/'analysis/firmware-resources'
# Image record type field in module tables; 0x30c and 0xff are not decoded yet.
TYPES = {0x1:'MB RLE bitmap',0x20a:'SJPG',0x20d:'JFIF JPEG',0x108:'SGIF',0xf:'unknown 0xf',
         0x30c:'packed bitmap 0x30c',0xff:'unknown 0xff'}
# First distinctive UTF-16 strings of each module identify its owner.
LABELS = (('Sokoban','games: Sokoban'),('World time','tools: calculator, world time, USB, memory'),
          ('Mmi Ui-Test','engineering test'),('Camera Select','camera'),('FM Radio','FM radio'),
          ('eBook reader','ebook'),('Audio player','audio player'),('Alarm1','alarm, schedule'),
          ('Input method','input method'),('WAP settings','browser'),('Write message','SMS'),
          ('Set regular send','MMS'),('Record options','recorder'),('Play list','video'),
          ('Copy all','phonebook'),('Holding','calls'),('Wallpaper switch','settings'),('New folder','file manager'),
          ('Call history','main'))
WALLPAPERS = (0x980dc,0x9d4fc,0xa0c00,0xa42e0)
WALLPAPER_KEPT = 0x97dc8
# Link strings for Media library and Social networks; menu code is not sized.
LINKS = (b'http://wap.zagruzka.com/preloaded/',b'm.vkontakte.ru',b'm.odnoklassniki.ru',
         b'm.facebook.com',b'm.twitter.com',b'm.livejournal.com',b'm.ebuddy.com')


def u32(image, offset):
    return struct.unpack_from('>I', image, offset)[0]


def utf16_strings(data):
    found = []
    for match in re.finditer(rb'(?:[\x00\x04][\x20-\x7e\x10-\x4f]){3,}', data):
        if match.start() % 2:
            continue
        text = match.group().decode('utf-16-be')
        if sum(c.isalpha() for c in text) >= 3:
            found.append(text)
    return found


def modules(image):
    """Module header: u32 0x60, image count, ..., total size at +0x30, 0x68 at +0x60."""
    result = []
    for start in range(0, len(image)-0x80, 4):
        if u32(image, start) != 0x60 or u32(image, start+0x60) != 0x68:
            continue
        count, size = u32(image, start+4), u32(image, start+0x30)
        if not 0 < size < 0x200000 or count >= 2000 or start+size > len(image):
            continue
        records = []
        for index in range(count):
            width, height, kind, zero, length = struct.unpack_from('>HHHHI', image, start+0x68+index*12)
            if zero or not 0 < width <= 640 or not 0 < height <= 640:
                raise ValueError(f'Bad image record {index} in module {start:#x}')
            records.append(dict(width=width, height=height, type=hex(kind), size=length))
        strings = utf16_strings(image[start:start+size])
        # A module also quotes other modules' names; the earliest own string wins.
        hits = [(next((i for i, s in enumerate(strings) if key in s), len(strings)), name) for key, name in LABELS]
        position, label = min(hits)
        label = label if position < len(strings) else None
        result.append(dict(start=hex(start), end=hex(start+size), size=size, label=label,
                           image_bytes=sum(r['size'] for r in records), images=records,
                           strings=len(strings), first_strings=strings[:6]))
    return result


def sjpg_header(image, offset):
    if image[offset:offset+4] != b'SJPG':
        raise ValueError(f'No SJPG at {offset:#x}')
    height, width = struct.unpack_from('>HH', image, offset+4)
    return width, height


def decode_sjpg(image, offset, size, quality=80):
    """SJPG: 'SJPG', u16 height, u16 width, u16 3, u16 4, headerless 4:2:2 baseline scan."""
    width, height = sjpg_header(image, offset)
    stream = io.BytesIO()
    Image.new('RGB', (8, 8)).save(stream, format='JPEG', quality=quality)
    data, position, quant = stream.getvalue(), 2, b''
    while data[position:position+2] != b'\xff\xda':
        length = int.from_bytes(data[position+2:position+4], 'big')
        if data[position+1] == 0xdb:
            quant += data[position:position+2+length]
        position += 2+length
    frame = bytes([8])+height.to_bytes(2,'big')+width.to_bytes(2,'big')+bytes([3,1,0x21,0,2,0x11,1,3,0x11,1])
    header = b'\xff\xd8'+quant+segment(0xc0, frame)
    for key, table in sorted(standard_huffman().items()):
        header += segment(0xc4, table)
    header += segment(0xda, bytes([3,1,0x00,2,0x11,3,0x11,0,63,0]))
    body = image[offset+12:offset+size]
    picture = Image.open(io.BytesIO(header+body+(b'' if body.endswith(b'\xff\xd9') else b'\xff\xd9')))
    picture.load()
    return picture.convert('RGB')


def cut_list(image, mods):
    by_label = {m['label']:m for m in mods if m['label']}
    kept = sjpg_header(image, WALLPAPER_KEPT)
    walls = []
    for offset, following in zip(WALLPAPERS, WALLPAPERS[1:]+(0xa9a90,)):
        walls.append(dict(offset=hex(offset), size=following-offset, geometry=sjpg_header(image, offset)))
    tools = by_label['tools: calculator, world time, USB, memory']
    world = [r for r in tools['images'] if (r['width'], r['height']) in ((176,80),(176,1),(1,80),(5,11))]
    games = by_label['games: Sokoban']
    links = {}
    for link in LINKS:
        links[link.decode()] = [hex(m.start()) for m in re.finditer(re.escape(link), image)]
    return dict(
        wallpapers=dict(slots=walls, total=sum(w['size'] for w in walls), kept=dict(offset=hex(WALLPAPER_KEPT), geometry=kept),
                        note='Each slot keeps record size; a tiny valid image goes first, the tail is usable space'),
        world_time=dict(module=tools['start'], images=world, image_bytes=sum(r['size'] for r in world),
                        note='Code and city tables are not sized'),
        sokoban=dict(module=games['start'], module_bytes=games['size'],
                     note='Game code near 0x60c9ec..0x61039a, rough estimate 2.5-15 KB, not delimited'),
        links=dict(strings=links, note='Media library / Social networks: menu entries and URLs, a few KB at most'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=DIRECTORY)
    args = parser.parse_args()
    profile = json.loads((ROOT/'emulator/profiles/b220-l7a.json').read_text(encoding='utf-8'))
    image, identity = load_firmware(profile)
    outputs = [args.directory/'resources.json', args.directory/'fullscreen.png']
    protect_outputs(profile, identity, outputs)
    mods = modules(image)
    report = dict(ps_sha256=identity['sha256'], module_count=len(mods), resource_bytes=sum(m['size'] for m in mods),
                  modules=mods, cut_list=cut_list(image, mods))
    args.directory.mkdir(parents=True, exist_ok=True)
    tiles = [(f'{o:x}', decode_sjpg(image, o, s['size']))
             for o, s in ((int(w['offset'],0), w) for w in report['cut_list']['wallpapers']['slots'])]
    sheet = Image.new('RGB', (sum(t.width+6 for _, t in tiles), 240), (60,60,60))
    draw, x = ImageDraw.Draw(sheet), 0
    for name, tile in tiles:
        sheet.paste(tile, (x, 16)); draw.text((x, 2), name, fill=(255,255,0)); x += tile.width+6
    sheet.save(outputs[1])
    outputs[0].write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding='utf-8')
    cut = report['cut_list']
    print(json.dumps(dict(modules=len(mods), resource_bytes=report['resource_bytes'],
                          wallpapers=cut['wallpapers']['total'], world_time_images=cut['world_time']['image_bytes'],
                          sokoban_module=cut['sokoban']['module_bytes'], output=str(args.directory)), indent=1))


if __name__ == '__main__':
    main()
