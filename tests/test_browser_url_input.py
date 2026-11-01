import gc
import unittest
from unicorn.arm_const import UC_ARM_REG_R0
from emulator.board import Board
from tools.browser_url_input import BrowserUrlInput


class BrowserUrlInputTests(unittest.TestCase):
    def test_debugger_url_input_is_one_shot_and_preserves_https_and_later_links(self):
        b = Board()
        b.experimental_network = {}
        b.last_window_id = 0x4001b
        source = bytes(b.flash.array)
        input_source = BrowserUrlInput(b,'https://original.example/path?a=b')
        try:
            input_source._input(b.uc,0x1d49f6,2,None)
            self.assertEqual(b.cstring(b.uc.reg_read(UC_ARM_REG_R0)),'https://original.example/path?a=b')
            b.uc.reg_write(UC_ARM_REG_R0,0x04001000)
            input_source._input(b.uc,0x1d49f6,2,None)
            self.assertEqual(b.uc.reg_read(UC_ARM_REG_R0),0x04001000)
            self.assertEqual(bytes(b.flash.array),source)
        finally:
            input_source.close()
        del input_source,b
        gc.collect()

    def test_control_characters_are_rejected_before_mapping_memory(self):
        b = Board()
        b.experimental_network = {}
        b.last_window_id = 0x4001b
        regions = list(b.uc.mem_regions())
        with self.assertRaises(ValueError):
            BrowserUrlInput(b,'http://example.invalid/\r\nInjected: yes')
        self.assertEqual(list(b.uc.mem_regions()),regions)
        del b
        gc.collect()
