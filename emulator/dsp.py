"""Observed ARM-side bootstrap mailbox. Protocol peer, not DSP instruction execution."""
import hashlib


class DspBootstrap:
    def __init__(self):
        self.command = 0
        self.status = 0
        self.offset = 0
        self.words = 0
        self.staged = None
        self.image = bytearray()
        self.transfers = []
        self.events = []

    def read(self,offset):
        return {0:self.command,2:self.status,4:self.offset,6:self.words}[offset]

    def write(self,offset,value,read_buffer):
        """Return True on the completion edge (IRQ13); invalid sequences raise."""
        if offset in (4,6):
            if offset == 4:
                self.offset = value
            else:
                self.words = value
            return False
        if offset == 2:
            if value:
                raise ValueError('Guest attempted nonzero write to DSP bootstrap status')
            return False  # guest zeroes the whole mailbox before startup
        if offset != 0 or value&~15:
            raise ValueError('Unsupported DSP bootstrap command')
        old,self.command = self.command,value
        if len(self.events) < 1000:
            self.events.append(dict(command=hex(value),previous=hex(old),offset_words=self.offset,words=self.words))
        if value == 0:
            self.status = 0
            self.staged = None
        elif value == 1:
            self.status = 1
        if value&2 and not old&2:
            if not 0 < self.words <= 1024:
                raise ValueError('DSP bootstrap block length outside observed 1..1024 words')
            self.staged = (self.offset,bytes(read_buffer(self.words*2)))
            self.status |= 2
        if value&4 and not old&4:
            if self.staged is None:
                raise ValueError('DSP bootstrap commit without a staged block')
            offset,payload = self.staged
            if offset*2 != len(self.image):
                raise ValueError('DSP bootstrap non-contiguous/duplicate block is not implemented')
            self.image.extend(payload)
            self.transfers.append(dict(offset_words=offset,words=len(payload)//2,
                sha256=hashlib.sha256(payload).hexdigest(),last_flag=bool(value&8)))
            self.staged = None
            self.status |= 4
            return True
        return False

    def report(self):
        return dict(kind='bootstrap protocol peer only; DSP code is not executed',
            command=hex(self.command),status=hex(self.status),received_bytes=len(self.image),
            image_sha256=hashlib.sha256(self.image).hexdigest(),transfers=self.transfers,events=self.events)
