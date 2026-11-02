"""Host-side breakpoints and inspection; no instruction patching or memory writes."""
from unicorn import UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_R0,UC_ARM_REG_SP,UC_ARM_REG_LR,UC_ARM_REG_PC,UC_ARM_REG_CPSR


def address(value):
    if isinstance(value,str):
        value = int(value.strip(),0)
    if type(value) is not int or not 0 <= value <= 0xffffffff:
        raise ValueError('Expected a 32-bit address (for example 0x483978)')
    return value


class Debugger:
    def __init__(self,board,breakpoints=()):
        self.board = board
        self.hooks = {}
        self.hit = None
        self.skip = None
        self.stepping = False
        self.view = None
        self.replace(breakpoints)
        board.terminal_observer = self._terminal

    def _terminal(self,uc,pc,size):
        # Board's diagnostic assertion/power hooks precede debugger hooks.
        # Give an explicit breakpoint/step priority over these synthetic stops.
        if pc in self.hooks:
            self._break(uc,pc,size,None)
            return True
        return self.stepping

    def replace(self,values):
        if not isinstance(values,(list,tuple)) or len(values)>64:
            raise ValueError('Expected at most 64 breakpoint addresses')
        locations = {address(v)&~1 for v in values}
        for pc in locations-self.hooks.keys():
            if not any(lo <= pc <= hi for lo,hi,_ in self.board.uc.mem_regions()) or self.board.is_mmio(pc):
                raise ValueError(f'Breakpoint is outside mapped program memory: {pc:#x}')
        for pc in list(self.hooks):
            if pc not in locations:
                self.board.uc.hook_del(self.hooks.pop(pc))
        for pc in locations-self.hooks.keys():
            self.hooks[pc] = self.board.uc.hook_add(UC_HOOK_CODE,self._break,begin=pc,end=pc)

    def _break(self,uc,pc,size,_):
        if self.stepping or self.skip == pc:
            return
        self.hit = pc
        self.board.stop = dict(reason='breakpoint',pc=hex(pc))
        uc.emu_stop()

    def run(self,instructions,seconds,until=0xffffffff,step=False):
        uc = self.board.uc
        pc = uc.reg_read(UC_ARM_REG_PC)
        entry = pc|(1 if uc.reg_read(UC_ARM_REG_CPSR)&0x20 else 0)
        self.skip = pc if self.hit == pc else None
        self.hit = None
        self.stepping = step
        # Only the first fetched instruction may bypass the breakpoint on
        # continuation. If an IRQ redirects execution, clear the bypass too.
        first = None
        if self.skip is not None:
            def consumed(*_):
                nonlocal first
                self.skip = None
                uc.hook_del(first)
                first = None
            first = uc.hook_add(UC_HOOK_CODE,consumed)
        try:
            result = self.board.run(1 if step else instructions,seconds,entry=entry,until=until,report=False)
        finally:
            self.stepping = False
            self.skip = None
            if first is not None:
                uc.hook_del(first)
        if step and result['stop']['reason'] == 'instruction budget':
            self.board.stop = result['stop'] = dict(reason='debug step',pc=hex(uc.reg_read(UC_ARM_REG_PC)))
        if result['stop']['reason'] in ('breakpoint','debug step'):
            self.inspect()
        return result

    def memory(self,location,length=128):
        location = address(location)
        if type(length) is not int or not 1 <= length <= 4096:
            raise ValueError('Read length must be between 1 and 4096 bytes')
        ranges = ((0,0x800000),(0x04000000,0x04400000),(0x34000000,0x34400000),(0x40000000,0x40010000))
        if not any(lo <= location and location+length <= hi for lo,hi in ranges):
            raise ValueError('Memory inspection supports NOR, RAM and SRAM; MMIO is excluded')
        return bytes(self.board.uc.mem_read(location,length))

    def inspect(self,location=None):
        uc = self.board.uc
        regs = {f'r{i}':hex(uc.reg_read(UC_ARM_REG_R0+i)) for i in range(13)}
        regs.update({name:hex(uc.reg_read(reg)) for name,reg in
                     (('sp',UC_ARM_REG_SP),('lr',UC_ARM_REG_LR),('pc',UC_ARM_REG_PC),('cpsr',UC_ARM_REG_CPSR))})
        pc = uc.reg_read(UC_ARM_REG_PC)
        thumb = bool(uc.reg_read(UC_ARM_REG_CPSR)&0x20)
        disassembly = []
        try:
            # Avoid reading over the end of a memory region.
            end = next(hi+1 for lo,hi,_ in uc.mem_regions() if lo <= pc <= hi)
            raw = self.memory(pc,min(48,end-pc))
            disassembly = [f'{i.address:08x}  {i.bytes.hex():8s}  {i.mnemonic} {i.op_str}'
                           for i in self.board.md[thumb].disasm(raw,pc)][:10]
        except (ValueError,StopIteration):
            pass
        self.view = dict(registers=regs,mode='Thumb' if thumb else 'ARM',disassembly=disassembly)
        if location is not None:
            location = address(location)
            data = self.memory(location)
            self.view['memory'] = [f'{location+offset:08x}  {data[offset:offset+16].hex(" ")}' for offset in range(0,len(data),16)]
        return self.view

    def status(self):
        return dict(breakpoints=[hex(pc) for pc in sorted(self.hooks)],hit=hex(self.hit) if self.hit is not None else None,
                    view=self.view)

    def close(self):
        self.board.terminal_observer = None
        for hook in self.hooks.values():
            self.board.uc.hook_del(hook)
        self.hooks.clear()
