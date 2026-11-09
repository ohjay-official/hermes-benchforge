"""Live model smoke test: the whole /bench loop (write the checks, write the sketch, fix until green)
against a real model, outside Hermes. CI runs it with a Gemini key from the repository secrets.

    GEMINI_API_KEY=... python tools/llm_smoke.py [model]

It stands in for Hermes's ctx.llm with Gemini's REST API and JSON mode, so it exercises exactly the
prompts and parsing BenchForge uses inside Hermes.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from benchforge import agent  # noqa: E402
from benchforge.forge import Workspace  # noqa: E402
from benchforge.spec import describe  # noqa: E402

REQUESTS = [
    "greenhouse fan: LM35 on A0, relay on D7 turns the fan on above 30 °C and off below 27 °C, "
    "LED on D13 blinks once a second as a heartbeat",
    "a push button on D2 toggles a lamp on D13 each time it's pressed",
    "parking sensor: HC-SR04 with trig on D9 and echo on D10; a buzzer on D8 sounds 1 kHz when something is "
    "closer than 30 cm and is quiet beyond 40 cm",
]


class Result:
    def __init__(self, text: str):
        self.text = text
        try:
            self.parsed = json.loads(text)
        except json.JSONDecodeError:
            self.parsed = None


class Gemini:
    def __init__(self, key: str, model: str):
        self.key, self.model = key, model
        self.calls = 0

    def complete_structured(self, *, instructions, input, json_schema=None, json_mode=False, purpose="",
                            max_tokens=None, timeout=None):
        self.calls += 1
        body = {
            "systemInstruction": {"parts": [{"text": instructions}]},
            "contents": [{"role": "user", "parts": [{"text": "".join(b["text"] for b in input)}]}],
            "generationConfig": {"responseMimeType": "application/json", "maxOutputTokens": max_tokens or 8000,
                                 "temperature": 0.2},
        }
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        for attempt in range(5):
            req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                         headers={"Content-Type": "application/json", "x-goog-api-key": self.key})
            try:
                with urllib.request.urlopen(req, timeout=timeout or 240) as r:
                    data = json.loads(r.read())
                text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
                return Result(text)
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 500, 503) and attempt < 4:
                    time.sleep(15 * (attempt + 1))
                    continue
                raise RuntimeError(f"Gemini HTTP {exc.code}: {exc.read()[:300]!r}") from None
        raise RuntimeError("Gemini kept refusing")


def main() -> int:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        print("GEMINI_API_KEY is not set")
        return 2
    llm = Gemini(key, sys.argv[1] if len(sys.argv) > 1 else "gemini-3.8-flash")
    ws = Workspace(Path(os.environ.get("BENCHFORGE_HOME") or tempfile.mkdtemp()))
    ok, missing = ws.ready()
    if not ok:
        print(f"missing {missing}")
        return 2
    green = 0
    for req in REQUESTS:
        print(f"\n━━ {req}")
        t = time.time()
        bench, notes = agent.write_bench(llm, req)
        print("\n".join("   " + line for line in describe(bench)) + (f"\n   notes: {notes}" if notes else ""))
        sketch = agent.write_sketch(llm, req, bench)
        rounds = ws.forge(bench, sketch, agent.fixer(llm, bench),
                          progress=lambda r: print(f"   round {r.n}: " + (
                              "compile error" if r.outcome.compile_error else
                              f"{r.outcome.report.counts()[0]}/{r.outcome.report.counts()[1]}") +
                              (f" · {r.change}" if r.change else "")))
        final = rounds[-1].outcome
        green += final.passed
        print(f"   → {'GREEN' if final.passed else 'NOT GREEN'} after {len(rounds) - 1} fixes, "
              f"{time.time() - t:.0f} s, {llm.calls} model calls so far")
        if not final.passed:
            print("   last failure:\n" + "\n".join("     " + x for x in final.brief().splitlines()))
    print(f"\n{green}/{len(REQUESTS)} requests reached green")
    return 0 if green == len(REQUESTS) else 1


if __name__ == "__main__":
    sys.exit(main())
