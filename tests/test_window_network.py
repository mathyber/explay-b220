"""Window lifecycle: deferred setup, in-flight PDP, detach and URL ownership."""
import gc
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from unicorn.arm_const import UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3
from emulator.board import Board
from emulator.host_network import LoopbackNetwork
from emulator.interactive import InteractiveSession
from emulator.run import save_outputs
from emulator.window_network import WindowNetwork


class WindowNetworkTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.board = b = Board(explore=True)
        for slot in range(3):
            b.uc.mem_write(0x0433287c+slot*0xcc,(7).to_bytes(4,'big'))
        b.uc.mem_write(0x04332870,(2).to_bytes(4,'big'))
        b.uc.mem_write(0x0433613c,bytes.fromhex('01010100'))
        b.uc.mem_write(0x0434c4c0,b'\xff\xff')
        self.controller = WindowNetwork(b,Path(self.temp.name)/'network.json',
            backend_factory=lambda board,service:LoopbackNetwork(board,('127.0.0.1',12345),service=service))

    def tearDown(self):
        self.controller.close()
        del self.controller,self.board
        gc.collect()
        self.temp.cleanup()

    def attach(self):
        self.board.last_window_id = 0x4001b
        self.controller.configure(enabled=True)

    def test_arm_before_browser_defers_all_ram_changes(self):
        before = bytes(self.board.ram)
        self.controller.configure(enabled=True)
        self.assertEqual(self.controller.status()['state'],'waiting_browser')
        self.assertEqual(bytes(self.board.ram),before)
        self.assertFalse(hasattr(self.board,'experimental_network'))
        self.board.last_window_id = 0x40001
        self.controller.update()
        self.assertEqual(self.controller.status()['state'],'ready')
        self.assertEqual(self.controller.service.provisioning['apn'],'internet.mts.ru')

    def test_disable_closes_owned_sockets_and_can_reenable(self):
        self.attach()
        backend = self.controller.backend
        self.controller.service.phase = 'active'
        uc = self.board.uc
        for reg,value in zip((UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3),(2,1,0,1)):
            uc.reg_write(reg,value)
        backend._call(uc,0,0,'socket')
        sock = next(iter(backend.sockets.values()))['socket']
        self.controller.configure(enabled=False)
        self.assertEqual(sock.fileno(),-1)
        self.assertFalse(backend.sockets)
        self.assertEqual(self.controller.status()['state'],'off')
        self.controller.configure(enabled=True)
        self.assertEqual(self.controller.status()['state'],'ready')
        self.assertIs(backend,self.controller.backend)
        with self.assertRaisesRegex(ValueError,'Restart'):
            self.controller.configure(enabled=True,plmn='25002')

    def test_service_change_waits_for_native_confirmation(self):
        self.attach()
        service = self.controller.service
        service.phase,service.pending_result = 'confirming',0
        self.controller.configure(enabled=True,registered=False)
        self.assertEqual(self.controller.status()['state'],'changing')
        self.assertFalse(self.controller.backend.enabled)
        self.assertTrue(service.registered)
        # A guest CNF can be consumed in the same slice as another socket API.
        # Its temporary 'active' phase must not reopen host access.
        service.phase = 'active'
        uc = self.board.uc
        for reg,value in zip((UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3),(2,1,0,1)):
            uc.reg_write(reg,value)
        self.controller.backend._call(uc,0,0,'socket')
        self.assertFalse(self.controller.backend.sockets)
        service.phase = 'confirming'
        pointer = 0x04001000
        self.board.uc.mem_write(pointer,bytes(8))
        self.board.uc.reg_write(UC_ARM_REG_R0,pointer)
        service._confirm(self.board.uc,0x599c14,0,None)
        self.controller.update()
        self.assertFalse(service.registered)
        self.assertEqual(service.phase,'ready')

    def test_later_url_does_not_overwrite_retained_guest_input(self):
        self.attach()
        controller = self.controller
        controller.open_url('https://example.org/first')
        first = controller.url_input
        first._input(self.board.uc,0,0,None)
        controller.update()
        controller.open_url('http://example.org/second')
        second = controller.url_input
        self.assertNotEqual(first.ADDRESS,second.ADDRESS)
        self.assertEqual(self.board.cstring(first.ADDRESS,100),'https://example.org/first')
        self.assertEqual(self.board.cstring(second.ADDRESS,100),'http://example.org/second')
        controller.write_report()
        self.assertEqual(len(json.loads(controller.report_path.read_text())['url_inputs']),2)

    def test_interactive_command_channel_reports_invalid_config_and_survives(self):
        command_path = Path(self.temp.name)/'commands.jsonl'
        args = SimpleNamespace(commands=command_path,session_status=Path(self.temp.name)/'status.json',out='test.json')
        session = InteractiveSession(self.board,args,lambda *_:None)
        try:
            command_path.write_text(json.dumps(dict(action='network',enabled=True,plmn='oops'))+'\n')
            session.commands()
            self.assertIn('PLMN',session.last_error)
            with command_path.open('a') as stream:
                stream.write(json.dumps(dict(action='network',enabled=True,plmn='25001'))+'\n')
            session.commands()
            self.assertIsNone(session.last_error)
            session.publish(force=True)
            status = json.loads(args.session_status.read_text())
            self.assertEqual(status['network']['state'],'waiting_browser')
            self.assertFalse(status['ready'])
        finally:
            session.network.close()

    def test_network_run_keeps_existing_ordinary_nor_file(self):
        self.attach()
        root = Path(self.temp.name)
        nor = root/'ordinary-nor.bin'
        nor.write_bytes(b'previous ordinary session')
        args = SimpleNamespace(save_flash=nor,out=root/'report.json')
        result = save_outputs(self.board,self.board.report(1,0,None),args,None)
        self.assertEqual(nor.read_bytes(),b'previous ordinary session')
        self.assertIn('flash_save_skipped',result)


if __name__ == '__main__':
    unittest.main()
