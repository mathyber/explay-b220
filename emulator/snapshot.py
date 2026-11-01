"""Versioned emulator snapshots: JSON and checked binary blocks, without pickle.

CPU registers are exported through Unicorn's register API, including ARM banks.
No native contexts, Python instances, callbacks, or host pointers enter the file.
"""
from collections import Counter, deque
import hashlib
import json
import os
from pathlib import Path
import tempfile
import zipfile

import PIL
import unicorn
from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_SP, UC_ARM_REG_LR,
                               UC_ARM_REG_PC, UC_ARM_REG_CPSR, UC_ARM_REG_SPSR)
from .board import Board, ROOT


FORMAT = 'explay-b220-state'
VERSION = 1
MAX_MANIFEST = 16*1024*1024
MAX_DATA = 64*1024*1024
REGISTERS = [UC_ARM_REG_R0+i for i in range(13)]+[UC_ARM_REG_SP,UC_ARM_REG_LR,UC_ARM_REG_PC]
BANKS = {'usr':0x10,'fiq':0x11,'irq':0x12,'svc':0x13,'abt':0x17,'und':0x1b}
# Stateful CP15 registers exposed by the current Unicorn ARM926 model.
# ARM DDI0198E, chapter 2. TCM region registers are not exposed by this model.
CP15 = ((1,0,0),(2,0,0),(3,0,0),(5,0,0),(5,0,1),(6,0,0),
        (9,0,0),(9,0,1),(10,0,0),(10,0,1),(13,0,0),(13,0,1))
STATE_FIELDS = {
    'lcd_backlight','last_window_id','nv_seed','loaded_flash','storage',
    'dma_gate','timer','block_serial','next_timer_block','timer_epoch',
    'lcd_dma','dma_enabled','full_lcd_frames','_lcd_frame_pixels','adc',
}
DEVICES = ('flash','lcd','dsp','irq','keypad','jpeg','rtc')
# Constructor-derived configuration, host execution controls and diagnostics.
# Keeping this list explicit detects newly added state that needs a decision.
EXCLUDED_FIELDS = {
    'profile','explore','fast_cpu','frame_callback','uc','ram','md','_cpsr_value',
    'mmio_windows','window_log_pointer','backlight_log_pointer','image_sha256',
    'chip_id','known_registers','flash_overlays','spi','spi_controllers',
    'countdown_timers','lcd_ports','pages','pending_frame_stop','stop_after_frames',
    'irq_entries','key_callbacks','dma_transfers','mmio_counts','mmio_pc_counts',
    'mmio_samples','mmio_tail','blocks','block_tail','logs','milestones',
    'endian_repairs','stop','started_at','deadline','_running',
}


class SnapshotError(ValueError):
    """Invalid, incompatible or unsupported snapshot."""


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _json(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf-8')


def _model_hash():
    names = ('board','snapshot','nor','lcd','dsp','irq','keypad','jpeg','rtc','spi','timer')
    return _hash(b''.join(name.encode()+b'\0'+(ROOT/'emulator'/f'{name}.py').read_text(encoding='utf-8-sig').encode()
                         for name in names))


def _dependencies():
    return dict(unicorn=unicorn.__version__,engine=list(unicorn.uc_version()),pillow=PIL.__version__)


def _u32(value):
    if type(value) is not int or not 0 <= value <= 0xffffffff:
        raise SnapshotError('CPU register must be an unsigned 32-bit integer')
    return value


def cpu_state(board):
    """Read banks from a temporary context; leave the running register file alone."""
    uc = board.uc
    context = uc.context_save()
    cpsr = context.reg_read(UC_ARM_REG_CPSR)
    registers = [context.reg_read(r) for r in REGISTERS]
    banks = {}
    for name,mode in BANKS.items():
        context.reg_write(UC_ARM_REG_CPSR,(cpsr&~0x1f)|mode)
        ids = [UC_ARM_REG_R0+i for i in range(8,13)]+[UC_ARM_REG_SP,UC_ARM_REG_LR] if name in ('usr','fiq') else [UC_ARM_REG_SP,UC_ARM_REG_LR]
        banks[name] = dict(registers=[context.reg_read(r) for r in ids],
                           spsr=context.reg_read(UC_ARM_REG_SPSR) if name != 'usr' else None)
    cp15 = [uc.cpr_read(15,0,n,m,o,0,False) for n,m,o in CP15]
    return dict(cpsr=cpsr,registers=registers,banks=banks,cp15=cp15)


def _validate_cpu(state):
    if set(state) != {'cpsr','registers','banks','cp15'}:
        raise SnapshotError('Invalid CPU state fields')
    cpsr = _u32(state['cpsr'])
    if cpsr&0x1f not in (*BANKS.values(),0x1f) or cpsr&0x01000000:
        raise SnapshotError('Unsupported CPU mode in snapshot')
    if len(state['registers']) != 16 or len(state['cp15']) != len(CP15) or set(state['banks']) != set(BANKS):
        raise SnapshotError('Incomplete CPU register file')
    for value in state['registers']+state['cp15']:
        _u32(value)
    if state['registers'][-1]&(1 if cpsr&0x20 else 3):
        raise SnapshotError('Unaligned program counter')
    for name,bank in state['banks'].items():
        if set(bank) != {'registers','spsr'} or len(bank['registers']) != (7 if name in ('usr','fiq') else 2):
            raise SnapshotError('Incomplete ARM register bank')
        for value in bank['registers']:
            _u32(value)
        if name == 'usr':
            if bank['spsr'] is not None:
                raise SnapshotError('User bank cannot have SPSR')
        else:
            _u32(bank['spsr'])


def _restore_cpu(board,state):
    uc,cpsr = board.uc,state['cpsr']
    for (n,m,o),value in zip(CP15[1:],state['cp15'][1:]):
        uc.cpr_write(15,0,n,m,o,0,False,value)
    uc.cpr_write(15,0,*CP15[0],0,False,state['cp15'][0])
    for name,mode in BANKS.items():
        uc.reg_write(UC_ARM_REG_CPSR,(cpsr&~0x1f)|mode)
        ids = [UC_ARM_REG_R0+i for i in range(8,13)]+[UC_ARM_REG_SP,UC_ARM_REG_LR] if name in ('usr','fiq') else [UC_ARM_REG_SP,UC_ARM_REG_LR]
        for register,value in zip(ids,state['banks'][name]['registers']):
            uc.reg_write(register,value)
        if name != 'usr':
            uc.reg_write(UC_ARM_REG_SPSR,state['banks'][name]['spsr'])
    uc.reg_write(UC_ARM_REG_CPSR,cpsr)
    for register,value in zip(REGISTERS[:-1],state['registers'][:-1]):
        uc.reg_write(register,value)
    uc.reg_write(UC_ARM_REG_PC,state['registers'][-1]|(1 if cpsr&0x20 else 0))
    uc.reg_write(UC_ARM_REG_CPSR,cpsr)
    uc.ctl_flush_tb()


class _Writer:
    def __init__(self):
        self.blobs = {}

    def blob(self,data):
        data = bytes(data)
        key = f'data/{_hash(data)}.bin'
        self.blobs[key] = data
        return key

    def encode(self,value):
        if value is None or type(value) in (bool,int,str):
            return value
        if type(value) is list:
            return [self.encode(v) for v in value]
        if type(value) in (bytes,bytearray):
            return dict(kind=type(value).__name__,blob=self.blob(value))
        if type(value) in (dict,Counter):
            pairs = [[self.encode(k),self.encode(v)] for k,v in value.items()]
            pairs.sort(key=lambda pair:_json(pair[0]))
            return dict(kind='counter' if type(value) is Counter else 'dict',items=pairs)
        if type(value) in (tuple,set,deque):
            items = [self.encode(v) for v in value]
            if type(value) is set:
                items.sort(key=_json)
            result = dict(kind=type(value).__name__,items=items)
            if type(value) is deque:
                result['maxlen'] = value.maxlen
            return result
        raise SnapshotError(f'Unsupported state value: {type(value).__name__}')


class _Reader:
    def __init__(self,archive,descriptions):
        self.archive,self.descriptions,self.blobs = archive,descriptions,{}

    def blob(self,key):
        if key not in self.descriptions:
            raise SnapshotError('Unknown binary block reference')
        if key not in self.blobs:
            data = self.archive.read(key)
            if _hash(data) != self.descriptions[key]['sha256']:
                raise SnapshotError(f'Binary block checksum mismatch: {key}')
            self.blobs[key] = data
        return self.blobs[key]

    def decode(self,value,depth=0):
        if depth > 64:
            raise SnapshotError('State nesting is too deep')
        if value is None or type(value) in (bool,int,str):
            return value
        decode = lambda v:self.decode(v,depth+1)
        if type(value) is list:
            return [decode(v) for v in value]
        if type(value) is not dict:
            raise SnapshotError('Invalid state value')
        kind = value.get('kind')
        if kind in ('bytes','bytearray') and set(value) == {'kind','blob'}:
            data = self.blob(value['blob'])
            return data if kind == 'bytes' else bytearray(data)
        if kind in ('dict','counter') and set(value) == {'kind','items'}:
            result = Counter() if kind == 'counter' else {}
            for key,item in value['items']:
                key = decode(key)
                if key in result:
                    raise SnapshotError('Duplicate dictionary key')
                result[key] = decode(item)
            return result
        if kind in ('tuple','set','deque') and set(value) == ({'kind','items','maxlen'} if kind == 'deque' else {'kind','items'}):
            items = [decode(v) for v in value['items']]
            if kind == 'deque':
                limit = value['maxlen']
                if limit is not None and (type(limit) is not int or not len(items) <= limit <= 1000000):
                    raise SnapshotError('Invalid queue length')
                return deque(items,maxlen=limit)
            return tuple(items) if kind == 'tuple' else set(items)
        raise SnapshotError('Unknown state encoding')


def _devices(board):
    result = {name:getattr(board,name) for name in DEVICES if getattr(board,name) is not None}
    result.update({f'spi:{address:x}':device for address,device in board.spi_controllers.items()})
    result.update({f'timer:{address:x}':device for address,device in board.countdown_timers.items()})
    return result


def _validate_device(device,state):
    if set(state) != set(vars(device)):
        raise SnapshotError(f'Invalid fields for {type(device).__name__}')
    for key,initial in vars(device).items():
        value = state[key]
        if initial is not None and type(value) is not type(initial) and not (key == 'trace_limit' and value is None):
            raise SnapshotError(f'Invalid field type: {type(device).__name__}.{key}')
    if hasattr(device,'gram'):
        if len(state['gram']) != device.width*device.height or any(type(v) is not int or not 0 <= v <= 0xffff for v in state['gram']):
            raise SnapshotError('Invalid LCD GRAM')
    if hasattr(device,'array'):
        if len(state['array']) != len(device.array):
            raise SnapshotError('Invalid NOR length')
        for key in ('source_length','manufacturer','device','parameter_start','parameter_size'):
            if state[key] != getattr(device,key):
                raise SnapshotError('Incompatible NOR geometry')
        if state['array'][:device.source_length] != device.array[:device.source_length]:
            raise SnapshotError('Snapshot contains a different firmware image')


def save_snapshot(board,path):
    """Save a stopped Board atomically. Does not write a separate NOR file."""
    path = Path(path)
    if path.suffix.lower() != '.b220state':
        raise SnapshotError('Snapshot output must use the .b220state extension')
    if getattr(board,'_running',False):
        raise SnapshotError('Stop CPU execution before saving a snapshot')
    extra = set(vars(board))-STATE_FIELDS-EXCLUDED_FIELDS-set(DEVICES)
    if extra:
        raise SnapshotError(f'Unclassified board state fields: {sorted(extra)}')
    board._restore_flash_overlays()
    if _hash(bytes(board.flash.array[:board.flash.source_length])) != board.image_sha256:
        raise SnapshotError('Snapshot contains a different firmware image')
    writer = _Writer()
    state = {key:writer.encode(getattr(board,key)) for key in sorted(STATE_FIELDS)}
    devices = {key:dict(model=type(device).__name__,state={name:writer.encode(value) for name,value in vars(device).items()})
               for key,device in _devices(board).items()}
    memory = []
    for start,end,permissions in board.uc.mem_regions():
        if start == 0x34000000:  # shared ctypes RAM; store its physical image once
            continue
        data = bytes(board.uc.mem_read(start,end-start+1))
        if start == 0 and data != bytes(board.flash.array):
            raise SnapshotError('CPU-visible NOR differs from the flash model')
        memory.append(dict(address=start,size=len(data),permissions=permissions,blob=writer.blob(data)))
    manifest = dict(format=FORMAT,version=VERSION,model_sha256=_model_hash(),dependencies=_dependencies(),
                    firmware_sha256=board.image_sha256,profile_sha256=_hash(_json(board.profile)),
                    options=dict(explore=board.explore,fast_cpu=board.fast_cpu),cpu=cpu_state(board),
                    board=state,devices=devices,memory=memory,
                    blobs={key:dict(size=len(data),sha256=_hash(data)) for key,data in writer.blobs.items()})
    _validate_cpu(manifest['cpu'])
    encoded = _json(manifest)
    if len(encoded) > MAX_MANIFEST or sum(len(data) for data in writer.blobs.values()) > MAX_DATA:
        raise SnapshotError('Snapshot exceeds the supported size limits')
    path.parent.mkdir(parents=True,exist_ok=True)
    descriptor,temporary = tempfile.mkstemp(prefix=f'.{path.stem}-',suffix='.tmp',dir=path.parent)
    try:
        with os.fdopen(descriptor,'wb') as stream:
            with zipfile.ZipFile(stream,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=1) as archive:
                archive.writestr('manifest.json',encoded)
                for key,data in writer.blobs.items():
                    archive.writestr(key,data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    # Translation cache history must not change the block-based virtual clock
    # when comparing continuation here with continuation in a fresh engine.
    board.uc.ctl_flush_tb()
    return dict(path=str(path),version=VERSION,pc=hex(board.uc.reg_read(UC_ARM_REG_PC)),
                virtual_blocks=board.block_serial,size=path.stat().st_size)


def _load(archive,profile,frame_callback):
    infos = archive.infolist()
    names = [info.filename for info in infos]
    if len(names) != len(set(names)) or len(names) > 4096 or 'manifest.json' not in names:
        raise SnapshotError('Invalid snapshot archive entries')
    if archive.getinfo('manifest.json').file_size > MAX_MANIFEST or sum(i.file_size for i in infos) > MAX_DATA+MAX_MANIFEST:
        raise SnapshotError('Snapshot exceeds the supported size limits')
    manifest = json.loads(archive.read('manifest.json'))
    if manifest['format'] != FORMAT or manifest['version'] != VERSION:
        raise SnapshotError('Unsupported snapshot format/version')
    if manifest['model_sha256'] != _model_hash() or manifest['dependencies'] != _dependencies():
        raise SnapshotError('Snapshot was created by a different emulator model or dependency version')
    if manifest['profile_sha256'] != _hash(_json(profile)) or manifest['firmware_sha256'] != profile['ps_sha256']:
        raise SnapshotError('Snapshot does not match the selected profile/firmware')
    blobs = manifest['blobs']
    if set(names) != {'manifest.json',*blobs}:
        raise SnapshotError('Unexpected/missing snapshot binary blocks')
    for key,description in blobs.items():
        if key != f"data/{description['sha256']}.bin" or not 0 <= description['size'] <= MAX_DATA or archive.getinfo(key).file_size != description['size']:
            raise SnapshotError('Invalid binary block description')
    reader = _Reader(archive,blobs)
    for key in blobs:
        reader.blob(key)  # validate every checksum before restoring anything
    _validate_cpu(manifest['cpu'])
    options = manifest['options']
    if set(options) != {'explore','fast_cpu'} or any(type(v) is not bool for v in options.values()):
        raise SnapshotError('Invalid snapshot execution options')
    board = Board(profile=profile,frame_callback=frame_callback,**options)
    if set(manifest['board']) != STATE_FIELDS:
        raise SnapshotError('Invalid board state fields')
    state = {key:reader.decode(value) for key,value in manifest['board'].items()}
    devices = _devices(board)
    if set(devices) != set(manifest['devices']):
        raise SnapshotError('Incompatible device set')
    device_states = {}
    for key,device in devices.items():
        encoded = manifest['devices'][key]
        if encoded['model'] != type(device).__name__:
            raise SnapshotError('Incompatible peripheral model')
        values = {name:reader.decode(value) for name,value in encoded['state'].items()}
        _validate_device(device,values)
        device_states[key] = values
    original = {(a,b-a+1,p) for a,b,p in board.uc.mem_regions() if a != 0x34000000}
    remaining = set(original)
    memory,previous_end = [],0
    for region in sorted(manifest['memory'],key=lambda r:r['address']):
        start,size,permissions = region['address'],region['size'],region['permissions']
        if type(start) is not int or type(size) is not int or start&0xfff or size&0xfff or size <= 0 or start < previous_end or start+size > 0x100000000:
            raise SnapshotError('Invalid/overlapping memory region')
        previous_end = start+size
        identity = (start,size,permissions)
        if identity in original:
            remaining.discard(identity)
        elif permissions != 3 or not any(lo <= start and start+size <= hi for lo,hi in board.mmio_windows):
            raise SnapshotError('Snapshot maps memory outside the board layout')
        data = reader.blob(region['blob'])
        if len(data) != size:
            raise SnapshotError('Memory image length mismatch')
        if start == 0 and data != bytes(device_states['flash']['array']):
            raise SnapshotError('CPU-visible NOR differs from the flash model')
        memory.append((start,size,permissions,data))
    if remaining:
        raise SnapshotError('Snapshot is missing required memory regions')
    for key,value in state.items():
        initial = getattr(board,key)
        if initial is not None and type(value) is not type(initial) and not (key == 'nv_seed' and value is None):
            raise SnapshotError(f'Invalid board field type: {key}')
    if any(type(state[k]) is not int or state[k] < 0 for k in ('block_serial','next_timer_block','timer_epoch','dma_enabled','full_lcd_frames','_lcd_frame_pixels')):
        raise SnapshotError('Invalid virtual clock/DMA/frame counters')
    for key,values in device_states.items():
        vars(devices[key]).update(values)
    vars(board).update(state)
    for start,size,permissions,data in memory:
        if (start,size,permissions) not in original:
            board.uc.mem_map(start,size,permissions)
            board.pages.update(range(start,start+size,0x1000))
        board.uc.mem_write(start,data)
    _restore_cpu(board,manifest['cpu'])
    if cpu_state(board) != manifest['cpu']:
        raise SnapshotError('CPU register state cannot be restored exactly by this engine')
    return board


def load_snapshot(path,profile=None,frame_callback=None):
    """Return a new Board; a failed load never changes an existing instance."""
    profile = profile or json.loads((ROOT/'emulator/profiles/b220-l7a.json').read_text())
    try:
        with zipfile.ZipFile(path,'r') as archive:
            return _load(archive,profile,frame_callback)
    except SnapshotError:
        raise
    except (OSError,ValueError,KeyError,TypeError,IndexError,RecursionError,RuntimeError,
            NotImplementedError,EOFError,zipfile.BadZipFile,unicorn.UcError) as exc:
        raise SnapshotError(f'Cannot load snapshot: {exc}') from exc
