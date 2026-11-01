"""Opt-in offline control-plane experiment for the pinned B220 browser.

Seeds recovered RAM caches, then substitutes the CMS PDP service at its task
handler. The firmware itself allocates and queues the success confirmation.
This is not a SIM controller, radio, IP interface or Internet backend.
The default mode stops before DNS. An explicitly selected separate backend
can supply the data calls; this control-plane class creates no host sockets.
"""
from unicorn import UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_PC

from .network_trace import PS_SHA256


class VirtualNetwork:
    def __init__(self,board,stop_at_dns=True,configure_http=False,home_plmn=None):
        if board.image_sha256 != PS_SHA256:
            raise ValueError('Virtual network requires the pinned B220 PS')
        if getattr(board,'_running',False) or hasattr(board,'experimental_network'):
            raise ValueError('Virtual network requires a stopped, unmodified laboratory session')
        if board.last_window_id is None or not 0x40000 <= board.last_window_id <= 0x400ff:
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
        )
        if configure_http:
            changes += ((0x04336160,1,(0,1),1,'explicit local fixture: WAP2/HTTP profile'),)
        if home_plmn is not None:
            if not isinstance(home_plmn,str) or len(home_plmn) not in (5,6) or not home_plmn.isascii() or not home_plmn.isdigit():
                raise ValueError('Virtual SIM home PLMN must contain 5 or 6 ASCII digits')
            changes += ((0x043328a0,2,range(1000),int(home_plmn[:3]),'virtual SIM home MCC'),
                        (0x043328a2,2,range(1000),int(home_plmn[3:]),'virtual SIM home MNC'))
        if any(int.from_bytes(uc.mem_read(0x0433287c+i*0xcc,4),'big') != 7 for i in (1,2)):
            raise ValueError('Only a single virtual SIM is supported; other slots must be absent')
        self.writes = []
        for address,size,allowed,value,label in changes:
            before = int.from_bytes(uc.mem_read(address,size),'big')
            if before not in allowed:
                raise ValueError(f'Unexpected laboratory RAM layout at {address:#x}: {before:#x}')
            self.writes.append(dict(address=hex(address),size=size,before=before,after=value,label=label))
        self.board = board
        self.stop_at_dns = stop_at_dns
        self.configure_http = configure_http
        self.home_plmn = home_plmn
        self.events = []
        self.phase = 'ready'
        self.sim_present = True
        self.registered = True
        self.pending_result = None
        self.on_link_down = []
        self.hooks = []
        # This marker intentionally makes the normal snapshot serializer reject
        # this board, including after close(). There is no silent loss of HLE state.
        board.experimental_network = dict(version=1,mode='offline PDP prototype')
        for item in self.writes:
            uc.mem_write(int(item['address'],16),item['after'].to_bytes(item['size'],'big'))
        self.provisioning = None
        if home_plmn is not None:
            from .sim_profiles import BASE,provision_wap_accounts
            settings,self.provisioning = provision_wap_accounts(board)
            uc.mem_write(BASE,settings)
        callbacks = [(0x240252,self._activate),(0x599c14,self._confirm)]
        if stop_at_dns:
            callbacks.append((0x2cf9c6,self._dns))
        for address,callback in callbacks:
            self.hooks.append(uc.hook_add(UC_HOOK_CODE,callback,begin=address,end=address))

    def _event(self,kind,**values):
        if len(self.events) < 128:
            self.events.append(dict(kind=kind,blocks=self.board.block_serial,**values))

    def _stop(self,operation,address,**values):
        self.board.stop = dict(reason='virtual network boundary',operation=operation,pc=hex(address),**values)
        self.board.uc.emu_stop()

    def set_service(self,*,sim_present=None,registered=None):
        """MN-level card/service change; not a hardware hot-swap interrupt."""
        if getattr(self.board,'_running',False) or self.phase == 'confirming':
            raise ValueError('Stop outside a pending PDP confirmation before changing SIM service')
        present,next_registered = self.sim_present,self.registered
        if sim_present is not None:
            if type(sim_present) is not bool:
                raise ValueError('SIM presence must be boolean')
            present = sim_present
            if not sim_present:
                next_registered = False
        if registered is not None:
            if type(registered) is not bool or registered and not present:
                raise ValueError('Registration requires a present virtual SIM')
            next_registered = registered
        self.sim_present,self.registered = present,next_registered
        if not self.sim_present or not self.registered:
            for callback in tuple(self.on_link_down):
                callback()
            self.phase = 'ready' if self.sim_present else 'absent'
        elif self.phase == 'absent':
            self.phase = 'ready'
        uc = self.board.uc
        uc.mem_write(0x0433287c,(0 if self.sim_present else 7).to_bytes(4,'big'))
        for address,size,value in ((0x0434c4b4,4,1 if self.registered else 0),
                                   (0x0434c4c0,2,1 if self.registered else 0xffff),
                                   (0x0434c4c8,1,1 if self.registered else 0)):
            uc.mem_write(address,value.to_bytes(size,'big'))
        self._event('sim.service',present=self.sim_present,registered=self.registered,phase=self.phase)

    def _activate(self,uc,address,size,data):
        slot,client,pointer = (uc.reg_read(r) for r in (UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2))
        if slot != 0 or client != 0x15:
            return  # The experiment only implements this CMS client on SIM1.
        if not 0x04000000 <= pointer <= 0x043fffc0:
            self._stop('unsupported PDP request buffer',address)
            return
        raw = bytes(uc.mem_read(pointer,64))
        if (raw[:4] != bytes.fromhex('13f40040') or raw[12:16] != client.to_bytes(4,'big') or
                raw[16] != 0 or raw[20:28] != bytes.fromhex('0000000100000000') or self.phase not in ('ready','absent')):
            self._stop('unsupported PDP activation request',address,request=raw.hex())
            return
        self.phase = 'confirming'
        self.pending_result = 0 if self.sim_present and self.registered else 0x66
        self._event('pdp.activation',slot=slot,client=client,request=raw.hex(),result=self.pending_result)
        # Same reply helper used by the original handler for result=0x66.
        # Tail call before the original prologue: LR and the caller's stack stay
        # intact. The guest dispatcher still owns/frees the request message.
        uc.reg_write(UC_ARM_REG_R2,self.pending_result)
        uc.reg_write(UC_ARM_REG_PC,0x53b045)

    def _confirm(self,uc,address,size,data):
        if self.phase != 'confirming':
            return
        pointer = uc.reg_read(UC_ARM_REG_R0)
        if not 0x04000000 <= pointer <= 0x043ffff8:
            self._stop('unsupported PDP confirmation buffer',address)
            return
        raw = bytes(uc.mem_read(pointer,8))
        if self.pending_result is None:
            self._stop('PDP confirmation without a pending request',address)
            return
        expected = self.pending_result
        if raw != expected.to_bytes(4,'big')+bytes(4):
            self._stop('unexpected PDP confirmation',address,confirmation=raw.hex())
            return
        self.phase = 'active' if expected == 0 else ('ready' if self.sim_present else 'absent')
        self._event('pdp.confirmation',result=expected,slot=0)
        self.pending_result = None

    def _dns(self,uc,address,size,data):
        name = self.board.cstring(uc.reg_read(UC_ARM_REG_R0),160)
        self._event('dns.boundary',name=name,phase=self.phase)
        self._stop('DNS backend not implemented',address,name=name)

    def report(self):
        return dict(version=1,mode='experimental PDP service',stop_at_dns=self.stop_at_dns,
                    configures_http_profile=self.configure_http,
                    sim_home_plmn=self.home_plmn,
                    native_sim_provisioning=self.provisioning,
                    sim_present=self.sim_present,registered=self.registered,
                    phase=self.phase,ps_sha256=PS_SHA256,slot=0,profile=0,
                    ram_setup=self.writes,events=self.events,
                    limitations=['RAM cache seeding, not a SIM/radio controller',
                                 'No native IP interface; data transport requires a separate HLE backend',
                                 'Snapshots disabled for this experimental session'])

    def close(self):
        for hook in self.hooks:
            self.board.uc.hook_del(hook)
        self.hooks.clear()
