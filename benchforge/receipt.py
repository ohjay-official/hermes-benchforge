"""The proof receipt: what was tested, on what, and what happened, in one JSON file anyone can re-run.

A receipt names the bench (and carries it whole), the sketch and firmware by SHA-256, the compiler
and simulator versions, and every check's result in every run. Its ``id`` is the SHA-256 of all of
that, so a receipt can't be edited without its id changing. ``verify`` rebuilds the firmware from the
sketch, runs the same bench again and compares every result: the simulator is deterministic, so a
genuine receipt reproduces exactly.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
from pathlib import Path

from . import VERSION
from .spec import bench_hash, canonical, load

ENGINE_DIR = Path(__file__).resolve().parent.parent / "engine"
RECEIPT_VERSION = 1


def engine_fingerprint() -> dict:
    h = hashlib.sha256()
    for f in sorted(ENGINE_DIR.rglob("*.js")):
        h.update(f.relative_to(ENGINE_DIR).as_posix().encode())
        h.update(f.read_bytes())
    commit = (ENGINE_DIR / "vendor" / "avr8js" / "SOURCE_COMMIT").read_text().strip()
    return {"name": "avr8js", "avr8js_commit": commit, "sha256": h.hexdigest()}


def _body_id(body: dict) -> str:
    return hashlib.sha256(canonical(body)).hexdigest()[:24]


def make(bench, build, report, *, node_version: str = "") -> dict:
    body = {
        "receipt": RECEIPT_VERSION,
        "benchforge": VERSION,
        "verdict": "pass" if report.passed else "fail",
        "bench": {"name": bench.name, "sha256": bench.sha256, "spec": bench.raw},
        "sketch": {"sha256": build.sketch_sha256},
        "firmware": {"sha256": build.hex_sha256, "flash_bytes": build.flash_bytes, "ram_bytes": build.ram_bytes,
                     "compiler": build.compiler},
        "chip": {"mcu": "atmega328p", "f_cpu": 16_000_000, "board": "Arduino Uno"},
        "engine": {**engine_fingerprint(), "node": node_version},
        "runs": [r.to_json() for r in report.runs],
    }
    return {"id": _body_id(body), **body,
            "created_at": _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()}


def check_integrity(receipt: dict) -> list:
    """Problems with the receipt itself (edited, or its bench doesn't match its own hash)."""
    problems = []
    body = {k: v for k, v in receipt.items() if k not in ("id", "created_at")}
    if receipt.get("id") != _body_id(body):
        problems.append("the receipt's id does not match its contents (it was edited)")
    spec = (receipt.get("bench") or {}).get("spec")
    if not isinstance(spec, dict) or bench_hash(spec) != receipt["bench"].get("sha256"):
        problems.append("the bench inside the receipt does not match its hash")
    return problems


def compare(receipt: dict, fresh: dict) -> list:
    """Differences between a stored receipt and a fresh one for the same sketch, in words."""
    diffs = []
    if receipt["firmware"]["sha256"] != fresh["firmware"]["sha256"]:
        diffs.append(f"the firmware built from this sketch is {fresh['firmware']['sha256'][:12]}, "
                     f"not {receipt['firmware']['sha256'][:12]} (different compiler or sketch)")
    if receipt["engine"]["sha256"] != fresh["engine"]["sha256"]:
        diffs.append("the simulator files differ from the ones that made this receipt")
    old = {r["name"]: r for r in receipt["runs"]}
    new = {r["name"]: r for r in fresh["runs"]}
    for name in sorted(set(old) | set(new)):
        a, b = old.get(name), new.get(name)
        if a is None or b is None:
            diffs.append(f"run {name!r} is only in the {'new' if a is None else 'stored'} result")
            continue
        ra = {x["id"]: x for x in a["results"]}
        rb = {x["id"]: x for x in b["results"]}
        for cid in sorted(set(ra) | set(rb)):
            x, y = ra.get(cid), rb.get(cid)
            if x != y:
                what = "missing" if y is None else ("passed" if y["passed"] else f"failed ({y['saw']})")
                was = "missing" if x is None else ("passed" if x["passed"] else "failed")
                diffs.append(f"{name}/{cid}: receipt says {was}, re-run {what}")
    if receipt["verdict"] != fresh["verdict"]:
        diffs.append(f"verdict: receipt says {receipt['verdict']}, re-run says {fresh['verdict']}")
    return diffs


def save(receipt: dict, hex_text: str, folder: Path) -> tuple:
    """Write the receipt and its firmware. Returns (receipt path, firmware path)."""
    folder.mkdir(parents=True, exist_ok=True)
    name = receipt["bench"]["name"]
    short = receipt["firmware"]["sha256"][:12]
    rpath = folder / f"{name}-{short}.receipt.json"
    hpath = folder / f"{name}-{short}.hex"
    rpath.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    hpath.write_bytes(hex_text.encode("ascii"))
    return rpath, hpath


def bench_of(receipt: dict):
    return load(receipt["bench"]["spec"])
