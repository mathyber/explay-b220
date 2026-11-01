r"""Run with .venv\Scripts\python.exe -m emulator.run --explore."""
import argparse
import json
import os
from pathlib import Path
from .board import Board, ROOT
from .report import write_html
from .frames import LiveFramePublisher


def save_outputs(board,result,args,publish_frame,save_flash=True):
    if publish_frame:
        result['live_frame_output'] = publish_frame.report()
    if args.save_flash and save_flash:
        args.save_flash.parent.mkdir(parents=True,exist_ok=True)
        temporary = args.save_flash.with_name(f'.{args.save_flash.name}-{os.getpid()}.tmp')
        temporary.write_bytes(board.flash.array)
        temporary.replace(args.save_flash)
        result['saved_flash'] = str(args.save_flash)
    if board.dsp and board.dsp.image:
        payload_path = args.out.parent/(args.out.stem+'-dsp-bootstrap.bin')
        payload_path.write_bytes(board.dsp.image)
        result['dsp_bootstrap']['image_path'] = str(payload_path)
    if board.lcd:
        result['lcd_image'] = board.lcd.save(args.out.parent,args.out.stem+'-lcd')
    args.out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    write_html(result,args.out.with_suffix('.html'))
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--profile',type=Path,default=ROOT/'emulator/profiles/b220-l7a.json')
    p.add_argument('--explore',action='store_true',help='Explicitly permit unknown zero-backed MMIO storage')
    p.add_argument('--instructions',type=int,default=12000000)
    p.add_argument('--seconds',type=float,default=20)
    p.add_argument('--until',type=lambda v:int(v,0),default=0xffffffff,help='Stop before this guest PC, e.g. 0x526912')
    p.add_argument('--out',type=Path,default=ROOT/'analysis/emulator/latest.json')
    p.add_argument('--load-flash',type=Path,help='Resume saved virtual NOR; PS prefix must match exactly')
    p.add_argument('--save-flash',type=Path,help='Save virtual NOR after this run; original PS is never written')
    p.add_argument('--live-frame',type=Path,help='Atomically update this PNG after each LCD DMA transfer')
    p.add_argument('--full-nor-trace',action='store_true',help='Keep every NOR command instead of the first/last 4096')
    p.add_argument('--frames',type=int,default=0,help='Stop after this many full LCD updates; 0 means unlimited')
    p.add_argument('--interactive',action='store_true',help='Keep CPU/RAM alive and accept phone keys after the first screen')
    p.add_argument('--commands',type=Path,help='Per-session JSONL input file')
    p.add_argument('--session-status',type=Path,help='Per-session atomic status JSON')
    a = p.parse_args()
    if a.instructions <= 0 or a.frames < 0 or not 0 < a.seconds < float('inf') or not 0 <= a.until <= 0xffffffff:
        p.error('Budgets must be positive/finite and --until must be a 32-bit address')
    if a.interactive and (not a.commands or not a.session_status):
        p.error('--interactive requires --commands and --session-status')
    a.out.parent.mkdir(parents=True,exist_ok=True)
    publish_frame = LiveFramePublisher(a.live_frame) if a.live_frame else None
    board = Board(json.loads(a.profile.read_text(encoding='utf-8')),explore=a.explore,
                  flash_state=a.load_flash.read_bytes() if a.load_flash else None,
                  frame_callback=publish_frame,full_nor_trace=a.full_nor_trace,stop_after_frames=a.frames)
    if a.interactive:
        from .interactive import InteractiveSession
        session = InteractiveSession(board,a,lambda result,save:save_outputs(board,result,a,publish_frame,save))
        result = session.run()
        save_outputs(board,result,a,publish_frame,save_flash=session.ready)
    else:
        result = board.run(a.instructions,a.seconds,until=a.until)
        save_outputs(board,result,a,publish_frame)
    summary = {k:result[k] for k in ('stop','registers','normalized_chip','elapsed_seconds','engine_error')}
    summary['hot_blocks'] = result['hot_blocks'][:5]
    summary['recent_logs'] = [v['format'] for v in result['guest_logs'][-12:]]
    summary['nor_events'] = result['nor_event_count']
    summary['lcd'] = {k:result['lcd'][k] for k in ('device_id','pixel_writes','register_writes')} if result['lcd'] else None
    print(json.dumps(summary,ensure_ascii=True,indent=2))


if __name__ == '__main__':
    main()
