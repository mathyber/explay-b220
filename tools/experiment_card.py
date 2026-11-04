"""Give an experimental firmware the SD card of the main CUT build (games, apps).

python -m tools.experiment_card <firmware.bin>: the card goes to
analysis/emulator/experiments/<firmware sha256>/sd-card.img (where the emulator
window looks for it) unless a non-empty card is already there. Exit code 1 when
the main card is in use (emulator open) or missing.
"""
import hashlib
import json
import shutil
import sys
from pathlib import Path

from emulator.firmware import ROOT

EXPERIMENTS = ROOT/'analysis/emulator/experiments'
MAIN_MANIFEST = ROOT/'analysis/firmware-patches/cut/manifest.json'


def main():
    firmware = Path(sys.argv[1])
    card = EXPERIMENTS/hashlib.sha256(firmware.read_bytes()).hexdigest()/'sd-card.img'
    if card.exists() and card.stat().st_size:
        return 0
    main_sha = json.loads(MAIN_MANIFEST.read_text(encoding='utf-8'))['output_sha256']
    source = EXPERIMENTS/main_sha/'sd-card.img'
    if not source.exists():
        print(f'The main CUT build has no SD card yet: run "Start B220 CUT.cmd" once ({source})')
        return 1
    card.parent.mkdir(parents=True, exist_ok=True)
    temporary = card.with_suffix('.tmp')
    try:
        shutil.copyfile(source, temporary)
    except OSError:
        temporary.unlink(missing_ok=True)
        print('Close the emulator with the CUT firmware first: its SD card is copied for this experiment.')
        return 1
    temporary.replace(card)
    print(f'SD card copied from the main CUT build: {card}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
