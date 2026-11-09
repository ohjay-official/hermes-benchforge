"""Firmware that misbehaves in the ways real first drafts do, run on the simulated chip.

Needs a compiler and Node.js (CI uses the real arduino-cli); skipped without them.
"""

import tempfile
import unittest
from pathlib import Path

from benchforge import build, spec
from benchforge.forge import Workspace

WS = Workspace(Path(tempfile.mkdtemp(prefix="benchforge-engine-test-")))
READY = WS.ready()[0]

LED_BENCH = {
    "name": "led", "parts": [{"id": "led", "type": "led", "pin": "D13"}],
    "steps": [{"id": "hello", "expect": {"serial": "hello", "within_ms": 500}},
              {"id": "on", "expect": {"part": "led", "stays": "on", "for_ms": 300}}],
}


def run(sketch: str, raw=LED_BENCH, crash=False):
    return WS.test(spec.load(raw), sketch, crash=crash, save=False)


def fails(out) -> dict:
    return {r.id: r.saw for run in out.report.runs for r in run.results if not r.passed}


@unittest.skipUnless(READY, "needs a compiler and Node.js")
class MisbehavingFirmware(unittest.TestCase):
    def test_good_sketch(self):
        out = run('void setup(){ pinMode(13, OUTPUT); digitalWrite(13, HIGH); Serial.begin(9600); '
                  'Serial.println("hello"); }\nvoid loop(){}\n')
        self.assertTrue(out.passed, fails(out))

    def test_forgotten_pinmode_is_named(self):
        out = run('void setup(){ digitalWrite(13, HIGH); Serial.begin(9600); Serial.println("hello"); }\n'
                  'void loop(){}\n')
        self.assertIn("pinMode(13, OUTPUT)", fails(out)["on"])

    def test_printed_too_early_is_explained(self):
        raw = dict(LED_BENCH, steps=list(reversed(LED_BENCH["steps"])))
        out = run('void setup(){ pinMode(13, OUTPUT); digitalWrite(13, HIGH); Serial.begin(9600); '
                  'Serial.println("hello"); }\nvoid loop(){}\n', raw=raw)
        self.assertIn("before this check started", fails(out)["hello"])

    def test_wrong_baud_or_no_serial(self):
        out = run("void setup(){ pinMode(13, OUTPUT); digitalWrite(13, HIGH); }\nvoid loop(){}\n")
        self.assertIn("nothing was printed", fails(out)["hello"])

    def test_hang_in_setup_is_judged_not_fatal(self):
        out = run("void setup(){ while (true) {} }\nvoid loop(){}\n")
        self.assertFalse(out.passed)
        self.assertEqual(set(fails(out)), {"on", "hello"})

    def test_watchdog_reset_loop(self):
        out = run("#include <avr/wdt.h>\nvoid setup(){ pinMode(13, OUTPUT); digitalWrite(13, HIGH); "
                  "Serial.begin(9600); Serial.println(\"hello\"); wdt_enable(WDTO_15MS); }\n"
                  "void loop(){ delay(100); }\n")
        self.assertIsNotNone(out.report)              # it runs; the resets just show up in the checks

    def test_compile_error_reaches_the_fixer(self):
        out = run("void setup(){ pinMode(13 OUTPUT); }\nvoid loop(){}\n")
        self.assertTrue(out.compile_error)
        self.assertIn("did not compile", out.brief())
        self.assertIn("error", out.brief().lower())

    def test_refused_sketch_never_compiles(self):
        out = run('#include "/srv/notes/plan.h"\nvoid setup(){}\nvoid loop(){}\n')
        self.assertIn("not on BenchForge's list", out.compile_error)

    def test_serial_flood_is_capped(self):
        out = run('void setup(){ pinMode(13, OUTPUT); digitalWrite(13, HIGH); Serial.begin(115200); }\n'
                  'void loop(){ Serial.println("hello hello hello hello hello hello"); }\n')
        self.assertTrue(out.passed, fails(out))

    def test_same_firmware_same_result(self):
        sketch = ('void setup(){ pinMode(13, OUTPUT); Serial.begin(9600); Serial.println("hello"); }\n'
                  'void loop(){ digitalWrite(13, (millis() / 137) % 2); }\n')
        a, b = run(sketch, crash=True), run(sketch, crash=True)
        self.assertEqual(a.build.hex_sha256, b.build.hex_sha256)
        self.assertEqual([r.to_json() for r in a.report.runs], [r.to_json() for r in b.report.runs])

    def test_elf_probes_found(self):
        b = build.compile_sketch("void setup(){}\nvoid loop(){ delay(millis() % 3); }\n", "probe",
                                 toolchain=WS.toolchain, compiler=WS.compiler)
        self.assertIn("main_pc", b.probes)
        self.assertIn("timer0_millis", b.probes)


if __name__ == "__main__":
    unittest.main()
