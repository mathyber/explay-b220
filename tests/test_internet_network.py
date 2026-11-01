"""Internet HLE keeps TCP payload/ports intact and does not add web capabilities."""
from concurrent.futures import Future
import gc
import select
import socket
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from unicorn.arm_const import (UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3,
                               UC_ARM_REG_SP,UC_ARM_REG_LR)
from emulator.board import Board
from emulator.host_network import InternetNetwork


class InternetNetworkTests(unittest.TestCase):
    def setUp(self):
        self.board = Board(explore=True)
        self.board.experimental_network = {}
        self.service = SimpleNamespace(phase='active')
        self.backend = InternetNetwork(self.board,self.service)

    def tearDown(self):
        self.backend.close()
        del self.backend,self.board
        gc.collect()

    def call(self,name,*args):
        b = self.board
        address = next(a for a,n in self.backend.APIS.items() if n == name)
        for r,v in zip((UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3),(*args,0,0,0,0)):
            b.uc.reg_write(r,v)
        b.uc.reg_write(UC_ARM_REG_SP,0x4000c000)
        b.uc.reg_write(UC_ARM_REG_LR,0x4000fff0)
        result = b.run(100,2,entry=address|1,until=0x4000fff0,report=False)
        self.assertEqual(result['stop']['reason'],'requested end address',result['stop'])
        return b.uc.reg_read(UC_ARM_REG_R0)

    def test_dns_waits_without_blocking_and_returns_real_resolver_result_or_failure(self):
        good,bad = Future(),Future()
        self.board.uc.mem_write(0x04001000,b'original.example\0')
        with patch.object(self.backend.resolver,'submit',side_effect=(good,bad)) as submit:
            self.assertEqual(self.call('dns',0x04001000),0)
            self.assertEqual(self.call('dns',0x04001000),0)
            good.set_result([(socket.AF_INET,socket.SOCK_STREAM,6,'',('192.0.2.31',0))])
            pointer = self.call('dns',0x04001000)
            self.assertEqual(bytes(self.board.uc.mem_read(pointer+0x30,4)),socket.inet_aton('192.0.2.31'))
            submit.assert_called_once_with(socket.getaddrinfo,'original.example',None,socket.AF_INET,socket.SOCK_STREAM)
            self.board.uc.mem_write(0x04001000,b'missing.invalid\0')
            self.assertEqual(self.call('dns',0x04001000),0)
            bad.set_exception(socket.gaierror(-2,'Name not found'))
            self.assertEqual(self.call('dns',0x04001000),0)
            self.assertTrue(any(e['kind']=='dns.error' for e in self.backend.events))

    def test_raw_binary_tcp_and_requested_port_are_preserved_without_tls_termination(self):
        # Opaque TLS-like bytes: neither endpoint asks the backend to decode TLS.
        payload = bytes.fromhex('160301001000ff80434c49454e54')+b'\0HTTP\xff\r\n'
        received = []
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0))
            listener.listen(1)
            listener.settimeout(3)
            port = listener.getsockname()[1]
            def echo():
                with listener.accept()[0] as peer:
                    peer.settimeout(3)
                    data = b''
                    while len(data) < len(payload):
                        data += peer.recv(len(payload)-len(data))
                    received.append(data)
                    peer.sendall(data)
            thread = threading.Thread(target=echo,daemon=True)
            thread.start()
            fd = self.call('socket',2,1,0,1)
            raw = b'\x00\x02'+port.to_bytes(2,'big')+socket.inet_aton('127.0.0.1')+bytes(8)
            self.board.uc.mem_write(0x04002000,raw)
            result = self.call('connect',fd,0x04002000,16)
            sock = self.backend.sockets[fd]['socket']
            if result == 0xffffffff:
                self.assertTrue(select.select([],[sock],[],2)[1])
                result = self.call('connect',fd,0x04002000,16)
            self.assertEqual(result,0)
            self.board.uc.mem_write(0x04003000,payload)
            self.assertEqual(self.call('send',fd,0x04003000,len(payload),0),len(payload))
            self.assertTrue(select.select([sock],[],[],2)[0])
            size = self.call('recv',fd,0x04004000,1024,0)
            self.assertEqual(bytes(self.board.uc.mem_read(0x04004000,size)),payload)
            thread.join(timeout=2)
            self.assertEqual(received,[payload])
            self.assertEqual(self.backend.sockets[fd]['endpoint'],['127.0.0.1',port])
            self.assertEqual(bytes(self.backend.sent),payload)
            self.assertEqual(bytes(self.backend.received),payload)

    def test_transport_is_unavailable_without_active_pdp(self):
        self.service.phase = 'ready'
        self.assertEqual(self.call('socket',2,1,0,1),0xffffffff)
        self.assertEqual(self.backend.sockets,{})
        self.service.phase = 'active'
        fd = self.call('socket',2,1,0,1)
        self.service.phase = 'ready'
        self.board.uc.mem_write(0x04003000,b'unchanged')
        self.assertEqual(self.call('send',fd,0x04003000,9,0),0xffffffff)
        self.assertEqual(bytes(self.backend.sent),b'')
        self.assertEqual(self.call('close',fd),0)

    def test_would_block_code_is_derived_from_original_receive_core(self):
        b = self.board
        b.uc.mem_write(0x0400100a,(0x100).to_bytes(2,'big'))
        b.uc.mem_write(0x04001010,(0x04002000).to_bytes(4,'big'))
        b.uc.mem_write(0x04003000,(16).to_bytes(4,'big'))
        for register,value in ((UC_ARM_REG_R0,0x04001000),(UC_ARM_REG_R1,0),
                               (UC_ARM_REG_R2,0x04004000),(UC_ARM_REG_R3,0x04003000),
                               (UC_ARM_REG_SP,0x4000c000),(UC_ARM_REG_LR,0x4000fff0)):
            b.uc.reg_write(register,value)
        result = b.run(10000,2,entry=0x65227f,until=0x4000fff0,report=False)
        self.assertEqual(result['stop']['reason'],'requested end address')
        self.assertEqual(b.uc.reg_read(UC_ARM_REG_R0),6)

    def test_explicit_run_scope_blocks_unknown_dns_and_other_ports_before_connect(self):
        b = self.board
        self.backend.allowed_hosts = frozenset(('allowed.example',))
        self.backend.allowed_ports = frozenset((80,))
        b.uc.mem_write(0x04001000,b'other.example\0')
        b.uc.reg_write(UC_ARM_REG_R0,0x04001000)
        result = b.run(100,2,entry=0x2cf9c7,report=False)
        self.assertEqual(result['stop']['detail'],'Host outside explicit run scope')
        self.assertEqual(self.backend.pending_dns,{})
        fd = self.call('socket',2,1,0,1)
        self.backend.resolved_addresses.add('192.0.2.5')
        b.uc.mem_write(0x04002000,bytes.fromhex('000201bbc0000205')+bytes(8))
        for r,v in ((UC_ARM_REG_R0,fd),(UC_ARM_REG_R1,0x04002000),(UC_ARM_REG_R2,16)):
            b.uc.reg_write(r,v)
        result = b.run(100,2,entry=0x2cfb6f,report=False)
        self.assertEqual(result['stop']['detail'],'Destination outside explicit run scope')
        self.assertFalse(self.backend.sockets[fd]['connecting'])


if __name__ == '__main__':
    unittest.main()
