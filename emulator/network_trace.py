"""Read-only browser/network observations for the pinned B220 firmware.

No network is opened, no guest registers or memory are changed. This observer
is deliberately external to Board state and can be attached after restoration.
"""
from collections import Counter
import json
from pathlib import Path
import re

from unicorn import UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3,UC_ARM_REG_R4,UC_ARM_REG_R6,UC_ARM_REG_LR,UC_ARM_REG_PC


PS_SHA256 = '7bd1dd7b3ce3a3efa65f957eb24e4e4b3114775c8b7770121f98b39816bacbeb'
# Function identities follow CMS call sites and the original veneer targets.
POINTS = {
    # Homepage checks the current SIM at 0x58d704. Another menu branch scans slots.
    0x58d708:'browser.sim.result',0x58d832:'browser.sim.scan.result',
    0x58d848:'browser.sim.reject',
    0x4b03c4:'phone.sim.check',0x4b03fc:'phone.sim.check.result',
    0x4adf68:'phone.sim.status.result',
    0x4b0574:'phone.sim.alert',
    0x59ab4c:'cms.socket',0x2cfb32:'socket.api',0x59ab72:'cms.socket.result',
    0x59ad1a:'cms.dns',0x2cf9c6:'dns.api',0x59ad50:'cms.dns.result',
    0x59ae14:'cms.connect',0x59b0ee:'cms.send',
    0x2cfd34:'tcpip.task',0x2cfd58:'tcpip.clock.task',
}
RELEVANT = re.compile(r'CMS|mmibrowser|mmiwap|\bWAP\b|GPRS|PDP|TCPIP|socket|SIM.*(?:Ready|READY|Status|status|Exist|exist|Valid|valid)|IsSim|IsSIM|HandleWap',re.I)
WINDOW_FORMAT = 'MMK_RunWinProc, win_handle'
REGISTERS = (UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3)


class NetworkTrace:
    def __init__(self,board,limit=10000):
        if board.image_sha256 != PS_SHA256:
            raise ValueError('Network trace addresses require the pinned B220 PS')
        if type(limit) is not int or not 0 < limit <= 100000:
            raise ValueError('Network trace limit must be 1..100000')
        self.board,self.limit = board,limit
        self.events = []
        self.counts = Counter()
        self.dropped = 0
        self.hooks = []
        for address,name in POINTS.items():
            self.hooks.append(board.uc.hook_add(UC_HOOK_CODE,self._point,user_data=name,begin=address,end=address))
        self.hooks.append(board.uc.hook_add(UC_HOOK_CODE,self._log,begin=0x48e11a,end=0x48e11a))

    def _append(self,kind,address,**values):
        self.counts[kind] += 1
        if len(self.events) >= self.limit:
            self.dropped += 1
            return
        self.events.append(dict(kind=kind,pc=hex(address),blocks=self.board.block_serial,
                                lr=hex(self.board.uc.reg_read(UC_ARM_REG_LR)),**values))

    def _point(self,uc,address,size,name):
        args = [uc.reg_read(register) for register in REGISTERS]
        self._append(name,address,args=[hex(v) for v in args],
                     strings=[self.board.cstring(v,160) for v in args],
                     r4=hex(uc.reg_read(UC_ARM_REG_R4)),r6=hex(uc.reg_read(UC_ARM_REG_R6)))

    def _log(self,uc,address,size,data):
        pointer = uc.reg_read(UC_ARM_REG_R0)
        fmt = self.board.cstring(pointer)
        if not fmt:
            return
        if fmt.startswith(WINDOW_FORMAT):
            self._append('window',address,handle=hex(uc.reg_read(UC_ARM_REG_R1)),
                         window_id=hex(uc.reg_read(UC_ARM_REG_R2)),message=hex(uc.reg_read(UC_ARM_REG_R3)))
        elif RELEVANT.search(fmt):
            args = [uc.reg_read(r) for r in REGISTERS[1:]]
            self._append('log',address,format_pointer=hex(pointer),format=fmt,
                         args=[hex(v) for v in args],strings=[self.board.cstring(v,160) for v in args])

    def mark(self,name):
        self._append('host.mark',self.board.uc.reg_read(UC_ARM_REG_PC),name=name)

    def report(self):
        return dict(version=1,mode='observe only; no host network connections',
                    ps_sha256=PS_SHA256,limit=self.limit,dropped=self.dropped,
                    counts=dict(self.counts),last_window_id=hex(self.board.last_window_id) if self.board.last_window_id is not None else None,
                    stop=self.board.stop,events=self.events)

    def write(self,path):
        path = Path(path)
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(self.report(),ensure_ascii=False,indent=2),encoding='utf-8')

    def close(self):
        for hook in self.hooks:
            self.board.uc.hook_del(hook)
        self.hooks.clear()
