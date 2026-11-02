"""Native bitmap packing, protected ROM boundaries and guest ABI checks."""
import unittest

from PIL import Image

from emulator.firmware import ROOT
from tools.build_font_patch import SETS, DEFAULT_FONT, build, glyph, pack, text_bitmap
from tools.prove_font_patch import check_native_apis


@unittest.skipUnless(DEFAULT_FONT.exists(), 'Local Arial Narrow required')
class FontPatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sources = list((ROOT/'analysis/extracted').glob('SC*.bin'))
        if len(sources) != 1:
            raise unittest.SkipTest('Extracted original PS required')
        cls.original = sources[0].read_bytes()
        cls.patched, cls.manifest = build(cls.original)

    def test_non_byte_aligned_rows_have_no_padding(self):
        bitmap = Image.new('1', (3, 3))
        for xy in ((0,0), (2,0), (1,1), (0,2), (1,2), (2,2)):
            bitmap.putpixel(xy, 1)
        self.assertEqual(pack(bitmap), bytes((0xab, 0x80)))  # 101 010 111

    def test_only_glyph_slots_widths_and_two_instructions_change(self):
        allowed = bytearray(len(self.original))
        for s in SETS:
            allowed[s.widths:s.widths+len(s.codepoints)] = bytes([1])*len(s.codepoints)
            allowed[s.bitmap:s.end] = bytes([1])*(s.end-s.bitmap)
            # Offset tables, terminators, unknown tails and padding stay exact.
            self.assertEqual(self.original[s.offsets:s.bitmap], self.patched[s.offsets:s.bitmap])
            self.assertEqual(self.original[s.end:s.end+10], self.patched[s.end:s.end+10])
        for address in (0x5b8716, 0x5b871e):
            allowed[address:address+2] = b'\x01\x01'
        self.assertEqual(len(self.patched), len(self.original))
        self.assertTrue(all(a == b or ok for a,b,ok in zip(self.original,self.patched,allowed)))

    def test_russian_and_latin_text_is_narrower_and_required_glyphs_visible(self):
        for text in ('Мультимедиа', 'Настройки', 'ООО ооо 000', 'ABC xyz 0123'):
            self.assertLess(text_bitmap(self.patched,text,14,16).width,
                            text_bitmap(self.original,text,16,16).width)
        for s in SETS:
            height = 14 if s.height == 16 else 12
            for cp in ((0x401, 0x419, 0x414, 0x426, 0x429, 0x451) if 'cyrillic' in s.name else (0x41,0x4f,0x39)):
                self.assertIsNotNone(glyph(self.patched,s,s.codepoints.index(cp),height).getbbox())

    def test_all_glyph_descriptors_work_through_native_code(self):
        report = check_native_apis(self.patched)
        self.assertEqual(report['descriptors_checked'], (143+255)*3)
        self.assertEqual(report['status'], 'PASS')

    def test_unknown_layout_and_repatching_are_rejected(self):
        with self.assertRaises(ValueError):
            build(self.patched)
        damaged = bytearray(self.original)
        damaged[SETS[0].offsets] ^= 1
        with self.assertRaises(ValueError):
            build(bytes(damaged))


if __name__ == '__main__':
    unittest.main()
