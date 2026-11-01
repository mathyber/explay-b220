"""Inferred IRQ controller subset: masked/raw status, enable-set, enable-clear."""


class InterruptController:
    def __init__(self):
        self.raw = 0
        self.enabled = 0
        self.events = []

    @property
    def pending(self):
        return self.raw&self.enabled

    def set_line(self,line,asserted):
        mask = 1<<line
        self.raw = self.raw|mask if asserted else self.raw&~mask
        if len(self.events) < 1000:
            self.events.append(dict(line=line,asserted=asserted,enabled=hex(self.enabled)))

    def read(self,offset):
        # DIS is a write-one command port; readback must not echo EN.
        return {0:self.pending,4:self.raw,8:self.enabled,12:0}[offset]

    def write(self,offset,value):
        if offset == 8:
            self.enabled |= value
        elif offset == 12:
            self.enabled &= ~value
        else:
            raise ValueError('Write to read-only interrupt status')

    def report(self):
        return dict(raw=hex(self.raw),enabled=hex(self.enabled),pending=hex(self.pending),events=self.events)
