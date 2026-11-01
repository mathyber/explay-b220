"""Offline board reconstruction. Unknown hardware behavior stays visible in logs.

No guest instruction patching, no USB, no writeback to input files.
"""
from __future__ import annotations

import collections
import ctypes
import hashlib
import json
from pathlib import Path
import time

from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM, CS_MODE_THUMB, CS_MODE_BIG_ENDIAN
from unicorn import (Uc, UcError, UC_ARCH_ARM, UC_MODE_ARM, UC_MODE_BIG_ENDIAN,
                     UC_PROT_ALL, UC_PROT_READ, UC_PROT_WRITE,
                     UC_HOOK_BLOCK, UC_HOOK_CODE, UC_HOOK_MEM_INVALID,
                     UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE, UC_HOOK_INTR,
                     UC_MEM_READ_UNMAPPED, UC_MEM_WRITE_UNMAPPED, UC_MEM_WRITE)
from unicorn.arm_const import (UC_CPU_ARM_926, UC_ARM_REG_CPSR, UC_ARM_REG_PC,
                               UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_SPSR, UC_ARM_REG_R0,
                               UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)
from .nor import NorFlash
from .lcd import Lcd9225
from .dsp import DspBootstrap
from .irq import InterruptController
from .timer import CountdownTimer
from .spi import EmptySpi
from .rtc import Rtc
from .jpeg import JpegEngine
from .keypad import Keypad

ROOT = Path(__file__).resolve().parents[1]


class Board:
    def __init__(self, profile=None, explore=False, flash_state=None, frame_callback=None,full_nor_trace=False,
                 fast_cpu=True,stop_after_frames=0):
        self.profile = profile or json.loads((ROOT/'emulator/profiles/b220-l7a.json').read_text())
        self.explore = explore
        self.frame_callback = frame_callback
        self.mmio_windows = [(int(start,0),int(end,0)) for start,end in self.profile['mmio_windows']]
        self.uc = uc = Uc(UC_ARCH_ARM, UC_MODE_ARM | UC_MODE_BIG_ENDIAN)
        # Same checked uc_reg_read call as reg_read(), without repeated Python
        # type selection/allocation for the one known 32-bit register per block.
        self.fast_cpu = fast_cpu and hasattr(uc,'_do_reg_read')
        self._cpsr_value = ctypes.c_uint32()
        uc.ctl_set_cpu_model(UC_CPU_ARM_926)
        image_path = next((ROOT/'analysis/extracted').glob('SC*.bin'))
        image = image_path.read_bytes()
        self.window_log_pointer = image.find(b'MMK_RunWinProc, win_handle')
        self.backlight_log_pointer = image.find(b'GPIO_SetLcdBackLight: %d')
        self.lcd_backlight = None
        self.last_window_id = None
        self.image_sha256 = hashlib.sha256(image).hexdigest()
        if self.image_sha256 != self.profile['ps_sha256']:
            raise ValueError('Profile is pinned to a different PS image')
        nor_profile = self.profile.get('nor',{})
        self.flash = NorFlash(image,manufacturer=int(nor_profile.get('manufacturer','0x20'),0),
                              device=int(nor_profile.get('device','0x88c0'),0),trace_limit=None if full_nor_trace else 4096,
                              parameter_start=int(nor_profile['parameter_start'],0) if 'parameter_start' in nor_profile else None,
                              parameter_size=int(nor_profile.get('parameter_size','0x2000'),0))
        self.nv_seed = None
        self.loaded_flash = None
        if flash_state is not None:
            if len(flash_state) != len(self.flash.array) or flash_state[:len(image)] != image:
                raise ValueError('Saved NOR must be exactly 8 MiB and contain the unchanged pinned PS')
            self.flash.array[:] = flash_state
            self.loaded_flash = dict(size=len(flash_state),sha256=hashlib.sha256(flash_state).hexdigest())
        elif seed := self.profile.get('nv_seed'):
            payload = (ROOT/seed['source']).read_bytes()
            if hashlib.sha256(payload).hexdigest() != seed['sha256']:
                raise ValueError('Profile is pinned to a different release NV template')
            if len(payload) > int(seed['slot_size'],0):
                raise ValueError('Release NV is larger than the selected virtual slot')
            for raw_address in seed['addresses']:
                address = int(raw_address,0)
                if address < len(image) or address+len(payload) > len(self.flash.array):
                    raise ValueError('NV seed would overlap PS or exceed virtual flash')
                self.flash.array[address:address+len(payload)] = payload
            self.nv_seed = dict(source=seed['source'],sha256=hashlib.sha256(payload).hexdigest(),
                                size=len(payload),addresses=seed['addresses'],kind='release template, not physical-device state')
        self.flash_overlays = []
        uc.mem_map(0, 0x800000, UC_PROT_ALL)
        uc.mem_write(0, bytes(self.flash.array))
        self.ram = ctypes.create_string_buffer(0x400000)
        uc.mem_map_ptr(0x04000000, 0x400000, UC_PROT_ALL, ctypes.addressof(self.ram))
        uc.mem_map_ptr(0x34000000, 0x400000, UC_PROT_ALL, ctypes.addressof(self.ram))
        uc.mem_map(0x40000000, 0x10000, UC_PROT_ALL)
        for region in self.profile.get('shared_memory',[]):
            uc.mem_map(int(region['address'],0),int(region['size'],0),UC_PROT_READ|UC_PROT_WRITE)
        # This page only models the observed identification register; no Boot ROM.
        uc.mem_map(0xfffff000, 0x1000, UC_PROT_READ)
        self.chip_id = int(self.profile['chip_id'], 0)
        uc.mem_write(0xfffffffc, self.chip_id.to_bytes(4, 'big'))
        self.storage = {int(addr, 0): int(value, 0) for addr,value in self.profile.get('registers', {}).items()}
        self.known_registers = set(self.storage)
        self.irq = InterruptController() if self.profile.get('models',{}).get('interrupt_controller') else None
        self.irq_entries = []
        self.keypad = Keypad() if self.profile.get('models',{}).get('keypad') else None
        self.key_callbacks = []
        self.pending_frame_stop = None
        if self.keypad:
            self.known_registers.update(range(self.keypad.base,self.keypad.base+0x30,4))
            self.known_registers.update((0x8a000000,0x8a000018,0x8a000020,0x8a000024))
        self.jpeg = JpegEngine() if self.profile.get('models',{}).get('jpeg') else None
        if self.jpeg:
            for start,end in self.jpeg.register_ranges:
                self.known_registers.update(range(self.jpeg.base+start,self.jpeg.base+end,4))
        if self.irq:
            self.known_registers.update(range(0x80000000,0x80000010,4))
        self.dsp = DspBootstrap() if self.profile.get('models',{}).get('dsp_bootstrap') else None
        if self.dsp and self.irq:
            self.known_registers.add(0x8b000014)
        self.spi = EmptySpi() if self.profile.get('models',{}).get('spi_no_card') else None
        self.spi_controllers = {0x8e000000:self.spi} if self.spi else {}
        self.rtc = Rtc() if self.profile.get('models',{}).get('rtc') else None
        if self.rtc:
            self.known_registers.update(range(0x89000000,0x8900003c,4))
        if self.spi:
            self.spi_controllers[0x8e003000] = EmptySpi('SIM-switch SPI transport; no external switch response modeled')
        if self.spi:
            for spi_base in self.spi_controllers:
                self.known_registers.update(range(spi_base,spi_base+0x38,4))
        self.countdown_timers = {}
        countdown = self.profile.get('models',{}).get('countdown_timers')
        if countdown:
            for base,line in ((0x81000000,4),(0x81000020,5)):
                self.countdown_timers[base] = CountdownTimer(line,countdown['blocks_per_ms'],countdown['frequency'])
                self.known_registers.update(range(base,base+16,4))
        dma = self.profile.get('models',{}).get('dma_gate')
        self.dma_gate = {k:int(dma[k],0) for k in ('control','request_mask','ack_mask')} if dma else None
        if self.dma_gate:
            self.known_registers.add(self.dma_gate['control'])
        timer = self.profile.get('models',{}).get('system_counter')
        self.timer = {k:int(timer[k],0) for k in ('counter','control','enable_mask')} if timer else None
        if self.timer:
            self.timer['blocks_per_tick'] = timer['blocks_per_tick']
            self.known_registers.update((self.timer['control'],self.timer['counter']))
        self.block_serial = 0
        self.next_timer_block = 1<<63
        self.timer_epoch = 0
        panel = self.profile.get('models',{}).get('lcd')
        self.lcd = Lcd9225(int(panel['device_id'],0)) if panel else None
        self.lcd_ports = {int(panel['command'],0):False,int(panel['data'],0):True} if panel else {}
        self.known_registers.update(self.lcd_ports)
        self.lcd_dma = self.profile.get('models',{}).get('lcd_dma')
        self.dma_enabled = 0
        self.dma_transfers = []
        self.full_lcd_frames = 0
        self._lcd_frame_pixels = 0
        self.stop_after_frames = stop_after_frames
        if self.lcd_dma:
            self.known_registers.update(range(0x20100600,0x20100640,4))
            self.known_registers.update((0x20100010,0x20100014,0x20100060,0x20100070,0x20100074))
        adc = self.profile.get('models',{}).get('adc')
        self.adc = {k:int(adc[k],0) for k in ('control','selection','result','start_mask','ready_mask')} if adc else None
        if self.adc:
            self.adc['samples'] = {int(k):v for k,v in adc['samples'].items()}
            self.known_registers.update(self.adc[k] for k in ('control','selection','result'))
        self.pages = set()
        self.mmio_counts = collections.Counter()
        self.mmio_pc_counts = collections.Counter()
        self.mmio_samples = {}
        self.mmio_tail = collections.deque(maxlen=160)
        self.blocks = collections.Counter()
        self.block_tail = collections.deque(maxlen=48)
        self.logs = []
        self.milestones = []
        self.endian_repairs = []
        self.stop = None
        self.started_at = None
        self.deadline = None
        self._running = False
        self.md = {False: Cs(CS_ARCH_ARM, CS_MODE_ARM | CS_MODE_BIG_ENDIAN),
                   True: Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_BIG_ENDIAN)}
        self.uc.reg_write(UC_ARM_REG_CPSR, 0x2d3)
        uc.hook_add(UC_HOOK_BLOCK, self._block)
        uc.hook_add(UC_HOOK_MEM_INVALID, self._invalid)
        uc.hook_add(UC_HOOK_MEM_WRITE,self._nor_write,begin=0,end=0x7fffff)
        uc.hook_add(UC_HOOK_MEM_READ,self._nor_read,begin=0,end=0x7fffff)
        if self.dsp:
            uc.hook_add(UC_HOOK_MEM_READ|UC_HOOK_MEM_WRITE,self._dsp_mailbox,begin=0x10000000,end=0x10000007)
        for start,end in self.mmio_windows:
            uc.hook_add(UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE,self._mmio,begin=start,end=end-1)
        uc.hook_add(UC_HOOK_INTR, self._interrupt)
        if self.keypad:
            uc.hook_add(UC_HOOK_CODE,self._key_callback,begin=0x1a1236,end=0x1a1236)
        # Observe the firmware's trace function; preserve every guest instruction.
        for addr in (0x48e11a,):
            uc.hook_add(UC_HOOK_CODE, self._guest_log, begin=addr, end=addr)
        uc.hook_add(UC_HOOK_CODE,self._assertion,begin=0x4822ac,end=0x4822ac)
        uc.hook_add(UC_HOOK_CODE,self._power_loop,begin=0x483b68,end=0x483b68)
        for addr in (0x483978, 0x483a02, 0x483a04, 0x394320):
            uc.hook_add(UC_HOOK_CODE, self._milestone, begin=addr, end=addr)

    def is_mmio(self,address):
        return any(start <= address < end for start,end in self.mmio_windows)

    def _block(self, uc, address, size, _):
        if self.pending_frame_stop:
            self.stop = self.pending_frame_stop
            self.stop['pc'] = hex(address)
            self.pending_frame_stop = None
            uc.emu_stop()
            return
        if self.flash_overlays:
            self._restore_flash_overlays()
        if self.fast_cpu:
            status = uc._do_reg_read(UC_ARM_REG_CPSR,ctypes.byref(self._cpsr_value))
            if status:
                raise UcError(status)
            cpsr = self._cpsr_value.value
        else:
            cpsr = uc.reg_read(UC_ARM_REG_CPSR)
        if not cpsr & 0x200:
            uc.reg_write(UC_ARM_REG_CPSR, cpsr | 0x200)
            if len(self.endian_repairs) < 100:
                self.endian_repairs.append(hex(address))
        self.blocks[address] += 1
        self.block_serial += 1
        self.block_tail.append((address, size, bool(cpsr & 0x20)))
        if self.keypad and self.irq:
            # Keep the acknowledged slot stable until the guest ISR has read
            # its matrix coordinate; latch the next edge after IRQ return.
            if cpsr&0x1f != 0x12:
                self.keypad.pump(self.block_serial,self.storage.get(0x8a000018,0))
            if bool(self.irq.raw&0x100) != self.keypad.pending:
                self.irq.set_line(8,self.keypad.pending)
            power_pending = bool(self.keypad.power_raw&self.storage.get(0x8a000018,0))
            if bool(self.irq.raw&0x40) != power_pending:
                self.irq.set_line(6,power_pending)
        if self.irq and self.fast_cpu:
            if self.block_serial >= self.next_timer_block:
                self._sync_timer_events()
        elif self.irq:
            if self.rtc and bool(self.irq.raw&0x80) != self.rtc.pending:
                self.irq.set_line(7,self.rtc.pending)
            for device in self.countdown_timers.values():
                device.tick(self.block_serial)
                if bool(self.irq.raw&(1<<device.line)) != device.pending:
                    self.irq.set_line(device.line,device.pending)
        if self.deadline and time.monotonic() >= self.deadline:
            self.stop = dict(reason='wall time budget', pc=hex(address))
            uc.emu_stop()
            return
        if self.irq and self.irq.pending and not cpsr&0x80:
            # IRQ entry: bank SP/LR, save old CPSR, clear T, set I, preserve F/E.
            uc.reg_write(UC_ARM_REG_CPSR,(cpsr&~0x3f)|0x92)
            uc.reg_write(UC_ARM_REG_SPSR,cpsr)
            uc.reg_write(UC_ARM_REG_LR,address+4)
            if len(self.irq_entries) < 1000:
                self.irq_entries.append(dict(resume_pc=hex(address),cpsr=hex(cpsr),pending=hex(self.irq.pending),
                                             irq_sp=hex(uc.reg_read(UC_ARM_REG_SP))))
            uc.reg_write(UC_ARM_REG_PC,0x18)

    def _sync_timer_events(self):
        self.next_timer_block = 1<<63
        for device in self.countdown_timers.values():
            device.tick(self.block_serial)
            if self.irq and bool(self.irq.raw&(1<<device.line)) != device.pending:
                self.irq.set_line(device.line,device.pending)
            event = device.next_event
            if event is not None:
                self.next_timer_block = min(self.next_timer_block,event)

    def _dsp_mailbox(self,uc,access,address,size,value,_):
        offset = address-0x10000000
        if size != 2 or offset not in (0,2,4,6):
            self.stop = dict(reason='unsupported DSP mailbox width',pc=hex(uc.reg_read(UC_ARM_REG_PC)),address=hex(address),size=size)
            uc.emu_stop()
            return
        try:
            if access == UC_MEM_WRITE:
                complete = self.dsp.write(offset,value,lambda n:uc.mem_read(0x10000008,n))
                if complete and self.irq:
                    self.irq.set_line(13,True)
            else:
                uc.mem_write(address,self.dsp.read(offset).to_bytes(2,'big'))
        except ValueError as exc:
            self.stop = dict(reason='unsupported DSP bootstrap sequence',detail=str(exc),pc=hex(uc.reg_read(UC_ARM_REG_PC)))
            uc.emu_stop()

    def _restore_flash_overlays(self):
        for address,size in self.flash_overlays:
            self.uc.mem_write(address,bytes(self.flash.array[address:address+size]))
        self.flash_overlays.clear()

    def _nor_write(self,uc,access,address,size,value,_):
        self._restore_flash_overlays()
        accepted = self.flash.write(address,size,value)
        for start,length in self.flash.changed:
            uc.mem_write(start,bytes(self.flash.array[start:start+length]))
        self.flash.changed.clear()
        self.flash.last_event['pc'] = hex(uc.reg_read(UC_ARM_REG_PC))
        self.flash_overlays.append((address,size))
        if not accepted:
            self.stop = dict(reason='unimplemented NOR command',address=hex(address),size=size,
                             value=hex(value),pc=hex(uc.reg_read(UC_ARM_REG_PC)))
            uc.emu_stop()

    def _nor_read(self,uc,access,address,size,value,_):
        self._restore_flash_overlays()
        if self.flash.mode != 'array':
            uc.mem_write(address,bytes(self.flash.read(address,size)))
            self.flash_overlays.append((address,size))

    def _invalid(self, uc, access, address, size, value, _):
        if access in (UC_MEM_READ_UNMAPPED, UC_MEM_WRITE_UNMAPPED) and self.is_mmio(address):
            page = address & ~0xfff
            if page not in self.pages:
                uc.mem_map(page, 0x1000, UC_PROT_READ | UC_PROT_WRITE)
                self.pages.add(page)
            return True
        for device in self.profile.get('unimplemented_peripherals',[]):
            if int(device['start'],0) <= address < int(device['end'],0):
                self.stop = dict(reason='unimplemented peripheral',device=device['name'],access=access,
                                 address=hex(address),size=size,value=hex(value),pc=hex(uc.reg_read(UC_ARM_REG_PC)))
                return False
        self.stop = dict(reason='unmodeled or protected memory', access=access,
                         address=hex(address), size=size, value=hex(value),
                         pc=hex(uc.reg_read(UC_ARM_REG_PC)))
        return False

    def _mmio(self, uc, access, address, size, value, _):
        pc = uc.reg_read(UC_ARM_REG_PC)
        word = address & ~3
        write = access == UC_MEM_WRITE
        page = address & ~0xfff
        if page not in self.pages:
            uc.mem_map(page,0x1000,UC_PROT_READ|UC_PROT_WRITE)
            self.pages.add(page)
        if not self.explore and word not in self.known_registers:
            self.stop = dict(reason='unknown MMIO write' if write else 'unknown MMIO read',
                             pc=hex(pc), address=hex(address), size=size, value=hex(value))
            uc.emu_stop()
            return
        if self.keypad and address in (0x8a000000,0x8a000020,0x8a000024):
            if size != 4:
                self.stop = dict(reason='unsupported power GPIO width',pc=hex(pc),address=hex(address),size=size)
                uc.emu_stop()
                return
            if write:
                if address == 0x8a000024:
                    self.keypad.power_raw &= ~value
                else:
                    self.storage[address] = value
            else:
                if address == 0x8a000020:
                    value = self.keypad.power_raw
                elif address == 0x8a000024:
                    value = 0
                else:
                    value = (self.storage.get(address,0)&~8)|(8 if self.keypad.power_down else 0)
                uc.mem_write(address,value.to_bytes(4,'big'))
            if self.irq:
                self.irq.set_line(6,bool(self.keypad.power_raw&self.storage.get(0x8a000018,0)))
            self._record_mmio(write,address,pc,value,size,'power/end GPIO3 / IRQ6 (other GPIO inputs static)')
            return
        if self.keypad and self.keypad.base <= address < self.keypad.base+0x30:
            if size != 4 or address != word:
                self.stop = dict(reason='unsupported keypad width',pc=hex(pc),address=hex(address),size=size)
                uc.emu_stop()
                return
            try:
                if write:
                    self.keypad.write(address-self.keypad.base,value)
                    if self.irq:
                        self.irq.set_line(8,self.keypad.pending)
                else:
                    value = self.keypad.read(address-self.keypad.base)
                    uc.mem_write(address,value.to_bytes(4,'big'))
            except ValueError as exc:
                self.stop = dict(reason='unsupported keypad access',detail=str(exc),pc=hex(pc))
                uc.emu_stop()
            self._record_mmio(write,address,pc,value,size,'keypad matrix / IRQ8')
            return
        if self.jpeg and self.jpeg.base <= address < self.jpeg.end:
            if size != 4 or address != word:
                self.stop = dict(reason='unsupported JPEG access width',pc=hex(pc),address=hex(address),size=size)
                uc.emu_stop()
                return
            try:
                if write:
                    self.jpeg.write(address-self.jpeg.base,value,uc.mem_read,uc.mem_write)
                else:
                    value = self.jpeg.read(address-self.jpeg.base,uc.mem_read)
                    uc.mem_write(address,value.to_bytes(4,'big'))
            except (ValueError,NotImplementedError) as exc:
                self.stop = dict(reason='unsupported JPEG operation',device='JPEG engine',detail=str(exc),
                                 pc=hex(pc),address=hex(address),value=hex(value))
                uc.emu_stop()
            self._record_mmio(write,address,pc,value,size,'partial JPEG register model')
            return
        if self.irq and 0x80000000 <= word < 0x80000010:
            if size != 4 or address != word:
                self.stop = dict(reason='unsupported interrupt-controller width',address=hex(address),size=size,pc=hex(pc))
                uc.emu_stop()
                return
            try:
                if write:
                    self.irq.write(word-0x80000000,value)
                else:
                    value = self.irq.read(word-0x80000000)
                    uc.mem_write(address,value.to_bytes(4,'big'))
            except ValueError as exc:
                self.stop = dict(reason='unsupported interrupt-controller access',detail=str(exc),pc=hex(pc))
                uc.emu_stop()
            self._record_mmio(write,address,pc,value,size,'inferred IRQ controller')
            return
        timer_base = word&~0x1f
        if timer_base in self.countdown_timers and word-timer_base in (0,4,8,12):
            device = self.countdown_timers[timer_base]
            if size != 4 or address != word:
                self.stop = dict(reason='unsupported countdown-timer width',pc=hex(pc),address=hex(address),size=size)
                uc.emu_stop()
                return
            try:
                if write:
                    device.write(word-timer_base,value,self.block_serial)
                else:
                    value = device.read(word-timer_base,self.block_serial)
                    uc.mem_write(address,value.to_bytes(4,'big'))
                if self.fast_cpu:
                    self._sync_timer_events()
            except ValueError as exc:
                self.stop = dict(reason='unsupported countdown-timer access',detail=str(exc),pc=hex(pc))
                uc.emu_stop()
            self._record_mmio(write,address,pc,value,size,'inferred 32k countdown timer')
            return
        spi_base = word&~0xfff
        if self.rtc and 0x89000000 <= word <= 0x89000038:
            if size != 4 or word != address:
                self.stop = dict(reason='unsupported RTC width',pc=hex(pc),address=hex(address),size=size)
                uc.emu_stop()
                return
            try:
                if write:
                    self.rtc.write(word-0x89000000,value,self.block_serial)
                else:
                    value = self.rtc.read(word-0x89000000,self.block_serial)
                    uc.mem_write(address,value.to_bytes(4,'big'))
                if self.fast_cpu and self.irq and bool(self.irq.raw&0x80) != self.rtc.pending:
                    self.irq.set_line(7,self.rtc.pending)
            except ValueError as exc:
                self.stop = dict(reason='unsupported RTC access',pc=hex(pc),detail=str(exc))
                uc.emu_stop()
            self._record_mmio(write,address,pc,value,size,'RTC shadow update and IRQ7')
            return
        if spi_base in self.spi_controllers and word-spi_base < 0x38:
            spi = self.spi_controllers[spi_base]
            if size != 4 or address != word:
                self.stop = dict(reason='unsupported SPI access width',pc=hex(pc),address=hex(address),size=size)
                uc.emu_stop()
                return
            if write:
                spi.write(word-spi_base,value)
            else:
                value = spi.read(word-spi_base)
                uc.mem_write(address,value.to_bytes(4,'big'))
            self._record_mmio(write,address,pc,value,size,'SPI transport, no attached responder')
            return
        if self.lcd_dma and word in (0x20100010,0x20100014,0x20100060,0x20100070,0x20100074):
            if size != 4 or address != word:
                self.stop = dict(reason='unsupported DMA control width',pc=hex(pc),address=hex(address),size=size)
                uc.emu_stop()
                return
            if write:
                if word == 0x20100010:
                    requested = value&~self.dma_enabled
                    self.dma_enabled |= value
                    for channel in range(16,22):
                        if requested&(1<<channel):
                            if channel not in (16,17):
                                self.stop = dict(reason='unsupported DMA channel',channel=channel,pc=hex(pc))
                                uc.emu_stop()
                                break
                            self._dma_transfer(channel,pc)
                            if self.stop:
                                break
                elif word == 0x20100014:
                    self.dma_enabled &= ~value
                elif word in (0x20100070,0x20100074):
                    self.storage[0x20100060] = self.storage.get(0x20100060,0)&~value
                else:
                    self.stop = dict(reason='unsupported DMA status write',pc=hex(pc),address=hex(address))
                    uc.emu_stop()
            else:
                value = (self.dma_enabled if word == 0x20100010 else
                         self.storage.get(word,0) if word == 0x20100060 else 0)
                uc.mem_write(address,value.to_bytes(4,'big'))
            self._record_mmio(write,address,pc,value,size,'DMA enable/disable and per-channel W1C completion')
            return
        if word in self.lcd_ports:
            if write:
                accepted = self.lcd.write(self.lcd_ports[word],size,value)
                if not accepted:
                    self.stop = dict(reason='unsupported LCD bus access',pc=hex(pc),address=hex(address),size=size)
                    uc.emu_stop()
            else:
                value = self.lcd.read(self.lcd_ports[word],size)
                uc.mem_write(address,value.to_bytes(size,'big'))
            self._record_mmio(write,address,pc,value,size,'ILI9225 port')
            return
        shift = (4 - (address & 3) - size) * 8
        if shift < 0 or size not in (1, 2, 4):
            self.stop = dict(reason='unsupported MMIO access width',pc=hex(pc),address=hex(address),size=size)
            uc.emu_stop()
            return
        mask = (1 << (size * 8)) - 1
        if write:
            old = self.storage.get(word, 0)
            self.storage[word] = (old & ~(mask << shift)) | ((value & mask) << shift)
            if self.rtc and word == 0x8b000020 and value&0x80000:
                # RTC invalid-state recovery: PS 0x18e712 writes bit19, then
                # expects status bit17 to be set on the next read at 0x18e6ea.
                self.storage[word] = (self.storage[word]&~0x80000)|0x20000
            if self.irq and self.dsp and word == 0x8b000014 and value&3:
                self.irq.set_line(13,False)
            if self.timer and word == self.timer['control']:
                if not old&self.timer['enable_mask'] and self.storage[word]&self.timer['enable_mask']:
                    self.timer_epoch = self.block_serial
        else:
            register_value = self.storage.get(word,0)
            if self.adc:
                active = self.storage.get(self.adc['control'],0)&self.adc['start_mask']
                if word == self.adc['control']:
                    register_value = (register_value & ~self.adc['ready_mask']) | (self.adc['ready_mask'] if active else 0)
                elif word == self.adc['result']:
                    selection = self.storage.get(self.adc['selection'],0)
                    channel = selection.bit_length()-1
                    register_value = self.adc['samples'].get(channel,0) if active else 0
            if self.dma_gate and word == self.dma_gate['control']:
                ack = self.dma_gate['ack_mask']
                register_value = (register_value & ~ack) | (ack if register_value & self.dma_gate['request_mask'] else 0)
            if self.timer and word == self.timer['counter']:
                enabled = self.storage.get(self.timer['control'],0)&self.timer['enable_mask']
                register_value = ((self.block_serial-self.timer_epoch)//self.timer['blocks_per_tick'])&0xffffffff if enabled else 0
            value = (register_value >> shift) & mask
            uc.mem_write(address, value.to_bytes(size, 'big'))
        model = 'DMA gate handshake' if self.dma_gate and word == self.dma_gate['control'] else (
            'profile storage' if word in self.known_registers else 'unknown zero/storage')
        if self.timer and word in (self.timer['counter'],self.timer['control']):
            model = 'deterministic system counter'
        if self.adc and word in (self.adc[k] for k in ('control','selection','result')):
            model = 'instant ADC conversion, virtual input'
        self._record_mmio(write,address,pc,value,size,model)

    def _dma_complete(self,channel):
        mask = 1<<channel
        self.storage[0x20100060] = self.storage.get(0x20100060,0)|mask
        self.dma_enabled &= ~mask

    def _dma_transfer(self,channel,pc):
        """Observed software channels: LCD on 16; RAM copies on 16 or 17."""
        base = 0x20100400+channel*0x20
        config,count,source,target = (self.storage.get(base+offset,0) for offset in (0,4,8,12))
        stride,frame = (self.storage.get(base+offset,0) for offset in (0x14,0x18))
        if config>>16 == 0x0a80 and stride == 0x40004 and frame == 4 and self.storage.get(base+0x1c,0) == 4:
            def in_ram(address,length):
                return any(start <= address <= end-length for start,end in
                           ((0x04000000,0x04400000),(0x34000000,0x34400000),(0x40000000,0x40010000)))
            if (not 0 < count <= 0x400000 or count&3 or not config&0xffff or config&3 or
                    not in_ram(target,count) or not (in_ram(source,count) or 0 <= source <= 0x800000-count)):
                self.stop = dict(reason='unsupported memory DMA range',pc=hex(pc),source=hex(source),target=hex(target),count=count)
                self.uc.emu_stop()
                return
            payload = bytes(self.uc.mem_read(source,count))
            self.uc.mem_write(target,payload)
            self._dma_complete(channel)
            self.dma_transfers.append(dict(channel=channel,kind='memory copy',pc=hex(pc),source=hex(source),target=hex(target),
                count=count,sha256=hashlib.sha256(payload).hexdigest(),completed='immediately; polling only'))
            return
        row_bytes = config&0xffff
        row_stride = row_bytes+(frame&0xffff)-2
        rows = (count+row_bytes-1)//row_bytes if row_bytes else 0
        extent = (rows-1)*row_stride+(count-(rows-1)*row_bytes) if rows else 0
        if (channel != 16 or not self.lcd or config>>16 != 0x0488 or target != 0x08020000 or
            stride != 0x20000 or frame>>16 or frame < 2 or frame&1 or
            not 0 < row_bytes <= self.lcd.width*2 or row_bytes&1 or
            count&1 or not 0 < count <= self.lcd.width*self.lcd.height*2 or
            not 0x04000000 <= source <= 0x04400000-extent):
            self.stop = dict(reason='unsupported LCD DMA configuration',pc=hex(pc),
                             config=hex(config),count=count,source=hex(source),target=hex(target))
            self.uc.emu_stop()
            return
        payload = b''.join(bytes(self.uc.mem_read(source+row*row_stride,min(row_bytes,count-row*row_bytes)))
                           for row in range(rows))
        previous_pixels = self.lcd.pixel_writes
        for byte in payload:
            self.lcd.write(True,2,byte<<8)
        self._dma_complete(channel)
        self.dma_transfers.append(dict(channel=16,pc=hex(pc),source=hex(source),target=hex(target),
                                       count=count,sha256=hashlib.sha256(payload).hexdigest(),
                                       row_bytes=row_bytes,source_row_stride=row_stride,virtual_blocks=self.block_serial,
                                       elapsed_seconds=round(time.monotonic()-self.started_at,4) if self.started_at else None,
                                       completed='immediately, polling only; no IRQ delivery'))
        if self.frame_callback:
            self.frame_callback(self.lcd)
        full_window = all(self.lcd.registers.get(register) == value for register,value in
                          ((0x37,0),(0x36,self.lcd.width-1),(0x39,0),(0x38,self.lcd.height-1)))
        if full_window:
            self._lcd_frame_pixels += self.lcd.pixel_writes-previous_pixels
            complete,self._lcd_frame_pixels = divmod(self._lcd_frame_pixels,self.lcd.width*self.lcd.height)
            self.full_lcd_frames += complete
        else:
            self._lcd_frame_pixels = 0
        if self.stop_after_frames and self.full_lcd_frames >= self.stop_after_frames:
            self.pending_frame_stop = dict(reason='requested LCD frame count',pc=hex(pc),frames=self.full_lcd_frames)

    def _key_callback(self,uc,address,size,_):
        if len(self.key_callbacks) < 1000:
            self.key_callbacks.append(dict(blocks=self.block_serial,
                args=[hex(uc.reg_read(r)) for r in (UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2)]))

    def _record_mmio(self,write,address,pc,value,size,model):
        event = ('write' if write else 'read',address,pc,value)
        self.mmio_counts[event[:2]] += 1
        self.mmio_pc_counts[event[:3]] += 1
        samples = self.mmio_samples.setdefault(event[:2],[])
        if len(samples) < 8:
            samples.append((pc,value))
        self.mmio_tail.append(dict(op=event[0],address=hex(address),pc=hex(pc),value=hex(value),size=size,model=model))

    def _interrupt(self, uc, number, _):
        self.stop = dict(reason='CPU exception',number=number,pc=hex(uc.reg_read(UC_ARM_REG_PC)))
        uc.emu_stop()

    def cstring(self, address, limit=512):
        if address > 0xffffffff:
            return None
        data = bytearray()
        try:
            while len(data) < limit:
                pointer = address+len(data)
                size = min(limit-len(data),128,0x1000-(pointer&0xfff))
                chunk = bytes(self.uc.mem_read(pointer,size))
                end = chunk.find(b'\x00')
                if end >= 0:
                    data.extend(chunk[:end])
                    break
                data.extend(chunk)
        except UcError:
            return None
        if not data or sum(32 <= c <= 126 or c in (9, 10, 13) for c in data) < len(data) * .85:
            return None
        return data.decode('ascii', errors='replace')

    def _guest_log(self, uc, address, size, _):
        ptr = uc.reg_read(UC_ARM_REG_R0)
        if ptr == self.window_log_pointer:
            self.last_window_id = uc.reg_read(UC_ARM_REG_R2)
        elif ptr == self.backlight_log_pointer:
            # Observe the firmware's LCD output request, including after the
            # general trace reaches its cap. This never changes guest state.
            self.lcd_backlight = bool(uc.reg_read(UC_ARM_REG_R1))
        if len(self.logs) >= 3000:
            return
        fmt = self.cstring(ptr)
        if fmt:
            args = [uc.reg_read(r) for r in (UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)]
            self.logs.append(dict(pc=hex(address),lr=hex(uc.reg_read(UC_ARM_REG_LR)),format=fmt,
                                  args=[hex(v) for v in args],strings=[self.cstring(v,128) for v in args]))

    def _assertion(self,uc,address,size,_):
        self.stop = dict(reason='firmware assertion',pc=hex(address),lr=hex(uc.reg_read(UC_ARM_REG_LR)),
                         args=[hex(uc.reg_read(r)) for r in (UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3)],
                         strings=[self.cstring(uc.reg_read(r)) for r in (UC_ARM_REG_R0,UC_ARM_REG_R1)])
        uc.emu_stop()

    def _power_loop(self,uc,address,size,_):
        self.stop = dict(reason='firmware power/reset terminal loop',pc=hex(address),
                         lr=hex(uc.reg_read(UC_ARM_REG_LR)))
        uc.emu_stop()

    def _milestone(self, uc, address, size, _):
        if len(self.milestones) < 100:
            self.milestones.append(dict(pc=hex(address),r0=hex(uc.reg_read(UC_ARM_REG_R0)),
                                        normalized_chip=bytes(uc.mem_read(0x04296450,4)).hex()))

    def run(self, instructions=2000000, seconds=20, entry=0, until=0xffffffff,report=True):
        if self._running:
            raise ValueError('CPU execution is already active')
        if instructions <= 0 or not 0 < seconds < float('inf'):
            raise ValueError('Instruction and wall-time budgets must be positive and finite')
        self.started_at = time.monotonic()
        self.deadline = self.started_at + seconds
        self.stop = None
        error = None
        self._running = True
        try:
            self.uc.emu_start(entry, until, count=instructions)
        except UcError as exc:
            error = str(exc)
        finally:
            self._running = False
        self._restore_flash_overlays()
        if not self.stop:
            pc = self.uc.reg_read(UC_ARM_REG_PC)
            self.stop = dict(reason='CPU engine error' if error else (
                'requested end address' if pc == until else 'instruction budget'),pc=hex(pc))
        elapsed = time.monotonic() - self.started_at
        if report:
            return self.report(instructions, elapsed, error)
        return dict(stop=self.stop,elapsed_seconds=elapsed,engine_error=error)

    def report(self, budget, elapsed, error=None):
        tail = []
        for addr,size,thumb in self.block_tail:
            raw = bytes(self.uc.mem_read(addr,min(size,256)))
            tail.append(dict(address=hex(addr),size=size,thumb=thumb,
                             instructions=[f'{i.address:08x}: {i.mnemonic} {i.op_str}' for i in self.md[thumb].disasm(raw,addr)]))
        return dict(profile=self.profile, image_sha256=self.image_sha256, explore=self.explore,
                    engine='Unicorn 2 / ARM926 approximation, fixed BE adapter',
                    instruction_budget=budget, elapsed_seconds=round(elapsed,3),
                    translated_blocks=self.block_serial,
                    stop=self.stop or dict(reason='instruction budget or end address',pc=hex(self.uc.reg_read(UC_ARM_REG_PC))),
                    engine_error=error, registers=dict(pc=hex(self.uc.reg_read(UC_ARM_REG_PC)),
                    sp=hex(self.uc.reg_read(UC_ARM_REG_SP)),lr=hex(self.uc.reg_read(UC_ARM_REG_LR)),
                    cpsr=hex(self.uc.reg_read(UC_ARM_REG_CPSR))),
                    general_registers={f'r{i}':hex(self.uc.reg_read(UC_ARM_REG_R0+i)) for i in range(13)},
                    normalized_chip=bytes(self.uc.mem_read(0x04296450,4)).hex(),
                    nv_seed=self.nv_seed,
                    loaded_flash=self.loaded_flash,
                    nor_mode=self.flash.mode,nor_events=self.flash.events+list(self.flash.recent_events),
                    nor_event_count=self.flash.event_count,
                    nor_geometry=dict(parameter_start=hex(self.flash.parameter_start) if self.flash.parameter_start is not None else None,
                                      parameter_size=self.flash.parameter_size,main_block_size=0x10000,model_revision=2),
                    nor_events_truncated=self.flash.event_count>len(self.flash.events)+len(self.flash.recent_events),
                    nor_programmed_words=self.flash.programmed_words,
                    nor_erased_blocks=self.flash.erased_blocks,
                    lcd=self.lcd.report() if self.lcd else None,
                    dma_transfers=self.dma_transfers,
                    full_lcd_frames=self.full_lcd_frames,
                    keypad=self.keypad.report() if self.keypad else None,key_callbacks=self.key_callbacks,
                    lcd_backlight=self.lcd_backlight,
                    mmi_window_id=hex(self.last_window_id) if self.last_window_id is not None else None,
                    dsp_bootstrap=self.dsp.report() if self.dsp else None,
                    jpeg=self.jpeg.report() if self.jpeg else None,
                    interrupt_controller=self.irq.report() if self.irq else None,irq_entries=self.irq_entries,
                    countdown_timers={hex(base):t.report(self.block_serial) for base,t in self.countdown_timers.items()},
                    spi=self.spi.report() if self.spi else None,
                    spi_controllers={hex(base):device.report() for base,device in self.spi_controllers.items()},
                    rtc=self.rtc.report(self.block_serial) if self.rtc else None,
                    rtos_tasks=self._rtos_tasks(),
                    milestones=self.milestones,guest_logs=self.logs,endian_repairs=self.endian_repairs,
                    unknown_mmio_accesses=sum(n for (op,addr),n in self.mmio_counts.items() if addr&~3 not in self.known_registers),
                    mmio_summary=[dict(op=op,address=hex(addr),count=n,
                        profiled=addr&~3 in self.known_registers,
                        samples=[dict(pc=hex(pc),value=hex(v)) for pc,v in self.mmio_samples[(op,addr)]])
                        for (op,addr),n in self.mmio_counts.most_common()],
                    mmio_tail=list(self.mmio_tail),mapped_mmio_pages=[hex(a) for a in sorted(self.pages)],
                    hot_blocks=[dict(pc=hex(addr),count=n) for addr,n in self.blocks.most_common(20)],
                    block_tail=tail)

    def _rtos_tasks(self):
        """Read-only reconstruction of this build's THRD list (PS 0x17f158)."""
        def word(address):
            return int.from_bytes(self.uc.mem_read(address,4),'big')
        tasks,seen = [],set()
        try:
            pointer = word(0x042b51d8)
            while pointer and pointer not in seen and len(seen) < 128:
                if word(pointer) != 0x54485244:
                    break
                seen.add(pointer)
                sp,wait = word(pointer+8),word(pointer+0x70)
                context = word(sp) if sp else None
                saved_pc = word(sp+(44 if context == 0 else 64)) if context in (0,1) else None
                tasks.append(dict(address=hex(pointer),name=self.cstring(word(pointer+0x28),80),
                    state_code=word(pointer+0x30),scheduled_count=word(pointer+4),priority=word(pointer+0x2c),
                    saved_sp=hex(sp),saved_pc=hex(saved_pc) if saved_pc is not None else None,
                    wait_object=hex(wait),wait_tag=self.cstring(wait,4) if wait else None,
                    saved_stack_words=[hex(word(sp+o)) for o in range(0,min(256,max(0,word(pointer+0x10)+1-sp)),4)] if sp else []))
                pointer = word(pointer+0x90)
        except UcError:
            pass
        return tasks
