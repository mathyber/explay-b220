"""Cold-boot the native game, navigate with KPD input and check its RAM/LCD.

Observers only read CPU state. No guest code, RAM or registers are injected.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import time

from unicorn import UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3
from emulator.board import Board
from emulator.snapshot import load_snapshot, save_snapshot
from .build_catch_game import DIRECTORY, ROM, STATE, build, elf_payload
from .prove_key_marker import execute


def game_state(board):
    fields = struct.unpack('>4I8B2I', bytes(board.uc.mem_read(STATE, 32)))
    return dict(zip(('magic', 'score', 'moves', 'seed', 'x', 'y', 'target_x', 'target_y',
                     'hundreds', 'tens', 'ones', 'reserved', 'window', 'paints'), fields))


def run_for(board, seconds):
    deadline = time.monotonic()+seconds
    while time.monotonic()<deadline:
        result = execute(board, 200000000, min(.5, deadline-time.monotonic()))
        if result['engine_error'] or result['stop']['reason'] not in ('wall time budget', 'instruction budget'):
            raise ValueError(f'Unexpected firmware stop: {result}')


def prove(board, directory, keys=None, boot=True):
    directory.mkdir(parents=True, exist_ok=True)
    report = dict(status='RUNNING', firmware=board.firmware, host_cpu_or_ram_writes=False,
                  steps=[], dispatch=[], native_entries=0)
    def observe(uc, pc, size, _):
        item = dict(window=hex(uc.reg_read(UC_ARM_REG_R0)), message=hex(uc.reg_read(UC_ARM_REG_R1)),
                    param=hex(uc.reg_read(UC_ARM_REG_R2)), handler=hex(uc.reg_read(UC_ARM_REG_R3)))
        if not report['dispatch'] or report['dispatch'][-1]!=item:
            report['dispatch'].append(item)
    def entry(uc, pc, size, _):
        report['native_entries'] += 1
    hooks = [board.uc.hook_add(UC_HOOK_CODE, observe, begin=p, end=p) for p in (0x4d6dda, 0x4d6dea)]
    hooks.append(board.uc.hook_add(UC_HOOK_CODE, entry, begin=ROM, end=ROM))
    def checkpoint(stem):
        frame = board.lcd.save(directory, stem)
        save_snapshot(board, directory/(stem+'.b220state'))
        return frame
    try:
        if boot:
            print('Cold boot: waiting for the date dialog...', flush=True)
            board.stop_after_frames = 5
            deadline = time.monotonic()+300
            while board.full_lcd_frames<5:
                if time.monotonic()>deadline:
                    raise ValueError('Cold boot did not reach the date dialog in 300 seconds')
                result = execute(board, 200000000, 5)
                if result['engine_error'] or result['stop']['reason'] not in ('wall time budget', 'instruction budget', 'requested LCD frame count'):
                    raise ValueError(f'Boot stopped: {result}')
            board.stop_after_frames = 0
            target = board.block_serial+512000
            while board.block_serial<target:
                run_for(board, .5)
            run_for(board, 3)
            checkpoint('boot')
        route = ['soft_left', 'soft_left', 'soft_left', 'down', 'ok',
                 'up', 'up', 'ok', 'ok', 'ok']
        exercise = ['right', 'right', 'up', 'left', 'ok', '2', '4', '6', '8',
                    'left', 'left', 'left', 'left', 'right', 'ok', 'soft_right', 'ok']
        selected = keys if keys is not None else route+exercise
        for index, key in enumerate(selected):
            start = len(report['dispatch'])
            board.keypad.key(key, True)
            board.keypad.key(key, False)
            run_for(board, 6)
            state = game_state(board)
            stem = f'{index:02d}-{key}'
            frame = checkpoint(stem)
            step = dict(key=key, state=state, frame=frame, dispatch=report['dispatch'][start:])
            report['steps'].append(step)
            (directory/'proof.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
            print(json.dumps(dict(step=index, key=key, state=state,
                                  windows=list(dict.fromkeys(d['window'] for d in step['dispatch'])))), flush=True)
            if keys is None and index>=len(route)-1:
                n = index-len(route)+1
                expected = ({'magic':0x43415443, 'score':0, 'x':3, 'y':3},
                            {'score':0, 'x':4, 'y':3}, {'score':1, 'x':5, 'y':3},
                            {'score':1, 'x':5, 'y':2}, {'score':1, 'x':4, 'y':2},
                            {'score':0, 'moves':0, 'x':3, 'y':3},
                            {'x':3, 'y':2}, {'x':2, 'y':2}, {'x':3, 'y':2}, {'x':3, 'y':3},
                            {'x':2}, {'x':1}, {'x':0}, {'x':0, 'moves':7}, {'x':1, 'moves':8},
                            {'score':0, 'moves':0, 'x':3, 'y':3})
                if n<len(expected) and any(state[k]!=v for k,v in expected[n].items()):
                    raise ValueError(f'Native game state check failed at step {index}: {state}')
                if n==len(expected) and not any(d['window']=='0x160001' for d in step['dispatch']):
                    raise ValueError('Right softkey did not return to the stock game menu')
                if n==len(expected)+1 and (state['magic']!=0x43415443 or state['moves'] or state['x']!=3 or state['y']!=3):
                    raise ValueError('Reopening the native game did not reset its state')
        visible = bytes(board.uc.mem_read(0,board.flash.source_length))
        if hashlib.sha256(visible).hexdigest()!=board.image_sha256:
            raise ValueError('CPU-visible PS changed during execution')
        if keys is None and not report['native_entries']:
            raise ValueError('Native game entry was never executed')
        report['status'] = 'PASS' if keys is None else 'OBSERVED'
        (directory/'proof.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
        return report
    except Exception as exc:
        report.update(status='FAIL', error=str(exc))
        checkpoint('failed')
        (directory/'proof.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
        raise
    finally:
        for hook in hooks:
            board.uc.hook_del(hook)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=DIRECTORY/'proof')
    parser.add_argument('--load-state', type=Path)
    parser.add_argument('--keys', nargs='*', help='Diagnostic input sequence instead of the full proof')
    args = parser.parse_args()
    try:
        image = DIRECTORY/'catch-game.bin'
        payload, entry = elf_payload((DIRECTORY/'catch-game.elf').read_bytes())
        from emulator.board import ROOT
        manifest = json.loads((DIRECTORY/'manifest.json').read_text(encoding='utf-8'))
        for key,name in (('source_sha256','catch_game.c'), ('linker_sha256','catch_game.ld')):
            if hashlib.sha256((ROOT/'patches'/name).read_bytes()).hexdigest()!=manifest['toolchain'][key]:
                raise ValueError('Game sources changed; rebuild the image before verification')
        expected, _ = build(next((ROOT/'analysis/extracted').glob('SC*.bin')).read_bytes(), payload, entry)
        if image.read_bytes()!=expected:
            raise ValueError('Rebuild the game image before verification')
        board = load_snapshot(args.load_state, firmware_path=image) if args.load_state else Board(explore=True, firmware_path=image)
        report = prove(board, args.directory, args.keys, not args.load_state)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps(dict(status=report['status'], native_entries=report['native_entries'],
                          report=str(args.directory/'proof.json')), indent=2))


if __name__=='__main__':
    main()
