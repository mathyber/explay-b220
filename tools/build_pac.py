"""Put a modified PS into a copy of the original PAC, for SPD Upgrade Tool.

python -m tools.build_pac [--firmware analysis/firmware-patches/cut/b220-cut.bin] [--out FILE] [--full]
The original PAC (BP_R1.0.0, no CRC fields) keeps its directory and every other
file; the PS payload is replaced, byte for byte, by an image of the same size.

By default the PAC writes the PS only: its flash scheme (the embedded XML, padded
to its old length) loses the NV, FLASH (erase of the running NV) and PhaseCheck
steps, and NV backup is off. The phone's NV - calibration, IMEI, settings - is
not touched. UpgradeDownload stopped the full scheme on a real B220 before
writing anything: its NV backup found no "Calibration" item in the phone
("[SW2071] Can not find nv id in device"). --full keeps the original scheme.
The result is checked by reading it back with the project's PAC reader.
"""
import argparse
import hashlib
import tempfile
from pathlib import Path

from .analyze_firmware import ROOT, extract_pac, u32, utf16z

DEFAULT_FIRMWARE = ROOT/'analysis/firmware-patches/cut/b220-cut.bin'
DEFAULT_OUT = ROOT/'analysis/firmware-patches/cut/B220_CUT.pac'
DROPPED = ('NV', 'FLASH', 'PhaseCheck')  # scheme steps removed for a PS-only PAC


def original_pac():
    return next(ROOT.glob('*/Firmware/*.pac'))


def ps_only_scheme(xml):
    """The flash scheme without the NV, FLASH and PhaseCheck steps, NV backup off,
    padded with spaces to the same length."""
    import re
    text = xml.decode('ascii')
    for ident in DROPPED:
        text, n = re.subn(rf'\s*<File>\s*<ID>{ident}</ID>.*?</File>', '', text, flags=re.S)
        if n != 1:
            raise ValueError(f'Scheme step {ident} not found once')
    text, n = re.subn(r'<NVBackup backup="1">', '<NVBackup backup="0">', text)
    if n != 1:
        raise ValueError('NV backup setting not found')
    text = re.sub(r'<NVItem name="([^"]+)" backup="1">', r'<NVItem name="\1" backup="0">', text)
    out = text.encode('ascii')
    if len(out) > len(xml):
        raise ValueError('Scheme grew')
    return out+b' '*(len(xml)-len(out))


def build(pac_path, firmware_path, out_path, full=False):
    data = bytearray(pac_path.read_bytes())
    firmware = firmware_path.read_bytes()
    count, directory = u32(data, 1076), u32(data, 1080)
    if not full:
        for i in range(count):
            pos = directory+i*0xa14
            name = utf16z(bytes(data[pos+516:pos+1028]))
            if not name.lower().endswith('.xml'):
                continue
            length, offset = u32(data, pos+0x604), u32(data, pos+0x610)
            data[offset:offset+length] = ps_only_scheme(bytes(data[offset:offset+length]))
            break
        else:
            raise ValueError('No flash scheme XML in the PAC')
    for i in range(count):
        pos = directory+i*0xa14
        if utf16z(bytes(data[pos+4:pos+516])) != 'PS':
            continue
        length = (u32(data, pos+0x5fc) << 32) | u32(data, pos+0x604)
        offset = (u32(data, pos+0x600) << 32) | u32(data, pos+0x610)
        if length != len(firmware):
            raise ValueError(f'PS is {length} bytes in the PAC, the firmware {len(firmware)}')
        data[offset:offset+length] = firmware
        break
    else:
        raise ValueError('No PS entry in the PAC')
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(data)
    with tempfile.TemporaryDirectory() as folder:  # read back: structure and payload
        result = extract_pac(out_path, Path(folder))
    ps = next(f for f in result['files'] if f['id'] == 'PS')
    if ps['sha256'] != hashlib.sha256(firmware).hexdigest():
        raise ValueError('PS in the written PAC differs from the firmware')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--firmware', type=Path, default=DEFAULT_FIRMWARE)
    parser.add_argument('--out', type=Path, default=DEFAULT_OUT)
    parser.add_argument('--full', action='store_true', help='keep the original scheme (writes NV, erases the running NV)')
    args = parser.parse_args()
    try:
        result = build(original_pac(), args.firmware, args.out, args.full)
    except (OSError, ValueError, StopIteration) as exc:
        parser.error(str(exc))
    print(f'{args.out}: {result["size"]} bytes, PS sha256 {hashlib.sha256(args.firmware.read_bytes()).hexdigest()}')
    for f in result['files']:
        print(f'  {f["id"]:10s} {f["name"] or "-":45s} {f["size"]:>9}')


if __name__ == '__main__':
    main()
