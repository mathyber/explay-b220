"""Recover this PS image's observed copy/zero tables and focused code listing.

Known offsets are pinned to this release's SHA256. No generic firmware detection.
"""
import hashlib
import json
from pathlib import Path
import struct
from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM, CS_MODE_THUMB, CS_MODE_BIG_ENDIAN

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = '7bd1dd7b3ce3a3efa65f957eb24e4e4b3114775c8b7770121f98b39816bacbeb'


def main():
    path = next((ROOT/'analysis/extracted').glob('SC*.bin'))
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != EXPECTED:
        raise SystemExit('This script only supports the analyzed Explay B220 release')
    table = 0x179e34
    bounds = [table+n for n in struct.unpack_from('>4I',data,table)]
    copies, zeros = [], []
    for off in range(bounds[0],bounds[1],12):
        src,dst,size = struct.unpack_from('>III',data,off)
        if size:
            if src&1 or dst&3 or src+size > len(data):
                raise ValueError('Unexpected relative-address flags or invalid source')
            copies.append(dict(table_offset=hex(off),source=hex(src),destination=hex(dst),size=hex(size),end=hex(dst+size)))
    for off in range(bounds[2],bounds[3],8):
        dst,size = struct.unpack_from('>II',data,off)
        if size:
            if dst&3:
                raise ValueError('Unexpected relative-address flags')
            zeros.append(dict(table_offset=hex(off),destination=hex(dst),size=hex(size),end=hex(dst+size)))
    result = dict(source=path.name,sha256=EXPECTED,table_header=hex(table),
                  bounds=[hex(n) for n in bounds],copy_regions=copies,zero_regions=zeros,
                  reset=hex(struct.unpack_from('>I',data,0x20)[0]),
                  exceptions=[hex(n) for n in struct.unpack_from('>8I',data,0x20)])
    (ROOT/'analysis/startup_map.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    regions = [('exception vector instructions',0,0x20,False),
               ('reset entry: first stage',0x6a4228,0x90,False),
               ('reset entry: runtime handoff',0x6a4378,0x68,False),
               ('scatter copy/zero startup code',0x179d68,0xcc,False),
               ('browser JAD handler, inferred from its debug string',0x59d888,0x4e,True),
               ('browser JAR handler, inferred from its debug string',0x59d96c,0x2c,True)]
    lines = ['ARM/Thumb BIG ENDIAN. Code and data are interleaved; disassemble only known code.',
             'PS raw image linked at address 0. Addresses below are also PS file offsets.',
             'Names except exception vectors are inferred, not recovered symbols.\n']
    for name,start,size,thumb in regions:
        lines.append(f'[{name}]')
        md = Cs(CS_ARCH_ARM,(CS_MODE_THUMB if thumb else CS_MODE_ARM)|CS_MODE_BIG_ENDIAN)
        lines.extend(f'{i.address:08x}: {i.bytes.hex():12} {i.mnemonic:8} {i.op_str}'
                     for i in md.disasm(data[start:start+size],start))
        lines.append('')
    (ROOT/'analysis/entrypoints.txt').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps(result,indent=2))


if __name__ == '__main__':
    main()
