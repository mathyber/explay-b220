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
# Code the network HLE hooks, re-enters or relies on: browser URL entry, CMS GPRS/
# PDP/socket/DNS paths, socket API and TCP/IP tasks, SIM checks, the native WAP
# account routine. A modified PS (the CUT build) gets the network when these bytes
# equal the pinned PS; RAM preconditions are still validated by VirtualNetwork.
NETWORK_CODE = ((0x1d4900,0x1d4b00),(0x23fe00,0x240400),(0x2cf900,0x2cfe00),(0x2d9c00,0x2d9d00),
                (0x2fe900,0x2fea00),(0x48e100,0x48e140),(0x4adf00,0x4ae000),(0x4b0300,0x4b0600),
                (0x53b000,0x53b100),(0x58d700,0x58d900),(0x598600,0x598e40),(0x5990f0,0x59b100),
                (0x59e500,0x59e600),(0x5eca00,0x5ece00),(0x652300,0x652400))


def network_code_matches(board):
    """True for the pinned PS, or a PS whose network-related code is unchanged."""
    if board.image_sha256 == PS_SHA256:
        return True
    reference = getattr(board,'firmware',{}).get('reference')
    if not reference or getattr(board,'firmware',{}).get('reference_sha256') != PS_SHA256:
        return False
    try:
        original = Path(reference).read_bytes()
    except OSError:
        return False
    return all(bytes(board.uc.mem_read(start,end-start)) == original[start:end] for start,end in NETWORK_CODE)


def image_network_code_matches(image,original):
    return len(image) == len(original) and all(image[start:end] == original[start:end] for start,end in NETWORK_CODE)


# Function identities follow CMS call sites and the original veneer targets.
POINTS = {
    0x1d49f6:'browser.access_url',0x2d9c9a:'cms.access_url',
    0x5991e6:'cms.gprs.start',0x5990f4:'cms.gprs.start.sim',
    0x59e5b8:'cms.pdp.configure',0x5ecdd2:'pdp.configure.request',
    0x5eca5e:'pdp.activate.request',0x599c14:'pdp.activate.confirm',
    0x24028a:'pdp.reject.66',0x23ff6a:'pdp.status.66',
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
        if not network_code_matches(board):
            raise ValueError('Network trace addresses require the pinned B220 PS network code')
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
        decoded = {}
        if name == 'pdp.activate.confirm' and 0x04000000 <= args[0] <= 0x043ffff8:
            raw = bytes(uc.mem_read(args[0],8))
            decoded = dict(result=int.from_bytes(raw[:4],'big'),slot=int.from_bytes(raw[4:],'big'))
        elif name == 'pdp.activate.request' and 0x04000000 <= args[2] <= 0x043fffd4:
            decoded = dict(slot=args[0],selection_mode=args[1],context_bytes=bytes(uc.mem_read(args[2],44)).hex())
        self._append(name,address,args=[hex(v) for v in args],
                     strings=[self.board.cstring(v,160) for v in args],
                     r4=hex(uc.reg_read(UC_ARM_REG_R4)),r6=hex(uc.reg_read(UC_ARM_REG_R6)),**decoded)

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
