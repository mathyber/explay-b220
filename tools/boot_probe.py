"""Strict, offline ARM BE execution probe. This is NOT a phone emulator.

No USB/device access, no flash writes. Unknown accesses stop execution;
--stub-zero-mmio explicitly enables zero-backed register pages for exploration.
Unicorn has no ARM7TDMI model: ARM926 is an approximation, recorded in output.
"""
import argparse
import collections
import ctypes
import json
from pathlib import Path
from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM, CS_MODE_THUMB, CS_MODE_BIG_ENDIAN
from unicorn import Uc, UcError, UC_ARCH_ARM, UC_MODE_ARM, UC_MODE_BIG_ENDIAN
from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_INVALID, UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE, UC_HOOK_INTR
from unicorn import UC_MEM_READ_UNMAPPED, UC_MEM_WRITE_UNMAPPED, UC_PROT_READ, UC_PROT_EXEC, UC_PROT_ALL
from unicorn.arm_const import UC_CPU_ARM_926, UC_ARM_REG_PC, UC_ARM_REG_CPSR, UC_ARM_REG_R0, UC_ARM_REG_SP

ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--fdl', action='store_true')
    p.add_argument('--instructions', type=int, default=500000)
    p.add_argument('--stub-zero-mmio', action='store_true')
    p.add_argument('--out', type=Path, default=ROOT/'analysis/boot_probe.json')
    a = p.parse_args()
    if not 1 <= a.instructions <= 10000000:
        p.error('instructions must be in 1..10000000')
    uc = Uc(UC_ARCH_ARM,UC_MODE_ARM|UC_MODE_BIG_ENDIAN)
    uc.ctl_set_cpu_model(UC_CPU_ARM_926)
    image = next((ROOT/'analysis/extracted').glob('SC*.bin')).read_bytes()
    uc.mem_map(0,0x800000,UC_PROT_ALL)
    uc.mem_write(0,image)
    uc.mem_protect(0,0x800000,UC_PROT_READ|UC_PROT_EXEC)
    # 4 MiB PSRAM is a working hypothesis from the release filename 64X32.
    ram = ctypes.create_string_buffer(0x400000)
    # Working alias hypothesis: low mapping used by PS pointers; high by FDL.
    uc.mem_map_ptr(0x34000000,0x400000,UC_PROT_ALL,ctypes.addressof(ram))
    uc.mem_map_ptr(0x04000000,0x400000,UC_PROT_ALL,ctypes.addressof(ram))
    # Internal SRAM: confirmed startup copy destination and stack addresses.
    # 64 KiB is a probe allocation, not a measured physical SRAM capacity.
    uc.mem_map(0x40000000,0x10000,UC_PROT_ALL)
    entry = 0
    if a.fdl:
        uc.mem_write(0x34000000,(ROOT/'analysis/extracted/nor_fdl.bin').read_bytes())
        entry = 0x34000000
    # Unicorn uses CPSR.E for data endianness even with this older CPU model.
    # Keep E=1; writing 0xd3 alone would silently turn literal loads into LE.
    uc.reg_write(UC_ARM_REG_CPSR,0x2d3)
    tail = collections.deque(maxlen=48)
    result = dict(image='FDL' if a.fdl else 'PS', engine='Unicorn ARM926 (approximation, not ARM7TDMI)',
                  endian='big / BE32',entry=hex(entry),instruction_limit=a.instructions,
                  zero_mmio_stubs=a.stub_zero_mmio,mmio_events=[],stubbed_pages=[],
                  endian_adapter='hold Unicorn CPSR.E=1 to approximate fixed-endian ARM7',
                  endian_repairs=[],executed=0,status='instruction limit',fault=None)
    counts = collections.Counter()
    def code(engine,address,size,user):
        cpsr = engine.reg_read(UC_ARM_REG_CPSR)
        if not cpsr&0x200:
            result['endian_repairs'].append(hex(address))
            engine.reg_write(UC_ARM_REG_CPSR,cpsr|0x200)
        thumb = bool(engine.reg_read(UC_ARM_REG_CPSR)&0x20)
        tail.append((address,size,thumb))
        result['executed'] += 1
        counts[address] += 1
        if counts[address] > 100000:
            result['status'] = 'repeated PC limit (inspect trace: may be an initialization loop)'
            engine.emu_stop()
    def invalid(engine,access,address,size,value,user):
        pc = engine.reg_read(UC_ARM_REG_PC)
        peripheral = 0x80000000 <= address < 0x90000000 or 0x20000000 <= address < 0x20010000
        if a.stub_zero_mmio and access in (UC_MEM_READ_UNMAPPED,UC_MEM_WRITE_UNMAPPED) and peripheral:
            page = address&~0xfff
            engine.mem_map(page,0x1000,UC_PROT_READ|2)
            result['stubbed_pages'].append(hex(page))
            return True
        result['fault'] = dict(access=access,address=hex(address),size=size,value=hex(value),pc=hex(pc))
        result['status'] = 'unmodeled or protected memory access'
        return False
    def memory(engine,access,address,size,value,user):
        if len(result['mmio_events']) < 1000:
            result['mmio_events'].append(dict(access=access,address=hex(address),size=size,value=hex(value),pc=hex(engine.reg_read(UC_ARM_REG_PC))))
    def intr(engine,number,user):
        result.update(status='CPU interrupt / exception',interrupt=number)
        engine.emu_stop()
    uc.hook_add(UC_HOOK_CODE,code)
    uc.hook_add(UC_HOOK_MEM_INVALID,invalid)
    uc.hook_add(UC_HOOK_MEM_READ|UC_HOOK_MEM_WRITE,memory,begin=0x80000000,end=0x8fffffff)
    uc.hook_add(UC_HOOK_MEM_READ|UC_HOOK_MEM_WRITE,memory,begin=0x20000000,end=0x2000ffff)
    uc.hook_add(UC_HOOK_INTR,intr)
    try:
        uc.emu_start(entry,0xffffffff,count=a.instructions,timeout=10000000)
    except UcError as e:
        result['engine_error'] = str(e)
    modes = {False:Cs(CS_ARCH_ARM,CS_MODE_ARM|CS_MODE_BIG_ENDIAN),True:Cs(CS_ARCH_ARM,CS_MODE_THUMB|CS_MODE_BIG_ENDIAN)}
    result['trace_tail'] = []
    for addr,size,thumb in tail:
        raw = bytes(uc.mem_read(addr,size))
        ins = next(modes[thumb].disasm(raw,addr),None)
        result['trace_tail'].append(dict(pc=hex(addr),thumb=thumb,bytes=raw.hex(),
                                         asm=f'{ins.mnemonic} {ins.op_str}' if ins else '?'))
    result['registers'] = dict(pc=hex(uc.reg_read(UC_ARM_REG_PC)),sp=hex(uc.reg_read(UC_ARM_REG_SP)),
                               r0=hex(uc.reg_read(UC_ARM_REG_R0)),cpsr=hex(uc.reg_read(UC_ARM_REG_CPSR)))
    result['hot_pcs'] = [(hex(pc),n) for pc,n in counts.most_common(10)]
    a.out.parent.mkdir(parents=True,exist_ok=True)
    a.out.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in ('trace_tail','mmio_events')},indent=2))


if __name__ == '__main__':
    main()
