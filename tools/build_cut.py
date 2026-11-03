"""Remove unused built-in content from a separate white-clock PS; run as python -m tools.build_cut.

Step 1, wallpapers: the four built-in pictures are replaced by the default black
one, the Display -> Wallpaper list keeps only "Default" and "Other wallpaper",
and the four resource slots become free ROM (0xFF after a copy of the black SJPG).
Image records keep their sizes, so every following resource stays in place.
Steps 2-3: Media library leaves Multimedia, Social networks leaves the main menu
(items and position icons move up), and its idle shortcut opens SD Apps.
The power-on GIF becomes a black screen with dots; World time leaves Tools
and its world map becomes a small black bitmap.
Freed regions are listed in the manifest for later code or data.
Last, icons/<module>/<index>.png (tools.icon_studio) replace firmware images;
one heavier than the original moves into freed ROM (ICONS_RU.md).
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import struct

from PIL import Image, ImageDraw

from emulator.firmware import ROOT, load_firmware, protect_outputs
from .map_resources import WALLPAPERS, WALLPAPER_KEPT, sjpg_header

DIRECTORY = ROOT/'analysis/firmware-patches/cut'
DEFAULT_SOURCE = ROOT/'analysis/firmware-patches/white-clock/b220-white-clock.bin'
SLOTS_END = 0xa9a90
BLACK_SIZE = 785
# Image IDs used by the idle screen and preview: table[stored index - 1].
WALLPAPER_IDS = 0x6d05e0
BLACK_ID = 0x400002ac


def sha(data):
    return hashlib.sha256(data).hexdigest()


def thumb_b(at, target):
    offset = target-(at+4)
    if offset & 1 or not -2048 <= offset >> 1 < 1024:
        raise ValueError(f'Branch out of range at {at:#x}')
    return 0xe000 | ((offset >> 1) & 0x7ff)


# (address, original halfword, new halfword, reason); Thumb, big-endian.
WALLPAPER_CODE = (
    (0x1c9ade, 0x2106, 0x2102, 'list window: 2 items instead of 6'),
    (0x1c9b1c, 0x2805, 0x2801, 'OK on item 1 opens "Other wallpaper"'),
    (0x1c9b50, 0xab00, 0x2100, 'refocus: built-in wallpaper is always item 0'),
    (0x1c9b52, 0x8a98, thumb_b(0x1c9b52, 0x1c9b5e), 'refocus: skip stored index'),
    (0x1c9b5c, 0x2105, 0x2101, 'refocus: "Other wallpaper" is item 1'),
    (0x4b63a4, 0x00a0, thumb_b(0x4b63a4, 0x4b63ba), 'list fill: skip "Wallpaper 1..4"'),
    (0x4b63ee, 0xab00, 0x2400, 'list fill: built-in wallpaper is always item 0'),
    (0x4b63f0, 0x8a98, thumb_b(0x4b63f0, 0x4b63fc), 'list fill: skip stored index'),
    (0x4b63fa, 0x2405, 0x2401, 'list fill: "Other wallpaper" is item 1'),
)


def cut_wallpapers(image, output):
    changes, free = [], []
    for address, old, new, reason in WALLPAPER_CODE:
        if struct.unpack_from('>H', image, address)[0] != old:
            raise ValueError(f'Unexpected instruction at {address:#x}')
        struct.pack_into('>H', output, address, new)
        changes.append(dict(start=hex(address), size=2, kind=reason))
    ids = [struct.unpack_from('>I', image, WALLPAPER_IDS+i*4)[0] for i in range(5)]
    if ids != list(range(BLACK_ID, BLACK_ID+5)):
        raise ValueError('Unexpected wallpaper image table')
    for index in range(1, 5):
        struct.pack_into('>I', output, WALLPAPER_IDS+index*4, BLACK_ID)
    changes.append(dict(start=hex(WALLPAPER_IDS+4), size=16, kind='wallpaper image IDs 2..5 -> default black'))
    black = image[WALLPAPER_KEPT:WALLPAPER_KEPT+BLACK_SIZE]
    if sjpg_header(image, WALLPAPER_KEPT) != (176, 220) or image[WALLPAPER_KEPT+BLACK_SIZE:WALLPAPER_KEPT+BLACK_SIZE+3] != b'\xad\xad\xad':
        raise ValueError('Unexpected default black wallpaper')
    for start, end in zip(WALLPAPERS, WALLPAPERS[1:]+(SLOTS_END,)):
        if sjpg_header(image, start) != (176, 220):
            raise ValueError(f'No wallpaper at {start:#x}')
        # 4-byte aligned free tail after a decodable black copy.
        tail = (start+BLACK_SIZE+3) & ~3
        output[start:start+BLACK_SIZE] = black
        output[start+BLACK_SIZE:end] = b'\xff'*(end-start-BLACK_SIZE)
        changes.append(dict(start=hex(start), size=end-start, kind='wallpaper slot: black copy + free tail'))
        free.append(dict(start=hex(tail), end=hex(end), size=end-tail, origin=f'wallpaper {start:#x}'))
    return changes, free


# Menu group: u32 items, u32 style, u32 title, 3 softkeys, u16 count, u16 0.
# Menu item: 40 bytes; u32 menu id, ..., u32 text at +0x14, u32 icon at +0x18.
GROUP_COUNT, ITEM_SIZE = 0x18, 40
MULTIMEDIA = 0x6db280
MAIN_MENU = (0x6db0c0, 0x6db0dc)  # grid and list views share one item array
MEDIA_LIBRARY_TEXT, SOCIAL_TEXT = 0x400004d2, 0x400004cc
# Main menu grid icons follow item positions, not items: normal and focused.
MAIN_ICONS = (0x70d46c, 0x70d49c)


def group(image, address):
    items, style, title = struct.unpack_from('>3I', image, address)
    count, zero = struct.unpack_from('>HH', image, address+GROUP_COUNT)
    if zero or not 0 < count <= 30:
        raise ValueError(f'Unexpected menu group at {address:#x}')
    return items, count


def remove_item(image, output, groups, text, reason):
    """Drop one item: later items move up, the freed last slot is zeroed."""
    items, count = group(image, groups[0])
    if any(group(image, g) != (items, count) for g in groups):
        raise ValueError('Menu views disagree')
    texts = [struct.unpack_from('>I', image, items+i*ITEM_SIZE+0x14)[0] for i in range(count)]
    if texts.count(text) != 1:
        raise ValueError(f'Menu item {text:#x} not found once')
    index = texts.index(text)
    start, end = items+index*ITEM_SIZE, items+count*ITEM_SIZE
    output[start:end-ITEM_SIZE] = image[start+ITEM_SIZE:end]
    output[end-ITEM_SIZE:end] = bytes(ITEM_SIZE)
    changes = [dict(start=hex(start), size=end-start, kind=f'{reason}: items after {index} move up')]
    for address in groups:
        struct.pack_into('>H', output, address+GROUP_COUNT, count-1)
        changes.append(dict(start=hex(address+GROUP_COUNT), size=2, kind=f'{reason}: {count} -> {count-1} items'))
    return changes, index, count


def remove_icon(image, output, table, index, count, reason):
    """Shift position icons; the unused last position keeps its old icon."""
    icons = list(struct.unpack_from(f'>{count}I', image, table))
    if any(icon >> 16 != 0x4000 for icon in icons):
        raise ValueError(f'Unexpected icon table at {table:#x}')
    icons = icons[:index]+icons[index+1:]+icons[-1:]
    struct.pack_into(f'>{count}I', output, table, *icons)
    return dict(start=hex(table), size=count*4, kind=f'{reason}: icons after {index} move up')


# Idle shortcut bar: functions 0..18 dispatch through this pointer table
# (copied by 0x278a6c); the six chosen numbers live in NV item 0xd003e,
# default [0, 17, 1, 3, 4, 2]. Number 17 opened Social networks.
SHORTCUT_ACTIONS, SOCIAL_SHORTCUT = 0x6e24b4, 17
SOCIAL_ACTION = 0x278c77
SD_APPS_ENTRY = 0x60eee9  # Games entry, already redirected to the SD Apps list


def cut_shortcut(image, output):
    address = SHORTCUT_ACTIONS+SOCIAL_SHORTCUT*4
    if struct.unpack_from('>I', image, address)[0] != SOCIAL_ACTION:
        raise ValueError('Unexpected Social networks shortcut action')
    if image[SD_APPS_ENTRY-1:SD_APPS_ENTRY+3] != bytes.fromhex('4b004718'):
        raise ValueError('SD Apps entry is missing; build on the SD-app chain')
    struct.pack_into('>I', output, address, SD_APPS_ENTRY)
    return dict(start=hex(address), size=4, kind='idle shortcut 17: Social networks -> SD Apps (icon unchanged)')


# Power-on animation: SGIF record (12-byte header + GIF89a 176x220, 10 frames, 3.36 s).
BOOT_SLOT, BOOT_END = 0x86934, 0x972ee
BOOT_HEADER = bytes.fromhex('53474946 0001 00dc 00b0 0000')  # 'SGIF', 1, height, width, 0
DOTS_Y = 108  # three white 4x4 dots centred on a black screen
DOT_STATES = (0, 1, 2, 3, 0, 1, 2, 3)
DOT_DURATIONS = (360, 400, 400, 400, 400, 400, 400, 600)  # same 3.36 s as the original


def boot_gif(original):
    """Black screen, then three blinking dots; later frames update only the dots."""
    source = Image.open(io.BytesIO(original))
    if source.size != (176, 220) or source.n_frames != 10:
        raise ValueError('Unexpected power-on GIF')

    def frame(dots):
        picture = Image.new('P', (176, 220), 0)
        picture.putpalette([0, 0, 0, 255, 255, 255])
        draw = ImageDraw.Draw(picture)
        for i in range(dots):
            x = 78+i*8
            draw.rectangle((x, DOTS_Y, x+3, DOTS_Y+3), fill=1)
        return picture
    frames = [frame(n) for n in DOT_STATES]
    stream = io.BytesIO()
    frames[0].save(stream, 'GIF', save_all=True, append_images=frames[1:], duration=list(DOT_DURATIONS),
                   loop=0, disposal=1, optimize=False)
    data = stream.getvalue()
    check = Image.open(io.BytesIO(data))
    if check.size != (176, 220) or check.n_frames != len(DOT_STATES):
        raise ValueError('Generated GIF does not round-trip')
    for index in range(1, check.n_frames):
        check.seek(index)
        left, top, right, bottom = check.tile[0][1]
        if top < DOTS_Y or bottom > DOTS_Y+4:
            raise ValueError('Dot frame is not a partial update')
    return data


def cut_boot_animation(image, output):
    if image[BOOT_SLOT:BOOT_SLOT+12] != BOOT_HEADER or image[BOOT_END-1] != 0x3b or image[BOOT_END:BOOT_END+2] != b'\xad\xad':
        raise ValueError('Unexpected power-on animation slot')
    data = boot_gif(image[BOOT_SLOT+12:BOOT_END])
    end = BOOT_SLOT+12+len(data)
    output[BOOT_SLOT+12:end] = data
    output[end:BOOT_END] = b'\xff'*(BOOT_END-end)
    tail = (end+3) & ~3
    return ([dict(start=hex(BOOT_SLOT+12), size=BOOT_END-BOOT_SLOT-12,
                  kind=f'power-on GIF: black screen + blinking dots ({len(data)} bytes) + free tail')],
            [dict(start=hex(tail), end=hex(BOOT_END), size=BOOT_END-tail, origin='power-on animation')])


# World time: Tools item (menu id 0x6d) and the same item in the "Others" group.
TOOLS, OTHERS = 0x6db168, 0x6db264
WORLD_TIME_TEXT = 0x400003bf
# World map, MB RLE 176x80 in the Tools resource module; used only by the
# world time window (image list at 0x3cae38).
WORLD_MAP, WORLD_MAP_SIZE = 0x15e70c, 22850


def solid_mb(header, width, height, color=0):
    """MB RLE bitmap of one color: per row one (repeat=width, literal=0) packet and 00 00."""
    if width > 255:
        raise ValueError('Row too wide for one RLE packet')
    rows = (bytes((width, 0))+struct.pack('>H', color)+b'\0\0')*height
    data = bytearray(header)+rows
    struct.pack_into('>I', data, 2, len(data))
    struct.pack_into('>I', data, 34, len(rows))
    return bytes(data)


def cut_world_time(image, output):
    changes = []
    for address in (TOOLS, OTHERS):
        more, _, _ = remove_item(image, output, (address,), WORLD_TIME_TEXT, f'menu {address:#x} without World time')
        changes += more
    header = image[WORLD_MAP:WORLD_MAP+66]
    if struct.unpack_from('>2sI', header) != (b'MB', WORLD_MAP_SIZE) or struct.unpack_from('>II', header, 18) != (176, 80):
        raise ValueError('Unexpected world map bitmap')
    small = solid_mb(header, 176, 80)
    end = WORLD_MAP+len(small)
    output[WORLD_MAP:end] = small
    output[end:WORLD_MAP+WORLD_MAP_SIZE] = b'\xff'*(WORLD_MAP+WORLD_MAP_SIZE-end)
    tail = (end+3) & ~3
    changes.append(dict(start=hex(WORLD_MAP), size=WORLD_MAP_SIZE, kind=f'world map: black {len(small)}-byte MB + free tail'))
    return changes, [dict(start=hex(tail), end=hex(WORLD_MAP+WORLD_MAP_SIZE),
                          size=WORLD_MAP+WORLD_MAP_SIZE-tail, origin='world map')]


def cut_links(image, output):
    changes, _, _ = remove_item(image, output, (MULTIMEDIA,), MEDIA_LIBRARY_TEXT, 'Multimedia without Media library')
    reason = 'Main menu without Social networks'
    more, index, count = remove_item(image, output, MAIN_MENU, SOCIAL_TEXT, reason)
    changes += more+[remove_icon(image, output, table, index, count, reason) for table in MAIN_ICONS]
    changes.append(cut_shortcut(image, output))
    return changes, []


ICONS = ROOT/'icons'


def take_free(free, size, low):
    """4-aligned space from the freed regions at or above low (offsets in a module are unsigned)."""
    for region in sorted(free, key=lambda r: int(r['start'], 16)):
        start, end = (int(region['start'], 16)+3) & ~3, int(region['end'], 16)
        if start >= low and end-start >= size:
            rest = min(end, (start+size+3) & ~3)
            region['start'] = hex(rest)
            region['size'] = end-rest
            return start
    raise ValueError(f'No free ROM for {size} bytes')


def replace_icons(image, output, free, directory=ICONS):
    """icons/<module>/<index>.png (from tools.icon_studio) replace firmware images of the same size.
    An image that fits stays in its slot; a larger one moves to freed ROM and the module's offset
    table and record size are updated."""
    from .extract_images import catalog
    from .image_codec import ENCODERS
    if not directory.exists():
        return [], []
    entries = {(e['module'], e['index']): e for e in catalog(image)}
    modules = {}
    for start in range(0, len(image)-0x80, 4):
        if struct.unpack_from('>I', image, start)[0] == 0x60 and struct.unpack_from('>I', image, start+0x60)[0] == 0x68:
            modules.setdefault(start, None)
    changes, placed = [], []
    for path in sorted(directory.glob('*/*.png')):
        key = (path.parent.name, int(path.stem))
        entry = entries.get(key)
        if not entry or entry['type'] not in ENCODERS:
            raise ValueError(f'{path}: no MB or ABMP image {key[0]}/{key[1]} in the firmware')
        picture = Image.open(path).convert('RGBA')
        if picture.size != (entry['width'], entry['height']):
            raise ValueError(f'{path}: {picture.size[0]}x{picture.size[1]}, the slot is {entry["width"]}x{entry["height"]}')
        data = ENCODERS[entry['type']](picture)
        address, size = int(entry['address'], 16), entry['size']
        name = f'icon {key[0]}/{key[1]}'
        if len(data) <= size:
            output[address:address+size] = data+b'\xff'*(size-len(data))
            changes.append(dict(start=hex(address), size=size, kind=f'{name}: {len(data)} bytes in place of {size}'))
            placed.append(dict(icon=f'{key[0]}/{key[1]}', bytes=len(data), old_bytes=size, address=hex(address)))
            continue
        # The module that holds this image: the closest module header below it.
        module = max(m for m in modules if m <= address)
        index = key[1]
        table = module+struct.unpack_from('>I', image, module+0x64)[0]
        record = module+0x68+index*12
        if struct.unpack_from('>I', image, table+index*4)[0] != address-module:
            raise ValueError(f'{name}: offset table does not point at the image')
        target = take_free(free, len(data), module)
        output[target:target+len(data)] = data
        struct.pack_into('>I', output, table+index*4, target-module)
        struct.pack_into('>I', output, record+8, len(data))
        changes += [dict(start=hex(target), size=len(data), kind=f'{name}: {len(data)} bytes moved from {address:#x}'),
                    dict(start=hex(table+index*4), size=4, kind=f'{name}: offset table entry'),
                    dict(start=hex(record+8), size=4, kind=f'{name}: record size')]
        placed.append(dict(icon=f'{key[0]}/{key[1]}', bytes=len(data), old_bytes=size, address=hex(target), moved_from=hex(address)))
    return changes, placed


def build(image, icons=ICONS):
    """icons: folder of replacement icons (None or a missing folder: none)."""
    output = bytearray(image)
    changes, free = cut_wallpapers(image, output)
    more, extra = cut_links(image, output)
    changes += more; free += extra
    more, extra = cut_boot_animation(image, output)
    changes += more; free += extra
    more, extra = cut_world_time(image, output)
    changes += more; free += extra
    more, placed = replace_icons(image, output, free, icons) if icons else ([], [])
    changes += more
    allowed = set()
    for change in changes:
        start = int(change['start'], 0)
        allowed.update(range(start, start+change['size']))
    if len(output) != len(image) or any(i not in allowed for i, (a, b) in enumerate(zip(image, output)) if a != b):
        raise ValueError('Changes outside declared regions')
    steps = ['wallpapers', 'media library', 'social networks', 'power-on animation', 'world time']+(['icons'] if placed else [])
    return bytes(output), dict(format='b220-cut', version=1, steps=steps, source_sha256=sha(image),
                               output_sha256=sha(output), size=len(output), changes=changes, icons=placed, free_rom=free,
                               free_rom_bytes=sum(f['size'] for f in free),
                               changed_bytes=sum(a != b for a, b in zip(image, output)))


def carry_card(card, previous):
    """A new firmware gets the previous build's SD card (files, games) instead of an empty one."""
    if card.exists() or not previous:
        return
    old = ROOT/'analysis/emulator/experiments'/previous/'sd-card.img'
    if not old.exists():
        return
    try:
        data = old.read_bytes()
    except OSError:
        print(f'Previous SD card is in use, the new firmware starts with an empty one: {old}')
        return
    card.parent.mkdir(parents=True, exist_ok=True)
    card.write_bytes(data)
    print(f'SD card of the previous build copied: {old} -> {card}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=DEFAULT_SOURCE)
    parser.add_argument('--directory', type=Path, default=DIRECTORY)
    parser.add_argument('--prepare-card', action='store_true')
    args = parser.parse_args()
    try:
        profile = json.loads((ROOT/'emulator/profiles/b220-l7a.json').read_text())
        image, identity = load_firmware(profile, args.source)
        paths = [args.directory/name for name in ('source-copy.bin', 'b220-cut.bin', 'manifest.json')]
        protect_outputs(profile, identity, paths)
        output, manifest = build(image)
        manifest['source'] = str(args.source.resolve())
        # Remember the build before this one: its SD card moves to the new firmware.
        old = json.loads(paths[2].read_text(encoding='utf-8')) if paths[2].exists() else {}
        previous = old.get('output_sha256')
        manifest['previous_output_sha256'] = (old.get('previous_output_sha256') if previous == manifest['output_sha256']
                                              else previous)
        args.directory.mkdir(parents=True, exist_ok=True)
        paths[0].write_bytes(image); paths[1].write_bytes(output)
        paths[2].write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
        if args.source.read_bytes() != image:
            raise ValueError('Source firmware changed while building')
        if args.prepare_card:
            from .install_sd_app import install
            card = ROOT/'analysis/emulator/experiments'/sha(output)/'sd-card.img'
            carry_card(card, manifest.get('previous_output_sha256'))
            for name in ('card_demo.b22asm', 'card_second.b22asm', 'card_native.c', 'card_screen.c'):
                install(ROOT/'apps'/name, card, ensure=True)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(dict(image=str(paths[1].resolve()), sha256=sha(output), steps=manifest['steps'],
                          changed_bytes=manifest['changed_bytes'], free_rom_bytes=manifest['free_rom_bytes']), indent=2))


if __name__ == '__main__':
    main()
