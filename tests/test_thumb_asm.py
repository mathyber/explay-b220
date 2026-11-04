"""The Thumb assembler against Capstone's disassembler."""
import unittest

from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_BIG_ENDIAN

from tools.thumb_asm import Thumb, bl_bytes


def disassemble(data, base):
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_BIG_ENDIAN)
    return [f'{i.mnemonic} {i.op_str}'.strip() for i in md.disasm(data, base)]


class ThumbTests(unittest.TestCase):
    def test_instructions_labels_and_literals(self):
        code = Thumb(0x1000)
        code.push(4, 'lr')
        code.movs(4, 0)
        code.label('loop')
        code.cmp(4, 7)
        code.b('next', 'eq')
        code.ldr_lit(1, 0x0429d94c)
        code.lsls(0, 4, 2)
        code.ldr(0, 1, 0)
        code.mov(2, 6)
        code.bl(0x2000)
        code.label('next')
        code.adds(4, 1)
        code.subs(4, 1)
        code.cmp(4, 18)
        code.b('loop', 'lo')
        code.b('end')
        code.nop()
        code.label('end')
        code.pop(4, 'pc')
        code.bx_lr()
        data = code.assemble()
        self.assertEqual(disassemble(data[:-4], 0x1000), [
            'push {r4, lr}', 'movs r4, #0', 'cmp r4, #7', 'beq #0x1014', 'ldr r1, [pc, #0x18]', 'lsls r0, r4, #2',
            'ldr r0, [r1, r0]', 'adds r2, r6, #0', 'bl #0x2000', 'adds r4, #1', 'subs r4, #1', 'cmp r4, #0x12',
            'blo #0x1004', 'b #0x1020', 'mov r8, r8', 'pop {r4, pc}', 'bx lr'])
        self.assertEqual(data[-4:], bytes.fromhex('0429d94c'))
        self.assertEqual(code.addresses, {'loop': 0x1004, 'next': 0x1014, 'end': 0x1020})

    def test_memory_and_data_processing(self):
        code = Thumb(0x2000)
        code.ldr_sp(1, 0x9c)
        code.str_sp(0, 0x24)
        code.add_sp(3, 20)
        code.ldr_imm(1, 3, 0x60)
        code.ldrh_imm(1, 5, 0)
        code.strh_imm(0, 5, 2)
        code.ldrb_reg(0, 1, 2)
        code.ldrsh_reg(0, 3, 0)
        code.lsrs(0, 0, 28)
        code.add3(5, 5, 0)
        code.sub3(0, 3, 0)
        code.alu('muls', 0, 1)
        code.alu('orrs', 0, 4)
        code.alu('ands', 1, 7)
        code.alu('cmp', 1, 2)
        code.alu('lsls', 0, 2)
        self.assertEqual(disassemble(code.assemble(), 0x2000), [
            'ldr r1, [sp, #0x9c]', 'str r0, [sp, #0x24]', 'add r3, sp, #0x14', 'ldr r1, [r3, #0x60]',
            'ldrh r1, [r5]', 'strh r0, [r5, #2]', 'ldrb r0, [r1, r2]', 'ldrsh r0, [r3, r0]', 'lsrs r0, r0, #0x1c',
            'adds r5, r5, r0', 'subs r0, r3, r0', 'muls r0, r1, r0', 'orrs r0, r4', 'ands r1, r7', 'cmp r1, r2',
            'lsls r0, r2'])
        with self.assertRaises(ValueError):
            Thumb(0).ldr_imm(0, 1, 0x80)

    def test_bl_reach_is_checked(self):
        self.assertEqual(disassemble(bl_bytes(0x278884, 0x86bf0), 0x278884), ['bl #0x86bf0'])
        with self.assertRaises(ValueError):
            bl_bytes(0, 0x500000)


if __name__ == '__main__':
    unittest.main()
