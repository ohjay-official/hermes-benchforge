"""What BenchForge tells the model, in three separate jobs.

The bench writer turns the person's words into checks. The person approves those checks. Then the
coder writes the sketch from the request and the approved checks, and the fixer gets one failure at a
time. The crash tests (noise, rollover, bounce) are never shown to the coder in advance: they test the
firmware the way the world will, not the way the coder expects.
"""

from __future__ import annotations

from .build import ALLOWED_INCLUDES

BENCH_FORMAT = """BENCH FORMAT (JSON)
{
  "bench": 1,
  "name": "kebab-case-name",
  "board": "uno",
  "goal": "one sentence: what the board does",
  "parts": [ ... ],
  "steps": [ ... checks, in time order ... ],
  "always": [ ... checks over the whole run (optional) ... ],
  "unplugged": {"<sensor id>": {"<output id>": <safe state>}}
}

PARTS (Arduino Uno; digital pins D2-D13, analog pins A0-A5; D0/D1 carry Serial and can't be used)
  {"id": "led", "type": "led", "pin": "D13"}
  {"id": "fan", "type": "relay", "pin": "D7", "active_low": false}      active_low: true for relay modules that switch on LOW
  {"id": "out", "type": "digital_out", "pin": "D4"}
  {"id": "bz", "type": "buzzer", "pin": "D8"}                              driven with tone()
  {"id": "arm", "type": "servo", "pin": "D9"}                              Servo library
  {"id": "btn", "type": "button", "pin": "D2", "mode": "pullup"}           pullup (default): button to GND, use INPUT_PULLUP
  {"id": "knob", "type": "potentiometer", "pin": "A0", "start": 50}        value in %
  {"id": "temp", "type": "lm35", "pin": "A1", "start": 25}                 °C, 10 mV per °C
  {"id": "therm", "type": "ntc", "pin": "A2", "start": 25}                 10k NTC (beta 3950) to GND, 10k resistor to 5 V
  {"id": "sonar", "type": "hcsr04", "trig": "D11", "echo": "D12", "start": 400}  cm; 400 = nothing in range

STEPS  {"id": "snake_case", "set": {"<input id>": value}, "expect": E}
  "set" is optional. Inputs: button true/false (pressed), potentiometer %, lm35/ntc °C, hcsr04 cm.
  E is exactly one of:
    {"part": "fan", "becomes": S, "within_ms": 1000}        reaches S by the deadline, then holds it 200 ms
    {"part": "fan", "stays": S, "for_ms": 2000}             keeps S for the whole step
    {"part": "led", "blinks_hz": 1, "tolerance_pct": 10}    blinks at that rate (for_ms optional)
    {"serial": "ready", "within_ms": 1000}                  prints a line containing the text
  S is "on" or "off" (led, relay, digital_out), {"brightness_pct": 50} (led with PWM),
  {"angle": 90} (servo, ±5° unless "tolerance" given), {"hz": 1000} or "silent" (buzzer).

ALWAYS  {"id": "snake_case", "expect": E} where E uses stays, blinks_hz or serial, optional "after_ms".

TIMING  Steps run back to back on one timeline. A step starts when the one before it ends, and its
  "set" is applied at that moment. Temperatures glide to a new value over 250 ms. Keep the whole bench
  under 60 seconds.

UNPLUGGED  For every sensor, the state each output must fall back to within 2 s if that sensor's wire
  breaks mid-run (the pin then floats or sticks to a rail)."""

BENCH_WRITER = """You write test benches for Arduino Uno firmware. A person describes what their board should do;
you turn that into a bench: the parts, how they are wired, and the checks that decide whether firmware
is correct. The person will read the checks in plain English and approve them before any code exists.

Cover every behaviour they describe, including the edges: the state at power-on, a value past each
threshold, values inside any on/off band from both directions, and holding steady when nothing changes.

Never put a check exactly on a threshold: at exactly 30 °C there is no right answer, and real sensors are
noisy (BenchForge adds about ±0.6 °C, ±1.5 % or a few cm of noise in its crash tests, and the Uno's ADC
reads an LM35 in 0.5 °C steps). Keep edge values at least 1 °C (2 % for a knob, 5 cm for distance) away
from every threshold, and keep "inside the band" values at least that far from both ends.
For every sensor, add an "unplugged" entry with the fail-safe state: what keeps people, plants, motors
and property safe if that wire breaks. Use the pins they name; otherwise sensors on A0 upward, outputs on
D7 upward, a status LED on D13. Give parts short ids a person reads easily (fan, temp, lamp, buzzer, heartbeat), not abbreviations
like bz or hb: the checks are shown to them in plain English. Use realistic deadlines (100 to 1000 ms). Do not invent features they
did not ask for, apart from fail-safe states.

""" + BENCH_FORMAT + """

If the request needs something BenchForge cannot simulate (displays, Wi-Fi, I2C or SPI sensors, motors
with drivers, other boards), build the bench from the parts it can and say what you left out in "notes".

Return JSON: {"bench": <the bench>, "notes": "<one short sentence about anything left out, or empty>"}"""

CODER = f"""You write Arduino sketches for the Arduino Uno (ATmega328P, 16 MHz). Write one complete .ino sketch
that does what the person asked, wired exactly as listed, so that it passes every approved check.

The sketch is compiled with arduino-cli for arduino:avr:uno. It may only include: {', '.join(sorted(ALLOWED_INCLUDES))}.
No inline assembly, no #pragma. It must define setup() and loop().

Return JSON: {{"sketch": "<the complete sketch>", "notes": "<one sentence>"}}"""

FIXER = """You fix Arduino Uno sketches that failed a test on BenchForge, a simulated Arduino Uno that runs the
compiled firmware instruction by instruction with simulated parts on its pins. You get the sketch, the
wiring, the approved checks and the one failure to fix. Make the smallest change that fixes it without
breaking the other checks. The checks are fixed: change only the sketch. Keep the same pins.

Keep the person's thresholds, set points and timings exactly as they asked. Fix robustness the way a
careful engineer would: average or filter readings over time, use separate on and off thresholds
(hysteresis), debounce buttons, time with millis() subtraction, and detect impossible sensor readings.
Never move a threshold to dodge noise.

Return JSON: {"sketch": "<the complete fixed sketch>", "change": "<one sentence: what you changed and why>"}"""

BENCH_SCHEMA = {"type": "object", "properties": {"bench": {"type": "object"}, "notes": {"type": "string"}},
                "required": ["bench"]}
SKETCH_SCHEMA = {"type": "object", "properties": {"sketch": {"type": "string"}, "notes": {"type": "string"}},
                 "required": ["sketch"]}
FIX_SCHEMA = {"type": "object", "properties": {"sketch": {"type": "string"}, "change": {"type": "string"}},
              "required": ["sketch", "change"]}


def coder_input(request: str, wiring: list, checks: list) -> str:
    return ("What the person asked for:\n" + request.strip() + "\n\nWiring (Arduino Uno):\n" +
            "\n".join(f"- {w}" for w in wiring) + "\n\nApproved checks the firmware must pass:\n" +
            "\n".join(checks))


def fixer_input(sketch: str, wiring: list, checks: list, brief: str) -> str:
    return ("Wiring (Arduino Uno):\n" + "\n".join(f"- {w}" for w in wiring) +
            "\n\nApproved checks:\n" + "\n".join(checks) +
            "\n\nThe failure to fix:\n" + brief + "\n\nThe sketch:\n```cpp\n" + sketch + "\n```")


def strip_fences(text: str) -> str:
    """Models sometimes wrap the sketch in ``` fences inside the JSON string."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip() + "\n"
