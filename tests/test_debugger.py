"""Break before instructions, resume loops, Thumb stepping and image identity."""
import gc
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from unicorn.arm_const import UC_ARM_REG_PC,UC_ARM_REG_CPSR,UC_ARM_REG_R0
from emulator.board import Board,ROOT
from emulator.debugger import Debugger
from emulator.firmware import load_firmware,protect_outputs,experiment_directory
from emulator.snapshot import save_snapshot,load_snapshot,SnapshotError


class DebuggerTests(unittest.TestCase):
    def tearDown(self):
        gc.collect()

    def test_arm_breakpoint_before_instruction_resume_rehits_and_step(self):
        b = Board(explore=True)
        base = 0x4000e000
        b.uc.mem_write(base,bytes.fromhex('e2800001eafffffd'))
        b.uc.reg_write(UC_ARM_REG_PC,base)
        original = bytes(b.uc.mem_read(base,8))
        d = Debugger(b,[base+4])
        for expected in (1,2):
            result = d.run(100,2)
            self.assertEqual(result['stop'],dict(reason='breakpoint',pc=hex(base+4)))
            self.assertEqual(b.uc.reg_read(UC_ARM_REG_R0),expected)
        result = d.run(1,2,step=True)
        self.assertEqual(result['stop']['reason'],'debug step')
        self.assertEqual(b.uc.reg_read(UC_ARM_REG_PC),base)
        self.assertEqual(b.uc.reg_read(UC_ARM_REG_R0),2)
        d.inspect(base)
        self.assertEqual(len(d.view['memory']),8)
        self.assertIn('add',d.view['disassembly'][0])
        self.assertEqual(bytes(b.uc.mem_read(base,8)),original)
        with self.assertRaisesRegex(ValueError,'MMIO'):
            d.memory(0x80000000)
        with self.assertRaises(ValueError):
            d.replace([0x12345678])
        self.assertEqual(d.status()['breakpoints'],[hex(base+4)])
        d.close()

    def test_thumb_alias_breakpoint_and_single_instruction(self):
        b = Board(explore=True)
        base = 0x4000e000
        b.uc.mem_write(base,bytes.fromhex('3001e7fd'))
        b.uc.reg_write(UC_ARM_REG_PC,base)
        b.uc.reg_write(UC_ARM_REG_CPSR,0x2f3)
        d = Debugger(b,[base|1])
        self.assertEqual(d.run(100,2)['stop']['reason'],'breakpoint')
        self.assertEqual(b.uc.reg_read(UC_ARM_REG_R0),0)
        self.assertEqual(d.view['mode'],'Thumb')
        self.assertEqual(d.run(1,2,step=True)['stop']['reason'],'debug step')
        self.assertEqual(b.uc.reg_read(UC_ARM_REG_PC),base+2)
        self.assertEqual(b.uc.reg_read(UC_ARM_REG_R0),1)
        self.assertEqual(d.run(100,2)['stop']['reason'],'breakpoint')
        self.assertEqual(b.uc.reg_read(UC_ARM_REG_R0),1)
        d.replace([])
        self.assertEqual(d.run(4,2)['stop']['reason'],'instruction budget')
        d.close()

    def test_breakpoint_has_priority_over_original_assertion_stop(self):
        b = Board(explore=True)
        b.uc.reg_write(UC_ARM_REG_PC,0x4822ac)
        d = Debugger(b,[0x4822ac])
        self.assertEqual(d.run(1,2)['stop']['reason'],'breakpoint')
        d.replace([])
        self.assertEqual(d.run(1,2)['stop']['reason'],'firmware assertion')
        d.close()


class FirmwareTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.profile = json.loads((ROOT/'emulator/profiles/b220-l7a.json').read_text())
        self.original,self.identity = load_firmware(self.profile)
        self.path = self.folder/'experiment.bin'
        image = bytearray(self.original)
        image[:8] = bytes.fromhex('e3a0002aeafffffe')
        self.path.write_bytes(image)

    def tearDown(self):
        self.temp.cleanup()
        gc.collect()

    def test_selected_image_executes_and_snapshots_require_same_hash(self):
        b = Board(explore=True,firmware_path=self.path)
        self.assertTrue(b.firmware['modified'])
        from emulator.window_network import WindowNetwork
        network = WindowNetwork(b,self.folder/'network.json')
        with self.assertRaisesRegex(ValueError,'original PS'):
            network.configure(enabled=True)
        self.assertFalse(network.enabled)
        network.close()
        self.assertEqual(b.image_sha256,hashlib.sha256(self.path.read_bytes()).hexdigest())
        result = b.run(1,2)
        self.assertIsNone(result['engine_error'])
        self.assertEqual(b.uc.reg_read(UC_ARM_REG_R0),42)
        saved = self.folder/'test.b220state'
        d = Debugger(b,[4])
        save_snapshot(b,saved)  # Debugger hooks are host controls, not board state.
        with self.assertRaisesRegex(SnapshotError,'profile/firmware'):
            load_snapshot(saved)
        copy = self.folder/'same-image-another-name.bin'
        copy.write_bytes(self.path.read_bytes())
        restored = load_snapshot(saved,firmware_path=copy)
        self.assertEqual(restored.uc.reg_read(UC_ARM_REG_R0),42)
        self.assertEqual(restored.image_sha256,b.image_sha256)
        changed = bytearray(copy.read_bytes())
        changed[-1] ^= 1
        copy.write_bytes(changed)
        with self.assertRaisesRegex(SnapshotError,'profile/firmware'):
            load_snapshot(saved,firmware_path=copy)
        with self.assertRaisesRegex(ValueError,'selected PS'):
            Board(explore=True,flash_state=bytes(b.flash.array))
        resumed = Board(explore=True,firmware_path=self.path,flash_state=bytes(b.flash.array))
        self.assertEqual(resumed.image_sha256,b.image_sha256)
        d.close()
        self.assertEqual(load_firmware(self.profile)[0],self.original)

    def test_invalid_inputs_and_output_aliases_fail_before_writing(self):
        _,identity = load_firmware(self.profile,self.path)
        self.assertNotEqual(experiment_directory(identity),experiment_directory(self.identity))
        for protected in (self.path,Path(self.identity['source'])):
            with self.assertRaisesRegex(ValueError,'overwrite'):
                protect_outputs(self.profile,identity,[protected])
        before = self.path.read_bytes()
        run = subprocess.run([sys.executable,'-m','emulator.run','--firmware',str(self.path),
                              '--out',str(self.path),'--instructions','1'],cwd=ROOT,capture_output=True,text=True)
        self.assertNotEqual(run.returncode,0)
        self.assertIn('overwrite',run.stderr)
        self.assertEqual(self.path.read_bytes(),before)
        self.path.write_bytes(b'bad input')
        with self.assertRaisesRegex(ValueError,'raw PS'):
            Board(firmware_path=self.path)


if __name__ == '__main__':
    unittest.main()
