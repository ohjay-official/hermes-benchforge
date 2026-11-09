"""Tool schemas: what the model reads to decide when and how to use BenchForge."""

BENCH_GUIDE = {
    "name": "bench_guide",
    "description": (
        "The BenchForge bench format, the parts it can simulate and an example. Call this before writing "
        "your first bench for bench_test."
    ),
    "parameters": {"type": "object", "properties": {}},
}

BENCH_TEST = {
    "name": "bench_test",
    "description": (
        "Test Arduino Uno firmware before it goes near real hardware. Compiles the sketch with arduino-cli, runs "
        "it instruction by instruction on a simulated ATmega328P with simulated LEDs, relays, buzzers, servos, "
        "buttons, potentiometers, LM35/NTC temperature sensors and HC-SR04 distance sensors, and judges it "
        "against the bench's checks, then crash-tests it (noisy sensors, button bounce, millis() rollover, a "
        "sensor wire cut mid-run).\n"
        "Use it whenever you write or change Arduino Uno code for someone, before you tell them it works and "
        "before any upload. Call bench_guide first for the bench format. If it fails, read 'fix', change only "
        "the sketch and call again with the same bench; never weaken the checks to make them pass. A person "
        "must approve the checks themselves (they send /bench approve <id>) before BenchForge lets the "
        "firmware be flashed; tell them the plain-English checks and the approve command it returns."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "bench": {"type": "object", "description": "The bench (see bench_guide): parts, steps, always, unplugged."},
            "sketch": {"type": "string", "description": "The complete Arduino .ino sketch."},
            "crash_tests": {"type": "boolean", "description": "Run the crash tests too (default true)."},
        },
        "required": ["bench", "sketch"],
    },
}
