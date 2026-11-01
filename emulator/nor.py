"""Observed x16 NOR subset: ID, configuration, status, word program, sector erase.

Operations complete immediately; PS bytes are protected and files never change.
Unsupported commands stop exploration instead of silently succeeding.
"""
from collections import deque


class NorFlash:
    def __init__(self, image, size=0x800000, manufacturer=0x20, device=0x88c0,trace_limit=4096,
                 parameter_start=None,parameter_size=0x2000):
        if len(image) > size or size <= 0 or size&0xffff:
            raise ValueError('NOR capacity must fit the image and contain whole 64 KiB sectors')
        if parameter_start is not None and (parameter_start&0xffff or not 0 <= parameter_start < size or
                parameter_size <= 0 or parameter_size&(parameter_size-1) or
                parameter_size > 0x10000 or (size-parameter_start)%parameter_size):
            raise ValueError('Invalid parameter-block geometry')
        self.parameter_start = parameter_start
        self.parameter_size = parameter_size
        self.array = bytearray(image) + bytearray(b'\xff'*(size-len(image)))
        self.source_length = len(image)
        self.manufacturer = manufacturer
        self.device = device
        self.mode = 'array'
        self.unlock = 0
        self.configuration = None
        self.configuration_address = None
        self.events = []
        self.trace_limit = trace_limit
        self.recent_events = deque(maxlen=trace_limit)
        self.event_count = 0
        self.last_event = None
        self.pending = None
        self.status = 0x80
        self.programmed_words = 0
        self.erased_blocks = 0
        self.changed = []
        self.last_erase = None
        self.suspend_requested = False

    def erase_region(self,address):
        length = self.parameter_size if self.parameter_start is not None and address >= self.parameter_start else 0x10000
        return address&~(length-1),length

    def write(self, address, size, value):
        self.event_count += 1
        self.last_event = dict(index=self.event_count,address=hex(address),size=size,value=hex(value),previous_mode=self.mode)
        if self.trace_limit is None or len(self.events) < self.trace_limit:
            self.events.append(self.last_event)
        else:
            self.recent_events.append(self.last_event)
        if size != 2 or address&1 or not 0 <= address <= len(self.array)-size:
            return False
        command = value & 0xffff
        if self.pending and self.pending[0] == 'program':
            target = self.pending[1]
            self.pending = None
            if address != target or address < self.source_length:
                return False
            old = int.from_bytes(self.array[address:address+2],'big')
            self.array[address:address+2] = (old & command).to_bytes(2,'big')
            self.changed.append((address,2))
            self.status = 0x80 if old & command == command else 0x90
            self.mode = 'status'
            self.programmed_words += 1
            return True
        if self.pending and self.pending[0] == 'erase':
            target = self.pending[1]
            self.pending = None
            start,length = self.erase_region(address)
            if command != 0xd0 or address != target or start < self.source_length:
                return False
            self.array[start:start+length] = b'\xff'*length
            self.changed.append((start,length))
            self.last_erase = (start,length)
            self.suspend_requested = False
            self.mode,self.status = 'status',0x80
            self.erased_blocks += 1
            return True
        if command in (0xf0,0xff):
            self.mode, self.unlock = 'array', 0
            self.configuration_address = None
            self.pending = None
            return True
        # Observed firmware routine 0x724604: 60 and 03 to the address
        # encoding the read-mode configuration, followed by FF at the base.
        if self.configuration_address == address and command == 3:
            self.configuration = address >> 1
            self.configuration_address = None
            return True
        if self.configuration_address == address and command == 0xd0:
            self.configuration_address = None
            self.mode, self.status = 'status', 0x80
            return True
        if command == 0x60:
            self.configuration_address = address
            return True
        if command in (0x40,0x41):
            self.pending = ('program',address)
            return True
        if command == 0x20:
            self.pending = ('erase',address)
            return True
        if command == 0x50:
            self.status, self.mode = 0x80, 'status'
            return True
        if command == 0x70:
            self.mode = 'status'
            return True
        if command == 0xb0 and self.last_erase and self.last_erase[0] <= address < self.last_erase[0]+self.last_erase[1]:
            # With immediate completion there is no active erase left to suspend.
            self.suspend_requested = True
            self.mode,self.status = 'status',0x80
            return True
        if command == 0xd0 and self.suspend_requested and self.last_erase and self.last_erase[0] <= address < self.last_erase[0]+self.last_erase[1]:
            self.suspend_requested = False
            self.mode,self.status = 'status',0x80
            return True
        if self.unlock == 0 and address == 0xaaa and command == 0xaa:
            self.unlock = 1
            return True
        if self.unlock == 1 and address == 0x554 and command == 0x55:
            self.unlock = 2
            return True
        if self.unlock == 2 and address == 0xaaa and command == 0x90:
            self.mode, self.unlock = 'autoselect', 0
            return True
        self.unlock = 0
        return False

    def read(self,address,size):
        if size <= 0 or not 0 <= address <= len(self.array)-size:
            raise ValueError('NOR read outside virtual capacity')
        if self.mode == 'array':
            return self.array[address:address+size]
        if self.mode == 'status':
            return bytes(0 if not a&1 else self.status for a in range(address,address+size))
        words = {0: self.manufacturer, 2: self.device, 0x1c: 0, 0x1e: 0}
        return bytes((words.get((a&~1),0xffff) >> (0 if a&1 else 8)) & 255 for a in range(address,address+size))
