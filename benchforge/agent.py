"""The three model jobs (write the bench, write the sketch, fix one failure) over Hermes's ``ctx.llm``.

Each takes ``llm``: anything with ``complete_structured(instructions=, input=, json_schema=, purpose=,
max_tokens=, timeout=)`` returning an object with ``.parsed`` and ``.text``, which is what Hermes gives a
plugin. Tests pass a stand-in.
"""

from __future__ import annotations

import json

from . import prompts
from .spec import BenchError, describe, load, wiring


class DraftError(RuntimeError):
    pass


def _ask(llm, instructions: str, text: str, schema: dict, purpose: str, max_tokens: int = 16000):
    result = llm.complete_structured(instructions=instructions, input=[{"type": "text", "text": text}],
                                     json_schema=schema, purpose=purpose, max_tokens=max_tokens, timeout=240)
    parsed = getattr(result, "parsed", None)
    if not isinstance(parsed, dict):
        raw = getattr(result, "text", "") or ""
        try:
            parsed = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
        except (ValueError, json.JSONDecodeError):
            raise DraftError("the model did not answer in the expected format") from None
    return parsed


def write_bench(llm, request: str, attempts: int = 3):
    """The person's words -> (a valid Bench, notes on anything left out). Validation errors go back to the
    model to correct."""
    text = request.strip()
    last = ""
    for _ in range(attempts):
        prompt = text if not last else (text + "\n\nYour previous bench was rejected: " + last +
                                          "\nReturn a corrected bench.")
        parsed = _ask(llm, prompts.BENCH_WRITER, prompt, prompts.BENCH_SCHEMA, "benchforge.bench", 12000)
        raw = parsed.get("bench", parsed)
        notes = parsed.get("notes") if isinstance(parsed.get("notes"), str) else ""
        try:
            return load(raw), notes.strip()[:300]
        except BenchError as exc:
            last = str(exc)
    raise DraftError(f"couldn't draft a valid bench: {last}")


def write_sketch(llm, request: str, bench) -> str:
    parsed = _ask(llm, prompts.CODER, prompts.coder_input(request, wiring(bench), describe(bench)),
                  prompts.SKETCH_SCHEMA, "benchforge.sketch")
    sketch = parsed.get("sketch")
    if not isinstance(sketch, str) or "setup" not in sketch:
        raise DraftError("the model returned no sketch")
    return prompts.strip_fences(sketch)


def fixer(llm, bench):
    """A fix function for ``Workspace.forge``: (sketch, brief) -> (new sketch, what changed)."""
    def fix(sketch: str, brief: str):
        parsed = _ask(llm, prompts.FIXER, prompts.fixer_input(sketch, wiring(bench), describe(bench), brief),
                      prompts.FIX_SCHEMA, "benchforge.fix")
        new = parsed.get("sketch")
        if not isinstance(new, str) or not new.strip():
            return "", ""
        change = str(parsed.get("change") or "").strip().replace("\n", " ")[:240]
        return prompts.strip_fences(new), change
    return fix
