"""Build the GO1 JVM for the phone and put it on an SD card image.

Run as python -m tools.build_java_phone [--app apps/java/Hello.java] [--jar game.jar ...]
[--card IMG].
Produces analysis/jvm/phone/: JAVA.GO1 (the VM as a GO1N program), JAVALIB.JAR
(class library) and APP.JAR (compiled from --app). Ready JARs given with --jar
go to E:\\JAVA\\ under their own names; JAVA.GO1 lists that folder and runs
APP.JAR only when the folder has no JARs.
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


def build_app(sources, main, midlet=False):
    """Main-Class program, or a MIDlet (MIDlet-1 in the manifest)."""
    with tempfile.TemporaryDirectory() as folder:
        classes = Path(folder)/'classes'
        javac(sources, classes)
        path = Path(folder)/'APP.JAR'
        if midlet:
            manifest = (f'Manifest-Version: 1.0\r\nMIDlet-1: {main}, , {main}\r\nMIDlet-Name: {main}\r\n'
                        'MIDlet-Vendor: GO1\r\nMIDlet-Version: 1.0\r\nMicroEdition-Profile: MIDP-2.0\r\n'
                        'MicroEdition-Configuration: CLDC-1.1\r\n')
        else:
            manifest = f'Manifest-Version: 1.0\r\nMain-Class: {main}\r\n'
        zip_directory(classes, path, manifest)
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
    parser.add_argument('--midlet', action='store_true', help='Package the class as MIDlet-1')
    parser.add_argument('--jar', type=Path, action='append', default=[], help='Ready JAR for the card folder JAVA (repeatable)')
    parser.add_argument('--card', type=Path)
    args = parser.parse_args()
    try:
        build_library()
        DIRECTORY.mkdir(parents=True, exist_ok=True)
        vm = build_vm()
        library = DIRECTORY/'JAVALIB.JAR'
        zip_directory(LIBRARY, library)
        files = {'JAVA.GO1': vm, 'JAVALIB.JAR': library.read_bytes()}
        if args.jar:
            files.update({f'JAVA/{jar.name}': jar.read_bytes() for jar in args.jar})
        else:
            files['APP.JAR'] = build_app([args.app], args.main or args.app.stem, args.midlet)
            (DIRECTORY/'APP.JAR').write_bytes(files['APP.JAR'])
        (DIRECTORY/'JAVA.GO1').write_bytes(vm)
        card = args.card or default_card()
        install(card, files)
    except (OSError, ValueError) as exc:
        parser.error(f'{exc}. Close the emulator before changing its SD image.')
    print(', '.join(f'{name} {len(data)} bytes' for name, data in files.items()), '->', card)


if __name__ == '__main__':
    main()
