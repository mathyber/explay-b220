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

TESTS = sorted((ROOT/'jvm/tests').glob('*.java'))
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
        subprocess.run([str(JDK/'javac.exe'), '--release', '8', '-nowarn', '-Xlint:-options', '-d', str(cls.reference),
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
