# BenchForge for Hermes

**The flight simulator for AI-written Arduino firmware. Proven on the chip before it touches the world.**

You tell Hermes what the board should do. BenchForge turns that into checks you approve in plain English, compiles the sketch with the real Arduino compiler, runs the firmware instruction by instruction on a simulated ATmega328P (the chip on an Arduino Uno) with simulated sensors and relays wired to its pins, then crash-tests it: noisy sensors, a bouncing button, the `millis()` clock wrapping on day 49.7, a sensor wire cut mid-run. When something fails, Hermes gets the one failing check and fixes the sketch, until every check is green. Only firmware with a passing receipt is allowed onto a real board.

> Status: in active development during the Hermes Agent Plugin Hackathon (Nov 2026). The engine, checks, crash tests, receipts and flash gate below work and are tested; the Hermes `/bench` command is being wired now.

## How To Test

You need Python 3.11+ and Node.js 18+. No Arduino board, no hardware.

```bash
git clone https://github.com/ohjay-official/hermes-benchforge && cd hermes-benchforge
python -m benchforge setup     # once: downloads the pinned, checksum-verified arduino-cli and AVR core
python -m benchforge demo      # a greenhouse fan controller: first draft vs fixed
```

What you should see: the first draft passes all 7 of its normal checks and still fails three crash tests: its heartbeat LED goes haywire when `millis()` wraps, and when the temperature sensor is unplugged the fan relay clicks 40 times a second instead of failing safe. The fixed sketch passes all 22 checks across 4 runs.

Test your own sketch against a bench:

```bash
python -m benchforge check examples/greenhouse-fan/bench.json          # the checks in plain English
python -m benchforge run examples/greenhouse-fan/bench.json my.ino     # compile, simulate, judge
python -m benchforge verify <receipt.json> my.ino                       # rebuild, re-run, prove the receipt
```

## What A Bench Looks Like

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
```

Those lines are generated from [`examples/greenhouse-fan/bench.json`](examples/greenhouse-fan/bench.json), never written by a model, so what you approve is exactly what runs.

## Golden Benches

| Example | Parts | What the first draft gets wrong |
|---|---|---|
| [greenhouse-fan](examples/greenhouse-fan) | LM35, relay, LED | heartbeat breaks at `millis()` rollover; relay chatters when the sensor is unplugged |
| [button-toggle](examples/button-toggle) | push button, LED | contact bounce toggles the lamp several times per press |
| [parking-sensor](examples/parking-sensor) | HC-SR04, buzzer | a dead sensor goes silent instead of warning |
| [servo-knob](examples/servo-knob) | potentiometer, servo | a broken knob wire makes the servo thrash |

CI compiles every example with the real `arduino-cli` and `arduino:avr` core on Linux and macOS and checks each draft fails exactly the recorded checks and each fix passes them all.

## License

MIT. The simulator core is [avr8js](https://github.com/wokwi/avr8js) by Uri Shaked (MIT), vendored as readable JavaScript in `engine/vendor/avr8js`.
