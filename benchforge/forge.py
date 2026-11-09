"""The whole loop in one place, shared by ``/bench`` in Hermes and by ``python -m benchforge``.

    ws = Workspace(data_dir)
    out = ws.test(bench, sketch)                        # compile, simulate, judge, write the receipt
    out = ws.forge(bench, sketch, fixer, progress=...)  # test, and let a model fix the sketch until green
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from . import build as B
from . import gate, receipt
from .bench import fix_brief, run_bench
from .spec import Bench, describe, wiring
from .toolchain import Toolchain, ToolchainError, node_version

MAX_FIX_ROUNDS = 5
KEEP_RESULTS = 200          # newest receipts (with their firmware and sketch) kept on disk
DRAFT_DAYS = 7              # unapproved drafts are deleted after a week


@dataclass
class Outcome:
    bench: Bench
    sketch: str
    build: object | None = None
    report: object | None = None
    receipt: dict | None = None
    receipt_path: Path | None = None
    hex_path: Path | None = None
    compile_error: str = ""

    @property
    def passed(self) -> bool:
        return self.report is not None and self.report.passed

    def brief(self) -> str:
        """What went wrong, for the model that fixes the sketch."""
        if self.compile_error:
            return ("The sketch did not compile for the Arduino Uno. Compiler output:\n" + self.compile_error +
                    "\nChange only the sketch.")
        return fix_brief(self.report, self.sketch) if self.report else ""


@dataclass
class Round:
    n: int
    outcome: Outcome
    change: str = ""


@dataclass
class Workspace:
    data_dir: Path
    compiler: object | None = None           # a BareAvrGcc for development; None means arduino-cli
    rounds: list = field(default_factory=list)

    def __post_init__(self):
        self.data_dir = Path(self.data_dir)
        if self.compiler is None:
            self.compiler = B.BareAvrGcc.from_env()

    @property
    def toolchain(self) -> Toolchain:
        return Toolchain(self.data_dir / "toolchain")

    @property
    def receipts(self) -> Path:
        return self.data_dir / "receipts"

    def ready(self) -> tuple:
        """(ready, what is missing in words)."""
        tc = self.toolchain
        node = tc.node()
        missing = []
        if not node or node_version(node)[:1] < (18,):
            missing.append("Node.js 18+ (for the chip simulator)")
        if self.compiler is None and not tc.ready():
            missing.append("the Arduino compiler (arduino-cli and the AVR core)")
        return (not missing, ", ".join(missing))

    def node(self) -> str:
        node = self.toolchain.node()
        if not node:
            raise ToolchainError("Node.js was not found; run `hermes bench setup`")
        return node

    def test(self, bench: Bench, sketch: str, *, crash: bool = True, save: bool = True) -> Outcome:
        out = Outcome(bench=bench, sketch=sketch)
        try:
            b = B.compile_sketch(sketch, _sketch_name(bench.name), toolchain=self.toolchain, compiler=self.compiler)
        except B.BuildError as exc:
            out.compile_error = exc.log or str(exc)
            return out
        out.build = b
        node = self.node()
        out.report = run_bench(bench, b, node, crash=crash)
        nv = ".".join(map(str, node_version(node)))
        out.receipt = receipt.make(bench, b, out.report, node_version=nv)
        if save:
            out.receipt_path, out.hex_path = receipt.save(out.receipt, b.hex, self.receipts)
            _prune(self.receipts, "*.receipt.json", keep=KEEP_RESULTS, companions=(".hex", ".ino"))
            gate.mark_passing(out.receipt, out.receipt_path, self.data_dir)
            sk = self.receipts / f"{bench.name}-{b.hex_sha256[:12]}.ino"
            sk.write_text(sketch, encoding="utf-8")
        return out

    def forge(self, bench: Bench, sketch: str, fixer, *, progress=None, max_rounds: int = MAX_FIX_ROUNDS) -> list:
        """Test, and while it fails, hand the one failure to ``fixer(sketch, brief) -> (new_sketch, change)``.
        Stops on green, after ``max_rounds`` fixes, or when the fixer gives up or repeats itself."""
        rounds = []
        seen = {hashlib.sha256(sketch.encode()).hexdigest()}
        out = self.test(bench, sketch)
        rounds.append(Round(0, out))
        if progress:
            progress(rounds[-1])
        for n in range(1, max_rounds + 1):
            if out.passed:
                break
            brief = out.brief()
            new, change = fixer(out.sketch, brief)
            if not new or not new.strip():
                break
            h = hashlib.sha256(new.encode()).hexdigest()
            if h in seen:
                change = (change + " " if change else "") + "(same sketch as an earlier round; stopped)"
                rounds.append(Round(n, out, change))
                break
            seen.add(h)
            out = self.test(bench, new)
            rounds.append(Round(n, out, change))
            if progress:
                progress(rounds[-1])
        self.rounds = rounds
        return rounds

    # ------------------------------------------------------------ approvals, drafts, bundles

    def approve(self, bench: Bench, by: str = "") -> None:
        """Record that a person approved this exact bench (by its hash). Only firmware that passes an
        approved bench may go through the flash gate."""
        d = self.data_dir / "approved"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{bench.sha256}.json").write_text(json.dumps(
            {"bench": bench.name, "by": by[:80], "at": _now(), "checks": describe(bench)}, ensure_ascii=False))

    def is_approved(self, bench_sha: str) -> bool:
        return bool(re.fullmatch(r"[0-9a-f]{64}", bench_sha or "")) and \
            (self.data_dir / "approved" / f"{bench_sha}.json").is_file()

    def save_draft(self, bench: Bench, request: str, sketch: str = "", by: str = "") -> str:
        did = hashlib.sha256((bench.sha256 + request + sketch).encode()).hexdigest()[:6]
        d = self.data_dir / "drafts"
        d.mkdir(parents=True, exist_ok=True)
        _prune(d, "*.json", keep=50, max_age_days=DRAFT_DAYS)
        (d / f"{did}.json").write_text(json.dumps({"id": did, "request": request, "bench": bench.raw,
                                                   "sketch": sketch, "by": by[:80], "at": _now()},
                                                  ensure_ascii=False, indent=1))
        return did

    def load_draft(self, did: str):
        did = (did or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{6}", did):
            return None
        try:
            return json.loads((self.data_dir / "drafts" / f"{did}.json").read_text())
        except (OSError, ValueError):
            return None

    def animate(self, out: Outcome) -> Path | None:
        """The run as an animated GIF of the board (best-effort: None if it can't be drawn)."""
        if out.report is None or out.build is None:
            return None
        try:
            from .board import render_report

            data = render_report(out.report)
        except Exception:
            return None
        if not data:
            return None
        d = self.data_dir / "bundles"
        d.mkdir(parents=True, exist_ok=True)
        _prune(d, "*.gif", keep=50)
        path = d / f"{out.bench.name}-{out.build.hex_sha256[:12]}.gif"
        path.write_bytes(data)
        return path

    def bundle(self, out: Outcome) -> Path | None:
        """One zip a person can download: sketch, firmware, receipt and how to flash it."""
        if out.build is None or out.receipt_path is None:
            return None
        name = f"{out.bench.name}-{out.build.hex_sha256[:12]}"
        d = self.data_dir / "bundles"
        d.mkdir(parents=True, exist_ok=True)
        _prune(d, "*.zip", keep=50)
        path = d / f"{name}.zip"
        how = (f"BenchForge {'PASSED' if out.passed else 'DID NOT PASS'}: {out.bench.name}\n"
               f"Receipt {out.receipt['id']} · firmware sha256 {out.build.hex_sha256}\n\n"
               "Wiring (Arduino Uno):\n" + "\n".join(f"  {w}" for w in wiring(out.bench)) + "\n\n"
               "Checks:\n" + "\n".join(f"  {c}" for c in describe(out.bench)) + "\n\n")
        if out.passed:
            how += (f"Flash it (Arduino Uno on USB):\n  arduino-cli upload -p <port> --fqbn arduino:avr:uno "
                    f"--input-file {name}.hex\n  (or open {out.bench.name}.ino in the Arduino IDE and upload)\n")
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(f"{out.bench.name}.ino", out.sketch)
            z.writestr(f"{name}.hex", out.build.hex)
            z.writestr(f"{name}.receipt.json", out.receipt_path.read_text(encoding="utf-8"))
            z.writestr("README.txt", how)
        return path

    def verify(self, rec: dict, sketch: str) -> list:
        """Rebuild and re-run a receipt's bench. Returns differences in words (empty: verified)."""
        problems = receipt.check_integrity(rec)
        if problems:
            return problems
        if B.sketch_sha256(sketch) != rec["sketch"]["sha256"]:
            return ["this is not the sketch the receipt was made for (its SHA-256 differs)"]
        bench = receipt.bench_of(rec)
        out = self.test(bench, sketch, save=False)
        if out.compile_error:
            return ["the sketch no longer compiles: " + out.compile_error[:300]]
        return receipt.compare(rec, out.receipt)


def _prune(folder: Path, pattern: str, *, keep: int, max_age_days: int | None = None,
           companions: tuple = ()) -> None:
    """Keep the newest ``keep`` files (and none older than ``max_age_days``); remove the rest."""
    import time

    try:
        files = sorted(folder.glob(pattern), key=lambda f: f.stat().st_mtime, reverse=True)
    except OSError:
        return
    cutoff = time.time() - max_age_days * 86400 if max_age_days else None
    for i, f in enumerate(files):
        try:
            if i >= keep or (cutoff is not None and f.stat().st_mtime < cutoff):
                stem = f.name.split(".")[0]
                f.unlink()
                for ext in companions:
                    (folder / f"{stem}{ext}").unlink(missing_ok=True)
        except OSError:
            continue


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def _sketch_name(name: str) -> str:
    return re.sub(r"[^a-z0-9_]", "_", name)[:40] or "sketch"


def load_receipt(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
