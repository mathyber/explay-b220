"""Local research checkpoints and CPU profiles; run as python -m tools.research_runtime.

Checkpoints contain Python/Unicorn internal objects and are only for files made
by this tool in this workspace, with the same Python and Unicorn versions.
"""
import argparse
import cProfile
import gzip
import json
import pickle
import pstats
from pathlib import Path

from unicorn.arm_const import UC_ARM_REG_PC, UC_ARM_REG_CPSR, UC_ARM_REG_R0
from emulator.board import Board, ROOT
from emulator.report import write_html


def capture(board,path):
    board._restore_flash_overlays()
    excluded = {'uc','ram','md','frame_callback','profile','mmio_windows'}
    state = {k:v for k,v in vars(board).items() if k not in excluded}
    memory = [(start,end,permissions,bytes(board.uc.mem_read(start,end-start+1)))
              for start,end,permissions in board.uc.mem_regions()]
    payload = dict(version=1,ps_sha256=board.image_sha256,state=state,memory=memory,
                   cpu=board.uc.context_save())
    path.parent.mkdir(parents=True,exist_ok=True)
    with gzip.open(path,'wb',compresslevel=1) as stream:
        pickle.dump(payload,stream,protocol=5)


def restore(path,profile=None):
    with gzip.open(path,'rb') as stream:
        payload = pickle.load(stream)
    board = Board(profile=profile,explore=True)
    if payload['version'] != 1 or payload['ps_sha256'] != board.image_sha256:
        raise ValueError('Incompatible research checkpoint')
    existing = list(board.uc.mem_regions())
    for start,end,permissions,data in payload['memory']:
        if not any(lo <= start and end <= hi for lo,hi,_ in existing):
            board.uc.mem_map(start,end-start+1,permissions)
        board.uc.mem_write(start,data)
    vars(board).update(payload['state'])
    if board.keypad and not hasattr(board.keypad,'power_raw'):
        board.keypad.power_raw = 0
        board.keypad.power_down = False
    if board.keypad and 'keypad' not in payload['state']:
        for offset in (0,4,0x14,0x18,0x1c,0x20,0x24,0x28):
            board.keypad.registers[offset] = board.storage.get(board.keypad.base+offset,0)
    if board.lcd and 'full_lcd_frames' not in payload['state']:
        board.full_lcd_frames,board._lcd_frame_pixels = divmod(board.lcd.pixel_writes,board.lcd.width*board.lcd.height)
    if board.irq:
        board._sync_timer_events()
    board.uc.context_restore(payload['cpu'])
    return board


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--restore',type=Path)
    parser.add_argument('--capture',type=Path)
    parser.add_argument('--load-flash',type=Path)
    parser.add_argument('--profile',type=Path)
    parser.add_argument('--until',type=lambda v:int(v,0),default=0xffffffff)
    parser.add_argument('--seconds',type=float,default=180)
    parser.add_argument('--instructions',type=int,default=200000000)
    parser.add_argument('--out',type=Path,default=ROOT/'analysis/emulator/research.json')
    parser.add_argument('--cpu-profile',type=Path)
    parser.add_argument('--reference-cpu',action='store_true')
    parser.add_argument('--frames',type=int,default=0)
    args = parser.parse_args()
    profile = json.loads(args.profile.read_text(encoding='utf-8')) if args.profile else None
    board = restore(args.restore,profile) if args.restore else Board(profile=profile,explore=True,
                    flash_state=args.load_flash.read_bytes() if args.load_flash else None)
    if args.reference_cpu:
        board.fast_cpu = False
    board.stop_after_frames = args.frames
    entry = board.uc.reg_read(UC_ARM_REG_PC)
    if board.uc.reg_read(UC_ARM_REG_CPSR)&0x20:
        entry |= 1
    profiler = cProfile.Profile() if args.cpu_profile else None
    if profiler:
        profiler.enable()
    result = board.run(args.instructions,args.seconds,entry=entry,until=args.until)
    if profiler:
        profiler.disable()
        args.cpu_profile.parent.mkdir(parents=True,exist_ok=True)
        profiler.dump_stats(args.cpu_profile)
        pstats.Stats(profiler).strip_dirs().sort_stats('tottime').print_stats(24)
    result['cpu_registers'] = {f'r{i}':hex(board.uc.reg_read(UC_ARM_REG_R0+i)) for i in range(13)}
    if args.capture:
        capture(board,args.capture)
        result['research_checkpoint'] = str(args.capture)
    if board.lcd:
        result['lcd_image'] = board.lcd.save(args.out.parent,args.out.stem+'-lcd')
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    write_html(result,args.out.with_suffix('.html'))
    print(json.dumps({k:result[k] for k in ('stop','registers','cpu_registers','elapsed_seconds')},indent=2))


if __name__ == '__main__':
    main()
