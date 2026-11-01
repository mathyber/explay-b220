"""SDSC SPI card behind the recovered SC6600L programmed-I/O SPI master.

512-byte sectors, byte addressing, CSD v1; protocol state and media live in RAM.
Host image persistence is owned separately by the runner, not by guest commands.
"""
from collections import deque
import binascii

from .spi import EmptySpi


def crc7(data):
    crc = 0
    for value in data:
        for bit in range(7,-1,-1):
            top = (crc>>6)^((value>>bit)&1)
            crc = (crc<<1)&0x7f
            if top&1:
                crc ^= 9
    return crc


class SdSpi(EmptySpi):
    def __init__(self,image):
        super().__init__('SDSC SPI card, byte-addressed sectors')
        if len(image) not in (16*1024*1024,32*1024*1024):
            raise ValueError('Supported SD image sizes are 16 and 32 MiB')
        self.image = bytearray(image)
        self.idle = True
        self.app_command = False
        self.crc_enabled = False
        self.command = bytearray()
        self.response = deque()
        self.write_sector = None
        self.write_multi = False
        self.write_payload = bytearray()
        self.write_receiving = False
        self.read_sector = None
        self.dirty_sectors = set()
        self.commands = []
        self.command_count = 0
        self.sectors_read = 0
        self.sectors_written = 0
        self.erase_start = None
        self.erase_end = None

    def _data(self,data):
        return b'\xff\xfe'+bytes(data)+binascii.crc_hqx(data,0).to_bytes(2,'big')

    def _csd(self):
        # C_SIZE=1023; C_SIZE_MULT=3/4 -> 16/32 MiB, READ_BL_LEN=9.
        value = (0x0e<<112)|(0x32<<96)|(0x5b5<<84)|(9<<80)|(1023<<62)
        value |= (3 if len(self.image)==16*1024*1024 else 4)<<47
        value |= (1<<46)|(127<<39)|(2<<26)|(9<<22)
        data = value.to_bytes(16,'big')
        return data[:15]+bytes([(crc7(data[:15])<<1)|1])

    def _cid(self):
        data = bytes.fromhex('03')+b'EBB220 '+bytes.fromhex('10')+bytes.fromhex('00000220')+bytes.fromhex('0121')
        return data+bytes([(crc7(data)<<1)|1])

    def _execute(self,raw):
        cmd,arg = raw[0]&63,int.from_bytes(raw[1:5],'big')
        app,self.app_command = self.app_command,False
        self.command_count += 1
        if len(self.commands)<512:
            self.commands.append(dict(command=cmd,argument=arg,application=app))
        if (self.crc_enabled or cmd in (0,8)) and raw[5] != (crc7(raw[:5])<<1)|1:
            self.response.extend((0xff,(1 if self.idle else 0)|8))
            return
        result = 1 if self.idle else 0
        tail = b''
        if cmd == 0:
            self.idle = True
            self.crc_enabled = False
            self.write_sector = self.read_sector = None
            self.write_receiving = False
            self.write_payload.clear()
            self.response.clear()
            result = 1
        elif cmd == 8:
            result |= 0 if arg&0xf00 == 0x100 else 0x40
            tail = arg.to_bytes(4,'big')
        elif cmd == 55:
            self.app_command = True
        elif cmd == 41 and app:
            self.idle = False
            result = 0
        elif cmd == 58:
            tail = ((0 if self.idle else 0x80000000)|0x00ff8000).to_bytes(4,'big')
        elif cmd == 59:
            self.crc_enabled = bool(arg&1)
        elif self.idle:
            result |= 4
        elif cmd in (9,10):
            tail = self._data(self._csd() if cmd==9 else self._cid())
        elif cmd == 16:
            if arg != 512:
                result = 0x40
        elif cmd in (17,18,24,25):
            if arg%512 or arg+512>len(self.image):
                result = 0x20
            elif cmd in (17,18):
                tail = self._data(self.image[arg:arg+512])
                self.sectors_read += 1
                self.read_sector = arg//512+1 if cmd==18 else None
            else:
                self.write_sector = arg//512
                self.write_multi = cmd==25
                self.write_payload.clear()
                self.write_receiving = False
        elif cmd == 12:
            self.read_sector = None
            self.response.clear()
            tail = b'\xff'  # CMD12 includes a stuff byte before R1.
            self.response.extend((0xff,0xff,0))
            return
        elif cmd == 13:
            tail = b'\0'
        elif cmd == 23 and app:
            pass  # Pre-erase hint does not change the stored bytes.
        elif cmd in (32,33):
            if arg%512 or arg>=len(self.image):
                result = 0x20
            elif cmd==32:
                self.erase_start = arg//512
            else:
                self.erase_end = arg//512
        elif cmd == 38:
            if self.erase_start is None or self.erase_end is None or self.erase_start>self.erase_end:
                result = 0x10
            else:
                for sector in range(self.erase_start,self.erase_end+1):
                    self.image[sector*512:(sector+1)*512] = bytes(512)
                    self.dirty_sectors.add(sector)
                tail = b'\0\xff'
        else:
            result |= 4  # R1 illegal-command; never fabricate command success.
        self.response.extend(b'\xff'+bytes([result])+tail)

    def exchange(self,value):
        if self.registers.get(8,0)&0x100:
            return 0xff  # SD uses hardware CS0, active low.
        outgoing = self.response.popleft() if self.response else 0xff
        if self.write_sector is not None:
            if not self.write_receiving:
                if value == 0xfd and self.write_multi:
                    self.write_sector = None
                elif value == (0xfc if self.write_multi else 0xfe):
                    self.write_receiving = True
                    self.write_payload.clear()
            else:
                self.write_payload.append(value)
                if len(self.write_payload)==514:
                    data = self.write_payload[:512]
                    good = not self.crc_enabled or int.from_bytes(self.write_payload[512:],'big')==binascii.crc_hqx(data,0)
                    position = self.write_sector*512
                    if position+512>len(self.image):
                        token = 0x0d
                    elif not good:
                        token = 0x0b
                    else:
                        self.image[position:position+512] = data
                        self.dirty_sectors.add(self.write_sector)
                        self.sectors_written += 1
                        token = 5
                    self.response.extend((token,0,0xff))
                    self.write_sector = self.write_sector+1 if self.write_multi else None
                    self.write_receiving = False
                    self.write_payload.clear()
            return outgoing
        if self.command or value&0xc0 == 0x40:
            self.command.append(value)
            if len(self.command)==6:
                raw = bytes(self.command)
                self.command.clear()
                self._execute(raw)
        elif not self.response and self.read_sector is not None:
            offset = self.read_sector*512
            if offset+512 <= len(self.image):
                self.response.extend(self._data(self.image[offset:offset+512]))
                self.read_sector += 1
                self.sectors_read += 1
            else:
                self.response.append(8)  # Out-of-range data-error token.
                self.read_sector = None
        return outgoing

    def write(self,offset,value):
        old = self.registers.get(offset,0)
        super().write(offset,value)
        if offset == 8 and value&0x100 and not old&0x100:
            self.command.clear()
            self.response.clear()
            self.read_sector = self.write_sector = None
            self.write_receiving = False
            self.write_payload.clear()
        if offset == 0:
            width = (self.registers.get(8,0)>>2)&31
            if width not in (0,8,16):
                raise ValueError(f'Unsupported SD SPI word width: {width}')
            for byte in value.to_bytes(4,'big')[-(width//8 if width else 4):]:
                self.exchange(byte)

    def read(self,offset):
        if offset != 0:
            return super().read(offset)
        if not self.rx_remaining:
            raise ValueError('SD SPI FIFO read without a receive transfer')
        self.rx_remaining -= 1
        self.rx_words += 1
        width = (self.registers.get(8,0)>>2)&31
        if width not in (0,8,16):
            raise ValueError(f'Unsupported SD SPI word width: {width}')
        result = 0
        for _ in range(width//8 if width else 4):
            result = (result<<8)|self.exchange(0xff)
        return result

    def report(self):
        return dict(super().report(),size=len(self.image),idle=self.idle,commands=self.commands,
                    command_count=self.command_count,sectors_read=self.sectors_read,
                    sectors_written=self.sectors_written,dirty_sectors=len(self.dirty_sectors))
