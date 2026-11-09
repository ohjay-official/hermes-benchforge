"""BenchForge for Hermes: test Arduino firmware on a simulated chip before it touches a real board.

Registers:
- ``/bench``: describe the board; approve the checks; Hermes writes, tests and fixes the sketch until green
- ``bench_guide`` and ``bench_test``: the same bench for the agent's own coding work
- a ``pre_tool_call`` hook (the flash gate): the agent may only flash firmware with a passing receipt
- ``hermes bench``: setup, demo, run, check and verify from the terminal

The engine and the checks live in ``benchforge/`` and run without Hermes too (``python -m benchforge``).
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

PLUGIN_NAME = "benchforge"
_HERE = Path(__file__).resolve().parent
logger = logging.getLogger(__name__)

HELP = """🧪 BenchForge proves Arduino firmware on a simulated Uno before it touches a real board.

/bench demo
Watch it test and fix a greenhouse fan controller's first draft.

/bench <what the board should do>
For example: /bench greenhouse fan: LM35 on A0, relay on D7 on above 30 °C and off below 27 °C, LED on D13 blinks every second
You approve the checks in plain English. Hermes writes the sketch; BenchForge compiles it, runs it on a simulated ATmega328P, crash-tests it and has Hermes fix it until every check passes.

/bench approve <id>
Approve a saved draft and build it.

/bench status
Is the compiler set up? Latest results.

/bench setup
Install the pinned Arduino compiler (once, about 150 MB)."""


# ---------------------------------------------------------------- shared state

def _data_dir() -> Path:
    from plugins.plugin_storage import plugin_data_dir

    return Path(plugin_data_dir(PLUGIN_NAME))


def _workspace():
    from .benchforge.forge import Workspace

    return Workspace(_data_dir())


def _who(source: Any) -> str:
    """Who approved, without storing a name: the platform and a short one-way hash of the user id, enough
    to tell later whether the same person approved two benches."""
    import hashlib

    if source is None:
        return "local"
    platform = getattr(getattr(source, "platform", None), "value", None) or "chat"
    uid = str(getattr(source, "user_id", "") or "")
    return f"{platform}:{hashlib.sha256(uid.encode()).hexdigest()[:10]}" if uid else str(platform)


def _shown_path(path) -> str:
    """A path for a chat message: the home folder becomes ~ so a group chat never sees the host's user name."""
    text = str(path)
    home = str(Path.home())
    return "~" + text[len(home):] if home not in ("", "/") and text.startswith(home) else text


class _Progress:
    """One chat message that grows line by line as the work goes (edited in place where possible).

    Telegram lets a chat take about one send or edit per second and quietly skips an edit that comes
    sooner, so updates are spaced out: every line that is added is a line the person actually sees."""

    GAP_S = 1.4

    def __init__(self, ctx):
        self.ctx = ctx
        self.handle = None
        self.lines: list = []
        self._last = 0.0

    def add(self, line: str) -> None:
        self.lines.append(line)
        self._push()

    def set_last(self, line: str) -> None:
        if self.lines:
            self.lines[-1] = line
        else:
            self.lines.append(line)
        self._push()

    def _push(self) -> None:
        text = "\n".join(self.lines)
        wait = self.GAP_S - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        try:
            if self.handle is not None and self.handle.ok:
                if self.handle.edit(text).get("ok"):
                    return
            self.handle = self.ctx.post_message(text)
        except Exception:                 # progress is best-effort; the final reply carries the result
            logger.debug("benchforge progress message failed", exc_info=True)

    @property
    def shown(self) -> bool:
        return self.handle is not None and bool(getattr(self.handle, "ok", False))


# ---------------------------------------------------------------- the /bench flow

def _not_ready(ws) -> Optional[str]:
    ok, missing = ws.ready()
    if ok:
        return None
    return (f"BenchForge isn't set up yet: missing {missing}.\n"
            "On the computer running Hermes, run `hermes bench setup` once (it downloads the official, "
            "checksum-pinned arduino-cli and Arduino AVR core, about 150 MB), or send /bench setup.")


def _round_line(r) -> str:
    out = r.outcome
    head = "First sketch" if r.n == 0 else f"Fix {r.n}"
    if out.compile_error:
        first = out.compile_error.strip().splitlines()[0][:140] if out.compile_error.strip() else "compile error"
        return f"{head}: ✗ didn't compile\n     {first}"
    good, total = out.report.counts()
    if out.passed:
        return f"{head}: ✅ {good}/{total} checks pass"
    run, res = out.report.first_failure
    where = "" if run.name == "main" else f"{run.title.split(' (')[0]}: "
    if res is None:
        return f"{head}: ✗ {good}/{total}\n     {where}{run.error}"
    saw = res.saw if len(res.saw) <= 120 else res.saw[:119] + "…"
    return f"{head}: ✗ {good}/{total}\n     ✗ {where}{res.text}\n       saw: {saw}"


def _score(r) -> str:
    o = r.outcome
    label = "first sketch" if r.n == 0 else f"fix {r.n}"
    if o.compile_error:
        return f"{label} ✗ compile"
    good, total = o.report.counts()
    return f"{label} {'✅ ' if o.passed else ''}{good}/{total}"


LINK_LIMIT = {"telegram": 3000, "discord": 1500, "whatsapp": 3000, "signal": 1500, "sms": 0}


def _forge_and_report(ctx, ws, bench, sketch: str, progress: _Progress, *, fixer=None, platform: str = "") -> str:
    from .benchforge.forge import MAX_FIX_ROUNDS
    from .benchforge.report import summary

    rounds_text: list = []
    fixing = "   🔧 Hermes is fixing it…"

    def on_round(r):
        if r.n > 0:
            progress.set_last(f"   🔧 {r.change}" if r.change else "   🔧 changed the sketch")
        line = _round_line(r)
        rounds_text.append(line)
        progress.add(line)
        if not r.outcome.passed and r.n < MAX_FIX_ROUNDS:
            progress.add(fixing)

    if fixer is None:
        from .benchforge import agent

        fixer = agent.fixer(ctx.llm, bench)
    rounds = ws.forge(bench, sketch, fixer, progress=on_round)
    final = rounds[-1].outcome
    if progress.lines and progress.lines[-1] == fixing:
        stop = rounds[-1].change if rounds[-1].n > 0 and rounds[-1].outcome is rounds[-2].outcome else ""
        progress.set_last(f"   ⚠ {stop}" if stop else "   (no further fix)")
    lines = []
    if not progress.shown:
        lines += rounds_text + [""]
    elif len(rounds) > 1:
        lines.append("Rounds: " + " → ".join(_score(r) for r in rounds))
    if final.compile_error:
        lines.append("❌ The sketch still doesn't compile after the fixes:\n" + final.compile_error[:1500])
        return "\n".join(lines)
    lines.append(summary(final.report, final.build,
                         title="✅ All green" if final.passed else f"❌ Not green after {len(rounds) - 1} fixes"))
    lines.append(f"Receipt {final.receipt['id']} (rerun it with `hermes bench verify`)")
    if final.passed:
        lines.append("The flash gate now allows this exact firmware onto a real board:")
        lines.append(f"arduino-cli upload -p <port> --fqbn arduino:avr:uno --input-file {_shown_path(final.hex_path)}")
    try:
        from .benchforge import live

        url = live.link(live.payload(final.bench, final.build, final.report))
        if len(url) <= LINK_LIMIT.get(platform, 30000):
            lines.append(f"▶ Watch it run in your browser: {url}")
        else:
            lines.append("▶ Watch it run: open live.html from the zip, or load the zip on " + live.LIVE_URL)
    except Exception:
        logger.debug("benchforge live link failed", exc_info=True)
    gif = ws.animate(final)
    if gif:
        lines.append(f"MEDIA:{gif}")
        if platform == "telegram":
            # Telegram turns a local image into a still photo (the first frame); a GIF sent as a document
            # arrives as an animation that plays inline.
            lines.append("[[as_document]]")
    bundle = ws.bundle(final)
    if bundle:
        lines.append(f"MEDIA:{bundle}")
    return "\n".join(lines)


def _approve_and_build(ctx, ws, draft: dict, source, progress: _Progress, *, demo_sketch: str = "") -> str:
    from .benchforge import agent
    from .benchforge.spec import load

    bench = load(draft["bench"])
    ws.approve(bench, by=_who(source))
    progress = _Progress(ctx)                        # a fresh message: the approved checks stay as they were
    progress.add(f"✅ {bench.name}: checks approved and locked (bench {bench.sha256[:12]}).")
    sketch = demo_sketch or draft.get("sketch") or ""
    if not sketch:
        progress.add("✍️ Hermes is writing the sketch…")
        sketch = agent.write_sketch(ctx.llm, draft["request"], bench)
        progress.set_last("✍️ Sketch written. Compiling and putting it on the bench…")
    else:
        progress.add("🔌 Compiling and putting the sketch on the bench…")
    platform = getattr(getattr(source, "platform", None), "value", "") or ""
    return _forge_and_report(ctx, ws, bench, sketch, progress, platform=platform)


def _say(ctx, text: str) -> bool:
    """A message of its own (sends wait their turn on Telegram; edits can be skipped). True if it went out."""
    try:
        handle = ctx.post_message(text)
        return bool(getattr(handle, "ok", handle is not None))
    except Exception:
        logger.debug("benchforge post failed", exc_info=True)
        return False


def _ask_approval(ctx, ws, bench, draft_id: str, progress: _Progress) -> tuple:
    """(the person's choice or None, the checks text if it could not be shown and must go in the reply)."""
    from .benchforge.report import approval_text

    text = approval_text(bench)
    shown = _say(ctx, text)
    n = len([1 for _ in bench.checks]) + len(bench.unplugged)
    question = f"Approve these {n} checks? The firmware will be judged by exactly these."
    choice = ctx.ask_choice(question if shown else f"{text}\n\n{question}", [
        {"value": "approve", "label": "✅ Approve and build"},
        {"value": "cancel", "label": "✖ Cancel"},
    ])
    return choice, ("" if shown else text + "\n\n")


def _cmd_new(ctx, ws, request: str, source) -> str:
    from .benchforge import agent

    progress = _Progress(ctx)
    progress.add("🧪 Writing the checks…")
    bench, notes = agent.write_bench(ctx.llm, request)
    did = ws.save_draft(bench, request, by=_who(source))
    progress.set_last(f"🧪 Draft {did}" + (f"\n⚠ Not simulated: {notes}" if notes else ""))
    choice, unseen = _ask_approval(ctx, ws, bench, did, progress)
    if choice is None:
        return (f"{unseen}Saved as draft {did}. Send /bench approve {did} to lock these checks and build, "
                "or /bench <new description> to start over.")
    if choice != "approve":
        return "Cancelled. Nothing was built."
    return _approve_and_build(ctx, ws, ws.load_draft(did), source, progress)


def _cmd_approve(ctx, ws, did: str, source) -> str:
    draft = ws.load_draft(did)
    if draft is None:
        return f"No draft called {did!r}. Start one with /bench <what the board should do>."
    return _approve_and_build(ctx, ws, draft, source, _Progress(ctx))


def _cmd_demo(ctx, ws, source) -> str:
    from .benchforge.spec import load

    ex = _HERE / "examples" / "greenhouse-fan"
    bench = load((ex / "bench.json").read_text(encoding="utf-8"))
    draft_sketch = (ex / "draft.ino").read_text(encoding="utf-8")
    request = bench.goal
    did = ws.save_draft(bench, request, sketch=draft_sketch, by=_who(source))
    progress = _Progress(ctx)
    progress.add("🧪 Demo: a greenhouse fan controller, starting from a typical first-draft sketch.")
    choice, unseen = _ask_approval(ctx, ws, bench, did, progress)
    if choice is None:
        return f"{unseen}Send /bench approve {did} to lock these checks and run the demo."
    if choice != "approve":
        return "Cancelled."
    return _approve_and_build(ctx, ws, ws.load_draft(did), source, progress, demo_sketch=draft_sketch)


def _cmd_status(ws) -> str:
    ok, missing = ws.ready()
    lines = ["🧪 BenchForge " + ("is ready." if ok else f"needs: {missing}. Run `hermes bench setup`.")]
    st = ws.toolchain.state()
    if st:
        lines.append(f"Compiler: arduino-cli {st.get('cli')} · {st.get('core')} · Node {st.get('node') or '?'}")
    receipts = sorted(ws.receipts.glob("*.receipt.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:5]
    if receipts:
        lines.append("Latest results:")
        for p in receipts:
            try:
                r = json.loads(p.read_text(encoding="utf-8"))
                lines.append(f"  {'✅' if r['verdict'] == 'pass' else '❌'} {r['bench']['name']} · "
                             f"firmware {r['firmware']['sha256'][:12]} · {r['created_at']}")
            except (OSError, ValueError, KeyError):
                continue
    return "\n".join(lines)


def _cmd_setup(ctx) -> str:
    ws = _workspace()
    progress = _Progress(ctx)
    progress.add("🔧 Installing BenchForge's compiler (official arduino-cli, checksum-pinned)…")
    ws.toolchain.install(log=lambda m: progress.add("· " + m))
    return "✅ BenchForge is ready. Try /bench demo"


def _run(ctx, raw_args: str, source) -> str:
    from .benchforge.agent import DraftError
    from .benchforge.build import BuildError
    from .benchforge.sim import SimError
    from .benchforge.spec import BenchError
    from .benchforge.toolchain import ToolchainError

    text = (raw_args or "").strip()
    word, _, rest = text.partition(" ")
    word = word.lower()
    try:
        if word in ("", "help", "-h", "--help"):
            return HELP
        if word == "setup":
            return _cmd_setup(ctx)
        ws = _workspace()
        if word == "status":
            return _cmd_status(ws)
        blocked = _not_ready(ws)
        if blocked:
            return blocked
        if word == "demo":
            return _cmd_demo(ctx, ws, source)
        if word == "approve":
            return _cmd_approve(ctx, ws, rest.strip(), source)
        return _cmd_new(ctx, ws, text, source)
    except (DraftError, BenchError, BuildError, SimError, ToolchainError) as exc:
        return f"BenchForge stopped: {exc}"
    except Exception as exc:                          # a slash command must answer, not crash the turn
        logger.exception("/bench failed")
        return f"BenchForge hit an unexpected error: {type(exc).__name__}: {exc}"


# ---------------------------------------------------------------- tools for the agent

def _guide(_args: dict, **_kw) -> str:
    from .benchforge.prompts import BENCH_FORMAT
    from .benchforge.build import ALLOWED_INCLUDES

    example = json.loads((_HERE / "examples" / "greenhouse-fan" / "bench.json").read_text(encoding="utf-8"))
    return json.dumps({"format": BENCH_FORMAT, "allowed_includes": sorted(ALLOWED_INCLUDES), "example": example},
                      ensure_ascii=False)


def _test(args: dict, **_kw) -> str:
    from .benchforge.build import BuildError
    from .benchforge.report import run_lines
    from .benchforge.sim import SimError
    from .benchforge.spec import BenchError, describe, load
    from .benchforge.toolchain import ToolchainError

    try:
        ws = _workspace()
        blocked = _not_ready(ws)
        if blocked:
            return json.dumps({"error": blocked})
        try:
            bench = load((args or {}).get("bench"))
        except BenchError as exc:
            return json.dumps({"error": f"bench is not valid: {exc}. See bench_guide."})
        sketch = str((args or {}).get("sketch") or "")
        out = ws.test(bench, sketch, crash=(args or {}).get("crash_tests", True) is not False)
        result: dict = {"checks": describe(bench), "bench_sha256": bench.sha256,
                        "approved": ws.is_approved(bench.sha256)}
        if out.compile_error:
            result.update(passed=False, compiled=False, fix=out.brief())
            return json.dumps(result, ensure_ascii=False)
        good, total = out.report.counts()
        result.update(passed=out.passed, compiled=True, score=f"{good}/{total}", runs=run_lines(out.report),
                      receipt_id=out.receipt["id"], firmware_sha256=out.build.hex_sha256,
                      flash_bytes=out.build.flash_bytes, ram_bytes=out.build.ram_bytes)
        if not out.passed:
            result["fix"] = out.brief()
        else:
            result["firmware_hex"] = str(out.hex_path)
            try:
                from .benchforge import live

                result["watch_it_run"] = live.link(live.payload(out.bench, out.build, out.report))
            except Exception:
                logger.debug("benchforge live link failed", exc_info=True)
            if not result["approved"]:
                did = ws.save_draft(bench, bench.goal or bench.name, sketch=sketch)
                result["approval_needed"] = (
                    f"Passed, but no person has approved these checks yet. Show them the checks above and ask "
                    f"them to send: /bench approve {did}  (until then the flash gate blocks uploading it)")
        return json.dumps(result, ensure_ascii=False)
    except (BuildError, SimError, ToolchainError) as exc:
        return json.dumps({"error": str(exc)})
    except Exception as exc:                          # tools return errors, never raise
        logger.exception("bench_test failed")
        return json.dumps({"error": f"{type(exc).__name__}: {exc}"})


# ---------------------------------------------------------------- the flash gate

def _gate(tool_name: str = "", args: Optional[dict] = None, task_id: str = "", **_kw):
    from .benchforge import gate

    if tool_name not in gate.TERMINAL_TOOLS:
        return None
    command = str((args or {}).get("command") or "")
    if not gate.FLASH_WORDS.search(command):
        return None
    try:
        return gate.decide(tool_name, args or {}, _data_dir())
    except Exception as exc:                          # a flash command we can't judge stays blocked
        logger.exception("benchforge flash gate failed")
        return {"action": "block", "message": f"🛑 BenchForge flash gate could not check this upload: {exc}"}


# ---------------------------------------------------------------- hermes bench (terminal)

def _cli_setup(parser) -> None:
    parser.add_argument("argv", nargs="*", help="setup | demo | check BENCH | run BENCH SKETCH | verify RECEIPT SKETCH")


def _cli_run(args) -> int:
    from .benchforge.__main__ import main

    os.environ["BENCHFORGE_HOME"] = str(_data_dir())
    argv = list(getattr(args, "argv", None) or ["--help"])
    return main(argv)


# ---------------------------------------------------------------- registration

def register(ctx) -> None:
    from . import schemas

    def bench_command(raw_args: str = "", source: Any = None, **_kwargs) -> str:
        return _run(ctx, raw_args, source)

    ctx.register_command("bench", bench_command,
                         description="Prove Arduino firmware on a simulated Uno before it touches a real board",
                         args_hint="[demo | <what the board should do> | approve <id> | status | setup]")
    ctx.register_tool(name="bench_guide", toolset="benchforge", schema=schemas.BENCH_GUIDE, handler=_guide,
                      description="BenchForge bench format and parts", emoji="🧪")
    ctx.register_tool(name="bench_test", toolset="benchforge", schema=schemas.BENCH_TEST, handler=_test,
                      description="Test Arduino firmware on a simulated Uno", emoji="🧪")
    ctx.register_hook("pre_tool_call", _gate)
    ctx.register_cli_command("bench", help="BenchForge: test Arduino firmware on a simulated Uno",
                             setup_fn=_cli_setup, handler_fn=_cli_run,
                             description="setup, demo, check, run and verify BenchForge benches")
