"""Fast tests that need no compiler and no simulator: the bench format, the sketch scanner, the check
logic (on hand-made traces), receipts and the flash gate."""

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from benchforge import build, checks, gate, receipt, spec
from benchforge.bench import Report, RunReport

ROOT = Path(__file__).resolve().parent.parent
GREENHOUSE = json.loads((ROOT / "examples" / "greenhouse-fan" / "bench.json").read_text())


def bench(**over):
    raw = copy.deepcopy(GREENHOUSE)
    raw.update(over)
    return spec.load(raw)


class BenchFormat(unittest.TestCase):
    def test_greenhouse_reads_as_plain_english(self):
        b = bench()
        self.assertEqual(spec.describe(b), [
            "1. at power-on → fan stays off for 1.5 s",
            "2. temp reads 32 °C → fan turns on within 1 s",
            "3. temp reads 28.5 °C → fan stays on for 2 s",
            "4. temp reads 26 °C → fan turns off within 1 s",
            "5. temp reads 29 °C → fan stays off for 2 s",
            "6. temp reads 30.8 °C → fan turns on within 1 s",
            "7. The whole time, led blinks at 1 Hz (±10 %)",
            "8. If temp is unplugged → fan on (fail safe)",
        ])

    def test_steps_share_one_timeline(self):
        b = bench()
        windows = [(c.window.start_ms, c.window.end_ms) for c in b.steps]
        self.assertEqual(windows[0], (0, 1500))
        self.assertEqual(windows[1], (1500, 2700))          # within 1000 + hold 200
        for (a0, a1), (b0, _b1) in zip(windows, windows[1:]):
            self.assertEqual(a1, b0)
        self.assertEqual(b.duration_ms, windows[-1][1] + spec.TAIL_MS)

    def test_hash_is_stable_and_key_order_free(self):
        a = bench().sha256
        reordered = json.loads(json.dumps(GREENHOUSE, sort_keys=True))
        self.assertEqual(a, spec.load(reordered).sha256)
        self.assertNotEqual(a, bench(goal="something else").sha256)

    def test_rejections_explain_the_fix(self):
        cases = {
            "carries Serial": {"parts": [{"id": "fan", "type": "relay", "pin": "D1"}]},
            "must be on A0 to A5": {"parts": [{"id": "temp", "type": "lm35", "pin": "D4"}]},
            "unknown part type": {"parts": [{"id": "x", "type": "laser", "pin": "D4"}]},
            "already used": {"parts": [{"id": "a", "type": "led", "pin": "D4"}, {"id": "b", "type": "led", "pin": "4"}]},
        }
        for needle, over in cases.items():
            raw = {"name": "t", "parts": over["parts"],
                   "steps": [{"id": "s", "expect": {"serial": "hi"}}]}
            with self.assertRaises(spec.BenchError) as cm:
                spec.load(raw)
            self.assertIn(needle, str(cm.exception))

    def test_set_can_only_touch_inputs(self):
        raw = copy.deepcopy(GREENHOUSE)
        raw["steps"][1]["set"] = {"fan": 1}
        with self.assertRaisesRegex(spec.BenchError, "can only change inputs"):
            spec.load(raw)

    def test_whole_bench_has_a_time_limit(self):
        raw = copy.deepcopy(GREENHOUSE)
        raw["steps"] = [{"id": f"s{i}", "expect": {"part": "fan", "stays": "off", "for_ms": 30000}} for i in range(3)]
        with self.assertRaisesRegex(spec.BenchError, "under 60 s"):
            spec.load(raw)

    def test_unknown_fields_are_refused(self):
        raw = copy.deepcopy(GREENHOUSE)
        raw["steps"][0]["expect"]["fudge"] = True
        with self.assertRaisesRegex(spec.BenchError, "unknown field 'fudge'"):
            spec.load(raw)


GOOD = "void setup() {}\nvoid loop() {}\n"


class SketchScanner(unittest.TestCase):
    def assertRefused(self, src, needle):
        problems = build.scan(src)
        self.assertTrue(problems, src)
        self.assertTrue(any(needle in p for p in problems), problems)

    def test_plain_sketch_passes(self):
        self.assertEqual(build.scan("#include <Servo.h>\n#include \"EEPROM.h\"\n" + GOOD), [])

    def test_strings_and_comments_are_not_directives(self):
        src = 'const char *s = "#include </etc/passwd>";\n// #include "x.h"\n/* #pragma */\n' + \
              'const char *r = R"(\n#include "/etc/shadow"\n)";\n' + GOOD
        self.assertEqual(build.scan(src), [])

    def test_file_reads_are_refused(self):
        self.assertRefused('#include "/etc/passwd"\n' + GOOD, "not on BenchForge's list")
        self.assertRefused("#include <../../secrets.h>\n" + GOOD, "not on BenchForge's list")
        self.assertRefused("#include <WiFi.h>\n" + GOOD, "not on BenchForge's list")
        self.assertRefused('#inc\\\nlude "/etc/passwd"\n' + GOOD, "/etc/passwd")
        self.assertRefused('/* x */ # include "/etc/passwd"\n' + GOOD, "/etc/passwd")
        self.assertRefused('%:include "/etc/passwd"\n' + GOOD, "/etc/passwd")
        self.assertRefused("#define F </etc/passwd>\n#include F\n" + GOOD, "#include must name")
        self.assertRefused("#include_next <Arduino.h>\n" + GOOD, "#include_next is not allowed")
        self.assertRefused('#pragma GCC dependency "/etc/passwd"\n' + GOOD, "#pragma is not allowed")

    def test_assembler_tricks_are_refused(self):
        self.assertRefused('asm(".incbin \\"/etc/passwd\\"");\n' + GOOD, "asm is not allowed")
        self.assertRefused('__asm__ volatile ("nop");\n' + GOOD, "__asm__ is not allowed")
        self.assertRefused('_Pragma("GCC poison x")\n' + GOOD, "_Pragma is not allowed")

    def test_needs_setup_and_loop(self):
        self.assertRefused("int x;\n", "setup() and void loop()")


def fake_run(duration, pins, signals=None, serial=None):
    """A trace shaped like the engine's, from per-ms level lists."""
    out = {"duration_ms": duration, "pins": {}, "signals": signals or {},
           "serial": {"text": "", "lines": serial or []}}
    for pin, levels in pins.items():
        edges, last = [], None
        for t, lv in enumerate(levels):
            if lv != last and lv in (0, 1):
                edges.append([t * 1000, lv])
            last = lv
        out["pins"][pin] = {"level": levels, "edges": edges, "truncated": False}
    return out


SMALL = {
    "name": "t", "parts": [{"id": "fan", "type": "relay", "pin": "D7"}, {"id": "temp", "type": "lm35", "pin": "A0"}],
    "steps": [{"id": "off", "expect": {"part": "fan", "stays": "off", "for_ms": 100}},
              {"id": "on", "set": {"temp": 40}, "expect": {"part": "fan", "becomes": "on", "within_ms": 100,
                                                           "hold_ms": 50}}],
}


class CheckLogic(unittest.TestCase):
    def judge(self, levels, raw=SMALL):
        b = spec.load(raw)
        return {r.id: r for r in checks.judge(b, fake_run(b.duration_ms, {"D7": levels}))}

    def test_on_time_pass_reports_latency(self):
        res = self.judge([0] * 130 + [1] * 320)
        self.assertTrue(res["off"].passed)
        self.assertTrue(res["on"].passed)
        self.assertEqual(res["on"].detail["latency_ms"], 30)

    def test_too_late_is_named(self):
        res = self.judge([0] * 230 + [1] * 220)
        self.assertFalse(res["on"].passed)
        self.assertIn("too late", res["on"].saw)

    def test_never_set_as_output_is_named(self):
        res = self.judge([-1] * 450)
        self.assertIn("pinMode(7, OUTPUT)", res["off"].saw)

    def test_relay_chatter_is_caught(self):
        levels = [0] * 100 + [(t // 20) % 2 for t in range(350)]
        res = self.judge(levels)
        self.assertIn("fan_chatter", res)
        self.assertIn("clicked", res["fan_chatter"].saw)

    def test_active_low_relay(self):
        raw = copy.deepcopy(SMALL)
        raw["parts"][0]["active_low"] = True
        res = self.judge([1] * 130 + [0] * 320, raw)
        self.assertTrue(res["off"].passed and res["on"].passed)

    def test_blink_frequency(self):
        raw = {"name": "b", "parts": [{"id": "led", "type": "led", "pin": "D13"}],
               "steps": [{"id": "blink", "expect": {"part": "led", "blinks_hz": 2, "for_ms": 3000}}]}
        b = spec.load(raw)
        two_hz = [(t // 250) % 2 for t in range(b.duration_ms)]
        one_hz = [(t // 500) % 2 for t in range(b.duration_ms)]
        self.assertTrue(checks.judge(b, fake_run(b.duration_ms, {"D13": two_hz}))[0].passed)
        r = checks.judge(b, fake_run(b.duration_ms, {"D13": one_hz}))[0]
        self.assertFalse(r.passed)
        self.assertIn("1.00 Hz", r.saw)

    def test_serial_window(self):
        raw = {"name": "s", "parts": [{"id": "led", "type": "led", "pin": "D13"}],
               "steps": [{"id": "hello", "expect": {"serial": "ready", "within_ms": 500}}]}
        b = spec.load(raw)
        ok = checks.judge(b, fake_run(b.duration_ms, {"D13": [0] * b.duration_ms}, serial=[[120, "ready!"]]))
        late = checks.judge(b, fake_run(b.duration_ms, {"D13": [0] * b.duration_ms}, serial=[[600, "ready"]]))
        self.assertTrue(ok[0].passed)
        self.assertFalse(late[0].passed)
        self.assertIn("too late", late[0].saw)


def fake_receipt(verdict="pass", hex_bytes=b":00000001FF\n"):
    b = bench()
    rr = RunReport(name="main", title="Your checks",
                   results=[checks.Result(id="x", text="t", passed=verdict == "pass", saw="s", window=(0, 1))])
    rep = Report(bench=b, runs=[rr])

    class FakeBuild:
        sketch_sha256 = "s" * 64
        hex_sha256 = hashlib.sha256(hex_bytes).hexdigest()
        flash_bytes, ram_bytes, compiler = 100, 10, "test"
    return receipt.make(b, FakeBuild, rep, node_version="22")


class Receipts(unittest.TestCase):
    def test_intact_receipt_checks_out(self):
        self.assertEqual(receipt.check_integrity(fake_receipt()), [])

    def test_editing_a_receipt_is_caught(self):
        r = fake_receipt("fail")
        r["verdict"] = "pass"
        self.assertIn("edited", receipt.check_integrity(r)[0])

    def test_swapping_the_bench_is_caught(self):
        r = fake_receipt()
        r["bench"]["spec"]["goal"] = "easier"
        self.assertTrue(receipt.check_integrity(r))


class FlashGate(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.hex = b":100000000C945C000C946E000C946E000C946E00CA\n:00000001FF\n"
        self.hex_path = self.dir / "fw.hex"
        self.hex_path.write_bytes(self.hex)

    def stamp(self, verdict, approved=True):
        r = fake_receipt(verdict, self.hex)
        rp, _ = receipt.save(r, self.hex.decode(), self.dir / "receipts")
        gate.mark_passing(r, rp, self.dir)
        if approved:
            from benchforge.forge import Workspace
            Workspace(self.dir).approve(bench(), by="test")
        return r

    def decide(self, cmd, **extra):
        return gate.decide("terminal", {"command": cmd, **extra}, self.dir)

    def test_other_commands_pass_through(self):
        for cmd in ("ls", "arduino-cli board list", "arduino-cli compile --fqbn arduino:avr:uno .",
                    "avrdude -c arduino -p m328p -P /dev/ttyUSB0 -U flash:r:backup.hex:i", "avrdude -?"):
            self.assertIsNone(self.decide(cmd))
        self.assertIsNone(gate.decide("read_file", {"path": "x"}, self.dir))

    def test_untested_firmware_is_blocked(self):
        d = self.decide(f"arduino-cli upload -p /dev/ttyACM0 --fqbn arduino:avr:uno --input-file {self.hex_path}")
        self.assertEqual(d["action"], "block")
        self.assertIn("no passing BenchForge receipt", d["message"])

    def test_failed_firmware_is_blocked(self):
        self.stamp("fail")
        d = self.decide(f"avrdude -c arduino -p m328p -P /dev/ttyUSB0 -U flash:w:{self.hex_path}:i")
        self.assertEqual(d["action"], "block")

    def test_green_firmware_is_allowed(self):
        self.stamp("pass")
        self.assertIsNone(self.decide(f"avrdude -p m328p -U flash:w:{self.hex_path}:i"))
        self.assertIsNone(self.decide(f"avrdude -p m328p -U {self.hex_path}"))
        self.assertIsNone(self.decide("arduino-cli upload -p COM3 --fqbn arduino:avr:uno --input-file fw.hex",
                                      workdir=str(self.dir)))

    def test_unapproved_checks_do_not_count(self):
        self.stamp("pass", approved=False)
        self.assertEqual(self.decide(f"avrdude -U flash:w:{self.hex_path}")["action"], "block")

    def test_a_tampered_receipt_does_not_count(self):
        self.stamp("pass")
        rp = next((self.dir / "receipts").glob("*.receipt.json"))
        r = json.loads(rp.read_text())
        r["runs"][0]["results"][0]["saw"] = "changed"
        rp.write_text(json.dumps(r))
        self.assertEqual(self.decide(f"avrdude -U flash:w:{self.hex_path}")["action"], "block")

    def test_sketch_uploads_and_hidden_commands_are_blocked(self):
        self.stamp("pass")
        for cmd in ("arduino-cli compile -u -p COM3 --fqbn arduino:avr:uno .",
                    "arduino-cli upload -p COM3 --fqbn arduino:avr:uno ./sketch",
                    "pio run -t upload", f"sh -c 'avrdude -U flash:w:{self.hex_path}'",
                    f"true && avrdude -U flash:w:{self.dir / 'other.hex'}",
                    f"avrdude -U {self.dir / 'other.hex'}", f"avrdude -Uflash:w:{self.dir / 'other.hex'}:i"):
            self.assertEqual(self.decide(cmd)["action"], "block", cmd)


if __name__ == "__main__":
    unittest.main()
