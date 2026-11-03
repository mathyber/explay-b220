"""Compile a freestanding C program into a B22N native SD application.

Run as python -m tools.build_native_app apps/card_native.c [--out DEMO.B22].
The program is linked twice, at 0 and at DELTA (patches/native_app.ld); words
that differ by DELTA hold program addresses and become the B22N relocation
table. Firmware addresses are plain constants and are not relocated.
See patches/b22n.h for the file format and the loader interface.
"""
import argparse
import os
from pathlib import Path
import struct
import subprocess
import tempfile

from emulator.firmware import ROOT
from .setup_arm_toolchain import EXECUTABLE

MAGIC, VERSION, HEADER = b'B22N', 1, 32
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
    """B22N bytes from the same program linked at 0 and at DELTA."""
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


def compile_native(source, sources=(), includes=()):
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
    with tempfile.TemporaryDirectory() as folder:
        objects = []
        # zig cc filters unknown linker flags, so compile here and link with ld.lld.
        for index, item in enumerate((source, *sources)):
            objects.append(str(Path(folder)/f'{index}.o'))
            execute([str(EXECUTABLE), 'cc', '-c', '-target', 'thumbeb-freestanding-eabi', '-mcpu=arm7tdmi', '-mthumb',
                     '-Oz', '-Wall', '-Wextra', '-Werror', '-ffreestanding', '-fno-builtin', '-fno-stack-protector',
                     '-fno-unwind-tables', '-fno-asynchronous-unwind-tables', '-ffunction-sections', '-fdata-sections',
                     '-Ipatches', *[f'-I{i}' for i in includes], str(item), '-o', objects[-1]])
        script = (ROOT/LINKER).read_text(encoding='utf-8')
        images = []
        for base in (0, DELTA):
            linker, elf = Path(folder)/f'{base:x}.ld', Path(folder)/f'{base:x}.elf'
            linker.write_text(script.replace('. = 0;', f'. = {base:#x};', 1), encoding='utf-8')
            execute([str(EXECUTABLE), 'ld.lld', '-T', str(linker), '--gc-sections', '--build-id=none',
                     '-e', 'b22n_main', *objects, str(runtime), '-o', str(elf)])
            images.append(elf.read_bytes())
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
    args = parser.parse_args()
    try:
        program = compile_native(args.source)
        out = args.out or ROOT/'analysis/native-apps'/(args.source.stem.upper()[:8]+'.B22')
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(program)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    count = struct.unpack_from('>I', program, 20)[0]
    print(f'{out}: {len(program)} bytes, image {struct.unpack_from(">I", program, 8)[0]}, '
          f'bss {struct.unpack_from(">I", program, 12)[0]}, relocations {count}')


if __name__ == '__main__':
    main()
