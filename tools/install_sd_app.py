"""Assemble and install APP.B22 on the loader's SD image without reflashing PS."""
import argparse
import hashlib
from pathlib import Path

from emulator.board import ROOT
from emulator.card_image import CardImage
from .b22asm import assemble
from .build_sd_app import DIRECTORY
from .sd_image import Fat16,create_image


def default_card():
    digest=hashlib.sha256((DIRECTORY/'sd-app.bin').read_bytes()).hexdigest()
    return ROOT/'analysis/emulator/experiments'/digest/'sd-card.img'


def install(source,card,ensure=False):
    program=assemble(source.read_text(encoding='utf-8-sig'))
    card.parent.mkdir(parents=True,exist_ok=True)
    if not card.exists():
        volume=Fat16(create_image());volume.add_file('APP.B22',program)
        with card.open('xb') as stream:stream.write(volume.image)
        return 'created'
    with CardImage(card) as image:
        volume=Fat16(image.data)
        if ensure and any(e['name']=='APP.B22' and not e['directory'] for e in volume.entries()):return 'kept'
        volume.replace_file('APP.B22',program)
        image.replace_contents(volume.image)
    return 'installed'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path,nargs='?',default=ROOT/'apps/card_demo.b22asm')
    parser.add_argument('--card',type=Path)
    parser.add_argument('--ensure',action='store_true',help='Keep an existing APP.B22 unchanged')
    args=parser.parse_args()
    try:
        card=args.card or default_card()
        result=install(args.source,card,args.ensure)
    except (ValueError,OSError) as exc:parser.error(f'{exc}. Close the emulator before changing its SD image.')
    print(f'{result}: {card} [APP.B22]')


if __name__=='__main__':main()
