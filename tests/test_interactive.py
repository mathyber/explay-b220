"""Startup must continue past the input checkpoint without a phone key."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from emulator.interactive import InteractiveSession


class InteractiveBootTests(unittest.TestCase):
    def test_cold_boot_keeps_executing_without_input_and_manual_pause_still_works(self):
        with TemporaryDirectory() as folder:
            b = SimpleNamespace(keypad=SimpleNamespace(events=[]),uc=SimpleNamespace(reg_read=lambda _:0),
                                block_serial=0,last_window_id=0,lcd_backlight=True,
                                key_callbacks=[],lcd=SimpleNamespace(pixel_writes=0),spi=None,stop=None)
            steps = []
            def run(*_,**__):
                steps.append(b.block_serial)
                b.block_serial += 512000
                reason = 'requested LCD frame count' if len(steps)==1 else 'wall time budget' if len(steps)==2 else 'requested end address'
                b.stop = dict(reason=reason)
                return dict(stop=b.stop,elapsed_seconds=0,engine_error=None)
            b.run = run
            b.report = lambda *_:dict(stop=b.stop)
            command_path = Path(folder)/'commands.jsonl'
            args = SimpleNamespace(commands=command_path,session_status=Path(folder)/'status.json',
                                   out='test.json',frames=5,seconds=10,instructions=100,until=0xffffffff)
            session = InteractiveSession(b,args,lambda *_:None)
            publish = session.publish
            def check_progress(**kwargs):
                if session.ready:
                    self.assertFalse(session.paused,'The input checkpoint must not freeze the boot animation')
                publish(**kwargs)
            session.publish = check_progress
            session.run()
            self.assertEqual(len(steps),3,'CPU must execute another slice after becoming ready, without key input')
            self.assertFalse(b.keypad.events)
            command_path.write_text('{"action":"pause"}\n')
            session.commands()
            self.assertTrue(session.paused)
            with command_path.open('a') as stream:
                stream.write('{"action":"resume"}\n')
            session.commands()
            self.assertFalse(session.paused)


if __name__ == '__main__':
    unittest.main()
