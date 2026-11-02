"""Extend the native key-marker patch with a conditional stock graphics call.

Only the exact Russian time-title pointer at one label draw callsite changes.
The wrapper tail-calls the original graphics routine with the original stack.
"""
import argparse
from io import BytesIO
import json
from pathlib import Path

from PIL import Image
from capstone import Cs,CS_ARCH_ARM,CS_MODE_THUMB,CS_MODE_BIG_ENDIAN
from emulator.firmware import ROOT,protect_outputs
from .build_key_marker import build as build_key_marker,thumb_bl,sha

DEFAULT_DIRECTORY = ROOT/'analysis/firmware-patches/ui-label'
JPEG_START = 0x421e7c
JPEG_SIZE = 599
JPEG_SHA256 = '3c080f39fda3939ddb92b2395fa6757e8b5eb7114d480fbbcb43269b88c5b811'
HOOK = 0x5b7dd0
NATIVE_DRAW = 0x4cf432
ORIGINAL_TEXT = 0x1294d8
TEXT_RESOURCE = 0x42263b
TEXT = 'B220 MOD'


def build(image):
    base,key_manifest = build_key_marker(image)
    if image[HOOK:HOOK+4] != bytes.fromhex('f717fb2f'):
        raise ValueError('Unexpected native label draw callsite')
    if image[ORIGINAL_TEXT:ORIGINAL_TEXT+10] != '\u0412\u0440\u0435\u043c\u044f'.encode('utf-16-be'):
        raise ValueError('Unexpected Russian time-title string')
    jpeg = image[JPEG_START:JPEG_START+JPEG_SIZE]
    if sha(jpeg) != JPEG_SHA256 or jpeg[:2]!=b'\xff\xd8' or jpeg[-2:]!=b'\xff\xd9':
        raise ValueError('Unexpected bounded metadata JPEG resource')
    stripped = jpeg[:2]
    position = 2
    removed = []
    while position<len(jpeg):
        if jpeg[position:position+2] == b'\xff\xda':
            stripped += jpeg[position:]
            break
        if jpeg[position]!=0xff or position+4>len(jpeg):
            raise ValueError('Invalid JPEG segment boundary')
        length = int.from_bytes(jpeg[position+2:position+4],'big')+2
        if length<4 or position+length>len(jpeg):
            raise ValueError('Invalid JPEG segment length')
        segment = jpeg[position:position+length]
        if jpeg[position+1] in (0xe0,0xed,0xee):
            removed.append(dict(marker=hex(jpeg[position+1]),size=length))
        else:
            stripped += segment
        position += length
    if len(jpeg)-len(stripped)!=48:
        raise ValueError('Expected exactly 48 metadata bytes to reclaim')
    with Image.open(BytesIO(jpeg)) as a,Image.open(BytesIO(stripped)) as b:
        if a.info.get('adobe_transform')!=1 or [v[0] for v in a.layer]!=[1,2,3]:
            raise ValueError('Expected standard YCbCr component interpretation')
        pixels = a.convert('RGB').tobytes()
        if a.size!=b.size or pixels!=b.convert('RGB').tobytes():
            raise ValueError('Removing redundant metadata changed RGB pixels')
    payload_address = (JPEG_START+len(stripped)+1)&~1
    text_jpeg = image[TEXT_RESOURCE:TEXT_RESOURCE+JPEG_SIZE]
    if sha(text_jpeg)!=JPEG_SHA256 or text_jpeg[:20]!=jpeg[:20]:
        raise ValueError('Unexpected second JPEG resource for the UTF-16 pool')
    text_stripped = text_jpeg[:2]+text_jpeg[20:]
    text_address = TEXT_RESOURCE+len(text_stripped)
    text = TEXT.encode('utf-16-be')+b'\x00\x00'
    if text_address&1 or len(text)!=18:
        raise ValueError('UTF-16 pool does not fit its 18 reclaimed JFIF bytes')
    with Image.open(BytesIO(text_jpeg)) as a,Image.open(BytesIO(text_stripped+text)) as b:
        if a.convert('RGB').tobytes()!=b.convert('RGB').tobytes():
            raise ValueError('UTF-16 pool changed the second resource pixels')
    # Real native ABI: R0 is the graphics device context, R1/R2 contain packed
    # coordinates, R3 is UTF-16, and [original SP] is the code-unit count.
    # PUSH saves scratch R4; [SP+4] then refers to that one outgoing argument.
    # POP restores SP before forwarding all the other stack arguments.
    code = bytes.fromhex('b4104c0542a3d1024b05240894014c0346a4bc10476046c0')
    literals = (ORIGINAL_TEXT.to_bytes(4,'big')+(NATIVE_DRAW|1).to_bytes(4,'big')+
                text_address.to_bytes(4,'big'))
    payload = code+literals
    if payload_address&3 or payload_address+len(payload)>JPEG_START+JPEG_SIZE:
        raise ValueError('Graphics wrapper exceeds its reclaimed metadata slot')
    replacement = bytearray(stripped.ljust(JPEG_SIZE,b'\x00'))
    offset = payload_address-JPEG_START
    replacement[offset:offset+len(payload)] = payload
    result = bytearray(base)
    result[JPEG_START:JPEG_START+JPEG_SIZE] = replacement
    result[TEXT_RESOURCE:TEXT_RESOURCE+JPEG_SIZE] = text_stripped+text
    branch = thumb_bl(HOOK,payload_address)
    result[HOOK:HOOK+4] = branch
    md = Cs(CS_ARCH_ARM,CS_MODE_THUMB|CS_MODE_BIG_ENDIAN)
    instructions = [dict(address=hex(i.address),bytes=i.bytes.hex(),assembly=f'{i.mnemonic} {i.op_str}')
                    for i in md.disasm(code,payload_address)]
    manifest = dict(format='b220-ui-label-patch',version=1,source_sha256=key_manifest['source_sha256'],
        key_marker_base_sha256=key_manifest['output_sha256'],output_sha256=sha(result),ps_size=len(result),
        hook=hex(HOOK),original_call=image[HOOK:HOOK+4].hex(),detour=branch.hex(),
        native_draw=hex(NATIVE_DRAW),original_text=hex(ORIGINAL_TEXT),text=TEXT,
        payload=hex(payload_address),payload_size=len(payload),string_address=hex(text_address),
        tail_call_checkpoint=hex(payload_address+20),instructions=instructions,
        parameters=dict(r0='native graphics device context',r1='packed coordinates',r2='packed coordinates',
                        r3='UTF-16 BE text pointer',stack_0='code-unit count',stack_12='native font argument',
                        stack_other='preserved'),
        resource=dict(start=hex(JPEG_START),size=JPEG_SIZE,removed_segments=removed,
                      source_sha256=sha(jpeg),output_sha256=sha(replacement),
                      decoded_rgb_sha256=sha(pixels),pixels_unchanged=True,
                      new_eoi_end=hex(JPEG_START+len(stripped))),
        text_resource=dict(start=hex(TEXT_RESOURCE),size=JPEG_SIZE,reclaimed_bytes=18,
                           output_sha256=sha(text_stripped+text),pixels_unchanged=True),
        key_marker=key_manifest,
        changed_regions=key_manifest['changed_regions']+[
            dict(start=hex(JPEG_START),size=JPEG_SIZE,kind='redundant JPEG metadata removal and graphics wrapper'),
            dict(start=hex(TEXT_RESOURCE),size=JPEG_SIZE,kind='JFIF removal and UTF-16 pool'),
            dict(start=hex(HOOK),size=4,kind='conditional native label draw detour')])
    return bytes(result),manifest


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path)
    p.add_argument('--directory',type=Path,default=DEFAULT_DIRECTORY)
    args = p.parse_args()
    source = args.source or next((ROOT/'analysis/extracted').glob('SC*.bin'))
    try:
        output,manifest = build(source.read_bytes())
        profile = json.loads((ROOT/'emulator/profiles/b220-l7a.json').read_text())
        identity = dict(source=str(source),reference=str(next((ROOT/'analysis/extracted').glob('SC*.bin'))))
        paths = [args.directory/'b220-mod.bin',args.directory/'manifest.json']
        protect_outputs(profile,identity,paths)
        args.directory.mkdir(parents=True,exist_ok=True)
        paths[0].write_bytes(output)
        paths[1].write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    except (OSError,ValueError) as exc:
        p.error(str(exc))
    print(json.dumps(dict(image=str(paths[0].resolve()),sha256=manifest['output_sha256'],
                         text=TEXT,payload=manifest['payload']),indent=2))


if __name__=='__main__':
    main()
