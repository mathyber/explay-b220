"""One-shot debugger URL input at the original browser's text handoff.

This replaces only a requested user input, not links, responses or protocols.
The editor is still confirmed with a real phone key; all URL parsing is guest code.
"""
from unicorn import UC_HOOK_CODE,UC_PROT_READ,UC_PROT_WRITE
from unicorn.arm_const import UC_ARM_REG_R0
from emulator.network_trace import PS_SHA256


class BrowserUrlInput:
    ADDRESS = 0x50001000

    def __init__(self,board,url):
        if board.image_sha256 != PS_SHA256 or not hasattr(board,'experimental_network'):
            raise ValueError('Debugger URL input requires an experimental network session')
        if not url.isascii() or not 0 < len(url) <= 512 or any(ord(c)<32 for c in url):
            raise ValueError('Expected an ASCII URL of 1..512 bytes without control characters')
        if board.last_window_id != 0x4001b:
            raise ValueError('Debugger URL input requires an already open URL editor')
        self.board,self.url,self.applied = board,url,False
        board.uc.mem_map(self.ADDRESS,4096,UC_PROT_READ|UC_PROT_WRITE)
        board.uc.mem_write(self.ADDRESS,url.encode('ascii')+b'\0')
        self.hook = board.uc.hook_add(UC_HOOK_CODE,self._input,begin=0x1d49f6,end=0x1d49f6)

    def _input(self,uc,address,size,data):
        if not self.applied:
            uc.reg_write(UC_ARM_REG_R0,self.ADDRESS)
            self.applied = True

    def report(self):
        return dict(mode='explicit one-shot debugger URL input',url=self.url,applied=self.applied,
                    entry='0x1d49f6',affects='first submitted URL only; subsequent links stay original')

    def close(self):
        self.board.uc.hook_del(self.hook)
        self.board.uc.mem_unmap(self.ADDRESS,4096)
