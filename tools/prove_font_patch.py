"""Check native glyph descriptors in isolation; optionally cold-boot one UI sample."""
import argparse
import json
from pathlib import Path
import struct
import time

from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_MODE_BIG_ENDIAN, UC_HOOK_CODE
from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
    UC_ARM_REG_R3, UC_ARM_REG_CPSR, UC_ARM_REG_PC, UC_ARM_REG_SP,
    UC_ARM_REG_LR, UC_CPU_ARM_926)
from .build_font_patch import DIRECTORY, SETS, sha
from emulator.firmware import ROOT


def check_native_apis(image, large_height=14, small_height=12):
    # An isolated ABI fixture: direct calls and initial RAM, NOT a booted phone.
    uc = Uc(UC_ARCH_ARM, UC_MODE_ARM | UC_MODE_BIG_ENDIAN)
    uc.ctl_set_cpu_model(UC_CPU_ARM_926)
    uc.mem_map(0, 0x800000); uc.mem_write(0, image)
    uc.mem_map(0x04000000, 0x400000)
    uc.mem_write(0x04293600, image[0x7237cc:0x7437e8])
    stack, outputs, returned = 0x043ff000, 0x04001000, 0x7ff000

    def call(pc, *args, stack_arg=0):
        uc.reg_write(UC_ARM_REG_CPSR, 0x2f3)  # Thumb, big-endian data, IRQ masked
        uc.reg_write(UC_ARM_REG_SP, stack); uc.reg_write(UC_ARM_REG_LR, returned | 1)
        uc.mem_write(stack, struct.pack('>I', stack_arg))
        for register, value in zip((UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3), args):
            uc.reg_write(register, value)
        uc.emu_start(pc | 1, returned, count=10000)
        if uc.reg_read(UC_ARM_REG_PC) != returned or uc.reg_read(UC_ARM_REG_SP) != stack:
            raise ValueError(f'Native function did not return cleanly: {pc:#x}')
        return uc.reg_read(UC_ARM_REG_R0)

    count = 0
    samples = []
    for font_id in (3, 4, 6):
        height = small_height if font_id == 3 else large_height
        for s in SETS:
            if s.height != (14 if font_id == 3 else 16):
                continue
            for index, cp in enumerate(s.codepoints):
                # The actual descriptor routine combines Unicode mapping, table
                # selection, width lookup, offset lookup and patched height.
                uc.mem_write(outputs, b'\xa5'*8)
                call(0x4cdc00, outputs, outputs+4, outputs+5, font_id, stack_arg=cp)
                pointer, width, actual_height = struct.unpack('>IBB', bytes(uc.mem_read(outputs, 6)))
                expected = s.bitmap+struct.unpack_from('>I', image, s.offsets+4*index)[0]
                if (pointer, width, actual_height) != (expected, image[s.widths+index], height):
                    raise ValueError(f'Native glyph mismatch: font {font_id} U+{cp:04X}')
                if call(0x4cdcc4, font_id, cp) != height:
                    raise ValueError('Public character height disagrees with the descriptor')
                count += 1
                if cp in (0x41e, 0x401, 0x449, 0x20):
                    samples.append(dict(font_id=font_id, codepoint=f'U+{cp:04X}', width=width, height=height))
    return dict(status='PASS', fixture='Isolated native ABI calls, not a phone boot',
                descriptors_checked=count, firmware_sha256=sha(image), samples=samples)


def cold_boot(firmware, directory):
    from emulator.board import Board
    from .sd_image import Fat16, create_image
    from .b22asm import assemble
    from .prove_key_marker import execute
    from .prove_catch_game import run_for
    volume = Fat16(create_image())
    for name, source in (('DEMO.B22', 'card_demo.b22asm'), ('SECOND.B22', 'card_second.b22asm')):
        volume.add_file(name, assemble((ROOT/'apps'/source).read_text(encoding='utf-8')))
    volume.add_file('APPS.LST', b'DEMO.B22\r\nSECOND.B22\r\n')
    board = Board(explore=True, firmware_path=firmware, sd_image=volume.image)
    heights = {'large': 0, 'small': 0}
    def height_used(uc, pc, size, data):
        if pc == 0x5b8716:
            heights['large'] += 1
        elif pc == 0x5b871e:
            heights['small'] += 1
    hook = board.uc.hook_add(UC_HOOK_CODE, height_used, begin=0x5b8716, end=0x5b871e)
    try:
        print('Cold boot with an isolated card; one final menu frame...', flush=True)
        board.stop_after_frames = 5
        deadline = time.monotonic()+300
        while board.full_lcd_frames < 5:
            if time.monotonic() > deadline:
                raise ValueError('Cold boot timed out')
            result = execute(board, 200000000, 5)
            if result['engine_error'] or result['stop']['reason'] not in (
                    'wall time budget', 'instruction budget', 'requested LCD frame count'):
                raise ValueError(f'Cold boot stopped: {result}')
        board.stop_after_frames = 0
        run_for(board, 5)
        for key in ('soft_left', 'soft_left', 'soft_left', 'down', 'ok'):
            print(f'Key: {key}', flush=True)
            board.keypad.key(key, True); board.keypad.key(key, False)
            run_for(board, 6)
        if not heights['large']:
            raise ValueError('Cold boot did not exercise the modified font-height path')
        if sha(bytes(board.uc.mem_read(0, len(firmware.read_bytes())))) != board.firmware['sha256']:
            raise ValueError('Guest PS memory changed during cold boot')
        frame = board.lcd.save(directory, 'native-menu')
        return dict(status='PASS', firmware_sha256=board.firmware['sha256'],
                    frame=frame, host_cpu_or_ram_writes=False,
                    native_font_height_calls=heights,
                    note='Independent cold boot and real keypad events; one final frame')
    finally:
        board.uc.hook_del(hook)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=DIRECTORY)
    parser.add_argument('--boot', action='store_true', help='Also cold-boot once and capture one menu frame')
    args = parser.parse_args()
    firmware = args.directory/'b220-narrow.bin'
    manifest = json.loads((args.directory/'manifest.json').read_text())
    image = firmware.read_bytes()
    if sha(image) != manifest['output_sha256']:
        parser.error('Firmware differs from its manifest')
    report = check_native_apis(image, manifest['sets'][0]['height'], manifest['sets'][1]['height'])
    if args.boot:
        report['cold_boot'] = cold_boot(firmware, args.directory)
    (args.directory/'proof.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
