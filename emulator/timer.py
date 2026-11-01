"""Inferred 32 kHz countdown timers, with deterministic virtual time."""


class CountdownTimer:
    def __init__(self,line,blocks_per_ms=128,frequency=32768):
        self.line = line
        self.denominator = blocks_per_ms*1000
        self.frequency = frequency
        self.load = 0
        self.control = 0
        self.int_enable = 0
        self.raw = False
        self.epoch = 0
        self.expirations = 0
        self.expired = False

    @property
    def pending(self):
        return bool(self.raw and self.int_enable)

    @property
    def next_event(self):
        if not self.control&0x80 or not self.load or self.expired:
            return None
        return self.epoch+(self.load*self.denominator+self.frequency-1)//self.frequency

    @property
    def next_event(self):
        if not self.control&0x80 or not self.load or self.expired:
            return None
        return self.epoch+(self.load*self.denominator+self.frequency-1)//self.frequency

    def value(self,blocks):
        if not self.control&0x80:
            return self.load
        ticks = (blocks-self.epoch)*self.frequency//self.denominator
        if self.control&0x40 and self.load:
            return self.load-ticks%self.load
        return max(0,self.load-ticks)

    def tick(self,blocks):
        if not self.control&0x80 or not self.load:
            return
        ticks = (blocks-self.epoch)*self.frequency//self.denominator
        if ticks >= self.load and not self.expired:
            self.raw,self.expired = True,True
            self.expirations += 1
            if self.control&0x40:
                # Re-arm the periodic edge without accumulating a backlog.
                self.epoch = blocks
                self.expired = False

    def read(self,offset,blocks):
        self.tick(blocks)
        return {0:self.load,4:self.value(blocks),8:self.control,
                12:self.int_enable|(2 if self.raw else 0)|(4 if self.pending else 0)}[offset]

    def write(self,offset,value,blocks):
        if offset == 0:
            self.load = value
            self.epoch = blocks
            self.expired = False
        elif offset == 8:
            if value&0x80 and not self.control&0x80:
                self.epoch = blocks
                self.expired = False
            self.control = value
        elif offset == 12:
            self.int_enable = value&1
            if value&8:
                self.raw = False
        else:
            raise ValueError('Write to countdown-timer value register')

    def report(self,blocks):
        return dict(line=self.line,load=self.load,value=self.value(blocks),control=hex(self.control),
                    irq_enabled=bool(self.int_enable),raw=self.raw,expirations=self.expirations)
