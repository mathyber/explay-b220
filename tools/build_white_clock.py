"""Recolor only the eleven clock ABMP palettes in a separate current-font PS."""
import argparse
import hashlib
import json
from pathlib import Path
import struct

from emulator.firmware import ROOT, load_firmware, protect_outputs
from .research_clock_digits import DIGITS, COLON, decode, extract

DIRECTORY = ROOT/'analysis/firmware-patches/white-clock'
DEFAULT_SOURCE = ROOT/'analysis/firmware-patches/main-font/b220-narrow.bin'   # Nokia font (Start B220 WHITE CLOCK.cmd)
OLD, NEW = 0xff55, 0xffff


def sha(data):
    return hashlib.sha256(data).hexdigest()


def build(image):
    output = bytearray(image)
    allowed = set()
    records = []
    for i, address in enumerate((*DIGITS, COLON)):
        before, _, info = decode(image, address)
        expected_size = (13,28) if address != COLON else (4,16)
        if before.size != expected_size or info['palette_rgb565'] != [hex(OLD)]:
            raise ValueError(f'Unexpected original clock palette or geometry at {address:#x}')
        start, end = int(info['palette_address'],0), int(info['alpha_address'],0)
        for position in range(start,end,2):
            if struct.unpack_from('>H',image,position)[0] != OLD:
                raise ValueError('Palette contains an unexpected color')
            struct.pack_into('>H',output,position,NEW)
            allowed.update((position,position+1))
        after, _, checked = decode(output,address)
        if (before.getchannel('A').tobytes() != after.getchannel('A').tobytes() or
                checked['palette_rgb565'] != [hex(NEW)] or
                image[end:int(info['end_exclusive'],0)] != output[end:int(info['end_exclusive'],0)]):
            raise ValueError('Clock transparency or indices changed')
        records.append(dict(character=str(i) if i<10 else ':', address=hex(address),
                            palette_start=hex(start), palette_size=end-start,
                            palette_words=(end-start)//2, alpha_unchanged=True))
    if len(output) != len(image) or any(i not in allowed for i,(a,b) in enumerate(zip(image,output)) if a!=b):
        raise ValueError('Changes outside clock palettes')
    return bytes(output), dict(format='b220-white-clock',version=1,source_sha256=sha(image),
        output_sha256=sha(output),size=len(output),old_rgb565=hex(OLD),new_rgb565=hex(NEW),
        changed_bytes=sum(a!=b for a,b in zip(image,output)),
        palette_words=sum(r['palette_words'] for r in records),resources=records,
        code_changed=False,alpha_unchanged=True,indices_unchanged=True,
        added_rom_bytes=0,added_ram_bytes=0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=DEFAULT_SOURCE)
    parser.add_argument('--directory',type=Path,default=DIRECTORY)
    parser.add_argument('--prepare-card',action='store_true')
    args = parser.parse_args()
    try:
        profile = json.loads((ROOT/'emulator/profiles/b220-l7a.json').read_text())
        image,identity = load_firmware(profile,args.source)
        paths = [args.directory/name for name in ('source-copy.bin','b220-white-clock.bin','manifest.json')]
        previews = [args.directory/'preview'/f'clock-{name}{suffix}.png'
                    for name in (*map(str,range(10)),'colon') for suffix in ('','-black')]
        previews += [args.directory/'preview'/name for name in ('clock-digits.png','clock-00-05.png')]
        protect_outputs(profile,identity,paths+previews)
        if len({p.resolve() for p in paths+previews}) != len(paths+previews):
            raise ValueError('Output paths must be distinct')
        output,manifest = build(image)
        manifest['source'] = str(args.source.resolve())
        args.directory.mkdir(parents=True,exist_ok=True)
        paths[0].write_bytes(image);paths[1].write_bytes(output)
        extract(output,args.directory/'preview')
        paths[2].write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
        if args.source.read_bytes() != image:
            raise ValueError('Source firmware changed while building')
        if args.prepare_card:
            from .install_sd_app import install
            card = ROOT/'analysis/emulator/experiments'/sha(output)/'sd-card.img'
            for name in ('card_demo.b22asm','card_second.b22asm'):
                install(ROOT/'apps'/name,card,ensure=True)
    except (OSError,ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(dict(image=str(paths[1].resolve()),sha256=sha(output),
                          palette_words=manifest['palette_words'],changed_bytes=manifest['changed_bytes'],
                          alpha_unchanged=True,added_rom_bytes=0,added_ram_bytes=0),indent=2))


if __name__ == '__main__':
    main()
