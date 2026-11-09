"""The live board: the same simulated Uno, running in a web browser, that you can poke.

``payload`` packs one tested firmware with its bench and results. ``link`` puts that payload in the
fragment of a URL to the hosted page (the part after ``#`` never leaves the browser, so nothing is
uploaded anywhere). ``standalone_html`` writes one self-contained HTML file with the engine inlined,
for the download bundle: it opens offline, by double-click.
"""

from __future__ import annotations

import base64
import json
import zlib
from pathlib import Path

from .bench import _stimuli
from .spec import describe, wiring

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / "engine"
WEB = ROOT / "web"
LIVE_URL = "https://ohjay-official.github.io/hermes-benchforge/"


def payload(bench, build, report) -> dict:
    main = report.main
    by_id = {r.id: r for r in main.results}
    steps = []
    for c in bench.steps:
        r = by_id.get(c.id)
        steps.append([c.id, c.text, c.window.start_ms, c.window.end_ms, None if r is None else r.passed,
                      "" if r is None or r.passed else r.saw])
    always = [[c.id, c.text, by_id[c.id].passed if c.id in by_id else None] for c in bench.checks if c.always]
    good, total = report.counts()
    return {
        "v": 1,
        "name": bench.name,
        "goal": bench.goal,
        "parts": bench.engine_parts(),
        "wiring": wiring(bench),
        "checks": describe(bench),
        "stimuli": _stimuli(bench),
        "duration_ms": bench.duration_ms,
        "steps": steps,
        "always": always,
        "runs": [[run.title, run.passed, bool(run.skipped)] for run in report.runs],
        "verdict": "pass" if report.passed else "fail",
        "score": f"{good}/{total}",
        "firmware": build.hex_sha256,
        "bin": base64.b64encode(_hex_to_bin(build.hex)).decode("ascii"),
    }


def _hex_to_bin(hex_text: str) -> bytes:
    """Intel HEX to a flat flash image (the page loads this straight into the simulated chip)."""
    image = bytearray()
    base = 0
    for line in hex_text.split():
        if not line.startswith(":"):
            continue
        n, addr, kind = int(line[1:3], 16), int(line[3:7], 16), int(line[7:9], 16)
        data = bytes.fromhex(line[9:9 + 2 * n])
        if kind == 0:
            at = base + addr
            if len(image) < at + n:
                image.extend(b"\xff" * (at + n - len(image)))
            image[at:at + n] = data
        elif kind == 2:
            base = int.from_bytes(data, "big") * 16
        elif kind == 4:
            base = int.from_bytes(data, "big") << 16
    return bytes(image)


def pack(p: dict) -> str:
    c = zlib.compressobj(9, zlib.DEFLATED, -15)
    raw = c.compress(json.dumps(p, separators=(",", ":"), ensure_ascii=False).encode("utf-8")) + c.flush()
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def link(p: dict, base: str = LIVE_URL) -> str:
    return f"{base}#f={pack(p)}"


# ---------------------------------------------------------------- the engine, for a browser

def engine_bundle() -> str:
    """engine/bench.js and the vendored avr8js as one browser script (a tiny CommonJS loader)."""
    files = sorted(ENGINE.rglob("*.js"))
    mods = []
    for f in files:
        key = f.relative_to(ENGINE).with_suffix("").as_posix()
        if key in ("sim",):
            continue
        src = f.read_text(encoding="utf-8")
        mods.append(f"{json.dumps(key)}: function (module, exports, require) {{\n{src}\n}}")
    return (
        "(function () {\n'use strict';\n"
        "var defs = {\n" + ",\n".join(mods) + "\n};\n"
        "var cache = {};\n"
        "function resolve(from, name) {\n"
        "  var parts = from.split('/').slice(0, -1);\n"
        "  name.split('/').forEach(function (p) { if (p === '..') parts.pop(); else if (p !== '.') parts.push(p); });\n"
        "  var key = parts.join('/');\n"
        "  if (defs[key]) return key;\n"
        "  if (defs[key + '/index']) return key + '/index';\n"
        "  throw new Error('module not found: ' + name + ' from ' + from);\n"
        "}\n"
        "function load(key) {\n"
        "  if (cache[key]) return cache[key].exports;\n"
        "  var module = { exports: {} };\n"
        "  cache[key] = module;\n"
        "  defs[key](module, module.exports, function (name) { return load(resolve(key, name)); });\n"
        "  return module.exports;\n"
        "}\n"
        "window.BenchForgeEngine = load('bench');\n"
        "})();\n"
    )


def standalone_html(p: dict) -> str:
    """One HTML file: the live page with the engine and this firmware inlined. Works offline."""
    page = (WEB / "index.html").read_text(encoding="utf-8")
    css = (WEB / "style.css").read_text(encoding="utf-8")
    app = (WEB / "app.js").read_text(encoding="utf-8")
    data = json.dumps(p, separators=(",", ":")).replace("</", "<\\/")
    page = page.replace('<link rel="stylesheet" href="style.css">', f"<style>\n{css}\n</style>")
    page = page.replace('<script src="engine.js"></script>', f"<script>\n{engine_bundle()}\n</script>")
    page = page.replace('<script src="app.js"></script>',
                        f"<script>window.BENCHFORGE_PAYLOAD = {data};</script>\n<script>\n{app}\n</script>")
    return page


__all__ = ["payload", "pack", "link", "engine_bundle", "standalone_html", "LIVE_URL"]
