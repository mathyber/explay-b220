"""Network lifecycle owned by one interactive window, between CPU slices."""
import json
import os
from pathlib import Path

from .virtual_network import VirtualNetwork
from .host_network import InternetNetwork
from .network_trace import network_code_matches,network_window


class WindowNetwork:
    def __init__(self,board,report_path,backend_factory=InternetNetwork):
        self.board,self.report_path,self.backend_factory = board,Path(report_path),backend_factory
        self.service = self.backend = self.url_input = None
        self.submitted_urls = []
        self.enabled = False
        self.sim_present = self.registered = True
        self.plmn = '25001'
        self.error = None
        self.failed = False
        self.dirty = False
        self.closed = False

    def configure(self,*,enabled,plmn='25001',sim_present=True,registered=True):
        if any(type(value) is not bool for value in (enabled,sim_present,registered)):
            raise ValueError('Network and SIM switches must be boolean')
        if enabled and not network_code_matches(self.board):
            raise ValueError('Network HLE needs the original PS network code (this firmware changed it)')
        if not isinstance(plmn,str) or len(plmn) not in (5,6) or not plmn.isascii() or not plmn.isdigit():
            raise ValueError('PLMN must contain 5 or 6 ASCII digits')
        if registered and not sim_present:
            raise ValueError('Insert the virtual SIM before registering it')
        if self.service and plmn != self.plmn:
            raise ValueError('Restart the phone before changing the virtual SIM operator')
        if self.failed:
            raise ValueError('Restart the phone after a network initialization error')
        self.enabled,self.plmn,self.sim_present,self.registered = enabled,plmn,sim_present,registered
        self.error = None
        self.dirty = True
        if self.backend:
            # Stop data immediately even if an outstanding guest confirmation
            # must be consumed before its MN service caches can change.
            self.backend.set_enabled(enabled and sim_present and registered)
        self.update()

    def update(self):
        if self.url_input and self.url_input.applied:
            # Guest code may retain the submitted pointer. Keep each input page
            # alive until shutdown; later submissions receive a distinct page.
            self.submitted_urls.append(self.url_input)
            self.url_input = None
        if self.dirty and not self.service and self.enabled:
            window = self.board.last_window_id
            if not network_window(window):
                return  # Browser settings do not exist yet during cold boot.
            if bytes(self.board.uc.mem_read(0x0433613c,4)) != bytes.fromhex('01010100'):
                return
            try:
                self.service = VirtualNetwork(self.board,stop_at_dns=False,home_plmn=self.plmn)
                self.backend = self.backend_factory(self.board,self.service)
                self.backend.set_enabled(self.enabled and self.sim_present and self.registered)
            except (ValueError,OSError) as exc:
                self.error = str(exc)
                self.failed = True
                self.dirty = False
                return
        if self.dirty and self.service:
            if self.service.phase == 'confirming':
                return  # Let the guest consume its in-flight confirmation first.
            self.service.set_service(sim_present=self.sim_present if self.enabled else False,
                                     registered=self.registered if self.enabled else False)
            self.dirty = False
        elif not self.enabled:
            self.dirty = False

    def open_url(self,url):
        if not self.enabled or not self.service or not self.sim_present or not self.registered:
            raise ValueError('Enable networking with a registered virtual SIM first')
        if self.dirty or self.service.phase == 'confirming':
            raise ValueError('Wait for the network state change to finish')
        if self.board.lcd_backlight is False:
            raise ValueError('Wake the phone screen before opening a URL')
        if self.url_input:
            raise ValueError('A URL is already waiting for the browser')
        from tools.browser_url_input import BrowserUrlInput
        if len(self.submitted_urls) >= 64:
            raise ValueError('Restart the phone after 64 debugger URL submissions')
        self.url_input = BrowserUrlInput(self.board,url,address=BrowserUrlInput.ADDRESS+4096*len(self.submitted_urls))
        self.board.keypad.key('soft_left',True)
        self.board.keypad.key('soft_left',False)

    def status(self):
        state = ('error' if self.failed else 'off' if not self.enabled else
                 'waiting_browser' if not self.service else
                 'changing' if self.dirty else self.service.phase)
        return dict(enabled=self.enabled,attached=self.service is not None,state=state,plmn=self.plmn,
                    sim_present=self.sim_present,registered=self.registered,
                    sent_bytes=len(self.backend.sent) if self.backend else 0,
                    received_bytes=len(self.backend.received) if self.backend else 0,
                    transport_mode=self.backend.mode if self.backend else None,
                    url_pending=self.url_input is not None,session_closed=self.closed,error=self.error,report=str(self.report_path))

    def write_report(self):
        value = dict(status=self.status(),service=self.service.report() if self.service else None,
                     transport=self.backend.report() if self.backend else None,
                     url_inputs=[item.report() for item in self.submitted_urls]+([self.url_input.report()] if self.url_input else []))
        temporary = self.report_path.with_name(f'.{self.report_path.name}-{os.getpid()}.tmp')
        try:
            self.report_path.parent.mkdir(parents=True,exist_ok=True)
            temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
            temporary.replace(self.report_path)
        except OSError:
            pass  # Optional UI output; socket state remains owned by this process.

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.url_input:
            self.submitted_urls.append(self.url_input)
            self.url_input = None
        self.write_report()
        for item in self.submitted_urls:
            item.close()
        if self.backend:
            self.backend.close()
        if self.service:
            self.service.close()
