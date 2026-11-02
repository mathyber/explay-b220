"""Graphics detour: exact image bounds, pixel preservation and both ABI paths."""
import gc
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from PIL import Image
from unicorn.arm_const import UC_ARM_REG_R0,UC_ARM_REG_PC,UC_ARM_REG_SP,UC_ARM_REG_LR,UC_ARM_REG_CPSR
from emulator.board import Board,ROOT
from emulator.debugger import Debugger
from tools.build_ui_label import build,HOOK,NATIVE_DRAW,ORIGINAL_TEXT,JPEG_START,JPEG_SIZE,TEXT


class UiLabelPatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = next((ROOT/'analysis/extracted').glob('SC*.bin')).read_bytes()
        cls.image,cls.manifest = build(cls.original)

    def tearDown(self):
        gc.collect()

    def test_bounded_repeatable_image_keeps_jpeg_pixels_and_old_marker(self):
        self.assertEqual(build(self.original),(self.image,self.manifest))
        self.assertEqual(len(self.image),len(self.original))
        allowed = set()
        for region in self.manifest['changed_regions']:
            start = int(region['start'],0)
            allowed.update(range(start,start+region['size']))
        self.assertFalse({i for i,(a,b) in enumerate(zip(self.image,self.original)) if a!=b}-allowed)
        with Image.open(BytesIO(self.original[JPEG_START:JPEG_START+JPEG_SIZE])) as a,Image.open(BytesIO(self.image[JPEG_START:JPEG_START+JPEG_SIZE])) as b:
            self.assertEqual(a.convert('RGB').tobytes(),b.convert('RGB').tobytes())
        self.assertEqual(self.image[0x152f2a:0x152f38],bytes.fromhex('48029000040d2000477042323230'))

    def test_tail_call_changes_only_text_and_count_and_preserves_other_titles(self):
        with TemporaryDirectory() as folder:
            path = Path(folder)/'patch.bin'
            path.write_bytes(self.image)
            for pointer,length in ((ORIGINAL_TEXT,5),(0x1294e8,4)):
                with self.subTest(pointer=hex(pointer)):
                    b = Board(explore=True,firmware_path=path)
                    values = [0x11220000+i for i in range(13)]
                    values[3] = pointer
                    for index,value in enumerate(values):
                        b.uc.reg_write(UC_ARM_REG_R0+index,value)
                    b.uc.reg_write(UC_ARM_REG_SP,0x4000e008)
                    b.uc.reg_write(UC_ARM_REG_LR,0x4000f001)
                    b.uc.reg_write(UC_ARM_REG_PC,HOOK)
                    b.uc.reg_write(UC_ARM_REG_CPSR,0xb00002f3)
                    stack = length.to_bytes(4,'big')+bytes(range(4,64))
                    b.uc.mem_write(0x4000e008,stack)
                    d = Debugger(b,[NATIVE_DRAW])
                    stop = d.run(100,2)
                    self.assertEqual(stop['stop']['reason'],'breakpoint')
                    for index in (0,1,2,4,5,6,7,8,9,10,11):
                        self.assertEqual(b.uc.reg_read(UC_ARM_REG_R0+index),values[index])
                    self.assertEqual(b.uc.reg_read(UC_ARM_REG_SP),0x4000e008)
                    self.assertEqual(b.uc.reg_read(UC_ARM_REG_LR),HOOK+5)
                    self.assertEqual(b.uc.reg_read(UC_ARM_REG_CPSR)&0xff,0xf3)
                    self.assertEqual(bytes(b.uc.mem_read(0x4000e00c,60)),stack[4:])
                    if pointer==ORIGINAL_TEXT:
                        target = int(self.manifest['string_address'],0)
                        self.assertEqual(b.uc.reg_read(UC_ARM_REG_R0+3),target)
                        self.assertEqual(int.from_bytes(b.uc.mem_read(0x4000e008,4),'big'),len(TEXT))
                        self.assertEqual(bytes(b.uc.mem_read(target,18)),TEXT.encode('utf-16-be')+bytes(2))
                    else:
                        self.assertEqual(b.uc.reg_read(UC_ARM_REG_R0+3),pointer)
                        self.assertEqual(int.from_bytes(b.uc.mem_read(0x4000e008,4),'big'),length)
                    d.close()


if __name__=='__main__':
    unittest.main()
