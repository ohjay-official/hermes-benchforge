"""Judge one simulation trace against a bench's checks.

The engine reports, for every millisecond, each output pin's level (1, 0, or -1 when the pin is not
an output), its duty (share of the millisecond it was high, when the pin is pulsing), each part's
measured value (servo angle, buzzer pitch) and every line printed on Serial. Each check reads only
the part of that record inside its own window, and explains a failure in words: what was expected,
what happened instead, and when.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from .spec import BOOT_GRACE_MS, ONOFF_TYPES, Bench, Check, describe_state, fmt_ms, fmt_num

RELAY_MAX_CLICKS_PER_S = 4


@dataclass
class Result:
    id: str
    text: str
    passed: bool
    saw: str                      # what actually happened, in words
    at_ms: int | None = None      # when it went wrong (or when it was satisfied)
    window: tuple = ()
    detail: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        d = {"id": self.id, "check": self.text, "passed": self.passed, "saw": self.saw,
             "window_ms": list(self.window)}
        if self.at_ms is not None:
            d["at_ms"] = self.at_ms
        if self.detail:
            d["detail"] = self.detail
        return d


class Trace:
    """Per-millisecond views of one engine run, by part."""

    def __init__(self, bench: Bench, run: dict):
        self.bench = bench
        self.run = run
        self.duration = run["duration_ms"]

    def pin(self, pid: str) -> dict:
        return self.run["pins"][self.bench.parts[pid]["pins"]["pin"]]

    def active(self, pid: str) -> list:
        """For an on/off output: per-ms share of the time it was active (on), or None where floating."""
        part = self.bench.parts[pid]
        rec = self.pin(pid)
        duty = rec.get("duty")
        low = part["params"].get("active_low", False)
        out = []
        for t, lv in enumerate(rec["level"]):
            if lv == -1:
                out.append(None)
                continue
            v = duty[t] if duty is not None else lv
            out.append(1 - v if low else v)
        return out

    def onoff(self, pid: str) -> list:
        return [None if a is None else ("on" if a >= 0.5 else "off") for a in self.active(pid)]

    def signal(self, pid: str, sig: str) -> list:
        return self.run["signals"][f"{pid}.{sig}"]


def _matches(ptype: str, state, trace: Trace, pid: str):
    """A per-ms predicate for `state`, plus a function that names what a sample shows."""
    if ptype in ONOFF_TYPES and state in ("on", "off"):
        series = trace.onoff(pid)
        return (lambda t: series[t] == state), (lambda t: "floating" if series[t] is None else series[t]), series
    if ptype == "led" and isinstance(state, dict):
        series = trace.active(pid)
        return None, None, series            # brightness is judged over the window, not per ms
    if ptype == "buzzer":
        hz = trace.signal(pid, "hz")
        if state == "silent":
            return (lambda t: hz[t] < 20), (lambda t: "silent" if hz[t] < 20 else f"{hz[t]} Hz"), hz
        target, tol = state["hz"], state["tolerance"]
        ok = lambda t: abs(hz[t] - target) <= max(20, target * tol / 100)
        return ok, (lambda t: "silent" if hz[t] < 20 else f"{hz[t]} Hz"), hz
    if ptype == "servo":
        ang = trace.signal(pid, "angle")
        target, tol = state["angle"], state["tolerance"]
        return (lambda t: ang[t] >= 0 and abs(ang[t] - target) <= tol), \
               (lambda t: "not driven" if ang[t] < 0 else f"{ang[t]}°"), ang
    raise ValueError(f"cannot judge {ptype} against {state!r}")


def _story(name_of, start: int, end: int, limit: int = 6) -> tuple:
    """Compress what an output did in [start, end) into runs: 'off until 1.2 s, then on'."""
    runs = []
    for t in range(start, end):
        label = name_of(t)
        if runs and runs[-1][0] == label:
            runs[-1][2] = t + 1
        else:
            runs.append([label, t, t + 1])
    if not runs:
        return "", 0
    flips = len(runs) - 1
    if len(runs) == 1:
        return f"stayed {runs[0][0]}", 0
    if flips > limit:
        labels = {r[0] for r in runs}
        nums = sorted(float(x.rstrip("°Hz ")) for x in labels if x.rstrip("°Hz ").replace(".", "", 1).isdigit())
        if len(labels) > 3 and len(nums) >= 3:
            unit = "°" if any(x.endswith("°") for x in labels) else " Hz"
            return (f"swung between {nums[0]:g}{unit} and {nums[-1]:g}{unit}, changing {flips} times"
                    + (" (sometimes not driven)" if "not driven" in labels else ""), flips)
        return f"flickered between {' and '.join(sorted(labels))} {flips} times", flips
    parts = [f"{runs[0][0]}"] + [f"{r[0]} at {fmt_ms(r[1])}" for r in runs[1:]]
    return ", then ".join(parts), flips


def _floating_hint(trace: Trace, pid: str) -> str:
    pin = trace.bench.parts[pid]["pins"]["pin"]
    num = pin[1:] if pin.startswith("D") else pin
    return f"{pin} was never set as an output (missing pinMode({num}, OUTPUT)?)"


def check_one(trace: Trace, c: Check) -> Result:
    e = c.expect
    w0, w1 = c.window.start_ms, min(c.window.end_ms, trace.duration)
    base = dict(id=c.id, text=c.text, window=(w0, w1))
    if "serial" in e:
        return _check_serial(trace, c, base)
    pid = e["part"]
    ptype = trace.bench.parts[pid]["type"]
    if "blinks_hz" in e:
        return _check_blinks(trace, c, base)
    key = "becomes" if "becomes" in e else "stays"
    state = e[key]
    if isinstance(state, dict) and "brightness_pct" in state:
        return _check_brightness(trace, c, base, key)
    ok, name_of, _series = _matches(ptype, state, trace, pid)
    if key == "stays":
        s0 = max(w0, BOOT_GRACE_MS) if w0 == 0 else w0
        for t in range(s0, w1):
            if not ok(t):
                story, _ = _story(name_of, s0, w1)
                saw = f"{pid} {story}"
                if name_of(t) == "floating":
                    saw = _floating_hint(trace, pid)
                return Result(passed=False, saw=saw, at_ms=t, **base)
        return Result(passed=True, saw=f"{pid} stayed {describe_state(state)}", **base)
    # becomes: some moment within the deadline after which it holds the state for hold_ms
    within, hold = e["within_ms"], e["hold_ms"]
    run_len = 0
    for t in range(w1 - 1, w0 - 1, -1):          # length of the matching run starting at each t
        run_len = run_len + 1 if ok(t) else 0
        if t <= w0 + within and run_len >= hold:
            first = t
            while first > w0 and ok(first - 1):
                first -= 1
            latency = first - w0
            return Result(passed=True, saw=f"{pid} {_verb_past(state)} after {fmt_ms(latency)}",
                          at_ms=first, detail={"latency_ms": latency}, **base)
    story, flips = _story(name_of, w0, w1)
    saw = f"{pid} {story}"
    if all(name_of(t) == "floating" for t in range(w0, w1)):
        saw = _floating_hint(trace, pid)
    late = _first_hold(ok, w0 + within + 1, trace.duration, hold)
    if late is not None and not flips > 6:
        saw += f" (it got there at {fmt_ms(late)}, {fmt_ms(late - w0)} after the change: too late)"
    return Result(passed=False, saw=saw, at_ms=w0 + within, **base)


def _first_hold(ok, start: int, end: int, hold: int):
    run = 0
    for t in range(start, end):
        run = run + 1 if ok(t) else 0
        if run >= hold:
            return t - hold + 1
    return None


def _verb_past(state) -> str:
    if state in ("on", "off"):
        return f"turned {state}"
    if state == "silent":
        return "went silent"
    return f"reached {describe_state(state)}"


def _check_serial(trace: Trace, c: Check, base: dict) -> Result:
    w0, w1 = base["window"]
    want = c.expect["serial"]
    lines = trace.run["serial"]["lines"]
    for t, text in lines:
        if w0 <= t < w1 and want in text:
            return Result(passed=True, saw=f'printed "{_clip(text)}" at {fmt_ms(t)}', at_ms=t, **base)
    nearby = [f'"{_clip(text)}" at {fmt_ms(t)}' for t, text in lines if w0 - 500 <= t < w1 + 500][:3]
    if not lines:
        saw = "nothing was printed on Serial (is Serial.begin() called, at the right baud?)"
    elif nearby:
        saw = "printed " + "; ".join(nearby) + f', but not "{want}"'
    else:
        saw = f"nothing was printed in this window; last line: \"{_clip(lines[-1][1])}\" at {fmt_ms(lines[-1][0])}"
    later = next((t for t, text in lines if t >= w1 and want in text), None)
    if later is not None:
        saw += f' ("{want}" came at {fmt_ms(later)}: too late)'
    return Result(passed=False, saw=saw, at_ms=w1, **base)


def _clip(s: str, n: int = 60) -> str:
    s = "".join(ch if 32 <= ord(ch) < 127 else "?" for ch in s)
    return s if len(s) <= n else s[: n - 1] + "…"


def _check_blinks(trace: Trace, c: Check, base: dict) -> Result:
    e = c.expect
    pid = e["part"]
    w0, w1 = base["window"]
    w0 = max(w0, BOOT_GRACE_MS)
    target, tol = e["blinks_hz"], e["tolerance_pct"]
    rises = _rising_edges_us(trace, pid, w0, w1)
    if rises is None:
        return Result(passed=False, saw=_floating_hint(trace, pid), at_ms=w0, **base)
    if len(rises) < 2:
        states = trace.onoff(pid)
        seg = [s for s in states[w0:w1] if s is not None]
        what = f"stayed {seg[0]}" if seg and len(set(seg)) == 1 else "barely changed"
        return Result(passed=False, saw=f"{pid} {what} (no steady blinking)", at_ms=w0, **base)
    periods = [(b - a) / 1e6 for a, b in zip(rises, rises[1:])]
    period = statistics.median(periods)
    hz = 1 / period if period > 0 else 0.0
    spread = (max(periods) - min(periods)) / period if len(periods) > 1 and period > 0 else 0.0
    detail = {"hz": round(hz, 3), "cycles": len(periods)}
    ok = abs(hz - target) <= target * tol / 100 and spread <= max(0.5, tol / 50)
    saw = f"{pid} blinked at {hz:.2f} Hz"
    if spread > max(0.5, tol / 50):
        saw += f", unevenly (periods from {fmt_ms(min(periods) * 1000)} to {fmt_ms(max(periods) * 1000)})"
    return Result(passed=ok, saw=saw, at_ms=None if ok else w0, detail=detail, **base)


def _rising_edges_us(trace: Trace, pid: str, w0: int, w1: int):
    """Times (µs) the output turned on inside [w0, w1). None if the pin was floating throughout."""
    states = trace.onoff(pid)
    if all(s is None for s in states[w0:w1]):
        return None
    rec = trace.pin(pid)
    if rec.get("duty") is None and not rec.get("truncated"):
        low = trace.bench.parts[pid]["params"].get("active_low", False)
        on_level = 0 if low else 1
        return [us for us, v in rec["edges"] if v == on_level and w0 * 1000 <= us < w1 * 1000]
    # pulsing (PWM) or very busy pins: use the per-millisecond on/off view
    out = []
    for t in range(max(w0, 1), w1):
        if states[t] == "on" and states[t - 1] != "on":
            out.append(t * 1000)
    return out


def _check_brightness(trace: Trace, c: Check, base: dict, key: str) -> Result:
    e = c.expect
    pid = e["part"]
    state = e[key]
    w0, w1 = base["window"]
    active = trace.active(pid)
    target, tol = state["brightness_pct"], state["tolerance"]
    if key == "becomes":
        # judge the last hold_ms of the window
        seg = active[max(w0, w1 - e["hold_ms"]):w1]
    else:
        seg = active[max(w0, BOOT_GRACE_MS if w0 == 0 else w0):w1]
    if not seg or any(a is None for a in seg):
        return Result(passed=False, saw=_floating_hint(trace, pid), at_ms=w0, **base)
    pct = 100 * sum(seg) / len(seg)
    ok = abs(pct - target) <= tol
    return Result(passed=ok, saw=f"{pid} was at {pct:.0f} % brightness", at_ms=None if ok else w0,
                  detail={"brightness_pct": round(pct, 1)}, **base)


def relay_chatter(trace: Trace) -> list:
    """Relays clicking faster than a real one should: a sign of no hysteresis or a noisy reading."""
    out = []
    for pid, part in trace.bench.parts.items():
        if part["type"] != "relay":
            continue
        rec = trace.pin(pid)
        changes = [us // 1000 for us, _v in rec["edges"]]
        if rec.get("truncated"):
            changes = changes + [trace.duration]
        worst, at = 0, None
        j = 0
        for i, t in enumerate(changes):
            while changes[j] < t - 1000:
                j += 1
            if i - j + 1 > worst:
                worst, at = i - j + 1, changes[j]
        if worst > RELAY_MAX_CLICKS_PER_S:      # every change of state is one click
            out.append(Result(id=f"{pid}_chatter", text=f"{pid} never clicks more than "
                              f"{RELAY_MAX_CLICKS_PER_S} times a second", passed=False,
                              saw=f"{pid} clicked {worst} times within one second, from {fmt_ms(at)}",
                              at_ms=at, window=(0, trace.duration), detail={"clicks_per_s": worst}))
    return out


def judge(bench: Bench, run: dict, *, chatter: bool = True) -> list:
    """All of a bench's checks against one run, in timeline order (steps first, then always-checks)."""
    trace = Trace(bench, run)
    results = [check_one(trace, c) for c in bench.checks]
    if chatter:
        results.extend(relay_chatter(trace))
    return results


def first_failure(results: list):
    failed = [r for r in results if not r.passed]
    if not failed:
        return None
    return min(failed, key=lambda r: (r.at_ms if r.at_ms is not None else 10 ** 9))


def fmt_result(r: Result) -> str:
    return f"{'✓' if r.passed else '✗'} {r.text}" + ("" if r.passed else f"\n    saw: {r.saw}")


__all__ = ["Result", "Trace", "judge", "first_failure", "fmt_result", "relay_chatter", "fmt_num"]
