"""Copy the current PS and recolor RGB565 gold pixels in validated BE BMP resources."""
import argparse
import json
from pathlib import Path
import struct

from emulator.firmware import ROOT,load_firmware,protect_outputs
from .build_key_marker import sha

DEFAULT_SOURCE=ROOT/'analysis/firmware-patches/sd-app/sd-app.bin'
DIRECTORY=ROOT/'analysis/firmware-patches/green-theme'
OLD=0xfeee
NEW=0x07e0


def bitmap_resources(image):
    """Read the firmware's 16-bit BE BMP/RLE variant, including row bounds.

    Its native decoder at ARM 0x527728 uses two-byte (repeat,literal) packets,
    literal RGB565 words followed by one repeat color, and 00 00 ends a row.
    Return actual pixel-word positions; headers and packet counts are excluded.
    """
    position=0
    while True:
        start=image.find(b'MB',position)
        if start<0:return
        position=start+2
        if start+66>len(image):continue
        size,reserved,offset,dib,width,height=struct.unpack_from('>6I',image,start+2)
        planes,bpp,compression=struct.unpack_from('>HHI',image,start+26)
        if not (dib==40 and planes==1 and bpp==16 and compression==0x1003 and offset==66 and
                reserved==0 and 0<width<=2048 and 0<height<=2048 and 66<size<=0x40000 and start+size<=len(image)):
            continue
        if struct.unpack_from('>3I',image,start+54)!=(0xf800,0x07e0,0x001f):continue
        cursor=start+offset;end=start+size;pixels=[]
        for y in range(height):
            x=0
            while True:
                if cursor+2>end:raise ValueError(f'RLE packet exceeds resource at {start:#x}')
                repeat,literal=image[cursor:cursor+2];cursor+=2
                if not repeat and not literal:break
                if x+repeat+literal>width:raise ValueError(f'RLE row exceeds width at {start:#x}')
                words=literal+bool(repeat)
                if cursor+words*2>end:raise ValueError(f'RLE pixels exceed resource at {start:#x}')
                pixels.extend(range(cursor,cursor+words*2,2))
                cursor+=words*2;x+=repeat+literal
            if x!=width:raise ValueError(f'Incomplete RLE row {y} at {start:#x}')
        if cursor!=end:raise ValueError(f'Unexpected RLE tail at {start:#x}')
        yield dict(start=start,size=size,width=width,height=height,pixels=pixels)


def build(image):
    output=bytearray(image);changes=[];patches=[];count=0
    for resource in bitmap_resources(image):
        count+=1
        offsets=[p for p in resource['pixels'] if int.from_bytes(image[p:p+2],'big')==OLD]
        if not offsets:continue
        for p in offsets:output[p:p+2]=NEW.to_bytes(2,'big')
        patches.extend(offsets)
        start=resource['start'];size=resource['size']
        changes.append(dict(address=hex(start),size=size,width=resource['width'],height=resource['height'],
                            source_sha256=sha(image[start:start+size]),output_sha256=sha(output[start:start+size]),
                            replaced_words=len(offsets)))
    if not patches:raise ValueError('No matching gold pixels in the supported bitmap resources')
    # Only selected pixel words may differ. Reparse all rows after recoloring.
    allowed={byte for p in patches for byte in (p,p+1)}
    if len(output)!=len(image) or any(i not in allowed for i,(a,b) in enumerate(zip(image,output)) if a!=b):
        raise ValueError('Unexpected changes outside the selected pixel words')
    if sum(1 for _ in bitmap_resources(output))!=count:raise ValueError('Resource structure changed')
    manifest=dict(format='b220-green-theme',version=1,source_sha256=sha(image),output_sha256=sha(output),
                  size=len(output),old_rgb565=hex(OLD),new_rgb565=hex(NEW),validated_bitmaps=count,
                  replaced_words=len(patches),resources=changes,pixel_word_offsets=[hex(p) for p in patches],
                  executable_code_changed=False,screen_verification='Deferred to user; no cold boot or screenshots')
    return bytes(output),manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=DEFAULT_SOURCE)
    parser.add_argument('--directory',type=Path,default=DIRECTORY)
    parser.add_argument('--prepare-card',action='store_true',help='Prepare the two example apps on this variant\'s own card')
    args=parser.parse_args()
    try:
        if not args.source.exists():raise ValueError('Build the current SD APP image first: Start B220 SD APP.cmd')
        profile=json.loads((ROOT/'emulator/profiles/b220-l7a.json').read_text())
        image,identity=load_firmware(profile,args.source)
        paths=[args.directory/n for n in ('source-copy.bin','b220-green.bin','manifest.json')]
        protect_outputs(profile,identity,paths)
        if len({p.resolve() for p in paths})!=len(paths):raise ValueError('Output paths must be distinct')
        output,manifest=build(image)
        manifest['source']=str(args.source.resolve())
        manifest['source_copy']=str(paths[0].resolve())
        args.directory.mkdir(parents=True,exist_ok=True)
        paths[0].write_bytes(image)
        paths[1].write_bytes(output)
        paths[2].write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
        if args.source.read_bytes()!=image:raise ValueError('Source image changed while preparing the experiment')
        if args.prepare_card:
            from .install_sd_app import install
            card=ROOT/'analysis/emulator/experiments'/sha(output)/'sd-card.img'
            for name in ('card_demo.b22asm','card_second.b22asm'):
                install(ROOT/'apps'/name,card,ensure=True)
    except (OSError,ValueError) as exc:parser.error(str(exc))
    print(json.dumps(dict(image=str(paths[1].resolve()),source_sha256=sha(image),sha256=sha(output),
                          changed_resources=manifest['resources'],replaced_words=manifest['replaced_words']),indent=2))


if __name__=='__main__':main()
