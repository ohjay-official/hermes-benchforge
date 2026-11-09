"""BenchForge without Hermes:

    python -m benchforge setup                         install the pinned Arduino compiler (once)
    python -m benchforge check  BENCH.json             show the checks in plain English
    python -m benchforge run    BENCH.json SKETCH.ino  compile, simulate, judge; write a receipt
    python -m benchforge verify RECEIPT.json SKETCH.ino  rebuild and re-run; prove the receipt
    python -m benchforge demo                          the greenhouse fan, first draft vs fixed

Data (compiler, receipts) goes in $BENCHFORGE_HOME, default ~/.benchforge.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import report as R
from .forge import Workspace, load_receipt
from .spec import BenchError, load
from .toolchain import ToolchainError

ROOT = Path(__file__).resolve().parent.parent


def _ws() -> Workspace:
    return Workspace(Path(os.environ.get("BENCHFORGE_HOME") or Path.home() / ".benchforge"))


def _need_ready(ws: Workspace) -> None:
    ok, missing = ws.ready()
    if not ok:
        raise ToolchainError(f"missing {missing}; run `python -m benchforge setup` first")


def cmd_setup(_a) -> int:
    ws = _ws()
    ws.toolchain.install(log=lambda m: print("·", m))
    print(f"Ready. BenchForge's compiler lives in {ws.toolchain.root}")
    return 0


def cmd_check(a) -> int:
    bench = load(Path(a.bench).read_text(encoding="utf-8"))
    print(R.approval_text(bench))
    print(f"\nBench hash: {bench.sha256}")
    return 0


def _show(out, *, as_json: bool, animate: bool = True) -> int:
    if as_json:
        print(json.dumps(out.receipt if out.receipt else {"compile_error": out.compile_error}, indent=2,
                         ensure_ascii=False))
        return 0 if out.passed else 1
    if out.compile_error:
        print("❌ Did not compile:\n" + out.compile_error)
        return 1
    print(R.summary(out.report, out.build))
    if out.receipt_path and animate:
        gif = _ws().animate(out)
        if gif:
            print(f"\nAnimation: {gif}")
        from . import live

        print(f"Watch it run: {live.link(live.payload(out.bench, out.build, out.report))}")
    if out.receipt_path:
        print(f"\nReceipt: {out.receipt_path}")
        if out.passed:
            print(f"Firmware: {out.hex_path}")
            print(f"Flash it: arduino-cli upload -p <port> --fqbn arduino:avr:uno --input-file {out.hex_path}")
    return 0 if out.passed else 1


def cmd_run(a) -> int:
    ws = _ws()
    _need_ready(ws)
    bench = load(Path(a.bench).read_text(encoding="utf-8"))
    sketch = Path(a.sketch).read_text(encoding="utf-8")
    ws.approve(bench, by="command line")          # you ran it with this bench: that is the approval
    out = ws.test(bench, sketch, crash=not a.no_crash)
    return _show(out, as_json=a.json)


def cmd_verify(a) -> int:
    ws = _ws()
    _need_ready(ws)
    rec = load_receipt(Path(a.receipt))
    diffs = ws.verify(rec, Path(a.sketch).read_text(encoding="utf-8"))
    if diffs:
        print("❌ Not verified:")
        for d in diffs:
            print(" -", d)
        return 1
    print(f"✅ Verified: receipt {rec['id']} reproduces exactly ({rec['verdict']}, "
          f"firmware {rec['firmware']['sha256'][:12]})")
    return 0


def cmd_demo(_a) -> int:
    ws = _ws()
    _need_ready(ws)
    ex = ROOT / "examples" / "greenhouse-fan"
    bench = load((ex / "bench.json").read_text(encoding="utf-8"))
    print(R.approval_text(bench))
    ws.approve(bench, by="command line demo")
    for label, f in (("First draft", "draft.ino"), ("After fixes", "fixed.ino")):
        print(f"\n━━ {label} ({f}) ━━")
        out = ws.test(bench, (ex / f).read_text(encoding="utf-8"))
        _show(out, as_json=False)
    return 0


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):           # ✓ and °C on a Windows console or a pipe
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser(prog="benchforge", description="A flight simulator for AI-written Arduino firmware.")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup", help="install the pinned Arduino compiler").set_defaults(fn=cmd_setup)
    c = sub.add_parser("check", help="show a bench's checks in plain English")
    c.add_argument("bench")
    c.set_defaults(fn=cmd_check)
    r = sub.add_parser("run", help="test a sketch on a bench")
    r.add_argument("bench")
    r.add_argument("sketch")
    r.add_argument("--no-crash", action="store_true", help="skip the crash tests")
    r.add_argument("--json", action="store_true", help="print the receipt as JSON")
    r.set_defaults(fn=cmd_run)
    v = sub.add_parser("verify", help="rebuild and re-run a receipt")
    v.add_argument("receipt")
    v.add_argument("sketch")
    v.set_defaults(fn=cmd_verify)
    sub.add_parser("demo", help="greenhouse fan: first draft vs fixed").set_defaults(fn=cmd_demo)
    a = p.parse_args(argv)
    try:
        return a.fn(a)
    except (BenchError, ToolchainError, OSError) as exc:
        print(f"benchforge: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
