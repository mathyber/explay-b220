"""Observe a real KPD-triggered native call in an independently booted PS.

CPU code/data are never written by the harness. Host actions are cold boot
or validated snapshot restore, keypad edges, breakpoints and read-only checks.
"""
import argparse
import hashlib
import json
from pathlib import Path
import time

from PIL import Image

from unicorn.arm_const import UC_ARM_REG_R0,UC_ARM_REG_PC,UC_ARM_REG_SP,UC_ARM_REG_LR,UC_ARM_REG_CPSR
from emulator.board import Board,ROOT
from emulator.debugger import Debugger
from emulator.snapshot import load_snapshot,save_snapshot
from .build_key_marker import DEFAULT_DIRECTORY,ENTRY,HOOK,RESUME,MARKER,build


def registers(board):
    values = {f'r{i}':board.uc.reg_read(UC_ARM_REG_R0+i) for i in range(13)}
    values.update({name:board.uc.reg_read(reg) for name,reg in
                   (('pc',UC_ARM_REG_PC),('sp',UC_ARM_REG_SP),('lr',UC_ARM_REG_LR),('cpsr',UC_ARM_REG_CPSR))})
    return values


def execute(board,instructions=200000000,seconds=180):
    entry = board.uc.reg_read(UC_ARM_REG_PC)|(1 if board.uc.reg_read(UC_ARM_REG_CPSR)&0x20 else 0)
    return board.run(instructions,seconds,entry=entry,report=False)


def require_stop(result,reason):
    if result['engine_error'] or result['stop']['reason']!=reason:
        raise ValueError(f'Unexpected CPU stop: {result}')


def compare_reports(directory):
    reports = [json.loads((directory/f'proof-{name}'/'proof.json').read_text(encoding='utf-8'))
               for name in ('original','patched')]
    original,patched = reports
    if any(value.get('status')!='PASS' for value in reports) or original['patched'] or not patched['patched']:
        raise ValueError('Expected successful independent original and patched proof runs')
    checks = dict(entry_registers_equal=original['entry_registers']==patched['entry_registers'],
                  returned_registers_equal=original['returned_registers']==patched['returned_registers'],
                  patched_marker=patched['marker']['text']=='B220',
                  boot_jpeg_operations_equal=original['jpeg_operations']==patched['jpeg_operations'])
    with Image.open(original['boot_frame']) as a,Image.open(patched['boot_frame']) as b:
        checks['boot_pixels_equal'] = a.size==b.size and a.tobytes()==b.tobytes()
    if not all(checks.values()):
        raise ValueError(f'Independent control comparison failed: {checks}')
    result = dict(status='PASS',checks=checks,original_sha256=original['firmware']['sha256'],
                  patched_sha256=patched['firmware']['sha256'],marker=patched['marker'],
                  note='Post-return screenshots use wall-time execution slices; cursor phase may differ')
    (directory/'comparison.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    return result


def prove(board,directory,manifest,patched):
    directory.mkdir(parents=True,exist_ok=True)
    print('Booting selected PS through five LCD frames...',flush=True)
    board.stop_after_frames = 5
    require_stop(execute(board),'requested LCD frame count')
    board.stop_after_frames = 0
    boot_frame = board.lcd.save(directory,'boot')
    target = board.block_serial+512000
    deadline = time.monotonic()+30
    while board.block_serial<target:
        if time.monotonic()>deadline:
            raise ValueError('Boot input checkpoint did not settle')
        require_stop(execute(board,200000000,.5),'wall time budget')
    save_snapshot(board,directory/'before-key.b220state')
    d = Debugger(board,[ENTRY])
    try:
        board.keypad.key('soft_left',True)
        board.keypad.key('soft_left',False)
        print('Waiting for the native keypad callback (physical KPD/IRQ input)...',flush=True)
        require_stop(d.run(200000000,90),'breakpoint')
        before = registers(board)
        if before['pc']!=ENTRY or not before['cpsr']&0x20 or before['cpsr']&0x1f==0x12:
            raise ValueError('Key callback is not running in the expected non-IRQ Thumb context')
        report = dict(firmware=board.firmware,entry_registers=before,key='soft_left',patched=patched,
                      boot_frame=boot_frame,marker=None,host_cpu_or_ram_writes=False)
        save_snapshot(board,directory/'callback-entry.b220state')
        return_address = before['lr']&~1
        if patched:
            checkpoint = int(manifest['marker_checkpoint'],0)
            d.replace([checkpoint])
            require_stop(d.run(1000,5),'breakpoint')
            marked = registers(board)
            data = bytes(board.uc.mem_read(marked['sp'],4))
            if marked['pc']!=checkpoint or marked['sp']!=before['sp']-16 or data!=MARKER:
                raise ValueError('Native payload did not write B220 into its allocated stack local')
            saved_lr = int.from_bytes(board.uc.mem_read(marked['sp']+12,4),'big')
            if saved_lr!=before['lr']:
                raise ValueError('Saved native caller LR was damaged')
            report['marker'] = dict(address=hex(marked['sp']),bytes=data.hex(),text=data.decode(),
                                    checkpoint=hex(checkpoint),saved_caller_lr=hex(saved_lr),registers=marked)
            save_snapshot(board,directory/'marker.b220state')
            d.replace([RESUME])
            require_stop(d.run(1000,5),'breakpoint')
            resumed = registers(board)
            if resumed['r0'] or resumed['r5']!=(before['r1']<<16)&0xffffffff:
                raise ValueError('Displaced instructions did not produce the native R0/R5 values')
            if resumed['sp']!=before['sp']-16:
                raise ValueError('Native frame was not preserved on resume')
            report['resumed_registers'] = resumed
            d.replace([0x1a1246])
            require_stop(d.run(1000,5),'breakpoint')
            if bytes(board.uc.mem_read(resumed['sp'],4))!=bytes(4):
                raise ValueError('Native STR-zero did not reclaim the temporary marker local')
        d.replace([return_address])
        require_stop(d.run(20000000,30),'breakpoint')
        after = registers(board)
        if after['pc']!=return_address or after['sp']!=before['sp']:
            raise ValueError('Native callback failed to return with its original SP')
        for name in ('r4','r5','r6','r7','r8','r9','r10','r11'):
            if after[name]!=before[name]:
                raise ValueError(f'Native callee-saved register changed: {name}')
        if (after['cpsr']&0xff)!=(before['cpsr']&0xff):
            raise ValueError('Native callback returned in a different CPU mode')
        report['returned_registers'] = after
        report['key_events'] = board.keypad.events
        save_snapshot(board,directory/'returned.b220state')
        d.replace([])
        frames = board.full_lcd_frames
        result = d.run(20000000,12)
        if result['engine_error'] or result['stop']['reason'] not in ('instruction budget','wall time budget'):
            raise ValueError(f'Firmware stopped after returning: {result}')
        if board.full_lcd_frames<=frames:
            raise ValueError('Firmware did not redraw the screen after returning')
        report.update(status='PASS',after_frame=board.lcd.save(directory,'after-key'),
                      lcd_frames_after_return=board.full_lcd_frames-frames,
                      jpeg_operations=board.jpeg.operations if board.jpeg else [],
                      ps_visible_sha256=hashlib.sha256(board.uc.mem_read(0,board.flash.source_length)).hexdigest())
        if report['ps_visible_sha256']!=board.image_sha256:
            raise ValueError('CPU-visible PS changed during execution')
        save_snapshot(board,directory/'working.b220state')
        (directory/'proof.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        return report
    finally:
        d.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--original',action='store_true',help='Independent unmodified control run')
    p.add_argument('--directory',type=Path)
    p.add_argument('--load-state',type=Path,help='Optional validated state before the fifth boot frame, same PS hash')
    p.add_argument('--compare',action='store_true',help='Compare the two existing independent proof reports')
    args = p.parse_args()
    if args.compare:
        try:
            comparison = compare_reports(args.directory or DEFAULT_DIRECTORY)
        except (ValueError,OSError) as exc:
            p.error(str(exc))
        print(json.dumps(dict(status=comparison['status'],checks=comparison['checks']),indent=2))
        return
    original = next((ROOT/'analysis/extracted').glob('SC*.bin')).read_bytes()
    expected,manifest = build(original)
    firmware = None if args.original else DEFAULT_DIRECTORY/'key-marker.bin'
    if firmware and firmware.read_bytes()!=expected:
        p.error('Rebuild key-marker.bin with tools.build_key_marker before verification')
    directory = args.directory or DEFAULT_DIRECTORY/('proof-original' if args.original else 'proof-patched')
    try:
        board = load_snapshot(args.load_state,firmware_path=firmware) if args.load_state else Board(explore=True,firmware_path=firmware)
        if board.full_lcd_frames>=5:
            raise ValueError('Expected a fresh boot or a snapshot before the fifth LCD frame')
        report = prove(board,directory,manifest,not args.original)
    except (ValueError,OSError) as exc:
        p.error(str(exc))
    print(json.dumps({key:report[key] for key in ('status','patched','marker','lcd_frames_after_return','after_frame')},indent=2))


if __name__=='__main__':
    main()
