"""Fetch a pinned official Zig distribution for freestanding ARM C builds."""
import hashlib
from pathlib import Path
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = '0.13.0'
URL = 'https://ziglang.org/download/0.13.0/zig-windows-x86_64-0.13.0.zip'
SHA256 = 'd859994725ef9402381e557c60bb57497215682e355204d754ee3df75ee3c158'
DIRECTORY = ROOT/'analysis/toolchains'
EXECUTABLE = DIRECTORY/f'zig-windows-x86_64-{VERSION}'/'zig.exe'


def main():
    DIRECTORY.mkdir(parents=True,exist_ok=True)
    archive = DIRECTORY/f'zig-{VERSION}.zip'
    if not archive.exists() or hashlib.sha256(archive.read_bytes()).hexdigest()!=SHA256:
        temporary = archive.with_suffix('.download')
        print('Downloading pinned official compiler...',flush=True)
        with urllib.request.urlopen(URL,timeout=60) as response,temporary.open('wb') as stream:
            while data:=response.read(1024*1024):
                stream.write(data)
        if hashlib.sha256(temporary.read_bytes()).hexdigest()!=SHA256:
            raise ValueError('Official Zig archive checksum mismatch')
        temporary.replace(archive)
    with zipfile.ZipFile(archive) as package:
        for entry in package.infolist():
            target = (DIRECTORY/entry.filename).resolve()
            if not target.is_relative_to(DIRECTORY.resolve()):
                raise ValueError('Compiler ZIP entry escapes its directory')
        if not EXECUTABLE.exists():
            package.extractall(DIRECTORY)
    print(EXECUTABLE)


if __name__=='__main__':
    main()
