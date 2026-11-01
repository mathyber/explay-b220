"""Network observation remains bounded and does not change guest execution."""
import gc
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from unicorn.arm_const import UC_ARM_REG_PC,UC_ARM_REG_LR,UC_ARM_REG_R0,UC_ARM_REG_R1
from emulator.board import Board
from emulator.network_trace import NetworkTrace,POINTS
from emulator.snapshot import cpu_state,save_snapshot,load_snapshot


class NetworkTraceTests(unittest.TestCase):
    def tearDown(self):
        gc.collect()

    def test_real_hook_observes_without_changing_registers_or_clock_and_detaches(self):
        boards = [Board(explore=True),Board(explore=True)]
        for board in boards:
            board.uc.mem_write(0x4000e000,bytes.fromhex('e2800001e12fff1e'))
            board.uc.reg_write(UC_ARM_REG_LR,0x4000fff0)
        with patch.dict(POINTS,{0x4000e000:'probe'},clear=True):
            trace = NetworkTrace(boards[1])
        for board in boards:
            result = board.run(10,2,entry=0x4000e000,until=0x4000fff0)
            self.assertEqual(result['stop']['reason'],'requested end address')
        self.assertEqual(cpu_state(boards[0]),cpu_state(boards[1]))
        self.assertEqual(boards[0].block_serial,boards[1].block_serial)
        self.assertEqual(trace.counts['probe'],1)
        self.assertEqual(trace.events[0]['args'][0],'0x0')
        trace.close()
        boards[1].run(10,2,entry=0x4000e000,until=0x4000fff0)
        self.assertEqual(trace.counts['probe'],1)

    def test_network_logs_are_observed_after_general_trace_fills(self):
        board = Board()
        trace = NetworkTrace(board)
        board.logs = [{}]*3000
        image = bytes(board.uc.mem_read(0,board.flash.source_length))
        pointer = image.index(b'mmiwap.c:ProcessGprsActiveCnf() OpenWin NETLINK_SUCCESS')
        board.uc.reg_write(UC_ARM_REG_R0,pointer)
        board.uc.reg_write(UC_ARM_REG_R1,7)
        before = cpu_state(board)
        trace._log(board.uc,0x48e11a,2,None)
        self.assertEqual(cpu_state(board),before)
        self.assertEqual(len(board.logs),3000)
        self.assertEqual(trace.events[0]['kind'],'log')
        self.assertEqual(trace.events[0]['args'][0],'0x7')
        self.assertIn('NETLINK_SUCCESS',trace.events[0]['format'])
        trace.close()

    def test_limit_and_snapshot_exclude_observer_resources(self):
        board = Board()
        trace = NetworkTrace(board,limit=2)
        for name in ('a','b','c'):
            trace.mark(name)
        self.assertEqual(len(trace.events),2)
        self.assertEqual(trace.dropped,1)
        with TemporaryDirectory() as directory:
            path = Path(directory)/'test.b220state'
            save_snapshot(board,path)
            restored = load_snapshot(path)
            self.assertEqual(cpu_state(restored),cpu_state(board))
            self.assertFalse(any('trace' in name for name in vars(restored)))
        trace.close()


if __name__ == '__main__':
    unittest.main()
