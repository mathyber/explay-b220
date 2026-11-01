"""SC6600L keypad subset, inferred from PS 0x5b407a and 0x1f6e58.

One hardware event slot is serialized at IRQ boundaries. Matrix slots map to
virtual keys in the original table at PS 0x3dd46c, not in the host UI.
"""
from collections import deque


KEY_MATRIX = {
    'soft_left':0,'up':1,'soft_right':2,
    'left':8,'ok':9,'right':10,
    'down':17,'call_3':18,'call_2':24,'call_1':16,
    '1':3,'2':4,'3':5,'4':11,'5':12,'6':13,
    '7':19,'8':20,'9':21,'star':27,'0':28,'hash':29,
}


class Keypad:
    base = 0x87000000
    line = 8

    def __init__(self):
        self.registers = {}
        self.raw = 0
        self.key_status = 8
        self.queue = deque()
        self.held = set()
        self.events = []
        self.acknowledgements = 0
        self.power_down = False
        self.power_raw = 0

    @property
    def pending(self):
        return bool(self.raw&self.registers.get(4,0))

    def key(self,name,down):
        if not isinstance(name,str) or name not in (*KEY_MATRIX,'end'):
            raise ValueError(f'Unknown phone key: {name}')
        if bool(down) == (name in self.held):
            return False
        if len(self.queue) >= 128:
            raise ValueError('Keypad input queue is full')
        if down:
            self.held.add(name)
        else:
            self.held.remove(name)
        self.queue.append((name,bool(down)))
        return True

    def release_all(self):
        for name in tuple(self.held):
            self.held.remove(name)
            self.queue.append((name,False))

    def pump(self,blocks,gpio_enabled=0):
        if self.raw or self.power_raw or not self.queue:
            return
        if self.queue[0][0] == 'end':
            # Power/end is GPIO3, not a matrix key. The original GPIO IRQ6
            # callback at PS 0x3a7d68 produces native key 0x2002.
            if not gpio_enabled&8:
                return
            name,down = self.queue.popleft()
            self.power_down = down
            self.power_raw = 8
            if len(self.events) < 1000:
                self.events.append(dict(key=name,down=down,gpio=3,blocks=blocks))
            return
        if not self.registers.get(0,0)&1:
            return
        name,down = self.queue.popleft()
        matrix = KEY_MATRIX[name]
        row,col = matrix>>3,matrix&7
        self.key_status = row|(col<<4)|(0 if down else 8)
        self.raw = 1 if down else 0x10
        if len(self.events) < 1000:
            self.events.append(dict(key=name,down=down,matrix=matrix,blocks=blocks))

    def read(self,offset):
        if offset == 8:
            return self.raw&self.registers.get(4,0)
        if offset == 12:
            return self.raw
        if offset == 16:
            return 0  # W1C, RMW must not acknowledge unrelated pending bits.
        if offset == 0x2c:
            return self.key_status
        if offset in (0,4,0x14,0x18,0x1c,0x20,0x24,0x28):
            return self.registers.get(offset,0)
        raise ValueError(f'Unmodeled keypad register {offset:#x}')

    def write(self,offset,value):
        if offset == 0x10:
            self.raw &= ~value
            self.acknowledgements += 1
        elif offset in (0,4,0x14,0x18,0x1c,0x20,0x24,0x28):
            self.registers[offset] = value
        else:
            raise ValueError(f'Unmodeled keypad register write {offset:#x}')

    def report(self):
        return dict(raw=hex(self.raw),enabled=hex(self.registers.get(4,0)),
                    power_gpio=dict(pin=3,down=self.power_down,raw=self.power_raw),
                    held=sorted(self.held),queued=len(self.queue),events=self.events,
                    acknowledgements=self.acknowledgements,
                    limitations='Serialized single-slot matrix events; analog debounce/multi-key rollover not measured')
