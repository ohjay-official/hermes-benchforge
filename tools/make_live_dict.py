"""Build the preset dictionary that keeps live links short enough for a chat message.

A live link carries the whole tested firmware, so anyone can replay it in a browser with nothing
uploaded anywhere. Plain, that is 4 to 7 KB of URL: too long for Telegram (4,096 characters a message)
or Discord. Most of any Uno firmware is the same Arduino core code (Serial, millis, the start-up code),
so links are compressed against a dictionary made of the golden benches' own results and flash images:
the fixed sketch of each example, compiled by the pinned toolchain. Links come out at about 2 to 2.5 KB.

The dictionary is data, not code: the JSON results the live page shows and the firmware it runs, the
same things the site serves under demos/. Rebuild it, or check the committed one matches a fresh build:

    python tools/make_live_dict.py           # write web/dicts/<zlib id>.txt and point current.txt at it
    python tools/make_live_dict.py --check   # exit 1 if a fresh build differs from the current one (CI, Linux)

zlib records the dictionary's id in every link, and the page fetches dicts/<id>.txt, so links made with
an older dictionary keep working as long as its file stays in web/dicts/. Never delete one.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import textwrap
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from benchforge import live  # noqa: E402
from benchforge.forge import Workspace  # noqa: E402
from benchforge.spec import load  # noqa: E402

EXAMPLES = ("greenhouse-fan", "button-toggle", "parking-sensor", "servo-knob", "incubator-heater")
MAX = 32768                                  # zlib's window: a dictionary can't usefully be longer


def build(ws: Workspace) -> bytes:
    heads, images = [], []
    for name in EXAMPLES:
        ex = ROOT / "examples" / name
        bench = load((ex / "bench.json").read_text(encoding="utf-8"))
        res = ws.test(bench, (ex / "fixed.ino").read_text(encoding="utf-8"), save=False)
        if res.report is None:
            raise SystemExit(f"{name}/fixed.ino did not compile")
        p = live.payload(bench, res.build, res.report)
        images.append(base64.b64decode(p.pop("bin")))
        heads.append(json.dumps(p, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    # Firmware last: zlib favours what sits nearest the data, and the firmware is most of every link.
    return (b"".join(heads) + b"".join(images))[-MAX:]


def render(d: bytes) -> str:
    head = ("# BenchForge live-link dictionary: the results and flash images of the golden benches' fixed\n"
            f"# sketches ({', '.join(EXAMPLES)}), base64. zlib id {zlib.adler32(d):08x}.\n"
            "# Built by tools/make_live_dict.py; see its docstring.\n")
    return head + "\n".join(textwrap.wrap(base64.b64encode(d).decode("ascii"), 76)) + "\n"


def main(argv: list) -> int:
    ws = Workspace(Path(os.environ.get("BENCHFORGE_HOME") or tempfile.mkdtemp(prefix="benchforge-dict-")))
    ok, missing = ws.ready()
    if not ok:
        print(f"missing {missing}; run python -m benchforge setup")
        return 2
    d = build(ws)
    name = f"{zlib.adler32(d):08x}"
    if "--check" in argv:
        same = live.live_dict() == d
        print(f"the current dictionary matches a fresh build ({name})" if same else
              f"the current dictionary differs from a fresh build ({name})")
        return 0 if same else 1
    live.DICTS.mkdir(exist_ok=True)
    (live.DICTS / f"{name}.txt").write_text(render(d), encoding="ascii")
    (live.DICTS / "current.txt").write_text(name + "\n", encoding="ascii")
    print(f"wrote web/dicts/{name}.txt and made it current")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
