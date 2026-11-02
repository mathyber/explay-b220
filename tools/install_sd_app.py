"""Install independent B22V programs and rebuild their on-card application catalog."""
import argparse
import hashlib
import re
from pathlib import Path

from emulator.board import ROOT
from emulator.card_image import CardImage
from .b22asm import assemble
from .build_sd_app import DIRECTORY
from .sd_image import Fat16,create_image


def default_card():
    digest=hashlib.sha256((DIRECTORY/'sd-app.bin').read_bytes()).hexdigest()
    return ROOT/'analysis/emulator/experiments'/digest/'sd-card.img'


def application_name(source,name=None):
    if name is None:
        stem=source.stem
        if stem.startswith('card_'):stem=stem[5:]
        name=stem.upper()+'.B22'
    name=name.upper()
    if not re.fullmatch(r'[A-Z0-9_]{1,8}\.B22',name):
        raise ValueError('Use --name NAME.B22 with an ASCII 8.3 name')
    return name


def update_catalog(volume):
    names=sorted(e['name'] for e in volume.entries() if not e['directory'] and e['name'].endswith('.B22'))
    if len(names)>16:raise ValueError('The current selector supports at most 16 applications')
    if any(not re.fullmatch(r'[A-Z0-9_]{1,8}\.B22',n) for n in names):
        raise ValueError('Application file names must use ASCII 8.3 names')
    volume.replace_file('APPS.LST',('\r\n'.join(names)+'\r\n').encode('ascii'))


def install(source,card,ensure=False,name=None):
    name=application_name(source,name)
    program=assemble(source.read_text(encoding='utf-8-sig'))
    card.parent.mkdir(parents=True,exist_ok=True)
    if not card.exists():
        volume=Fat16(create_image());volume.add_file(name,program);update_catalog(volume)
        with card.open('xb') as stream:stream.write(volume.image)
        return 'created'
    with CardImage(card) as image:
        volume=Fat16(image.data)
        kept=ensure and any(e['name']==name and not e['directory'] for e in volume.entries())
        if not kept:volume.replace_file(name,program)
        update_catalog(volume)
        image.replace_contents(volume.image)
    return 'kept' if kept else 'installed'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path,nargs='?',default=ROOT/'apps/card_demo.b22asm')
    parser.add_argument('--card',type=Path)
    parser.add_argument('--ensure',action='store_true',help='Keep an existing application file unchanged')
    parser.add_argument('--name',help='Override the installed ASCII 8.3 name')
    parser.add_argument('--examples',action='store_true',help='Install both example programs, keeping existing copies')
    args=parser.parse_args()
    try:
        card=args.card or default_card()
        if args.examples:
            for source in (ROOT/'apps/card_demo.b22asm',ROOT/'apps/card_second.b22asm'):
                install(source,card,ensure=True)
            result='examples ready'
        else:result=install(args.source,card,args.ensure,args.name)
    except (ValueError,OSError) as exc:parser.error(f'{exc}. Close the emulator before changing its SD image.')
    print(f'{result}: {card} [APPS.LST]')


if __name__=='__main__':main()
