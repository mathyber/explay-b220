"""Select an immutable raw PS image for the known B220 board layout."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_firmware(profile, path=None):
    candidates = sorted((ROOT/'analysis/extracted').glob('SC*.bin'))
    if len(candidates) != 1:
        raise ValueError('Expected one original SC*.bin in analysis/extracted')
    reference = candidates[0]
    original = reference.read_bytes()
    if hashlib.sha256(original).hexdigest() != profile['ps_sha256']:
        raise ValueError('Profile is pinned to a different original PS image')
    source = Path(path).resolve() if path is not None else reference.resolve()
    if source.stat().st_size != len(original):
        raise ValueError(f'Expected raw PS of {len(original)} bytes, with the original layout; PAC and full NOR are unsupported')
    image = source.read_bytes()
    digest = hashlib.sha256(image).hexdigest()
    identity = dict(source=str(source),reference=str(reference.resolve()),sha256=digest,
                    reference_sha256=profile['ps_sha256'],size=len(image),
                    experimental=path is not None,modified=digest != profile['ps_sha256'])
    return image,identity


def experiment_directory(identity):
    return ROOT/'analysis/emulator/experiments'/identity['sha256']


def protect_outputs(profile, identity, outputs, inputs=()):
    """Reject input aliases (including hardlinks) and canonical extraction output."""
    protected = [Path(identity['source']),Path(identity['reference'])]
    protected.extend(Path(path) for path in inputs)
    protected.extend(ROOT.glob('*.zip'))
    if profile.get('nv_seed'):
        protected.append(ROOT/profile['nv_seed']['source'])
    for output in filter(None,outputs):
        path = Path(output).resolve()
        if (path.is_relative_to((ROOT/'analysis/extracted').resolve()) or
                path.is_relative_to((ROOT/'Explay_B220_M224_V02.02_B06_20120720_FSPD').resolve())):
            raise ValueError(f'Output would overwrite original extracted artifacts: {path}')
        for source in protected:
            if path == source.resolve() or (path.exists() and path.samefile(source)):
                raise ValueError(f'Output would overwrite a firmware input: {path}')


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image',type=Path)
    args = parser.parse_args()
    profile = json.loads((ROOT/'emulator/profiles/b220-l7a.json').read_text())
    try:
        image,identity = load_firmware(profile,args.image)
    except (OSError,ValueError) as exc:
        parser.error(str(exc))
    from .network_trace import image_network_code_matches
    identity['directory'] = str(experiment_directory(identity))
    identity['network'] = image_network_code_matches(image,Path(identity['reference']).read_bytes())
    print(json.dumps(identity,ensure_ascii=True))


if __name__ == '__main__':
    main()
