"""B22 Java VM on the Windows host, checked against the JDK's own JVM.

Each jvm/tests/*.java program is compiled twice: against our class library
(run on b22jvm.exe) and against the JDK (run on java.exe); outputs must match.
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
        environment = dict(os.environ, B22JVM_STATS='1')
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

    def test_small_heap_collects_and_survivors_stay_valid(self):
        ours = self.run_ours('GcStress', 160)
        self.assertEqual(ours.returncode, 0, ours.stderr)
        collections = int(ours.stderr.split('collections')[1])
        self.assertGreater(collections, 5)

    def test_uncaught_exception_reports_and_fails(self):
        source = Path(self.folder.name)/'Thrower.java'
        source.write_text('public class Thrower { public static void main(String[] a) {'
                          ' throw new IllegalStateException("bad state"); } }', encoding='utf-8')
        javac([source], self.ours)
        ours = self.run_ours('Thrower')
        self.assertEqual(ours.returncode, 1)
        self.assertIn('Uncaught exception: java.lang.IllegalStateException: bad state', ours.stderr)


if __name__ == '__main__':
    unittest.main()
