"""Build the first B220 native detour from the exact original PS, without host injection.

Reclaim 18 JFIF metadata bytes inside one bounded JPEG resource; put a Thumb-1
payload after its earlier EOI. Pixels, PS length, resource position/slot size,
native keypad prologue and all code outside the 4-byte detour stay unchanged.
"""
import argparse
import hashlib
from io import BytesIO
import json
from pathlib import Path

from PIL import Image
from capstone import Cs,CS_ARCH_ARM,CS_MODE_THUMB,CS_MODE_BIG_ENDIAN
from emulator.firmware import ROOT,protect_outputs

PS_SHA256 = '7bd1dd7b3ce3a3efa65f957eb24e4e4b3114775c8b7770121f98b39816bacbeb'
JPEG_START = 0x14c528
JPEG_SIZE = 27155
JPEG_SHA256 = '74770eefbc79ec6b736d20acf4f672aa2ca7b0c627000417e78eda56761e2963'
ENTRY = 0x1a1236
HOOK = 0x1a1238
RESUME = 0x1a123c
MARKER = b'B220'
DEFAULT_DIRECTORY = ROOT/'analysis/firmware-patches/key-marker'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def thumb_bl(source,target):
    displacement = target-source-4
    if source&1 or target&1 or not -(1<<22) <= displacement < (1<<22):
        raise ValueError('Thumb-1 BL destination is unaligned or out of range')
    return b''.join(word.to_bytes(2,'big') for word in
                    (0xf000|((displacement>>12)&0x7ff),0xf800|((displacement>>1)&0x7ff)))


def build(image):
    if sha(image) != PS_SHA256:
        raise ValueError('Patch requires the exact original B220 PS SHA-256')
    if image[ENTRY:RESUME] != bytes.fromhex('b538040d2000'):
        raise ValueError('Unexpected keypad prologue / displaced instructions')
    jpeg = image[JPEG_START:JPEG_START+JPEG_SIZE]
    if sha(jpeg) != JPEG_SHA256:
        raise ValueError('Unexpected source JPEG resource')
    if jpeg[:20] != bytes.fromhex('ffd8ffe000104a46494600010100000100010000') or jpeg[-2:] != b'\xff\xd9':
        raise ValueError('Unexpected JFIF header or JPEG endpoint')
    stripped = jpeg[:2]+jpeg[20:]
    with Image.open(BytesIO(jpeg)) as before,Image.open(BytesIO(stripped)) as after:
        before.load()
        after.load()
        rgb = before.convert('RGB').tobytes()
        if before.size != after.size or rgb != after.convert('RGB').tobytes():
            raise ValueError('Removing JFIF changed the decoded image')
    payload_address = (JPEG_START+len(stripped)+1)&~1
    # Keypad prologue already allocated [sp]: its output-pointer local.
    # LDR/STR do not change flags. Replay both displaced instructions, BX LR
    # returns to the original STR-zero at RESUME. The saved caller LR stays
    # in the native frame, so no new global RAM or stack convention is needed.
    code = bytes.fromhex('48029000040d20004770')
    literal = payload_address+len(code)
    if literal&3 or ((payload_address+4)&~3)+8 != literal:
        raise ValueError('Payload literal is not reachable by this Thumb LDR')
    payload = code+MARKER
    if payload_address+len(payload)>JPEG_START+JPEG_SIZE:
        raise ValueError('Payload exceeds the explicitly reclaimed metadata slot')
    replacement = bytearray(stripped.ljust(JPEG_SIZE,b'\x00'))
    offset = payload_address-JPEG_START
    replacement[offset:offset+len(payload)] = payload
    branch = thumb_bl(HOOK,payload_address)
    md = Cs(CS_ARCH_ARM,CS_MODE_THUMB|CS_MODE_BIG_ENDIAN)
    detour = list(md.disasm(branch,HOOK))
    if len(detour)!=1 or detour[0].mnemonic!='bl' or int(detour[0].op_str.lstrip('#'),0)!=payload_address:
        raise ValueError('Detour does not decode as the expected Thumb BL')
    instructions = [dict(address=hex(i.address),bytes=i.bytes.hex(),assembly=f'{i.mnemonic} {i.op_str}')
                    for i in md.disasm(code,payload_address)]
    result = bytearray(image)
    result[JPEG_START:JPEG_START+JPEG_SIZE] = replacement
    result[HOOK:RESUME] = branch
    manifest = dict(format='b220-key-marker-patch',version=1,source_sha256=PS_SHA256,
        output_sha256=sha(result),ps_size=len(result),entry=hex(ENTRY),hook=hex(HOOK),resume=hex(RESUME),
        payload=hex(payload_address),marker_checkpoint=hex(payload_address+4),marker_hex=MARKER.hex(),
        marker_storage='native keypad stack local [sp], temporary until original STR-zero resumes',
        marker_lifetime='Only guaranteed at marker_checkpoint; not a reserved global RAM address',
        displaced_instructions=image[HOOK:RESUME].hex(),detour=branch.hex(),instructions=instructions,
        resource=dict(start=hex(JPEG_START),size=JPEG_SIZE,source_sha256=sha(jpeg),
                      output_sha256=sha(replacement),reclaimed_bytes=18,decoded_rgb_sha256=sha(rgb),
                      pixels_unchanged=True,new_eoi_end=hex(JPEG_START+len(stripped))),
        changed_regions=[dict(start=hex(JPEG_START),size=JPEG_SIZE,kind='JPEG APP0 removal and tail payload'),
                         dict(start=hex(HOOK),size=4,kind='Thumb BL detour')])
    return bytes(result),manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path)
    parser.add_argument('--directory',type=Path,default=DEFAULT_DIRECTORY)
    args = parser.parse_args()
    source = args.source or next((ROOT/'analysis/extracted').glob('SC*.bin'))
    try:
        image = source.read_bytes()
        output,manifest = build(image)
        profile = json.loads((ROOT/'emulator/profiles/b220-l7a.json').read_text())
        identity = dict(source=str(source),reference=str(next((ROOT/'analysis/extracted').glob('SC*.bin'))))
        paths = [args.directory/'key-marker.bin',args.directory/'manifest.json']
        protect_outputs(profile,identity,paths)
        args.directory.mkdir(parents=True,exist_ok=True)
        paths[0].write_bytes(output)
        paths[1].write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    except (OSError,ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(dict(image=str(paths[0].resolve()),manifest=str(paths[1].resolve()),
                         sha256=manifest['output_sha256'],payload=manifest['payload'],
                         marker_checkpoint=manifest['marker_checkpoint']),indent=2))


if __name__ == '__main__':
    main()
