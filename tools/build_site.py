"""Build the live-board website (GitHub Pages): the page, the engine as one browser script, and the
example benches compiled and tested, so anyone can watch them run without installing anything.

    python tools/build_site.py site/
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from benchforge import live  # noqa: E402
from benchforge.forge import Workspace  # noqa: E402
from benchforge.spec import load  # noqa: E402


def main(out: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    for f in ("index.html", "style.css", "app.js"):
        shutil.copy(ROOT / "web" / f, out / f)
    (out / "engine.js").write_text(live.engine_bundle(), encoding="utf-8")
    ws = Workspace(Path(os.environ.get("BENCHFORGE_HOME") or tempfile.mkdtemp(prefix="benchforge-site-")))
    ok, missing = ws.ready()
    if not ok:
        print(f"missing {missing}; run python -m benchforge setup")
        return 2
    demos = out / "demos"
    demos.mkdir(exist_ok=True)
    for ex in sorted(p for p in (ROOT / "examples").iterdir() if (p / "bench.json").is_file()):
        bench = load((ex / "bench.json").read_text(encoding="utf-8"))
        for sketch, suffix in (("fixed.ino", ""), ("draft.ino", "-draft")):
            res = ws.test(bench, (ex / sketch).read_text(encoding="utf-8"), save=False)
            if res.report is None:
                print(f"{ex.name}/{sketch}: did not compile")
                return 1
            p = live.payload(bench, res.build, res.report)
            (demos / f"{ex.name}{suffix}.json").write_text(json.dumps(p, separators=(",", ":")), encoding="utf-8")
            print(f"{ex.name}{suffix}: {p['verdict']} {p['score']}")
    (out / ".nojekyll").write_text("")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1] if len(sys.argv) > 1 else "site")))
