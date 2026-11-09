"""The flash gate: the agent can't put firmware on a real board unless that exact firmware passed its bench.

Hermes asks every plugin's ``pre_tool_call`` hook before a tool runs. BenchForge looks at terminal
commands that would flash an AVR board (avrdude, ``arduino-cli upload``, ``arduino-cli compile --upload``,
PlatformIO upload). It lets one through only when it names a ``.hex`` file whose SHA-256 matches a
passing, untampered BenchForge receipt. Everything else is blocked with a message that says how to get
a green receipt.

This is a seatbelt for an agent's honest mistakes, not a wall against a determined person: a person
can always flash from their own terminal, and anything that can write BenchForge's data folder could
forge a marker. The gate checks the receipt's own hash so an edited receipt does not count.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
from pathlib import Path

from .receipt import check_integrity

FLASH_WORDS = re.compile(r"\bavrdude\b|\barduino-cli\b[^\n]*\b(upload|--upload)\b|\barduino-cli\b[^\n]*\s-u\b|"
                         r"\b(pio|platformio)\b[^\n]*\bupload\b", re.I)
TERMINAL_TOOLS = {"terminal"}


def _segments(command: str) -> list:
    try:
        lex = shlex.shlex(command, posix=True, punctuation_chars=";&|")
        lex.whitespace_split = True
        tokens = list(lex)
    except ValueError:
        return []
    segs, cur = [], []
    for t in tokens:
        if t and set(t) <= set(";&|"):
            if cur:
                segs.append(cur)
            cur = []
        else:
            cur.append(t)
    if cur:
        segs.append(cur)
    return segs


def _hex_targets(seg: list) -> tuple:
    """(is a flash command, [hex paths it writes]) for one command segment."""
    words = [os.path.basename(w).lower() for w in seg]
    paths = []
    if "avrdude" in words or "avrdude.exe" in words:
        for i, w in enumerate(seg):
            spec = None
            if w == "-U" and i + 1 < len(seg):
                spec = seg[i + 1]
            elif w.startswith("-U") and len(w) > 2:
                spec = w[2:]
            if spec:
                m = re.match(r"(?:flash:)?w:(.+?)(?::[ihrsmb])?$", spec)
                if m:
                    paths.append(m.group(1))
        return True, paths
    if any(w in ("arduino-cli", "arduino-cli.exe") for w in words):
        if "upload" in seg or "--upload" in seg or "-u" in seg:
            for i, w in enumerate(seg):
                if w in ("--input-file", "-i") and i + 1 < len(seg):
                    paths.append(seg[i + 1])
                elif w.startswith("--input-file="):
                    paths.append(w.split("=", 1)[1])
            if "compile" in seg:
                return True, []           # compile-and-upload builds something nobody benched
            return True, paths
    if any(w in ("pio", "platformio") for w in words) and "upload" in " ".join(seg):
        return True, []
    return False, []


def _passing(sha: str, data_dir: Path):
    marker = data_dir / "passed" / f"{sha}.json"
    try:
        info = json.loads(marker.read_text())
        receipt = json.loads((data_dir / "receipts" / Path(info["receipt"]).name).read_text())
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if check_integrity(receipt) or receipt.get("verdict") != "pass" or receipt["firmware"]["sha256"] != sha:
        return None
    return receipt


def decide(tool_name: str, args: dict, data_dir: Path):
    """None to let the call run; a Hermes block directive otherwise."""
    if tool_name not in TERMINAL_TOOLS or not isinstance(args, dict):
        return None
    command = str(args.get("command") or "")
    if not FLASH_WORDS.search(command):
        return None
    workdir = args.get("workdir")
    segs = _segments(command)
    flashing = [(seg, paths) for seg in segs for is_flash, paths in [_hex_targets(seg)] if is_flash]
    if not flashing:
        # The words are there but not as a plain command (inside sh -c "...", a script, a variable).
        return _block("BenchForge can't tell which firmware this command flashes. Flash a bench-tested "
                      ".hex directly: arduino-cli upload -p <port> --fqbn arduino:avr:uno --input-file <file.hex>")
    for seg, paths in flashing:
        if not paths:
            return _block("This would flash firmware that BenchForge has not tested (it builds or uploads a "
                          "sketch folder). Run /bench on it first; a passing run saves the exact .hex and "
                          "prints the upload command for it.")
        for raw in paths:
            p = Path(os.path.expanduser(raw))
            if not p.is_absolute():
                if not workdir:
                    return _block(f"BenchForge needs the full path of {raw} to check its receipt.")
                p = Path(workdir) / p
            try:
                data = p.read_bytes()
            except OSError:
                return _block(f"BenchForge can't read {raw} to check its receipt.")
            sha = hashlib.sha256(data).hexdigest()
            if _passing(sha, data_dir) is None:
                return _block(f"{p.name} (firmware {sha[:12]}) has no passing BenchForge receipt. "
                              "Run /bench on the sketch; only firmware that passes every check is allowed onto "
                              "a real board.")
    return None


def _block(why: str) -> dict:
    return {"action": "block", "message": f"🛑 BenchForge flash gate: {why}"}


def mark_passing(receipt: dict, receipt_path: Path, data_dir: Path) -> None:
    if receipt.get("verdict") != "pass":
        return
    d = data_dir / "passed"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{receipt['firmware']['sha256']}.json").write_text(
        json.dumps({"receipt": receipt_path.name, "id": receipt["id"]}))
