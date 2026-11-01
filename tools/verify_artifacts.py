"""Verify original ZIP, extraction byte ranges, carved assets and probe invariants."""
from pathlib import Path
import hashlib
import json
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    result = {}
    zip_path = next(ROOT.glob('*.zip'))
    with zipfile.ZipFile(zip_path) as archive:
        assert archive.testzip() is None, 'ZIP CRC failure'
        checked = 0
        for entry in archive.infolist():
            if entry.is_dir():
                continue
            disk = ROOT/entry.filename
            assert disk.read_bytes() == archive.read(entry), f'Original file changed: {disk}'
            checked += 1
    result['original_archive'] = dict(sha256=hashlib.sha256(zip_path.read_bytes()).hexdigest(),unchanged_files=checked,zip_crc='pass')
    manifest = json.loads((ROOT/'analysis/pac_manifest.json').read_text(encoding='utf-8'))
    pac = (ROOT/manifest['source']).read_bytes()
    assert hashlib.sha256(pac).hexdigest() == manifest['sha256']
    files = 0
    for entry in manifest['files']:
        if not entry['size']:
            continue
        extracted = (ROOT/'analysis/extracted'/entry['name']).read_bytes()
        assert extracted == pac[entry['offset']:entry['offset']+entry['size']]
        assert hashlib.sha256(extracted).hexdigest() == entry['sha256']
        files += 1
    result['pac_extracted_files'] = files
    ps = next((ROOT/'analysis/extracted').glob('SC*.bin')).read_bytes()
    resources = json.loads((ROOT/'analysis/resources/manifest.json').read_text(encoding='utf-8'))
    for entry in resources['entries']:
        extracted = (ROOT/'analysis/resources'/entry['file']).read_bytes()
        assert extracted == ps[entry['offset']:entry['offset']+entry['size']]
        assert hashlib.sha256(extracted).hexdigest() == entry['sha256']
    result['resources_identical_to_ps'] = len(resources['entries'])
    ps_probe = json.loads((ROOT/'analysis/boot_probe_ps_strict.json').read_text())
    fdl_probe = json.loads((ROOT/'analysis/boot_probe_fdl_strict.json').read_text())
    assert ps_probe['executed'] == 6 and ps_probe['fault']['address'] == '0x8000000c'
    assert fdl_probe['executed'] == 22 and fdl_probe['fault']['address'] == '0x8b000018'
    result['strict_probe_first_mmio'] = 'pass'
    startup = json.loads((ROOT/'analysis/startup_map.json').read_text())
    assert startup['reset'] == '0x6a4228'
    assert len(startup['copy_regions']) == 2 and len(startup['zero_regions']) == 2
    result['startup_table'] = 'pass'
    result['status'] = 'PASS'
    (ROOT/'analysis/verification.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))


if __name__ == '__main__':
    main()
