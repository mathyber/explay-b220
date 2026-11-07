"""GO1 Java VM on the Windows host, checked against the JDK's own JVM.

Each jvm/tests/*.java program is compiled twice: against our class library
(run on go1jvm.exe) and against the JDK (run on java.exe); outputs must match.
"""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from emulator.firmware import ROOT
from tools.build_jvm import HOST, JDK, LIBRARY, build_host, build_library, javac
from tools.setup_arm_toolchain import EXECUTABLE as ZIG

# Resources needs its JAR and runs in its own test.
TESTS = sorted(p for p in (ROOT/'jvm/tests').glob('*.java') if p.stem != 'Resources')
HEAP_KB = {'GcStress': 160}


@unittest.skipUnless((JDK/'javac.exe').exists() and ZIG.exists(), 'Local JDK and Zig toolchains required')
class JvmTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        build_library()
        build_host()
        cls.folder = tempfile.TemporaryDirectory()
        root = Path(cls.folder.name)
        cls.ours, cls.reference = root/'ours', root/'reference'
        javac(TESTS, cls.ours)
        cls.reference.mkdir()
        subprocess.run([str(JDK/'javac.exe'), '--release', '8', '-nowarn', '-Xlint:-options', '-encoding', 'UTF-8', '-d', str(cls.reference),
                        *map(str, TESTS)], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def run_ours(self, name, heap_kb=None):
        command = [str(HOST), '-cp', f'{LIBRARY};{self.ours}', name]
        if heap_kb:
            command[1:1] = ['-heap', str(heap_kb)]
        environment = dict(os.environ, GO1JVM_STATS='1')
        return subprocess.run(command, capture_output=True, text=True, encoding='utf-8', timeout=120, env=environment)

    def test_programs_match_the_jdk(self):
        for source in TESTS:
            name = source.stem
            with self.subTest(program=name):
                expected = subprocess.run([str(JDK/'java.exe'), '-cp', str(self.reference), name],
                                          capture_output=True, text=True, encoding='utf-8', timeout=120)
                self.assertEqual(expected.returncode, 0, expected.stderr)
                ours = self.run_ours(name, HEAP_KB.get(name))
                self.assertEqual(ours.returncode, 0, ours.stderr)
                self.assertEqual(ours.stdout, expected.stdout)

    def test_record_stores_are_saved_on_the_card(self):
        out, card = Path(self.folder.name)/'iotests', Path(self.folder.name)/'card'
        javac([ROOT/'jvm/iotests/StoreTest.java'], out)
        card.mkdir()
        command = [str(HOST), '-card', str(card), '-cp', f'{LIBRARY};{out}', 'StoreTest']
        runs = [subprocess.run(command, capture_output=True, text=True, encoding='utf-8', timeout=120) for _ in range(2)]
        for run in runs:
            self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(runs[0].stdout.splitlines()[0], 'saved 2 next 3')
        self.assertEqual(runs[1].stdout.splitlines()[:2], ['loaded one TWO! next 3', 'stores 1 scores'])
        for run in runs:
            self.assertIn('no network: no network on this host', run.stdout)
            self.assertIn('https: Not supported: https', run.stdout)
        # The deleted "temporary" store leaves no file: the index and one store remain.
        files = sorted(p.relative_to(card).as_posix() for p in card.rglob('*') if p.is_file())
        self.assertEqual(len(files), 2, files)
        self.assertEqual(files[0], 'JAVA/RMS/APP/STORES.IDX')
        self.assertTrue(files[1].startswith('JAVA/RMS/APP/scores_') and files[1].endswith('.RMS'), files)

    def test_current_time_is_the_local_wall_clock(self):
        import time
        out = Path(self.folder.name)/'clocktest'
        javac([ROOT/'jvm/iotests/ClockTest.java'], out)
        run = subprocess.run([str(HOST), '-cp', f'{LIBRARY};{out}', 'ClockTest'],
                             capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        seconds, calendar = run.stdout.splitlines()[:2]
        # Local time shown as GMT, as on the phone (the RTC holds local time).
        local = time.time()+time.localtime().tm_gmtoff
        self.assertLess(abs(int(seconds)-local), 30)
        shown = time.gmtime(int(seconds))
        self.assertEqual(calendar, f'{shown.tm_year} {shown.tm_hour}:{shown.tm_min}')

    def test_draw_region_transforms_clipping_and_alpha(self):
        import hashlib
        out = Path(self.folder.name)/'regiontest'
        javac([ROOT/'jvm/iotests/RegionTest.java'], out)
        run = subprocess.run([str(HOST), '-cp', f'{LIBRARY};{out}', 'RegionTest'],
                             capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(len(run.stdout.splitlines()), 300)
        # Checksums of the per-pixel implementation before the clipped, stepped blit.
        self.assertEqual(hashlib.sha256(run.stdout.encode()).hexdigest(),
                         '7fb9384590991f4942efc4a716afd5e78dd01b74f8338624c20439d4e90759c1')

    def test_wide_blits_match_the_pixel_by_pixel_copy(self):
        """drawRegion of 100x40 images at every source/destination alignment: opaque,
        bit alpha, byte alpha (eight pixels per mask byte, joined half-word copies)."""
        import hashlib
        out = Path(self.folder.name)/'blittest'
        javac([ROOT/'jvm/iotests/BlitTest.java'], out)
        run = subprocess.run([str(HOST), '-cp', f'{LIBRARY};{out}', 'BlitTest'],
                             capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(len(run.stdout.splitlines()), 241)
        # Checksums of the pixel-at-a-time loops before the eight-pixel mask bytes.
        self.assertEqual(hashlib.sha256(run.stdout.encode()).hexdigest(),
                         '0e467ead5b83fc1cd0183bddb7aa1ffc1b7fd23ea56c8edc96eea59b18ae33f9')

    def test_inflate_matches_python_for_every_block_kind(self):
        """Stored (level 0), fixed and dynamic Huffman blocks, sizes 0..200 KB, read in odd chunks."""
        import random, zipfile
        out = Path(self.folder.name)/'inflatetest'
        javac([ROOT/'jvm/iotests/InflateTest.java'], out)
        rnd = random.Random(5)
        words = [b'class', b'java/lang/', b'\x00\x01', b'Image', b'\xff\xfe\xfd', b'  ', b'png', b'opera']
        entries = []
        for i in range(120):
            kind, size = i % 4, rnd.choice([0, 1, 7, 100, 1000, 5000, 40000, 200000])
            if kind == 0: data = bytes(rnd.randrange(256) for _ in range(size))
            elif kind == 1: data = b''.join(rnd.choice(words) for _ in range(size//4+1))[:size]
            elif kind == 2: data = bytes([rnd.randrange(4)])*size
            else: data = bytes((j*7+(j >> 5)) & 255 for j in range(size))
            entries.append(data)
        jar = Path(self.folder.name)/'inflate.jar'
        with zipfile.ZipFile(jar, 'w') as z:
            z.writestr('META-INF/MANIFEST.MF', 'Manifest-Version: 1.0\nMain-Class: InflateTest\n')
            z.write(out/'InflateTest.class', 'InflateTest.class')
            for i, data in enumerate(entries):
                z.writestr(zipfile.ZipInfo(f'e{i}'), data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=i % 10)
        run = subprocess.run([str(HOST), '-heap', '2000', '-cp', str(LIBRARY), '-jar', str(jar)],
                             capture_output=True, text=True, timeout=120)
        self.assertEqual(run.returncode, 0, run.stderr)
        lines = run.stdout.splitlines()
        self.assertEqual(len(lines), len(entries))
        for i, data in enumerate(entries):
            checksum = 0
            for b in data: checksum = (checksum*31+b) & 0xffffffff
            if checksum >= 1 << 31: checksum -= 1 << 32
            self.assertEqual(lines[i], f'{i} {len(data)} {checksum}')

    def test_binary_alpha_draws_like_byte_alpha(self):
        import hashlib
        out = Path(self.folder.name)/'alphatest'
        javac([ROOT/'jvm/iotests/AlphaTest.java'], out)
        run = subprocess.run([str(HOST), '-cp', f'{LIBRARY};{out}', 'AlphaTest'],
                             capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(len(run.stdout.splitlines()), 208)
        # Checksums of the byte-alpha implementation (before images with only 0/255 got a bit per pixel).
        self.assertEqual(hashlib.sha256(run.stdout.encode()).hexdigest(),
                         '44918ea2f908fa1a2f4744e1263a1fb00cc362c13e4fe4ab02308f44e4a5aef3')

    def test_png_decoding_of_every_type(self):
        """Palette 1-8 bit, grey 1-16 bit, grey+alpha, RGB, RGBA (8 and 16 bit), tRNS, filters, split IDAT."""
        import hashlib, random, struct, zipfile, zlib
        out = Path(self.folder.name)/'pngtest'
        javac([ROOT/'jvm/iotests/PngTest.java'], out)
        rnd = random.Random(3)
        def chunk(t, data): return struct.pack('>I', len(data))+t+data+struct.pack('>I', zlib.crc32(t+data) & 0xffffffff)
        def raw_png(w, h, depth, color, rows, plte=None, trns=None, level=6):
            png = bytes([0x89])+b'PNG'+bytes([13, 10, 26, 10])+chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, depth, color, 0, 0, 0))
            if plte: png += chunk(b'PLTE', plte)
            if trns is not None: png += chunk(b'tRNS', trns)
            z = zlib.compress(b''.join(bytes([rnd.randrange(5)])+r for r in rows), level)
            step = max(1, len(z)//3+1)
            for k in range(0, len(z), step): png += chunk(b'IDAT', z[k:k+step])
            return png+chunk(b'IEND', b'')
        chans = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
        pngs = []
        for color, depths in ((0, (1, 2, 4, 8, 16)), (2, (8, 16)), (3, (1, 2, 4, 8)), (4, (8, 16)), (6, (8, 16))):
            for depth in depths:
                for w, h in ((1, 1), (7, 5), (33, 17), (120, 90)):
                    rb = (w*chans[color]*depth+7)//8
                    rows = [bytes(rnd.randrange(256) for _ in range(rb)) for _ in range(h)]
                    if color == 3 and depth < 8: rows = [bytes(rnd.randrange(256) for _ in range(rb)) for _ in range(h)]
                    plte = bytes(rnd.randrange(256) for _ in range(3*(1 << min(depth, 8)))) if color == 3 else None
                    trns = None
                    if color == 3 and rnd.random() < 0.6: trns = bytes(rnd.choice([0, 255, 128]) for _ in range(rnd.randrange(1, 1 << min(depth, 8))))
                    if color == 0 and rnd.random() < 0.5: trns = struct.pack('>H', rnd.randrange(1 << depth))
                    if color == 2 and rnd.random() < 0.5: trns = struct.pack('>HHH', *[rnd.randrange(1 << depth) for _ in range(3)])
                    pngs.append(raw_png(w, h, depth, color, rows, plte, trns, level=rnd.choice([0, 1, 6, 9])))
        jar = Path(self.folder.name)/'png.jar'
        with zipfile.ZipFile(jar, 'w') as z:
            z.writestr('META-INF/MANIFEST.MF', 'Manifest-Version: 1.0\nMain-Class: PngTest\n')
            z.write(out/'PngTest.class', 'PngTest.class')
            for i, png in enumerate(pngs): z.writestr(f'p{i}.png', png)
        run = subprocess.run([str(HOST), '-heap', '3000', '-cp', str(LIBRARY), '-jar', str(jar)],
                             capture_output=True, text=True, encoding='utf-8', timeout=120)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(len(run.stdout.splitlines()), 60)
        # Checksums of the whole-image decoder (before PNGs were decoded row by row).
        self.assertEqual(hashlib.sha256(run.stdout.encode()).hexdigest(), '2b67ddb85087b293df18696d995274d3ce31ef37bbd5dd80c7ea592b11fb6a80')

    def test_png_alpha_switches_from_bits_to_bytes_mid_image(self):
        """RGBA and grey+alpha whose first rows have only 0 and 255, then partial alpha."""
        import hashlib, random, struct, zipfile, zlib
        out = Path(self.folder.name)/'pngswitch'
        javac([ROOT/'jvm/iotests/PngTest.java'], out)
        rnd = random.Random(9)
        def chunk(t, data): return struct.pack('>I', len(data))+t+data+struct.pack('>I', zlib.crc32(t+data) & 0xffffffff)
        def png(w, h, color, rows):
            head = bytes([0x89])+b'PNG\r\n\x1a\n'+chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, color, 0, 0, 0))
            return head+chunk(b'IDAT', zlib.compress(b''.join(b'\x00'+r for r in rows), 6))+chunk(b'IEND', b'')
        pngs = []
        for w, h, switch, color in ((13, 9, 4, 6), (64, 40, 39, 6), (31, 20, 0, 6), (50, 30, None, 6), (17, 11, 5, 4), (40, 25, None, 4), (8, 8, 7, 6)):
            channels = 4 if color == 6 else 2
            rows = []
            for y in range(h):
                row = bytearray()
                for x in range(w):
                    a = rnd.choice([0, 255]) if switch is None or y < switch else rnd.choice([0, 255, 77, 200])
                    if switch is not None and y == switch and x == w//2: a = 128
                    row += bytes(rnd.randrange(256) for _ in range(channels-1))+bytes([a])
                rows.append(bytes(row))
            pngs.append(png(w, h, color, rows))
        jar = Path(self.folder.name)/'pngswitch.jar'
        with zipfile.ZipFile(jar, 'w') as z:
            z.writestr('META-INF/MANIFEST.MF', 'Manifest-Version: 1.0\nMain-Class: PngTest\n')
            z.write(out/'PngTest.class', 'PngTest.class')
            for i, data in enumerate(pngs): z.writestr(f'p{i}.png', data)
        run = subprocess.run([str(HOST), '-heap', '3000', '-cp', str(LIBRARY), '-jar', str(jar)],
                             capture_output=True, text=True, encoding='utf-8', timeout=120)
        self.assertEqual(run.returncode, 0, run.stderr)
        # Checksums of the byte-alpha decoder.
        self.assertEqual(hashlib.sha256(run.stdout.encode()).hexdigest(),
                         'c40b38fdca8dc6cf74b2b67dcead09ed3a6fc141de7e338085fbbb1fe1ae4693')

    def make_jar(self, classes, path):
        """Classes deflated, small resources stored and deflated, a large one deflated (dynamic codes)."""
        import random, struct, zipfile
        rnd = random.Random(7)
        words = ['alpha', 'beta', 'gamma', 'delta', 'кот', 'дом', '\n']
        big = ''.join(rnd.choice(words) + ' ' for _ in range(40000)).encode('utf-8')[:200000]
        utf = 'Нокиа'.encode('utf-8')
        data = struct.pack('>ih', 123456, -7) + struct.pack('>H', len(utf)) + utf + struct.pack('>q', -9000000000) + b'\x01\xfe'
        with zipfile.ZipFile(path, 'w') as z:
            z.writestr('META-INF/MANIFEST.MF', 'Manifest-Version: 1.0\r\nMain-Class: Resources\r\n', zipfile.ZIP_STORED)
            for item in sorted(classes.rglob('*.class')):
                z.write(item, item.relative_to(classes).as_posix(), zipfile.ZIP_DEFLATED)
            z.writestr('res/hello.txt', 'Привет, B220! Hello from the JAR.\n'.encode('utf-8'), zipfile.ZIP_DEFLATED)
            z.writestr('local.txt', b'relative resource\n', zipfile.ZIP_STORED)
            z.writestr('res/data.bin', data, zipfile.ZIP_STORED)
            z.writestr('res/big.txt', big, zipfile.ZIP_DEFLATED, compresslevel=9)

    def test_resources_from_a_jar_match_the_jdk(self):
        root = Path(self.folder.name)
        ours_classes, reference_classes = root/'jar-ours', root/'jar-reference'
        source = ROOT/'jvm/tests/Resources.java'
        javac([source], ours_classes)
        reference_classes.mkdir(exist_ok=True)
        subprocess.run([str(JDK/'javac.exe'), '--release', '8', '-nowarn', '-Xlint:-options', '-encoding', 'UTF-8',
                        '-d', str(reference_classes), str(source)], check=True, capture_output=True)
        self.make_jar(ours_classes, root/'ours.jar')
        self.make_jar(reference_classes, root/'reference.jar')
        expected = subprocess.run([str(JDK/'java.exe'), '-Dstdout.encoding=UTF-8', '-cp', str(root/'reference.jar'), 'Resources'],
                                  capture_output=True, text=True, encoding='utf-8', timeout=120)
        self.assertEqual(expected.returncode, 0, expected.stderr)
        ours = subprocess.run([str(HOST), '-cp', str(LIBRARY), '-jar', str(root/'ours.jar'), 'Resources'],
                              capture_output=True, text=True, encoding='utf-8', timeout=120)
        self.assertEqual(ours.returncode, 0, ours.stderr)
        self.assertEqual(ours.stdout, expected.stdout)

    def midlet_jar(self, path):
        """DrawTest with a PNG sprite: opaque red, opaque blue and transparent quadrants (RGBA)."""
        import zipfile
        from PIL import Image
        classes = Path(self.folder.name)/'midlet-classes'
        javac([ROOT/'jvm/midlets/DrawTest.java'], classes)
        sprite = Image.new('RGBA', (20, 20), (0, 0, 0, 0))
        for x in range(20):
            for y in range(20):
                if x < 10 and y < 10: sprite.putpixel((x, y), (255, 0, 0, 255))
                elif x >= 10 and y < 10: sprite.putpixel((x, y), (0, 0, 255, 255))
        import io
        png = io.BytesIO()
        sprite.save(png, 'PNG')
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr('META-INF/MANIFEST.MF', 'Manifest-Version: 1.0\r\nMIDlet-1: Draw Test, /icon.png, DrawTest\r\n'
                       'MIDlet-Name: Draw Test\r\nMIDlet-Vendor: GO1\r\nMIDlet-Version: 1.0\r\n'
                       'MicroEdition-Profile: MIDP-2.0\r\nMicroEdition-Configuration: CLDC-1.1\r\n')
            for item in sorted(classes.rglob('*.class')):
                z.write(item, item.relative_to(classes).as_posix())
            z.writestr('sprite.png', png.getvalue())

    def run_midlet(self, keys, run_ms):
        root = Path(self.folder.name)
        jar, shot = root/'midlet.jar', root/f'shot-{len(keys)}.ppm'
        if not jar.exists():
            self.midlet_jar(jar)
        command = [str(HOST), '-cp', str(LIBRARY), '-jar', str(jar), '-run', str(run_ms), '-shot', str(shot)]
        if keys:
            command[1:1] = ['-keys', ','.join(f'{at}:{code}' for at, code in keys)]
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', timeout=120)
        from PIL import Image
        return result, Image.open(shot).convert('RGB')

    def test_midlet_draws_and_handles_keys(self):
        result, image = self.run_midlet([], 1500)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('startApp Draw Test', result.stdout)
        px = image.getpixel
        self.assertEqual(px((5, 5)), (0, 0, 128))          # background
        self.assertEqual(px((20, 20)), (248, 0, 0))        # fillRect (RGB565)
        self.assertEqual(px((90, 60)), (248, 252, 248))    # drawLine
        self.assertEqual(px((120, 20)), (248, 252, 0))     # fillTriangle
        text = [px((x, y)) for x in range(20, 156) for y in range(70, 90)]
        self.assertGreater(text.count((248, 252, 248)), 40)  # "Привет, B220!" glyphs
        self.assertEqual(px((12, 102)), (248, 0, 0))       # PNG opaque red
        self.assertEqual(px((22, 102)), (0, 0, 248))       # PNG opaque blue
        self.assertEqual(px((12, 112)), (0, 0, 128))       # PNG transparent -> background
        # 8x4 arrow rotated 90 degrees clockwise: 4 wide, 8 tall, green end at the top.
        self.assertEqual(px((61, 100)), (0, 252, 0))
        self.assertEqual(px((61, 107)), (248, 0, 0))
        r, g, b = px((105, 105))                           # 50% white over navy
        self.assertTrue(120 <= r <= 136 and 120 <= g <= 136 and 184 <= b <= 200, (r, g, b))
        self.assertEqual(px((135, 105)), (0, 252, 248))    # clipped translated fill
        self.assertEqual(px((145, 105)), (0, 0, 128))      # outside the clip
        self.assertEqual(px((2, 210)), (32, 32, 32))       # command bar
        _, pressed = self.run_midlet([(700, -5)], 1500)
        self.assertEqual(pressed.getpixel((5, 5)), (0, 128, 0))
        exited, _ = self.run_midlet([(700, -7)], 3000)
        self.assertEqual(exited.returncode, 0, exited.stderr)
        # MIDP: after notifyDestroyed the MIDlet has cleaned up itself; destroyApp is
        # called only when the platform ends it (red key).
        self.assertIn('startApp', exited.stdout)
        self.assertNotIn('destroyApp', exited.stdout)

    def test_record_stores_players_and_nokia_graphics(self):
        classes = Path(self.folder.name)/'platform-classes'
        javac([ROOT/'jvm/midlets/PlatformTest.java'], classes)
        ours = subprocess.run([str(HOST), '-cp', f'{LIBRARY};{classes}', 'PlatformTest'],
                              capture_output=True, text=True, encoding='utf-8', timeout=120)
        self.assertEqual(ours.returncode, 0, ours.stderr)
        lines = ours.stdout.splitlines()
        self.assertEqual(lines[:12], [
            'not found: missing', 'ids 1 2 records 2 next 4', 'first 5 second 1,2', 'deleted 3', 'sorted 2 1',
            'reopened 2 stores 1', 'after delete null',
            'state 100', 'volume 100', 'started true', 'stopped 300 audio/midi', 'closed 0'])
        # Nokia flips and counter-clockwise rotations against PIL on the same 3x2 colour indexes.
        from PIL import Image
        source = Image.new('L', (3, 2))
        source.putdata(range(6))
        expected = {0: source, 0x2000: source.transpose(Image.FLIP_LEFT_RIGHT),
                    0x4000: source.transpose(Image.FLIP_TOP_BOTTOM), 0x6000: source.transpose(Image.ROTATE_180),
                    90: source.transpose(Image.ROTATE_90), 180: source.transpose(Image.ROTATE_180),
                    270: source.transpose(Image.ROTATE_270)}
        for line in lines[12:12+len(expected)]:
            name, *pixels = line.split()
            self.assertEqual([int(v) for v in pixels], list(expected[int(name[1:])].getdata()), line)
        # Sprite: the reference pixel stays put through a turn; TextBox multi-tap
        # (see PlatformTest.textInput for the keys); IMPLICIT List selection.
        self.assertEqual(lines[12+len(expected):], [
            'sprite frames 2 frame 2 at 9,10', 'turned at 9,9 ref 10,10', 'collides true', 'collides false',
            'text [бIP0J] caret 3', 'url [a?]', 'list 2 c'])

    def test_small_heap_collects_and_survivors_stay_valid(self):
        ours = self.run_ours('GcStress', 160)
        self.assertEqual(ours.returncode, 0, ours.stderr)
        collections = int(ours.stderr.split('collections ')[1].split(',')[0])
        self.assertGreater(collections, 5)

    def test_dead_local_does_not_keep_objects(self):
        # Opera Mini probes memory this way: fill a Vector until OutOfMemoryError,
        # then call on with the Vector left in a dead local.
        source = Path(self.folder.name)/'DeadLocal.java'
        source.write_text('import java.util.Vector;\n'
                          'public class DeadLocal {\n'
                          '  static int probe() {\n'
                          '    Vector v = new Vector();\n'
                          '    try { while (true) v.addElement(new byte[8192]); } catch (OutOfMemoryError e) {}\n'
                          '    int n = v.size();\n'
                          '    return n + after();\n'
                          '  }\n'
                          '  static int after() { byte[] b = new byte[100000]; return b.length; }\n'
                          '  public static void main(String[] a) { System.out.println(probe() > 100000 ? "ok" : "bad"); }\n'
                          '}\n', encoding='utf-8')
        javac([source], self.ours)
        ours = self.run_ours('DeadLocal', 300)
        self.assertEqual(ours.returncode, 0, ours.stderr)
        self.assertEqual(ours.stdout.strip(), 'ok')

    def test_uncaught_exception_reports_and_fails(self):
        source = Path(self.folder.name)/'Thrower.java'
        source.write_text('public class Thrower { public static void main(String[] a) {'
                          ' throw new IllegalStateException("bad state"); } }', encoding='utf-8')
        javac([source], self.ours)
        ours = self.run_ours('Thrower')
        self.assertEqual(ours.returncode, 1)
        self.assertIn('Uncaught exception: java.lang.IllegalStateException: bad state', ours.stderr)
        self.assertIn('  at Thrower.main ', ours.stderr)


if __name__ == '__main__':
    unittest.main()
