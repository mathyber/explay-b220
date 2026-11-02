"""Observe the patched PS tail-call stock text rendering and produce a real LCD frame."""
import argparse
import json
from pathlib import Path
import time

from unicorn import UC_HOOK_MEM_READ,UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_PC,UC_ARM_REG_SP
from emulator.board import Board,ROOT
from emulator.debugger import Debugger
from emulator.snapshot import load_snapshot,save_snapshot
from .build_ui_label import build,DEFAULT_DIRECTORY,HOOK,NATIVE_DRAW,ORIGINAL_TEXT,TEXT
from .prove_key_marker import execute,registers,require_stop


def settle_display(board,directory):
    """A frame-count boundary can interrupt native paint cleanup; finish it."""
    result = execute(board,20000000,3)
    if result['engine_error'] or result['stop']['reason'] not in ('instruction budget','wall time budget'):
        raise ValueError(f'Firmware stopped while completing the paint: {result}')
    frame = board.lcd.save(directory,'settled-title')
    state = directory/'working-title.b220state'
    save_snapshot(board,state)
    return dict(frame=frame,state=str(state),execution=result)


def check_followup_input(board,directory):
    result = []
    for key,stem in (('up','settled-hour-up'),('soft_left','after-confirm')):
        board.keypad.key(key,True)
        board.keypad.key(key,False)
        step = execute(board,20000000,8)
        if step['engine_error'] or step['stop']['reason'] not in ('instruction budget','wall time budget'):
            raise ValueError(f'Firmware stopped after {key}: {step}')
        result.append(dict(key=key,execution=step,frame=board.lcd.save(directory,stem),
                           recent_key_events=board.keypad.events[-4:]))
    save_snapshot(board,directory/'after-confirm.b220state')
    return result


def prove(board,directory,manifest):
    directory.mkdir(parents=True,exist_ok=True)
    report = dict(firmware=board.firmware,host_cpu_or_ram_writes=False,text=TEXT,marker_events=[],text_reads=[])
    text_address = int(manifest['string_address'],0)
    def text_read(uc,access,address,size,value,_):
        if len(report['text_reads'])<256:
            report['text_reads'].append(dict(address=hex(address),size=size,pc=hex(uc.reg_read(UC_ARM_REG_PC))))
    def marker(uc,address,size,_):
        sp = uc.reg_read(UC_ARM_REG_SP)
        data = bytes(uc.mem_read(sp,4))
        if len(report['marker_events'])<32:
            report['marker_events'].append(dict(pc=hex(address),sp=hex(sp),bytes=data.hex()))
    read_hook = board.uc.hook_add(UC_HOOK_MEM_READ,text_read,begin=text_address,end=text_address+len(TEXT)*2-1)
    marker_hook = board.uc.hook_add(UC_HOOK_CODE,marker,begin=0x152f2e,end=0x152f2e)
    debugger = None
    try:
        print('Booting selected image to the date dialog...',flush=True)
        board.stop_after_frames = 5
        require_stop(execute(board),'requested LCD frame count')
        board.stop_after_frames = 0
        target = board.block_serial+512000
        deadline = time.monotonic()+30
        while board.block_serial<target:
            if time.monotonic()>deadline:
                raise ValueError('Input checkpoint did not settle')
            require_stop(execute(board,200000000,.5),'wall time budget')
        report['before_frame'] = board.lcd.save(directory,'before-date')
        save_snapshot(board,directory/'before-key.b220state')
        debugger = Debugger(board,[HOOK])
        board.keypad.key('soft_left',True)
        board.keypad.key('soft_left',False)
        print('Waiting for the stock time-title draw call...',flush=True)
        deadline = time.monotonic()+90
        while True:
            if time.monotonic()>deadline:
                raise ValueError('Time-title draw call was not reached')
            result = debugger.run(200000000,5)
            if result['stop']['reason']=='wall time budget':
                continue
            require_stop(result,'breakpoint')
            before = registers(board)
            if before['r3']==ORIGINAL_TEXT:
                break
        if int.from_bytes(board.uc.mem_read(before['sp'],4),'big')!=5 or before['cpsr']&0x1f==0x12:
            raise ValueError('Unexpected title text length or IRQ drawing context')
        stack = bytes(board.uc.mem_read(before['sp'],64))
        report['callsite_registers'] = before
        report['native_rectangle_hex'] = bytes(board.uc.mem_read(before['r0'],8)).hex()
        save_snapshot(board,directory/'before-draw.b220state')
        debugger.replace([NATIVE_DRAW])
        require_stop(debugger.run(1000,5),'breakpoint')
        native = registers(board)
        if native['r3']!=text_address or int.from_bytes(board.uc.mem_read(native['sp'],4),'big')!=len(TEXT):
            raise ValueError('Wrapper did not forward the replacement UTF-16 string/count')
        if native['sp']!=before['sp'] or native['lr']!=HOOK+5:
            raise ValueError('Wrapper changed the native SP/return address')
        if bytes(board.uc.mem_read(native['sp']+4,60))!=stack[4:]:
            raise ValueError('Wrapper damaged native stack arguments')
        for name in ('r0','r1','r2','r4','r5','r6','r7','r8','r9','r10','r11'):
            if native[name]!=before[name]:
                raise ValueError(f'Wrapper changed preserved argument/register {name}')
        report['native_draw_registers'] = native
        save_snapshot(board,directory/'native-draw.b220state')
        debugger.replace([HOOK+4])
        require_stop(debugger.run(20000000,30),'breakpoint')
        returned = registers(board)
        if returned['sp']!=before['sp']:
            raise ValueError('Stock renderer did not restore SP')
        for name in ('r4','r5','r6','r7','r8','r9','r10','r11'):
            if returned[name]!=before[name]:
                raise ValueError(f'Stock renderer failed to restore {name}')
        expected = {text_address+2*i for i in range(len(TEXT))}
        observed = {int(value['address'],0) for value in report['text_reads']}
        if not expected.issubset(observed):
            raise ValueError('Native renderer did not read every replacement UTF-16 code unit')
        report['returned_registers'] = returned
        debugger.replace([])
        frames = board.full_lcd_frames
        board.stop_after_frames = frames+1
        require_stop(debugger.run(200000000,60),'requested LCD frame count')
        board.stop_after_frames = 0
        report['visible_frame'] = board.lcd.save(directory,'visible-title')
        report['full_lcd_frames'] = board.full_lcd_frames
        save_snapshot(board,directory/'visible.b220state')
        report['settled_display'] = settle_display(board,directory)
        report['followup_input'] = check_followup_input(board,directory)
        if not report['marker_events'] or any(v['bytes']!='42323230' for v in report['marker_events']):
            raise ValueError('Previous native key-marker patch is not working alongside graphics')
        report['status'] = 'PASS'
        (directory/'proof.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        return report
    finally:
        if debugger:
            debugger.close()
        board.uc.hook_del(read_hook)
        board.uc.hook_del(marker_hook)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--load-state',type=Path,help='Validated state of this same image before the fifth LCD frame')
    p.add_argument('--directory',type=Path,default=DEFAULT_DIRECTORY/'proof')
    args = p.parse_args()
    try:
        image = DEFAULT_DIRECTORY/'b220-mod.bin'
        expected,manifest = build(next((ROOT/'analysis/extracted').glob('SC*.bin')).read_bytes())
        if image.read_bytes()!=expected:
            raise ValueError('Rebuild the UI image before verification')
        board = load_snapshot(args.load_state,firmware_path=image) if args.load_state else Board(explore=True,firmware_path=image)
        if board.full_lcd_frames>=5:
            raise ValueError('Expected a state before the fifth boot frame')
        result = prove(board,args.directory,manifest)
    except (OSError,ValueError) as exc:
        p.error(str(exc))
    print(json.dumps(dict(status=result['status'],text=TEXT,visible_frame=result['visible_frame'],
                         native_draw=hex(NATIVE_DRAW),native_text_reads=len(result['text_reads']),
                         key_marker_hits=len(result['marker_events'])),indent=2))


if __name__=='__main__':
    main()
