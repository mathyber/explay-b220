"""Native application packaging: compiler format, bounded writes and input guards."""
from io import BytesIO
from tempfile import TemporaryDirectory
import struct
import unittest

from PIL import Image
from emulator.board import ROOT
from tools.build_catch_game import build, compile_game, elf_payload, ROM, LIMIT, POINTERS
from tools.build_key_marker import JPEG_START, JPEG_SIZE
from tools.setup_arm_toolchain import EXECUTABLE


@unittest.skipUnless(EXECUTABLE.exists(), 'Pinned local ARM compiler is not installed')
class CatchGamePatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = next((ROOT/'analysis/extracted').glob('SC*.bin')).read_bytes()
        with TemporaryDirectory() as folder:
            from pathlib import Path
            cls.payload, cls.entry, _ = compile_game(Path(folder))
            cls.elf = (Path(folder)/'catch-game.elf').read_bytes()
        cls.image, cls.manifest = build(cls.original, cls.payload, cls.entry)

    def test_compiled_thumb_be_application_changes_only_declared_slots(self):
        self.assertEqual(elf_payload(self.elf), (self.payload, self.entry))
        self.assertEqual(build(self.original, self.payload, self.entry), (self.image, self.manifest))
        self.assertEqual(len(self.image), len(self.original))
        allowed = bytearray(len(self.original))
        for region in self.manifest['changed_regions']:
            start, size = int(region['start'],0), region['size']
            allowed[start:start+size] = b'\x01'*size
        self.assertTrue(all(allowed[i] for i,(a,b) in enumerate(zip(self.image,self.original)) if a!=b))
        self.assertEqual(self.image[ROM:ROM+len(self.payload)], self.payload)
        for pointer in POINTERS:
            self.assertEqual(int.from_bytes(self.image[pointer:pointer+4],'big'), self.entry)
        self.assertEqual(self.image[LIMIT:LIMIT+14], bytes.fromhex('48029000040d2000477042323230'))
        with Image.open(BytesIO(self.image[JPEG_START:JPEG_START+JPEG_SIZE])) as wallpaper:
            self.assertEqual(wallpaper.size, (176,220))
            wallpaper.load()

    def test_rejects_wrong_source_and_payload_overflow_or_wrong_entry(self):
        wrong = bytearray(self.original)
        wrong[0] ^= 1
        with self.assertRaises(ValueError):
            build(wrong,self.payload,self.entry)
        for payload,entry in ((b'',self.entry), (b'x'*(LIMIT-ROM+1),self.entry),
                              (self.payload,ROM), (self.payload,(ROM+len(self.payload)+4)|1)):
            with self.subTest(entry=entry,size=len(payload)), self.assertRaises(ValueError):
                build(self.original,payload,entry)

    def test_rejects_truncated_or_wrong_architecture_elf(self):
        for data in (b'',self.elf[:40],self.elf[:-1]):
            with self.assertRaises(ValueError):
                elf_payload(data)
        for offset,value in ((5,b'\x01'), (18,b'\x00\x03'), (24,struct.pack('>I',ROM)),
                             (32,struct.pack('>I',len(self.elf)))):
            invalid = bytearray(self.elf)
            invalid[offset:offset+len(value)] = value
            with self.assertRaises(ValueError):
                elf_payload(invalid)


if __name__=='__main__':
    unittest.main()
