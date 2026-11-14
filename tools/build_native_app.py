"""Compile a freestanding C program into a GO1N native SD application.

Run as python -m tools.build_native_app apps/card_native.c [--out DEMO.GO1].
The program is linked twice, at 0 and at DELTA (patches/native_app.ld); words
that differ by DELTA hold program addresses and become the GO1N relocation
table. Firmware addresses are plain constants and are not relocated.
See patches/go1.h for the file format and the loader interface.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
import re
from pathlib import Path
import struct
import subprocess
import tempfile

from emulator.firmware import ROOT
from .setup_arm_toolchain import EXECUTABLE

MAGIC, VERSION, HEADER = b'GO1N', 1, 32
MAX_FILE = 0x100000
LINKER = 'patches/native_app.ld'
# Second link address: every word that differs by exactly this amount holds an
# address inside the program. This also catches linker-made interworking thunks,
# whose literals --emit-relocs does not report.
DELTA = 0x10000000
ELF_HEADER = bytes((0x7f, 0x45, 0x4c, 0x46, 1, 2, 1))  # ELF32, big-endian, version 1


def sections(data):
    if len(data) < 52 or data[:7] != ELF_HEADER or struct.unpack_from('>HH', data, 16) != (2, 40):
        raise ValueError('Expected an executable ELF32 big-endian ARM image')
    table = struct.unpack_from('>I', data, 32)[0]
    stride, count, index = struct.unpack_from('>HHH', data, 46)
    if stride != 40 or not count or index >= count or table+stride*count > len(data):
        raise ValueError('Invalid ELF section table')
    raw = [struct.unpack_from('>10I', data, table+i*stride) for i in range(count)]
    names = data[raw[index][4]:raw[index][4]+raw[index][5]]
    return [dict(index=i, name=names[s[0]:].split(bytes(1), 1)[0].decode(), type=s[1], flags=s[2],
                 address=s[3], offset=s[4], size=s[5]) for i, s in enumerate(raw)]


def linked(data, base):
    """Image bytes, .bss size and entry offset of an ELF linked at base."""
    found = {s['name']: s for s in sections(data)}
    image, bss = found.get('.image'), found.get('.bss')
    if not image or image['type'] != 1 or image['address'] != base or not image['size']:
        raise ValueError(f'Expected a .image section linked at {base:#x}')
    others = [s['name'] for s in found.values() if s['flags'] & 2 and s['size'] and s['name'] not in ('.image', '.bss')]
    if others:
        raise ValueError(f'Unexpected allocated sections: {others}')
    bss_size = 0
    if bss and bss['size']:
        if bss['type'] != 8 or bss['address'] != base+image['size']:
            raise ValueError('.bss must directly follow .image')
        bss_size = bss['size']
    entry = struct.unpack_from('>I', data, 24)[0]-base
    return data[image['offset']:image['offset']+image['size']], bss_size, entry


def native_file(low, high):
    """GO1N bytes from the same program linked at 0 and at DELTA."""
    body, bss_size, entry = linked(low, 0)
    other, other_bss, other_entry = linked(high, DELTA)
    if len(other) != len(body) or other_bss != bss_size or other_entry != entry:
        raise ValueError('Program layout depends on its link address')
    relocations = []
    for offset in range(0, len(body)-3, 4):
        a, b = struct.unpack_from('>I', body, offset)[0], struct.unpack_from('>I', other, offset)[0]
        if a == b:
            continue
        if (b-a) & 0xffffffff != DELTA or a >= len(body)+bss_size:
            raise ValueError(f'Word at {offset:#x} changes unlike an address ({a:#x} -> {b:#x})')
        relocations.append(offset)
    tail = len(body) & ~3
    if body[tail:] != other[tail:]:
        raise ValueError('Unaligned address at the end of the image')
    if not entry & 1 or (entry & ~1) >= len(body):
        raise ValueError('Entry must be a Thumb function inside .image')
    header = MAGIC+struct.pack('>HHIIIIII', VERSION, HEADER, len(body), bss_size, entry, len(relocations), 0, 0)
    result = header+body+b''.join(struct.pack('>I', r) for r in relocations)
    if len(result) > MAX_FILE:
        raise ValueError(f'Program is {len(result)} bytes, limit {MAX_FILE}')
    return result


ICON_SIZE = 16


def icon_bytes(path):
    """16x16 PNG (with transparency) as the GO1N icon block: size, RGB565, alpha."""
    from PIL import Image
    with Image.open(path) as image:
        if image.size != (ICON_SIZE, ICON_SIZE):
            raise ValueError(f'Icon {path} must be {ICON_SIZE}x{ICON_SIZE}, not {image.size[0]}x{image.size[1]}')
        pixels = list(image.convert('RGBA').getdata())
    rgb = b''.join(struct.pack('>H', (r >> 3) << 11 | (g >> 2) << 5 | b >> 3) for r, g, b, _ in pixels)
    return struct.pack('>HH', ICON_SIZE, ICON_SIZE)+rgb+bytes(a for *_, a in pixels)


TITLE_MAX = 24
TITLE_MARK = re.compile(r'go1-title:\s*(.+?)\s*(?:\*/)?\s*$', re.M)


def source_title(source):
    """Program title from a 'go1-title: ...' comment line in the source, or None."""
    match = TITLE_MARK.search(Path(source).read_text(encoding='utf-8'))
    return match.group(1) if match else None


def title_bytes(title):
    if not 0 < len(title) <= TITLE_MAX or any(ord(c) > 0xffff or ord(c) < 32 for c in title):
        raise ValueError(f'Title must be 1..{TITLE_MAX} printable BMP characters: {title!r}')
    return struct.pack('>H', len(title))+title.encode('utf-16-be')


def with_extras(program, title=None, icon=None):
    """Append the optional title and icon and point header +28/+24 at them (go1.h)."""
    if program[24:32] != bytes(8):
        raise ValueError('Program already has a title or icon')
    result = bytearray(program)
    if title:
        struct.pack_into('>I', result, 28, len(result))
        result += title_bytes(title)
    if icon:
        struct.pack_into('>I', result, 24, len(result))
        result += icon_bytes(icon)
    if len(result) > MAX_FILE:
        raise ValueError(f'Program with title and icon is {len(result)} bytes, limit {MAX_FILE}')
    return bytes(result)


def compile_native(source, sources=(), includes=(), icon=None, title=None, libraries=(), library_flags=(), arm_sources=(), defines=()):
    """GO1N program. Defaults: the title from a 'go1-title:' line in the source,
    the icon from a 16x16 PNG with the source's name; both are optional."""
    png = Path(source).with_suffix('.png')
    icon = icon or (png if png.exists() else None)
    title = title or source_title(source)
    return with_extras(_compile(source, sources, includes, libraries, library_flags, arm_sources, defines), title, icon)


def _compile(source, sources=(), includes=(), libraries=(), library_flags=(), arm_sources=(), defines=()):
    """libraries: third-party C files, compiled without warnings and cached by content and flags.
    arm_sources: own C files compiled in ARM state (all warnings are errors), for hot code:
    Thumb on ARMv4T has 8 low registers and spills an interpreter's state to the stack."""
    if not EXECUTABLE.exists():
        raise ValueError('Run python -m tools.setup_arm_toolchain first')
    environment = os.environ.copy()
    environment['ZIG_GLOBAL_CACHE_DIR'] = str(ROOT/'analysis/toolchains/cache')
    environment['ZIG_LOCAL_CACHE_DIR'] = str(ROOT/'analysis/toolchains/local-cache')

    def execute(command):
        run = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, text=True, encoding='utf-8')
        if run.returncode:
            raise ValueError(f'ARM build failed:\n{" ".join(command)}\n{run.stdout}\n{run.stderr}')
    runtime = compiler_rt(execute)
    common = ['cc', '-c', '-target', 'thumbeb-freestanding-eabi', '-mcpu=arm7tdmi', '-mthumb', '-Oz',
              '-ffreestanding', '-fno-builtin', '-fno-stack-protector', '-fno-unwind-tables',
              '-fno-asynchronous-unwind-tables', '-ffunction-sections', '-fdata-sections',
              '-Ipatches', *[f'-I{i}' for i in includes], *[f'-D{d}' for d in defines]]
    cache = ROOT/'analysis/toolchains/objects'
    cache.mkdir(parents=True, exist_ok=True)

    def library_object(item):
        flags = [*common, '-w', *library_flags]
        if '-marm' in library_flags:  # ARM state: the triple decides, not -marm alone
            flags = ['armeb-freestanding-eabi' if f == 'thumbeb-freestanding-eabi' else f
                     for f in flags if f not in ('-mthumb', '-marm')]
        key = hashlib.sha256(Path(item).read_bytes()+'|'.join(flags).encode()).hexdigest()
        target = cache/f'{key}.o'
        if not target.exists():
            temporary = target.with_suffix(f'.{os.getpid()}.tmp')
            execute([str(EXECUTABLE), *flags, str(item), '-o', str(temporary)])
            temporary.replace(target)
        return str(target)
    with tempfile.TemporaryDirectory() as folder:
        objects = []
        # zig cc filters unknown linker flags, so compile here and link with ld.lld.
        for index, item in enumerate((source, *sources)):
            objects.append(str(Path(folder)/f'{index}.o'))
            execute([str(EXECUTABLE), *common, '-Wall', '-Wextra', '-Werror', str(item), '-o', objects[-1]])
        arm = ['armeb-freestanding-eabi' if f == 'thumbeb-freestanding-eabi' else f for f in common if f != '-mthumb']
        for index, item in enumerate(arm_sources):
            objects.append(str(Path(folder)/f'arm{index}.o'))
            execute([str(EXECUTABLE), *arm, '-Wall', '-Wextra', '-Werror', str(item), '-o', objects[-1]])
        # ARMv4T-safe returns for compiler-rt's assembly division helpers (patches/aeabi_v4t.S).
        objects.append(str(Path(folder)/'aeabi_v4t.o'))
        execute([str(EXECUTABLE), *arm, str(ROOT/'patches/aeabi_v4t.S'), '-o', objects[-1]])
        with ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as pool:
            objects += list(pool.map(library_object, libraries))
        script = (ROOT/LINKER).read_text(encoding='utf-8')
        images = []
        for base in (0, DELTA):
            linker, elf = Path(folder)/f'{base:x}.ld', Path(folder)/f'{base:x}.elf'
            linker.write_text(script.replace('. = 0;', f'. = {base:#x};', 1), encoding='utf-8')
            execute([str(EXECUTABLE), 'ld.lld', '-T', str(linker), '--gc-sections', '--build-id=none',
                     '-e', 'go1_main', *objects, str(runtime), '-o', str(elf)])
            images.append(elf.read_bytes())
            if base == 0 and os.environ.get('GO1_KEEP_ELF'):
                Path(os.environ['GO1_KEEP_ELF']).write_bytes(images[-1])  # symbols for debugging
        return native_file(*images)


def compiler_rt(execute):
    """Zig's compiler-rt (division, 64-bit and soft-float helpers) as ARM code; its Thumb inline asm does not assemble.

    ld.lld adds Thumb-to-ARM interworking thunks for ARMv4T calls.
    """
    library = ROOT/'analysis/toolchains/rt/compiler_rt_arm.a'
    if not library.exists():
        library.parent.mkdir(parents=True, exist_ok=True)
        object_file = library.with_suffix('.o')
        execute([str(EXECUTABLE), 'build-obj', '-target', 'armeb-freestanding-eabi', '-mcpu', 'arm7tdmi',
                 '-O', 'ReleaseSmall', '-ffunction-sections', '-fdata-sections',
                 str(EXECUTABLE.parent/'lib/compiler_rt.zig'), f'-femit-bin={object_file}'])
        execute([str(EXECUTABLE), 'ar', 'rcs', str(library), str(object_file)])
    return library


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--icon', type=Path, help='16x16 PNG; default: the source name with .png, if it exists')
    parser.add_argument('--title', help='Name in the application list; default: a go1-title: line in the source')
    args = parser.parse_args()
    try:
        program = compile_native(args.source, icon=args.icon, title=args.title)
        out = args.out or ROOT/'analysis/native-apps'/(args.source.stem.upper()[:8]+'.GO1')
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(program)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    count = struct.unpack_from('>I', program, 20)[0]
    print(f'{out}: {len(program)} bytes, image {struct.unpack_from(">I", program, 8)[0]}, '
          f'bss {struct.unpack_from(">I", program, 12)[0]}, relocations {count}')


if __name__ == '__main__':
    main()
