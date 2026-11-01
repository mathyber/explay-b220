"""Exercise the recovered guest ABI with real local TCP, including partial I/O."""
import gc
import select
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from unicorn import UcError
from unicorn.arm_const import (UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3,
                               UC_ARM_REG_SP,UC_ARM_REG_LR)
from emulator.board import Board
from emulator.host_network import LoopbackNetwork
from emulator.snapshot import SnapshotError,save_snapshot
from tools.http_fixture import HttpFixture


class HostNetworkTests(unittest.TestCase):
    def setUp(self):
        self.board = Board(explore=True)
        self.board.experimental_network = {}
        self.fixture = HttpFixture()
        self.backend = LoopbackNetwork(self.board,self.fixture.endpoint)

    def tearDown(self):
        self.backend.close()
        self.fixture.close()
        del self.backend,self.board
        gc.collect()

    def call(self,name,*args):
        b = self.board
        address = next(a for a,n in LoopbackNetwork.APIS.items() if n == name)
        for register,value in zip((UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3),(*args,0,0,0,0)):
            b.uc.reg_write(register,value)
        b.uc.reg_write(UC_ARM_REG_SP,0x4000c000)
        b.uc.reg_write(UC_ARM_REG_LR,0x4000fff0)
        result = b.run(100,2,entry=address|1,until=0x4000fff0,report=False)
        self.assertEqual(result['stop']['reason'],'requested end address',result['stop'])
        return b.uc.reg_read(UC_ARM_REG_R0)

    def connect(self):
        fd = self.call('socket',2,1,0,1)
        self.assertEqual(self.call('ioctl',fd,0x1014,0),0)
        self.board.uc.mem_write(0x04002000,bytes.fromhex('000200507f000001')+bytes(8))
        status = self.call('connect',fd,0x04002000,16)
        if status == 0xffffffff:
            self.assertEqual(self.call('error',fd),22)
            sock = self.backend.sockets[fd]['socket']
            self.assertTrue(select.select([],[sock],[],2)[1])
            status = self.call('connect',fd,0x04002000,16)
        self.assertEqual(status,0)
        return fd

    def test_dns_layout_is_consumed_by_original_cms_ipv4_formatter(self):
        b = self.board
        b.uc.mem_write(0x04001000,b'a.aa\0')
        hostent = self.call('dns',0x04001000)
        self.assertEqual(hostent,LoopbackNetwork.SCRATCH)
        self.assertEqual(bytes(b.uc.mem_read(hostent+8,8)),bytes.fromhex('0000000200000004'))
        b.uc.reg_write(UC_ARM_REG_R0,hostent)
        b.uc.reg_write(UC_ARM_REG_R1,0x04003000)
        b.uc.reg_write(UC_ARM_REG_LR,0x4000fff0)
        result = b.run(100000,2,entry=0x59adb5,until=0x4000fff0,report=False)
        self.assertEqual(result['stop']['reason'],'requested end address')
        self.assertEqual(b.cstring(0x04003000),'127.0.0.1')

    def test_real_http_partial_reads_eof_and_closed_handle(self):
        b = self.board
        fd = self.connect()
        sock = self.backend.sockets[fd]['socket']
        b.uc.mem_write(0x04004000,b'!'*64)
        # The server has no request yet, so this must not block or overwrite RAM.
        self.assertEqual(self.call('recv',fd,0x04004000,16,0),0xffffffff)
        self.assertEqual(self.call('error',fd),6)
        self.assertEqual(bytes(b.uc.mem_read(0x04004000,64)),b'!'*64)
        request = b'GET /next HTTP/1.0\r\nHost: a.aa\r\n\r\n'
        for piece in (request[:5],request[5:]):
            b.uc.mem_write(0x04003000,piece)
            self.assertEqual(self.call('send',fd,0x04003000,len(piece),0),len(piece))
        chunks = []
        for _ in range(300):
            self.assertTrue(select.select([sock],[],[],2)[0])
            count = self.call('recv',fd,0x04004000,3,0)
            self.assertNotEqual(count,0xffffffff)
            if count == 0:
                break
            self.assertLessEqual(count,3)
            chunks.append(bytes(b.uc.mem_read(0x04004000,count)))
            self.assertEqual(bytes(b.uc.mem_read(0x04004003,61)),b'!'*61)
        else:
            self.fail('HTTP response never reached EOF')
        response = b''.join(chunks)
        headers,body = response.split(b'\r\n\r\n',1)
        self.assertIn(b'200 OK',headers)
        self.assertIn(self.fixture.token.encode(),body)
        self.assertIn(b'Page: /next',body)
        self.assertEqual(bytes(self.backend.sent),request)
        self.assertEqual(bytes(self.backend.received),response)
        self.assertEqual(self.fixture.requests[0]['host'],'a.aa')
        self.assertEqual(self.call('close',fd),0)
        self.assertEqual(self.call('recv',fd,0x04004000,3,0),0xffffffff)

    def test_unknown_route_and_dns_pause_before_host_connection(self):
        b = self.board
        fd = self.call('socket',2,1,0,1)
        b.uc.mem_write(0x04002000,bytes.fromhex('000201bb08080808')+bytes(8))
        for register,value in ((UC_ARM_REG_R0,fd),(UC_ARM_REG_R1,0x04002000),(UC_ARM_REG_R2,16)):
            b.uc.reg_write(register,value)
        result = b.run(100,2,entry=0x2cfb6f,report=False)
        self.assertEqual(result['stop']['detail'],'Destination outside laboratory route')
        self.assertFalse(self.backend.sockets[fd]['connecting'])
        b.uc.mem_write(0x04001000,b'unmapped.invalid\0')
        b.uc.reg_write(UC_ARM_REG_R0,0x04001000)
        result = b.run(100,2,entry=0x2cf9c7,report=False)
        self.assertEqual(result['stop']['detail'],'Unmapped laboratory DNS name')
        self.assertEqual(self.fixture.requests,[])
        b.uc.reg_write(UC_ARM_REG_R0,fd)
        result = b.run(100,2,entry=0x2cfb19,report=False)
        self.assertEqual(result['stop']['detail'],'Unimplemented guest socket API')

    def test_refused_host_connection_is_an_error_instead_of_success(self):
        # Winsock can leave a connection to a bound/non-listening port pending
        # for a long time. Supply its explicit refusal result deterministically;
        # successful connect/send/recv are covered with a real server above.
        with patch('emulator.host_network.socket.socket') as constructor:
            constructor.return_value.connect_ex.return_value = 10061
            fd = self.call('socket',2,1,0,1)
            self.board.uc.mem_write(0x04002000,bytes.fromhex('000200507f000001')+bytes(8))
            result = self.call('connect',fd,0x04002000,16)
            self.assertEqual(result,0xffffffff)
            self.assertEqual(self.call('error',fd),1)
            self.assertTrue(any(e['kind']=='host.error' for e in self.backend.events))
            self.assertEqual(self.fixture.requests,[])

    def test_cleanup_closes_sockets_and_snapshots_cannot_drop_the_backend(self):
        fd = self.call('socket',2,1,0,1)
        sock = self.backend.sockets[fd]['socket']
        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(SnapshotError,'experimental_network'):
                save_snapshot(self.board,Path(directory)/'network.b220state')
        self.backend.close()
        self.backend.close()
        self.assertEqual(sock.fileno(),-1)
        with self.assertRaises(UcError):
            self.board.uc.mem_read(LoopbackNetwork.SCRATCH,4)


if __name__ == '__main__':
    unittest.main()
