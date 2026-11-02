"""Execute an SD-resident application through native SFS and the firmware VM."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import time

from unicorn import UC_HOOK_CODE,UC_HOOK_MEM_READ
from unicorn.arm_const import UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3,UC_ARM_REG_SP,UC_ARM_REG_PC
from emulator.board import Board,ROOT
from emulator.snapshot import save_snapshot
from .b22asm import assemble
from .sd_image import Fat16,create_image
from .build_sd_app import DIRECTORY,build
from .build_catch_game import elf_payload,ROM,LIMIT
from .prove_catch_game import run_for
from .prove_key_marker import execute

DEFAULT_KEYS=['ok','right','down','ok','soft_left','soft_right','down','ok','soft_right','soft_right']


def compare_proofs(directory):
    from PIL import Image
    report=json.loads((directory/'proof-multi/proof.json').read_text(encoding='utf-8'))
    checks=dict(run_passed=report['status']=='PASS',
                current_ps_matches=hashlib.sha256((directory/'sd-app.bin').read_bytes()).hexdigest()==report['firmware']['sha256'],
                different_programs=report['program_sha256']!=report['second_sha256'],
                both_read_from_same_card=all(any(r.get('sha256')==digest and r['result']==0 for r in report['file_reads'])
                                            for digest in (report['program_sha256'],report['second_sha256'])),
                direct_selector=report['direct_selector'],clean_lower_strip=report['clean_lower_strip'],
                no_host_injection=report['host_cpu_or_ram_writes'] is False)
    with Image.open(report['steps'][9]['frame']) as a,Image.open(report['steps'][16]['frame']) as b:
        checks['different_lcd']=a.tobytes()!=b.tobytes()
    if not all(checks.values()):raise ValueError(f'Application selector comparison failed: {checks}')
    result=dict(status='PASS',checks=checks,firmware_sha256=report['firmware']['sha256'])
    (directory/'comparison.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    return result


def state(board):
    values=struct.unpack('>20I',bytes(board.uc.mem_read(0x04388f50,80)))
    result=dict(zip(('magic','code','size','status','loads','pc',
                    *[f'r{i}' for i in range(8)],'steps','checksum','event','mode','catalog','io_count'),values))
    return result


def catalog_state(board):
    pointer=state(board)['catalog']
    if not pointer:return dict(count=0,selected=0,names=[])
    count,selected=struct.unpack('>2I',bytes(board.uc.mem_read(pointer,8)))
    if count>16:raise ValueError('Invalid catalog count')
    return dict(count=count,selected=selected,names=[bytes(board.uc.mem_read(pointer+8+i*13,13)).split(b'\0')[0].decode('ascii') for i in range(count)])


def checksum(data):
    value=2166136261
    for byte in data:value=((value^byte)*16777619)&0xffffffff
    return value


def prove(directory,application,keys,missing=False,corrupt=False):
    directory.mkdir(parents=True,exist_ok=True)
    firmware=DIRECTORY/'sd-app.bin'
    manifest=json.loads((DIRECTORY/'manifest.json').read_text(encoding='utf-8'))
    if hashlib.sha256((ROOT/'patches/sd_app.c').read_bytes()).hexdigest()!=manifest['toolchain']['source_sha256']:
        raise ValueError('Loader source changed; rebuild the image')
    payload,entry=elf_payload((DIRECTORY/'catch-game.elf').read_bytes())
    expected,_=build(next((ROOT/'analysis/extracted').glob('SC*.bin')).read_bytes(),payload,entry)
    if firmware.read_bytes()!=expected:raise ValueError('Rebuild the SD loader image')
    program=assemble(application.read_text(encoding='utf-8-sig'))
    if corrupt:program=b'BAD!'+program[4:]
    volume=Fat16(create_image())
    second=assemble((ROOT/'apps/card_second.b22asm').read_text(encoding='utf-8'))
    if not missing:
        volume.add_file('DEMO.B22',program)
        volume.add_file('SECOND.B22',second)
        volume.add_file('APPS.LST',b'DEMO.B22\r\nSECOND.B22\r\n')
    (directory/'input-card.img').write_bytes(volume.image)
    (directory/'APP.B22').write_bytes(program)
    board=Board(explore=True,firmware_path=firmware,sd_image=volume.image)
    report=dict(status='RUNNING',firmware=board.firmware,application=str(application),
                program_sha256=hashlib.sha256(program).hexdigest(),host_cpu_or_ram_writes=False,
                file_opens=[],file_reads=[],file_closes=0,heap_frees=[],bytecode_reads=[],steps=[],windows=[],
                second_sha256=hashlib.sha256(second).hexdigest())
    hooks=[];pending={}
    def opened(uc,pc,size,_):
        address=uc.reg_read(UC_ARM_REG_R0)
        try:
            data=bytes(uc.mem_read(address,128))
            end=next((i for i in range(0,len(data),2) if data[i:i+2]==b'\0\0'),len(data))
            name=data[:end].decode('utf-16-be')
        except Exception:name='?'
        if name.startswith('E:\\') and (name.endswith('.B22') or name.endswith('APPS.LST')):
            report['file_opens'].append(dict(path=name,flags=hex(uc.reg_read(UC_ARM_REG_R1))))
    def read(uc,pc,size,_):
        pending[uc.reg_read(UC_ARM_REG_SP)]=dict(handle=uc.reg_read(UC_ARM_REG_R0),
            buffer=uc.reg_read(UC_ARM_REG_R1),requested=uc.reg_read(UC_ARM_REG_R2),
            count_pointer=uc.reg_read(UC_ARM_REG_R3))
    def read_return(uc,pc,size,_):
        item=pending.pop(uc.reg_read(UC_ARM_REG_SP),None)
        if item and item['requested']<=4096:
            item.update(result=uc.reg_read(UC_ARM_REG_R0),actual=int.from_bytes(uc.mem_read(item['count_pointer'],4),'big'))
            if item['actual']<=4096:
                item['sha256']=hashlib.sha256(uc.mem_read(item['buffer'],item['actual'])).hexdigest()
            report['file_reads'].append(item)
    def closed(*_):report['file_closes']+=1
    def freed(uc,pc,size,_):
        owner=state(board);pointer=uc.reg_read(UC_ARM_REG_R0)
        if owner['magic']==0x53444150 and pointer==owner['code'] and pointer:
            report['heap_frees'].append(pointer)
    def window(uc,pc,size,_):
        win=uc.reg_read(UC_ARM_REG_R0)
        if win not in report['windows']:report['windows'].append(win)
    for pc,callback in ((0x5edc5e,opened),(0x5ee49e,read),(0x5ee4ce,read_return),(0x5ee484,closed),(0x6a5468,freed)):
        hooks.append(board.uc.hook_add(UC_HOOK_CODE,callback,begin=pc,end=pc))
    for pc in (0x4d6dda,0x4d6dea):hooks.append(board.uc.hook_add(UC_HOOK_CODE,window,begin=pc,end=pc))
    def capture(name):
        frame=board.lcd.save(directory,name)
        save_snapshot(board,directory/(name+'.b220state'))
        return frame
    try:
        print('Cold boot with isolated SD card...',flush=True)
        board.stop_after_frames=5;deadline=time.monotonic()+300
        while board.full_lcd_frames<5:
            if time.monotonic()>deadline:raise ValueError('Cold boot timed out')
            result=execute(board,200000000,5)
            if result['engine_error'] or result['stop']['reason'] not in ('wall time budget','instruction budget','requested LCD frame count'):
                raise ValueError(f'Unexpected boot stop: {result}')
        board.stop_after_frames=0
        target=board.block_serial+512000
        while board.block_serial<target:run_for(board,.5)
        run_for(board,3)
        capture('boot')
        route=['soft_left','soft_left','soft_left','down','ok','up','up','ok','ok']
        for index,key in enumerate(route+keys):
            board.keypad.key(key,True);board.keypad.key(key,False)
            run_for(board,6)
            if index==8:
                deadline=time.monotonic()+60
                while True:
                    opening=state(board)
                    if opening['magic']==0x53444150 and (catalog_state(board)['count'] or opening['status']):break
                    if time.monotonic()>deadline:raise ValueError('Native SD application did not finish opening')
                    run_for(board,.5)
                run_for(board,1)
            current=state(board)
            report['steps'].append(dict(key=key,state=current,catalog=catalog_state(board),frame=capture(f'{index:02d}-{key}')))
            print(json.dumps(dict(step=index,key=key,state=current)),flush=True)
            if index==8:
                expected_status=1 if missing else 0
                if current['magic']!=0x53444150 or current['status']!=expected_status:
                    raise ValueError(f'Unexpected loader state: {current}')
                if current['mode']!=0 or current['code']:raise ValueError('Selector did not open directly')
                if not missing and catalog_state(board)['names']!=['DEMO.B22','SECOND.B22']:
                    raise ValueError('Both applications were not listed')
            if index==9 and not missing:
                if current['status']!=(5 if corrupt else 0):raise ValueError('Unexpected application status')
                if not corrupt:
                    code=current['code']
                    if bytes(board.uc.mem_read(code,len(program)))!=program or current['checksum']!=checksum(program):
                        raise ValueError('Guest buffer differs from the card application')
                    def fetch(uc,access,address,size,value,_):
                        if len(report['bytecode_reads'])<128:
                            report['bytecode_reads'].append(dict(address=hex(address),size=size,pc=hex(uc.reg_read(UC_ARM_REG_PC))))
                    hooks.append(board.uc.hook_add(UC_HOOK_MEM_READ,fetch,begin=code+16,end=code+16+int.from_bytes(program[8:10],'big')*8-1))
            if index>9 and not missing and not corrupt:
                if key=='soft_right' and current['code']:
                    raise ValueError('Application buffer was not released on exit')
                if key=='soft_left' and (current['loads']!=2 or current['status']):
                    raise ValueError('F1 did not reload the program through SFS')
                if application.name=='card_demo.b22asm':
                    positions={10:(76,80),11:(76,84),12:(72,80),13:(72,80),16:(48,64)}
                    if keys==DEFAULT_KEYS and index in positions:
                        if (current['r0'],current['r1'])!=positions[index]:raise ValueError('SD program movement/reset check failed')
            if index in (9,10,16) and not missing:
                from PIL import Image
                with Image.open(report['steps'][-1]['frame']) as frame:
                    if any(pixel!=(0,0,0) for pixel in frame.convert('RGB').crop((0,176,176,196)).getdata()):
                        raise ValueError('Previous window pixels remain in the lower content strip')
        if not missing and not corrupt:
            if not any(r.get('sha256')==report['program_sha256'] and r['result']==0 and r['actual']==len(program) for r in report['file_reads']):
                raise ValueError('Native SFS_ReadFile did not read the card program')
            if keys and not any(ROM<=int(r['pc'],0)<LIMIT for r in report['bytecode_reads']):
                raise ValueError('VM did not fetch instructions from the file buffer')
            if keys==DEFAULT_KEYS:
                if not any(r.get('sha256')==report['second_sha256'] for r in report['file_reads']):
                    raise ValueError('Second application was not read from the same SD card')
                if state(board)['catalog'] or state(board)['code']:raise ValueError('Application window did not release all buffers')
        if 0x160001 in report['windows']:raise ValueError('Legacy New game menu was opened')
        if hashlib.sha256(board.uc.mem_read(0,board.flash.source_length)).hexdigest()!=board.image_sha256:
            raise ValueError('CPU-visible firmware changed')
        report.update(status='PASS',final_state=state(board),sd=board.spi.report(),
                      direct_selector=True,clean_lower_strip=True)
        (directory/'proof.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        return report
    except Exception as exc:
        report.update(status='FAIL',error=str(exc),final_state=state(board))
        capture('failed')
        (directory/'proof.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        raise
    finally:
        for hook in hooks:board.uc.hook_del(hook)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,default=DIRECTORY/'proof-multi')
    parser.add_argument('--application',type=Path,default=ROOT/'apps/card_demo.b22asm')
    parser.add_argument('--keys',nargs='*',default=DEFAULT_KEYS)
    parser.add_argument('--missing',action='store_true')
    parser.add_argument('--corrupt',action='store_true')
    parser.add_argument('--compare',action='store_true',help='Check both applications in the proof-multi run')
    args=parser.parse_args()
    if args.compare:
        try:result=compare_proofs(DIRECTORY)
        except (OSError,ValueError) as exc:parser.error(str(exc))
        print(json.dumps(result,indent=2));return
    try:report=prove(args.directory,args.application,args.keys,args.missing,args.corrupt)
    except (ValueError,OSError) as exc:parser.error(str(exc))
    print(json.dumps(dict(status=report['status'],state=report['final_state'],
                          file_opens=report['file_opens'],file_reads=report['file_reads']),indent=2))


if __name__=='__main__':main()
