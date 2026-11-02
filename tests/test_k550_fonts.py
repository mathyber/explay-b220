"""Check original K550 font integrity and Russian coverage after B220 packing."""
import struct
import unittest
from unittest.mock import patch

from tools.build_font_patch import build, glyph, BASELINE_SHIFTS, SETS, DEFAULT_SOURCE
from tools.extract_k550_fonts import DIRECTORY, SOURCE_NAME, FONTS, RUSSIAN, extract, validate_ttf


@unittest.skipUnless((DIRECTORY/SOURCE_NAME).exists() and DEFAULT_SOURCE.exists(),
                     'Locally downloaded K550 FS and built SD APP image required')
class K550FontTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (DIRECTORY/SOURCE_NAME).read_bytes()
        cls.fonts, cls.manifest = extract(cls.source)

    def test_regular_and_bold_are_complete_original_fonts(self):
        self.assertEqual(len(self.manifest['fonts']), 2)
        for name, _, _ in FONTS:
            self.assertEqual(self.fonts[name], (DIRECTORY/name).read_bytes())
            self.assertEqual(validate_ttf(self.fonts[name])['russian_letters'], 66)

    def test_corrupt_download_and_corrupt_font_are_rejected(self):
        with self.assertRaises(ValueError):
            extract(self.source[:-1])
        bad = bytearray(self.fonts[FONTS[0][0]])
        bad[0x180] ^= 1
        with self.assertRaises(ValueError):
            validate_ttf(bytes(bad))

    def test_all_russian_letters_use_new_font_in_both_sizes(self):
        # Ensure the existing fallback mechanism never silently retains an old
        # Russian glyph. Test both faces even though the launcher uses Regular.
        for name, _, _ in FONTS:
            _, manifest = build(DEFAULT_SOURCE.read_bytes(), DIRECTORY/name)
            for s in manifest['sets']:
                if not s['name'].startswith('cyrillic'):
                    continue
                records = {int(g['codepoint'][2:],16):g for g in s['glyphs']}
                for cp in RUSSIAN:
                    with self.subTest(font=name, table=s['name'], codepoint=hex(cp)):
                        self.assertFalse(records[cp]['fallback'])
                        self.assertGreater(records[cp]['width'], 0)

    def test_cyrillic_capital_k_uses_shared_baseline_without_moving_other_glyphs(self):
        source = DEFAULT_SOURCE.read_bytes()
        font = DIRECTORY/FONTS[0][0]
        with patch.dict(BASELINE_SHIFTS, clear=True):
            before, _ = build(source, font)
        after, manifest = build(source, font)
        permitted = bytearray(len(source))
        for s in SETS:
            if not s.name.startswith('cyrillic'):
                continue
            height = 14 if s.height == 16 else 12
            k_index = s.codepoints.index(0x41a)
            start = s.bitmap+struct.unpack_from('>I', before, s.offsets+k_index*4)[0]
            capacity = (source[s.widths+k_index]*s.height+7)//8
            permitted[start:start+capacity] = bytes([1])*capacity
            old = glyph(before, s, k_index, height)
            new = glyph(after, s, k_index, height)
            self.assertEqual(old.width, new.width)
            self.assertEqual(new.getbbox()[1], old.getbbox()[1]+1)
            for y in range(height):
                for x in range(new.width):
                    self.assertEqual(new.getpixel((x,y)), old.getpixel((x,y-1)) if y else 0)
            for cp in (0x41d, 0x43e, 0x43d, 0x442):
                self.assertEqual(new.getbbox()[3], glyph(after,s,s.codepoints.index(cp),height).getbbox()[3])
        # Includes descenders and accents: every byte outside the two K slots
        # remains exact, including ALL widths, offsets and height instructions.
        self.assertTrue(all(a == b or ok for a,b,ok in zip(before,after,permitted)))
        shifts = [(s['name'],g['codepoint'],g['baseline_shift'])
                  for s in manifest['sets'] for g in s['glyphs'] if g['baseline_shift']]
        self.assertEqual(shifts, [('cyrillic16','U+041A',1), ('cyrillic14','U+041A',1)])


if __name__ == '__main__':
    unittest.main()
