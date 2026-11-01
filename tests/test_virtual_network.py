"""Experimental PDP service: ABI, guest ownership and explicit offline boundary."""
import gc
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from unicorn.arm_const import (UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,
                               UC_ARM_REG_PC,UC_ARM_REG_SP,UC_ARM_REG_LR)
from emulator.board import Board
from emulator.snapshot import SnapshotError,cpu_state,save_snapshot
from emulator.virtual_network import VirtualNetwork


class VirtualNetworkTests(unittest.TestCase):
    def setUp(self):
        self.board = b = Board(explore=True)
        b.last_window_id = 0x4001b
        # Recovered pre-experiment RAM cache values. Tests don't require a local
        # saved UI session; the getter and handler entry code are the original PS.
        for slot in range(3):
            b.uc.mem_write(0x0433287c+slot*0xcc,(7).to_bytes(4,'big'))
        b.uc.mem_write(0x04332870,(2).to_bytes(4,'big'))
        b.uc.mem_write(0x0433613c,bytes.fromhex('01010100'))
        b.uc.mem_write(0x0434c4c0,b'\xff\xff')
        self.network = None

    def tearDown(self):
        if self.network:
            self.network.close()
        del self.board
        gc.collect()

    def attach(self):
        self.network = VirtualNetwork(self.board)
        return self.network

    def test_setup_changes_ram_only_and_original_getter_keeps_other_slots_absent(self):
        b = self.board
        cpu,flash = cpu_state(b),bytes(b.flash.array)
        rom = bytes(b.uc.mem_read(0,b.flash.source_length))
        self.attach()
        self.assertEqual(cpu_state(b),cpu)
        self.assertEqual(bytes(b.flash.array),flash)
        self.assertEqual(bytes(b.uc.mem_read(0,b.flash.source_length)),rom)
        for slot,expected in ((0,0),(1,7),(2,7)):
            b.uc.reg_write(UC_ARM_REG_R0,slot)
            b.uc.reg_write(UC_ARM_REG_SP,0x4000c000)
            b.uc.reg_write(UC_ARM_REG_LR,0x4000fff0)
            result = b.run(1000,2,entry=0x4adf4d,until=0x4000fff0,report=False)
            self.assertEqual(result['stop']['reason'],'requested end address')
            self.assertEqual(b.uc.reg_read(UC_ARM_REG_R0),expected)

    def test_bad_layout_is_rejected_before_any_ram_or_cpu_mutation(self):
        b = self.board
        b.uc.mem_write(0x0434c4c0,b'\x12\x34')
        before = bytes(b.ram),cpu_state(b)
        with self.assertRaisesRegex(ValueError,'RAM layout'):
            self.attach()
        self.assertEqual((bytes(b.ram),cpu_state(b)),before)
        self.assertFalse(hasattr(b,'experimental_network'))

    def test_default_service_preserves_browser_profile_and_gateway(self):
        b = self.board
        before = bytes(b.uc.mem_read(0x0433613c,0x43b0))
        network = self.attach()
        self.assertEqual(bytes(b.uc.mem_read(0x0433613c,0x43b0)),before)
        self.assertFalse(network.report()['configures_http_profile'])

    def request(self,valid=True):
        b = self.board
        pointer = 0x04001000
        raw = bytearray(64)
        raw[:4] = bytes.fromhex('13f40040')
        raw[12:16] = (0x15).to_bytes(4,'big')
        raw[20:24] = (1 if valid else 2).to_bytes(4,'big')
        b.uc.mem_write(pointer,bytes(raw))
        for register,value in ((UC_ARM_REG_R0,0),(UC_ARM_REG_R1,0x15),(UC_ARM_REG_R2,pointer),
                               (UC_ARM_REG_SP,0x4000c000),(UC_ARM_REG_LR,0x4000fff1)):
            b.uc.reg_write(register,value)
        return pointer,bytes(raw)

    def test_handler_tail_calls_native_reply_without_touching_stack_or_request(self):
        b = self.board
        network = self.attach()
        pointer,raw = self.request()
        b.uc.mem_write(0x4000bf00,b'x'*256)
        result = b.run(100,2,entry=0x240253,until=0x53b044,report=False)
        self.assertEqual(result['stop']['reason'],'requested end address')
        self.assertEqual(b.uc.reg_read(UC_ARM_REG_PC),0x53b044)
        self.assertEqual(b.uc.reg_read(UC_ARM_REG_SP),0x4000c000)
        self.assertEqual(b.uc.reg_read(UC_ARM_REG_LR),0x4000fff1)
        self.assertEqual(b.uc.reg_read(UC_ARM_REG_R0),0)
        self.assertEqual(b.uc.reg_read(UC_ARM_REG_R1),0x15)
        self.assertEqual(b.uc.reg_read(UC_ARM_REG_R2),0)
        self.assertEqual(bytes(b.uc.mem_read(pointer,64)),raw)
        self.assertEqual(bytes(b.uc.mem_read(0x4000bf00,256)),b'x'*256)
        self.assertEqual(network.phase,'confirming')

    def test_unknown_context_stops_instead_of_fabricating_success(self):
        b = self.board
        network = self.attach()
        pointer,raw = self.request(valid=False)
        result = b.run(100,2,entry=0x240253,report=False)
        self.assertEqual(result['stop']['operation'],'unsupported PDP activation request')
        self.assertEqual(network.phase,'ready')
        self.assertEqual(b.uc.reg_read(UC_ARM_REG_R2),pointer)
        self.assertEqual(bytes(b.uc.mem_read(pointer,64)),raw)

    def test_dns_stops_before_native_execution_and_hle_state_cannot_be_lost_in_snapshot(self):
        b = self.board
        network = self.attach()
        network.phase = 'confirming'
        network.pending_result = 0
        b.uc.mem_write(0x04002000,bytes(8))
        b.uc.reg_write(UC_ARM_REG_R0,0x04002000)
        network._confirm(b.uc,0x599c14,2,None)
        self.assertEqual(network.phase,'active')
        b.uc.mem_write(0x04003000,b'example.invalid\0')
        b.uc.reg_write(UC_ARM_REG_R0,0x04003000)
        b.uc.reg_write(UC_ARM_REG_SP,0x4000c000)
        b.uc.reg_write(UC_ARM_REG_LR,0x4000fff1)
        before = bytes(b.ram)
        result = b.run(1000,2,entry=0x2cf9c7,report=False)
        self.assertEqual(result['stop']['operation'],'DNS backend not implemented')
        self.assertEqual(result['stop']['name'],'example.invalid')
        self.assertEqual(bytes(b.ram),before)
        self.assertEqual(b.uc.reg_read(UC_ARM_REG_PC),0x2cf9c6)
        with TemporaryDirectory() as directory:
            for closed in (False,True):
                if closed:
                    network.close()
                with self.assertRaisesRegex(SnapshotError,'experimental_network'):
                    save_snapshot(b,Path(directory)/'unsupported.b220state')
                self.assertEqual(list(Path(directory).iterdir()),[])

    def test_sim_home_plmn_uses_firmware_accounts_without_forcing_http(self):
        b = self.board
        before = cpu_state(b),bytes(b.flash.array)
        self.network = VirtualNetwork(b,home_plmn='25001')
        report = self.network.report()
        self.assertEqual(report['native_sim_provisioning']['account_count'],2)
        self.assertEqual(report['native_sim_provisioning']['apn'],'internet.mts.ru')
        self.assertFalse(report['configures_http_profile'])
        self.assertEqual(cpu_state(b),before[0])
        self.assertEqual(bytes(b.flash.array),before[1])

    def test_registration_loss_rejects_pdp_through_native_reply_helper(self):
        b = self.board
        network = self.attach()
        network.set_service(registered=False)
        self.request()
        result = b.run(100,2,entry=0x240253,until=0x53b044,report=False)
        self.assertEqual(result['stop']['reason'],'requested end address')
        self.assertEqual(b.uc.reg_read(UC_ARM_REG_R2),0x66)
        b.uc.mem_write(0x04002000,(0x66).to_bytes(4,'big')+bytes(4))
        b.uc.reg_write(UC_ARM_REG_R0,0x04002000)
        network._confirm(b.uc,0x599c14,2,None)
        self.assertEqual(network.phase,'ready')
        self.assertFalse(network.registered)
        network.set_service(sim_present=False)
        self.assertEqual(int.from_bytes(b.uc.mem_read(0x0433287c,4),'big'),7)
        with self.assertRaises(ValueError):
            network.set_service(registered=True)
        self.assertFalse(network.sim_present)

    def test_unknown_sim_operator_does_not_get_a_forced_http_profile(self):
        self.network = VirtualNetwork(self.board,home_plmn='99999')
        self.assertEqual(self.network.provisioning['account_count'],0)
        self.assertEqual(self.network.provisioning['connection_type'],0)

    def test_sim_removal_closes_host_transport(self):
        from emulator.host_network import InternetNetwork
        b = self.board
        network = self.attach()
        network.phase = 'active'
        backend = InternetNetwork(b,network)
        try:
            b.uc.reg_write(UC_ARM_REG_R0,2)
            b.uc.reg_write(UC_ARM_REG_R1,1)
            from unicorn.arm_const import UC_ARM_REG_R2,UC_ARM_REG_R3
            b.uc.reg_write(UC_ARM_REG_R2,0)
            b.uc.reg_write(UC_ARM_REG_R3,1)
            b.uc.reg_write(UC_ARM_REG_LR,0x4000fff0)
            result = b.run(100,2,entry=0x2cfb33,until=0x4000fff0,report=False)
            self.assertEqual(result['stop']['reason'],'requested end address')
            sock = next(iter(backend.sockets.values()))['socket']
            network.set_service(sim_present=False)
            self.assertEqual(sock.fileno(),-1)
            self.assertEqual(backend.sockets,{})
            self.assertEqual(network.phase,'absent')
        finally:
            backend.close()


if __name__ == '__main__':
    unittest.main()
