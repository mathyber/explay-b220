"""RTC time counters and shadow-write acknowledgements inferred from PS 0x18e5da.

No wall clock, periodic IRQs, or alarm firing yet. Update IRQ7 is level-sensitive.
"""


class Rtc:
    def __init__(self,blocks_per_ms=128):
        self.blocks_per_second = blocks_per_ms*1000
        self.seconds = 0
        self.epoch = 0
        self.enable = 0
        self.raw = 0
        self.registers = {}
        self.updates = 0

    @property
    def pending(self):
        return bool(self.raw&self.enable)

    def fields(self,blocks):
        value = self.seconds+(blocks-self.epoch)//self.blocks_per_second
        return (value%60,(value//60)%60,(value//3600)%24,value//86400)

    def read(self,offset,blocks):
        if offset in (0,4,8,12):
            return self.fields(blocks)[offset//4]
        return {0x30:self.enable,0x34:self.raw,0x38:0}.get(offset,self.registers.get(offset,0))

    def write(self,offset,value,blocks):
        if offset == 0x30:
            self.enable = value
        elif offset == 0x38:
            self.raw &= ~value
        elif 0x10 <= offset <= 0x1c and offset%4 == 0:
            index = (offset-0x10)//4
            values = list(self.fields(blocks))
            values[index] = value
            self.seconds = values[0]+60*values[1]+3600*values[2]+86400*values[3]
            self.epoch = blocks
            self.raw |= 1<<(8+index)
            self.updates += 1
            self.registers[offset] = value
        elif 0x20 <= offset <= 0x2c and offset%4 == 0:
            self.registers[offset] = value
            self.raw |= 1<<(12+(offset-0x20)//4)
            self.updates += 1
        elif offset in (0,4,8,12,0x34):
            raise ValueError('Write to read-only RTC register')
        else:
            raise ValueError('Unknown RTC register')

    def report(self,blocks):
        return dict(time_fields=self.fields(blocks),enable=hex(self.enable),raw=hex(self.raw),updates=self.updates,
                    limitations='No wall-clock synchronization, periodic RTC IRQ, or alarm firing')
