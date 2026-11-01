"""Disassemble observed ARM/Thumb BE regions; find raw BE pointer references.

Requires Capstone. Pointer matches are candidates, not a recovered call graph.
"""
import argparse
from pathlib import Path
import struct
from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM, CS_MODE_THUMB, CS_MODE_BIG_ENDIAN

ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--fdl', action='store_true')
    p.add_argument('--offset', type=lambda x:int(x,0), default=0)
    p.add_argument('--size', type=lambda x:int(x,0), default=0x100)
    p.add_argument('--thumb', action='store_true')
    p.add_argument('--refs', help='Find literal pointers to the first occurrence of this ASCII string')
    p.add_argument('--words', action='store_true')
    p.add_argument('--out', type=Path)
    a = p.parse_args()
    path = ROOT/'analysis/extracted/nor_fdl.bin' if a.fdl else next((ROOT/'analysis/extracted').glob('SC*.bin'))
    data = path.read_bytes()
    base = 0x34000000 if a.fdl else 0
    lines = [f'file={path.name}, base={base:#x}, ARM big-endian']
    if a.refs:
        target = data.find(a.refs.encode())
        if target < 0:
            raise SystemExit('String not found')
        lines.append(f'String offset={target:#x} VA={base+target:#x}')
        needle = struct.pack('>I',base+target)
        lines.extend(f'pointer candidate @{base+i:#010x} file+{i:#x}'
                     for i in range(len(data)-3) if data[i:i+4] == needle)
    if a.words:
        for off in range(a.offset,min(len(data)-3,a.offset+a.size),4):
            lines.append(f'{base+off:08x}: {struct.unpack_from(">I",data,off)[0]:08x}')
    else:
        md = Cs(CS_ARCH_ARM, (CS_MODE_THUMB if a.thumb else CS_MODE_ARM)|CS_MODE_BIG_ENDIAN)
        for i in md.disasm(data[a.offset:a.offset+a.size],base+a.offset):
            lines.append(f'{i.address:08x}: {i.bytes.hex():12} {i.mnemonic:8} {i.op_str}')
    text = '\n'.join(lines)+'\n'
    if a.out:
        a.out.write_text(text,encoding='utf-8')
    print(text)


if __name__ == '__main__':
    main()
