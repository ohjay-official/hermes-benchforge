"""Golden benches: every example's first draft must fail exactly the checks recorded in its expect.json,
and its fixed sketch must pass everything. CI runs this with the real arduino-cli on every push.

    python tools/golden.py            check every example against expect.json
    python tools/golden.py --update   rewrite expect.json from the current results (review the diff!)
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from benchforge.forge import Workspace  # noqa: E402
from benchforge.spec import load  # noqa: E402


def failing(out) -> list:
    if out.compile_error:
        return ["compile"]
    ids = []
    for run in out.report.runs:
        if run.error:
            ids.append(f"{run.name}/error")
        ids += [f"{run.name}/{r.id}" for r in run.results if not r.passed]
    return sorted(ids)


def main(update: bool) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    home = Path(os.environ.get("BENCHFORGE_HOME") or tempfile.mkdtemp(prefix="benchforge-golden-"))
    ws = Workspace(home)
    ok, missing = ws.ready()
    if not ok:
        print(f"missing {missing}; run python -m benchforge setup")
        return 2
    bad = 0
    for ex in sorted(p for p in (ROOT / "examples").iterdir() if (p / "bench.json").is_file()):
        bench = load((ex / "bench.json").read_text(encoding="utf-8"))
        expect_path = ex / "expect.json"
        expect = json.loads(expect_path.read_text()) if expect_path.is_file() else {}
        got = {}
        for sk in sorted(ex.glob("*.ino")):
            t = time.time()
            out = ws.test(bench, sk.read_text(encoding="utf-8"), save=False)
            got[sk.name] = {"verdict": "pass" if out.passed else "fail", "fails": failing(out)}
            want = expect.get(sk.name)
            mark = "✓" if want == got[sk.name] else "✗"
            if want != got[sk.name]:
                bad += 1
            print(f"{mark} {ex.name}/{sk.name}: {got[sk.name]['verdict']} {got[sk.name]['fails']} "
                  f"({time.time() - t:.1f} s)")
            if want != got[sk.name] and not update:
                print(f"    expected {want}")
                if out.compile_error:
                    print("    " + out.compile_error.replace("\n", "\n    "))
        if update:
            expect_path.write_text(json.dumps(got, indent=2) + "\n")
    if update:
        print("expect.json files rewritten; review them before committing")
        return 0
    print("all golden benches match" if not bad else f"{bad} golden result(s) changed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main("--update" in sys.argv))
