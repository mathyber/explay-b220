"""Extract structurally validated standard media; preserve original bytes.

Pillow is only used to validate image decoding and build a contact sheet.
Raw/custom RGB resources and proprietary image tables need separate decoding.
"""
from pathlib import Path
import collections
import hashlib
import html
import io
import json
import re
import struct
import zlib
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]


def gif_end(data, start):
    pos = start+13
    flags = data[start+10]
    if flags&128:
        pos += 3*(2**((flags&7)+1))
    for _ in range(10000):
        tag = data[pos]
        pos += 1
        if tag == 0x3b:
            return pos
        if tag == 0x21:
            pos += 1
        elif tag == 0x2c:
            flags = data[pos+8]
            pos += 9
            if flags&128:
                pos += 3*(2**((flags&7)+1))
            pos += 1  # LZW minimum code size
        else:
            raise ValueError('Invalid GIF block')
        for _ in range(100000):
            n = data[pos]
            pos += 1
            if n == 0:
                break
            pos += n
        else:
            raise ValueError('GIF sub-block limit')
    raise ValueError('GIF block limit')


def main():
    path = next((ROOT/'analysis/extracted').glob('SC*.bin'))
    data = path.read_bytes()
    out = ROOT/'analysis/resources'
    out.mkdir(parents=True,exist_ok=True)
    entries, rejected, pictures = [], [], []
    sigs = {'jpg':rb'\xff\xd8\xff','png':rb'\x89PNG\r\n\x1a\n',
            'gif':rb'GIF8[79]a','riff':rb'RIFF','mid':rb'MThd'}
    for kind, pattern in sigs.items():
        for match in re.finditer(pattern,data):
            start = match.start()
            try:
                if kind == 'jpg':
                    end = data.find(b'\xff\xd9',start+3)
                    if end < 0:
                        raise ValueError('No EOI')
                    end += 2
                elif kind == 'png':
                    pos = start+8
                    for _ in range(1000):
                        length = struct.unpack_from('>I',data,pos)[0]
                        tag = data[pos+4:pos+8]
                        if length > 0x400000 or pos+length+12 > len(data):
                            raise ValueError('PNG chunk bounds')
                        chunk = data[pos+4:pos+8+length]
                        crc = struct.unpack_from('>I',data,pos+8+length)[0]
                        if zlib.crc32(chunk) != crc:
                            raise ValueError('PNG chunk CRC')
                        pos += length+12
                        if tag == b'IEND':
                            break
                    else:
                        raise ValueError('PNG missing IEND')
                    end = pos
                elif kind == 'gif':
                    end = gif_end(data,start)
                elif kind == 'riff':
                    length = struct.unpack_from('<I',data,start+4)[0]
                    end = start+8+length
                    if data[start+8:start+12] not in (b'WAVE',b'AVI ',b'WEBP'):
                        raise ValueError('Unsupported RIFF form')
                    pos = start+12
                    while pos+8 <= end:
                        chunk_length = struct.unpack_from('<I',data,pos+4)[0]
                        pos += 8+chunk_length+(chunk_length&1)
                    if pos != end:
                        raise ValueError('RIFF chunk bounds')
                else:
                    length = struct.unpack_from('>I',data,start+4)[0]
                    if length != 6:
                        raise ValueError('MIDI header length')
                    fmt,tracks,division = struct.unpack_from('>HHH',data,start+8)
                    if fmt > 2 or not 1 <= tracks <= 256 or not division:
                        raise ValueError('MIDI header fields')
                    end = start+14
                    for _ in range(tracks):
                        if data[end:end+4] != b'MTrk':
                            raise ValueError('Missing MIDI track')
                        end += 8+struct.unpack_from('>I',data,end+4)[0]
                if not start < end <= len(data) or end-start > 0x400000:
                    raise ValueError('Resource bounds')
                payload = data[start:end]
                item = dict(offset=start,offset_hex=hex(start),size=len(payload),kind=kind,
                            sha256=hashlib.sha256(payload).hexdigest())
                suffix = kind
                if kind in ('jpg','png','gif'):
                    im = Image.open(io.BytesIO(payload))
                    im.load()
                    item.update(width=im.width,height=im.height,frames=getattr(im,'n_frames',1))
                    preview = im.convert('RGB')
                    preview.thumbnail((176,190))
                    pictures.append((item,preview))
                elif kind == 'riff':
                    item['form'] = payload[8:12].decode('ascii')
                    suffix = {'WAVE':'wav','AVI ':'avi','WEBP':'webp'}[item['form']]
                    if item['form'] == 'WAVE':
                        pos = 12
                        while pos+8 <= len(payload):
                            length = struct.unpack_from('<I',payload,pos+4)[0]
                            if payload[pos:pos+4] == b'fmt ' and length >= 16:
                                fmt,channels,rate,byte_rate,align,bits = struct.unpack_from('<HHIIHH',payload,pos+8)
                                item['wave_format'] = dict(tag=fmt,channels=channels,rate=rate,byte_rate=byte_rate,block_align=align,bits=bits)
                            pos += 8+length+(length&1)
                item['file'] = f'{start:08x}.{suffix}'
                (out/item['file']).write_bytes(payload)
                entries.append(item)
            except (ValueError, IndexError, struct.error, OSError, OverflowError) as e:
                rejected.append(dict(offset=hex(start),kind=kind,reason=str(e)))
    entries.sort(key=lambda x:x['offset'])
    report = dict(source=path.name,source_sha256=hashlib.sha256(data).hexdigest(),
                  counts=dict(collections.Counter(e['kind'] for e in entries)),
                  entries=entries,rejected=rejected)
    (out/'manifest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    # Paginate sheets so every image can be inspected without huge dimensions.
    for page in range(0,len(pictures),48):
        batch = pictures[page:page+48]
        sheet = Image.new('RGB',(6*200,((len(batch)+5)//6)*230),'#e5e7eb')
        draw = ImageDraw.Draw(sheet)
        for i,(item,preview) in enumerate(batch):
            x,y = (i%6)*200,(i//6)*230
            sheet.paste(preview,(x+(200-preview.width)//2,y+5))
            draw.text((x+6,y+198),f"{item['offset_hex']} {item['width']}x{item['height']}",fill='black')
            draw.text((x+6,y+213),f"{item['kind']} {item['size']} bytes",fill='black')
        sheet.save(out/f'contact_sheet_{page//48+1:02}.png')
    rows = []
    for e in entries:
        link = html.escape(e['file'])
        preview = f'<img loading="lazy" src="{link}">' if e['kind'] in ('jpg','png','gif') else ''
        rows.append(f'<tr><td><a href="{link}">{link}</a></td><td>{e["size"]}</td><td>{e.get("width", "")} x {e.get("height", "")}</td><td>{preview}</td></tr>')
    (out/'index.html').write_text('<!doctype html><meta charset="utf-8"><title>Explay B220 resources</title><style>body{font:16px sans-serif;margin:2rem}td{padding:10px;border-bottom:1px solid #ddd}img{max-width:176px;max-height:220px}</style><h1>Extracted standard media</h1><p>Offsets refer to the extracted PS binary. Original bytes; proprietary resources are not included.</p><table>'+''.join(rows)+'</table>',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('entries','rejected')},indent=2))
    print('Rejected',len(rejected),'false or invalid signatures')


if __name__ == '__main__':
    main()
