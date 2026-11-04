"""RTC time counters, shadow-write acknowledgements and tick/alarm IRQs (PS 0x18e5da..0x18e930).

Counters: +0 seconds, +4 minutes, +8 hours, +0xc days since 1980-01-01 (day 11688 is
2012-01-01, the firmware default). Raw/enable bits (PS 0x18e83c enables 1<<type):
bit0 second, bit1 minute (IRQ handler 0x18e8b8 sends 0x74 to task 7), bit2 hour,
bit3 day (0x73), bit4 alarm (0x72; the handler disables it), bits8..11 time shadow
written, bits12..15 alarm shadow written. Update IRQ7 is level-sensitive.

Time runs on the deterministic block clock. follow() (interactive window) keeps it on
the host's local wall clock; a time the firmware sets (Settings) is kept as an offset.
"""
from datetime import datetime

EPOCH = datetime(1980,1,1)
DAY = 86400
DEFAULT_DAY = 11688   # 2012-01-01, the firmware's factory date (MMI literal at 0x1cf240)


def host_seconds():
    """Local wall-clock time in RTC seconds (fractional)."""
    return (datetime.now()-EPOCH).total_seconds()


class Rtc:
    def __init__(self,blocks_per_ms=128):
        self.blocks_per_second = blocks_per_ms*1000
        self.seconds = 0
        self.epoch = 0
        self.enable = 0
        self.raw = 0
        self.registers = {}
        self.updates = 0
        self.last = 0              # counter value at the previous tick()
        self.time_writes = 0
        self.host_offset = None    # None: block clock only; else seconds added to host time
        self.followed_writes = 0
        self.host_base = 0         # host time (microseconds; snapshots hold ints) at the last follow(), and its block
        self.host_block = 0
        self.staged = {}           # host clock: time fields written before the day
        self.ignored_resets = 0
        self.after_reset = False
        self.ticks = 0

    @property
    def pending(self):
        return bool(self.raw&self.enable)

    def value(self,blocks):
        return self.seconds+(blocks-self.epoch)//self.blocks_per_second

    def fields(self,blocks):
        value = self.value(blocks)
        return (value%60,(value//60)%60,(value//3600)%24,value//DAY)

    def alarm(self):
        r = self.registers
        return r.get(0x20,0)+60*r.get(0x24,0)+3600*r.get(0x28,0)+DAY*r.get(0x2c,0)

    def tick(self,blocks):
        """Raise second/minute/hour/day/alarm bits for counter boundaries crossed since the last call."""
        value = self.value(blocks)
        last,self.last = self.last,value
        if value <= last:
            return
        bits = 1
        if value//60 != last//60:
            bits |= 2
        if value//3600 != last//3600:
            bits |= 4
        if value//DAY != last//DAY:
            bits |= 8
        if last < self.alarm() <= value and value-last <= 120:  # not across a clock jump
            bits |= 0x10
        self.raw |= bits
        self.ticks += 1

    def follow(self,blocks,host):
        """Put the counters on host time (+ the offset of a time the firmware has set).

        host_offset None (re)starts on plain host time; the next tick() then raises the
        minute/hour/day bits, so the firmware redraws its clocks right away."""
        if self.host_offset is None:
            self.host_offset = 0
            self.followed_writes = self.time_writes
        elif self.time_writes != self.followed_writes:
            # Since the last follow both clocks ran on blocks: compare them on blocks.
            exact = self.seconds+(blocks-self.epoch)/self.blocks_per_second
            self.host_offset = round(exact-(self.host_base/1e6+(blocks-self.host_block)/self.blocks_per_second))
            self.followed_writes = self.time_writes
        self.host_base,self.host_block = round(host*1e6),blocks
        target = host+self.host_offset
        self.seconds = int(target)
        self.epoch = blocks-int((target-self.seconds)*self.blocks_per_second)

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
            self.raw |= 1<<(8+index)
            self.updates += 1
            self.registers[offset] = value
            if self.host_offset is not None:
                # PS writes either the day alone (0x18e670) or all four fields ending
                # with the day (0x18e5da): commit on the day. The firmware's factory
                # date is a reset, not a choice (boot 0x17e75c when the NV year lags,
                # MMI 0x1cee90 while "date not set"): host time stays.
                self.staged[index] = value
                if index != 3:
                    return
                staged,self.staged = self.staged,{}
                # The reset is a date, then 00:00:00 (PS 0x17e6d6): ignore both.
                midnight = self.after_reset and all(staged.get(i) == 0 for i in range(3))
                self.after_reset = value == DEFAULT_DAY
                if self.after_reset or midnight:
                    self.ignored_resets += 1
                    return
            else:
                staged = {index:value}
            values = list(self.fields(blocks))
            for i,v in staged.items():
                values[i] = v
            self.seconds = values[0]+60*values[1]+3600*values[2]+DAY*values[3]
            self.epoch = blocks
            self.last = self.seconds
            self.time_writes += 1
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
                    ticks=self.ticks,host_clock=self.host_offset is not None,host_offset=self.host_offset,
                    ignored_factory_resets=self.ignored_resets)
