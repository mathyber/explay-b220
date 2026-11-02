"""Compile a freestanding C game and redirect the stock Sokoban window to it."""
import argparse
from io import BytesIO
import json
import os
from pathlib import Path
import struct
import subprocess

from PIL import Image
from emulator.firmware import ROOT,protect_outputs
from .build_ui_label import build as build_ui_label
from .build_key_marker import JPEG_START,JPEG_SIZE,sha
from .setup_arm_toolchain import EXECUTABLE,VERSION

DIRECTORY = ROOT/'analysis/firmware-patches/game'
ROM = 0x14d000
LIMIT = 0x152f2a
POINTERS = (0x70d154,0x70d198,0x70d1dc,0x70d220)
STATE = 0x04388f50


def elf_payload(data):
    if data[:7]!=b'\x7fELF\x01\x02\x01' or struct.unpack_from('>HH',data,16)!=(2,40):
        raise ValueError('Expected an executable ELF32 big-endian ARM image')
    entry = struct.unpack_from('>I',data,24)[0]
    table = struct.unpack_from('>I',data,32)[0]
    stride,count,index = struct.unpack_from('>HHH',data,46)
    sections = [struct.unpack_from('>10I',data,table+i*stride) for i in range(count)]
    strings = data[sections[index][4]:sections[index][4]+sections[index][5]]
    payload = None
    for section in sections:
        name = strings[section[0]:].split(b'\0',1)[0]
        if section[1] in (4,9) and section[5]:
            raise ValueError('Unresolved ELF relocations remain')
        if section[2]&2 and section[5]:
            if name!=b'.payload' or section[1]!=1 or section[3]!=ROM:
                raise ValueError(f'Unexpected allocated ELF section: {name!r}')
            payload = data[section[4]:section[4]+section[5]]
    if payload is None or not entry&1 or not ROM<=entry&~1<ROM+len(payload) or ROM+len(payload)>LIMIT:
        raise ValueError('Game code does not fit its bounded ROM resource allocation')
    return payload,entry


def compile_game(directory):
    if not EXECUTABLE.exists():
        raise ValueError('Run python -m tools.setup_arm_toolchain first')
    environment = os.environ.copy()
    environment['ZIG_GLOBAL_CACHE_DIR'] = str(ROOT/'analysis/toolchains/cache')
    environment['ZIG_LOCAL_CACHE_DIR'] = str(ROOT/'analysis/toolchains/local-cache')
    directory.mkdir(parents=True,exist_ok=True)
    elf = directory/'catch-game.elf'
    command = [str(EXECUTABLE),'cc','-target','armeb-freestanding-eabi','-mcpu=arm7tdmi','-mthumb',
               '-Oz','-ffreestanding','-fno-builtin','-fno-stack-protector','-fno-unwind-tables',
               '-fno-asynchronous-unwind-tables','-nostdlib','-Wl,-T,patches/catch_game.ld',
               '-Wl,--gc-sections','-Wl,--build-id=none','patches/catch_game.c','-o',str(elf)]
    run = subprocess.run(command,cwd=ROOT,env=environment,capture_output=True,text=True,encoding='utf-8')
    if run.returncode:
        raise ValueError(f'ARM compilation failed:\n{run.stdout}\n{run.stderr}')
    data = elf.read_bytes()
    payload,entry = elf_payload(data)
    return payload,entry,dict(version=VERSION,command=command,elf_sha256=sha(data),
                              source_sha256=sha((ROOT/'patches/catch_game.c').read_bytes()),
                              linker_sha256=sha((ROOT/'patches/catch_game.ld').read_bytes()))


def build(image,payload,entry):
    base,previous = build_ui_label(image)
    stream = BytesIO()
    Image.new('RGB',(176,220),(8,16,24)).save(stream,format='JPEG',quality=80,subsampling=1,optimize=False)
    wallpaper = stream.getvalue()
    if JPEG_START+len(wallpaper)>ROM or ROM+len(payload)>LIMIT or entry&1!=1:
        raise ValueError('Code or replacement wallpaper exceeds the reserved resource')
    result = bytearray(base)
    resource = bytearray(JPEG_SIZE)
    resource[:len(wallpaper)] = wallpaper
    resource[ROM-JPEG_START:ROM-JPEG_START+len(payload)] = payload
    resource[LIMIT-JPEG_START:LIMIT-JPEG_START+14] = base[LIMIT:LIMIT+14]
    result[JPEG_START:JPEG_START+JPEG_SIZE] = resource
    for pointer in POINTERS:
        if image[pointer:pointer+4]!=(0x60f74f).to_bytes(4,'big'):
            raise ValueError('Unexpected Sokoban window handler descriptor')
        result[pointer:pointer+4] = entry.to_bytes(4,'big')
    for offset,old,new in ((0x16c0f8,'Sokoban','Catch! '),
                           (0x16c3b4,'\u0421\u043e\u043a\u043e\u0431\u0430\u043d','\u041f\u043e\u0439\u043c\u0430\u0439!')):
        if image[offset:offset+14]!=old.encode('utf-16-be'):
            raise ValueError('Unexpected stock game title')
        result[offset:offset+14] = new.encode('utf-16-be')
    manifest = dict(format='b220-catch-game',version=1,source_sha256=previous['source_sha256'],
        output_sha256=sha(result),entry=hex(entry),rom=hex(ROM),code_size=len(payload),
        code_sha256=sha(payload),handler_descriptors=[hex(p) for p in POINTERS],
        original_handler='0x60f74f',state=dict(address=hex(STATE),size=32,
            ownership='First 32 bytes of the original 81-byte Sokoban board at game root +0x14'),
        wallpaper=dict(address=hex(JPEG_START),slot_size=JPEG_SIZE,jpeg_size=len(wallpaper),
                       change='One built-in wallpaper replaced by solid dark blue; code follows its EOI'),
        inherited_ui_patch_sha256=previous['output_sha256'],key_marker_preserved=True)
    return bytes(result),manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,default=DIRECTORY)
    args = parser.parse_args()
    try:
        source = next((ROOT/'analysis/extracted').glob('SC*.bin'))
        profile = json.loads((ROOT/'emulator/profiles/b220-l7a.json').read_text())
        protect_outputs(profile,dict(source=str(source),reference=str(source)),
                        [args.directory/'catch-game.elf',args.directory/'catch-game.bin',args.directory/'manifest.json'])
        payload,entry,toolchain = compile_game(args.directory)
        output,manifest = build(source.read_bytes(),payload,entry)
        manifest['toolchain'] = toolchain
        (args.directory/'catch-game.bin').write_bytes(output)
        (args.directory/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    except (OSError,ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(dict(image=str((args.directory/'catch-game.bin').resolve()),entry=hex(entry),
                         code_bytes=len(payload),sha256=manifest['output_sha256']),indent=2))


if __name__=='__main__':
    main()
