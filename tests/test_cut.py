"""Check the content-removal PS built on top of the white-clock image."""
import io
import struct
import unittest

from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_BIG_ENDIAN
from PIL import Image

from tools.build_cut import (BLACK_ID, BLACK_SIZE, BOOT_END, BOOT_HEADER, BOOT_SLOT, DEFAULT_SOURCE, DOT_STATES,
                             DOTS_Y, GROUP_COUNT, ITEM_SIZE, MAIN_ICONS, MAIN_MENU,
                             MEDIA_LIBRARY_TEXT, MULTIMEDIA, SD_APPS_ENTRY, SHORTCUT_ACTIONS, SLOTS_END,
                             SOCIAL_SHORTCUT, SOCIAL_TEXT, OTHERS, TOOLS, WALLPAPER_IDS, WORLD_MAP, WORLD_TIME_TEXT,
                             build)
from tools.build_green_theme import bitmap_resources
from tools.map_resources import WALLPAPERS, WALLPAPER_KEPT


@unittest.skipUnless(DEFAULT_SOURCE.exists(), 'Built white-clock PS required')
class CutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = DEFAULT_SOURCE.read_bytes()
        cls.output, cls.manifest = build(cls.source)

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
        self.assertEqual(len(multimedia), 8)
        self.assertNotIn(MEDIA_LIBRARY_TEXT, multimedia)
        for group in MAIN_MENU:
            main = texts(group)
            self.assertEqual(len(main), 8)
            self.assertNotIn(SOCIAL_TEXT, main)

    def test_main_menu_icons_follow_items(self):
        for table in MAIN_ICONS:
            before = struct.unpack_from('>9I', self.source, table)
            after = struct.unpack_from('>9I', self.output, table)
            self.assertEqual(after, before[:1]+before[2:]+before[-1:])

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
        self.assertGreater(free['size'], 67000)
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

    def test_wrong_source_is_rejected(self):
        broken = bytearray(self.source)
        broken[0x1c9ade:0x1c9ae0] = b'\x21\x07'
        with self.assertRaises(ValueError):
            build(bytes(broken))


if __name__ == '__main__':
    unittest.main()
