r"""Run with .venv\Scripts\python.exe -m emulator.run --explore."""
import argparse
import json
import os
from contextlib import nullcontext
from pathlib import Path
from .board import Board, ROOT
from .report import write_html
from .frames import LiveFramePublisher
from .snapshot import SnapshotError,load_snapshot,save_snapshot
from .firmware import load_firmware,experiment_directory,protect_outputs
from .debugger import Debugger,address
from unicorn.arm_const import UC_ARM_REG_PC,UC_ARM_REG_CPSR


def save_outputs(board,result,args,publish_frame,save_flash=True,card_image=None):
    if card_image:
        card_image.flush(board.spi)
        result['sd_image'] = str(card_image.path)
    if publish_frame:
        result['live_frame_output'] = publish_frame.report()
    if args.save_flash and save_flash and not hasattr(board,'experimental_network'):
        args.save_flash.parent.mkdir(parents=True,exist_ok=True)
        temporary = args.save_flash.with_name(f'.{args.save_flash.name}-{os.getpid()}.tmp')
        temporary.write_bytes(board.flash.array)
        temporary.replace(args.save_flash)
        result['saved_flash'] = str(args.save_flash)
    elif args.save_flash and save_flash:
        result['flash_save_skipped'] = 'Experimental network RAM/configuration must not persist into ordinary NOR'
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
    p.add_argument('--out',type=Path)
    p.add_argument('--firmware',type=Path,help='Separate raw PS image, same length/layout as the original; never written')
    p.add_argument('--firmware-sha256',help='Reject changes to the selected image between GUI selection and startup')
    p.add_argument('--breakpoint',type=address,action='append',default=[],help='Stop before this address; repeat up to 64 times')
    p.add_argument('--debug-start-paused',action='store_true',help='Pause before the first instruction (interactive mode)')
    inputs = p.add_mutually_exclusive_group()
    inputs.add_argument('--load-flash',type=Path,help='Resume saved virtual NOR; PS prefix must match exactly')
    inputs.add_argument('--load-state',type=Path,help='Restore a complete .b220state snapshot and continue from its PC')
    p.add_argument('--save-state',type=Path,help='Save a complete .b220state snapshot after execution stops')
    p.add_argument('--save-flash',type=Path,help='Save virtual NOR after this run; original PS is never written')
    p.add_argument('--sd-image',type=Path,help='Attach an exclusively locked 16/32 MiB SDSC image; persist guest sector writes')
    p.add_argument('--live-frame',type=Path,help='Atomically update this PNG after each LCD DMA transfer')
    p.add_argument('--full-nor-trace',action='store_true',help='Keep every NOR command instead of the first/last 4096')
    p.add_argument('--frames',type=int,default=0,help='Stop after this many full LCD updates; 0 means unlimited')
    p.add_argument('--interactive',action='store_true',help='Keep CPU/RAM alive and accept phone keys after the first screen')
    p.add_argument('--commands',type=Path,help='Per-session JSONL input file')
    p.add_argument('--session-status',type=Path,help='Per-session atomic status JSON')
    p.add_argument('--window-http-test',action='store_true',help='Use only a private loopback HTTP fixture for interactive UI validation')
    p.add_argument('--network-trace',type=Path,help='Write read-only CMS/GPRS/socket observations to this JSON; does not enable networking')
    a = p.parse_args()
    if a.instructions <= 0 or a.frames < 0 or not 0 < a.seconds < float('inf') or not 0 <= a.until <= 0xffffffff:
        p.error('Budgets must be positive/finite and --until must be a 32-bit address')
    if a.interactive and (not a.commands or not a.session_status):
        p.error('--interactive requires --commands and --session-status')
    if a.window_http_test and not a.interactive:
        p.error('--window-http-test requires --interactive')
    if a.debug_start_paused and not a.interactive:
        p.error('--debug-start-paused requires --interactive')
    if a.sd_image and a.load_state:
        p.error('SD media is embedded in snapshots; attach --sd-image on cold boot')
    if a.save_state and a.save_state.suffix.lower() != '.b220state':
        p.error('--save-state output must use the .b220state extension')
    from .card_image import CardImage
    try:
        profile = json.loads(a.profile.read_text(encoding='utf-8'))
        _,identity = load_firmware(profile,a.firmware)
        if a.firmware_sha256 and a.firmware_sha256 != identity['sha256']:
            p.error('Selected firmware changed before startup; select it again')
        if not a.out:
            a.out = (experiment_directory(identity) if a.firmware else ROOT/'analysis/emulator')/'latest.json'
        protect_outputs(profile,identity,[a.out,a.out.with_suffix('.html'),a.save_flash,a.save_state,a.sd_image,
                        a.live_frame,a.live_frame.with_suffix('.png') if a.live_frame else None,
                        a.commands,a.session_status,a.session_status.with_suffix('.network.json') if a.session_status else None,
                        a.network_trace,
                        *(a.out.parent/(a.out.stem+suffix) for suffix in ('-lcd.png','-lcd.rgb565be','-dsp-bootstrap.bin'))],inputs=[a.profile])
        with CardImage(a.sd_image) if a.sd_image else nullcontext(None) as card_image:
            run_emulator(a,p,card_image)
    except (OSError,ValueError) as exc:
        p.error(str(exc))


def run_emulator(a,p,card_image=None):
    a.out.parent.mkdir(parents=True,exist_ok=True)
    publish_frame = LiveFramePublisher(a.live_frame) if a.live_frame else None
    profile = json.loads(a.profile.read_text(encoding='utf-8'))
    try:
        if a.load_state:
            board = load_snapshot(a.load_state,profile=profile,frame_callback=publish_frame,firmware_path=a.firmware)
            if a.explore and not board.explore:
                p.error('--explore cannot override the strict mode stored in this snapshot')
            if a.full_nor_trace and board.flash.trace_limit is not None:
                p.error('--full-nor-trace cannot override the trace mode stored in this snapshot')
            board.stop_after_frames = board.full_lcd_frames+a.frames if a.frames else 0
            if publish_frame:
                publish_frame(board.lcd)
        else:
            board = Board(profile,explore=a.explore,
                          flash_state=a.load_flash.read_bytes() if a.load_flash else None,
                          frame_callback=publish_frame,full_nor_trace=a.full_nor_trace,stop_after_frames=a.frames,
                          sd_image=card_image.data if card_image else None,firmware_path=a.firmware)
    except SnapshotError as exc:
        p.error(str(exc))
    if a.firmware_sha256 and a.firmware_sha256 != board.image_sha256:
        p.error('Selected firmware changed while constructing the board')
    network_trace = None
    if a.network_trace:
        from .network_trace import NetworkTrace
        network_trace = NetworkTrace(board)
    debugger = Debugger(board,a.breakpoint)
    try:
        if a.interactive:
            from .interactive import InteractiveSession
            session = InteractiveSession(board,a,lambda result,save:save_outputs(board,result,a,publish_frame,save,card_image),
                                         flush_media=(lambda:card_image.flush(board.spi)) if card_image else None,debugger=debugger)
            result = session.run()
            save_flash = session.ready
        else:
            step = debugger.run(a.instructions,a.seconds,until=a.until)
            result = board.report(a.instructions,step['elapsed_seconds'],step['engine_error'])
            save_flash = True
        result['debugger'] = debugger.status()
    finally:
        debugger.close()
    if a.load_state:
        result['loaded_state'] = str(a.load_state)
    if a.save_state:
        try:
            result['saved_state'] = save_snapshot(board,a.save_state)
        except (SnapshotError,OSError) as exc:
            p.error(str(exc))
    save_outputs(board,result,a,publish_frame,save_flash=save_flash,card_image=card_image)
    if network_trace:
        network_trace.write(a.network_trace)
        network_trace.close()
    summary = {k:result[k] for k in ('stop','registers','normalized_chip','elapsed_seconds','engine_error')}
    summary['hot_blocks'] = result['hot_blocks'][:5]
    summary['recent_logs'] = [v['format'] for v in result['guest_logs'][-12:]]
    summary['nor_events'] = result['nor_event_count']
    if a.save_state:
        summary['saved_state'] = result['saved_state']
    summary['lcd'] = {k:result['lcd'][k] for k in ('device_id','pixel_writes','register_writes')} if result['lcd'] else None
    print(json.dumps(summary,ensure_ascii=True,indent=2))


if __name__ == '__main__':
    main()
