"""Fetch pinned portable Ghidra and Temurin JDK distributions into analysis/."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
import urllib.request
import zipfile

ROOT=Path(__file__).resolve().parents[1]
DIRECTORY=ROOT/'analysis/toolchains'
GHIDRA=DIRECTORY/'ghidra_12.2_PUBLIC'
JDK=DIRECTORY/'jdk-25.0.5+8'
PACKAGES=(
    ('ghidra_12.2_PUBLIC_20261016.zip',
     'https://github.com/NationalSecurityAgency/ghidra/releases/download/Ghidra_12.2_build/ghidra_12.2_PUBLIC_20261016.zip',
     '2c2cd776db3c1f6a097ba418922378ac3649cadd5e844a6f5f088ae628a7be67',GHIDRA/'support/analyzeHeadless.bat'),
    ('OpenJDK25U-jdk_x64_windows_hotspot_25.0.5_8.zip',
     'https://github.com/adoptium/temurin25-binaries/releases/download/jdk-25.0.5%2B8/OpenJDK25U-jdk_x64_windows_hotspot_25.0.5_8.zip',
     '636bdc13dc649ddc7d5921c5c7d23cca6e44fd19d662b45cc9c02135a6c96c42',JDK/'bin/javac.exe'))


def digest(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def prepare(package):
    name,url,checksum,executable=package
    archive=DIRECTORY/name
    if not archive.exists() or digest(archive)!=checksum:
        temporary=archive.with_suffix('.download')
        print(f'Downloading {name}...',flush=True)
        request=urllib.request.Request(url,headers={'User-Agent':'B220-firmware-research'})
        with urllib.request.urlopen(request,timeout=60) as response,temporary.open('wb') as stream:
            while chunk:=response.read(1024*1024):stream.write(chunk)
        if digest(temporary)!=checksum:raise ValueError(f'Checksum mismatch: {name}')
        temporary.replace(archive)
    if not executable.exists():
        print(f'Extracting {name}...',flush=True)
        with zipfile.ZipFile(archive) as package:
            for member in package.infolist():
                if not (DIRECTORY/member.filename).resolve().is_relative_to(DIRECTORY.resolve()):
                    raise ValueError('ZIP entry escapes the local tool directory')
            package.extractall(DIRECTORY)
    if not executable.exists():raise ValueError(f'Missing executable after extraction: {executable}')
    print(f'Ready: {executable}',flush=True)


def main():
    DIRECTORY.mkdir(parents=True,exist_ok=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        for result in pool.map(prepare,PACKAGES):pass


if __name__=='__main__':main()
