"""Snapshot integrity, ARM banks and deterministic continuation in a new engine."""
import copy
import gc
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import zipfile

from unicorn import UC_HOOK_CODE
from unicorn.arm_const import (UC_ARM_REG_R0,UC_ARM_REG_R2,UC_ARM_REG_SP,
                               UC_ARM_REG_LR,UC_ARM_REG_PC,UC_ARM_REG_CPSR,UC_ARM_REG_SPSR)
from emulator.board import Board,ROOT
from emulator.snapshot import save_snapshot,load_snapshot,cpu_state,SnapshotError


def entry(board):
    return board.uc.reg_read(UC_ARM_REG_PC)|(1 if board.uc.reg_read(UC_ARM_REG_CPSR)&0x20 else 0)


class SnapshotCase(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.path = self.directory/'test.b220state'

    def tearDown(self):
        self.temporary.cleanup()
        gc.collect()

    def rewrite(self,transform=None,damage_blob=False):
        with zipfile.ZipFile(self.path) as archive:
            files = {name:archive.read(name) for name in archive.namelist()}
        manifest = json.loads(files['manifest.json'])
        if transform:
            transform(manifest)
        if damage_blob:
            name = next(iter(manifest['blobs']))
            files[name] = files[name][:-1]+bytes([files[name][-1]^1])
        files['manifest.json'] = json.dumps(manifest).encode()
        with zipfile.ZipFile(self.path,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=1) as archive:
            for name,data in files.items():
                archive.writestr(name,data)

    def loop_board(self):
        b = Board(explore=True)
        # A small CPU fixture in SRAM; no firmware bytes are modified.
        code = bytes.fromhex('e59f1014e5910000e2800001e5810000e25220011afffffae12fff1e04001000')
        b.uc.mem_write(0x4000e000,code)
        b.uc.reg_write(UC_ARM_REG_PC,0x4000e000)
        b.uc.reg_write(UC_ARM_REG_R2,20000)
        b.uc.reg_write(UC_ARM_REG_LR,0x4000fff0)
        timer = b.countdown_timers[0x81000000]
        timer.write(0,64,0)
        timer.write(12,1,0)
        timer.write(8,0x80,0)
        b._sync_timer_events()
        b.rtc.blocks_per_second = 100
        b.keypad.write(0,1)
        b.keypad.write(4,0x777)
        b.keypad.key('up',True)
        b.keypad.key('up',False)
        return b

    def assert_guest_equal(self,a,b):
        self.assertEqual(cpu_state(a),cpu_state(b))
        self.assertEqual(a.block_serial,b.block_serial)
        self.assertEqual(a.next_timer_block,b.next_timer_block)
        self.assertEqual(a.dma_enabled,b.dma_enabled)
        self.assertEqual(a.storage,b.storage)
        for name in ('flash','lcd','keypad','jpeg','rtc','dsp','irq'):
            self.assertEqual(vars(getattr(a,name)),vars(getattr(b,name)),name)
        for address in a.countdown_timers:
            self.assertEqual(vars(a.countdown_timers[address]),vars(b.countdown_timers[address]))
        for address in a.spi_controllers:
            self.assertEqual(vars(a.spi_controllers[address]),vars(b.spi_controllers[address]))
        for address,size in ((0,0x800000),(0x04000000,0x400000),(0x40000000,0x10000),(0x10000000,0x1000)):
            self.assertEqual(hashlib.sha256(a.uc.mem_read(address,size)).digest(),
                             hashlib.sha256(b.uc.mem_read(address,size)).digest(),hex(address))

class SnapshotTests(SnapshotCase):
    def test_continuation_preserves_virtual_time_ram_and_queued_input(self):
        b = self.loop_board()
        first = b.run(12345,5,entry=entry(b))
        self.assertEqual(first['stop']['reason'],'instruction budget')
        save_snapshot(b,self.path)
        restored = load_snapshot(self.path)
        self.assert_guest_equal(b,restored)
        for board in (b,restored):
            result = board.run(200000,10,entry=entry(board),until=0x4000fff0)
            self.assertEqual(result['stop']['reason'],'requested end address')
        self.assertEqual(int.from_bytes(restored.uc.mem_read(0x04001000,4),'big'),20000)
        self.assertTrue(restored.countdown_timers[0x81000000].raw)
        self.assert_guest_equal(b,restored)

    def test_partial_devices_banked_registers_and_ram_alias(self):
        b = Board(explore=True)
        for mode in (0x10,0x11,0x12,0x13,0x17,0x1b):
            b.uc.reg_write(UC_ARM_REG_CPSR,0x2c0|mode)
            b.uc.reg_write(UC_ARM_REG_SP,0x40000000+mode*32)
            b.uc.reg_write(UC_ARM_REG_LR,0x40002000+mode*32)
            if mode == 0x11:
                for i in range(8,13):
                    b.uc.reg_write(UC_ARM_REG_R0+i,0xf1000000+i)
            if mode != 0x10:
                b.uc.reg_write(UC_ARM_REG_SPSR,0xa00002f3)
        b.uc.reg_write(UC_ARM_REG_CPSR,0x2d2)
        b.uc.reg_write(UC_ARM_REG_PC,0x4000d000)
        b.uc.reg_write(UC_ARM_REG_LR,0x4000e004)
        b.uc.reg_write(UC_ARM_REG_SPSR,0xa00002f3)
        b.uc.mem_write(0x4000d000,bytes.fromhex('e25ef004'))  # exception return
        b.uc.mem_write(0x34001234,b'aliased RAM')
        b.uc.mem_map(0x8a000000,0x1000,3)
        b.pages.add(0x8a000000)
        b.uc.mem_write(0x8a000000,bytes.fromhex('12345678'))
        b.lcd.command_hi = 0
        b.lcd.data_hi = 0xab
        b.lcd.index = 0x22
        b.lcd.gram[7] = 0xf81f
        b.lcd.pixel_writes = 1
        b.jpeg.decoded = bytes(range(64))
        b.jpeg.bit_position = 17
        b.jpeg.bit_value = 9
        b.dsp.staged = (0,b'\x12\x34')
        b.keypad.key('end',True)
        b.keypad.pump(0,8)
        b.spi.rx_remaining = 3
        b.flash.pending = ('program',0x7e1000)
        b.flash.mode = 'status'
        b.dma_enabled = 1
        save_snapshot(b,self.path)
        restored = load_snapshot(self.path)
        self.assert_guest_equal(b,restored)
        self.assertIs(restored.spi,restored.spi_controllers[0x8e000000])
        restored.uc.mem_write(0x34001234,b'new')
        self.assertEqual(bytes(restored.uc.mem_read(0x04001234,3)),b'new')
        self.assertEqual(bytes(b.uc.mem_read(0x04001234,3)),b'ali')
        self.assertEqual(bytes(restored.uc.mem_read(0x8a000000,4)),bytes.fromhex('12345678'))
        restored.lcd.write(True,2,0xcd00)
        self.assertEqual(restored.lcd.gram[0],0xabcd)
        self.assertTrue(restored.dsp.write(0,5,lambda size:b''))
        self.assertEqual(restored.dsp.image,bytes.fromhex('1234'))
        restored.spi.read(0)
        self.assertEqual(restored.spi_controllers[0x8e000000].rx_remaining,2)
        result = restored.run(1,2,entry=entry(restored),until=0x4000e000)
        self.assertIn(result['stop']['reason'],('instruction budget','requested end address'))
        self.assertEqual(restored.uc.reg_read(UC_ARM_REG_PC),0x4000e000)
        self.assertEqual(restored.uc.reg_read(UC_ARM_REG_CPSR),0xa00002f3)
        restored.flash.write(0x7e1000,2,0x1234)
        self.assertEqual(restored.flash.array[0x7e1000:0x7e1002],bytes.fromhex('1234'))

    def test_saved_host_controls_are_reset_and_nor_file_is_not_written(self):
        b = Board(flash_state=bytes(Board().flash.array))
        b.stop_after_frames = 5
        b.pending_frame_stop = dict(reason='requested LCD frame count')
        b.deadline = 123.0
        b.started_at = 122.0
        before = cpu_state(b)
        save_snapshot(b,self.path)
        self.assertEqual(cpu_state(b),before)
        restored = load_snapshot(self.path)
        self.assertIsNone(restored.nv_seed)
        self.assertEqual(restored.stop_after_frames,0)
        self.assertIsNone(restored.pending_frame_stop)
        self.assertIsNone(restored.deadline)
        self.assertFalse(restored._running)
        self.assertEqual(list(self.directory.iterdir()),[self.path])

    def test_incompatible_profiles_versions_and_damaged_blocks_are_rejected(self):
        b = Board()
        for field,value in (('version',999),('model_sha256','0'*64),('firmware_sha256','0'*64)):
            with self.subTest(field=field):
                save_snapshot(b,self.path)
                self.rewrite(lambda manifest:manifest.__setitem__(field,value))
                with self.assertRaises(SnapshotError):
                    load_snapshot(self.path)
        save_snapshot(b,self.path)
        profile = copy.deepcopy(b.profile)
        profile['chip_id'] = '0x12345678'
        with self.assertRaisesRegex(SnapshotError,'profile/firmware'):
            load_snapshot(self.path,profile=profile)
        self.rewrite(damage_blob=True)
        with self.assertRaisesRegex(SnapshotError,'checksum'):
            load_snapshot(self.path)

    def test_invalid_memory_layout_and_cpu_data_are_rejected(self):
        b = Board()
        save_snapshot(b,self.path)
        self.rewrite(lambda manifest:manifest['memory'][1].__setitem__('address',0x60000000))
        with self.assertRaisesRegex(SnapshotError,'layout'):
            load_snapshot(self.path)
        save_snapshot(b,self.path)
        self.rewrite(lambda manifest:manifest['cpu']['registers'].__setitem__(0,1<<40))
        with self.assertRaisesRegex(SnapshotError,'32-bit'):
            load_snapshot(self.path)

    def test_atomic_save_failure_keeps_previous_file_and_active_cpu_is_rejected(self):
        b = Board()
        save_snapshot(b,self.path)
        before = self.path.read_bytes()
        with patch('emulator.snapshot.os.replace',side_effect=PermissionError('locked')):
            with self.assertRaises(PermissionError):
                save_snapshot(b,self.path)
        self.assertEqual(self.path.read_bytes(),before)
        self.assertEqual(list(self.directory.iterdir()),[self.path])
        b._running = True
        with self.assertRaisesRegex(SnapshotError,'Stop CPU'):
            save_snapshot(b,self.path)
        b._running = False
        with self.assertRaisesRegex(SnapshotError,'extension'):
            save_snapshot(b,self.directory/'firmware.bin')

    def test_cli_resumes_in_another_process_and_reports_bad_files_without_traceback(self):
        b = self.loop_board()
        save_snapshot(b,self.path)
        output = self.directory/'report.json'
        target = self.directory/'continued.b220state'
        command = [sys.executable,'-m','emulator.run','--load-state',str(self.path),
                   '--save-state',str(target),'--instructions','200000','--seconds','10',
                   '--until','0x4000fff0','--out',str(output)]
        result = subprocess.run(command,cwd=ROOT,capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        restored = load_snapshot(target)
        self.assertEqual(int.from_bytes(restored.uc.mem_read(0x04001000,4),'big'),20000)
        self.assertEqual(json.loads(output.read_text())['stop']['reason'],'requested end address')
        self.path.write_bytes(b'not a snapshot')
        result = subprocess.run(command,cwd=ROOT,capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,2)
        self.assertIn('Cannot load snapshot',result.stderr)
        self.assertNotIn('Traceback',result.stderr)


class FirmwareSnapshotTests(SnapshotCase):
    def test_original_firmware_continues_identically_before_and_inside_irq(self):
        b = Board(explore=True)
        def stop_at_irq(uc,address,size,data):
            b.stop = dict(reason='IRQ snapshot checkpoint',pc=hex(address))
            uc.emu_stop()
        hook = b.uc.hook_add(UC_HOOK_CODE,stop_at_irq,begin=0x18,end=0x18)
        result = b.run(30000000,120,until=0x17e8fc)
        b.uc.hook_del(hook)
        self.assertEqual(result['stop']['reason'],'IRQ snapshot checkpoint')
        self.assertEqual(b.uc.reg_read(UC_ARM_REG_CPSR)&0x1f,0x12)
        for label in ('irq','boot'):
            save_snapshot(b,self.path)
            restored = load_snapshot(self.path)
            for board in (b,restored):
                if label == 'irq':
                    step = board.run(30000000,120,entry=entry(board),until=0x17e8fc,report=False)
                else:
                    step = board.run(300000,10,entry=entry(board),report=False)
                self.assertEqual(step['stop']['reason'],'requested end address' if label == 'irq' else 'instruction budget',label)
            self.assert_guest_equal(b,restored)
            del restored
            gc.collect()
