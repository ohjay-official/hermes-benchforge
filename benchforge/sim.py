"""Run compiled firmware on the simulated ATmega328P (``engine/``) and return its trace.

The engine is a separate Node.js process started with Node's permission model on: it may read only
its own folder, and it cannot start programs, open threads or write files. The firmware itself runs
inside the emulated chip, instruction by instruction, and can only touch the simulated pins.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from .toolchain import node_version

ENGINE_DIR = Path(__file__).resolve().parent.parent / "engine"
MAX_OUTPUT = 64 * 1024 * 1024


class SimError(RuntimeError):
    pass


def permission_flags(version: tuple) -> list:
    """Node's permission model: read only the engine's folder; no child processes, workers or writes.
    Stable as --permission from Node 22.13 / 23.5; --experimental-permission from Node 20."""
    allow = f"--allow-fs-read={ENGINE_DIR}"
    if version >= (23, 5) or (22, 13) <= version < (23,):
        return ["--permission", allow]
    if version >= (20,):
        return ["--experimental-permission", allow, "--no-warnings"]
    return []


def run(node: str, hex_text: str, parts: list, *, duration_ms: int, stimuli: list | None = None,
        pokes: list | None = None, poke_at_pc: int | None = None, seed: int = 1, watch: list | None = None,
        max_wall_s: float = 120.0) -> dict:
    spec = {"hex": hex_text, "parts": parts, "duration_ms": int(duration_ms), "stimuli": stimuli or [],
            "seed": seed, "watch": watch or [], "max_wall_ms": int(max_wall_s * 1000)}
    if pokes:
        spec["pokes"] = pokes
        spec["poke_at_pc"] = poke_at_pc
    args = [node, *permission_flags(node_version(node))]
    args += ["--max-old-space-size=1024", "--disallow-code-generation-from-strings", str(ENGINE_DIR / "sim.js")]
    env = {"PATH": os.environ.get("PATH", ""), "NODE_OPTIONS": "", "LANG": "C"}
    if "SYSTEMROOT" in os.environ:
        env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    try:
        p = subprocess.run(args, input=json.dumps(spec), capture_output=True, text=True,
                           timeout=max_wall_s + 15, env=env, cwd=str(ENGINE_DIR))
    except subprocess.TimeoutExpired:
        raise SimError(f"the simulation took longer than {max_wall_s:.0f} s and was stopped") from None
    except OSError as exc:
        raise SimError(f"could not start Node.js ({node}): {exc}") from None
    if len(p.stdout) > MAX_OUTPUT:
        raise SimError("the simulation produced too much output")
    try:
        out = json.loads(p.stdout)
    except json.JSONDecodeError:
        tail = (p.stderr or p.stdout or "").strip()[-600:]
        raise SimError(f"the simulator stopped unexpectedly: {tail}") from None
    if not out.get("ok"):
        raise SimError(out.get("error") or "the simulator failed")
    return out
