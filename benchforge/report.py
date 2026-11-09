"""Turn a bench report into the short text people read in a chat or a terminal."""

from __future__ import annotations

from .spec import describe, wiring


def approval_text(bench, *, header: bool = True) -> str:
    lines = []
    if header:
        lines.append(f"🧪 Bench: {bench.name}")
        if bench.goal:
            lines.append(bench.goal)
        lines.append("")
    lines.append("Wiring (Arduino Uno):")
    lines += [f"  {w}" for w in wiring(bench)]
    lines.append("")
    lines.append("The firmware passes only if:")
    lines += [f"  {line}" for line in describe(bench)]
    lines.append("")
    lines.append("Crash tests after that: noisy sensor, clock rollover (day 49.7)"
                 + (", bouncy button" if any(p["type"] == "button" for p in bench.parts.values()) else "")
                 + (", unplugged sensor" if bench.unplugged else "") + ".")
    return "\n".join(lines)


def run_lines(report) -> list:
    out = []
    for run in report.runs:
        if run.skipped:
            out.append(f"– {run.title}: skipped ({run.skipped})")
            continue
        if run.error:
            out.append(f"✗ {run.title}: {run.error}")
            continue
        ok = sum(r.passed for r in run.results)
        if run.passed:
            out.append(f"✓ {run.title}: {ok}/{len(run.results)}")
            continue
        out.append(f"✗ {run.title}: {ok}/{len(run.results)}")
        for r in run.results:
            if not r.passed:
                out.append(f"    ✗ {r.text}")
                out.append(f"      saw: {r.saw}")
    return out


def summary(report, build=None, *, title: str | None = None) -> str:
    good, total = report.counts()
    head = title or ("✅ All green" if report.passed else "❌ Not yet")
    lines = [f"{head}: {good}/{total} checks passed across {sum(1 for r in report.runs if not r.skipped)} runs"]
    lines += run_lines(report)
    if build is not None:
        size = []
        if build.flash_bytes is not None:
            size.append(f"{build.flash_bytes:,} B flash")
        if build.ram_bytes is not None:
            size.append(f"{build.ram_bytes:,} B RAM")
        lines.append("")
        lines.append(f"Firmware {build.hex_sha256[:12]} · " + " · ".join(size) if size else
                     f"Firmware {build.hex_sha256[:12]}")
        lines += [f"⚠ {w}" for w in build.warnings]
    return "\n".join(lines)
