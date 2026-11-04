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
        self.assertIn('destroyApp', exited.stdout)

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
