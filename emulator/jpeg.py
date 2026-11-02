"""Observed JPEG register protocol, reconstructed from this firmware's drivers."""
from functools import lru_cache
from io import BytesIO
import hashlib
from PIL import Image


ZIGZAG = (0,1,8,16,9,2,3,10,17,24,32,25,18,11,4,5,12,19,26,33,40,48,41,34,
          27,20,13,6,7,14,21,28,35,42,49,56,57,50,43,36,29,22,15,23,30,37,44,
          51,58,59,52,45,38,31,39,46,53,60,61,54,47,55,62,63)
STANDARD_HW_HUFFMAN_SHA256 = '73a6d4b639f3c04505fbf1430dca2dc09080b1e98522a6e7fb3e02f99cf4b0c9'


def segment(marker,data):
    return b'\xff'+bytes([marker])+(len(data)+2).to_bytes(2,'big')+data


@lru_cache(maxsize=1)
def standard_huffman():
    """Obtain libjpeg's standard, non-optimized baseline tables."""
    stream = BytesIO()
    Image.new('RGB',(8,8)).save(stream,format='JPEG',optimize=False)
    data,offset,tables = stream.getvalue(),2,{}
    while data[offset:offset+2] != b'\xff\xda':
        marker = data[offset+1]
        size = int.from_bytes(data[offset+2:offset+4],'big')
        if marker == 0xc4:
            payload = data[offset+4:offset+2+size]
            tables[payload[0]] = payload
        offset += 2+size
    return tables


def entropy_end(data,mcus,tables,luma_blocks=2):
    """Find the exact end of a supported baseline scan without decoding pixels."""
    codes = {}
    for key,table in tables.items():
        code,index,mapping = 0,17,{}
        for length,count in enumerate(table[1:17],1):
            for _ in range(count):
                mapping[length,code] = table[index]
                index += 1
                code += 1
            code <<= 1
        codes[key] = mapping
    position,bits,accumulator = 0,0,0
    def take(count):
        nonlocal position,bits,accumulator
        while bits < count:
            if position >= len(data):
                raise ValueError('JPEG entropy exceeds the available input window')
            value = data[position]
            position += 1
            if value == 255:
                if position >= len(data) or data[position] != 0:
                    raise NotImplementedError('JPEG restart/marker inside baseline scan')
                position += 1
            accumulator = (accumulator<<8)|value
            bits += 8
        bits -= count
        value = (accumulator>>bits)&((1<<count)-1)
        accumulator &= (1<<bits)-1
        return value
    def symbol(table):
        code = 0
        for length in range(1,17):
            code = (code<<1)|take(1)
            if (length,code) in codes[table]:
                return codes[table][length,code]
        raise ValueError('Invalid JPEG Huffman code')
    for _ in range(mcus):
        for table in (0,)*luma_blocks+(1,1):
            category = symbol(table)
            if category > 11:
                raise ValueError('Invalid baseline JPEG DC category')
            take(category)
            coefficient = 1
            while coefficient < 64:
                value = symbol(0x10|table)
                if value == 0:
                    break
                run,size = value>>4,value&15
                if size == 0 and run != 15 or size > 10:
                    raise ValueError('Invalid baseline JPEG AC symbol')
                coefficient += run if size else 16
                if size:
                    if coefficient >= 64:
                        raise ValueError('JPEG AC run exceeds the block')
                    take(size)
                    coefficient += 1
    return position


class JpegEngine:
    base = 0x20200000
    end = 0x20220000
    register_ranges = ((0,0x6c),(0x300,0x400),(0x2000,0x2288),(0x10000,0x10008),
                       (0x10400,0x1041c),(0x10800,0x1098c),(0x11000,0x11048),(0x11800,0x1181c))

    def __init__(self):
        self.registers = {}
        self.events = []
        self.operations = []
        self.processed = 0
        self.decoded = None
        self.bit_position = 0
        self.bit_value = 0
        self.planar_bank = 0

    def write(self,offset,value,read_memory,write_memory):
        if not any(start <= offset < end for start,end in self.register_ranges):
            raise NotImplementedError(f'JPEG register write {offset:#x}')
        old = self.registers.get(offset,0)
        self.registers[offset] = value
        if offset == 0x28:
            self.registers[0x1c] = self.registers.get(0x1c,0)&~value
            self.registers[0x28] = 0
        if len(self.events) < 4000:
            self.events.append(dict(op='write',offset=hex(offset),value=hex(value)))
        if offset == 0 and value == 9:
            self.decoded = None
            self.planar_bank = 0
        if offset == 0x11810:
            self.registers[offset] = old&~(value&3)
        if offset == 0x11818 and value&1:
            self.processed = 0
            self.planar_bank = 0
            self.registers[0x11810] = 0
        if offset == 0x10800 and value&1:
            self.decode(read_memory,write_memory)
        if offset == 0x10404:
            self.bit_position = (value&0x3fffffff)*32
        if offset == 0x50:
            # A newly programmed bitstream buffer starts at its first byte;
            # the driver can then seek within it with BSM_CFG1 (0x10404).
            self.bit_position,self.bit_value = 0,0
        if offset == 0x10408:
            if value == 2:
                self.bit_position,self.bit_value = 0,0
            else:
                count = value>>24
                if not 0 < count <= 32 or value&0xffffff not in (0,1):
                    raise NotImplementedError(f'JPEG bit-read command {value:#x}')
                start = (self.registers.get(0x50,0)<<8)+(self.bit_position//8)
                shift = self.bit_position&7
                size = (shift+count+7)//8
                if not 0x04000000 <= start <= 0x04400000-size:
                    raise ValueError('JPEG bit-reader input exceeds external RAM')
                data = bytes(read_memory(start,size))
                self.bit_value = (int.from_bytes(data,'big')>>(size*8-shift-count))&((1<<count)-1)
                if value&1:
                    self.bit_position += count

    def read(self,offset,read_memory):
        if not any(start <= offset < end for start,end in self.register_ranges):
            raise NotImplementedError(f'JPEG register read {offset:#x}')
        if offset == 0:
            # PS 0x2ffd08 requests the table port with 19 and waits for bit7.
            return self.registers.get(0,0)|(0x80 if self.registers.get(0,0)&0x10 else 0)
        if offset == 0x14:
            return 2  # bitstream interface available, polled by 0x2fa95e
        if offset == 0x20:
            return self.registers.get(0x1c,0)&self.registers.get(0x24,0)&0x3fff
        if offset == 0x10410:
            return self.bit_value
        if offset == 0x10414:
            return self.bit_position
        if offset == 0x10418:
            # PS 0x5e103c starts a bitstream preload; 0x5e1046 waits for
            # bit31 here before parsing a JPEG file header (unlike boot scans).
            start = self.registers.get(0x50,0)<<8
            control = self.registers.get(0x10400,0)
            size = (control&0x3fffffff)*4
            if control&0x80000000 and 0<size and 0x04000000<=start<=0x04400000-size:
                # 0x5e0d90 also waits for a complete 32-bit FIFO word (bit3)
                # while reading DQT/DHT data through the same bitstream port.
                return 0x80000000|(8 if size*8-self.bit_position>=32 else 0)
            return 0
        return self.registers.get(offset,0)

    def decode(self,read_memory,write_memory):
        regs = self.registers
        width,height = regs.get(0xc,0)&0xffff,regs.get(0xc,0)>>16
        sampling = regs.get(0x10004,0)>>24
        if regs.get(0x11800,0)&0x3000:
            raise NotImplementedError('JPEG scaled output')
        if sampling not in (0,3) or not 0 < width <= 1024 or not 0 < height <= 1024:
            raise NotImplementedError(f'JPEG dimensions/sampling {width}x{height}, mode {sampling}')
        mcu_height = 16 if sampling==0 else 8
        vertical_chroma = 2 if sampling==0 else 1
        planar = bool(regs.get(0x40,0) or regs.get(0x44,0))
        columns = (width+15)//16
        frame_total = columns*((height+mcu_height-1)//mcu_height)
        total,batch = regs.get(0x1180c,0),regs.get(0x1087c,0)
        if not 0<total<=frame_total or (not planar and total!=frame_total):
            raise ValueError('JPEG MCU count does not match frame dimensions')
        if not 0<batch<=frame_total or not 0<=self.processed<frame_total:
            raise ValueError('JPEG MCU batch is empty, too large or follows a finished frame')
        completed = min(self.processed+batch,frame_total)
        first_row = self.processed//columns*mcu_height
        if planar:
            bank = getattr(self,'planar_bank',0)
            destination,chroma = (regs.get(0x40+bank*8+off,0)<<8 for off in (0,4))
            rows = min(completed//columns*mcu_height,height)-first_row
            if self.processed%columns or completed%columns or batch>total:
                raise NotImplementedError('JPEG planar batch must contain complete MCU rows')
            size = width*rows
            chroma_size = width*((rows+vertical_chroma-1)//vertical_chroma)
            if (not 0x04000000<=destination<=0x04400000-size or
                    not 0x04000000<=chroma<=0x04400000-chroma_size or
                    not (destination+size<=chroma or chroma+chroma_size<=destination)):
                raise ValueError('JPEG planar buffers exceed RAM or overlap')
        else:
            destination = regs.get(0x58,0)<<8
            if not 0x04000000<=destination<=0x04400000-width*height*2:
                raise ValueError('JPEG RGB output exceeds external RAM')
        if self.decoded is None:
            tables = standard_huffman()
            # Restrict this first model to the exact standard DC/AC code and
            # symbol tables observed at 0x627c2c/0x627e58. Reject custom tables.
            keys = (*range(0x10884,0x1098c,4),*range(0x2000,0x2288,4))
            table_bytes = b''.join(regs.get(key,0).to_bytes(4,'big') for key in keys)
            if hashlib.sha256(table_bytes).hexdigest() != STANDARD_HW_HUFFMAN_SHA256:
                raise NotImplementedError('JPEG custom or incomplete Huffman tables')
            quant = []
            for table in range(2):
                natural = bytearray(64)
                for i in range(32):
                    value = regs.get(0x300+table*0x80+i*4,0)
                    for lane in range(2):
                        index = int(f'{2*i+lane:06b}'[::-1],2)
                        natural[index] = (value>>(16*lane))&255
                if not all(natural):
                    raise ValueError('JPEG quantization table contains zero')
                quant.append(bytes(natural[i] for i in ZIGZAG))
            header = b'\xff\xd8'+segment(0xdb,b'\x00'+quant[0]+b'\x01'+quant[1])
            header += b''.join(segment(0xc4,v) for v in tables.values())
            header += segment(0xc0,b'\x08'+height.to_bytes(2,'big')+width.to_bytes(2,'big')+
                              bytes([3,1,0x22 if sampling==0 else 0x21,0,2,0x11,1,3,0x11,1]))
            header += segment(0xda,b'\x03\x01\x00\x02\x11\x03\x11\x00\x3f\x00')
            if self.bit_position&7:
                raise NotImplementedError('JPEG entropy starts at a non-byte boundary')
            source = (regs.get(0x50,0)<<8)+self.bit_position//8
            if not 0x04000000 <= source < 0x04400000:
                raise NotImplementedError('JPEG bitstream outside external RAM')
            available = min(0x10000,0x04400000-source)
            if planar:
                buffered = (regs.get(0x10400,0)&0x3fffffff)*4-self.bit_position//8
                if buffered<=0:
                    raise ValueError('JPEG stream position exceeds its declared input buffer')
                available = min(available,buffered)
            entropy = bytes(read_memory(source,available))
            length = entropy_end(entropy,frame_total,tables,4 if sampling==0 else 2)
            try:
                image = Image.open(BytesIO(header+entropy[:length]+b'\xff\xd9'))
                if planar:
                    image.draft('YCbCr',image.size)
                image = image.convert('YCbCr' if planar else 'RGB')
            except OSError as exc:
                raise ValueError(f'JPEG pixel decode failed: {exc}') from exc
            raw = bytearray()
            if planar:
                raw.extend(image.tobytes())
            else:
                for r,g,b in image.getdata():
                    raw.extend((((r>>3)<<11)|((g>>2)<<5)|(b>>3)).to_bytes(2,'big'))
            self.decoded = bytes(raw)
            self.operations.append(dict(width=width,height=height,sampling=sampling,
                source=hex(source),input_bytes=length,destination=hex(destination),output=f'Y + interleaved CbCr{420 if sampling==0 else 422}, alternating strips' if planar else 'RGB565 BE, linear (hypothesis)',
                output_sha256=hashlib.sha256(raw).hexdigest()))
        if len(self.decoded)!=width*height*(3 if planar else 2):
            raise ValueError('JPEG output configuration changed without resetting the decoder')
        if planar:
            for row in range(first_row,min(completed//columns*mcu_height,height)):
                pixels = self.decoded[row*width*3:(row+1)*width*3]
                write_memory(destination+(row-first_row)*width,pixels[0::3])
                if row%vertical_chroma:
                    continue
                next_pixels = self.decoded[min(row+1,height-1)*width*3:(min(row+1,height-1)+1)*width*3] if vertical_chroma==2 else None
                uv = bytearray()
                for x in range(0,width,2):
                    for component in (1,2):
                        value = pixels[x*3+component]+pixels[min(x+1,width-1)*3+component]
                        if next_pixels is not None:
                            value += next_pixels[x*3+component]+next_pixels[min(x+1,width-1)*3+component]
                        uv.append((value+vertical_chroma)//(2*vertical_chroma))
                write_memory(chroma+(row-first_row)//vertical_chroma*width,bytes(uv))
            self.operations[-1].setdefault('batches',[]).append(dict(bank=bank,first_row=first_row,rows=rows,
                luma=hex(destination),chroma=hex(chroma),completed_mcus=completed))
        else:
            for mcu in range(self.processed,completed):
                x,y = (mcu%columns)*16,(mcu//columns)*mcu_height
                length = min(16,width-x)*2
                for row in range(y,min(y+mcu_height,height)):
                    start = (row*width+x)*2
                    write_memory(destination+start,self.decoded[start:start+length])
        self.processed = completed
        regs[0x11808] = ((self.processed//columns)<<16)|(self.processed%columns)
        regs[0x11810] = ((1<<bank)|(4 if completed==frame_total else 0)) if planar else (4 if self.processed == total else 0)
        regs[0x10800] = 0
        regs[0x68] = 0
        if planar:
            regs[0x1c] = regs.get(0x1c,0)|(1<<9)
            self.planar_bank = 1-bank

    @property
    def pending(self):
        return bool(self.registers.get(0x1c,0)&self.registers.get(0x24,0)&0x3fff)

    def report(self):
        return dict(registers={hex(k):hex(v) for k,v in self.registers.items()},
                    operations=self.operations,events=self.events)
