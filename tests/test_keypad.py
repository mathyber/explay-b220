"""Input edge ordering, interrupt acknowledgement and host command framing."""
import json
from pathlib import Path
import tempfile
import unittest

from emulator.keypad import Keypad, KEY_MATRIX
from emulator.interactive import CommandInbox


class KeypadTests(unittest.TestCase):
    def configured(self):
        keypad = Keypad()
        keypad.write(0,0x770007)
        keypad.write(4,0x777)
        return keypad

    def test_press_release_coordinate_and_w1c(self):
        keypad = self.configured()
        keypad.key('9',True)
        keypad.key('9',False)
        keypad.pump(1)
        self.assertTrue(keypad.pending)
        self.assertEqual(keypad.read(0x2c),0x52)
        keypad.pump(2)
        self.assertEqual(len(keypad.queue),1)
        self.assertEqual(keypad.read(16),0)
        keypad.write(16,0x10)  # unrelated acknowledgement must keep press
        self.assertTrue(keypad.pending)
        keypad.write(16,1)
        keypad.pump(3)
        self.assertEqual(keypad.read(8),0x10)
        self.assertEqual(keypad.read(0x2c),0x5a)
        keypad.write(16,0x10)
        self.assertFalse(keypad.pending)

    def test_masked_event_remains_latched(self):
        keypad = self.configured()
        keypad.write(4,0)
        keypad.key('ok',True)
        keypad.pump(1)
        self.assertEqual(keypad.read(12),1)
        self.assertFalse(keypad.pending)
        keypad.write(4,1)
        self.assertTrue(keypad.pending)

    def test_duplicate_down_and_focus_loss_do_not_stick(self):
        keypad = self.configured()
        self.assertTrue(keypad.key('up',True))
        self.assertFalse(keypad.key('up',True))
        keypad.release_all()
        keypad.release_all()
        self.assertEqual(list(keypad.queue),[('up',True),('up',False)])
        self.assertEqual(keypad.held,set())
        with self.assertRaises(ValueError):
            keypad.key('invalid',True)

    def test_power_gpio_is_separate_and_keeps_level_until_irq_return(self):
        keypad = self.configured()
        keypad.key('end',True)
        keypad.key('end',False)
        keypad.key('1',True)
        keypad.pump(1,0)
        self.assertEqual(len(keypad.queue),3)
        keypad.pump(2,8)
        self.assertEqual((keypad.power_raw,keypad.power_down,keypad.raw),(8,True,0))
        keypad.pump(3,8)
        self.assertEqual(len(keypad.queue),2)
        keypad.power_raw = 0  # GPIO W1C by guest, followed by IRQ return
        keypad.pump(4,8)
        self.assertEqual((keypad.power_raw,keypad.power_down),(8,False))
        keypad.power_raw = 0
        keypad.pump(5,8)
        self.assertEqual(keypad.raw,1)

    def test_physical_keys_match_original_firmware_translation(self):
        # Independent PS table: catches accidental aliases for the three call
        # keys, central OK, and directions. Red is absent from this matrix.
        image = next(Path('analysis/extracted').glob('SC*.bin')).read_bytes()
        native = {'soft_left':8,'soft_right':9,'up':4,'down':5,
                  'left':6,'right':7,'ok':13,'call_1':1,'call_2':26,'call_3':27,
                  'star':42,'hash':35,**{str(i):ord(str(i)) for i in range(10)}}
        self.assertEqual(set(native),set(KEY_MATRIX))
        for key,expected in native.items():
            offset = 0x3dd46c+KEY_MATRIX[key]*2
            self.assertEqual(int.from_bytes(image[offset:offset+2],'big'),expected,key)


class CommandInboxTests(unittest.TestCase):
    def test_fragmented_commands_malformed_lines_and_no_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'commands.jsonl'
            inbox = CommandInbox(path)
            self.assertEqual(inbox.poll(),[])
            path.write_bytes(b'{"action":"key","key":"1",')
            self.assertEqual(inbox.poll(),[])
            with path.open('ab') as stream:
                stream.write(b'"down":true}\ninvalid\n[]\n{"action":"pause"}\n')
            self.assertEqual(inbox.poll(),[dict(action='key',key='1',down=True),dict(action='pause')])
            self.assertEqual(inbox.poll(),[])
            with path.open('ab') as stream:
                stream.write((json.dumps(dict(action='stop'))+'\n').encode())
            self.assertEqual(inbox.poll(),[dict(action='stop')])


if __name__ == '__main__':
    unittest.main()
