"""Put firmware on the bench: the person's checks first, then the crash tests.

    report = run_bench(bench, build, node)
    report.passed        # every check in every run
    report.first_failure # the one thing to fix next (earliest failure, your checks before crash tests)

Crash tests replay the same checks under conditions real boards meet and bench demos skip:

- noisy sensor: every analog reading jitters, the way a long wire or a cheap sensor does
- clock rollover: millis() starts just short of 4,294,967,295 ms and wraps mid-test, as it does on
  a board left running for 49.7 days
- bouncy button: every press and release chatters for 5 ms before it settles
- unplugged sensor: the wire is cut while running; the outputs must fall back to the safe state
  the person approved

Every run is deterministic: the same firmware on the same bench gives the same result, every time.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from . import checks as C
from . import sim
from .spec import PART_TYPES, SENSOR_TYPES, Bench, Check, Window, fmt_ms, state_noun

NOISE = {"lm35": (0.6, "°C"), "ntc": (0.6, "°C"), "potentiometer": (1.5, "%")}
BOUNCE_MS = 5
UNPLUG_SETTLE_MS = 1500
UNPLUG_WITHIN_MS = 2000
UNPLUG_HOLD_MS = 1000
SEED = 20261111
TEMP_GLIDE_MS = 250


@dataclass
class RunReport:
    name: str
    title: str
    results: list = field(default_factory=list)
    skipped: str = ""
    error: str = ""
    serial_tail: list = field(default_factory=list)
    trace: dict | None = None                  # kept only for the main run (board animation, debugging)

    @property
    def passed(self) -> bool:
        return not self.skipped and not self.error and all(r.passed for r in self.results)

    def to_json(self) -> dict:
        d = {"name": self.name, "title": self.title, "passed": self.passed,
             "results": [r.to_json() for r in self.results]}
        if self.skipped:
            d["skipped"] = self.skipped
        if self.error:
            d["error"] = self.error
        return d


@dataclass
class Report:
    bench: Bench
    runs: list

    @property
    def passed(self) -> bool:
        return self.runs[0].passed and all(r.passed or r.skipped for r in self.runs)

    @property
    def main(self) -> RunReport:
        return self.runs[0]

    @property
    def first_failure(self):
        """(run, result) to fix next: errors first, then the earliest failed check, main run first."""
        for run in self.runs:
            if run.error:
                return run, None
            hit = C.first_failure(run.results)
            if hit is not None:
                return run, hit
        return None

    def counts(self) -> tuple:
        done = [r for run in self.runs for r in run.results]
        return sum(r.passed for r in done), len(done)


def _stimuli(bench: Bench) -> list:
    """Each step's inputs, applied when the step starts. Temperatures glide to the new value over
    TEMP_GLIDE_MS, as air does; nothing in a greenhouse jumps 7 °C in a millisecond."""
    out = []
    now = {pid: p["start"] for pid, p in bench.parts.items() if "start" in p}
    for c in bench.steps:
        t = c.window.start_ms
        for pid, value in c.set.items():
            ptype = bench.parts[pid]["type"]
            sig = PART_TYPES[ptype].signal
            if ptype in ("lm35", "ntc") and now.get(pid) != value:
                out.append({"from_ms": t, "to_ms": t + TEMP_GLIDE_MS, "part": pid, "ramp": {sig: [now[pid], value]}})
            else:
                out.append({"at_ms": t, "part": pid, "set": {sig: value}})
            now[pid] = value
    return out


def _sensors(bench: Bench, types=SENSOR_TYPES) -> list:
    return [p for p in bench.parts.values() if p["type"] in types]


def plan(bench: Bench, probes: dict) -> list:
    """The runs to do, as (name, title, engine kwargs, judge) tuples. Runs that can't apply are skipped."""
    base = {"parts": bench.engine_parts(), "duration_ms": bench.duration_ms, "stimuli": _stimuli(bench)}
    runs = [("main", "Your checks", base, None)]

    noisy = [p for p in _sensors(bench, set(NOISE))]
    if noisy:
        faults = [{"at_ms": 0, "part": p["id"], "fault": "noise", "value": NOISE[p["type"]][0]} for p in noisy]
        desc = ", ".join(f"{p['id']} ±{NOISE[p['type']][0]:g} {NOISE[p['type']][1]}" for p in noisy)
        runs.append(("noise", f"Noisy sensor ({desc})", {**base, "stimuli": faults + base["stimuli"]}, None))
    else:
        runs.append(("noise", "Noisy sensor", None, "no analog sensors on this bench"))

    if "main_pc" in probes and "timer0_millis" in probes:
        wrap_at = int(min(max(1000, bench.duration_ms * 0.4), 20000))
        millis = (2 ** 32 - wrap_at) % 2 ** 32
        pokes = [{"addr": probes["timer0_millis"], "bytes": list(millis.to_bytes(4, "little"))}]
        if "timer0_overflow_count" in probes:
            ovf = (2 ** 24 - (wrap_at * 1000) // 1024) % 2 ** 32
            pokes.append({"addr": probes["timer0_overflow_count"], "bytes": list(ovf.to_bytes(4, "little"))})
        runs.append(("rollover", f"Clock rollover (millis() wraps at {fmt_ms(wrap_at)}, as on day 49.7)",
                     {**base, "pokes": pokes, "poke_at_pc": probes["main_pc"]}, None))
    else:
        runs.append(("rollover", "Clock rollover", None, "the firmware's clock variables were not found"))

    buttons = _sensors(bench, {"button"})
    if buttons:
        faults = [{"at_ms": 0, "part": p["id"], "fault": "bounce", "value": BOUNCE_MS} for p in buttons]
        runs.append(("bounce", f"Bouncy button ({BOUNCE_MS} ms of contact chatter on every press)",
                     {**base, "stimuli": faults + base["stimuli"]}, None))

    for sid, states in bench.unplugged.items():
        duration = UNPLUG_SETTLE_MS + UNPLUG_WITHIN_MS + UNPLUG_HOLD_MS + 200
        kw = {"parts": bench.engine_parts(), "duration_ms": duration,
              "stimuli": [{"at_ms": UNPLUG_SETTLE_MS, "part": sid, "fault": "disconnect"}]}
        checks = []
        for oid, st in states.items():
            e = {"part": oid, "becomes": st, "within_ms": UNPLUG_WITHIN_MS, "hold_ms": UNPLUG_HOLD_MS}
            text = f"{sid} unplugged → {oid} falls back to {state_noun(st)} within {fmt_ms(UNPLUG_WITHIN_MS)}"
            checks.append(Check(id=f"unplugged_{sid}_{oid}", expect=e, window=Window(
                UNPLUG_SETTLE_MS, UNPLUG_SETTLE_MS + UNPLUG_WITHIN_MS + UNPLUG_HOLD_MS), text=text))
        runs.append((f"unplugged_{sid}", f"{sid} unplugged mid-run", kw, checks))
    return runs


def _do(bench: Bench, node: str, hex_text: str, name: str, kw: dict, own_checks):
    if own_checks is None:
        out = sim.run(node, hex_text, seed=SEED, **kw)
        results = C.judge(bench, out)
    else:
        out = sim.run(node, hex_text, seed=SEED, **kw)
        trace = C.Trace(bench, out)
        results = [C.check_one(trace, c) for c in own_checks] + C.relay_chatter(trace)
    return out, results


def run_bench(bench: Bench, build, node: str, *, crash: bool = True, workers: int | None = None,
              keep_trace: bool = True) -> Report:
    runs_plan = plan(bench, build.probes)
    if not crash:
        runs_plan = runs_plan[:1]
    reports = []
    todo = []
    for name, title, kw, judge in runs_plan:
        rr = RunReport(name=name, title=title)
        if kw is None:
            rr.skipped = judge
        else:
            todo.append((rr, kw, judge))
        reports.append(rr)
    workers = workers or max(1, min(4, os.cpu_count() or 1))

    def work(item):
        rr, kw, judge = item
        try:
            out, results = _do(bench, node, build.hex, rr.name, kw, judge)
            rr.results = results
            rr.serial_tail = out["serial"]["lines"][-20:]
            if rr.name == "main" and keep_trace:
                rr.trace = out
        except sim.SimError as exc:
            rr.error = str(exc)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(work, todo))
    return Report(bench=bench, runs=reports)


def fix_brief(report: Report, sketch: str) -> str:
    """What a model needs to fix the firmware: the one failure, what happened, and nearby evidence.
    The checks are fixed: this brief never invites changing them."""
    ff = report.first_failure
    if ff is None:
        return ""
    run, res = ff
    lines = [f"BenchForge ran the firmware on a simulated Arduino Uno (ATmega328P, 16 MHz).",
             f"Run: {run.title}."]
    if res is None:
        lines.append(f"The simulation itself failed: {run.error}")
    else:
        lines += [f"Failed check: {res.text}", f"What happened: {res.saw}",
                  f"Check window: {fmt_ms(res.window[0])} to {fmt_ms(res.window[1])} after power-on."]
    if run.serial_tail:
        tail = "; ".join(f"{fmt_ms(t)}: {txt[:60]}" for t, txt in run.serial_tail[-8:])
        lines.append(f"Last Serial lines: {tail}")
    if run.name == "noise":
        lines.append("The readings jitter in this run. Average several readings and use separate on/off "
                     "thresholds (hysteresis) so outputs do not flicker.")
    if run.name == "rollover":
        lines.append("millis() wraps to 0 during this run. Compare elapsed time with subtraction "
                     "(millis() - start >= interval), never millis() >= deadline.")
    if run.name == "bounce":
        lines.append("Each press chatters for a few ms. Debounce: accept a new button state only after "
                     "it has held steady for 20 to 50 ms.")
    if run.name.startswith("unplugged_"):
        lines.append("The sensor wire is cut mid-run, so its pin reads nonsense (floating, or stuck at a rail). "
                     "Detect impossible or wildly jumping readings and drive the outputs to the safe state.")
    lines.append("Change only the sketch. The checks are approved by the person and cannot change.")
    return "\n".join(lines)
