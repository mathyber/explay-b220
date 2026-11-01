"""SD protocol bytes/CRC, FAT16 media, persistence, and snapshot continuation."""
import binascii
import gc
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
import unittest

from emulator.sd import SdSpi,crc7
from emulator.board import Board
from emulator.card_image import CardImage
from emulator.snapshot import load_snapshot,save_snapshot
from tools.sd_image import create_image,Fat16,sample_jpeg


def command(card,number,arg=0):
    raw = bytes([0x40|number])+arg.to_bytes(4,'big')
    for value in raw+bytes([(crc7(raw)<<1)|1]):
        card.exchange(value)
    for _ in range(10):
        result = card.exchange(0xff)
        if not result&0x80:
            return result
    raise AssertionError('No SD response')


def initialize(card):
    assert command(card,0)==1
    assert command(card,8,0x1aa)==1
    assert bytes(card.exchange(0xff) for _ in range(4))==bytes.fromhex('000001aa')
    assert command(card,55)==1
    assert command(card,41,0x40000000)==0


class SdProtocolTests(unittest.TestCase):
    def setUp(self):
        self.card = SdSpi(create_image())
        initialize(self.card)

    def test_crc_vectors_and_csd_capacity(self):
        self.assertEqual((crc7(bytes.fromhex('4000000000'))<<1)|1,0x95)
        self.assertEqual((crc7(bytes.fromhex('48000001aa'))<<1)|1,0x87)
        self.assertEqual(command(self.card,9),0)
        self.assertEqual(self.card.exchange(0xff),0xff)
        self.assertEqual(self.card.exchange(0xff),0xfe)
        csd = bytes(self.card.exchange(0xff) for _ in range(16))
        value = int.from_bytes(csd,'big')
        capacity = (((value>>62)&4095)+1)*(1<<(((value>>47)&7)+2))*(1<<((value>>80)&15))
        self.assertEqual(capacity,len(self.card.image))
        self.assertEqual(int.from_bytes(bytes(self.card.exchange(0xff) for _ in range(2)),'big'),binascii.crc_hqx(csd,0))

    def test_sector_write_crc_rejection_readback_and_bounds(self):
        card = self.card
        self.assertEqual(command(card,59,1),0)
        data = bytes(range(256))*2
        for good in (False,True):
            self.assertEqual(command(card,24,4096),0)
            checksum = binascii.crc_hqx(data,0)^(0 if good else 1)
            for value in b'\xfe'+data+checksum.to_bytes(2,'big'):
                card.exchange(value)
            self.assertEqual(card.exchange(0xff),5 if good else 0x0b)
            self.assertEqual(card.exchange(0xff),0)
            self.assertEqual(card.exchange(0xff),0xff)
            self.assertEqual(card.sectors_written,1 if good else 0)
        self.assertEqual(card.dirty_sectors,{8})
        self.assertEqual(command(card,17,4096),0)
        self.assertEqual(bytes(card.exchange(0xff) for _ in range(2)),b'\xff\xfe')
        self.assertEqual(bytes(card.exchange(0xff) for _ in range(512)),data)
        self.assertEqual(bytes(card.exchange(0xff) for _ in range(2)),binascii.crc_hqx(data,0).to_bytes(2,'big'))
        self.assertEqual(command(card,17,len(card.image)),0x20)
        self.assertEqual(command(card,24,1),0x20)
        self.assertEqual(command(card,63),4)

    def test_chip_select_and_receive_word_order(self):
        card = self.card
        card.write(8,0x120)
        self.assertEqual(card.exchange(0x40),0xff)
        self.assertFalse(card.command)
        card.write(8,0x20)
        self.assertEqual(command(card,58),0)
        card.write(0x18,0x8004)
        card.write(0x18,0x8204)
        self.assertEqual(bytes(card.read(0) for _ in range(4)),bytes.fromhex('80ff8000'))
        self.assertEqual(card.read(0x34),0xa0)
        card.write(8,0)
        card.response.extend(bytes.fromhex('12345678'))
        card.write(0x18,0x8001)
        card.write(0x18,0x8201)
        self.assertEqual(card.read(0),0x12345678)

    def test_multiblock_read_and_stop_keep_sector_and_crc_boundaries(self):
        card = self.card
        data = bytes((i*13)&255 for i in range(1024))
        card.image[4096:5120] = data
        self.assertEqual(command(card,18,4096),0)
        for block in range(2):
            while card.exchange(0xff)!=0xfe:
                pass
            actual = bytes(card.exchange(0xff) for _ in range(512))
            self.assertEqual(actual,data[block*512:(block+1)*512])
            self.assertEqual(bytes(card.exchange(0xff) for _ in range(2)),binascii.crc_hqx(actual,0).to_bytes(2,'big'))
        self.assertEqual(command(card,12),0)
        self.assertIsNone(card.read_sector)
        self.assertEqual(card.exchange(0xff),0xff)


class SdStorageTests(unittest.TestCase):
    def test_native_dma_enable_moves_spi_words_and_acknowledges_completion(self):
        from unicorn.arm_const import UC_ARM_REG_CPSR,UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_LR
        b = Board(explore=True,sd_image=create_image())
        b.uc.mem_write(0x4000e000,bytes.fromhex('e5810000e12fff1e'))
        def write(address,value):
            b.uc.reg_write(UC_ARM_REG_CPSR,0x2d3)
            b.uc.reg_write(UC_ARM_REG_R0,value)
            b.uc.reg_write(UC_ARM_REG_R1,address)
            b.uc.reg_write(UC_ARM_REG_LR,0x4000fff0)
            result = b.run(100,2,entry=0x4000e000,until=0x4000fff0,report=False)
            self.assertEqual(result['stop']['reason'],'requested end address')
        try:
            source = bytes(range(16))
            b.spi.response.extend(source)
            b.spi.rx_remaining = 4
            for off,value in ((0,0x0a040010),(4,16),(8,0x8e000000),(12,0x04001000)):
                b.storage[0x201004a0+off] = value
            write(0x20100010,1<<5)
            self.assertEqual(bytes(b.uc.mem_read(0x04001000,16)),source)
            self.assertEqual(b.storage[0x20100060]&(1<<5),1<<5)
            self.assertFalse(b.dma_enabled&(1<<5))
            write(0x20100074,1<<5)
            self.assertFalse(b.storage[0x20100060]&(1<<5))
            initialize(b.spi)
            self.assertEqual(command(b.spi,24,4096),0)
            b.spi.exchange(0xfe)
            data = bytes(range(256))*2
            b.uc.mem_write(0x04002000,data)
            for off,value in ((0,0x0a080010),(4,512),(8,0x04002000),(12,0x8e000000)):
                b.storage[0x201004c0+off] = value
            write(0x20100010,1<<6)
            b.spi.exchange(0);b.spi.exchange(0)
            self.assertEqual(b.spi.exchange(0xff),5)
            self.assertEqual(bytes(b.spi.image[4096:4608]),data)
        finally:
            del b
            gc.collect()

    def test_fat16_partition_file_allocation_and_native_jpeg_fixture(self):
        from PIL import Image
        import io
        volume = Fat16(create_image())
        data = sample_jpeg()
        volume.add_file('TEST.JPG',data)
        image = volume.image
        self.assertEqual(image[510:512],b'\x55\xaa')
        first = volume.entries()[0]
        self.assertEqual((first['name'],first['size']),('TEST.JPG',len(data)))
        chain,cluster = [],first['cluster']
        while cluster<0xfff8:
            position = volume.data+(cluster-2)*volume.cluster_bytes
            chain.append(image[position:position+volume.cluster_bytes])
            cluster = struct.unpack_from('<H',image,volume.fat+cluster*2)[0]
        self.assertEqual(b''.join(chain)[:len(data)],data)
        self.assertEqual(image[volume.fat:volume.fat+volume.fat_sectors*512],image[volume.fat+volume.fat_sectors*512:volume.root])
        self.assertEqual(Image.open(io.BytesIO(data)).size,(176,220))
        with self.assertRaisesRegex(ValueError,'already exists'):
            volume.add_file('TEST.JPG',b'bad')
        self.assertEqual(volume.unique_name('a long name.jpg'),'FILE0001.JPG')

    def test_exclusive_image_owner_persists_guest_sectors(self):
        with TemporaryDirectory() as folder:
            path = Path(folder)/'sd.img'
            path.write_bytes(create_image())
            with CardImage(path) as image:
                with self.assertRaises(OSError):
                    CardImage(path)
                card = SdSpi(image.data)
                card.image[4096:4608] = b'x'*512
                card.dirty_sectors.add(8)
                image.flush(card)
                self.assertFalse(card.dirty_sectors)
            self.assertEqual(path.read_bytes()[4096:4608],b'x'*512)

    def test_snapshot_embeds_card_and_resumes_partial_spi_response(self):
        with TemporaryDirectory() as folder:
            board = Board(explore=True,sd_image=create_image())
            initialize(board.spi)
            self.assertEqual(command(board.spi,17),0)
            prefix = bytes(board.spi.exchange(0xff) for _ in range(20))
            path = Path(folder)/'card.b220state'
            save_snapshot(board,path)
            restored = load_snapshot(path)
            self.assertIs(restored.spi,restored.spi_controllers[0x8e000000])
            self.assertEqual(bytes(restored.spi.exchange(0xff) for _ in range(496)),bytes(board.spi.exchange(0xff) for _ in range(496)))
            self.assertEqual(prefix[:2],b'\xff\xfe')
            del board,restored
            gc.collect()


if __name__ == '__main__':
    unittest.main()
