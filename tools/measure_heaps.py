"""Measure PS memory pools and heaps in the emulator; run as python -m tools.measure_heaps.

Cold-boots the SD-app PS to Games -> SD Apps (or loads --load-state), stops in the
MMI task at the native fill call and calls the firmware allocator 0x6a5439 directly.
Each failed request is rolled back: on a large request with an unknown heap type
the firmware asserts and restarts. The PS, NOR and card images are not modified.

Allocator: alloc(size, heap_type, file, line). Requests up to ~1.6 KB come from
fixed-size pools; larger ones use heap_type (see HEAPS). The emulator has no
DSP/Bluetooth/modem services, so a real phone may have less free memory.
"""
import argparse
import json
import struct
import time
from pathlib import Path

from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_SP, UC_ARM_REG_LR

from emulator.board import Board, ROOT
from emulator.debugger import Debugger
from emulator.snapshot import load_snapshot, save_snapshot
from .b22asm import assemble
from .build_sd_app import DIRECTORY as SD_APP
from .prove_catch_game import run_for
from .prove_key_marker import execute
from .sd_image import Fat16, create_image

DIRECTORY = ROOT/'analysis/memory'
ALLOC, FREE = 0x6a5439, 0x6a5469
MMI_FILL = 0x4c65ac
SENTINEL = 0x1000
HEAPS = {'static':0x22222222, 'base':0x33333333, 'app':0x44444444}
HEAP_CONTROL, HEAP_STRIDE = 0x42c4cc0, 0x48
POOLS, POOL_STRIDE, POOL_COUNT = 0x42b4a5c, 0x3c, 0x3b4a68
ROLLBACK = ((0x04000000,0x400000), (0x40000000,0x10000), (0x10000000,0x1000))
ROUTE = ['soft_left','soft_left','soft_left','down','ok','up','up','ok','ok']


def boot_to_sd_apps(firmware, snapshot):
    volume = Fat16(create_image())
    volume.add_file('DEMO.B22', assemble((ROOT/'apps/card_demo.b22asm').read_text(encoding='utf-8')))
    volume.add_file('APPS.LST', b'DEMO.B22\r\n')
    board = Board(explore=True, firmware_path=firmware, sd_image=volume.image)
    board.stop_after_frames = 5
    deadline = time.monotonic()+300
    while board.full_lcd_frames < 5:
        if time.monotonic() > deadline:
            raise ValueError('Cold boot timed out')
        result = execute(board, 200000000, 5)
        if result['engine_error'] or result['stop']['reason'] not in ('wall time budget','instruction budget','requested LCD frame count'):
            raise ValueError(f'Unexpected boot stop: {result}')
    board.stop_after_frames = 0
    target = board.block_serial+512000
    while board.block_serial < target:
        run_for(board, .5)
    run_for(board, 3)
    for key in ROUTE:
        board.keypad.key(key, True); board.keypad.key(key, False)
        run_for(board, 6)
    run_for(board, 4)
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    board.lcd.save(snapshot.parent, snapshot.stem)
    save_snapshot(board, snapshot)
    return board


class Allocator:
    """Injected calls from a saved MMI-task context; host writes only the tag string and rollbacks."""
    def __init__(self, board):
        self.board, self.uc = board, board.uc
        debugger = Debugger(board, [MMI_FILL])
        board.keypad.key('down', True); board.keypad.key('down', False)
        result = debugger.run(80000000, 600)
        if result['stop']['reason'] != 'breakpoint':
            raise ValueError(f'MMI task was not reached: {result["stop"]}')
        debugger.replace([])
        self.tag = self.uc.reg_read(UC_ARM_REG_SP)-0x400
        self.uc.mem_write(self.tag, b'HEAPPROBE\0')
        self.context = self.uc.context_save()
        self.calls = self.failures = 0

    def u32(self, address):
        return struct.unpack('>I', bytes(self.uc.mem_read(address, 4)))[0]

    def call(self, function, *args):
        self.calls += 1
        self.uc.context_restore(self.context)
        self.uc.reg_write(UC_ARM_REG_SP, (self.tag-0x40)&~7)
        for index, value in enumerate(args):
            self.uc.reg_write(UC_ARM_REG_R0+index, value)
        self.uc.reg_write(UC_ARM_REG_LR, SENTINEL|1)
        result = self.board.run(5000000, 60, entry=function, until=SENTINEL, report=False)
        value = self.uc.reg_read(UC_ARM_REG_R0)
        self.uc.context_restore(self.context)
        return result['stop']['reason'] == 'requested end address', value

    def alloc(self, size, heap):
        saved = [bytes(self.uc.mem_read(a, n)) for a, n in ROLLBACK]
        ok, pointer = self.call(ALLOC, size, heap, self.tag, 0)
        if ok and pointer:
            return pointer
        self.failures += 1
        for (address, _), data in zip(ROLLBACK, saved):
            self.uc.mem_write(address, data)
        return 0

    def free(self, pointer):
        if not self.call(FREE, pointer)[0]:
            raise ValueError(f'Free failed for {pointer:#x}')

    def largest(self, heap, high=4<<20, step=512):
        low = 0
        while high-low > step:
            middle = (low+high)//2
            pointer = self.alloc(middle, heap)
            if pointer:
                self.free(pointer); low = middle
            else:
                high = middle
        return low


def measure(allocator):
    u32 = allocator.u32
    pools = []
    for index in range(u32(POOL_COUNT)):
        base = POOLS+index*POOL_STRIDE
        pools.append(dict(block=u32(base+0x1c), free=u32(base+8), total=u32(base+12), area=u32(base+0x18)))
    report = dict(pools=pools, pool_free_bytes=sum(p['block']*p['free'] for p in pools),
                  pool_area_bytes=sum(p['area'] for p in pools), heaps={})
    for index, (name, heap) in enumerate(HEAPS.items()):
        control = HEAP_CONTROL+index*HEAP_STRIDE
        largest = allocator.largest(heap)
        held, high = [], largest+1024
        while len(held) < 40:
            size = allocator.largest(heap, high)
            if size < 8192:
                break
            held.append((allocator.alloc(size, heap), size)); high = size+1024
        for pointer, _ in held:
            allocator.free(pointer)
        report['heaps'][name] = dict(type=hex(heap), start=hex(u32(control+0x10)), size=u32(control+0x1c),
                                     free_counter=u32(control+8), largest_block=largest,
                                     greedy_total=sum(size for _, size in held))
        print(name, report['heaps'][name], flush=True)
    report.update(calls=allocator.calls, rolled_back_failures=allocator.failures)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--firmware', type=Path, default=SD_APP/'sd-app.bin')
    parser.add_argument('--load-state', type=Path, help='Snapshot of this firmware in an MMI window that redraws on DOWN')
    parser.add_argument('--directory', type=Path, default=DIRECTORY)
    args = parser.parse_args()
    if not args.firmware.exists():
        parser.error('Build the SD-app PS first: python -m tools.build_sd_app')
    if args.load_state:
        board = load_snapshot(args.load_state, firmware_path=args.firmware)
    else:
        print('Cold boot to SD Apps...', flush=True)
        board = boot_to_sd_apps(args.firmware, args.directory/'sd-apps.b220state')
    report = dict(firmware=board.firmware, state=str(args.load_state or args.directory/'sd-apps.b220state'),
                  **measure(Allocator(board)))
    args.directory.mkdir(parents=True, exist_ok=True)
    (args.directory/'heaps.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('pools','firmware')}, indent=1))


if __name__ == '__main__':
    main()
