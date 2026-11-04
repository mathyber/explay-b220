"""Experimental B220 socket HLE with loopback and unmodified Internet transport.

The original CMS adapters, timers, message delivery and HTTP parser keep running.
The Internet mode does not parse HTTP or terminate TLS. SIM/radio and native
TCP/IP packet emulation are separate from this host socket transport.
"""
import errno
import select
import socket
from concurrent.futures import ThreadPoolExecutor

from unicorn import UC_HOOK_CODE,UC_PROT_READ,UC_PROT_WRITE
from unicorn.arm_const import (UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,
                               UC_ARM_REG_R3,UC_ARM_REG_LR,UC_ARM_REG_PC)
from .network_trace import PS_SHA256,network_code_matches


class SocketNetwork:
    SCRATCH = 0x50000000
    FIRST_HANDLE = 0x61000001
    APIS = {0x2cfb32:'socket',0x2cf9c6:'dns',0x2cfb6e:'connect',
            0x2cfbca:'send',0x2cfbaa:'recv',0x2cfc2c:'close',
            0x2cfc38:'error',0x2cfb92:'ioctl'}
    UNSUPPORTED = (0x2cfb18,0x2cfb24,0x2cfb3e,0x2cfb4a,0x2cfb56,0x2cfb62,
                   0x2cfb7a,0x2cfb86,0x2cfb9e,0x2cfbb6,0x2cfbd6,0x2cfbea,
                   0x2cfbf6,0x2cfc02,0x2cfc0e,0x2cfc1a)

    def __init__(self,board,service=None,byte_limit=1024*1024):
        if not network_code_matches(board) or not hasattr(board,'experimental_network'):
            raise ValueError('Host backend requires the pinned experimental PDP session')
        if 'host_backend' in board.experimental_network:
            raise ValueError('A host backend has already been attached to this session')
        if type(byte_limit) is not int or not 1 <= byte_limit <= 256*1024*1024:
            raise ValueError('Network byte limit must be 1..256 MiB')
        self.board,self.service,self.byte_limit = board,service,byte_limit
        self.sockets = {}
        self.errors = {}
        self.next_handle = self.FIRST_HANDLE
        self.events = []
        self.sent = bytearray()
        self.received = bytearray()
        self.hooks = []
        self.closed = False
        self.enabled = True
        if service is not None and hasattr(service,'on_link_down'):
            service.on_link_down.append(self.disconnect)
        board.uc.mem_map(self.SCRATCH,4096,UC_PROT_READ|UC_PROT_WRITE)
        board.experimental_network['host_backend'] = dict(mode=self.mode)
        for address,name in self.APIS.items():
            self.hooks.append(board.uc.hook_add(UC_HOOK_CODE,self._call,user_data=name,begin=address,end=address))
        for address in self.UNSUPPORTED:
            self.hooks.append(board.uc.hook_add(UC_HOOK_CODE,self._call,user_data='unsupported',begin=address,end=address))

    def _event(self,kind,**values):
        if len(self.events) < 512:
            self.events.append(dict(kind=kind,blocks=self.board.block_serial,**values))

    def _return(self,value):
        uc = self.board.uc
        uc.reg_write(UC_ARM_REG_R0,value&0xffffffff)
        uc.reg_write(UC_ARM_REG_PC,uc.reg_read(UC_ARM_REG_LR))

    def _stop(self,detail,**values):
        self.board.stop = dict(reason=self.boundary,detail=detail,
                               pc=hex(self.board.uc.reg_read(UC_ARM_REG_PC)),**values)
        self.board.uc.emu_stop()

    @staticmethod
    def _buffer(pointer,size):
        return 0 <= size <= 65536 and any(start <= pointer <= end-size for start,end in
            ((0x04000000,0x04400000),(0x34000000,0x34400000),(0x40000000,0x40010000)))

    def _call(self,uc,address,size,name):
        args = [uc.reg_read(r) for r in (UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3)]
        fd = args[0]
        if (not self.enabled or self.service and self.service.phase != 'active') and name not in ('close','error'):
            if name == 'dns':
                self._return(0)
            elif name == 'socket' or fd in self.errors:
                if fd in self.errors:
                    self.errors[fd] = 13  # Closed by bearer loss, not would-block.
                self._return(-1)
            else:
                return
            self._event('service.unavailable',operation=name,phase=self.service.phase if self.service else 'disabled')
            return
        if name == 'dns':
            host = self.board.cstring(fd,254)
            addresses = self._resolve(host)
            if addresses is None:
                if self.board.stop is None:
                    self._return(0)
                return
            base = self.SCRATCH
            # hostent: name, aliases, family, addr length, addr-list (all u32 BE).
            words = (base+0x80,base+0x40,2,4,base+0x20)
            uc.mem_write(base,b''.join(v.to_bytes(4,'big') for v in words))
            uc.mem_write(base+0x20,(base+0x30).to_bytes(4,'big')+bytes(4))
            uc.mem_write(base+0x30,socket.inet_aton(addresses[0]))
            uc.mem_write(base+0x80,host.encode('ascii')+b'\0')
            self._event('dns',name=host,ipv4=addresses[0],addresses=addresses)
            self._return(base)
            return
        if name == 'socket':
            if args != [2,1,0,1] or len(self.sockets) >= 8 or len(self.errors) >= 128:
                self._stop('Unsupported socket or handle budget exhausted',args=args)
                return
            sock = socket.socket(socket.AF_INET,socket.SOCK_STREAM)
            sock.setblocking(False)
            fd = self.next_handle
            self.next_handle += 1
            self.sockets[fd] = dict(socket=sock,connecting=False,connected=False)
            self.errors[fd] = 0
            self._event('socket',handle=fd)
            self._return(fd)
            return
        if fd not in self.errors:
            return  # Other native clients retain their original implementation.
        state = self.sockets.get(fd)
        if name == 'error':
            self._return(self.errors[fd])
            return
        if not state:
            self.errors[fd] = 1
            self._return(-1)
            return
        sock = state['socket']
        try:
            if name == 'connect':
                if args[2] != 16 or not self._buffer(args[1],16):
                    self._stop('Invalid guest sockaddr')
                    return
                raw = bytes(uc.mem_read(args[1],16))
                endpoint = self._route(raw)
                if endpoint is None:
                    return
                if not state['connecting'] and not state['connected']:
                    status = sock.connect_ex(endpoint)
                    state['endpoint'] = list(endpoint)
                    if status in (0,errno.EISCONN,10056):
                        state['connected'] = True
                    elif status in (errno.EINPROGRESS,errno.EWOULDBLOCK,errno.EALREADY,10035,10036,10037):
                        state['connecting'] = True
                    else:
                        raise OSError(status,'Host connect failed')
                if state['connecting']:
                    _,writable,failed = select.select([],[sock],[sock],0)
                    if writable or failed:
                        error = sock.getsockopt(socket.SOL_SOCKET,socket.SO_ERROR)
                        if error:
                            raise OSError(error,'Host connect failed')
                        state.update(connecting=False,connected=True)
                self.errors[fd] = 0 if state['connected'] else 22
                self._event('connect',handle=fd,connected=state['connected'],endpoint=list(endpoint))
                self._return(0 if state['connected'] else -1)
            elif name in ('send','recv'):
                pointer,length,flags = args[1:]
                if not self._buffer(pointer,length) or flags:
                    self._stop('Unsupported guest data buffer or flags',operation=name,args=args)
                    return
                if len(self.sent)+len(self.received)+length > self.byte_limit:
                    self._stop('Emulator network byte budget exceeded')
                    return
                if name == 'send':
                    data = bytes(uc.mem_read(pointer,length))
                    count = sock.send(data)
                    self.sent.extend(data[:count])
                else:
                    data = sock.recv(length)
                    count = len(data)
                    if data:
                        uc.mem_write(pointer,data)
                        self.received.extend(data)
                self.errors[fd] = 0
                self._event(name,handle=fd,count=count)
                self._return(count)
            elif name == 'ioctl':
                # Captured CMS socket-mode command. All host sockets are already
                # nonblocking; this is not the BSD shutdown API.
                if args[1:3] != [0x1014,0]:
                    self._stop('Unsupported socket mode command',args=args)
                    return
                self.errors[fd] = 0
                self._event('socket.mode',handle=fd,command=args[1])
                self._return(0)
            elif name == 'close':
                sock.close()
                del self.sockets[fd]
                self._event('close',handle=fd)
                self._return(0)
            else:
                self._stop('Unimplemented guest socket API',args=args)
        except BlockingIOError:
            # Original receive core 0x652374 returns 6 for an empty
            # nonblocking socket. 13 means closed (0x2fe948), not would-block.
            self.errors[fd] = 6
            self._return(-1)
        except OSError as exc:
            self.errors[fd] = 1
            self._event('host.error',operation=name,error=str(exc))
            self._return(-1)

    def report(self):
        return dict(version=1,mode=self.mode,events=self.events,byte_limit=self.byte_limit,
                    sent_bytes=len(self.sent),received_bytes=len(self.received),
                    sent_preview=bytes(self.sent[:8192]).decode('ascii',errors='replace'),
                    received_preview=bytes(self.received[:8192]).decode('ascii',errors='replace'),
                    sent_hex_preview=bytes(self.sent[:8192]).hex(),received_hex_preview=bytes(self.received[:8192]).hex())

    def disconnect(self):
        for fd,state in self.sockets.items():
            state['socket'].close()
            self.errors[fd] = 13
        self.sockets.clear()
        self._event('bearer.down')

    def set_enabled(self,enabled):
        if type(enabled) is not bool:
            raise ValueError('Transport enabled flag must be boolean')
        if self.enabled and not enabled:
            self.disconnect()
        self.enabled = enabled

    def close(self):
        if self.closed:
            return
        for state in self.sockets.values():
            state['socket'].close()
        self.sockets.clear()
        if self.service is not None and hasattr(self.service,'on_link_down'):
            self.service.on_link_down.remove(self.disconnect)
        for hook in self.hooks:
            self.board.uc.hook_del(hook)
        self.hooks.clear()
        self.board.uc.mem_unmap(self.SCRATCH,4096)
        self.closed = True


class LoopbackNetwork(SocketNetwork):
    mode = 'loopback socket HLE; static laboratory DNS'
    boundary = 'loopback network boundary'

    def __init__(self,board,endpoint,domain='a.aa',service=None):
        if endpoint[0] != '127.0.0.1' or type(endpoint[1]) is not int or not 0 < endpoint[1] < 65536:
            raise ValueError('Only one IPv4 loopback endpoint is supported')
        if not domain or len(domain) > 120 or not domain.isascii():
            raise ValueError('Expected a short ASCII test domain')
        self.endpoint,self.domain = endpoint,domain
        super().__init__(board,service=service)

    def _resolve(self,host):
        if host != self.domain:
            self._stop('Unmapped laboratory DNS name',name=host)
            return None
        return ['127.0.0.1']

    def _route(self,raw):
        if raw[:8] != bytes.fromhex('000200507f000001'):
            self._stop('Destination outside laboratory route',sockaddr=raw.hex())
            return None
        return self.endpoint

    def report(self):
        return dict(super().report(),endpoint=list(self.endpoint),domain=self.domain)


class InternetNetwork(SocketNetwork):
    mode = 'Internet socket HLE; system IPv4 DNS; raw TCP, no HTTP/TLS adaptation'
    boundary = 'Internet emulation boundary'

    def __init__(self,board,service,byte_limit=16*1024*1024,allowed_hosts=None,allowed_ports=None):
        self.pending_dns = {}
        self.allowed_hosts = frozenset(allowed_hosts) if allowed_hosts is not None else None
        self.allowed_ports = frozenset(allowed_ports) if allowed_ports is not None else None
        self.resolved_addresses = set()
        self.resolver = ThreadPoolExecutor(max_workers=2,thread_name_prefix='b220-dns')
        try:
            super().__init__(board,service=service,byte_limit=byte_limit)
        except BaseException:
            self.resolver.shutdown(wait=False,cancel_futures=True)
            raise

    def _resolve(self,host):
        if self.allowed_hosts is not None and host not in self.allowed_hosts:
            self._stop('Host outside explicit run scope',name=host)
            return None
        if not host or not host.isascii() or len(host) > 253:
            self._event('dns.error',name=host,error='invalid guest hostname')
            return None
        if host not in self.pending_dns:
            if len(self.pending_dns) >= 128:
                self._stop('Emulator DNS name budget exhausted')
                return None
            self.pending_dns[host] = self.resolver.submit(socket.getaddrinfo,host,None,socket.AF_INET,socket.SOCK_STREAM)
            self._event('dns.pending',name=host)
            return None
        future = self.pending_dns[host]
        if not future.done():
            return None
        try:
            addresses = list(dict.fromkeys(item[4][0] for item in future.result()))
            self.resolved_addresses.update(addresses)
            return addresses or None
        except OSError as exc:
            self._event('dns.error',name=host,error=str(exc))
            return None

    def _route(self,raw):
        if int.from_bytes(raw[:2],'big') != socket.AF_INET or not int.from_bytes(raw[2:4],'big'):
            self._stop('Unsupported sockaddr family or port',sockaddr=raw.hex())
            return None
        endpoint = socket.inet_ntoa(raw[4:8]),int.from_bytes(raw[2:4],'big')
        if ((self.allowed_hosts is not None and endpoint[0] not in self.resolved_addresses) or
                (self.allowed_ports is not None and endpoint[1] not in self.allowed_ports)):
            self._stop('Destination outside explicit run scope',endpoint=list(endpoint))
            return None
        return endpoint

    def report(self):
        return dict(super().report(),allowed_hosts=sorted(self.allowed_hosts) if self.allowed_hosts is not None else None,
                    allowed_ports=sorted(self.allowed_ports) if self.allowed_ports is not None else None)

    def close(self):
        self.resolver.shutdown(wait=False,cancel_futures=True)
        super().close()
