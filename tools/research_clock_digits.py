"""Read original clock ABMP sprites; export alpha, native RGB565 previews and evidence."""
import argparse
import json
from pathlib import Path
import struct

from PIL import Image, ImageDraw

from emulator.firmware import ROOT, load_firmware, protect_outputs

DIRECTORY = ROOT/'analysis/clock-research'
DIGITS = (0x345c0, 0x347e4, 0x3491c, 0x34b2c, 0x34d70,
          0x34ef4, 0x350b8, 0x35300, 0x354d4, 0x35738)
COLON = 0x35978


def rgb(value):
    r, g, b = (value >> 11) & 31, (value >> 5) & 63, value & 31
    return (r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)


def blend565(color, background, alpha):
    # Same component arithmetic and rounding as ARM 0x40002a30..0x40002ac0.
    channels = []
    for shift, mask, bias in ((11,31,144), (5,63,160), (0,31,144)):
        src, dst = (color >> shift) & mask, (background >> shift) & mask
        channels.append(((src*alpha + dst*(256-alpha) + bias) >> 8) & mask)
    return channels[0] << 11 | channels[1] << 5 | channels[2]


def decode(image, address):
    if image[address:address+4] != b'ABMP':
        raise ValueError(f'ABMP signature missing at {address:#x}')
    width, height, opaque, partial = struct.unpack_from('>4H', image, address+4)
    top, left, bottom, right = image[address+12:address+16]
    if not (0<width<=2048 and 0<height<=2048 and opaque>0 and opaque+partial<=65536
            and left+right<width and top+bottom<height):
        raise ValueError('Invalid ABMP geometry/palette')
    count = opaque+partial-1  # index 0 is transparent, has no palette entry
    bits = (opaque+partial-1).bit_length()
    palette_at = address+16
    alpha_at = palette_at+count*2
    data_at = alpha_at+(partial+1)//2*2
    active_width, active_height = width-left-right, height-top-bottom
    byte_count = (active_width*active_height*bits+15)//16*2
    end = data_at+byte_count
    if end>len(image):
        raise ValueError('ABMP data exceeds image')
    palette = struct.unpack_from(f'>{count}H', image, palette_at)
    alpha = image[alpha_at:alpha_at+partial]
    words = struct.unpack_from(f'>{byte_count//2}H', image, data_at)
    rgba = Image.new('RGBA', (width,height))
    native = Image.new('RGB', (width,height))
    indexed = []
    for k in range(active_width*active_height):
        index = 0
        # Native LDRH reads BE words; indices are consumed least-significant
        # bit first WITHIN each 16-bit word (not a normal MSB-first byte stream).
        for shift in range(bits):
            bit = k*bits+shift
            index |= (words[bit//16] >> (bit%16) & 1) << shift
        if index>count:
            raise ValueError('ABMP palette index outside table')
        indexed.append(index)
        if not index:
            continue
        color = palette[index-1]
        opacity = 255 if index<opaque else alpha[index-opaque]
        x, y = left+k%active_width, top+k//active_width
        rgba.putpixel((x,y), (*rgb(color),opacity))
        native.putpixel((x,y), rgb(color if index<opaque else blend565(color,0,opacity)))
    report = dict(address=hex(address), size=end-address, end_exclusive=hex(end),
        width=width, height=height, margins=dict(top=top,left=left,bottom=bottom,right=right),
        opaque_index_limit=opaque, opaque_colors=opaque-1, alpha_entries=partial,
        alpha_min=min(alpha) if alpha else None, alpha_max=max(alpha) if alpha else None,
        alpha_levels_used=sorted({alpha[i-opaque] for i in indexed if i>=opaque}),
        palette_rgb565=sorted({hex(c) for c in palette}), bits_per_index=bits,
        palette_address=hex(palette_at), alpha_address=hex(alpha_at),
        index_data_address=hex(data_at), index_data_size=byte_count)
    return rgba, native, report


def extract(image, directory):
    directory.mkdir(parents=True, exist_ok=True)
    records, previews = [], {}
    sheet = Image.new('RGB', (660,280), '#24282e')
    draw = ImageDraw.Draw(sheet)
    for i, address in enumerate((*DIGITS,COLON)):
        name = str(i) if i<10 else 'colon'
        rgba, native, record = decode(image,address)
        record['character'] = str(i) if i<10 else ':'
        records.append(record); previews[record['character']] = native
        rgba.save(directory/f'clock-{name}.png')
        native.save(directory/f'clock-{name}-black.png')
        x, y = (i%6)*110, (i//6)*140
        draw.text((x+5,y+5),record['character']+' '+hex(address),fill='white')
        sheet.paste(native.resize((native.width*3,native.height*3),Image.Resampling.NEAREST),(x+8,y+26))
        draw.text((x+5,y+118),f"{record['size']} B / {record['alpha_entries']} alpha",fill='white')
    sheet.save(directory/'clock-digits.png')
    clock = Image.new('RGB',(61,28));x=0
    for char in '00:05':
        bitmap = previews[char]
        clock.paste(bitmap,(x,28-bitmap.height));x += 6 if char==':' else 14
    clock.resize((244,112),Image.Resampling.NEAREST).save(directory/'clock-00-05.png')
    return records


def observe(directory):
    # Resume a compatible snapshot in a NEW board. Only actual keypad events
    # and a read-only hook are used; no current emulator window is controlled.
    from unicorn import UC_HOOK_CODE
    from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_LR
    from emulator.snapshot import load_snapshot
    from .prove_catch_game import run_for
    snapshot = ROOT/'analysis/firmware-patches/sd-app/proof-multi/01-soft_left.b220state'
    firmware = ROOT/'analysis/firmware-patches/sd-app/sd-app.bin'
    board = load_snapshot(snapshot,firmware_path=firmware)
    board.keypad.key('soft_left',True);board.keypad.key('soft_left',False);run_for(board,3)
    calls = []
    def entered(uc,pc,size,data):
        descriptor = bytes(uc.mem_read(uc.reg_read(UC_ARM_REG_R0),28))
        palette = int.from_bytes(descriptor[20:24],'big')
        address = palette-16
        if address not in (*DIGITS,COLON):
            return
        canvas = bytes(uc.mem_read(uc.reg_read(UC_ARM_REG_R2),12))
        calls.append(dict(resource=hex(address),descriptor=descriptor.hex(),canvas=canvas.hex(),
                          bitstream=hex(uc.reg_read(UC_ARM_REG_R1)),caller=hex(uc.reg_read(UC_ARM_REG_LR))))
    hook = board.uc.hook_add(UC_HOOK_CODE,entered,begin=0x40002800,end=0x40002800)
    try:
        board.keypad.key('end',True);board.keypad.key('end',False);run_for(board,5)
    finally:
        board.uc.hook_del(hook)
    if not any(int(c['resource'],0)==DIGITS[0] for c in calls) or not any(int(c['resource'],0)==COLON for c in calls):
        raise ValueError('Clock draw not observed; do not infer success from an idle frame')
    checks = []
    ps = firmware.read_bytes()
    for call in calls:
        canvas = bytes.fromhex(call['canvas']);x,y,stride,height,buffer = struct.unpack('>4HI',canvas)
        _, expected, _ = decode(ps,int(call['resource'],0))
        values = []
        for row in range(expected.height):
            data = bytes(board.uc.mem_read(buffer+2*((y+row)*stride+x),expected.width*2))
            values.extend(struct.unpack(f'>{expected.width}H',data))
        actual = Image.new('RGB',expected.size);actual.putdata([rgb(v) for v in values])
        equal = actual.tobytes()==expected.tobytes()
        checks.append(dict(resource=call['resource'],x=x,y=y,all_pixels_match=equal))
    if not all(c['all_pixels_match'] for c in checks):
        raise ValueError(f'Native framebuffer differs from decoded clock sprites: {checks}')
    return dict(status='PASS',snapshot=str(snapshot),firmware=board.firmware,
                host_cpu_or_ram_writes=False,alpha_function_runtime='0x40002800',
                alpha_function_rom='0x744f70',calls=calls,pixel_checks=checks)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,default=DIRECTORY)
    parser.add_argument('--observe',action='store_true',help='Read-only observation of native clock draw in separate snapshot board')
    args = parser.parse_args()
    profile = json.loads((ROOT/'emulator/profiles/b220-l7a.json').read_text())
    image, identity = load_firmware(profile)
    protect_outputs(profile,identity,[args.directory/f'clock-{n}.png' for n in (*map(str,range(10)),'colon')])
    records = extract(image,args.directory)
    result = dict(source=identity,format='ABMP',resources=records,
        total_encoded_bytes=sum(r['size'] for r in records),
        occupied_span_bytes=int(records[-1]['end_exclusive'],0)-DIGITS[0],
        resource_group_pointer='0x72d334',original_firmware_modified=False)
    if args.observe:
        result['native_observation'] = observe(args.directory)
    (args.directory/'clock-resources.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(resources=len(records),total_encoded_bytes=result['total_encoded_bytes'],
                          occupied_span_bytes=result['occupied_span_bytes'],
                          native_observation=result.get('native_observation',{}).get('status')),indent=2))


if __name__ == '__main__':
    main()
