"""Check the content-removal PS built on top of the white-clock image."""
import io
from pathlib import Path
import struct
import unittest

from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_BIG_ENDIAN
from PIL import Image

from tools.build_cut import (BLACK_ID, BLACK_SIZE, BOOT_END, BOOT_HEADER, BOOT_SLOT, DEFAULT_SOURCE, DOT_STATES,
                             DOTS_Y, GROUP_COUNT, ITEM_SIZE, MAIN_ICONS, MAIN_MENU,
                             MEDIA_LIBRARY_TEXT, MULTIMEDIA, SD_APPS_ENTRY, SHORTCUT_ACTIONS, SLOTS_END,
                             SOCIAL_SHORTCUT, SOCIAL_TEXT, OTHERS, TOOLS, WALLPAPER_IDS, WORLD_MAP, WORLD_TIME_TEXT,
                             GAMES_TEXT, SD_APPS_MENU_ID, build, text_slot, SHORTCUT_LIST_LOOP, SHORTCUT_LIST_END,
                             SHORTCUT_LIST_GET, SHORTCUT_LIST_SET, LIST_APPEND, LIST_POSITION)
from tools.build_green_theme import bitmap_resources
from tools.map_resources import WALLPAPERS, WALLPAPER_KEPT


@unittest.skipUnless(DEFAULT_SOURCE.exists(), 'Built white-clock PS required')
class CutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = DEFAULT_SOURCE.read_bytes()
        cls.output, cls.manifest = build(cls.source, icons=None)

    def test_only_declared_regions_change(self):
        self.assertEqual(len(self.output), len(self.source))
        declared = set()
        for change in self.manifest['changes']:
            start = int(change['start'], 0)
            declared.update(range(start, start+change['size']))
        self.assertTrue(all(i in declared for i, (a, b) in enumerate(zip(self.source, self.output)) if a != b))

    def test_every_wallpaper_id_is_default_black(self):
        ids = [struct.unpack_from('>I', self.output, WALLPAPER_IDS+i*4)[0] for i in range(5)]
        self.assertEqual(ids, [BLACK_ID]*5)
        self.assertEqual(self.output[WALLPAPER_KEPT:WALLPAPER_KEPT+BLACK_SIZE],
                         self.source[WALLPAPER_KEPT:WALLPAPER_KEPT+BLACK_SIZE])

    def test_slots_are_free_after_black_copy(self):
        black = self.source[WALLPAPER_KEPT:WALLPAPER_KEPT+BLACK_SIZE]
        for start, end in zip(WALLPAPERS, WALLPAPERS[1:]+(SLOTS_END,)):
            self.assertEqual(self.output[start:start+BLACK_SIZE], black)
            self.assertEqual(set(self.output[start+BLACK_SIZE:end]), {0xff})
        walls = [f for f in self.manifest['free_rom'] if f['origin'].startswith('wallpaper')]
        self.assertEqual(sum(f['size'] for f in walls), 68964)
        self.assertTrue(all(int(f['start'], 0) % 4 == 0 for f in self.manifest['free_rom']))

    def test_wallpaper_list_has_two_items(self):
        md = Cs(CS_ARCH_ARM, CS_MODE_THUMB|CS_MODE_BIG_ENDIAN)
        code = {a: next(md.disasm(self.output[a:a+2], a)) for a in (0x1c9ade, 0x1c9b1c, 0x4b63a4, 0x4b63fa)}
        self.assertEqual(code[0x1c9ade].op_str, 'r1, #2')
        self.assertEqual(code[0x1c9b1c].op_str, 'r0, #1')
        self.assertEqual((code[0x4b63a4].mnemonic, code[0x4b63a4].op_str), ('b', '#0x4b63ba'))
        self.assertEqual(code[0x4b63fa].op_str, 'r4, #1')

    def test_media_library_and_social_networks_are_gone(self):
        def texts(group):
            items = struct.unpack_from('>I', self.output, group)[0]
            count = struct.unpack_from('>H', self.output, group+GROUP_COUNT)[0]
            return [struct.unpack_from('>I', self.output, items+i*ITEM_SIZE+0x14)[0] for i in range(count)]
        multimedia = texts(MULTIMEDIA)
        self.assertEqual(len(multimedia), 7)
        self.assertNotIn(MEDIA_LIBRARY_TEXT, multimedia)
        self.assertNotIn(GAMES_TEXT, multimedia)
        for group in MAIN_MENU:
            main = texts(group)
            self.assertEqual(len(main), 9)
            # The ninth item is SD Apps: the old Social networks text now says so.
            self.assertEqual(main.index(SOCIAL_TEXT), 8)
        items = struct.unpack_from('>I', self.output, MAIN_MENU[0])[0]
        self.assertEqual(struct.unpack_from('>I', self.output, items+8*ITEM_SIZE)[0], SD_APPS_MENU_ID)
        for language in (0, 1):
            address, _ = text_slot(self.output, SOCIAL_TEXT, language)
            length = struct.unpack_from('>H', self.output, address+2)[0]
            self.assertEqual(self.output[address+4:address+4+length].decode('utf-16-be'), 'SD Apps')

    def test_main_menu_icons_follow_items(self):
        for table in MAIN_ICONS:
            before = struct.unpack_from('>9I', self.source, table)
            after = struct.unpack_from('>9I', self.output, table)
            self.assertEqual(after, before[:1]+before[2:]+(before[1],))  # SD Apps takes Social networks' icon

    def test_social_shortcut_opens_sd_apps(self):
        address = SHORTCUT_ACTIONS+SOCIAL_SHORTCUT*4
        self.assertEqual(struct.unpack_from('>I', self.output, address)[0], SD_APPS_ENTRY)
        actions = lambda image: struct.unpack_from('>19I', image, SHORTCUT_ACTIONS)
        self.assertEqual(sum(a != b for a, b in zip(actions(self.source), actions(self.output))), 1)

    def test_power_on_animation_is_black_with_dots(self):
        self.assertEqual(self.output[BOOT_SLOT:BOOT_SLOT+12], BOOT_HEADER)
        gif = Image.open(io.BytesIO(self.output[BOOT_SLOT+12:BOOT_END]))
        self.assertEqual((gif.size, gif.n_frames), ((176, 220), len(DOT_STATES)))
        durations = []
        for index in range(gif.n_frames):
            gif.seek(index)
            durations.append(gif.info['duration'])
            picture = gif.convert('RGB')
            self.assertEqual(picture.getpixel((0, 0)), (0, 0, 0))
            self.assertEqual(sum(picture.getpixel((79+i*8, DOTS_Y+1)) == (255, 255, 255) for i in range(3)),
                             DOT_STATES[index])
        self.assertEqual(sum(durations), 3360)
        free = next(f for f in self.manifest['free_rom'] if f['origin'] == 'power-on animation')
        self.assertEqual(int(free['end'], 0), BOOT_END)   # later steps take its start (font alpha tables)
        self.assertEqual(set(self.output[int(free['start'], 0):BOOT_END]), {0xff})

    def test_world_time_is_gone(self):
        def texts(group):
            items = struct.unpack_from('>I', self.output, group)[0]
            count = struct.unpack_from('>H', self.output, group+GROUP_COUNT)[0]
            return [struct.unpack_from('>I', self.output, items+i*ITEM_SIZE+0x14)[0] for i in range(count)]
        self.assertEqual(len(texts(TOOLS)), 5)
        self.assertEqual(len(texts(OTHERS)), 2)
        self.assertNotIn(WORLD_TIME_TEXT, texts(TOOLS)+texts(OTHERS))
        maps = [b for b in bitmap_resources(self.output) if b['start'] == WORLD_MAP]
        self.assertEqual([(m['width'], m['height'], m['size']) for m in maps], [(176, 80, 546)])
        self.assertEqual(sum(1 for _ in bitmap_resources(self.output)), sum(1 for _ in bitmap_resources(self.source)))
        free = next(f for f in self.manifest['free_rom'] if f['origin'] == 'world map')
        self.assertEqual(set(self.output[int(free['start'], 0):int(free['end'], 0)]), {0xff})

    def test_time_title_is_stock_again(self):
        from tools.build_ui_label import HOOK, NATIVE_DRAW
        from tools.thumb_asm import bl_bytes
        self.assertNotEqual(self.source[HOOK:HOOK+4], self.output[HOOK:HOOK+4])
        self.assertEqual(self.output[HOOK:HOOK+4], bl_bytes(HOOK, NATIVE_DRAW))

    def test_quick_access_list_skips_games(self):
        md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_BIG_ENDIAN)
        def code(address, size):
            return [f'{i.mnemonic} {i.op_str}' for i in md.disasm(self.output[address:address+size], address)]
        helpers = next(c for c in self.manifest['changes'] if c['kind'] == 'Quick access without Games: list helpers')
        base = int(helpers['start'], 0)
        self.assertEqual(code(SHORTCUT_LIST_LOOP, 6), [f'bl #{base:#x}', f'b #{SHORTCUT_LIST_END:#x}'])
        listing = code(base, helpers['size'])
        self.assertIn('cmp r4, #7', listing)
        self.assertIn('cmp r4, #0x12', listing)
        self.assertIn(f'bl #{LIST_APPEND:#x}', listing)
        self.assertIn(f'bl #{LIST_POSITION:#x}', listing)
        get, put = code(SHORTCUT_LIST_GET, 4)[0], code(SHORTCUT_LIST_SET, 4)[0]
        self.assertTrue(get.startswith('bl #') and put.startswith('bl #'))
        self.assertTrue(base < int(get[4:], 0) < base+helpers['size'] and base < int(put[4:], 0) < base+helpers['size'])

    def test_glyph_loops_use_the_alpha_helpers(self):
        from tools.build_cut import GLYPH_LOOPS
        from tools.build_font_patch import SETS
        md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_BIG_ENDIAN)
        helpers = next(c for c in self.manifest['changes'] if c['kind'] == 'font antialiasing: alpha and blend helpers')
        base = int(helpers['start'], 0)
        for test, skip, store in GLYPH_LOOPS:
            listing = [f'{i.mnemonic} {i.op_str}' for i in md.disasm(self.output[test:test+8], test)]
            self.assertEqual(listing[1:], ['cmp r0, #0', f'beq #{skip:#x}'])
            for call in (listing[0], next(md.disasm(self.output[store:store+4], store)).op_str):
                self.assertTrue(base <= int(call.split('#')[1], 0) < base+helpers['size'])
        tables = [c for c in self.manifest['changes'] if c['kind'].startswith('font ') and '4bpp alpha' in c['kind']]
        self.assertEqual([c['size'] for c in tables], [4*(s.end-s.bitmap) for s in SETS])

    def test_squeezed_glyphs_move_to_their_own_width(self):
        from tools.build_font_patch import SETS
        moved = [c for c in self.manifest['changes'] if c['kind'].endswith(' moved')]
        region = next(c for c in self.manifest['changes'] if 'own width (1bpp)' in c['kind'])
        start = int(region['start'], 0)
        self.assertTrue(moved)
        for change in moved:
            fontset = next(s for s in SETS if change['kind'].startswith(f'font {s.name}:'))
            index = (int(change['start'], 0)-fontset.offsets)//4
            pointer = (fontset.bitmap+struct.unpack_from('>I', self.output, fontset.offsets+4*index)[0]) & 0xffffffff
            self.assertTrue(start <= pointer < start+region['size'])
            self.assertGreater(self.output[fontset.widths+index], self.source[fontset.widths+index])

    def test_wrong_source_is_rejected(self):
        broken = bytearray(self.source)
        broken[0x1c9ade:0x1c9ae0] = b'\x21\x07'
        with self.assertRaises(ValueError):
            build(bytes(broken), icons=None)


@unittest.skipUnless(DEFAULT_SOURCE.exists(), 'Built white-clock PS required')
class IconTests(unittest.TestCase):
    def test_icons_replace_images_in_place_or_in_freed_rom(self):
        from tempfile import TemporaryDirectory
        from tools.extract_images import catalog, decode
        source = DEFAULT_SOURCE.read_bytes()
        entries = {(e['module'], e['index']): e for e in catalog(source)}
        small, large = entries[('main', 6)], entries[('main', 302)]  # MB battery, ABMP calendar
        with TemporaryDirectory() as folder:
            icons = Path(folder)/'main'
            icons.mkdir()
            Image.new('RGBA', (small['width'], small['height']), (0, 255, 0, 255)).save(icons/'006.png')
            # Noise does not compress: heavier than the original, so it must move.
            noisy = Image.new('RGBA', (large['width'], large['height']))
            noisy.putdata([((i*37) % 256, (i*91) % 256, (i*53) % 256, 255 if i % 3 else 128)
                           for i in range(large['width']*large['height'])])
            noisy.save(icons/'302.png')
            output, manifest = build(source, icons=Path(folder))
        self.assertEqual(manifest['steps'][-1], 'icons')
        placed = {p['icon']: p for p in manifest['icons']}
        self.assertNotIn('moved_from', placed['main/6'])
        self.assertEqual(placed['main/302']['moved_from'], large['address'])
        moved = {(e['module'], e['index']): e for e in catalog(output)}
        self.assertNotEqual(moved[('main', 302)]['address'], large['address'])
        self.assertEqual(moved[('main', 6)]['address'], small['address'])
        self.assertEqual(set(decode(output, moved[('main', 6)]).getdata()), {(0, 255, 0, 255)})
        self.assertEqual(decode(output, moved[('main', 302)]).getpixel((1, 0))[3], 255)
        self.assertTrue(all(int(f['start'], 0) % 4 == 0 for f in manifest['free_rom']))
        declared = set()
        for change in manifest['changes']:
            start = int(change['start'], 0)
            declared.update(range(start, start+change['size']))
        self.assertTrue(all(i in declared for i, (a, b) in enumerate(zip(source, output)) if a != b))

    def test_removed_images_become_empty_and_free_their_space(self):
        from tempfile import TemporaryDirectory
        from tools.extract_images import catalog, decode
        source = DEFAULT_SOURCE.read_bytes()
        with TemporaryDirectory() as folder:
            (Path(folder)/'removed.txt').write_text('main/291\n', encoding='utf-8')
            output, manifest = build(source, icons=Path(folder))
        image = next(e for e in catalog(output) if e['module'] == 'main' and e['index'] == 291)
        self.assertEqual(set(decode(output, image).getdata()), {(0, 0, 0, 0)})
        self.assertLess(image['size'], 32)
        tail = next(f for f in manifest['free_rom'] if f['origin'] == 'icon main/291 tail')
        self.assertGreater(tail['size'], 1200)
        self.assertEqual(set(output[int(tail['start'], 0):int(tail['end'], 0)]), {0xff})

    def test_wrong_icon_size_is_rejected(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as folder:
            (Path(folder)/'main').mkdir()
            Image.new('RGBA', (5, 5)).save(Path(folder)/'main'/'006.png')
            with self.assertRaisesRegex(ValueError, 'the slot is 14x11'):
                build(DEFAULT_SOURCE.read_bytes(), icons=Path(folder))


if __name__ == '__main__':
    unittest.main()


class GlyphHelperTests(unittest.TestCase):
    """The antialiasing helpers run in Unicorn on a fake glyph loop frame."""
    BASE, TABLE, MONO, ALPHA, STACK, LAYER, RETURN = 0x10000, 0x20000, 0x30000, 0x40000, 0x80000, 0x90000, 0x50000

    def setUp(self):
        from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_BIG_ENDIAN
        from tools.build_cut import glyph_helpers
        self.uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_BIG_ENDIAN)
        self.uc.mem_map(0, 0x100000)
        self.code = glyph_helpers(self.BASE, self.TABLE)
        self.uc.mem_write(self.BASE, self.code.assemble())
        # One alpha set: 1bpp [MONO, MONO+0x100) -> ALPHA; glyph at MONO+0x10.
        self.uc.mem_write(self.TABLE, struct.pack('>IIII', self.MONO, self.MONO+0x100, self.ALPHA, 0))
        self.uc.mem_write(self.ALPHA+0x40, bytes([0x0f, 0x8a]))
        self.uc.mem_write(self.MONO+0x200, bytes([0x40]))      # outside every set: 1bpp, pixel 1 set

    def call(self, name, **registers):
        from unicorn.arm_const import UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_R0, UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6
        names = dict(r4=UC_ARM_REG_R4, r5=UC_ARM_REG_R5, r6=UC_ARM_REG_R6)
        for key, value in registers.items():
            self.uc.reg_write(names[key], value)
        self.uc.reg_write(UC_ARM_REG_SP, self.STACK)
        self.uc.reg_write(UC_ARM_REG_LR, self.RETURN | 1)
        self.uc.emu_start(self.code.addresses[name] | 1, self.RETURN, count=200)
        return self.uc.reg_read(UC_ARM_REG_R0), {k: self.uc.reg_read(v) for k, v in names.items()}

    def alpha(self, glyph, index):
        self.uc.mem_write(self.STACK+0x94, struct.pack('>I', glyph))
        value, kept = self.call('alpha', r6=index)
        self.assertEqual(kept['r6'], index)
        self.assertEqual(struct.unpack('>I', bytes(self.uc.mem_read(self.STACK+0x1c, 4)))[0], value)
        return value

    def test_alpha_nibbles_and_1bpp_fallback(self):
        self.assertEqual([self.alpha(self.MONO+0x10, i) for i in range(4)], [0, 15, 8, 10])
        self.assertEqual([self.alpha(self.MONO+0x200, i) for i in range(3)], [0, 15, 0])

    def store(self, alpha, colour, under):
        x, y, width = 3, 2, 176
        pixel = self.LAYER+2*(y*width+x)
        self.uc.mem_write(self.STACK+0x20, struct.pack('>h', y))
        self.uc.mem_write(self.STACK+0x24, struct.pack('>h', x))
        self.uc.mem_write(self.STACK+0x60, struct.pack('>I', width))
        self.uc.mem_write(self.STACK+0x1c, struct.pack('>I', alpha))
        self.uc.mem_write(pixel, struct.pack('>H', under))
        _, kept = self.call('store', r4=colour, r5=self.LAYER, r6=77)
        self.assertEqual((kept['r4'], kept['r5'], kept['r6']), (colour, self.LAYER, 77))
        return struct.unpack('>H', bytes(self.uc.mem_read(pixel, 2)))[0]

    def test_store_blends_rgb565(self):
        self.assertEqual(self.store(15, 0xffff, 0x0000), 0xffff)
        self.assertEqual(self.store(15, 0x1234, 0xffff), 0x1234)
        self.assertEqual(self.store(1, 0xffff, 0x0000), 1 << 11 | 3 << 5 | 1)   # 2/32 white
        half = self.store(8, 0xffff, 0x0000)                                    # 17/32
        self.assertEqual((half >> 11, half >> 5 & 63, half & 31), (16, 33, 16))
        self.assertEqual(self.store(8, 0x0000, 0xf800), 14 << 11)   # 15/32 of the red under black text
