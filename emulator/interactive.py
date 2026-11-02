"""Continuous emulator session with a small, per-window JSONL command channel."""
import json
import os
from pathlib import Path
import time

from unicorn.arm_const import UC_ARM_REG_PC,UC_ARM_REG_CPSR


class CommandInbox:
    def __init__(self,path):
        self.path = Path(path)
        self.position = 0
        self.partial = b''

    def poll(self):
        try:
            with self.path.open('rb') as stream:
                stream.seek(self.position)
                data = stream.read(65536)
                self.position = stream.tell()
        except (FileNotFoundError,PermissionError):
            return []
        lines = (self.partial+data).split(b'\n')
        self.partial = lines.pop()
        if len(self.partial) > 4096:
            self.partial = b''
        result = []
        for line in lines:
            if not line.strip():
                continue
            try:
                value = json.loads(line)
                if isinstance(value,dict):
                    result.append(value)
            except (ValueError,UnicodeError):
                continue
        return result


class InteractiveSession:
    def __init__(self,board,args,emit_result,flush_media=None,debugger=None):
        if not board.keypad:
            raise ValueError('Interactive session requires the keypad model')
        self.board,self.args,self.emit_result = board,args,emit_result
        self.inbox = CommandInbox(args.commands)
        self.started = time.monotonic()
        self.active_seconds = 0
        self.ready = False
        self.paused = False
        self.shutdown = False
        self.last_key = None
        self.last_error = None
        self.last_status = None
        self.last_publish = 0
        self.boot_seconds = None
        self.boot_checkpoint_block = None
        from .window_network import WindowNetwork
        self.fixture = None
        options = {}
        if getattr(args,'window_http_test',False):
            from tools.http_fixture import HttpFixture
            from .host_network import LoopbackNetwork
            self.fixture = HttpFixture()
            options['backend_factory'] = lambda b,s:LoopbackNetwork(b,self.fixture.endpoint,service=s)
        self.network = WindowNetwork(board,Path(args.session_status).with_suffix('.network.json'),**options)
        self.last_network_publish = 0
        self.flush_media = flush_media
        self.debugger = debugger
        self.debug_stopped = False
        self.step_requested = False

    def commands(self):
        for command in self.inbox.poll():
            action = command.get('action')
            if action == 'stop':
                self.shutdown = True
                break
            elif action == 'network':
                try:
                    self.network.configure(enabled=command.get('enabled'),plmn=command.get('plmn','25001'),
                                           sim_present=command.get('sim_present',True),registered=command.get('registered',True))
                    self.last_error = None
                except (ValueError,OSError) as exc:
                    self.last_error = str(exc)
            elif action == 'open_url' and self.ready:
                try:
                    url = command.get('url')
                    if not isinstance(url,str):
                        raise ValueError('URL must be a string')
                    if self.debug_stopped:
                        raise ValueError('Resume the debugger before opening a URL')
                    self.network.open_url(url)
                    self.paused = False
                    self.last_error = None
                except (ValueError,OSError) as exc:
                    self.last_error = str(exc)
            elif action == 'debug':
                try:
                    if not self.debugger:
                        raise ValueError('Debugger is not available in this session')
                    operation = command.get('operation')
                    if operation == 'breakpoints':
                        self.debugger.replace(command.get('addresses'))
                    elif operation == 'inspect':
                        if not self.paused:
                            raise ValueError('Pause the CPU before inspection')
                        self.debugger.inspect(command.get('address'))
                    elif operation == 'step':
                        if not self.paused:
                            raise ValueError('Pause the CPU before stepping')
                        self.step_requested = True
                    else:
                        raise ValueError('Unknown debugger command')
                    self.last_error = None
                except ValueError as exc:
                    self.last_error = str(exc)
            elif action == 'pause':
                self.paused = True
                if self.debugger:
                    self.debugger.inspect()
            elif action == 'resume':
                self.paused = False
                self.debug_stopped = False
            elif action == 'release_all':
                self.board.keypad.release_all()
                if self.ready and self.board.keypad.queue and not self.debug_stopped:
                    self.paused = False
            elif action == 'key' and self.ready:
                try:
                    if not isinstance(command.get('down'),bool):
                        raise ValueError('Key direction must be boolean')
                    self.board.keypad.key(command.get('key'),command['down'])
                    self.last_key = command.get('key')
                    if not self.debug_stopped:
                        self.paused = False
                    self.last_error = None
                except ValueError as exc:
                    self.last_error = str(exc)

    def publish(self,force=False,state=None):
        value = dict(state=state or ('paused' if self.paused else 'booting' if not self.ready else 'running'),
                     ready=self.ready,last_key=self.last_key,input_error=self.last_error,
                     key_edges=len(self.board.keypad.events),guest_key_events=len(self.board.key_callbacks),
                     window_id=hex(self.board.last_window_id) if self.board.last_window_id is not None else None,
                     backlight=self.board.lcd_backlight,
                     lcd_pixels=self.board.lcd.pixel_writes if self.board.lcd else 0,
                     boot_seconds=self.boot_seconds,report=str(self.args.out),network=self.network.status(),
                     sd_card=dict(present=hasattr(self.board.spi,'image'),
                                  sectors_read=getattr(self.board.spi,'sectors_read',0),
                                  sectors_written=getattr(self.board.spi,'sectors_written',0)))
        if self.debugger:
            value['debugger'] = self.debugger.status()
            value['cpu_stop'] = self.board.stop
        now = time.monotonic()
        if force or now-self.last_network_publish >= 1:
            if self.flush_media:
                self.flush_media()
            self.network.write_report()
            self.last_network_publish = now
        if not force and value == self.last_status and now-self.last_publish < 1:
            return
        path = Path(self.args.session_status)
        path.parent.mkdir(parents=True,exist_ok=True)
        temporary = path.with_name(f'.{path.name}-{os.getpid()}.tmp')
        try:
            temporary.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')
            temporary.replace(path)
        except OSError:
            return  # Optional presentation output; retry on the next slice.
        self.last_status,self.last_publish = value,now

    def result(self,error=None):
        value = self.board.report(self.args.instructions,time.monotonic()-self.started,error)
        value['interactive_session'] = dict(ready=self.ready,paused=self.paused,
                    active_seconds=round(self.active_seconds,3),boot_seconds=self.boot_seconds,
                    last_input_error=self.last_error,instruction_budget_scope='per execution slice')
        return value

    def run(self):
        try:
            return self._run()
        finally:
            self.network.close()
            if self.fixture:
                self.fixture.close()

    def _run(self):
        if getattr(self.args,'load_state',None):
            self.ready,self.paused = True,True
            self.boot_seconds = 0
            self.board.stop_after_frames = 0
        else:
            self.board.stop_after_frames = self.args.frames or 5
        if getattr(self.args,'debug_start_paused',False):
            self.paused,self.debug_stopped = True,True
        if self.paused and self.debugger:
            self.debugger.inspect()
        self.publish(force=True)
        while True:
            self.commands()
            if self.shutdown:
                self.board.stop = dict(reason='host requested shutdown',pc=hex(self.board.uc.reg_read(UC_ARM_REG_PC)))
                break
            self.network.update()
            if self.paused and not self.step_requested:
                self.publish()
                time.sleep(0.02)
                continue
            if not self.ready and self.active_seconds >= self.args.seconds:
                self.board.stop = dict(reason='wall time budget',pc=hex(self.board.uc.reg_read(UC_ARM_REG_PC)))
                break
            pc = self.board.uc.reg_read(UC_ARM_REG_PC)
            if self.board.uc.reg_read(UC_ARM_REG_CPSR)&0x20:
                pc |= 1
            if self.debugger:
                step = self.debugger.run(self.args.instructions,0.2,until=self.args.until,step=self.step_requested)
            else:
                step = self.board.run(self.args.instructions,0.2,entry=pc,until=self.args.until,report=False)
            self.step_requested = False
            self.active_seconds += step['elapsed_seconds']
            self.network.update()
            if step['stop']['reason'] in ('breakpoint','debug step'):
                self.paused,self.debug_stopped = True,True
            elif step['stop']['reason'] == 'requested LCD frame count' and not self.ready:
                self.boot_checkpoint_block = self.board.block_serial
                self.board.stop_after_frames = 0
            elif step['stop']['reason'] not in ('instruction budget','wall time budget'):
                self.publish(force=True,state='stopped')
                return self.result(step['engine_error'])
            # This checkpoint enables host input; it does not establish that
            # the boot animation has finished. In particular, SD initialization
            # changes the boot timing. Keep the CPU running through the splash
            # and let the original firmware reach its home/date screen itself.
            if (not self.ready and self.boot_checkpoint_block is not None and
                    self.board.block_serial >= self.boot_checkpoint_block+512000):
                self.ready = True
                if not self.debug_stopped:
                    self.paused = False
                self.boot_seconds = round(time.monotonic()-self.started,3)
                self.emit_result(self.result(),True)
            self.publish()
        self.publish(force=True,state='stopped')
        return self.result()
