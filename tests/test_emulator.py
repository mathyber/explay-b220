"""Offline checks of bus semantics and a real unmodified-firmware boot checkpoint."""
import gc
import hashlib
import unittest

from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_CPSR
from emulator.board import Board, ROOT
from emulator.nor import NorFlash
from emulator.lcd import Lcd9225
from emulator.dsp import DspBootstrap
from emulator.irq import InterruptController
from emulator.timer import CountdownTimer
from emulator.spi import EmptySpi
from emulator.rtc import Rtc


class PeripheralTests(unittest.TestCase):
    def test_rtc_shadow_update_irq_ack_and_midnight(self):
        rtc = Rtc()
        rtc.write(0x30,0xff00,0)
        for offset,value in ((0x10,59),(0x14,59),(0x18,23),(0x1c,1)):
            rtc.write(offset,value,0)
        self.assertTrue(rtc.pending)
        self.assertEqual(rtc.read(0x34,0),0xf00)
        self.assertEqual(rtc.fields(128000),(0,0,0,2))
        rtc.write(0x38,0xf00,128000)
        self.assertFalse(rtc.pending)
        rtc.write(0x20,20,128000)
        self.assertEqual(rtc.raw,0x1000)
        rtc.write(0x30,0,128000)
        self.assertFalse(rtc.pending)
    def test_mailbox_stages_copies_commits_and_does_not_repeat_irq(self):
        dsp = DspBootstrap()
        dsp.write(0,1,lambda n:b'')
        self.assertEqual(dsp.read(2),1)
        dsp.write(6,2,lambda n:b'')
        payload = bytearray.fromhex('1234abcd')
        dsp.write(0,3,lambda n:payload[:n])
        self.assertEqual(dsp.read(2),3)
        payload[:] = bytes(4)  # staged data must not alias the transfer buffer
        dsp.write(0,1,lambda n:b'')
        self.assertTrue(dsp.write(0,5,lambda n:b''))
        self.assertEqual(dsp.image,bytes.fromhex('1234abcd'))
        self.assertEqual(dsp.read(2),5)
        self.assertFalse(dsp.write(0,5,lambda n:b''))
        dsp.write(0,1,lambda n:b'')
        dsp.write(4,2,lambda n:b'')
        dsp.write(6,1,lambda n:b'')
        dsp.write(0,3,lambda n:bytes.fromhex('5678'))
        dsp.write(0,1,lambda n:b'')
        dsp.write(0,9,lambda n:b'')
        self.assertTrue(dsp.write(0,13,lambda n:b''))
        self.assertEqual(dsp.image,bytes.fromhex('1234abcd5678'))
        self.assertTrue(dsp.transfers[-1]['last_flag'])

    def test_mailbox_rejects_unimplemented_sequences(self):
        with self.assertRaises(ValueError):
            DspBootstrap().write(0,5,lambda n:b'')
        with self.assertRaises(ValueError):
            DspBootstrap().write(0,3,lambda n:b'')

    def test_irq_masks_disable_readback_and_pending_level(self):
        irq = InterruptController()
        irq.set_line(13,True)
        self.assertEqual(irq.pending,0)
        irq.write(8,0x2010)
        self.assertEqual(irq.pending,0x2000)
        self.assertEqual(irq.read(12),0)
        irq.write(12,0x10)
        self.assertEqual(irq.enabled,0x2000)
        irq.set_line(13,False)
        self.assertEqual(irq.pending,0)

    def test_countdown_enable_expiry_clear_and_rearm(self):
        t = CountdownTimer(4)
        t.write(0,32768,0)
        t.write(12,1,0)
        t.write(8,0x80,0)
        self.assertEqual(t.read(4,64000),16384)
        self.assertFalse(t.pending)
        self.assertEqual(t.read(12,128000),7)
        self.assertTrue(t.pending)
        t.write(12,9,128001)
        t.tick(200000)
        self.assertFalse(t.pending)
        t.write(8,0,200000)
        t.write(8,0x80,200000)
        t.tick(328000)
        self.assertTrue(t.pending)
        self.assertEqual(t.expirations,2)

    def test_spi_no_card_fifo_ready_and_high_miso(self):
        spi = EmptySpi()
        self.assertEqual(spi.read(0x34),0xa0)
        spi.write(0,0x40)
        spi.write(0x18,0x8002)
        spi.write(0x18,0x8202)
        self.assertEqual(spi.read(0x34),0x80)
        self.assertEqual(spi.read(0),0xffffffff)
        self.assertEqual(spi.read(0),0xffffffff)
        self.assertEqual(spi.read(0x34),0xa0)
        self.assertEqual(spi.tx_sample,['0x40'])


class NorTests(unittest.TestCase):
    def setUp(self):
        self.flash = NorFlash(b'\x12\x34\x56\x78',size=0x20000)

    def test_identification_and_reset_preserve_rom(self):
        f = self.flash
        for address,value in ((0xaaa,0xaa),(0x554,0x55),(0xaaa,0x90)):
            self.assertTrue(f.write(address,2,value))
        self.assertEqual(f.read(0,4),b'\x00\x20\x88\xc0')
        self.assertEqual(f.read(1,2),b'\x20\x88')
        self.assertTrue(f.write(0,2,0xf0))
        self.assertEqual(f.read(0,4),b'\x12\x34\x56\x78')

    def test_program_bits_and_status(self):
        f = self.flash
        self.assertTrue(f.write(0x10000,2,0x40))
        # 0x00ff is data in this state, even though FF is also a command.
        self.assertTrue(f.write(0x10000,2,0xff))
        self.assertEqual(f.array[0x10000:0x10002],b'\x00\xff')
        self.assertEqual(f.read(0x10000,2),b'\x00\x80')
        f.write(0x10000,2,0x40)
        f.write(0x10000,2,0xffff)
        self.assertEqual(f.status,0x90)  # NOR cannot program zero bits back to one.
        self.assertEqual(f.array[0x10000:0x10002],b'\x00\xff')
        f.write(0,2,0x50)
        self.assertEqual(f.status,0x80)

    def test_erase_and_ps_protection(self):
        f = self.flash
        f.write(0x10000,2,0x40)
        f.write(0x10000,2,0)
        f.write(0x10000,2,0x20)
        self.assertTrue(f.write(0x10000,2,0xd0))
        self.assertEqual(f.array[0x10000:],b'\xff'*0x10000)
        original = bytes(f.array[:0x10000])
        f.write(4,2,0x20)
        self.assertFalse(f.write(4,2,0xd0))  # sector intersects the source image.
        f.write(0,2,0x40)
        self.assertFalse(f.write(0,2,0))
        self.assertEqual(bytes(f.array[:0x10000]),original)

    def test_bounds_and_unsupported_commands(self):
        for addr,size,value in ((0x20000,2,0xff),(-2,2,0xff),(1,2,0xff),(0,4,0xff),(0,2,0x1234)):
            self.assertFalse(self.flash.write(addr,size,value))
        with self.assertRaises(ValueError):
            self.flash.read(0x1ffff,2)

    def test_trace_is_bounded_without_losing_command_count(self):
        for _ in range(9000):
            self.flash.write(0,2,0xff)
        self.assertEqual(self.flash.event_count,9000)
        self.assertEqual(len(self.flash.events),4096)
        self.assertEqual(len(self.flash.recent_events),4096)
        self.assertEqual(self.flash.events[0]['index'],1)
        self.assertEqual(self.flash.recent_events[-1]['index'],9000)
        self.assertEqual(self.flash.array[:4],bytes.fromhex('12345678'))

    def test_parameter_erase_preserves_neighbor_and_completed_suspend_is_safe(self):
        f = NorFlash(b'PS',parameter_start=0x7f0000)
        f.array[0x7f0000:] = bytes(0x10000)
        f.write(0x7fe000,2,0x20)
        self.assertTrue(f.write(0x7fe000,2,0xd0))
        self.assertEqual(f.array[0x7f0000:0x7fe000],bytes(0xe000))
        self.assertEqual(f.array[0x7fe000:],b'\xff'*0x2000)
        self.assertEqual(f.changed,[(0x7fe000,0x2000)])
        self.assertTrue(f.write(0x7fe000,2,0xb0))
        self.assertEqual(f.status,0x80)
        f.write(0x7fe000,2,0xff)
        self.assertTrue(f.write(0x7fe000,2,0xd0))
        self.assertEqual(f.erased_blocks,1)
        self.assertFalse(f.write(0x7fc000,2,0xb0))
        self.assertFalse(f.write(0x7fc000,2,0xd0))


class LcdTests(unittest.TestCase):
    def test_id_byte_lane_and_pixel_window(self):
        lcd = Lcd9225()
        lcd.write(False,2,0)
        lcd.write(False,2,0)
        self.assertEqual([lcd.read(True,2),lcd.read(True,2)],[0x9200,0x2500])
        lcd.registers.update({0x36:1,0x38:1})
        lcd.write(False,2,0)
        lcd.write(False,2,0x2200)
        for pixel in (0xf800,0x07e0,0x001f,0xffff):
            lcd.write(True,2,(pixel>>8)<<8)
            lcd.write(True,2,(pixel&255)<<8)
        self.assertEqual([lcd.gram[i] for i in (0,1,176,177)],[0xf800,0x07e0,0x001f,0xffff])
        self.assertEqual((lcd.registers[0x20],lcd.registers[0x21]),(0,0))
        self.assertEqual(lcd.pixel_writes,4)

    def test_vertical_decrement_window(self):
        lcd = Lcd9225()
        lcd.index = 0x22
        lcd.registers.update({3:0x1008,0x36:1,0x38:1,0x20:1,0x21:1})
        for value in (1,2,3,4):
            lcd.write_word(value)
        self.assertEqual([lcd.gram[i] for i in (177,1,176,0)],[1,2,3,4])


class BoardTests(unittest.TestCase):
    def setUp(self):
        self.board = Board()

    def tearDown(self):
        del self.board
        gc.collect()

    def access(self,address,instruction=0xe5910000,value=0):
        b = self.board
        b.uc.mem_write(0x4000e000,instruction.to_bytes(4,'big')+bytes.fromhex('e12fff1e'))
        b.uc.ctl_remove_cache(0x4000e000,0x4000e008)
        b.uc.reg_write(UC_ARM_REG_CPSR,0x2d3)
        b.uc.reg_write(UC_ARM_REG_R1,address)
        b.uc.reg_write(UC_ARM_REG_R0,value)
        b.uc.reg_write(UC_ARM_REG_LR,0x4000fff0)
        result = b.run(100,2,entry=0x4000e000,until=0x4000fff0)
        return result,b.uc.reg_read(UC_ARM_REG_R0)

    def test_mmio_first_read_big_endian_widths_and_strict_same_page(self):
        self.board.storage[0x8b000024] = 0x11223344
        for addr,op,expected in ((0x8b000024,0xe5910000,0x11223344),
                                 (0x8b000025,0xe5d10000,0x22),
                                 (0x8b000026,0xe1d100b0,0x3344)):
            result,value = self.access(addr,op)
            self.assertIsNone(result['engine_error'])
            self.assertEqual(value,expected)
        result,_ = self.access(0x8b000028)
        self.assertEqual(result['stop']['reason'],'unknown MMIO read')

    def test_shared_ram_alias(self):
        self.board.uc.mem_write(0x04001234,b'alias')
        self.assertEqual(bytes(self.board.uc.mem_read(0x34001234,5)),b'alias')

    def test_jpeg_interrupt_mask_and_w1c_drive_irq26_through_guest_mmio(self):
        b = self.board
        b.irq.write(8,1<<26)
        b.jpeg.registers[0x1c] = 1<<9
        result,_ = self.access(0x20200024,0xe5810000,1<<9)
        self.assertEqual(result['stop']['reason'],'requested end address')
        self.assertEqual(b.irq.pending,1<<26)
        result,_ = self.access(0x20200028,0xe5810000,1<<9)
        self.assertEqual(result['stop']['reason'],'requested end address')
        self.assertEqual(b.irq.pending,0)
        self.assertEqual(b.jpeg.read(0x20,b.uc.mem_read),0)

    def test_saved_flash_preserves_nv_and_rejects_a_changed_ps(self):
        saved = bytearray(self.board.flash.array)
        saved[0x7a0010:0x7a0014] = b'test'
        resumed = Board(flash_state=saved)
        self.assertEqual(bytes(resumed.uc.mem_read(0x7a0010,4)),b'test')
        self.assertIsNone(resumed.nv_seed)
        self.assertEqual(resumed.loaded_flash['size'],0x800000)
        saved[0] ^= 1
        with self.assertRaises(ValueError):
            Board(flash_state=saved)
        with self.assertRaises(ValueError):
            Board(flash_state=saved[:-1])
        del resumed
        gc.collect()

    def test_irq_entry_and_exception_return_preserve_arm_and_thumb_state(self):
        b = self.board
        b.explore = True
        # Synthetic test-only vector and ISR; production firmware is never patched.
        b.uc.mem_write(0x18,bytes.fromhex('e51ff0044000e100'))
        b.uc.mem_write(0x4000e100,bytes.fromhex(
            'e92d0003e59f000ce3a01003e5801000e8bd0003e25ef0048b000014'))
        for thumb,code in ((False,'e1a00000e12fff1e'),(True,'46c04770')):
            b.uc.mem_write(0x4000e000,bytes.fromhex(code))
            b.uc.ctl_remove_cache(0x4000e000,0x4000e008)
            b.uc.reg_write(UC_ARM_REG_CPSR,0x2d2)
            b.uc.reg_write(UC_ARM_REG_SP,0x4000d000)
            original_cpsr = 0xa0000213|(0x20 if thumb else 0)
            b.uc.reg_write(UC_ARM_REG_CPSR,original_cpsr)
            b.uc.reg_write(UC_ARM_REG_SP,0x4000c000)
            for i in range(13):
                b.uc.reg_write(UC_ARM_REG_R0+i,0x10203040+i)
            b.uc.reg_write(UC_ARM_REG_LR,0x4000fff0|(1 if thumb else 0))
            b.irq.write(8,0x2000)
            b.irq.set_line(13,True)
            result = b.run(100,2,entry=0x4000e000|(1 if thumb else 0),until=0x4000fff0)
            self.assertIsNone(result['engine_error'])
            self.assertEqual(result['stop']['reason'],'requested end address')
            self.assertEqual(b.uc.reg_read(UC_ARM_REG_CPSR),original_cpsr)
            self.assertEqual(b.uc.reg_read(UC_ARM_REG_SP),0x4000c000)
            for i in range(13):
                self.assertEqual(b.uc.reg_read(UC_ARM_REG_R0+i),0x10203040+i)
            self.assertEqual(b.irq.pending,0)
            self.assertEqual(b.irq_entries[-1]['irq_sp'],'0x4000d000')

    def test_real_chip_identification_function(self):
        b = self.board
        b.uc.reg_write(UC_ARM_REG_SP,0x40008000)
        b.uc.reg_write(UC_ARM_REG_LR,0x4000fff0)
        result = b.run(10000,2,entry=0x483979,until=0x4000fff0)
        self.assertEqual(result['stop']['reason'],'requested end address')
        self.assertIsNone(result['engine_error'])
        self.assertEqual(bytes(b.uc.mem_read(0x04296450,4)),bytes.fromhex('00050009'))

    def test_adc_ready_and_battery_channel(self):
        self.access(0x8a003004,0xe5810000,0x20)
        self.access(0x8a003000,0xe5810000,2)
        self.assertEqual(self.access(0x8a003000)[1]&0x40,0x40)
        self.assertEqual(self.access(0x8a00300c)[1],900)
        self.access(0x8a003000,0xe5810000,0)
        self.assertEqual(self.access(0x8a003000)[1]&0x40,0)

    def test_rtc_invalid_state_recovery_latch(self):
        self.access(0x8b000020,0xe5810000,0x80004)
        self.assertEqual(self.access(0x8b000020)[1],0x20004)

    def test_unsupported_jpeg_does_not_silently_acknowledge(self):
        result,_ = self.access(0x20210800,0xe5810000,1)
        self.assertEqual(result['stop']['reason'],'unsupported JPEG operation')
        self.assertEqual(result['stop']['device'],'JPEG engine')
        self.assertEqual(result['stop']['address'],'0x20210800')

    def test_rectangular_lcd_dma_skips_source_row_gaps(self):
        b = self.board
        b.lcd.index = 0x22
        b.lcd.registers.update({0x36:1,0x37:0,0x38:1,0x39:0})
        b.uc.mem_write(0x04001000,bytes.fromhex('f80007e0deadbeef001fffff'))
        for offset,value in ((0,0x04880004),(4,8),(8,0x04001000),(12,0x08020000),(20,0x20000),(24,6)):
            self.access(0x20100600+offset,0xe5810000,value)
        result,_ = self.access(0x20100010,0xe5810000,0x10000)
        self.assertEqual(result['stop']['reason'],'requested end address')
        self.assertEqual([b.lcd.gram[i] for i in (0,1,176,177)],[0xf800,0x07e0,0x001f,0xffff])
        self.assertEqual(b.dma_transfers[-1]['source_row_stride'],8)
        self.assertEqual(b.full_lcd_frames,0)

    def test_memory_dma_copies_through_ram_alias_and_protects_flash(self):
        b = self.board
        payload = bytes(range(32))
        b.uc.mem_write(0x04001000,payload)
        for offset,value in ((0,0x0a800010),(4,32),(8,0x34001000),(12,0x04002000),
                             (20,0x40004),(24,4),(28,4)):
            self.access(0x20100600+offset,0xe5810000,value)
        result,_ = self.access(0x20100010,0xe5810000,0x10000)
        self.assertEqual(result['stop']['reason'],'requested end address')
        self.assertEqual(bytes(b.uc.mem_read(0x04002000,32)),payload)
        self.assertEqual(self.access(0x20100060)[1],0x10000)
        self.assertEqual(b.lcd.pixel_writes,0)
        self.access(0x2010060c,0xe5810000,0x1000)
        original = bytes(b.uc.mem_read(0x1000,32))
        result,_ = self.access(0x20100010,0xe5810000,0x10000)
        self.assertEqual(result['stop']['reason'],'unsupported memory DMA range')
        self.assertEqual(bytes(b.uc.mem_read(0x1000,32)),original)

    def test_browser_partial_lcd_update_on_channel_17_has_independent_completion(self):
        b = self.board
        # Captured browser update: 32x19 pixels cut from a 176-pixel-wide buffer.
        payload = b''.join((y*176+x).to_bytes(2,'big') for y in range(19) for x in range(176))
        b.uc.mem_write(0x04010000,payload)
        b.lcd.index = 0x22
        b.lcd.registers.update({0x36:31,0x37:0,0x38:18,0x39:0})
        b.storage[0x20100060] = 1<<16
        for offset,value in ((0,0x04880040),(4,1216),(8,0x04010000),(12,0x08020000),
                             (20,0x20000),(24,0x122),(28,0)):
            self.access(0x20100620+offset,0xe5810000,value)
        result,_ = self.access(0x20100010,0xe5810000,1<<17)
        self.assertEqual(result['stop']['reason'],'requested end address')
        self.assertEqual([b.lcd.gram[y*176+x] for y in range(19) for x in range(32)],
                         [y*176+x for y in range(19) for x in range(32)])
        self.assertEqual(b.lcd.pixel_writes,608)
        self.assertEqual(b.lcd.gram[32],0)
        self.assertEqual(b.dma_transfers[-1]['channel'],17)
        self.assertEqual(b.dma_transfers[-1]['source_row_stride'],352)
        self.assertEqual(self.access(0x20100060)[1],(1<<16)|(1<<17))
        self.assertEqual(self.access(0x20100010)[1],0)
        self.access(0x20100070,0xe5810000,1<<17)
        self.assertEqual(self.access(0x20100060)[1],1<<16)

    def test_lcd_frame_survives_interleaved_memory_dma_on_channel_17(self):
        b = self.board
        pixels = [((row&255)<<8)|(column&255) for row in range(220) for column in range(176)]
        b.uc.mem_write(0x04010000,b''.join(v.to_bytes(2,'big') for v in pixels))
        b.uc.mem_write(0x04030000,bytes(range(64)))
        b.lcd.index = 0x22
        for row,rows in ((0,64),(64,64),(128,64),(192,28)):
            for offset,value in ((0,0x04880160),(4,rows*352),(8,0x04010000+row*352),
                                 (12,0x08020000),(20,0x20000),(24,2)):
                b.storage[0x20100600+offset] = value
            self.access(0x20100010,0xe5810000,self.access(0x20100010)[1]|0x10000)
            if row == 64:
                before = b.lcd.pixel_writes
                for offset,value in ((0,0x0a800100),(4,64),(8,0x04030000),(12,0x04031000),
                                     (20,0x40004),(24,4),(28,4)):
                    self.access(0x20100620+offset,0xe5810000,value)
                result,_ = self.access(0x20100010,0xe5810000,self.access(0x20100010)[1]|0x20000)
                self.assertEqual(result['stop']['reason'],'requested end address')
                self.assertEqual(b.lcd.pixel_writes,before)
                self.assertEqual(bytes(b.uc.mem_read(0x04031000,64)),bytes(range(64)))
                self.assertEqual(self.access(0x20100060)[1]&0x30000,0x30000)
                self.assertEqual(self.access(0x20100070)[1],0)
                self.access(0x20100070,0xe5810000,self.access(0x20100070)[1]|0x20000)
                self.assertEqual(self.access(0x20100060)[1]&0x30000,0x10000)
                self.assertEqual(self.access(0x20100074)[1],0)
        self.assertEqual(b.lcd.gram,pixels)
        self.assertEqual(b.lcd.pixel_writes,176*220)
        self.assertEqual((b.lcd.registers[0x20],b.lcd.registers[0x21]),(0,0))
        self.assertEqual(self.access(0x20100010)[1]&0x30000,0)
        self.assertEqual([v['channel'] for v in b.dma_transfers],[16,16,17,16,16])

    def test_dma_disable_is_write_only_and_other_channels_do_not_repeat_lcd(self):
        b = self.board
        self.access(0x20100010,0xe5810000,1)
        self.assertEqual(self.access(0x20100010)[1],1)
        self.assertEqual(self.access(0x20100014)[1],0)
        self.access(0x20100014,0xe5810000,1)
        self.assertEqual(self.access(0x20100010)[1],0)
        self.assertEqual(b.dma_transfers,[])

    def test_power_gpio_input_and_irq_acknowledgement(self):
        b = self.board
        self.access(0x8a000018,0xe5810000,8)
        b.keypad.key('end',True)
        self.assertEqual(self.access(0x8a000000)[1]&8,8)
        self.assertEqual(self.access(0x8a000020)[1],8)
        self.assertTrue(b.irq.raw&0x40)
        self.assertEqual(self.access(0x8a000024)[1],0)
        self.access(0x8a000024,0xe5810000,8)
        self.assertFalse(b.irq.raw&0x40)
        self.assertEqual(self.access(0x8a000000)[1]&8,8)
        b.keypad.key('end',False)
        self.assertEqual(self.access(0x8a000000)[1]&8,0)
        self.assertTrue(b.irq.raw&0x40)

    def test_backlight_observer_survives_trace_cap_without_guest_mutation(self):
        b = self.board
        b.logs = [{}]*3000
        b.uc.reg_write(UC_ARM_REG_R0,b.backlight_log_pointer)
        b.uc.reg_write(UC_ARM_REG_R1,0)
        observed = (UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_SP,UC_ARM_REG_LR,UC_ARM_REG_CPSR)
        before = [b.uc.reg_read(r) for r in observed]
        b._guest_log(b.uc,0x48e11a,2,None)
        self.assertIs(b.lcd_backlight,False)
        self.assertEqual(b.uc.reg_read(UC_ARM_REG_R0),b.backlight_log_pointer)
        self.assertEqual(b.uc.reg_read(UC_ARM_REG_R1),0)
        self.assertEqual(len(b.logs),3000)
        self.assertEqual([b.uc.reg_read(r) for r in observed],before)
        b.uc.reg_write(UC_ARM_REG_R1,1)
        b._guest_log(b.uc,0x48e11a,2,None)
        self.assertIs(b.lcd_backlight,True)

    def test_lcd_dma_moves_bytes_and_clear_is_not_storage(self):
        b = self.board
        b.lcd.index = 0x22
        b.uc.mem_write(0x04001000,bytes.fromhex('f80007e0'))
        for offset,value in ((0,0x04880160),(4,4),(8,0x04001000),(12,0x08020000),(20,0x20000),(24,2)):
            self.access(0x20100600+offset,0xe5810000,value)
        result,_ = self.access(0x20100010,0xe5810000,0x10000)
        self.assertEqual(result['stop']['reason'],'requested end address')
        self.assertEqual(b.lcd.gram[:2],[0xf800,0x07e0])
        self.assertEqual(self.access(0x20100060)[1],0x10000)
        self.access(0x20100070,0xe5810000,0x10000)
        self.assertEqual(self.access(0x20100060)[1],0)
        b.storage[0x20100600] = 0xdead
        result,_ = self.access(0x20100010,0xe5810000,0x10000)
        self.assertEqual(result['stop']['reason'],'unsupported LCD DMA configuration')


class FirmwareBootTests(unittest.TestCase):
    def test_real_dsp_transfer_irqs_and_mmi_startup(self):
        b = Board(explore=True)
        try:
            result = b.run(30000000,60,until=0x17e8fc)
            self.assertIsNone(result['engine_error'])
            self.assertEqual(result['stop']['reason'],'requested end address')
            image = next((ROOT/'analysis/extracted').glob('SC*.bin')).read_bytes()
            self.assertEqual(b.dsp.image,image[0x3e6c9c:0x3eac9c])
            self.assertEqual(len(b.dsp.transfers),8)
            self.assertEqual(sum(v['pending'] == '0x2000' for v in b.irq_entries),8)
            self.assertTrue(any('APP_Task: begin init' in v['format'] for v in b.logs))
            self.assertTrue(any('MMI_InitilizeWinIdNameArr' in v['format'] for v in b.logs))
            self.assertTrue(any(v['pending'] == '0x80' for v in b.irq_entries))
            self.assertGreater(b.rtc.updates,0)
            self.assertTrue(b.storage[0x8b000020]&0x20000)
            self.assertTrue(any(t['scheduled_count'] > 1 for t in result['rtos_tasks']))
            self.assertEqual(bytes(b.uc.mem_read(0,len(image))),image)
        finally:
            del b
            gc.collect()

    def test_unmodified_ps_reaches_logo_and_dsp_wait(self):
        b = Board(explore=True)
        try:
            result = b.run(12000000,45,until=0x526912)
            self.assertIsNone(result['engine_error'])
            self.assertEqual(result['stop']['reason'],'requested end address')
            self.assertEqual(result['normalized_chip'],'00050009')
            self.assertEqual(result['endian_repairs'],[])
            self.assertEqual(sum(t['count'] for t in result['dma_transfers']),176*220*2)
            self.assertEqual(b.lcd.pixel_writes,176*220)
            self.assertFalse(any('time out' in v['format'] for v in result['guest_logs']))
            self.assertTrue(any(v['format'] == 'PROD_Init() Exit !' for v in result['guest_logs']))
            self.assertTrue(any('MOCOR_09A.W10.25.16.04_Debug' in v['format'] for v in result['guest_logs']))
            raw = b''.join(v.to_bytes(2,'big') for v in b.lcd.gram)
            self.assertEqual(hashlib.sha256(raw).hexdigest(),'a07f230ca92130efe570c6eca1ea0365392f3b34502c3e95e105097c7bede5c5')
            image = next((ROOT/'analysis/extracted').glob('SC*.bin')).read_bytes()
            self.assertEqual(bytes(b.uc.mem_read(0,len(image))),image)
        finally:
            del b
            gc.collect()


if __name__ == '__main__':
    unittest.main()
