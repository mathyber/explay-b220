"""Extract and validate the original Regular/Bold fonts from a pinned K550 FS."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import urllib.request

from emulator.firmware import ROOT

DIRECTORY = ROOT/'analysis/donor-fonts/k550'
SOURCE_NAME = 'K550_R8BA024_FS_C_ASIA_RED52.fbn'
SOURCE_URL = 'https://firmware.center/firmware/Sony-Ericsson/K550/CID%2052%20RED/'+SOURCE_NAME
SOURCE_SHA256 = '2ba7c970262f184c6659b8bed3dc9275b654cff27b16a26a1a1e540e15b6d732'
FONTS = (
    ('Sans-Serif_Europe_Reg.ttf', 0x922000, 'a30e2289ea0c694121e211eaf95a4a5f97ce4479825b25393db089d2ec7fceaa'),
    ('Sans-Serif_Europe_Bd.ttf', 0x95c000, 'ceaf4e808d5fc29b1bc98f14e6428432d6f7755dab4214a3aeb8b6a4a77f0c09'),
)
RUSSIAN = tuple(range(0x410, 0x450)) + (0x401, 0x451)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def checksum(data):
    data += bytes((-len(data)) % 4)
    return sum(struct.unpack('>'+str(len(data)//4)+'I', data)) & 0xffffffff


def validate_ttf(data):
    if len(data)<12 or data[:4] != b'\x00\x01\x00\x00':
        raise ValueError('Expected a TrueType sfnt file')
    count = struct.unpack_from('>H', data, 4)[0]
    tables = {}
    for index in range(count):
        if 12+(index+1)*16 > len(data):
            raise ValueError('Truncated sfnt directory')
        tag, wanted, offset, size = struct.unpack_from('>4s3I', data, 12+index*16)
        if tag in tables or offset+size > len(data):
            raise ValueError('Invalid sfnt table bounds')
        block = data[offset:offset+size]
        tables[tag] = block
        if tag == b'head':
            block = block[:8]+bytes(4)+block[12:]
        if checksum(block) != wanted:
            raise ValueError(f'Invalid table checksum: {tag!r}')
    if checksum(data) != 0xb1b0afba:
        raise ValueError('Invalid whole-font checksum')
    cmap = tables[b'cmap']
    for index in range(struct.unpack_from('>H', cmap, 2)[0]):
        platform, encoding, offset = struct.unpack_from('>HHI', cmap, 4+index*8)
        if platform != 3 or encoding != 1:
            continue
        sub = cmap[offset:]
        if struct.unpack_from('>H', sub)[0] != 4:
            continue
        segments = struct.unpack_from('>H', sub, 6)[0]//2
        ends = struct.unpack_from(f'>{segments}H', sub, 14)
        starts = struct.unpack_from(f'>{segments}H', sub, 16+segments*2)
        deltas = struct.unpack_from(f'>{segments}h', sub, 16+segments*4)
        base = 16+segments*6
        ranges = struct.unpack_from(f'>{segments}H', sub, base)
        def glyph_id(cp):
            for k, (lo, hi) in enumerate(zip(starts, ends)):
                if lo <= cp <= hi:
                    if not ranges[k]:
                        return (cp+deltas[k]) & 0xffff
                    g = struct.unpack_from('>H', sub, base+k*2+ranges[k]+2*(cp-lo))[0]
                    return (g+deltas[k]) & 0xffff if g else 0
            return 0
        if not all(glyph_id(cp) for cp in (*range(0x20, 0x7f), *RUSSIAN)):
            raise ValueError('Latin or Russian characters missing in cmap')
        return dict(tables_checked=count, whole_font_checksum='0xB1B0AFBA',
                    latin_ascii=True, russian_letters=66, yo_and_short_i=True)
    raise ValueError('Expected a Windows Unicode format-4 cmap')


def extract(source):
    if sha(source) != SOURCE_SHA256:
        raise ValueError('Unsupported K550 FS image or incomplete download')
    cursor = 0x1154
    blocks = []
    for index in range(176):
        address, size = struct.unpack_from('<II', source, cursor)
        if address != 0x45e00000+index*0x10000 or size != 0x10000:
            raise ValueError('Unexpected BABE record')
        blocks.append(source[cursor+8:cursor+8+size])
        cursor += 8+size
    raw = b''.join(blocks)
    result = {}
    records = []
    for name, start, digest in FONTS:
        count = struct.unpack_from('>H', raw, start+4)[0]
        size = max(sum(struct.unpack_from('>II', raw, start+20+i*16)) for i in range(count))
        data = raw[start:start+size]
        if sha(data) != digest:
            raise ValueError(f'Unexpected font bytes: {name}')
        validation = validate_ttf(data)
        result[name] = data
        records.append(dict(file=name, size=size, raw_fs_offset=hex(start), sha256=digest, **validation))
    start = raw.find(b'<xml>')
    end = raw.find(b'</xml>', start)+6
    xml = raw[start:end]
    if start<0 or not xml.endswith(b'</xml>') or not all(name.encode() in xml for name,_,_ in FONTS):
        raise ValueError('Original font configuration not found')
    result['FONTS.xml'] = xml
    manifest = dict(source_url=SOURCE_URL, source_sha256=SOURCE_SHA256,
        source_version='K550 R8BA024 FS C_ASIA RED52', babe_first_record='0x1154',
        babe_blocks=176, babe_block_size=65536, babe_address_base='0x45e00000',
        method='Validated BABE records; sfnt carving by table bounds; pinned file hashes and all table checksums',
        fonts=records)
    return result, manifest


def prepare(directory=DIRECTORY, download=False):
    directory.mkdir(parents=True, exist_ok=True)
    source = directory/SOURCE_NAME
    if not source.exists():
        if not download:
            raise ValueError('K550 FS missing; run with --download')
        print(f'Downloading original K550 FS: {SOURCE_URL}', flush=True)
        with urllib.request.urlopen(SOURCE_URL, timeout=60) as response:
            data = response.read(12000000)
        # Validate before saving; no downloaded code is executed.
        files, manifest = extract(data)
        source.write_bytes(data)
    else:
        files, manifest = extract(source.read_bytes())
    for name, data in files.items():
        destination = directory/name
        if destination.resolve() == source.resolve() or (destination.exists() and destination.samefile(source)):
            raise ValueError('Output aliases the source image')
        destination.write_bytes(data)
    (directory/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=DIRECTORY)
    parser.add_argument('--download', action='store_true', help='Download pinned FS if not present')
    args = parser.parse_args()
    try:
        manifest = prepare(args.directory, args.download)
    except (OSError, ValueError, KeyError, struct.error) as exc:
        parser.error(str(exc))
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
