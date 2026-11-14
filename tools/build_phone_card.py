"""Files for the memory card of a real B220 with the CUT firmware.

python -m tools.build_phone_card [--out analysis/phone-card] [--jar game.jar ...] [--debug]
Builds the programs and writes them into one folder; copy its contents to the
root of the card. JARs go into JAVA\\ (already there are kept): the Java list
shows every .jar in E:\\JAVA\\. --debug adds JAVALOG.GO1 (writes E:\\JAVA.LOG),
MEMTEST.GO1 and FILES.GO1. Nothing is written to the emulator's card image.
"""
import argparse
import shutil
import tempfile
from pathlib import Path

from emulator.firmware import ROOT
from .build_native_app import compile_native

DEFAULT_OUT = ROOT/'analysis/phone-card'


def build(out, jars=(), debug=False):
    from .build_browser import build as build_browser
    from .build_java_phone import build_vm, zip_directory
    from .build_jvm import LIBRARY, build_library
    out.mkdir(parents=True, exist_ok=True)
    files = {
        'JAVA.GO1': build_vm(),
        'BROWSER.GO1': build_browser()[0],
        'BENCH.GO1': compile_native(ROOT/'apps/card_bench.c'),
    }
    if debug:
        files['JAVALOG.GO1'] = build_vm(defines=['JVM_PHONE_LOG'], title='Java журнал')
        files['MEMTEST.GO1'] = compile_native(ROOT/'apps/card_memtest.c')
        files['FILES.GO1'] = compile_native(ROOT/'apps/card_files.c')
    build_library()
    with tempfile.TemporaryDirectory() as folder:
        library = Path(folder)/'JAVALIB.JAR'
        zip_directory(LIBRARY, library)
        files['JAVALIB.JAR'] = library.read_bytes()
    for name, data in files.items():
        (out/name).write_bytes(data)
    programs = [n for n in files if n.endswith('.GO1')]
    (out/'APPS.LST').write_bytes(''.join(f'{n}\r\n' for n in programs).encode('ascii'))
    (out/'JAVA').mkdir(exist_ok=True)
    for jar in jars:
        shutil.copyfile(jar, out/'JAVA'/jar.name)
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--out', type=Path, default=DEFAULT_OUT)
    parser.add_argument('--jar', type=Path, action='append', default=[], help='J2ME game or application for E:\\JAVA\\ (repeatable)')
    parser.add_argument('--debug', action='store_true', help='also JAVALOG.GO1 and MEMTEST.GO1')
    args = parser.parse_args()
    try:
        files = build(args.out, args.jar, args.debug)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(f'{args.out}:')
    for name, data in files.items():
        print(f'  {name:12s} {len(data):>8}')
    print('  APPS.LST, JAVA\\  (copy everything to the root of the card)')


if __name__ == '__main__':
    main()
