"""Opt-in offline control-plane experiment for the pinned B220 browser.

Seeds recovered RAM caches, then substitutes the CMS PDP service at its task
handler. The firmware itself allocates and queues the success confirmation.
This is not a SIM controller, radio, IP interface or Internet backend.
Execution stops before DNS; no host network resources are created.
"""
from unicorn import UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_PC

from .network_trace import PS_SHA256


class VirtualNetwork:
    def __init__(self,board):
        if board.image_sha256 != PS_SHA256:
            raise ValueError('Virtual network requires the pinned B220 PS')
        if getattr(board,'_running',False) or hasattr(board,'experimental_network'):
            raise ValueError('Virtual network requires a stopped, unmodified laboratory session')
        if board.last_window_id not in (0x40001,0x4001b):
            raise ValueError('Open the browser menu or URL editor before enabling the experiment')
        # Only slot 0 / profile 0 is recovered and supported by this experiment.
        # Validate all preconditions before making any writes.
        uc = board.uc
        if bytes(uc.mem_read(0x0433613c,4)) != b'\x01\x01\x01\x00':
            raise ValueError('Expected initialized browser settings with SIM1 profile 0 selected')
        changes = (
            (0x0433287c,4,(7,0),0,'MMI SIM1 readiness cache'),
            (0x04332870,4,(2,),2,'MMI SIM1 service cache'),
            (0x0434c4b4,4,(0,1),1,'packet service precondition at 0x240270'),
            (0x0434c4c0,2,(0xffff,1),1,'packet registration precondition at 0x240276'),
            (0x0434c4c8,1,(0,1),1,'packet availability precondition at 0x240284'),
            (0x04336160,1,(0,1),1,'browser profile 0: WAP2/HTTP instead of WAP1 gateway'),
        )
        if any(int.from_bytes(uc.mem_read(0x0433287c+i*0xcc,4),'big') != 7 for i in (1,2)):
            raise ValueError('Only a single virtual SIM is supported; other slots must be absent')
        self.writes = []
        for address,size,allowed,value,label in changes:
            before = int.from_bytes(uc.mem_read(address,size),'big')
            if before not in allowed:
                raise ValueError(f'Unexpected laboratory RAM layout at {address:#x}: {before:#x}')
            self.writes.append(dict(address=hex(address),size=size,before=before,after=value,label=label))
        self.board = board
        self.events = []
        self.phase = 'ready'
        self.hooks = []
        # This marker intentionally makes the normal snapshot serializer reject
        # this board, including after close(). There is no silent loss of HLE state.
        board.experimental_network = dict(version=1,mode='offline PDP prototype')
        for item in self.writes:
            uc.mem_write(int(item['address'],16),item['after'].to_bytes(item['size'],'big'))
        for address,callback in ((0x240252,self._activate),(0x599c14,self._confirm),(0x2cf9c6,self._dns)):
            self.hooks.append(uc.hook_add(UC_HOOK_CODE,callback,begin=address,end=address))

    def _event(self,kind,**values):
        if len(self.events) < 128:
            self.events.append(dict(kind=kind,blocks=self.board.block_serial,**values))

    def _stop(self,operation,address,**values):
        self.board.stop = dict(reason='virtual network boundary',operation=operation,pc=hex(address),**values)
        self.board.uc.emu_stop()

    def _activate(self,uc,address,size,data):
        slot,client,pointer = (uc.reg_read(r) for r in (UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2))
        if slot != 0 or client != 0x15:
            return  # The experiment only implements this CMS client on SIM1.
        if not 0x04000000 <= pointer <= 0x043fffc0:
            self._stop('unsupported PDP request buffer',address)
            return
        raw = bytes(uc.mem_read(pointer,64))
        if (raw[:4] != bytes.fromhex('13f40040') or raw[12:16] != client.to_bytes(4,'big') or
                raw[16] != 0 or raw[20:28] != bytes.fromhex('0000000100000000') or self.phase != 'ready'):
            self._stop('unsupported PDP activation request',address,request=raw.hex())
            return
        self.phase = 'confirming'
        self._event('pdp.activation',slot=slot,client=client,request=raw.hex())
        # Same reply helper used by the original handler for result=0x66.
        # Tail call before the original prologue: LR and the caller's stack stay
        # intact. The guest dispatcher still owns/frees the request message.
        uc.reg_write(UC_ARM_REG_R2,0)
        uc.reg_write(UC_ARM_REG_PC,0x53b045)

    def _confirm(self,uc,address,size,data):
        if self.phase != 'confirming':
            return
        pointer = uc.reg_read(UC_ARM_REG_R0)
        if not 0x04000000 <= pointer <= 0x043ffff8:
            self._stop('unsupported PDP confirmation buffer',address)
            return
        raw = bytes(uc.mem_read(pointer,8))
        if raw != bytes(8):  # uint32 result=0, uint32 slot=0, big endian
            self._stop('unexpected PDP confirmation',address,confirmation=raw.hex())
            return
        self.phase = 'active'
        self._event('pdp.confirmation',result=0,slot=0)

    def _dns(self,uc,address,size,data):
        name = self.board.cstring(uc.reg_read(UC_ARM_REG_R0),160)
        self._event('dns.boundary',name=name,phase=self.phase)
        self._stop('DNS backend not implemented',address,name=name)

    def report(self):
        return dict(version=1,mode='experimental offline PDP service; no Internet backend',
                    phase=self.phase,ps_sha256=PS_SHA256,slot=0,profile=0,
                    ram_setup=self.writes,events=self.events,
                    limitations=['RAM cache seeding, not a SIM/radio controller',
                                 'No IP/DNS configuration or packet/socket backend',
                                 'Stops before native DNS; snapshots disabled for this session'])

    def close(self):
        for hook in self.hooks:
            self.board.uc.hook_del(hook)
        self.hooks.clear()
