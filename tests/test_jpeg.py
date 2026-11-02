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
        with self.assertRaisesRegex(ValueError,'planar'):
            self.start()

    def test_planar_strips_alternate_banks_signal_irq_and_preserve_rgb_buffer(self):
        self.check_planar_strips(3)

    def test_planar_420_uses_half_height_chroma_and_six_block_mcus(self):
        self.check_planar_strips(0)

    def check_planar_strips(self,sampling):
        mcu_height = 16 if sampling==0 else 8
        vertical_chroma = 2 if sampling==0 else 1
        height = mcu_height*3
        image = Image.new('RGB',(32,height))
        image.putdata([((255,0,0),(0,255,0),(0,0,255))[y//mcu_height] for y in range(height) for _ in range(32)])
        stream = BytesIO()
        image.save(stream,format='JPEG',quality=95,subsampling=2 if sampling==0 else 1,optimize=False)
        data = stream.getvalue()
        offset = 2
        while data[offset+1]!=0xda:
            marker = data[offset+1]
            length = int.from_bytes(data[offset+2:offset+4],'big')
            payload = data[offset+4:offset+2+length]
            if marker==0xdb:
                natural = bytearray(64)
                for index,value in zip(ZIGZAG,payload[1:]):
                    natural[index] = value
                for i in range(32):
                    a,b = (int(f'{2*i+lane:06b}'[::-1],2) for lane in range(2))
                    self.jpeg.registers[0x300+(payload[0]&15)*0x80+i*4] = natural[a]|(natural[b]<<16)
            offset += 2+length
        offset += 2+int.from_bytes(data[offset+2:offset+4],'big')
        self.write(self.source,data[offset:-2])
        buffers = [self.destination+i*0x1000 for i in range(5)]
        for address in buffers:
            self.write(address,b'\xa5'*(32*mcu_height+16))
        self.jpeg.registers.update({0xc:(height<<16)|32,0x10004:(sampling<<24)|(3<<12)|2,
            0x1180c:2,0x1087c:2,0x24:1<<9,0x58:buffers[4]>>8,
            0x10400:0x80000000|((len(data)-offset-2+3)//4)})
        for offset,address in zip((0x40,0x44,0x48,0x4c),buffers):
            self.jpeg.registers[offset] = address>>8
        for part,(y,cb,cr) in enumerate(((76,85,255),(150,44,21),(29,255,107))):
            bank = part%2
            self.start()
            center = mcu_height//2
            for actual,expected in zip((self.read(buffers[bank*2]+32*center,1)[0],*self.read(buffers[bank*2+1]+32*(center//vertical_chroma),2)),(y,cb,cr)):
                self.assertLessEqual(abs(actual-expected),2)
            self.assertEqual(self.read(buffers[bank*2]+32*mcu_height,16),b'\xa5'*16)
            self.assertEqual(self.read(buffers[bank*2+1]+32*mcu_height//vertical_chroma,16),b'\xa5'*16)
            self.assertEqual(self.read(buffers[4],32*mcu_height+16),b'\xa5'*(32*mcu_height+16))
            self.assertEqual(self.jpeg.processed,(part+1)*2)
            self.assertEqual(bool(self.jpeg.read(0x11810,self.read)&4),part==2)
            self.assertTrue(self.jpeg.pending)
            self.jpeg.write(0x24,0,self.read,self.write)
            self.assertFalse(self.jpeg.pending)
            self.assertEqual(self.jpeg.read(0x1c,self.read),1<<9)
            self.jpeg.write(0x24,1<<9,self.read,self.write)
            self.assertTrue(self.jpeg.pending)
            self.jpeg.write(0x28,1<<9,self.read,self.write)
            self.assertFalse(self.jpeg.pending)
            self.assertEqual(self.jpeg.read(0x28,self.read),0)
            self.jpeg.write(0x11810,1<<bank,self.read,self.write)
            self.assertEqual(bool(self.jpeg.read(0x11810,self.read)&4),part==2)
        self.assertEqual([item['bank'] for item in self.jpeg.operations[0]['batches']],[0,1,0])

    def test_invalid_planar_buffer_is_rejected_before_writing_pixels_or_signalling_irq(self):
        self.jpeg.registers.update({0x40:self.destination>>8,0x44:0x043fff00>>8,0x24:1<<9})
        self.write(self.destination,b'\xa5'*100)
        with self.assertRaisesRegex(ValueError,'planar'):
            self.start()
        self.assertEqual(self.read(self.destination,100),b'\xa5'*100)
        self.assertEqual(self.jpeg.processed,0)
        self.assertFalse(self.jpeg.pending)

    def test_unsupported_sampling_and_hardware_scaling_do_not_write_output(self):
        for sampling,scaling in ((1,0),(3,0x1000)):
            self.jpeg.registers.update({0x10004:sampling<<24,0x11800:scaling})
            with self.assertRaises(NotImplementedError):
                self.start()
            self.assertEqual(self.jpeg.processed,0)
            self.assertFalse(self.jpeg.pending)
            self.assertEqual(self.read(self.destination,32),bytes(32))

    def test_planar_decoder_stops_at_the_declared_input_buffer_boundary(self):
        self.write(self.source,(FIXTURES/'jpeg-first-scan.bin').read_bytes())
        self.jpeg.registers.update({0x40:self.destination>>8,0x44:(self.destination+0x10000)>>8,
                                   0x10400:0x80000001,0x24:1<<9})
        with self.assertRaisesRegex(ValueError,'available input window'):
            self.start()
        self.assertEqual(self.jpeg.processed,0)
        self.assertIsNone(self.jpeg.decoded)
        self.assertFalse(self.jpeg.pending)
        self.assertEqual(self.read(self.destination,100),bytes(100))

    def test_snapshot_continues_a_planar_frame_in_the_second_buffer_bank(self):
        import gc
        from tempfile import TemporaryDirectory
        from emulator.board import Board
        from emulator.snapshot import save_snapshot,load_snapshot
        with TemporaryDirectory() as folder:
            b = Board(explore=True)
            b.jpeg.registers = self.jpeg.registers.copy()
            b.jpeg.registers.update({0x40:0x04020000>>8,0x44:0x04030000>>8,
                0x48:0x04040000>>8,0x4c:0x04050000>>8,0x1180c:154,0x1087c:154,0x24:1<<9,
                0x10400:0x80000000|194})
            b.uc.mem_write(self.source,(FIXTURES/'jpeg-first-scan.bin').read_bytes())
            b.jpeg.decode(b.uc.mem_read,b.uc.mem_write)
            b.irq.write(8,1<<26)
            b.irq.set_line(26,b.jpeg.pending)
            path = Path(folder)/'half-frame.b220state'
            save_snapshot(b,path)
            restored = load_snapshot(path)
            self.assertEqual(restored.jpeg.planar_bank,1)
            self.assertEqual(restored.irq.pending,1<<26)
            self.assertEqual(restored.jpeg.decoded,b.jpeg.decoded)
            restored.jpeg.write(0x28,1<<9,restored.uc.mem_read,restored.uc.mem_write)
            restored.jpeg.decode(restored.uc.mem_read,restored.uc.mem_write)
            self.assertEqual(restored.jpeg.processed,308)
            self.assertTrue(restored.jpeg.read(0x11810,restored.uc.mem_read)&4)
            self.assertEqual(bytes(restored.uc.mem_read(0x04040000,176*112)),bytes(176*112))
            chroma = bytes(restored.uc.mem_read(0x04050000,176*112))
            self.assertEqual(chroma,bytes(b.uc.mem_read(0x04030000,176*112)))
            self.assertLessEqual(max(abs(value-128) for value in chroma),1)
            del b,restored
            gc.collect()

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
        self.jpeg.write(0x50,self.source>>8,self.read,self.write)
        self.assertEqual(self.jpeg.bit_position,0)
        self.jpeg.write(0x10408,0x10000001,self.read,self.write)
        self.assertEqual(self.jpeg.read(0x10410,self.read),0xffd8)


if __name__ == '__main__':
    unittest.main()
