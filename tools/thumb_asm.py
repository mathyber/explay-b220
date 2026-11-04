"""A small Thumb (ARMv4T) assembler for firmware patches, big-endian like the B220 PS.

Only the instructions the patches need. Branch targets are labels or absolute
addresses; ldr_lit values go to a literal pool after the code.

    code = Thumb(0x86bf0)
    code.push(4, 'lr'); code.movs(4, 0)
    code.label('loop'); code.cmp(4, 7); code.b('next', 'eq')
    ...
    data = code.assemble()
"""
import struct

CONDITIONS = {'eq': 0, 'ne': 1, 'hs': 2, 'lo': 3, 'mi': 4, 'pl': 5, 'hi': 8, 'ls': 9, 'ge': 10, 'lt': 11, 'gt': 12, 'le': 13}


def bl_bytes(at, target):
    """The two halfwords of a Thumb BL at address at to target."""
    offset = target-(at+4)
    if not -0x400000 <= offset < 0x400000 or offset & 1:
        raise ValueError(f'BL from {at:#x} cannot reach {target:#x}')
    return struct.pack('>HH', 0xf000 | (offset >> 12) & 0x7ff, 0xf800 | (offset >> 1) & 0x7ff)


def register_list(registers):
    bits, extra = 0, 0
    for r in registers:
        if r in ('lr', 'pc'):
            extra = 1
        else:
            bits |= 1 << r
    return bits, extra


class Thumb:
    def __init__(self, base):
        if base & 1:
            raise ValueError('Code must start on a halfword')
        self.base = base
        self.items = []       # (size, encoder(address, labels, pool) -> bytes)
        self.labels = {}
        self.literals = []

    def _add(self, size, encode):
        self.items.append((size, encode))

    def _fixed(self, halfword):
        self._add(2, lambda at, labels, pool: struct.pack('>H', halfword))

    def label(self, name):
        self.items.append((0, name))

    def push(self, *registers):
        bits, lr = register_list(registers)
        self._fixed(0xb400 | lr << 8 | bits)

    def pop(self, *registers):
        bits, pc = register_list(registers)
        self._fixed(0xbc00 | pc << 8 | bits)

    def movs(self, rd, imm):
        self._fixed(0x2000 | rd << 8 | imm)

    def cmp(self, rn, imm):
        self._fixed(0x2800 | rn << 8 | imm)

    def adds(self, rd, imm):
        self._fixed(0x3000 | rd << 8 | imm)

    def subs(self, rd, imm):
        self._fixed(0x3800 | rd << 8 | imm)

    def mov(self, rd, rn):
        """adds rd, rn, #0 (low registers, sets flags)."""
        self._fixed(0x1c00 | rn << 3 | rd)

    def lsls(self, rd, rm, imm):
        self._fixed(imm << 6 | rm << 3 | rd)

    def ldr(self, rd, rn, rm):
        self._fixed(0x5800 | rm << 6 | rn << 3 | rd)

    def _offset(self, value, scale, limit):
        if value % scale or not 0 <= value//scale < limit:
            raise ValueError(f'Offset {value:#x} out of range')
        return value//scale

    def ldr_sp(self, rd, offset):
        self._fixed(0x9800 | rd << 8 | self._offset(offset, 4, 256))

    def str_sp(self, rd, offset):
        self._fixed(0x9000 | rd << 8 | self._offset(offset, 4, 256))

    def add_sp(self, rd, offset):
        """add rd, sp, #offset"""
        self._fixed(0xa800 | rd << 8 | self._offset(offset, 4, 256))

    def ldr_imm(self, rd, rn, offset):
        self._fixed(0x6800 | self._offset(offset, 4, 32) << 6 | rn << 3 | rd)

    def ldrh_imm(self, rd, rn, offset):
        self._fixed(0x8800 | self._offset(offset, 2, 32) << 6 | rn << 3 | rd)

    def strh_imm(self, rd, rn, offset):
        self._fixed(0x8000 | self._offset(offset, 2, 32) << 6 | rn << 3 | rd)

    def ldrb_reg(self, rd, rn, rm):
        self._fixed(0x5c00 | rm << 6 | rn << 3 | rd)

    def ldrsh_reg(self, rd, rn, rm):
        self._fixed(0x5e00 | rm << 6 | rn << 3 | rd)

    def lsrs(self, rd, rm, imm):
        self._fixed(0x0800 | imm << 6 | rm << 3 | rd)

    def add3(self, rd, rn, rm):
        """adds rd, rn, rm"""
        self._fixed(0x1800 | rm << 6 | rn << 3 | rd)

    def sub3(self, rd, rn, rm):
        """subs rd, rn, rm"""
        self._fixed(0x1a00 | rm << 6 | rn << 3 | rd)

    ALU = ('ands', 'eors', 'lsls', 'lsrs', 'asrs', 'adcs', 'sbcs', 'rors',
           'tst', 'negs', 'cmp', 'cmn', 'orrs', 'muls', 'bics', 'mvns')

    def alu(self, operation, rd, rs):
        """Two-register data processing: alu('muls', r0, r1) is muls r0, r1, r0."""
        self._fixed(0x4000 | self.ALU.index(operation) << 6 | rs << 3 | rd)

    def ldr_lit(self, rd, value):
        if value not in self.literals:
            self.literals.append(value)
        index = self.literals.index(value)

        def encode(at, labels, pool):
            offset = pool+index*4-((at+4) & ~3)
            if not 0 <= offset <= 1020:
                raise ValueError(f'Literal out of reach at {at:#x}')
            return struct.pack('>H', 0x4800 | rd << 8 | offset >> 2)
        self._add(2, encode)

    def b(self, target, condition=None):
        def encode(at, labels, pool):
            offset = (labels[target] if isinstance(target, str) else target)-(at+4)
            if condition is None:
                if not -2048 <= offset < 2048:
                    raise ValueError(f'B out of reach at {at:#x}')
                return struct.pack('>H', 0xe000 | (offset >> 1) & 0x7ff)
            if not -256 <= offset < 256:
                raise ValueError(f'B{condition} out of reach at {at:#x}')
            return struct.pack('>H', 0xd000 | CONDITIONS[condition] << 8 | (offset >> 1) & 0xff)
        self._add(2, encode)

    def bl(self, target):
        self._add(4, lambda at, labels, pool: bl_bytes(at, labels[target] if isinstance(target, str) else target))

    def bx_lr(self):
        self._fixed(0x4770)

    def nop(self):
        self._fixed(0x46c0)  # mov r8, r8

    def assemble(self):
        labels, at = {}, self.base
        for size, item in self.items:
            if size == 0:
                labels[item] = at
            at += size
        pool = (at+3) & ~3
        code, at = bytearray(), self.base
        for size, item in self.items:
            if size:
                code += item(at, labels, pool)
                at += size
        code += bytes(pool-at)
        for value in self.literals:
            code += struct.pack('>I', value)
        self.addresses = labels
        return bytes(code)
