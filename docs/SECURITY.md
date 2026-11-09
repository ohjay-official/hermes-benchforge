# BenchForge Security And Privacy

BenchForge compiles and runs code a model wrote, on the computer that runs Hermes, and decides whether that code may be put on a real board. This page says what could go wrong, what stops it, and what is left. Every control named here has a test; the test file is in brackets.

## What BenchForge Touches

| | What | When |
|---|---|---|
| **Network** | `downloads.arduino.cc` and `github.com` (the official arduino-cli binary, then the `arduino:avr@1.8.6` core and `Servo@1.2.2` library through arduino-cli's own package index) | Only during `hermes bench setup`. Never at run time. |
| **Files written** | `<HERMES_HOME>/plugin-data/benchforge/` only: the toolchain, receipts, firmware, sketches, drafts, approvals, download bundles | Every run |
| **Files read** | its own plugin folder and its data folder; for the flash gate, the `.hex` file an upload command names | Every run |
| **Programs started** | `arduino-cli` (compile only, never upload), `node` (the chip simulator), `arch` (macOS setup check) | Every run |
| **Model calls** | through Hermes's `ctx.llm` with the user's own provider; no keys of its own | `/bench` only |
| **Credentials** | none stored, none read | Never |
| **Telemetry** | none | Never |
| **Background processes** | none; every child process ends with the command that started it | Never |

## What Is Sent To The Model

Through Hermes's own provider, and only for `/bench`: the person's description, the bench (parts, pins, checks), the sketch, and for each fix the one failed check with what happened, up to 8 lines of the firmware's Serial output (clipped to 60 printable characters each) and the compiler's messages with local paths replaced (`~` for the home folder, `<build>` for the build folder).

## What Is Stored

All on the machine running Hermes, in BenchForge's data folder:

- **Receipts** (`receipts/`): the bench, SHA-256 hashes of the sketch and firmware, compiler and simulator versions, and every check's result. No names, no paths, no chat ids. The newest 200 are kept, with their firmware and sketch.
- **Approvals** (`approved/`): the bench's hash, its plain-English checks, the time, and who approved it as the chat platform plus a one-way hash of the user id (for example `telegram:3f9a1c0d2e`). No names.
- **Drafts** (`drafts/`): an unapproved bench with the request that produced it. Deleted after 7 days.
- **Bundles** (`bundles/`): the zip sent to the chat (sketch, firmware, receipt, how to flash). The newest 50 are kept.

Delete the folder to remove everything. Chat replies show the home folder as `~`, so a group chat never sees the host's user name.

## The Five Audits

### 1. A Sketch Reading Files At Compile Time

**Risk.** A C++ sketch can ask the compiler to paste in any file it can read: `#include "/home/me/.ssh/id_rsa"`, or inline assembly with `.incbin`. The contents would end up in compiler errors sent to the model, or in firmware whose Serial output prints them.

**Controls.**
- **Read it the way cpp does.** Before compiling, BenchForge joins continued lines and strips comments, keeping string literals intact, just as the C preprocessor will.
- **Allowed directives.** It then allows only these directives: `include define undef if ifdef ifndef elif else endif error warning line`.
- **Allowed headers.** Includes must be a literal `<name>` or `"name"` from a fixed list of 19 standard headers: no paths, no macros, no `include_next`, no `#pragma`.
- **Refused outright:** `asm`, `__asm`, `__asm__`, `_Pragma`, `__has_include`, assembler `.incbin`/`.include`, token pasting (`##` and `%:%:`, which could assemble a refused word from pieces), trigraphs, NUL bytes, and sketches over 64 KB.
- **Sealed compile.** The sketch is then compiled alone in a fresh temporary folder (no `sketch.yaml`, no neighbouring files). The environment holds only `PATH`, locale, proxy settings, and its own `HOME` and `TMPDIR` inside BenchForge's folder, so arduino-cli never reads the user's config or environment secrets. There is a 300 s timeout.

**Tests.** `SketchScanner` covers a continued-line include, an include hidden after a comment, digraph includes, macro includes, an asm keyword built by token pasting, and includes inside strings and raw strings that must not be refused [`tests/test_core.py`, `tests/test_engine.py`].

**Left.** A compiler bug triggered by crafted source. The compile is a normal user process.

### 2. Firmware Or A Bench Escaping The Simulator

**Risk.** The firmware is untrusted machine code. So is the bench JSON a model writes.

**Controls.**
- **Emulated chip.** The firmware never runs on the host CPU. avr8js interprets it one AVR instruction at a time, and all it can reach is the emulated chip's memory and pins.
- **Boxed-in Node process.** The simulator runs in its own Node.js process under Node's permission model (`--permission --allow-fs-read=<engine folder>`, or `--experimental-permission` on Node 20 to 22.12). It cannot read outside its folder, write files, start programs or threads, or `eval`.
- **Limits.** It has a fixed small environment (no inherited `NODE_OPTIONS`), a 4 MB input cap, a 1 GB heap, a wall-clock limit per run, a 64 MB output cap, and caps on recorded edges (20,000 per pin), Serial text (200 KB) and lines (5,000).
- **Bench validation.** Part ids must match `^[a-z][a-z0-9_]{0,31}$` (no `__proto__`). Every field is checked against a closed vocabulary in Python before the engine sees it, and the engine re-checks part types, pins and signals.

**Tests.** `NodeSandbox` checks that file reads, writes, child processes, workers and eval are all denied with the exact flags BenchForge uses [`tests/test_sandbox.py`]. `MisbehavingFirmware` covers a hang in `setup()`, a watchdog reset loop and a Serial flood [`tests/test_engine.py`].

**Left.** A V8 or Node permission-model bug. Node's own docs describe the permission model as a seatbelt against mistakes, not a hardened sandbox against an attacker.

### 3. The Model Working Around The Checks

**Risk.** A model asked to make tests pass may weaken the tests, or be steered by text the firmware prints.

**Controls.**
- **Checks fixed before code exists.** The checks are written first, shown to a person in plain English generated from the JSON (never model-written prose), and locked by SHA-256 when approved.
- **The fixer can't touch them.** The fixer receives the checks read-only and returns only a sketch; the bench it is judged against is the approved one, loaded from disk.
- **Hidden tests.** The crash tests (noise, rollover, bounce) are not shown to the coder in advance.
- **Clipped evidence.** Firmware Serial output that reaches the model is clipped to 8 lines of 60 printable ASCII characters.
- **Approval required to flash.** The `bench_test` tool lets the agent test against any bench, but a pass only counts at the flash gate once a person has approved that exact bench with `/bench approve`.

**Tests.** The end-to-end plugin tests check that a request asks for approval before any sketch is written, that cancelling builds nothing, and that the fix loop sees the failure brief [`tests/test_plugin.py`].

**Left.** A person can approve weak checks. BenchForge shows them plainly and adds fail-safe checks for every sensor, but the approval is theirs.

### 4. Flashing Untested Firmware

**Risk.** The agent flashes a real board with firmware that never passed, or that passed checks nobody approved.

**Controls.**
- **The hook.** A `pre_tool_call` hook reads every terminal command that would write a board's flash: `avrdude -U flash:w:…`, the `-U file.hex` short form, `arduino-cli upload`, `arduino-cli compile --upload/-u`, and PlatformIO upload.
- **What passes.** It lets one through only if each `.hex` it names hashes to a receipt that passed, is untampered (the receipt's id is the hash of its contents), and was made against a bench a person approved.
- **What's blocked.** Sketch-folder uploads (which compile something new) are blocked. So is a flashing tool hidden inside `sh -c` or a script.
- **Fails closed.** A gate error on a flash command blocks it. Non-flashing commands (listing boards, reading flash) pass untouched.

**Tests.** `FlashGate` covers untested, failed, unapproved and tampered firmware, green firmware by absolute and relative path, sketch-folder uploads, hidden commands and the avrdude forms [`tests/test_core.py`, `tests/test_plugin.py`].

**Left.** This is a seatbelt for an agent's mistakes, not a wall against a person. A person can always flash from their own terminal. Code that can write BenchForge's data folder could also plant an approval file.

### 5. Supply Chain And Data

**Risk.** A swapped download, a dependency that changes under us, or data kept longer than needed.

**Controls.**
- **Pinned compiler.** arduino-cli 1.4.1 is pinned by SHA-256 for every platform. It comes from Arduino's server or the GitHub release, and either way is checked before a byte is unpacked.
- **Careful unpacking.** Only the one executable is extracted, by exact name, so archive paths can't land outside its folder.
- **Pinned core and library.** The AVR core and Servo library are pinned by version and verified by arduino-cli against Arduino's index checksums.
- **Vendored simulator.** avr8js is vendored as readable JavaScript compiled from commit `bee6f0a` (MIT), and the receipt records a hash of every engine file.
- **No Python dependencies.**
- **Pinned CI.** GitHub Actions are pinned by commit SHA.
- **Data.** Retention limits as above; approvals hold no names.

**Tests.** CI runs `hermes plugins validate --install-deps` (the catalog's own admission check, including its security scan) and `hermes plugins doctor --ci` on every push.

**Left.** The Arduino package index is trusted (it is the toolchain's own upstream).

**The live board.** A live link carries a firmware in the part of the URL after `#`, which the browser never sends anywhere. The page fetches only its own files: the engine and, for short links, the dictionary the link names by its zlib id (`dicts/<8 hex digits>.txt`, so a link can't point the page at any other path). Its decoder (`web/inflate.js`) checks every length and distance, stops at 4 MB of output, and checks zlib's Adler-32 trailer, so a damaged or hostile link is refused with a message instead of misread; CI tests it against Python's zlib. A link can still carry any firmware someone compiled, and it only ever runs inside the simulated chip on that page.

## Reporting

Open a GitHub issue, or for anything sensitive message [@Oh_Jay0](https://x.com/Oh_Jay0) on X.
