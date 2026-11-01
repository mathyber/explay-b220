"""Read-only PAC extraction and reproducible binary inventory. Python 3 stdlib only.

Layout reference: ilyakurdyukov/spreadtrum_flash/unpac/unpac.c.
The BP_R1.0.0 archive in this project has zero magic/CRC fields; these are
reported as absent, never reported as successfully verified.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
from pathlib import Path
import re
import struct

ROOT = Path(__file__).resolve().parents[1]


def u32(data, off):
    return struct.unpack_from('<I', data, off)[0]


def utf16z(data):
    return data.decode('utf-16le', errors='replace').split('\0', 1)[0]


def crc16(data):
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0xa001 if crc & 1 else 0)
    return crc


def entropy(data):
    counts = collections.Counter(data)
    n = len(data)
    return -sum((c / n) * math.log2(c / n) for c in counts.values()) if n else 0


def extract_pac(path, out):
    data = path.read_bytes()
    if len(data) < 0x84c:
        raise ValueError('Truncated PAC header')
    version = utf16z(data[:48])
    size, count, directory = u32(data, 48), u32(data, 1076), u32(data, 1080)
    if size != len(data) or count > 1024 or directory != 0x84c:
        raise ValueError('Unexpected PAC size/count/directory')
    magic = u32(data, 2116)
    header_crc, data_crc = struct.unpack_from('<HH', data, 2120)
    if magic not in (0, 0xfffafffa):
        raise ValueError(f'Unknown PAC magic: {magic:#x}')
    if magic == 0 and version != 'BP_R1.0.0':
        raise ValueError('Only the observed old zero-magic variant is supported')
    result = dict(source=str(path.relative_to(ROOT)), size=size,
                  sha256=hashlib.sha256(data).hexdigest(), version=version,
                  product=utf16z(data[52:564]), build=utf16z(data[564:1076]),
                  magic=hex(magic), file_count=count, directory_offset=directory,
                  checksums={'status': 'absent in this old archive' if magic == 0 else 'present',
                             'header_stored': header_crc, 'data_stored': data_crc}, files=[])
    if magic:
        result['checksums'].update(header_computed=crc16(data[:2120]), data_computed=crc16(data[directory:]))
        if header_crc != result['checksums']['header_computed'] or data_crc != result['checksums']['data_computed']:
            raise ValueError('PAC CRC mismatch')
    out.mkdir(parents=True, exist_ok=True)
    ranges = []
    for i in range(count):
        pos = directory + i * 0xa14
        if pos + 0xa14 > len(data) or u32(data, pos) != 0xa14:
            raise ValueError('Unexpected directory entry size')
        ident, name = utf16z(data[pos+4:pos+516]), utf16z(data[pos+516:pos+1028])
        length = (u32(data, pos+0x5fc) << 32) | u32(data, pos+0x604)
        offset = (u32(data, pos+0x600) << 32) | u32(data, pos+0x610)
        item = dict(index=i, id=ident, name=name, size=length, offset=offset,
                    type=u32(data, pos+0x608), flash_use=u32(data, pos+0x60c),
                    address_count=u32(data, pos+0x618),
                    addresses=[u32(data, pos+0x61c+j*4) for j in range(min(5, u32(data,pos+0x618)))])
        if length:
            if not name or re.search(r'[\\/:]', name) or name in ('.', '..'):
                raise ValueError('Unsafe or missing filename')
            if offset < directory + count*0xa14 or offset+length > len(data):
                raise ValueError('File outside payload bounds')
            if any(offset < end and offset+length > start for start,end in ranges):
                raise ValueError('Overlapping payload entries')
            ranges.append((offset, offset+length))
            payload = data[offset:offset+length]
            (out/name).write_bytes(payload)
            item['sha256'] = hashlib.sha256(payload).hexdigest()
            item['entropy'] = entropy(payload)
        result['files'].append(item)
    return result


def inventory(path, out):
    data = path.read_bytes()
    strings = [(m.start(), 'ascii', m.group().decode('ascii'))
               for m in re.finditer(rb'[\x20-\x7e]{5,}', data)]
    # This firmware stores wide strings BE. A shifted LE scan would generate
    # duplicate, truncated false strings (the same bytes, one byte later).
    for m in re.finditer(rb'(?:\x00[\x20-\x7e]){5,}', data):
        strings.append((m.start(), 'utf16be', m.group().decode('utf-16be')))
    strings.sort()
    (out/(path.name+'.strings.tsv')).write_text('offset\tencoding\ttext\n'+''.join(
        f'0x{o:08x}\t{enc}\t{s}\n' for o,enc,s in strings), encoding='utf-8')
    patterns = {
        'stone': b'TRAPGAMI', 'DRPS': b'DRPS', 'RRPS': b'RRPS', 'COLB': b'COLB',
        'CAPN': b'CAPN', 'ELF': b'\x7fELF', 'PNG': b'\x89PNG\r\n\x1a\n',
        'JPEG': b'\xff\xd8\xff', 'GIF87': b'GIF87a', 'GIF89': b'GIF89a',
        'ZIP': b'PK\x03\x04', 'RIFF': b'RIFF', 'MIDI': b'MThd',
        'secureflag': b'SPRD-SECUREFLAG', 'ThreadX': b'ThreadX', 'Nucleus': b'Nucleus',
    }
    found = {}
    for name, sig in patterns.items():
        offsets, start = [], 0
        while (pos := data.find(sig, start)) >= 0:
            offsets.append(pos)
            start = pos+1
        found[name] = offsets
    keywords = re.compile(r'6600|6610|653[01]|ARM|ThreadX|Nucleus|RTOS|SCI_|S915|B220|M224|2012|java|j2me|midp|cldc|jblend|jbed|mre|mythroad|\.mrp|\.vxp|lcd|ili9|st77|hx83|gc9|flash|samsung|spansion|winbond|nanya|psram', re.I)
    hits = [dict(offset=o, encoding=e, text=s) for o,e,s in strings if keywords.search(s)]
    (out/(path.name+'.keywords.tsv')).write_text('offset\tencoding\ttext\n'+''.join(
        f"0x{v['offset']:08x}\t{v['encoding']}\t{v['text']}\n" for v in hits), encoding='utf-8')
    return dict(size=len(data), sha256=hashlib.sha256(data).hexdigest(),
                first_256_hex=data[:256].hex(' '), string_count=len(strings),
                signatures=found, keyword_hits=hits,
                entropy_blocks=[dict(offset=o,size=len(data[o:o+65536]),entropy=round(entropy(data[o:o+65536]),5))
                                for o in range(0,len(data),65536)])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pac', nargs='?', type=Path)
    parser.add_argument('--out', type=Path, default=ROOT/'analysis')
    args = parser.parse_args()
    pac = args.pac or next(ROOT.glob('*/Firmware/*.pac'))
    pac = pac.resolve()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    result = extract_pac(pac, out/'extracted')
    (out/'pac_manifest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    bins = {}
    for file in sorted((out/'extracted').glob('*.bin')):
        bins[file.name] = inventory(file,out)
    (out/'binary_inventory.json').write_text(json.dumps(bins,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k != 'files'},ensure_ascii=True,indent=2))
    for item in result['files']:
        print(f"{item['id']:12} {item['size']:9} @ {item['offset']:#010x} {item['name']}")
    for name, meta in bins.items():
        print(name, 'signatures:', {k:[hex(o) for o in v[:12]] for k,v in meta['signatures'].items() if v})


if __name__ == '__main__':
    main()
