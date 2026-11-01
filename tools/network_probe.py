"""Replay phone keys from an official snapshot and collect network observations.

Example: python -m tools.network_probe --load-state home.b220state
         --steps soft_left,right,right,ok,wait,down,ok --out analysis/probe
Each step runs a finite CPU budget; 'wait' only advances the CPU.
This tool never saves over the input snapshot or writes the source firmware.
"""
import argparse
import json
from pathlib import Path

from unicorn.arm_const import UC_ARM_REG_PC,UC_ARM_REG_CPSR
from emulator.keypad import KEY_MATRIX
from emulator.network_trace import NetworkTrace
from emulator.snapshot import load_snapshot,save_snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--load-state',type=Path,required=True)
    parser.add_argument('--steps',required=True,help='Comma-separated phone keys or wait; no automatic wake key')
    parser.add_argument('--out',type=Path,required=True,help='New, empty output directory')
    parser.add_argument('--instructions',type=int,default=8000000,help='CPU budget per step')
    parser.add_argument('--seconds',type=float,default=45,help='Wall time limit per step')
    parser.add_argument('--virtual-network',action='store_true',help='Experimental SIM1/PDP/HTTP setup; stop before DNS; no host network and no output snapshots')
    args = parser.parse_args()
    steps = args.steps.split(',')
    if any(key not in (*KEY_MATRIX,'end','wait') for key in steps):
        parser.error('Unknown step; use a phone key name or wait')
    if args.instructions <= 0 or not 0 < args.seconds < float('inf'):
        parser.error('Budgets must be positive and finite')
    if args.out.exists() and (not args.out.is_dir() or any(args.out.iterdir())):
        parser.error('--out must be a new or empty directory')
    board = load_snapshot(args.load_state)
    args.out.mkdir(parents=True,exist_ok=True)
    trace = NetworkTrace(board)
    network = None
    try:
        if args.virtual_network:
            from emulator.virtual_network import VirtualNetwork
            try:
                network = VirtualNetwork(board)
            except ValueError as exc:
                parser.error(str(exc))
            trace.mark('virtual-network-enabled')
        for index,key in enumerate(steps,1):
            name = f'{index:02d}-{key}'
            trace.mark(name)
            if key != 'wait':
                board.keypad.key(key,True)
                board.keypad.key(key,False)
            pc = board.uc.reg_read(UC_ARM_REG_PC)
            entry = pc | (1 if board.uc.reg_read(UC_ARM_REG_CPSR)&0x20 else 0)
            result = board.run(args.instructions,args.seconds,entry=entry,report=False)
            board.lcd.save(args.out,name)
            if not network:
                save_snapshot(board,args.out/(name+'.b220state'))
            else:
                (args.out/'virtual-network.json').write_text(json.dumps(network.report(),indent=2),encoding='utf-8')
            trace.write(args.out/'network-trace.json')
            print(json.dumps(dict(step=name,stop=result['stop'],backlight=board.lcd_backlight,
                                  counts=dict(trace.counts))),flush=True)
            if result['stop']['reason'] not in ('instruction budget','wall time budget'):
                break
    finally:
        if network:
            network.close()
        trace.close()


if __name__ == '__main__':
    main()
