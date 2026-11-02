"""Execute an image-only Thumb detour and compare its resumed native state."""
import gc
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from PIL import Image
from unicorn.arm_const import UC_ARM_REG_R0,UC_ARM_REG_PC,UC_ARM_REG_SP,UC_ARM_REG_LR,UC_ARM_REG_CPSR
from emulator.board import Board,ROOT
from emulator.debugger import Debugger
from tools.build_key_marker import build,thumb_bl,ENTRY,HOOK,RESUME,JPEG_START,JPEG_SIZE,MARKER


class FirmwarePatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.original = next((ROOT/'analysis/extracted').glob('SC*.bin')).read_bytes()
        self.image,self.manifest = build(self.original)
        self.path = Path(self.temp.name)/'patch.bin'
        self.path.write_bytes(self.image)

    def tearDown(self):
        self.temp.cleanup()
        gc.collect()

    def test_reproducible_bounded_patch_and_identical_jpeg_pixels(self):
        self.assertEqual(build(self.original),(self.image,self.manifest))
        self.assertEqual(len(self.original),len(self.image))
        changed = {i for i,(a,b) in enumerate(zip(self.original,self.image)) if a!=b}
        allowed = set(range(JPEG_START,JPEG_START+JPEG_SIZE))|set(range(HOOK,RESUME))
        self.assertTrue(changed)
        self.assertFalse(changed-allowed)
        a = Image.open(BytesIO(self.original[JPEG_START:JPEG_START+JPEG_SIZE]))
        b = Image.open(BytesIO(self.image[JPEG_START:JPEG_START+JPEG_SIZE]))
        self.assertEqual(a.convert('RGB').tobytes(),b.convert('RGB').tobytes())
        a.close()
        b.close()
        corrupted = bytearray(self.original)
        corrupted[HOOK] ^= 1
        with self.assertRaisesRegex(ValueError,'exact original'):
            build(corrupted)
        with self.assertRaisesRegex(ValueError,'exact original'):
            build(self.image)
        with self.assertRaises(ValueError):
            thumb_bl(0,0x800000)

    def test_thumb_detour_writes_only_native_local_then_replays_exact_state(self):
        results = []
        for experimental in (False,True):
            b = Board(explore=True,firmware_path=self.path if experimental else None)
            for index in range(13):
                b.uc.reg_write(UC_ARM_REG_R0+index,0x12340000+index*0x123)
            b.uc.reg_write(UC_ARM_REG_SP,0x4000e008)
            b.uc.reg_write(UC_ARM_REG_LR,0x4000f001)
            b.uc.reg_write(UC_ARM_REG_PC,ENTRY)
            b.uc.reg_write(UC_ARM_REG_CPSR,0xb00002f3)
            b.uc.mem_write(0x4000dfe0,b'\xa5'*64)
            checkpoint = int(self.manifest['marker_checkpoint'],0)
            d = Debugger(b,[checkpoint if experimental else RESUME])
            stop = d.run(40,2)
            self.assertEqual(stop['stop']['reason'],'breakpoint')
            self.assertEqual(b.uc.reg_read(UC_ARM_REG_SP),0x4000dff8)
            if experimental:
                self.assertEqual(bytes(b.uc.mem_read(0x4000dff8,4)),MARKER)
                self.assertEqual(bytes(b.uc.mem_read(0x4000e004,4)),bytes.fromhex('4000f001'))
                d.replace([RESUME])
                self.assertEqual(d.run(40,2)['stop']['reason'],'breakpoint')
            registers = [b.uc.reg_read(UC_ARM_REG_R0+i) for i in range(13)]
            self.assertTrue(b.uc.reg_read(UC_ARM_REG_CPSR)&0x20)
            results.append(dict(registers=registers,sp=b.uc.reg_read(UC_ARM_REG_SP),
                                cpsr=b.uc.reg_read(UC_ARM_REG_CPSR)))
            # Original STR-zero clears the marker. Compare through the next
            # native callsite: all original instructions still execute.
            d.replace([0x1a1246])
            self.assertEqual(d.run(40,2)['stop']['reason'],'breakpoint')
            self.assertEqual(bytes(b.uc.mem_read(0x4000dff8,4)),bytes(4))
            self.assertEqual(bytes(b.uc.mem_read(0x4000dfe0,24)),b'\xa5'*24)
            self.assertEqual(bytes(b.uc.mem_read(0x4000e008,24)),b'\xa5'*24)
            d.close()
        # BL uses LR transiently; caller LR is saved in the unchanged native
        # prologue. Every other register and all CPSR bits match at RESUME.
        self.assertEqual(results[0],results[1])


if __name__ == '__main__':
    unittest.main()
