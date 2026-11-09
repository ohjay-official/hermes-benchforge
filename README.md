# BenchForge for Hermes

**The flight simulator for AI-written Arduino firmware. Proven on the chip before it touches the world.**

Ask Hermes for Arduino code and you get something that compiles and looks right. Then it meets a real board. The relay buzzes because the temperature reading jitters. The status LED goes haywire after 49 days because the timer wrapped. One press of the button counts as five. And when the sensor wire works loose, the greenhouse fan switches off and the plants cook. You find these bugs after the wiring is done, with mains power, a motor or a heater on the other end.

BenchForge finds them first. Tell Hermes what the board should do. BenchForge writes the checks, and you approve them in plain English before any code exists. Hermes writes the sketch, and the real Arduino compiler builds it. The firmware then runs instruction by instruction on a simulated ATmega328P (the chip on an Arduino Uno), with simulated sensors, buttons, relays, servos and buzzers wired to its pins. After that come the crash tests. Every failure goes back to Hermes, one at a time, until everything is green. You get the sketch, the firmware and a proof receipt anyone can re-run. And the agent can't flash a real board with anything that hasn't passed.

[How to test](#how-to-test) · [What you see](#what-you-see) · [How it works](#how-it-works) · [Why it's a plugin, not a prompt](#why-its-a-plugin-not-a-prompt) · [The checks](#the-checks) · [What works today](#what-works-today) · [What it touches](#what-it-touches)

## How To Test

No Arduino, no hardware, no wiring. You need Python 3.11+ and Node.js 18+.

**In two commands, without Hermes:**

```bash
git clone https://github.com/ohjay-official/hermes-benchforge && cd hermes-benchforge
python -m benchforge setup    # once: the official arduino-cli and AVR core, SHA-256 pinned (~150 MB)
python -m benchforge demo     # a greenhouse fan controller: first draft vs fixed
```

**In Hermes:**

```bash
hermes plugins install ohjay-official/hermes-benchforge --enable
hermes bench setup
```

Then, in any Hermes chat (CLI, Telegram, Discord, Slack):

```text
/bench demo
/bench greenhouse fan: LM35 on A0, relay on D7 on above 30 °C and off below 27 °C, LED on D13 blinks every second
```

`/bench demo` starts from a typical first-draft sketch so you can watch the fix loop. A `/bench <description>` request starts from nothing.

**Check a sketch of your own:**

```bash
python -m benchforge check examples/greenhouse-fan/bench.json            # the checks in plain English
python -m benchforge run   examples/greenhouse-fan/bench.json my.ino     # compile, simulate, judge
python -m benchforge verify <receipt.json> my.ino                         # rebuild, re-run, prove the receipt
```

Inside Hermes the same commands are `hermes bench check|run|verify`.

## What You See

`python -m benchforge demo` prints the bench, then runs both sketches:

```text
The firmware passes only if:
  1. at power-on → fan stays off for 1.5 s
  2. temp reads 32 °C → fan turns on within 1 s
  3. temp reads 28.5 °C → fan stays on for 2 s
  4. temp reads 26 °C → fan turns off within 1 s
  5. temp reads 29 °C → fan stays off for 2 s
  6. temp reads 30.8 °C → fan turns on within 1 s
  7. The whole time, led blinks at 1 Hz (±10 %)
  8. If temp is unplugged → fan on (fail safe)

━━ First draft (draft.ino) ━━
❌ Not yet: 20/23 checks passed across 4 runs
✓ Your checks: 7/7
✓ Noisy sensor (temp ±0.6 °C): 7/7
✗ Clock rollover (millis() wraps at 3.7 s, as on day 49.7): 6/7
    ✗ the whole time, led blinks at 1 Hz (±10 %)
      saw: led blinked at 333.33 Hz, unevenly (periods from 3 ms to 1 s)
✗ temp unplugged mid-run: 0/2
    ✗ temp unplugged → fan falls back to on within 2 s
      saw: fan flickered between off and on 68 times
    ✗ fan never clicks more than 4 times a second
      saw: fan clicked 40 times within one second, from 2.7 s

━━ After fixes (fixed.ino) ━━
✅ All green: 22/22 checks passed across 4 runs
```

The first draft passes every normal check, and most people would ship it. The crash tests catch two bugs:

- **The heartbeat timer.** It is written as `millis() >= next`, which breaks the moment the clock wraps.
- **The cut sensor wire.** A cut wire makes the reading jump around, and the fan relay clicks 40 times a second instead of failing safe.

In a chat, `/bench` shows the same thing live, as one message that updates itself:

```text
✅ greenhouse-fan: checks approved and locked (bench e228f7e6043a).
🔌 Compiling and putting the sketch on the bench…
First sketch: ✗ 20/23 [Clock rollover…] the whole time, led blinks at 1 Hz: led blinked at 333.33 Hz
   🔧 Switched to millis() subtraction and added a sensor-fault check that turns the fan on.
Fix 1: ✅ 22/22 checks pass
```

The 🔧 line is the model's own summary of its change. The reply ends with a zip holding the sketch, the firmware (`.hex`), the receipt and how to flash it.

## How It Works

```text
 "greenhouse fan…"  →  checks (plain English)  →  you approve  →  locked by SHA-256
                                                                        │
       ┌──────────────── Hermes fixes one failure at a time ◄───────────┤
       ▼                                                                │
   sketch.ino → arduino-cli (sealed) → firmware.hex → simulated ATmega328P + parts
                                                        │
                                   your checks, then the crash tests
                                                        │
                              all green → receipt → the flash gate opens for this exact .hex
```

1. **Checks first.** The bench is JSON in a small fixed vocabulary: parts, pins, steps, expectations. BenchForge turns it into plain-English lines itself (the model never writes the wording you approve), and a model can't slip in anything the vocabulary doesn't have. On Telegram, Discord and Slack you approve with a button; elsewhere with `/bench approve <id>`.
2. **Real compiler.** The sketch is compiled by arduino-cli 1.4.1 for `arduino:avr:uno` (avr-gcc 7.3, LTO), the same build the Arduino IDE makes. It is first scanned and compiled alone in a sealed folder ([why](docs/SECURITY.md#1-a-sketch-reading-files-at-compile-time)).
3. **Real chip, simulated parts.** The firmware runs on [avr8js](https://github.com/wokwi/avr8js), the open-source AVR emulator behind Wokwi, at 16 MHz, one instruction at a time. The timers, interrupts, ADC, UART, EEPROM and watchdog are all emulated. BenchForge adds the parts:
   - an LM35 at 10 mV/°C
   - a 10k NTC thermistor in a divider
   - a potentiometer
   - a push button with pull-up
   - an HC-SR04 that answers trigger pulses with echo pulses 58 µs/cm long
   - LEDs, including PWM brightness
   - relays (active-high or active-low)
   - a buzzer, measured in Hz
   - a servo, read from the pulse width
4. **Deterministic.** There is no wall clock and no `Math.random`. Sensor noise comes from a seeded generator, so the same firmware on the same bench gives the same result every time, which is what makes receipts provable.
5. **Crash tests.** The approved checks run again under conditions real boards meet (below).
6. **The fix loop.** Hermes gets the earliest failure: what was expected, what happened, when, the last Serial lines, and a hint for crash-test failures. It returns a new sketch. This repeats up to 5 times, and stops early if the model repeats itself.
7. **Receipt and flash gate.** A receipt records the bench, the sketch and firmware hashes, and every result. Its id is the hash of its contents. A `pre_tool_call` hook blocks the agent from flashing any `.hex` that lacks a passing, untampered receipt against checks a person approved.

### The Crash Tests

| Test | What happens | The bug it catches |
|---|---|---|
| Noisy sensor | every analog reading jitters (±0.6 °C, ±1.5 %) | no hysteresis or smoothing: relays chatter, servos twitch |
| Clock rollover | `millis()` and `micros()` start just short of their wrap and wrap mid-test | `millis() >= deadline` timing, which breaks after 49.7 days of uptime |
| Bouncy button | every press and release chatters for 5 ms | no debounce: one press counts several times |
| Unplugged sensor | the sensor wire is cut mid-run: the pin floats, or a divider pulls it to a rail | firmware that trusts garbage and drives outputs unsafely |
| Relay chatter (always on) | any relay that clicks more than 4 times in one second | fails in every run, crash test or not |

## Why It's A Plugin, Not A Prompt

A prompt can say "I tested it". BenchForge actually runs the code:

- **The real toolchain.** It downloads and pins arduino-cli and the AVR core, and runs an instruction-level AVR emulator as a sandboxed Node.js process.
- **Five Hermes surfaces, one core.**
  - **`/bench`** uses `ctx.ask_choice` (approval buttons), `ctx.post_message` (a live progress message it edits in place) and `ctx.llm` (three separate model jobs: bench writer, coder, fixer).
  - **`bench_guide` and `bench_test`** are tools, so the agent tests its own Arduino work in ordinary chats.
  - **A `pre_tool_call` hook** is the flash gate.
  - **`hermes bench`** covers the terminal.
  - Everything also runs without Hermes, as `python -m benchforge`.
- **Separation of duties.** The checks are written and approved before the code exists. The coder never sees the crash tests, and the fixer can't change the checks.
- **Proof, not trust.** `hermes bench verify` rebuilds the firmware from the sketch, re-runs the bench and compares every result with the receipt. CI does exactly this on every push.

## The Checks

A bench is JSON. This is the greenhouse fan from [`examples/greenhouse-fan/bench.json`](examples/greenhouse-fan/bench.json), shortened:

```json
{
  "name": "greenhouse-fan",
  "parts": [{"id": "temp", "type": "lm35", "pin": "A0", "start": 25},
            {"id": "fan", "type": "relay", "pin": "D7"},
            {"id": "led", "type": "led", "pin": "D13"}],
  "steps": [{"id": "boots_off", "expect": {"part": "fan", "stays": "off", "for_ms": 1500}},
            {"id": "hot_turns_on", "set": {"temp": 32}, "expect": {"part": "fan", "becomes": "on", "within_ms": 1000}}],
  "always": [{"id": "heartbeat", "expect": {"part": "led", "blinks_hz": 1}}],
  "unplugged": {"temp": {"fan": "on"}}
}
```

- **Steps** run back to back on one timeline. A step's `set` changes its inputs when it starts. Its `expect` is one of:
  - `becomes`: reach a state by a deadline and hold it
  - `stays`: hold a state for the whole step
  - `blinks_hz`
  - `serial`: a line containing some text
- **States** are `on`/`off`, an LED's brightness in %, a servo angle, or a buzzer pitch.
- **`always`** checks hold over the whole run.
- **`unplugged`** gives each sensor's fail-safe state.

The full format, with every part, is what the `bench_guide` tool returns ([`benchforge/prompts.py`](benchforge/prompts.py)).

### Golden Benches

| Example | Parts | What the first draft gets wrong |
|---|---|---|
| [greenhouse-fan](examples/greenhouse-fan) | LM35, relay, LED | the heartbeat breaks when `millis()` wraps; the relay chatters when the sensor is unplugged |
| [button-toggle](examples/button-toggle) | push button, LED | contact bounce toggles the lamp several times per press |
| [parking-sensor](examples/parking-sensor) | HC-SR04, buzzer | a dead sensor goes silent instead of warning |
| [servo-knob](examples/servo-knob) | potentiometer, servo | a broken knob wire makes the servo thrash |

On every push, CI compiles all eight sketches with the real arduino-cli on Linux and macOS. Each draft must fail exactly the checks recorded in its `expect.json`, and each fix must pass them all ([`tools/golden.py`](tools/golden.py)).

## What Works Today

| Area | Status |
|---|---|
| Compile with the real arduino-cli and AVR core, sealed | Works; CI on Linux and macOS (Apple Silicon needs Rosetta, Arduino's avr-gcc is Intel-only) |
| ATmega328P simulation with LEDs, relays, buzzer, servo, button, potentiometer, LM35, NTC, HC-SR04 | Works; 4 golden benches plus misbehaving-firmware tests |
| Crash tests: noise, rollover, bounce, unplugged, relay chatter | Works; every one is caught by a golden draft |
| Receipts and `verify` | Works; CI re-runs a receipt and requires an exact match |
| Flash gate | Works; unit and end-to-end tests |
| `/bench`, `bench_test`, `bench_guide`, `hermes bench` | Work against a stand-in Hermes in tests; `hermes plugins validate` and `doctor` pass in CI |
| Live run on Telegram with a real model | Being filmed for the demo; this row is updated when it's done |
| Windows | Not tested yet |
| Other boards (ESP32, Mega, Nano Every), I2C/SPI parts, displays | Not yet: the Uno and the parts above only |

These are simulated parts, not physics. An LM35 here is a perfect 10 mV/°C, a relay switches instantly, and a cut wire is one of two modelled failures. What BenchForge proves is that the firmware's logic handles the cases on the bench. Real boards add things no simulator has.

## What It Touches

- **Network:** only `hermes bench setup` downloads, from `downloads.arduino.cc` and `github.com`, every file SHA-256 pinned or index-verified. Nothing at run time.
- **Files:** writes only to `<HERMES_HOME>/plugin-data/benchforge/`: the toolchain, receipts, firmware, drafts (deleted after 7 days), approvals and download bundles.
- **Programs:** `arduino-cli` (compile only, never upload) and `node` (the simulator, under Node's permission model: no file writes, no child processes, no reads outside its own folder).
- **Model:** through Hermes's `ctx.llm` with your own provider, for `/bench` only. It sends your description, the bench, the sketch and failure details (Serial lines clipped, local paths removed).
- **Stored about people:** an approval records the chat platform and a one-way hash of the user id. No names.
- **No** credentials, telemetry or background processes.

The threat model and five audits are in [docs/SECURITY.md](docs/SECURITY.md).

## Built During The Hermes Agent Plugin Hackathon

- Built 8 to 11 November 2026 by Oh-Jay ([@Oh_Jay0](https://x.com/Oh_Jay0)), a mechatronics student. The code was written with AI (Claude, by Anthropic), directed and tested by Oh-Jay.
- Simulator core: [avr8js](https://github.com/wokwi/avr8js) by Uri Shaked (MIT), vendored as readable JavaScript in [`engine/vendor/avr8js`](engine/vendor/avr8js) from commit `bee6f0a`. The parts, the bench runner and everything else here is new.
- Requires Hermes Agent `>=0.23.0`. No Python dependencies.

## License

MIT
