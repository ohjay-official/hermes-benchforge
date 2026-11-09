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
    def __init__(self, key: str, model: str, wait: int = 420, tries: int = 3, thinking: str | None = "low"):
        self.key, self.model, self.wait, self.tries, self.thinking = key, model, wait, tries, thinking
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
        if self.thinking:
            body["generationConfig"]["thinkingConfig"] = {"thinkingLevel": self.thinking}
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        t0 = time.time()
        for attempt in range(5):
            req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                         headers={"Content-Type": "application/json", "x-goog-api-key": self.key})
            try:
                with urllib.request.urlopen(req, timeout=self.wait) as r:
                    data = json.loads(r.read())
                text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"]
                               if not p.get("thought"))
                print(f"   ({self.model} {purpose}: {time.time() - t0:.0f} s)", flush=True)
                return Result(text)
            except TimeoutError:
                if attempt < self.tries - 1:
                    print(f"   ({purpose}: timed out, retrying)", flush=True)
                    continue
                raise
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 500, 503) and attempt < 4:
                    time.sleep(15 * (attempt + 1))
                    continue
                raise RuntimeError(f"Gemini HTTP {exc.code}: {exc.read()[:300]!r}") from None
        raise RuntimeError("Gemini kept refusing")


PROBE_MODELS = ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.5-flash", "gemini-flash-latest",
                "gemini-3.5-flash-lite", "gemini-flash-lite-latest", "gemini-3-flash-preview"]


def probe(key: str) -> int:
    """Time one real bench-writing call on several models at once, to pick a fast one for the demo."""
    from concurrent.futures import ThreadPoolExecutor

    def one(model):
        llm = Gemini(key, model, wait=200, tries=1)
        t = time.time()
        try:
            bench, _notes = agent.write_bench(llm, REQUESTS[0], attempts=1)
            return f"{model}: {time.time() - t:.0f} s, valid bench with {len(describe(bench))} checks"
        except Exception as exc:
            return f"{model}: {time.time() - t:.0f} s, FAILED ({type(exc).__name__}: {str(exc)[:120]})"

    with ThreadPoolExecutor(len(PROBE_MODELS)) as pool:
        for line in pool.map(one, PROBE_MODELS):
            print(line, flush=True)
    return 0


def main() -> int:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        print("GEMINI_API_KEY is not set")
        return 2
    if len(sys.argv) > 1 and sys.argv[1] == "probe":
        return probe(key)
    llm = Gemini(key, sys.argv[1] if len(sys.argv) > 1 else "gemini-3.5-flash")
    try:
        req = urllib.request.Request("https://generativelanguage.googleapis.com/v1beta/models?pageSize=200",
                                     headers={"x-goog-api-key": key})
        with urllib.request.urlopen(req, timeout=30) as r:
            names = [m["name"].split("/")[-1] for m in json.loads(r.read()).get("models", [])
                     if "generateContent" in m.get("supportedGenerationMethods", [])]
        print("models this key can use:", ", ".join(n for n in names if "flash" in n or "pro" in n))
    except Exception as exc:                      # informational only
        print("could not list models:", exc)
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
