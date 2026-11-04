"""Build the GO1 Java VM: class library (javac) and the Windows test host (zig cc).

Run as python -m tools.build_jvm. Outputs go to analysis/jvm/:
lib/ (library classes) and go1jvm.exe (32-bit x86, like the phone's references).
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess

from emulator.firmware import ROOT
from .setup_arm_toolchain import EXECUTABLE as ZIG

DIRECTORY = ROOT/'analysis/jvm'
SOURCES = ROOT/'jvm'
JDK = ROOT/'analysis/toolchains/jdk-25.0.5+8/bin'
LIBRARY = DIRECTORY/'lib'
HOST = DIRECTORY/'go1jvm.exe'
VM_FILES = ('class.c', 'heap.c', 'interp.c', 'native.c', 'jar.c', 'midp.c')


def run(command, **options):
    result = subprocess.run([str(c) for c in command], capture_output=True, text=True, encoding='utf-8', **options)
    if result.returncode:
        raise ValueError(f'{" ".join(map(str, command))}\n{result.stdout}\n{result.stderr}')
    return result


def javac(sources, output, classpath=None):
    """Java 8 class files compiled against our own library, not the JDK's."""
    if not (JDK/'javac.exe').exists():
        raise ValueError(f'JDK not found at {JDK}')
    output.mkdir(parents=True, exist_ok=True)
    boot = classpath or LIBRARY
    run([JDK/'javac.exe', '-source', '8', '-target', '8', '-bootclasspath', boot, '-Xlint:-options',
         '-encoding', 'UTF-8', '-nowarn', '-d', output, *sources])


def build_library():
    if LIBRARY.exists():
        shutil.rmtree(LIBRARY)
    LIBRARY.mkdir(parents=True)
    sources = sorted((SOURCES/'lib').rglob('*.java'))
    javac(sources, LIBRARY, classpath=LIBRARY)
    from .build_font import build_fonts
    build_fonts(LIBRARY)
    return len(sources)


def build_host():
    environment = os.environ.copy()
    environment['ZIG_GLOBAL_CACHE_DIR'] = str(ROOT/'analysis/toolchains/cache')
    environment['ZIG_LOCAL_CACHE_DIR'] = str(ROOT/'analysis/toolchains/local-cache')
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    run([ZIG, 'cc', '-target', 'x86-windows-gnu', '-O2', '-std=c11', '-Wall', '-Wextra', '-Werror',
         '-fno-strict-aliasing', *[SOURCES/f for f in VM_FILES], SOURCES/'host_main.c', '-o', HOST],
        env=environment, cwd=ROOT)
    return HOST


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    try:
        count = build_library()
        build_host()
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(f'library: {count} sources -> {LIBRARY}\nhost: {HOST}')


if __name__ == '__main__':
    main()
