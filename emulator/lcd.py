"""Partial ILI9225, 176x220, 8-bit bus on the upper lane of 16-bit accesses.

Register identities/window layout: Linux drivers/gpu/drm/tiny/ili9225.c.
Bus lane and ID read sequence: this firmware's _LCM_ReadID at 0x6bd464.
This does not model analog power, scan timing, gamma, or panel orientation pins.
"""
import collections
import os
import time
from pathlib import Path


class Lcd9225:
    width, height = 176, 220

    def __init__(self, device_id=0x9225):
        self.device_id = device_id
        self.index = 0
        self.command_hi = None
        self.data_hi = None
        self.read_index = 0
        self.registers = {3:0x1030,0x36:175,0x37:0,0x38:219,0x39:0,0x20:0,0x21:0}
        self.gram = [0]*(self.width*self.height)
        self.pixel_writes = 0
        self.register_writes = 0
        self.commands = collections.Counter()
        self.events = []

    def write(self,data_port,size,value):
        if size != 2:
            return False
        byte = (value >> 8)&255
        if not data_port:
            if self.command_hi is None:
                self.command_hi = byte
            else:
                self.index = (self.command_hi << 8)|byte
                self.command_hi = None
                self.data_hi = None
                self.read_index = 0
                self.commands[self.index] += 1
            return True
        if self.data_hi is None:
            self.data_hi = byte
            return True
        word = (self.data_hi << 8)|byte
        self.data_hi = None
        self.write_word(word)
        return True

    def write_word(self,word):
        if self.index != 0x22:
            self.registers[self.index] = word
            self.register_writes += 1
            if len(self.events) < 3000:
                self.events.append(dict(register=hex(self.index),value=hex(word)))
            return
        x,y = self.registers[0x20],self.registers[0x21]
        if 0 <= x < self.width and 0 <= y < self.height:
            self.gram[y*self.width+x] = word
            self.pixel_writes += 1
        mode = self.registers[3]
        dx,dy = (1 if mode&0x10 else -1),(1 if mode&0x20 else -1)
        xmin,xmax = self.registers[0x37],self.registers[0x36]
        ymin,ymax = self.registers[0x39],self.registers[0x38]
        if mode&8:
            y += dy
            if not ymin <= y <= ymax:
                y = ymin if dy > 0 else ymax
                x += dx
                if not xmin <= x <= xmax:
                    x = xmin if dx > 0 else xmax
        else:
            x += dx
            if not xmin <= x <= xmax:
                x = xmin if dx > 0 else xmax
                y += dy
                if not ymin <= y <= ymax:
                    y = ymin if dy > 0 else ymax
        self.registers[0x20],self.registers[0x21] = x,y

    def read(self,data_port,size):
        if not data_port:
            return 0
        word = self.device_id if self.index == 0 else self.registers.get(self.index,0)
        byte = (word >> (8 if self.read_index%2 == 0 else 0))&255
        self.read_index += 1
        return byte << 8 if size == 2 else byte

    def report(self):
        return dict(device='virtual ILI9225 (chosen supported panel)',device_id=hex(self.device_id),
                    width=self.width,height=self.height,pixel_writes=self.pixel_writes,
                    register_writes=self.register_writes,
                    registers={hex(k):hex(v) for k,v in sorted(self.registers.items())},
                    command_counts={hex(k):v for k,v in self.commands.items()},events=self.events,
                    limitations='Raw RGB565 GRAM view; no BGR/wiring validation, analog timing/gamma/scan-pin orientation')

    def save(self,directory,stem='lcd-gram',write_raw=True):
        if not self.pixel_writes:
            return None
        from PIL import Image
        directory = Path(directory)
        directory.mkdir(parents=True,exist_ok=True)
        rgb = bytearray()
        for value in self.gram:
            r,g,b = (value>>11)&31,(value>>5)&63,value&31
            rgb.extend(((r<<3)|(r>>2),(g<<2)|(g>>4),(b<<3)|(b>>2)))
        path = directory/(stem+'.png')
        temporary = path.with_name(f'.{path.stem}-{os.getpid()}.tmp.png')
        try:
            Image.frombytes('RGB',(self.width,self.height),bytes(rgb)).save(temporary)
            # Windows readers/scanners may briefly open the destination without
            # FILE_SHARE_DELETE. Keep the last complete PNG while retrying.
            for attempt,delay in enumerate((0,0.01,0.03,0.06)):
                if delay:
                    time.sleep(delay)
                try:
                    temporary.replace(path)
                    break
                except PermissionError:
                    if attempt == 3:
                        raise
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass  # A scanner can also hold the temporary file briefly.
        if write_raw:
            (directory/(stem+'.rgb565be')).write_bytes(b''.join(v.to_bytes(2,'big') for v in self.gram))
        return str(path)
