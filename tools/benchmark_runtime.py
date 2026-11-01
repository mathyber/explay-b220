"""Compare reference and optimized execution at the same firmware checkpoint."""
import gc
import hashlib
import json
from pathlib import Path

from unicorn.arm_const import UC_ARM_REG_R0,UC_ARM_REG_PC,UC_ARM_REG_SP,UC_ARM_REG_LR,UC_ARM_REG_CPSR
from emulator.board import Board,ROOT


def measure(fast):
    board = Board(explore=True,fast_cpu=fast)
    try:
        result = board.run(30000000,120,until=0x17e8fc)
        if result['stop']['reason'] != 'requested end address' or result['engine_error']:
            raise RuntimeError(result['stop'])
        digest = lambda data:hashlib.sha256(data).hexdigest()
        registers = {f'r{i}':board.uc.reg_read(UC_ARM_REG_R0+i) for i in range(13)}
        for name,reg in [('pc',UC_ARM_REG_PC),('sp',UC_ARM_REG_SP),('lr',UC_ARM_REG_LR),('cpsr',UC_ARM_REG_CPSR)]:
            registers[name] = board.uc.reg_read(reg)
        state = dict(registers=registers,ram=digest(board.uc.mem_read(0x04000000,0x400000)),
                     sram=digest(board.uc.mem_read(0x40000000,0x10000)),flash=digest(board.flash.array),
                     lcd=digest(b''.join(v.to_bytes(2,'big') for v in board.lcd.gram)),
                     translated_blocks=board.block_serial,irq_entries=result['irq_entries'],
                     timer_state=result['countdown_timers'],guest_logs=result['guest_logs'])
        return dict(elapsed_seconds=result['elapsed_seconds'],state=state)
    finally:
        del board
        gc.collect()


def main():
    reference = measure(False)
    optimized = measure(True)
    identical = reference['state'] == optimized['state']
    comparison = dict(checkpoint='0x17e8fc',reference=reference,optimized=optimized,
                      identical_guest_state=identical,
                      speedup=round(reference['elapsed_seconds']/optimized['elapsed_seconds'],3))
    output = ROOT/'analysis/emulator/performance/comparison.json'
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(comparison,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in comparison.items() if k not in ('reference','optimized')},indent=2))
    print('Seconds:',reference['elapsed_seconds'],'->',optimized['elapsed_seconds'])
    if not identical:
        print('Differing fields:',[k for k in reference['state'] if reference['state'][k] != optimized['state'][k]])
        raise SystemExit(1)


if __name__ == '__main__':
    main()
