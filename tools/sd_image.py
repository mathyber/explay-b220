"""Create a FAT16 SD image and add/list root files without mounting it in Windows."""
import argparse
import io
from pathlib import Path
import re
import struct

from emulator.card_image import CardImage


def create_image(size=16*1024*1024):
    if size not in (16*1024*1024,32*1024*1024):
        raise ValueError('Supported SD image sizes are 16 and 32 MiB')
    image = bytearray(size)
    start,total = 63,size//512-63
    fat_sectors = 1
    while True:
        clusters = (total-1-2*fat_sectors-32)//2
        required = ((clusters+2)*2+511)//512
        if required <= fat_sectors:
            break
        fat_sectors = required
    image[446:462] = struct.pack('<B3sB3sII',0,b'\x01\x01\0',6,b'\xfe\xff\xff',start,total)
    image[510:512] = b'\x55\xaa'
    boot = start*512
    image[boot:boot+11] = b'\xeb\x3c\x90MSDOS5.0'
    struct.pack_into('<HBHBHHBHHHII',image,boot+11,512,2,1,2,512,total if total<65536 else 0,
                     0xf8,fat_sectors,32,64,start,total if total>=65536 else 0)
    image[boot+36:boot+62] = b'\x80\0\x29'+struct.pack('<I',0x220)+b'B220 CARD  '+b'FAT16   '
    image[boot+510:boot+512] = b'\x55\xaa'
    for index in range(2):
        image[(start+1+index*fat_sectors)*512:(start+1+index*fat_sectors)*512+4] = b'\xf8\xff\xff\xff'
    return image


class Fat16:
    def __init__(self,data):
        self.image = bytearray(data)
        if self.image[510:512] != b'\x55\xaa':
            raise ValueError('Missing MBR signature')
        self.start,self.total = struct.unpack_from('<II',self.image,454)
        boot = self.start*512
        if not 0<self.start<self.start+self.total<=len(data)//512:
            raise ValueError('Invalid SD partition bounds')
        bps,self.spc,reserved,copies,roots,_,_,self.fat_sectors = struct.unpack_from('<HBHBHHBH',self.image,boot+11)
        if bps!=512 or self.spc not in (1,2,4,8,16,32,64) or copies!=2 or not reserved or not roots or not self.fat_sectors:
            raise ValueError('Unsupported FAT16 geometry')
        self.fat = (self.start+reserved)*512
        self.root = self.fat+copies*self.fat_sectors*512
        self.root_end = self.root+roots*32
        self.data = (self.root_end+511)//512*512
        self.cluster_bytes = self.spc*512
        self.clusters = ((self.start+self.total)*512-self.data)//self.cluster_bytes
        if not 4085<=self.clusters<65525 or (self.clusters+2)*2>self.fat_sectors*512:
            raise ValueError('Image is not a supported FAT16 volume')

    def entries(self):
        result = []
        for offset in range(self.root,self.root_end,32):
            raw = self.image[offset:offset+32]
            if raw[0] == 0:
                break
            if raw[0] == 0xe5 or raw[11]&8:
                continue
            base,ext = raw[:8].decode('ascii',errors='replace').rstrip(),raw[8:11].decode('ascii',errors='replace').rstrip()
            result.append(dict(name=base+('.'+ext if ext else ''),directory=bool(raw[11]&16),
                               size=int.from_bytes(raw[28:32],'little'),offset=offset,
                               cluster=int.from_bytes(raw[26:28],'little')))
        return result

    def add_file(self,name,data):
        name = name.upper()
        if not re.fullmatch(r'[A-Z0-9_]{1,8}(\.[A-Z0-9_]{1,3})?',name):
            raise ValueError('Use an ASCII 8.3 file name')
        if any(item['name']==name for item in self.entries()):
            raise ValueError(f'File already exists: {name}')
        needed = (len(data)+self.cluster_bytes-1)//self.cluster_bytes
        free = [c for c in range(2,self.clusters+2) if self.image[self.fat+2*c:self.fat+2*c+2]==b'\0\0'][:needed]
        entry = next((i for i in range(self.root,self.root_end,32) if self.image[i] in (0,0xe5)),None)
        if len(free)!=needed or entry is None:
            raise ValueError('Not enough free clusters or root directory entries')
        for i,cluster in enumerate(free):
            next_cluster = free[i+1] if i+1<len(free) else 0xffff
            for fat_index in range(2):
                struct.pack_into('<H',self.image,self.fat+fat_index*self.fat_sectors*512+cluster*2,next_cluster)
            position = self.data+(cluster-2)*self.cluster_bytes
            chunk = data[i*self.cluster_bytes:(i+1)*self.cluster_bytes]
            self.image[position:position+self.cluster_bytes] = chunk+bytes(self.cluster_bytes-len(chunk))
        base,_,ext = name.partition('.')
        raw = bytearray(32)
        raw[:11] = (base.ljust(8)+ext.ljust(3)).encode('ascii')
        raw[11] = 0x20
        struct.pack_into('<HHHI',raw,22,0,0x21,free[0] if free else 0,len(data))
        self.image[entry:entry+32] = raw

    def unique_name(self,path):
        name = Path(path).name.upper()
        names = {item['name'] for item in self.entries()}
        if re.fullmatch(r'[A-Z0-9_]{1,8}(\.[A-Z0-9_]{1,3})?',name) and name not in names:
            return name
        ext = re.sub('[^A-Z0-9]','',Path(path).suffix.upper()[1:])[:3] or 'BIN'
        for i in range(1,10000):
            name = f'FILE{i:04d}.{ext}'
            if name not in names:
                return name
        raise ValueError('No available import name')


def sample_image(format='JPEG'):
    from PIL import Image,ImageDraw
    image = Image.new('RGB',(176,220),'white')
    draw = ImageDraw.Draw(image)
    for i,color in enumerate(('red','lime','blue','yellow')):
        draw.rectangle((i*44,0,(i+1)*44-1,65),fill=color)
    draw.text((16,95),'B220 SD CARD',fill='black')
    draw.text((16,120),'176 x 220',fill='black')
    draw.rectangle((12,160,163,205),outline='black',width=3)
    stream = io.BytesIO()
    if format=='JPEG':
        image.save(stream,format=format,quality=85,subsampling=1,progressive=False)
    else:
        image.save(stream,format=format)
    return stream.getvalue()


def sample_jpeg():
    return sample_image()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image',type=Path)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--create',action='store_true')
    group.add_argument('--add',type=Path,nargs='+')
    group.add_argument('--list',action='store_true')
    parser.add_argument('--size-mib',type=int,choices=(16,32),default=16)
    parser.add_argument('--sample',action='store_true',help='Include TEST.BMP for native picture-viewer validation')
    args = parser.parse_args()
    if args.create:
        volume = Fat16(create_image(args.size_mib*1024*1024))
        if args.sample:
            volume.add_file('TEST.BMP',sample_image('BMP'))
        args.image.parent.mkdir(parents=True,exist_ok=True)
        with args.image.open('xb') as stream:
            stream.write(volume.image)
    else:
        with CardImage(args.image) as card:
            volume = Fat16(card.data)
            if args.add:
                for path in args.add:
                    name = volume.unique_name(path)
                    volume.add_file(name,path.read_bytes())
                    print(name)
                card.replace_contents(volume.image)
    if args.list or args.create:
        for entry in volume.entries():
            print(entry['name'],entry['size'],'DIR' if entry['directory'] else '')


if __name__ == '__main__':
    main()
