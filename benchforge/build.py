"""Compile one Arduino sketch for the Uno, sealed off from the rest of the computer.

The sketch may have been written by a model. Before it reaches the compiler it is read the way the
C preprocessor will read it (comments removed, continued lines joined) and refused if it tries to
pull in anything beyond a short list of standard Arduino headers: no local includes, no absolute or
relative paths, no computed includes, no inline assembly (``.incbin`` can paste any file into the
firmware). It is then compiled alone, in a fresh folder, with a small fixed environment.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import elf
from .toolchain import FQBN, Toolchain, ToolchainError

MAX_SKETCH_BYTES = 64 * 1024
FLASH_BYTES = 32256          # Uno: 32 KB minus the 512-byte bootloader
SRAM_BYTES = 2048

ALLOWED_INCLUDES = {
    "Arduino.h", "Servo.h", "EEPROM.h",
    "avr/io.h", "avr/interrupt.h", "avr/pgmspace.h", "avr/wdt.h", "avr/eeprom.h",
    "util/delay.h", "util/atomic.h", "util/crc16.h",
    "math.h", "stdint.h", "stdlib.h", "string.h", "stdbool.h", "stdio.h", "limits.h", "ctype.h",
}
BANNED_WORDS = re.compile(r"\b(asm|__asm|__asm__|_Pragma|__has_include|__has_include_next)\b")
ALLOWED_DIRECTIVES = {"include", "define", "undef", "if", "ifdef", "ifndef", "elif", "else", "endif",
                      "error", "warning", "line"}


class BuildError(RuntimeError):
    """The sketch did not compile. ``log`` holds the compiler's messages, with local paths removed."""

    def __init__(self, message: str, log: str = ""):
        super().__init__(message)
        self.log = log


@dataclass
class Build:
    hex: str
    elf: bytes
    sketch_sha256: str
    hex_sha256: str
    flash_bytes: int | None = None
    ram_bytes: int | None = None
    probes: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    log: str = ""
    compiler: str = ""


# ---------------------------------------------------------------- reading the sketch like cpp does

def _preprocess_view(src: str) -> str:
    """Join backslash-continued lines and blank out comments, keeping string literals intact."""
    src = src.replace("\r\n", "\n").replace("\r", "\n")
    src = re.sub(r"\\[ \t]*\n", "", src)
    out = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            j = src.find("\n", i)
            i = n if j < 0 else j
            out.append(" ")
        elif c == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            chunk = src[i:n if j < 0 else j + 2]
            out.append(" " + "\n" * chunk.count("\n"))
            i = n if j < 0 else j + 2
        elif c == "R" and src.startswith('R"', i) and (i == 0 or not (src[i - 1].isalnum() or src[i - 1] == "_")):
            m = re.match(r'R"([^ ()\\\t\n]{0,16})\(', src[i:])
            if not m:
                out.append(c)
                i += 1
                continue
            end = src.find(")" + m.group(1) + '"', i + len(m.group(0)))
            stop = n if end < 0 else end + len(m.group(1)) + 2
            out.append('""' + "\n" * src[i:stop].count("\n"))
            i = stop
        elif c in "\"'":
            j = i + 1
            while j < n and src[j] != c and src[j] != "\n":
                j += 2 if src[j] == "\\" else 1
            out.append(src[i:j + 1])
            i = j + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _strip_strings(line: str) -> str:
    return re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', '""', line)


def scan(src: str) -> list:
    """Problems that stop a sketch being compiled, as plain sentences (empty list: fine)."""
    problems = []
    if len(src.encode("utf-8")) > MAX_SKETCH_BYTES:
        return [f"the sketch is larger than {MAX_SKETCH_BYTES // 1024} KB"]
    if "\x00" in src:
        return ["the sketch contains a NUL byte"]
    if "??/" in src or "??=" in src:
        problems.append("trigraphs (??/, ??=) are not allowed")
    view = _preprocess_view(src)
    for lineno, line in enumerate(view.split("\n"), 1):
        stripped = line.strip()
        if stripped.startswith(("#", "%:")):
            body = stripped[1:] if stripped.startswith("#") else stripped[2:]
            m = re.match(r"\s*([A-Za-z_]\w*)", body)
            if not m:
                if body.strip():
                    problems.append(f"line {lineno}: unknown preprocessor line")
                continue
            word = m.group(1)
            if word not in ALLOWED_DIRECTIVES:
                problems.append(f"line {lineno}: #{word} is not allowed")
                continue
            if word == "include":
                rest = body[m.end():].strip()
                inc = re.fullmatch(r"<([A-Za-z0-9_/.]+)>", rest) or re.fullmatch(r'"([A-Za-z0-9_/.]+)"', rest)
                if not inc:
                    problems.append(f"line {lineno}: #include must name a standard header like <Servo.h>")
                elif inc.group(1) not in ALLOWED_INCLUDES:
                    problems.append(f"line {lineno}: #include {rest} is not on BenchForge's list of "
                                    f"standard headers")
        hit = BANNED_WORDS.search(line)
        if hit:
            problems.append(f"line {lineno}: {hit.group(1)} is not allowed (BenchForge only builds plain C++)")
        if re.search(r"\.(incbin|include|import)\b", line):
            problems.append(f"line {lineno}: assembler file includes are not allowed")
        if "##" in _strip_strings(line) or "%:%:" in _strip_strings(line):
            problems.append(f"line {lineno}: token pasting (##) is not allowed (it can build words the "
                            "scanner would refuse)")
    if not re.search(r"\bvoid\s+setup\s*\(", view) or not re.search(r"\bvoid\s+loop\s*\(", view):
        problems.append("an Arduino sketch needs both void setup() and void loop()")
    return problems


def sketch_sha256(src: str) -> str:
    return hashlib.sha256(src.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- compiling

def _clean_log(text: str, work: Path, name: str) -> str:
    text = text.replace(str(work / name / f"{name}.ino"), f"{name}.ino")
    text = text.replace(str(work), "<build>")
    home = str(Path.home())
    if home and home != "/":
        text = text.replace(home, "~")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    out = "\n".join(lines)
    return out if len(out) <= 4000 else out[:4000] + "\n… (cut)"


def _sizes(log: str):
    flash = re.search(r"Sketch uses (\d+) bytes", log)
    ram = re.search(r"Global variables use (\d+) bytes", log)
    return (int(flash.group(1)) if flash else None, int(ram.group(1)) if ram else None)


def compile_sketch(src: str, name: str = "sketch", *, toolchain: Toolchain | None = None,
                   compiler=None, timeout: int = 300) -> Build:
    """Scan, then compile. ``compiler`` overrides arduino-cli (tests and machines with a bare avr-gcc)."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,47}", name):
        raise BuildError("sketch name must be lowercase letters, digits, - and _")
    problems = scan(src)
    if problems:
        raise BuildError("BenchForge refused to compile this sketch:\n- " + "\n- ".join(problems),
                         "\n".join(problems))
    sha = sketch_sha256(src)
    work = Path(tempfile.mkdtemp(prefix="benchforge-build-"))
    try:
        sketch_dir = work / name
        sketch_dir.mkdir()
        (sketch_dir / f"{name}.ino").write_text(src, encoding="utf-8")
        out = work / "out"
        out.mkdir()
        if compiler is not None:
            log, label = compiler(sketch_dir, out, work)
        else:
            log, label = _arduino_cli(toolchain, sketch_dir, out, work, timeout)
        hex_path, elf_path = out / f"{name}.ino.hex", out / f"{name}.ino.elf"
        if not hex_path.is_file() or not elf_path.is_file():
            raise BuildError("the compiler finished but produced no firmware", log)
        hex_bytes = hex_path.read_bytes()
        hex_text = hex_bytes.decode("ascii")
        elf_bytes = elf_path.read_bytes()
        flash, ram = _sizes(log)
        b = Build(hex=hex_text, elf=elf_bytes, sketch_sha256=sha, hex_sha256=hashlib.sha256(hex_bytes).hexdigest(),
                  flash_bytes=flash, ram_bytes=ram, log=log, compiler=label)
        try:
            b.probes = elf.probes(elf_bytes)
        except elf.ElfError:
            b.probes = {}
        if ram is not None and ram > SRAM_BYTES * 0.75:
            b.warnings.append(f"global variables use {ram} of {SRAM_BYTES} bytes of RAM; "
                              "the Uno may run out of memory at runtime")
        return b
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _arduino_cli(tc: Toolchain | None, sketch_dir: Path, out: Path, work: Path, timeout: int):
    if tc is None or not tc.ready():
        raise ToolchainError("the Arduino compiler is not set up yet; run `hermes bench setup` once")
    args = ["compile", "--fqbn", FQBN, "--output-dir", str(out), "--build-path", str(work / "build"),
            "--warnings", "default", str(sketch_dir)]
    try:
        p = tc.run_cli(*args, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise BuildError(f"compiling took longer than {timeout} s and was stopped") from None
    log = _clean_log((p.stdout or "") + "\n" + (p.stderr or ""), work, sketch_dir.name)
    if p.returncode != 0:
        if "error" not in log.replace("Error during build", "").lower():
            # arduino-cli hides a failing tool's own message unless asked to be verbose
            try:
                v = tc.run_cli(*args, "--verbose", timeout=timeout)
                tail = _clean_log((v.stdout or "") + "\n" + (v.stderr or ""), work, sketch_dir.name)
                log = log + "\n" + tail[-2500:]
            except subprocess.TimeoutExpired:
                pass
        raise BuildError("the sketch did not compile", log)
    return log, f"arduino-cli ({FQBN})"


class BareAvrGcc:
    """Compile with a bare avr-gcc and an ArduinoCore-avr source tree, for developing BenchForge where
    arduino-cli cannot download its core. Same flags arduino-cli uses for the Uno, minus LTO."""

    FLAGS = ["-mmcu=atmega328p", "-DF_CPU=16000000L", "-DARDUINO=10819", "-DARDUINO_AVR_UNO",
             "-DARDUINO_ARCH_AVR", "-Os", "-ffunction-sections", "-fdata-sections", "-Wall"]
    CXX = ["-std=gnu++17", "-fno-exceptions", "-fno-threadsafe-statics", "-fpermissive"]

    def __init__(self, bin_dir: str, core: str, libraries: str | None = None):
        self.bin = Path(bin_dir)
        self.core = Path(core)
        self.libs = Path(libraries) if libraries else None
        self._core_objs: dict = {}

    @classmethod
    def from_env(cls):
        b, c = os.environ.get("BENCHFORGE_DEV_AVR_GCC"), os.environ.get("BENCHFORGE_DEV_CORE")
        return cls(b, c, os.environ.get("BENCHFORGE_DEV_LIBS")) if b and c else None

    def _run(self, args, log):
        p = subprocess.run(args, capture_output=True, text=True, timeout=120)
        log.append(p.stdout + p.stderr)
        return p.returncode

    def _core(self, cache: Path, log) -> list:
        key = str(cache)
        if key in self._core_objs:
            return self._core_objs[key]
        inc = self._inc()
        objs = []
        cache.mkdir(parents=True, exist_ok=True)
        srcs = sorted((self.core / "cores" / "arduino").iterdir())
        if self.libs and (self.libs / "Servo").is_dir():
            srcs += sorted((self.libs / "Servo" / "src" / "avr").glob("*.cpp"))
        for src in srcs:
            if src.suffix not in (".c", ".cpp", ".S"):
                continue
            obj = cache / (src.parent.name + "_" + src.name + ".o")
            if not obj.exists():
                cc = self.bin / ("avr-g++" if src.suffix == ".cpp" else "avr-gcc")
                extra = self.CXX if src.suffix == ".cpp" else (["-x", "assembler-with-cpp"] if src.suffix == ".S" else [])
                if self._run([str(cc), *self.FLAGS, "-w", *extra, *inc, "-c", str(src), "-o", str(obj)], log):
                    raise BuildError("the Arduino core did not compile", "\n".join(log))
            objs.append(obj)
        self._core_objs[key] = objs
        return objs

    def _inc(self) -> list:
        inc = ["-I", str(self.core / "cores" / "arduino"), "-I", str(self.core / "variants" / "standard"),
               "-I", str(self.core / "libraries" / "EEPROM" / "src")]
        if self.libs and (self.libs / "Servo").is_dir():
            inc += ["-I", str(self.libs / "Servo" / "src")]
        return inc

    def __call__(self, sketch_dir: Path, out: Path, work: Path):
        log: list = []
        name = sketch_dir.name
        core = self._core(Path(tempfile.gettempdir()) / "benchforge-devcore", log)
        cpp = work / "sketch.cpp"
        src = (sketch_dir / f"{name}.ino").read_text(encoding="utf-8")
        cpp.write_text("#include <Arduino.h>\n" + _prototypes(src) + f'#line 1 "{name}.ino"\n' + src,
                       encoding="utf-8")
        obj = work / "sketch.o"
        rc = self._run([str(self.bin / "avr-g++"), *self.FLAGS, *self.CXX, *self._inc(), "-c", str(cpp),
                        "-o", str(obj)], log)
        if rc:
            raise BuildError("the sketch did not compile", _clean_log("\n".join(log), work, name))
        elf_path = out / f"{name}.ino.elf"
        if self._run([str(self.bin / "avr-gcc"), "-mmcu=atmega328p", "-Os", "-Wl,--gc-sections", "-o",
                      str(elf_path), str(obj), *map(str, core), "-lm"], log):
            raise BuildError("the sketch did not link", _clean_log("\n".join(log), work, name))
        self._run([str(self.bin / "avr-objcopy"), "-O", "ihex", "-R", ".eeprom", str(elf_path),
                   str(out / f"{name}.ino.hex")], log)
        size = subprocess.run([str(self.bin / "avr-size"), "-A", str(elf_path)], capture_output=True,
                              text=True, timeout=30).stdout
        text = sum(int(m.group(2)) for m in re.finditer(r"^\.(text|data)\s+(\d+)", size, re.M))
        ram = sum(int(m.group(2)) for m in re.finditer(r"^\.(data|bss|noinit)\s+(\d+)", size, re.M))
        log.append(f"Sketch uses {text} bytes of program storage space.\n"
                   f"Global variables use {ram} bytes of dynamic memory.")
        return _clean_log("\n".join(log), work, name), "avr-gcc (dev build)"


def _prototypes(src: str) -> str:
    """Forward declarations for the sketch's functions, as the Arduino builder adds them."""
    view = _preprocess_view(src)
    protos = []
    for m in re.finditer(r"^[ \t]*((?:static\s+|inline\s+)*[A-Za-z_][\w:<>]*[\s*&]+)([A-Za-z_]\w*)\s*\(([^;{}()]*)\)\s*\{",
                         view, re.M):
        ret, fname, params = m.group(1).strip(), m.group(2), m.group(3).strip()
        if fname in ("if", "for", "while", "switch", "setup", "loop") or ret in ("return", "else"):
            continue
        if "=" in params:
            continue
        protos.append(f"{ret} {fname}({params});")
    return "\n".join(protos) + ("\n" if protos else "")
