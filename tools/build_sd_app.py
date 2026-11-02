"""Build the native SD bytecode loader as a separate PS image."""
import argparse
import json
from pathlib import Path

from emulator.firmware import ROOT,protect_outputs
from .build_catch_game import compile_game,build as game_image,ROM
from .build_key_marker import sha

DIRECTORY=ROOT/'analysis/firmware-patches/sd-app'


def build(image,payload,entry):
    result,manifest=game_image(image,payload,entry)
    result=bytearray(result)
    for offset in (0x16c0f8,0x16c3b4):
        result[offset:offset+14]='SD Apps'.encode('utf-16-be')
    if image[0x60eee8:0x60eef0]!=bytes.fromhex('b580210048f0f6c8'):
        raise ValueError('Unexpected Sokoban launch function')
    result[0x60eee8:0x60eef0]=bytes.fromhex('4b004718')+(ROM|1).to_bytes(4,'big')
    manifest.update(format='b220-sd-app',output_sha256=sha(result),
                    state=dict(address='0x04388f50',size=80,ownership='Original 81-byte Sokoban board'),
                    catalog='E:\\APPS.LST',max_applications=16,source_code='patches/sd_app.c',
                    launch=dict(hook='0x60eee8',original=image[0x60eee8:0x60eef0].hex(),replacement=hex(ROM|1),window='0x160002'))
    manifest['changed_regions'].append(dict(start='0x60eee8',size=8,kind='Sokoban launch tail-call to SD application selector'))
    return bytes(result),manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,default=DIRECTORY)
    args=parser.parse_args()
    try:
        source=next((ROOT/'analysis/extracted').glob('SC*.bin'))
        profile=json.loads((ROOT/'emulator/profiles/b220-l7a.json').read_text())
        protect_outputs(profile,dict(source=str(source),reference=str(source)),
                        [args.directory/n for n in ('catch-game.elf','sd-app.bin','manifest.json')])
        payload,entry,toolchain=compile_game(args.directory,'patches/sd_app.c')
        image,manifest=build(source.read_bytes(),payload,entry)
        manifest['toolchain']=toolchain
        (args.directory/'sd-app.bin').write_bytes(image)
        (args.directory/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    except (OSError,ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(dict(image=str(args.directory/'sd-app.bin'),entry=hex(entry),
                          code_size=len(payload),sha256=sha(image)),indent=2))


if __name__=='__main__':main()
