"""MB and ABMP encoders against their decoders and the firmware's own images."""
import struct
import unittest

from PIL import Image

from tools.extract_images import FIRMWARE, catalog
from tools.image_codec import DECODERS, decode_abmp, decode_mb, encode, encode_abmp, encode_mb


def pattern(width, height, alpha=False):
    picture = Image.new('RGBA', (width, height))
    for y in range(height):
        for x in range(width):
            if (x+y) % 7 == 0:
                picture.putpixel((x, y), (0, 0, 0, 0))
            elif x < width//2:
                picture.putpixel((x, y), (248, 0, 0, 255))  # long runs
            else:
                picture.putpixel((x, y), (x*8 % 256, y*4 % 256, 120, (40+x*3) % 255 if alpha else 255))
    return picture


class ImageCodecTests(unittest.TestCase):
    def test_mb_round_trip_with_transparency_and_long_rows(self):
        for size in ((11, 11), (300, 3), (1, 1)):
            picture = pattern(*size)
            data = encode_mb(picture)
            self.assertEqual(data[:2], b'MB')
            self.assertEqual(struct.unpack_from('>I', data, 2)[0], len(data))
            back = decode_mb(data)
            self.assertEqual(list(back.getdata()), list(decode_mb(encode_mb(back)).getdata()))
            self.assertEqual([p[3] for p in back.getdata()], [255 if p[3] >= 128 else 0 for p in picture.getdata()])

    def test_mb_keeps_an_opaque_magenta_visible(self):
        picture = Image.new('RGBA', (3, 1), (255, 0, 255, 255))
        self.assertTrue(all(p[3] == 255 for p in decode_mb(encode_mb(picture)).getdata()))

    def test_mb_packets_always_repeat_like_the_firmware(self):
        data = encode_mb(pattern(40, 5))
        if struct.unpack_from('>I', data, 30)[0] == 0x1003:
            at = 66
            for _ in range(5):
                while True:
                    repeat, literal = data[at], data[at+1]
                    at += 2
                    if not repeat and not literal:
                        break
                    self.assertGreaterEqual(repeat, 1)
                    at += 2*literal+2

    def test_abmp_round_trip_with_alpha_and_margins(self):
        picture = Image.new('RGBA', (20, 12), (0, 0, 0, 0))
        picture.paste(pattern(10, 6, alpha=True), (4, 3))
        back = decode_abmp(encode_abmp(picture))
        expected = decode_abmp(encode_abmp(back))
        self.assertEqual(list(back.getdata()), list(expected.getdata()))
        self.assertEqual(back.getpixel((0, 0)), (0, 0, 0, 0))
        self.assertEqual(back.getpixel((4, 3)), (0, 0, 0, 0))   # (x+y) % 7 == 0 inside the pattern
        self.assertEqual(back.getpixel((5, 3))[3], 255)
        self.assertEqual(decode_abmp(encode_abmp(Image.new('RGBA', (5, 5)))).getpixel((2, 2)), (0, 0, 0, 0))

    @unittest.skipUnless(FIRMWARE.exists(), 'Built firmware required')
    def test_every_firmware_image_round_trips_no_larger(self):
        rom = FIRMWARE.read_bytes()
        entries = [e for e in catalog(rom) if e['type'] in DECODERS]
        self.assertGreater(len(entries), 1000)
        for entry in entries:
            picture = DECODERS[entry['type']](rom, int(entry['address'], 16))
            data = encode(picture, entry['type'])
            self.assertEqual(list(DECODERS[entry['type']](data).getdata()), list(picture.getdata()), entry)
            self.assertLessEqual(len(data), entry['size'], entry)


if __name__ == '__main__':
    unittest.main()
