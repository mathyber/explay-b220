"""Report what a MIDlet JAR needs from the GO1 JVM class library.

Run as python -m tools.jar_report game.jar. Lists the manifest, class file
versions, resources, platform classes the JAR references and, for each, the
fields and methods it uses that analysis/jvm/lib does not provide.
"""
import argparse
import collections
from pathlib import Path
import struct
import zipfile

from .build_jvm import LIBRARY


def constants(data):
    count = struct.unpack_from('>H', data, 8)[0]
    pool, at, i = [None]*count, 10, 1
    while i < count:
        tag = data[at]
        if tag == 1:
            length = struct.unpack_from('>H', data, at+1)[0]
            pool[i] = ('utf8', data[at+3:at+3+length].decode('utf-8', 'replace'))
            at += 3+length
        elif tag in (3, 4):
            at += 5
        elif tag in (5, 6):
            at += 9
            i += 1
        elif tag in (7, 8, 16):
            pool[i] = ('class' if tag == 7 else 'other', struct.unpack_from('>H', data, at+1)[0])
            at += 3
        elif tag in (9, 10, 11, 12, 18):
            pool[i] = ({9: 'field', 10: 'method', 11: 'imethod', 12: 'nat', 18: 'indy'}[tag],
                       struct.unpack_from('>HH', data, at+1))
            at += 5
        elif tag == 15:
            at += 4
        else:
            raise ValueError(f'constant tag {tag}')
        i += 1
    return pool


def members(data):
    """Declared fields and methods (name+descriptor) of a class file."""
    pool = constants(data)
    at = 10
    count = len(pool)
    # Skip the constant pool again to reach the class body.
    i = 1
    while i < count:
        tag = data[at]
        if tag == 1: at += 3+struct.unpack_from('>H', data, at+1)[0]
        elif tag in (3, 4, 9, 10, 11, 12, 18): at += 5
        elif tag in (5, 6): at += 9; i += 1
        elif tag in (7, 8, 16): at += 3
        elif tag == 15: at += 4
        i += 1
    super_index = struct.unpack_from('>H', data, at+4)[0]
    interfaces = struct.unpack_from('>H', data, at+6)[0]
    at += 8+2*interfaces
    found = set()
    for _ in range(2):
        n = struct.unpack_from('>H', data, at)[0]
        at += 2
        for _ in range(n):
            name, desc, attributes = struct.unpack_from('>HHH', data, at+2)
            found.add(pool[name][1]+pool[desc][1])
            at += 8
            for _ in range(attributes):
                at += 6+struct.unpack_from('>I', data, at+2)[0]
    parent = pool[pool[super_index][1]][1] if super_index else None
    return found, parent


def library_members():
    table = {}
    for path in LIBRARY.rglob('*.class'):
        name = path.relative_to(LIBRARY).as_posix()[:-6]
        table[name] = members(path.read_bytes())
    return table


def provided(table, cls, member):
    while cls in table:
        found, parent = table[cls]
        if member in found:
            return True
        cls = parent
    return cls == 'java/lang/Object' and member in table.get('java/lang/Object', (set(), None))[0]


def report(path):
    jar = zipfile.ZipFile(path)
    lines = [jar.read('META-INF/MANIFEST.MF').decode('utf-8', 'replace').strip(), '']
    classes = [n for n in jar.namelist() if n.endswith('.class')]
    own = {c[:-6] for c in classes}
    versions = collections.Counter()
    used = collections.defaultdict(set)
    for c in classes:
        data = jar.read(c)
        versions[struct.unpack_from('>H', data, 6)[0]] += 1
        pool = constants(data)
        for entry in pool:
            if entry and entry[0] == 'class':
                name = pool[entry[1]][1]
                if not name.startswith('[') and name not in own:
                    used[name]
            if entry and entry[0] in ('field', 'method', 'imethod'):
                owner = pool[pool[entry[1][0]][1]][1]
                nat = pool[entry[1][1]][1]
                if not owner.startswith('[') and owner not in own:
                    used[owner].add(pool[nat[0]][1]+pool[nat[1]][1])
    resources = [(i.filename, i.file_size) for i in jar.infolist() if not i.filename.endswith('.class')]
    lines.append(f'{len(classes)} classes, versions {dict(versions)}, {len(resources)} resources')
    lines.append(', '.join(f'{n} ({s})' for n, s in resources))
    table = library_members()
    lines.append('')
    for cls in sorted(used):
        if cls not in table:
            lines.append(f'MISSING CLASS {cls}: {", ".join(sorted(used[cls]))}')
            continue
        gaps = sorted(m for m in used[cls] if not provided(table, cls, m))
        lines.append(f'{cls}' + (f' -- missing: {", ".join(gaps)}' if gaps else ''))
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('jar', type=Path)
    args = parser.parse_args()
    print(report(args.jar))


if __name__ == '__main__':
    main()
