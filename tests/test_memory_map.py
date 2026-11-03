"""Static checks of the resource map and allocator facts used by MEMORY_RU.md."""
import struct
import unittest
from pathlib import Path

from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_BIG_ENDIAN

from emulator.firmware import ROOT
from tools.map_resources import WALLPAPERS, WALLPAPER_KEPT, cut_list, decode_sjpg, modules, sjpg_header
from tools.measure_heaps import ALLOC, HEAPS

SOURCES = sorted((ROOT/'analysis/extracted').glob('SC*.bin'))


@unittest.skipUnless(len(SOURCES) == 1, 'Extracted original PS required')
class MemoryMapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.image = SOURCES[0].read_bytes()
        cls.modules = modules(cls.image)

    def test_resource_modules_are_found_and_labelled(self):
        self.assertEqual(len(self.modules), 19)
        self.assertEqual(sum(m['size'] for m in self.modules), 1473776)
        labels = {m['label']:m for m in self.modules}
        self.assertEqual(labels['games: Sokoban']['size'], 7820)
        self.assertEqual(labels['main']['start'], '0x2884')
        self.assertEqual(len(labels['main']['images']), 729)
        self.assertTrue(all(m['label'] for m in self.modules))

    def test_wallpapers_are_adjacent_fullscreen_sjpg(self):
        main = next(m for m in self.modules if m['label'] == 'main')
        sizes = [r['size'] for r in main['images'] if r['type'] == '0x20a' and (r['width'], r['height']) == (176, 220)]
        self.assertEqual(sizes, [785, 21535, 14083, 14045, 22448])
        self.assertEqual(sjpg_header(self.image, WALLPAPER_KEPT), (176, 220))
        cut = cut_list(self.image, self.modules)
        self.assertEqual(cut['wallpapers']['total'], 0xa9a90-WALLPAPERS[0])
        picture = decode_sjpg(self.image, WALLPAPERS[0], 21535)
        self.assertEqual(picture.size, (176, 220))

    def test_allocator_dispatch_uses_heap_type_argument(self):
        # 0x6a5439 -> Thumb/ARM veneers -> 0x17f457; large requests compare the
        # second argument (pushed r1 at sp+0xb0) with three heap tags.
        self.assertEqual(struct.unpack_from('>I', self.image, (ALLOC&~1)+12)[0], 0x17f457)
        md = Cs(CS_ARCH_ARM, CS_MODE_THUMB|CS_MODE_BIG_ENDIAN)
        code = list(md.disasm(self.image[0x17f456:0x17f48e], 0x17f456))
        self.assertEqual(code[0].op_str, '{r0, r1, r2, r3, r4, r5, r6, r7, lr}')
        self.assertIn('ldr r0, [sp, #0xb0]', [f'{i.mnemonic} {i.op_str}' for i in code])
        tags = []
        for i in code:
            if i.mnemonic == 'ldr' and '[pc' in i.op_str:
                target = ((i.address+4)&~3)+int(i.op_str.split('#')[1].rstrip(']'), 0)
                tags.append(struct.unpack_from('>I', self.image, target)[0])
        self.assertEqual(tags, list(HEAPS.values()))
        self.assertEqual(code[-1].mnemonic, 'bl')  # assert path for any other tag
        self.assertEqual(code[-1].op_str, '#0x3950fc')


if __name__ == '__main__':
    unittest.main()
