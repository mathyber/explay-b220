"""Give an experimental firmware the SD card and settings of the main CUT build.

python -m tools.experiment_card <firmware.bin>: into
analysis/emulator/experiments/<firmware sha256>/ (where the emulator window looks)
- sd-card.img: the main build's card (games, apps), unless a non-empty card is there;
- window-nor-v2.bin: the settings memory (date, time, wallpaper...) of the main build,
  or the newest one of another experiment, unless the experiment has its own. A fresh
  settings memory starts at the date screen, which redraws too little for the window
  to unlock its keys (it waits for five full LCD frames).
Exit code 1 when a source is in use (emulator open) or the main card is missing.
"""
import hashlib
import json
import shutil
import sys
from pathlib import Path

from emulator.firmware import ROOT

EXPERIMENTS = ROOT/'analysis/emulator/experiments'
MAIN_MANIFEST = ROOT/'analysis/firmware-patches/cut/manifest.json'
SETTINGS = 'window-nor-v2.bin'


def copy(source, target):
    temporary = target.with_suffix('.tmp')
    try:
        shutil.copyfile(source, temporary)
    except OSError:
        temporary.unlink(missing_ok=True)
        print('Close the emulator first: its files are copied for this experiment.')
        return False
    temporary.replace(target)
    return True


def main():
    firmware = Path(sys.argv[1])
    folder = EXPERIMENTS/hashlib.sha256(firmware.read_bytes()).hexdigest()
    folder.mkdir(parents=True, exist_ok=True)
    main_folder = EXPERIMENTS/json.loads(MAIN_MANIFEST.read_text(encoding='utf-8'))['output_sha256']
    card = folder/'sd-card.img'
    if not (card.exists() and card.stat().st_size):
        source = main_folder/'sd-card.img'
        if not source.exists():
            print(f'The main CUT build has no SD card yet: run "Start B220 CUT.cmd" once ({source})')
            return 1
        if not copy(source, card):
            return 1
        print(f'SD card copied from the main CUT build: {card}')
    settings = folder/SETTINGS
    if not (settings.exists() and settings.stat().st_size):
        candidates = [main_folder/SETTINGS] if (main_folder/SETTINGS).exists() else sorted(
            (p for p in EXPERIMENTS.glob(f'*/{SETTINGS}') if p.parent != folder and p.stat().st_size),
            key=lambda p: p.stat().st_mtime, reverse=True)
        if candidates:
            # The saved NOR is the whole flash: the PS image first, then the settings.
            # Keep the settings, put this firmware in front.
            image = firmware.read_bytes()
            try:
                nor = bytearray(candidates[0].read_bytes())
            except OSError:
                print('Close the emulator first: its files are copied for this experiment.')
                return 1
            if len(nor) <= len(image):
                print(f'Unexpected settings memory size in {candidates[0]}')
                return 1
            nor[:len(image)] = image
            temporary = settings.with_suffix('.tmp')
            temporary.write_bytes(nor)
            temporary.replace(settings)
            print(f'Settings memory copied from {candidates[0].parent.name[:12]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
