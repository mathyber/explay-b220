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
Fonts: grey-level copies of the four font sets go to freed ROM and the native
glyph loops blend their pixels (tools.font_alpha, CUT_RU.md).
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


# Texts of the main resource module: header +0x24 is the offset of a block that
# starts with two (table offset, u32) language entries; each table holds one u32
# offset per text, relative to the module. A text is u16 0x8000, u16 byte length,
# UTF-16BE characters, zero padding to 4 bytes. Language 0 is English, 1 Russian.
MAIN_MODULE = 0x2884
GAMES_TEXT = 0x40000027
# The menu dispatcher (0x25e7f8) sends menu id 0xc0002, compared with the
# literal at 0x25eab0, straight to the SD Apps entry (0x25ea00 -> 0x60eee9);
# Games' own id 0x37 would open the Games list first.
SD_APPS_MENU_ID, SD_APPS_ID_LITERAL = 0xc0002, 0x25eab0
SD_APPS_LABEL = 'SD Apps'
# Social networks' grid icons (images main/342 and main/351) become SD Apps'.
SD_APPS_ICONS = (0x40000157, 0x40000160)


def text_slot(image, text, language):
    block, count = struct.unpack_from('>II', image, MAIN_MODULE+0x24)
    table = struct.unpack_from('>I', image, MAIN_MODULE+block+language*8)[0]
    number = (text & 0xffff)-1
    if text >> 16 != 0x4000 or not 0 <= number < count-1:
        raise ValueError(f'Text {text:#x} is not in the main module')
    start, end = struct.unpack_from('>II', image, MAIN_MODULE+table+number*4)
    if struct.unpack_from('>H', image, MAIN_MODULE+start)[0] != 0x8000:
        raise ValueError(f'Unexpected text record for {text:#x}')
    return MAIN_MODULE+start, end-start


def rewrite_text(image, output, text, value):
    changes = []
    for language in (0, 1):
        address, size = text_slot(image, text, language)
        data = struct.pack('>HH', 0x8000, len(value)*2)+value.encode('utf-16-be')
        if len(data) > size:
            raise ValueError(f'"{value}" does not fit text {text:#x}')
        output[address:address+size] = data+bytes(size-len(data))
        changes.append(dict(start=hex(address), size=size, kind=f'text {text:#x} language {language}: "{value}"'))
    return changes


# Settings -> Quick access lists idle shortcut functions 0..17 (handler near
# 0x278800): its list position is the function number. Games (7) only opens a
# window with SD Apps, which is shortcut 17, so it leaves the list: positions
# after it are one function higher.
SHORTCUT_LIST_LOOP, SHORTCUT_LIST_END = 0x278884, 0x27889a   # append items 0..17
SHORTCUT_LIST_GET = 0x278852    # BL: list position of the chosen item
SHORTCUT_LIST_SET = 0x2788f0    # movs r2,#1; adds r1,r4,#0 before "highlight r4"
LIST_APPEND, LIST_POSITION = 0x1c682c, 0x395e24
GAMES_SHORTCUT, SHORTCUT_LIST_SIZE = 7, 18
SHORTCUT_LIST_BYTES = {SHORTCUT_LIST_LOOP: '00a0492b1c3b580849251c32f74dffcc34012c12d3f4',
                       SHORTCUT_LIST_GET: 'f11dfae7', SHORTCUT_LIST_SET: '22011c21'}


LABEL_WRAPPER_CODE = bytes.fromhex('b4104c0542a3d1024b05240894014c0346a4bc10476046c0')


def time_title_back(image, output, free):
    """The early label demo (tools.build_ui_label) makes the date/time screen's
    title "B220 MOD": its call site goes to a wrapper that swaps the "Время"
    string. The call site gets the original BL to the stock text drawing again,
    and the wrapper (code, literals) and its "B220 MOD" string become free ROM."""
    from .build_ui_label import HOOK, NATIVE_DRAW, ORIGINAL_TEXT, TEXT
    from .thumb_asm import bl_bytes
    original = bl_bytes(HOOK, NATIVE_DRAW)
    if image[HOOK:HOOK+4] == original:
        return []
    hi, lo = struct.unpack_from('>HH', image, HOOK)
    if hi & 0xf800 != 0xf000 or lo & 0xf800 != 0xf800:
        raise ValueError('Unexpected time title call site')
    offset = (hi & 0x7ff) << 12 | (lo & 0x7ff) << 1
    wrapper = HOOK+4+(offset-(1 << 23) if offset & (1 << 22) else offset)
    size = len(LABEL_WRAPPER_CODE)+12
    text_at = struct.unpack_from('>I', image, wrapper+size-4)[0]
    string = TEXT.encode('utf-16-be')+b'\0\0'
    if (image[wrapper:wrapper+len(LABEL_WRAPPER_CODE)] != LABEL_WRAPPER_CODE
            or struct.unpack_from('>II', image, wrapper+len(LABEL_WRAPPER_CODE)) != (ORIGINAL_TEXT, NATIVE_DRAW | 1)
            or image[text_at:text_at+len(string)] != string):
        raise ValueError('Unexpected label wrapper')
    output[HOOK:HOOK+4] = original
    output[wrapper:wrapper+size] = b'\xff'*size
    output[text_at:text_at+len(string)] = b'\xff'*len(string)
    for start, end, origin in ((wrapper, wrapper+size, 'label wrapper'), (text_at, text_at+len(string), '"B220 MOD" string')):
        aligned = (start+3) & ~3
        if end-aligned >= 8:
            free.append(dict(start=hex(aligned), end=hex(end), size=end-aligned, origin=origin))
    return [dict(start=hex(HOOK), size=4, kind='date/time title: stock drawing ("Время" instead of "B220 MOD")'),
            dict(start=hex(wrapper), size=size, kind='label wrapper removed'),
            dict(start=hex(text_at), size=len(string), kind='"B220 MOD" string removed')]


# The native 1bpp glyph drawing (0x4cc9c4) has two equal pixel loops (r6 pixel index,
# r4 colour, r5 layer buffer): a bit test, then x/y, clipping and one STRH of the colour.
# (bit test start, its skip target, pixel store start); bit test 18 bytes, store 20 bytes.
GLYPH_LOOPS = ((0x4ccb66, 0x4ccbc8, 0x4ccbb4), (0x4ccbd6, 0x4ccc20, 0x4ccc0c))
GLYPH_BIT_TEST = '992508f05c0807720f52218040d14008d0'   # ...; ANDS; BEQ skip
GLYPH_STORE = 'ab0020205e189918434821245e5918400040522c'
GLYPH_ALPHA_SLOT = 0x1c    # caller stack word unused on this path: the pixel's alpha 0..15
BL_LOW = 0x4ccb66-0x3ff000


def glyph_helpers(base, table):
    """alpha: r6 pixel index of the glyph at [sp+0x94] -> r0 = alpha 0..15 (also in the
    alpha slot): its nibble in the alpha copy of the set, the 1bpp bit for other glyphs.
    store: blends r4 (RGB565) into the pixel at the loop's x/y with that alpha."""
    from .thumb_asm import Thumb
    code = Thumb(base)
    code.label('alpha')
    code.push(4, 'lr')                       # caller sp = sp+8
    code.ldr_sp(1, 0x94+8)
    code.ldr_lit(4, table)
    code.label('set')
    code.ldr_imm(2, 4, 0)
    code.cmp(2, 0)
    code.b('mono', 'eq')
    code.alu('cmp', 1, 2)
    code.b('next', 'lo')
    code.ldr_imm(3, 4, 4)
    code.alu('cmp', 1, 3)
    code.b('next', 'hs')
    code.sub3(1, 1, 2)                       # alpha glyph = alpha base + 4*(glyph - 1bpp base)
    code.lsls(1, 1, 2)
    code.ldr_imm(2, 4, 8)
    code.add3(1, 1, 2)
    code.lsrs(2, 6, 1)
    code.ldrb_reg(0, 1, 2)
    code.lsls(3, 6, 31)
    code.b('high', 'eq')
    code.lsls(0, 0, 28)
    code.lsrs(0, 0, 28)
    code.b('found')
    code.label('high')
    code.lsrs(0, 0, 4)
    code.label('found')
    code.str_sp(0, GLYPH_ALPHA_SLOT+8)
    code.pop(4, 'pc')
    code.label('next')
    code.adds(4, 12)
    code.b('set')
    code.label('mono')                       # the original bit test, 1 -> 15
    code.lsrs(2, 6, 3)
    code.ldrb_reg(0, 1, 2)
    code.lsls(2, 6, 29)
    code.lsrs(2, 2, 29)
    code.alu('lsls', 0, 2)
    code.lsls(0, 0, 24)
    code.lsrs(0, 0, 31)
    code.lsls(3, 0, 4)
    code.sub3(0, 3, 0)
    code.b('found')

    code.label('store')
    code.push(4, 5, 6, 7, 'lr')              # caller sp = sp+20
    code.add_sp(3, 20)
    code.movs(0, 0x20)
    code.ldrsh_reg(0, 3, 0)                  # y
    code.ldr_imm(1, 3, 0x60)                 # layer width
    code.alu('muls', 0, 1)
    code.movs(1, 0x24)
    code.ldrsh_reg(1, 3, 1)                  # x
    code.add3(0, 0, 1)
    code.lsls(0, 0, 1)
    code.add3(5, 5, 0)
    code.lsls(4, 4, 16)
    code.lsrs(4, 4, 16)
    code.ldr_imm(2, 3, GLYPH_ALPHA_SLOT)
    code.cmp(2, 15)
    code.b('solid', 'hs')
    code.ldrh_imm(1, 5, 0)
    code.ldr_lit(7, 0x07e0f81f)              # RGB565 spread: G in the high half, R and B low
    code.lsls(0, 4, 16)
    code.alu('orrs', 0, 4)
    code.alu('ands', 0, 7)
    code.lsls(6, 1, 16)
    code.alu('orrs', 1, 6)
    code.alu('ands', 1, 7)
    code.lsls(6, 2, 1)                       # alpha 0..15 -> 0..31
    code.lsrs(2, 2, 3)
    code.add3(2, 6, 2)
    code.movs(6, 32)
    code.sub3(6, 6, 2)
    code.alu('muls', 0, 2)
    code.alu('muls', 1, 6)
    code.add3(0, 0, 1)
    code.lsrs(0, 0, 5)
    code.alu('ands', 0, 7)
    code.lsrs(1, 0, 16)
    code.alu('orrs', 0, 1)
    code.strh_imm(0, 5, 0)
    code.pop(4, 5, 6, 7, 'pc')
    code.label('solid')
    code.strh_imm(4, 5, 0)
    code.pop(4, 5, 6, 7, 'pc')
    return code


def antialias_fonts(image, output, free):
    """Grey-level copies of the four font sets (tools.font_alpha) go to freed ROM;
    the native glyph loops take each pixel's alpha from them and blend."""
    from .font_alpha import alpha_sets, heights
    from .thumb_asm import bl_bytes
    for test, skip, store in GLYPH_LOOPS:
        if (image[test:test+17].hex() != GLYPH_BIT_TEST or image[test+16:test+18] != b'\xd0'+bytes([(skip-(test+20)) >> 1 & 0xff])
                or image[store:store+20].hex() != GLYPH_STORE):
            raise ValueError(f'Unexpected glyph drawing loop at {test:#x}')
    changes, table, moved = [], b'', []
    stock = heights(image) == {16: 16, 14: 14}     # stock bitmap font: no outline, Scale2x edges
    for fontset, data, grey, wider in alpha_sets(image, smooth_bitmaps=stock):
        at = take_free(free, len(data), 0)
        output[at:at+len(data)] = data
        table += struct.pack('>III', fontset.bitmap, fontset.end, at)
        changes.append(dict(start=hex(at), size=len(data), kind=f'font {fontset.name}: 4bpp alpha ({grey} grey glyphs)'))
        moved += [(fontset, *glyph) for glyph in wider]
    if moved:
        # Glyphs squeezed into their slots get their own width: 1bpp data in freed ROM
        # (offset = new place - set base, the firmware adds it modulo 2^32), alpha 4x after it.
        bits_size = sum(len(g[3]) for g in moved)
        bits_at = take_free(free, bits_size, 0)
        alpha_at = take_free(free, 4*bits_size, 0)
        place = bits_at
        for fontset, index, width, bits, alpha in moved:
            output[place:place+len(bits)] = bits
            output[alpha_at+4*(place-bits_at):alpha_at+4*(place-bits_at)+len(alpha)] = alpha
            output[fontset.widths+index] = width
            struct.pack_into('>I', output, fontset.offsets+4*index, (place-fontset.bitmap) & 0xffffffff)
            changes += [dict(start=hex(fontset.widths+index), size=1, kind=f'font {fontset.name}: glyph {index} own width'),
                        dict(start=hex(fontset.offsets+4*index), size=4, kind=f'font {fontset.name}: glyph {index} moved')]
            place += len(bits)
        table += struct.pack('>III', bits_at, bits_at+bits_size, alpha_at)
        changes += [dict(start=hex(bits_at), size=bits_size, kind=f'font: {len(moved)} glyphs at their own width (1bpp)'),
                    dict(start=hex(alpha_at), size=4*bits_size, kind='font: their 4bpp alpha')]
    table += bytes(4)
    at = take_free(free, len(table), 0)
    output[at:at+len(table)] = table
    changes.append(dict(start=hex(at), size=len(table), kind='font alpha sets: 1bpp range -> alpha copy'))
    size = len(glyph_helpers(0, 0).assemble())
    base = take_free(free, size, BL_LOW)
    code = glyph_helpers(base, at)
    data = code.assemble()
    output[base:base+size] = data
    changes.append(dict(start=hex(base), size=size, kind='font antialiasing: alpha and blend helpers'))
    nop = b'\x46\xc0'
    for test, skip, store in GLYPH_LOOPS:
        patch = bl_bytes(test, code.addresses['alpha'])+bytes.fromhex('2800')
        patch += struct.pack('>H', 0xd000 | (skip-(test+10)) >> 1 & 0xff)
        output[test:test+18] = patch+nop*((18-len(patch))//2)
        output[store:store+20] = bl_bytes(store, code.addresses['store'])+nop*8
        changes += [dict(start=hex(test), size=18, kind='glyph loop: pixel alpha instead of the bit'),
                    dict(start=hex(store), size=20, kind='glyph loop: blended pixel')]
    return changes


# Resource animations of the main module: header +0x0c the table (records offset, data
# offsets offset), +0x10 their count; a record is width, height (16 bit), frames, size.
WAIT_ANIMATION = 2          # the 30x30 wheel of the "Ожидание" window (Bluetooth on, ...)
WAIT_DOTS = (5, 13, 21)     # three 4x4 dots, as on the power-on screen, in green
WAIT_DOTS_Y = 13
WAIT_GREEN = (3, 254, 0)    # the colour of the selection frames
# The animation blit skips pure black (0x0000): a near-black backdrop (RGB565 0x0020) erases
# the dots of the previous frame. The original wheel redraws every petal in each frame.
WAIT_BACK = (0, 4, 0)


def wait_dots(count):
    picture = Image.new('RGBA', (30, 30), WAIT_BACK+(255,))
    draw = ImageDraw.Draw(picture)
    for x in WAIT_DOTS[:count]:
        draw.rectangle((x, WAIT_DOTS_Y, x+3, WAIT_DOTS_Y+3), fill=WAIT_GREEN+(255,))
    return picture


def wait_animation(image, output, free):
    """The waiting wheel becomes three green dots that light up one by one (DOT_STATES)
    on a near-black backdrop (WAIT_BACK). Same place, its tail is freed."""
    from .image_codec import decode_mv, encode_mv
    table = MAIN_MODULE+struct.unpack_from('>I', image, MAIN_MODULE+0x0c)[0]
    count, = struct.unpack_from('>I', image, MAIN_MODULE+0x10)
    records, offsets = struct.unpack_from('>II', image, table)
    record = MAIN_MODULE+records+12*WAIT_ANIMATION
    width, height, frames, size = struct.unpack_from('>HHII', image, record)
    at = MAIN_MODULE+struct.unpack_from('>I', image, MAIN_MODULE+offsets+4*WAIT_ANIMATION)[0]
    if count != 3 or (width, height, frames) != (30, 30, 8) or len(decode_mv(image, at)) != 8:
        raise ValueError('Unexpected waiting animation')
    data = encode_mv([wait_dots(n) for n in DOT_STATES], image[at:at+0x42])
    check = decode_mv(data)
    first = (WAIT_DOTS[0]+1, WAIT_DOTS_Y+1)
    if len(data) > size or len(check) != len(DOT_STATES) or check[0].getpixel(first)[1] > 100 or check[1].getpixel(first)[1] < 200:
        raise ValueError('Waiting animation does not fit or round-trip')
    output[at:at+size] = data+b'\xff'*(size-len(data))
    struct.pack_into('>I', output, record+8, len(data))
    tail = (at+len(data)+3) & ~3
    if at+size-tail >= 16:
        free.append(dict(start=hex(tail), end=hex(at+size), size=at+size-tail, origin='waiting animation tail'))
    return [dict(start=hex(at), size=size, kind=f'waiting animation: {len(DOT_STATES)} frames of green dots ({len(data)} bytes)'),
            dict(start=hex(record+8), size=4, kind='waiting animation: size')]


def games_out_of_shortcut_list(image, output, free):
    from .thumb_asm import Thumb, bl_bytes
    for address, original in SHORTCUT_LIST_BYTES.items():
        if image[address:address+len(original)//2].hex() != original:
            raise ValueError(f'Unexpected Quick access code at {address:#x}')
    labels_table = struct.unpack_from('>I', image, 0x278934)[0]   # literals of the original loop
    item_style = struct.unpack_from('>I', image, 0x278924)[0]

    def helpers(base):
        code = Thumb(base)
        code.label('append')            # r6 list, r7 style argument: add every function but Games
        code.push(4, 'lr')
        code.movs(4, 0)
        code.label('loop')
        code.cmp(4, GAMES_SHORTCUT)
        code.b('next', 'eq')
        code.ldr_lit(1, labels_table)
        code.lsls(0, 4, 2)
        code.ldr(0, 1, 0)
        code.ldr_lit(1, item_style)
        code.mov(2, 6)
        code.mov(3, 7)
        code.bl(LIST_APPEND)
        code.label('next')
        code.adds(4, 1)
        code.cmp(4, SHORTCUT_LIST_SIZE)
        code.b('loop', 'lo')
        code.pop(4, 'pc')
        code.label('get')               # list position -> function
        code.push('lr')
        code.bl(LIST_POSITION)
        code.cmp(0, GAMES_SHORTCUT)
        code.b('got', 'lo')
        code.adds(0, 1)
        code.label('got')
        code.pop('pc')
        code.label('position')          # function in r4 -> list position, then the replaced instructions
        code.cmp(4, GAMES_SHORTCUT)
        code.b('keep', 'ls')
        code.subs(4, 1)
        code.label('keep')
        code.movs(2, 1)
        code.mov(1, 4)
        code.bx_lr()
        return code
    size = len(helpers(0).assemble())
    base = take_free(free, size, 0)
    code = helpers(base)
    data = code.assemble()
    output[base:base+size] = data
    at = code.addresses
    loop = bl_bytes(SHORTCUT_LIST_LOOP, at['append'])+struct.pack('>H', 0xe000 | (SHORTCUT_LIST_END-(SHORTCUT_LIST_LOOP+8)) >> 1)
    loop += b'\x46\xc0'*((SHORTCUT_LIST_END-SHORTCUT_LIST_LOOP-len(loop))//2)
    output[SHORTCUT_LIST_LOOP:SHORTCUT_LIST_END] = loop
    output[SHORTCUT_LIST_GET:SHORTCUT_LIST_GET+4] = bl_bytes(SHORTCUT_LIST_GET, at['get'])
    output[SHORTCUT_LIST_SET:SHORTCUT_LIST_SET+4] = bl_bytes(SHORTCUT_LIST_SET, at['position'])
    return [dict(start=hex(base), size=size, kind='Quick access without Games: list helpers'),
            dict(start=hex(SHORTCUT_LIST_LOOP), size=SHORTCUT_LIST_END-SHORTCUT_LIST_LOOP, kind='Quick access list: skip Games'),
            dict(start=hex(SHORTCUT_LIST_GET), size=4, kind='Quick access: list position -> function'),
            dict(start=hex(SHORTCUT_LIST_SET), size=4, kind='Quick access: function -> list position')]


def sd_apps_in_main_menu(output):
    """Games leaves Multimedia; SD Apps becomes the ninth main menu item: a menu
    id that opens the SD Apps list, the former Social networks text and grid icons."""
    current = bytes(output)
    if struct.unpack_from('>I', current, SD_APPS_ID_LITERAL)[0] != SD_APPS_MENU_ID:
        raise ValueError('Unexpected menu dispatcher')
    changes, _, _ = remove_item(current, output, (MULTIMEDIA,), GAMES_TEXT, 'Multimedia without Games')
    items, count = group(current, MAIN_MENU[0])
    if count != 8 or any(current[items+count*ITEM_SIZE:items+(count+1)*ITEM_SIZE]):
        raise ValueError('Main menu: expected 8 items and a free ninth slot')
    item = bytearray(current[items:items+ITEM_SIZE])
    struct.pack_into('>I', item, 0, SD_APPS_MENU_ID)
    struct.pack_into('>I', item, 0x14, SOCIAL_TEXT)
    slot = items+count*ITEM_SIZE
    output[slot:slot+ITEM_SIZE] = item
    changes.append(dict(start=hex(slot), size=ITEM_SIZE, kind=f'main menu item 9: SD Apps (menu id {SD_APPS_MENU_ID:#x})'))
    for address in MAIN_MENU:
        struct.pack_into('>H', output, address+GROUP_COUNT, count+1)
        changes.append(dict(start=hex(address+GROUP_COUNT), size=2, kind='main menu: 8 -> 9 items'))
    for table, icon in zip(MAIN_ICONS, SD_APPS_ICONS):
        struct.pack_into('>I', output, table+count*4, icon)
        changes.append(dict(start=hex(table+count*4), size=4, kind=f'main menu icon 9: {icon:#x}'))
    return changes+rewrite_text(current, output, SOCIAL_TEXT, SD_APPS_LABEL)


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
    # Pictures to place: PNGs, and an empty picture for each image in removed.txt.
    wanted = {}
    for path in sorted(directory.glob('*/*.png')):
        wanted[(path.parent.name, int(path.stem))] = (path, Image.open(path).convert('RGBA'))
    removed = directory/'removed.txt'
    for line in (removed.read_text(encoding='utf-8').split() if removed.exists() else []):
        module_name, _, index = line.partition('/')
        key = (module_name, int(index))
        if key in entries:
            wanted[key] = (removed, Image.new('RGBA', (entries[key]['width'], entries[key]['height']), (0, 0, 0, 0)))
        else:
            raise ValueError(f'{removed}: no image {line} in the firmware')
    jobs = []
    for key, (path, picture) in sorted(wanted.items()):
        entry = entries.get(key)
        if not entry or entry['type'] not in ENCODERS:
            raise ValueError(f'{path}: no MB or ABMP image {key[0]}/{key[1]} in the firmware')
        if picture.size != (entry['width'], entry['height']):
            raise ValueError(f'{path}: {picture.size[0]}x{picture.size[1]}, the slot is {entry["width"]}x{entry["height"]}')
        address = int(entry['address'], 16)
        # The module that holds this image: the closest module header below it.
        module = max(m for m in modules if m <= address)
        table = module+struct.unpack_from('>I', image, module+0x64)[0]
        if struct.unpack_from('>I', image, table+key[1]*4)[0] != address-module:
            raise ValueError(f'icon {key[0]}/{key[1]}: offset table does not point at the image')
        jobs.append((key, entry, ENCODERS[entry['type']](picture), module, table))
    changes, placed = [], []
    # First every image that fits its slot: the unused tail of the slot becomes
    # free ROM (the record size follows), then the heavier ones move into it.
    for key, entry, data, module, table in sorted(jobs, key=lambda j: len(j[2]) > j[1]['size']):
        address, size, index = int(entry['address'], 16), entry['size'], key[1]
        record = module+0x68+index*12
        name = f'icon {key[0]}/{key[1]}'
        if len(data) <= size:
            output[address:address+size] = data+b'\xff'*(size-len(data))
            changes.append(dict(start=hex(address), size=size, kind=f'{name}: {len(data)} bytes in place of {size}'))
            tail = (address+len(data)+3) & ~3
            if address+size-tail >= 16:
                free.append(dict(start=hex(tail), end=hex(address+size), size=address+size-tail, origin=f'{name} tail'))
                struct.pack_into('>I', output, record+8, len(data))
                changes.append(dict(start=hex(record+8), size=4, kind=f'{name}: record size'))
            placed.append(dict(icon=f'{key[0]}/{key[1]}', bytes=len(data), old_bytes=size, address=hex(address)))
            continue
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
    more += sd_apps_in_main_menu(output)
    changes += more; free += extra
    more, extra = cut_boot_animation(image, output)
    changes += more; free += extra
    more, extra = cut_world_time(image, output)
    changes += more; free += extra
    changes += games_out_of_shortcut_list(image, output, free)
    changes += time_title_back(image, output, free)
    changes += wait_animation(image, output, free)
    changes += antialias_fonts(image, output, free)
    more, placed = replace_icons(image, output, free, icons) if icons else ([], [])
    changes += more
    allowed = set()
    for change in changes:
        start = int(change['start'], 0)
        allowed.update(range(start, start+change['size']))
    if len(output) != len(image) or any(i not in allowed for i, (a, b) in enumerate(zip(image, output)) if a != b):
        raise ValueError('Changes outside declared regions')
    steps = ['wallpapers', 'media library', 'social networks', 'SD Apps in the main menu', 'power-on animation', 'world time', 'Quick access without Games', 'time title', 'waiting dots', 'font antialiasing']+(['icons'] if placed else [])
    return bytes(output), dict(format='b220-cut', version=1, steps=steps, source_sha256=sha(image),
                               output_sha256=sha(output), size=len(output), changes=changes, icons=placed, free_rom=free,
                               free_rom_bytes=sum(f['size'] for f in free),
                               changed_bytes=sum(a != b for a, b in zip(image, output)))


def carry_card(card, previous):
    """A new firmware gets the previous build's SD card (files, games) instead of an
    empty one. Returns 0 when that card is in use: then no card is made yet."""
    if card.exists() or not previous:
        return 1
    old = ROOT/'analysis/emulator/experiments'/previous/'sd-card.img'
    if not old.exists():
        return 1
    try:
        data = old.read_bytes()
    except OSError:
        print(f'The previous SD card is in use: close the emulator and run this again to carry it over ({old})')
        return 0
    card.parent.mkdir(parents=True, exist_ok=True)
    card.write_bytes(data)
    print(f'SD card of the previous build copied: {old} -> {card}')
    return 1


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
            if carry_card(card, manifest.get('previous_output_sha256')):
                for name in ('card_demo.b22asm', 'card_second.b22asm', 'card_native.c', 'card_screen.c'):
                    install(ROOT/'apps'/name, card, ensure=True)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(dict(image=str(paths[1].resolve()), sha256=sha(output), steps=manifest['steps'],
                          changed_bytes=manifest['changed_bytes'], free_rom_bytes=manifest['free_rom_bytes']), indent=2))


if __name__ == '__main__':
    main()
