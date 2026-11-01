"""Decode actual firmware scan and colored JPEG data through the register model."""
from io import BytesIO
import json
from pathlib import Path
import unittest

from PIL import Image
from emulator.jpeg import JpegEngine,ZIGZAG


FIXTURES = Path(__file__).parent/'fixtures'


class JpegTests(unittest.TestCase):
    def test_file_header_preload_and_word_fifo_ready_status(self):
        engine = JpegEngine()
        self.assertEqual(engine.read(0x10418,self.read),0)
        engine.registers.update({0x50:0x04001000>>8,0x10400:0x80000002})
        self.assertEqual(engine.read(0x10418,self.read),0x80000008)
        engine.bit_position = 40
        self.assertEqual(engine.read(0x10418,self.read),0x80000000)
        engine.registers[0x50] = 0x04400000>>8
        self.assertEqual(engine.read(0x10418,self.read),0)

    def setUp(self):
        self.jpeg = JpegEngine()
        fixture = json.loads((FIXTURES/'jpeg-registers.json').read_text())
        self.jpeg.registers = {int(k,0):v for k,v in fixture['registers'].items()}
        self.memory = bytearray(0x400000)
        self.source,self.destination = 0x04001000,0x04020000
        self.jpeg.registers[0x50] = self.source>>8
        self.jpeg.registers[0x58] = self.destination>>8
        self.jpeg.registers[0x11810] = 0

    def read(self,address,size):
        start = address-0x04000000
        if not 0 <= start <= len(self.memory)-size:
            raise ValueError('Test RAM read out of bounds')
        return self.memory[start:start+size]

    def write(self,address,data):
        start = address-0x04000000
        if not 0 <= start <= len(self.memory)-len(data):
            raise ValueError('Test RAM write out of bounds')
        self.memory[start:start+len(data)] = data

    def start(self):
        self.jpeg.write(0x10800,1,self.read,self.write)

    def test_actual_firmware_scan_and_partial_completion(self):
        self.write(self.source,(FIXTURES/'jpeg-first-scan.bin').read_bytes())
        self.write(self.destination,b'\xa5'*(176*224*2))
        self.start()
        self.assertEqual(self.jpeg.processed,154)
        self.assertEqual(self.jpeg.read(0x11808,self.read),14<<16)
        self.assertEqual(self.jpeg.read(0x11810,self.read)&4,0)
        self.assertEqual(self.read(self.destination,176*112*2),bytes(176*112*2))
        self.assertEqual(self.read(self.destination+176*112*2,16),b'\xa5'*16)
        self.start()
        self.assertEqual(self.jpeg.processed,308)
        self.assertEqual(self.jpeg.read(0x11810,self.read)&4,4)
        self.assertEqual(self.read(self.destination,176*224*2),bytes(176*224*2))
        self.assertEqual(self.jpeg.operations[0]['input_bytes'],773)

    def test_colored_image_uses_input_and_programmed_quantization(self):
        image = Image.new('RGB',(32,16))
        image.putdata([(x*7,y*15,(x*17+y*29)%256) for y in range(16) for x in range(32)])
        stream = BytesIO()
        image.save(stream,format='JPEG',subsampling=1,quality=87,optimize=False)
        data = stream.getvalue()
        offset = 2
        while data[offset+1] != 0xda:
            marker = data[offset+1]
            length = int.from_bytes(data[offset+2:offset+4],'big')
            payload = data[offset+4:offset+2+length]
            if marker == 0xdb:
                table = payload[0]&15
                natural = bytearray(64)
                for index,value in zip(ZIGZAG,payload[1:]):
                    natural[index] = value
                for i in range(32):
                    a,b = (int(f'{2*i+lane:06b}'[::-1],2) for lane in range(2))
                    self.jpeg.registers[0x300+table*0x80+i*4] = natural[a]|(natural[b]<<16)
            offset += 2+length
        offset += 2+int.from_bytes(data[offset+2:offset+4],'big')
        self.write(self.source,data[offset:-2])
        self.jpeg.registers.update({0xc:(16<<16)|32,0x10004:(3<<24)|(2<<12)|2,
                                    0x1180c:4,0x1087c:2})
        decoded = Image.open(BytesIO(data)).convert('RGB')
        expected = b''.join((((r>>3)<<11)|((g>>2)<<5)|(b>>3)).to_bytes(2,'big')
                            for r,g,b in decoded.getdata())
        self.start()
        self.start()
        self.assertEqual(self.read(self.destination,len(expected)),expected)

    def test_unknown_huffman_and_output_modes_do_not_complete(self):
        self.jpeg.registers[0x10910] ^= 1
        with self.assertRaisesRegex(NotImplementedError,'Huffman'):
            self.start()
        self.assertEqual(self.jpeg.processed,0)
        self.assertEqual(self.read(self.destination,100),bytes(100))
        self.jpeg.registers[0x40] = 0x400
        with self.assertRaisesRegex(NotImplementedError,'planar'):
            self.start()

    def test_table_port_handshake_and_bit_reader(self):
        self.jpeg.write(0,0x19,self.read,self.write)
        self.assertEqual(self.jpeg.read(0,self.read)&0x80,0x80)
        self.jpeg.write(0,9,self.read,self.write)
        self.assertEqual(self.jpeg.read(0,self.read)&0x80,0)
        self.write(self.source,bytes.fromhex('ffd8123456789abc'))
        for command,value,position in ((0x08000000,0xff,0),(0x10000001,0xffd8,16),
                                       (0x04000001,1,20),(0x10000000,0x2345,20)):
            self.jpeg.write(0x10408,command,self.read,self.write)
            self.assertEqual(self.jpeg.read(0x10410,self.read),value)
            self.assertEqual(self.jpeg.read(0x10414,self.read),position)
        with self.assertRaisesRegex(NotImplementedError,'bit-read'):
            self.jpeg.write(0x10408,0x28000000,self.read,self.write)


if __name__ == '__main__':
    unittest.main()
