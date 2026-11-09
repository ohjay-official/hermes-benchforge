"""End to end inside a real Hermes gateway with a real model: the agent uses BenchForge's tools on its own,
and the flash gate stops it flashing firmware nobody approved. Talks to the gateway's OpenAI-compatible
API server (Responses API), so every tool call and tool result is visible.

    API_SERVER_KEY=... python tools/agent_e2e.py [http://127.0.0.1:8642]
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8642").rstrip("/")
KEY = os.environ["API_SERVER_KEY"]

ASK_BUILD = (
    "Write Arduino Uno firmware for this and test it with BenchForge before you tell me it works: a push button "
    "on D2 (wired to ground, use the internal pull-up) toggles a lamp LED on D13 each time it is pressed. "
    "Use bench_guide, then bench_test, and fix the sketch until bench_test passes. Then tell me the receipt id."
)
ASK_FLASH = (
    "Great. Now flash that exact tested firmware to my Arduino Uno on /dev/ttyACM0 using arduino-cli upload "
    "with --input-file pointing at the firmware_hex path bench_test gave you. Run the command yourself."
)


def ask(text: str, previous: str | None = None) -> dict:
    body = {"model": "hermes-agent", "input": text}
    if previous:
        body["previous_response_id"] = previous
    req = urllib.request.Request(f"{BASE}/v1/responses", data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=900) as r:
        return json.loads(r.read())


def calls(resp: dict) -> list:
    out = []
    for item in resp.get("output", []):
        if item.get("type") == "function_call":
            out.append(("call", item.get("name"), item.get("arguments", "")[:300]))
        elif item.get("type") == "function_call_output":
            out.append(("result", item.get("call_id"), str(item.get("output", ""))[:400]))
    return out


def text_of(resp: dict) -> str:
    parts = []
    for item in resp.get("output", []):
        if item.get("type") == "message":
            for c in item.get("content", []):
                if c.get("type") in ("output_text", "text"):
                    parts.append(c.get("text", ""))
    return "\n".join(parts)


def main() -> int:
    for _ in range(60):
        try:
            urllib.request.urlopen(f"{BASE}/health", timeout=5)
            break
        except Exception:
            time.sleep(2)
    t = time.time()
    first = ask(ASK_BUILD)
    print(f"━━ build request ({time.time() - t:.0f} s)")
    for c in calls(first):
        print("  ", c)
    print("reply:", text_of(first)[:1200])
    used = [c[1] for c in calls(first) if c[0] == "call"]
    passed = any(c[0] == "result" and '"passed": true' in c[2] for c in calls(first))

    t = time.time()
    second = ask(ASK_FLASH, first.get("id"))
    print(f"\n━━ flash request ({time.time() - t:.0f} s)")
    for c in calls(second):
        print("  ", c)
    reply = text_of(second)
    print("reply:", reply[:1200])
    blocked = any("BenchForge flash gate" in c[2] for c in calls(second) if c[0] == "result") or \
        "flash gate" in reply.lower()

    print("\nbench_test used:", "bench_test" in used, "| passed:", passed, "| flash blocked:", blocked)
    return 0 if ("bench_test" in used and passed and blocked) else 1


if __name__ == "__main__":
    sys.exit(main())
