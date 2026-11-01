"""Regressions for Windows readers preventing replacement of the live PNG."""
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import gc
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Timer
import unittest
from unittest.mock import Mock

from PIL import Image
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1
from emulator.board import Board, ROOT
from emulator.frames import LiveFramePublisher
from emulator.lcd import Lcd9225


@contextmanager
def deny_replacement(path):
    """Real Win32 reader allowing reads/writes but denying FILE_SHARE_DELETE."""
    kernel = ctypes.WinDLL('kernel32',use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,
                       wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE)
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL
    handle = create(str(path),0x80000000,3,None,3,0x80,None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    def release():
        nonlocal handle
        if handle is not None:
            previous,handle = handle,None
            if not close(previous):
                raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield release
    finally:
        release()


class LiveFrameTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt','Win32 sharing semantics')
    def test_locked_preview_does_not_stop_cpu_or_lose_dma_pixels(self):
        with TemporaryDirectory(dir=ROOT/'analysis/emulator') as directory:
            path = Path(directory)/'live.png'
            Image.new('RGB',(176,220),'black').save(path)
            previous = path.read_bytes()
            publish = LiveFramePublisher(path)
            board = Board(explore=True,frame_callback=publish)
            try:
                board.lcd.index = 0x22
                board.uc.mem_write(0x04001000,bytes.fromhex('f80007e0'))
                for offset,value in ((0,0x04880160),(4,4),(8,0x04001000),
                                     (12,0x08020000),(20,0x20000),(24,2)):
                    board.storage[0x20100600+offset] = value
                board.uc.mem_write(0x4000f000,bytes.fromhex('e5810000'))  # STR r0,[r1]
                board.uc.reg_write(UC_ARM_REG_R0,0x10000)
                board.uc.reg_write(UC_ARM_REG_R1,0x20100010)
                with deny_replacement(path):
                    result = board.run(100,3,entry=0x4000f000,until=0x4000f004)
                    self.assertIsNone(result['engine_error'])
                    self.assertEqual(result['stop']['reason'],'requested end address')
                    self.assertEqual(board.lcd.gram[:2],[0xf800,0x07e0])
                    self.assertEqual(publish.skipped,1)
                    self.assertEqual(path.read_bytes(),previous)
                publish(board.lcd)
                self.assertEqual(publish.published,1)
                with Image.open(path) as image:
                    self.assertEqual(image.getpixel((0,0)),(255,0,0))
                    self.assertEqual(image.getpixel((1,0)),(0,255,0))
                self.assertEqual(list(Path(directory).glob('.*.tmp.png')),[])
            finally:
                del board
                gc.collect()

    @unittest.skipUnless(os.name == 'nt','Win32 sharing semantics')
    def test_short_reader_lock_is_retried(self):
        with TemporaryDirectory(dir=ROOT/'analysis/emulator') as directory:
            path = Path(directory)/'live.png'
            Image.new('RGB',(176,220),'black').save(path)
            lcd = Lcd9225()
            lcd.index = 0x22
            lcd.write_word(0xf800)
            with deny_replacement(path) as release:
                timer = Timer(0.025,release)
                timer.start()
                try:
                    lcd.save(directory,'live',write_raw=False)
                finally:
                    timer.join()
            with Image.open(path) as image:
                self.assertEqual(image.getpixel((0,0)),(255,0,0))

    def test_preview_does_not_hide_model_errors(self):
        lcd = Mock()
        lcd.save.side_effect = ValueError('invalid model state')
        with self.assertRaisesRegex(ValueError,'invalid model state'):
            LiveFramePublisher('unused.png')(lcd)


if __name__ == '__main__':
    unittest.main()
