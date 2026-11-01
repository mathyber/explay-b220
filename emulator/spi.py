"""SPI master subset with no attached card: clocks complete, MISO is pulled high."""


class EmptySpi:
    def __init__(self,description='unconnected SPI, no SD card'):
        self.description = description
        self.registers = {}
        self.rx_remaining = 0
        self.rx_words = 0
        self.tx_words = 0
        self.tx_sample = []

    def write(self,offset,value):
        old = self.registers.get(offset,0)
        self.registers[offset] = value
        if offset == 0:
            self.tx_words += 1
            if len(self.tx_sample) < 256:
                self.tx_sample.append(hex(value))
        elif offset == 0x18 and value&0x200 and not old&0x200:
            self.rx_remaining = value&0x1ff

    def read(self,offset):
        if offset == 0x34:
            # bit7 TX ready, bit8 busy clear, bit5 RX empty.
            return 0x80|(0x20 if not self.rx_remaining else 0)
        if offset == 0:
            if self.rx_remaining:
                self.rx_remaining -= 1
            self.rx_words += 1
            return 0xffffffff
        return self.registers.get(offset,0)

    def report(self):
        return dict(device=self.description,rx_words=self.rx_words,tx_words=self.tx_words,
                    rx_remaining=self.rx_remaining,tx_sample=self.tx_sample)
