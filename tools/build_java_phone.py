"""Build the B22 JVM for the phone and put it on an SD card image.

Run as python -m tools.build_java_phone [--app apps/java/Hello.java] [--card IMG].
Produces analysis/jvm/phone/: JAVA.B22 (the VM as a B22N program), JAVALIB.JAR
(class library) and APP.JAR (the application, Main-Class in its manifest).
Without --card the files go to the card of the current content-removal PS.
"""
import argparse
import hashlib
from pathlib import Path
import shutil
import tempfile
import zipfile

from emulator.board import ROOT
from emulator.card_image import CardImage
from .build_jvm import LIBRARY, VM_FILES, SOURCES, build_library, javac
from .build_native_app import compile_native
from .install_sd_app import update_catalog
from .sd_image import Fat16, create_image

DIRECTORY = ROOT/'analysis/jvm/phone'
FIRMWARE = ROOT/'analysis/firmware-patches/cut/b220-cut.bin'


def build_vm():
    return compile_native(SOURCES/'phone_main.c', [SOURCES/f for f in VM_FILES], includes=[SOURCES])


def zip_directory(directory, path, manifest=None):
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        if manifest:
            archive.writestr('META-INF/MANIFEST.MF', manifest)
        for item in sorted(directory.rglob('*')):
            if item.is_file():
                archive.write(item, item.relative_to(directory).as_posix())


def build_app(sources, main):
    with tempfile.TemporaryDirectory() as folder:
        classes = Path(folder)/'classes'
        javac(sources, classes)
        path = Path(folder)/'APP.JAR'
        zip_directory(classes, path, f'Manifest-Version: 1.0\r\nMain-Class: {main}\r\n')
        return path.read_bytes()


def default_card():
    digest = hashlib.sha256(FIRMWARE.read_bytes()).hexdigest()
    return ROOT/'analysis/emulator/experiments'/digest/'sd-card.img'


def install(card, files):
    card.parent.mkdir(parents=True, exist_ok=True)
    if not card.exists():
        volume = Fat16(create_image())
        for name, data in files.items():
            volume.add_file(name, data)
        update_catalog(volume)
        card.write_bytes(volume.image)
        return
    with CardImage(card) as image:
        volume = Fat16(image.data)
        for name, data in files.items():
            volume.replace_file(name, data)
        update_catalog(volume)
        image.replace_contents(volume.image)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', type=Path, default=ROOT/'apps/java/Hello.java')
    parser.add_argument('--main', help='Main class (default: the source file name)')
    parser.add_argument('--card', type=Path)
    args = parser.parse_args()
    try:
        build_library()
        DIRECTORY.mkdir(parents=True, exist_ok=True)
        vm = build_vm()
        library = DIRECTORY/'JAVALIB.JAR'
        zip_directory(LIBRARY, library)
        app = build_app([args.app], args.main or args.app.stem)
        (DIRECTORY/'JAVA.B22').write_bytes(vm)
        (DIRECTORY/'APP.JAR').write_bytes(app)
        card = args.card or default_card()
        install(card, {'JAVA.B22': vm, 'JAVALIB.JAR': library.read_bytes(), 'APP.JAR': app})
    except (OSError, ValueError) as exc:
        parser.error(f'{exc}. Close the emulator before changing its SD image.')
    print(f'JAVA.B22 {len(vm)} bytes, JAVALIB.JAR {library.stat().st_size} bytes, APP.JAR {len(app)} bytes -> {card}')


if __name__ == '__main__':
    main()
