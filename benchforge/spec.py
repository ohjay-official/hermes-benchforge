"""The bench: which parts are wired where, and the checks the firmware must pass, in order.

A bench is plain JSON. Every check is written in a small fixed vocabulary, so it can be shown to a
person in plain English (``describe``), run the same way every time, and locked by its hash before
any firmware is judged against it.

    {
      "name": "greenhouse-fan",
      "board": "uno",
      "goal": "Fan on above 30 C, off below 27 C, heartbeat LED",
      "parts": [{"id": "temp", "type": "lm35", "pin": "A0", "start": 25},
                {"id": "fan", "type": "relay", "pin": "D7"},
                {"id": "led", "type": "led", "pin": "D13"}],
      "steps": [{"id": "boots_off", "expect": {"part": "fan", "stays": "off", "for_ms": 1500}},
                {"id": "hot", "set": {"temp": 32}, "expect": {"part": "fan", "becomes": "on", "within_ms": 1000}}],
      "always": [{"id": "heartbeat", "expect": {"part": "led", "blinks_hz": 1}}],
      "unplugged": {"temp": {"fan": "on"}}
    }

Steps run one after another on one timeline. A step's ``set`` changes its inputs when the step starts,
then its ``expect`` is judged over a window that depends only on the bench, never on the firmware, so
every run of a bench lines up with every other run.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any

BENCH_VERSION = 1
MAX_TIMELINE_MS = 60_000
BOOT_GRACE_MS = 50
DEFAULT_HOLD_MS = 200
TAIL_MS = 200

ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")

# Uno pins. D0 and D1 carry Serial, so parts may not use them.
DIGITAL_PINS = [f"D{i}" for i in range(2, 14)]
ANALOG_PINS = [f"A{i}" for i in range(6)]
PWM_PINS = {"D3", "D5", "D6", "D9", "D10", "D11"}


@dataclass(frozen=True)
class PartType:
    kind: str                 # "output" or "input"
    pins: tuple               # pin roles
    signal: str | None = None # the input's one value, or the output's measured value
    unit: str = ""
    low: float | None = None
    high: float | None = None
    default: Any = None
    analog: bool = False
    noun: str = ""


PART_TYPES: dict[str, PartType] = {
    "led": PartType("output", ("pin",), noun="LED"),
    "relay": PartType("output", ("pin",), noun="relay"),
    "digital_out": PartType("output", ("pin",), noun="output"),
    "buzzer": PartType("output", ("pin",), signal="hz", unit="Hz", noun="buzzer"),
    "servo": PartType("output", ("pin",), signal="angle", unit="°", noun="servo"),
    "button": PartType("input", ("pin",), signal="pressed", default=False, noun="button"),
    "potentiometer": PartType("input", ("pin",), signal="percent", unit="%", low=0, high=100, default=0,
                              analog=True, noun="knob"),
    "lm35": PartType("input", ("pin",), signal="celsius", unit="°C", low=-55, high=150, default=25,
                     analog=True, noun="temperature sensor"),
    "ntc": PartType("input", ("pin",), signal="celsius", unit="°C", low=-40, high=125, default=25,
                    analog=True, noun="thermistor"),
    "hcsr04": PartType("input", ("trig", "echo"), signal="cm", unit="cm", low=2, high=500, default=400,
                       noun="distance sensor"),
}

ONOFF_TYPES = {"led", "relay", "digital_out"}
SENSOR_TYPES = {"potentiometer", "lm35", "ntc", "hcsr04"}


class BenchError(ValueError):
    """The bench file is not valid. The message says what to fix, in words a person can act on."""


@dataclass
class Window:
    start_ms: int
    end_ms: int


@dataclass
class Check:
    id: str
    expect: dict
    set: dict = field(default_factory=dict)
    always: bool = False
    window: Window | None = None      # filled in by the timeline
    text: str = ""                    # plain English, generated from the JSON


@dataclass
class Bench:
    raw: dict
    name: str
    goal: str
    parts: dict                       # id -> part dict (normalised)
    checks: list                      # steps in order, then always-checks
    unplugged: dict                   # sensor id -> {output id: state}
    duration_ms: int

    @property
    def steps(self) -> list:
        return [c for c in self.checks if not c.always]

    @property
    def sha256(self) -> str:
        return bench_hash(self.raw)

    def part(self, pid: str) -> dict:
        return self.parts[pid]

    def engine_parts(self) -> list:
        """The parts as the simulation engine wants them."""
        out = []
        for p in self.parts.values():
            t = PART_TYPES[p["type"]]
            item = {"id": p["id"], "type": p["type"], "pins": dict(p["pins"]), "params": dict(p["params"])}
            if t.kind == "input":
                item["initial"] = {t.signal: p["start"]}
            out.append(item)
        return out


def canonical(raw: dict) -> bytes:
    return json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def bench_hash(raw: dict) -> str:
    return hashlib.sha256(canonical(raw)).hexdigest()


# ---------------------------------------------------------------- loading

def _err(where: str, msg: str) -> BenchError:
    return BenchError(f"{where}: {msg}" if where else msg)


def _int(v, where, low, high) -> int:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v != int(v):
        raise _err(where, f"must be a whole number, got {v!r}")
    v = int(v)
    if not low <= v <= high:
        raise _err(where, f"must be between {low} and {high}, got {v}")
    return v


def _num(v, where, low, high) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise _err(where, f"must be a number, got {v!r}")
    if not low <= v <= high:
        raise _err(where, f"must be between {low} and {high}, got {v}")
    return float(v) if not float(v).is_integer() else int(v)


def _keys(d: dict, where: str, allowed: set, required: set = frozenset()):
    if not isinstance(d, dict):
        raise _err(where, "must be an object")
    extra = set(d) - allowed
    if extra:
        raise _err(where, f"unknown field {sorted(extra)[0]!r} (allowed: {', '.join(sorted(allowed))})")
    missing = set(required) - set(d)
    if missing:
        raise _err(where, f"missing {sorted(missing)[0]!r}")


def _pin(v, where) -> str:
    if isinstance(v, int) and not isinstance(v, bool):
        v = f"D{v}"
    if not isinstance(v, str):
        raise _err(where, f"pin must look like D7 or A0, got {v!r}")
    p = v.strip().upper()
    if re.fullmatch(r"\d+", p):
        p = f"D{int(p)}"
    if p in ("D0", "D1"):
        raise _err(where, f"{p} carries Serial on the Uno; pick D2 to D13")
    if p not in DIGITAL_PINS and p not in ANALOG_PINS:
        raise _err(where, f"{v!r} is not an Uno pin; use D2 to D13 or A0 to A5")
    return p


def load(raw: Any) -> Bench:
    """Validate a bench (a dict, or JSON text) and lay its checks out on one timeline."""
    if isinstance(raw, (str, bytes)):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise BenchError(f"bench is not valid JSON: {exc}") from None
    if not isinstance(raw, dict):
        raise BenchError("bench must be a JSON object")
    raw = normalize(raw)
    _keys(raw, "bench", {"bench", "name", "board", "goal", "parts", "steps", "always", "unplugged"},
          {"name", "parts", "steps"})
    if raw.get("bench", BENCH_VERSION) != BENCH_VERSION:
        raise BenchError(f"bench version {raw.get('bench')!r} is not supported (this is version {BENCH_VERSION})")
    name = raw["name"]
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise BenchError("name: use lowercase letters, digits and dashes, like greenhouse-fan")
    if raw.get("board", "uno") != "uno":
        raise BenchError("board: only the Arduino Uno (ATmega328P) is supported")
    goal = raw.get("goal", "")
    if not isinstance(goal, str) or len(goal) > 500:
        raise BenchError("goal: a sentence of up to 500 characters")

    parts = _load_parts(raw["parts"])
    checks = []
    seen = set()
    steps = raw["steps"]
    if not isinstance(steps, list) or not 1 <= len(steps) <= 40:
        raise BenchError("steps: a list of 1 to 40 checks")
    always = raw.get("always", [])
    if not isinstance(always, list) or len(always) > 10:
        raise BenchError("always: a list of up to 10 checks")
    for i, s in enumerate(steps):
        checks.append(_load_check(s, f"steps[{i}]", parts, seen, always=False))
    for i, s in enumerate(always):
        checks.append(_load_check(s, f"always[{i}]", parts, seen, always=True))
    unplugged = _load_unplugged(raw.get("unplugged", {}), parts)

    # Lay the steps out on the timeline.
    t = 0
    for c in checks:
        if c.always:
            continue
        span = _span(c.expect)
        c.window = Window(t, t + span)
        t += span
    duration = t + TAIL_MS
    if duration > MAX_TIMELINE_MS:
        raise BenchError(f"the steps add up to {duration / 1000:.1f} s; keep the whole bench under "
                         f"{MAX_TIMELINE_MS // 1000} s")
    for c in checks:
        if c.always:
            start = c.expect.get("after_ms", 0)
            if start >= duration - 100:
                raise BenchError(f"{c.id}: after_ms {start} leaves no time to check (the bench runs {duration} ms)")
            c.window = Window(start, duration)
    bench = Bench(raw=raw, name=name, goal=goal, parts=parts, checks=checks, unplugged=unplugged,
                  duration_ms=duration)
    for c in checks:
        c.text = describe_check(bench, c)
    return bench


_NOTE_KEYS = {"description", "say", "comment", "comments", "note", "notes", "label", "why", "text"}
_ON = {"on", "high", "true", "1", "active"}
_OFF = {"off", "low", "false", "0", "inactive"}


def _plain_state(v):
    """'HIGH', True, 1 -> 'on'; 'LOW', False, 0 -> 'off'. Anything else is left for validation."""
    if isinstance(v, bool):
        return "on" if v else "off"
    if isinstance(v, (int, float)) and v in (0, 1):
        return "on" if v == 1 else "off"
    if isinstance(v, str) and v.strip().lower() in _ON | _OFF:
        return "on" if v.strip().lower() in _ON else "off"
    return v


def normalize(raw: dict) -> dict:
    """Forgive the near-misses models make (a "pins" object, "HIGH" for on, "within" for within_ms, notes)
    so a bench is rejected only for what it means, not how it is spelled. Returns a new dict; the bench's
    hash is taken over this normalised form."""
    import copy

    r = copy.deepcopy(raw)
    for k in _NOTE_KEYS & set(r):
        if k != "description":
            r.pop(k)
    if r.get("bench") in ("1", 1.0):
        r["bench"] = 1
    if isinstance(r.get("board"), str) and "uno" in r["board"].lower():
        r["board"] = "uno"
    if isinstance(r.get("name"), str):
        r["name"] = re.sub(r"[^a-z0-9-]+", "-", r["name"].strip().lower()).strip("-")[:48] or r["name"]
    if "description" in r and "goal" not in r:
        r["goal"] = r.pop("description")
    r.pop("description", None)
    parts = r.get("parts")
    types = {}
    if isinstance(parts, list):
        for p in parts:
            if not isinstance(p, dict):
                continue
            for k in _NOTE_KEYS & set(p):
                p.pop(k)
            if isinstance(p.get("pins"), dict):
                pins = p.pop("pins")
                roles = PART_TYPES.get(p.get("type"), PartType("output", ("pin",))).pins
                for role in roles:
                    if role in pins:
                        p.setdefault(role, pins[role])
                if roles == ("pin",) and "pin" not in p and len(pins) == 1:
                    p["pin"] = next(iter(pins.values()))
            if "initial" in p and "start" not in p:
                v = p.pop("initial")
                p["start"] = next(iter(v.values())) if isinstance(v, dict) and len(v) == 1 else v
            if isinstance(p.get("start"), dict) and len(p["start"]) == 1:
                p["start"] = next(iter(p["start"].values()))
            if isinstance(p.get("id"), str):
                types[p["id"]] = p.get("type")
    renames = {"within": "within_ms", "timeout_ms": "within_ms", "deadline_ms": "within_ms",
               "for": "for_ms", "duration_ms": "for_ms", "hold": "hold_ms", "tolerance_percent": "tolerance_pct"}
    for group in ("steps", "always"):
        for c in r.get(group) or []:
            if not isinstance(c, dict):
                continue
            for k in _NOTE_KEYS & set(c):
                c.pop(k)
            if isinstance(c.get("set"), dict):
                c["set"] = {pid: (next(iter(v.values())) if isinstance(v, dict) and len(v) == 1 else v)
                            for pid, v in c["set"].items()}
                if not c["set"]:
                    c.pop("set")
            e = c.get("expect")
            if isinstance(e, dict):
                for old, new in renames.items():
                    if old in e and new not in e:
                        e[new] = e.pop(old)
                ptype = types.get(e.get("part"))
                for kind in ("becomes", "stays"):
                    if kind in e and ptype in ONOFF_TYPES:
                        e[kind] = _plain_state(e[kind])
                    if kind in e and ptype == "buzzer" and e[kind] in ("off", "quiet", "none", False):
                        e[kind] = "silent"
    if isinstance(r.get("unplugged"), dict):
        for sid, states in r["unplugged"].items():
            if isinstance(states, dict):
                for oid, st in list(states.items()):
                    if types.get(oid) in ONOFF_TYPES:
                        states[oid] = _plain_state(st)
                    elif types.get(oid) == "buzzer" and st in ("off", "quiet", False):
                        states[oid] = "silent"
    return r


def _load_parts(raw_parts) -> dict:
    if not isinstance(raw_parts, list) or not 1 <= len(raw_parts) <= 16:
        raise BenchError("parts: a list of 1 to 16 parts")
    parts: dict = {}
    used: dict = {}
    for i, p in enumerate(raw_parts):
        where = f"parts[{i}]"
        if not isinstance(p, dict):
            raise _err(where, "must be an object")
        ptype = p.get("type")
        if ptype not in PART_TYPES:
            raise _err(where, f"unknown part type {ptype!r}; known: {', '.join(PART_TYPES)}")
        t = PART_TYPES[ptype]
        allowed = {"id", "type", "start", "active_low", "mode", "r0", "beta", "r_fixed", "wiring", *t.pins}
        _keys(p, where, allowed, {"id", "type", *t.pins})
        pid = p["id"]
        if not isinstance(pid, str) or not ID_RE.fullmatch(pid):
            raise _err(where, f"id {pid!r}: use lowercase letters, digits and underscores, like fan or temp_1")
        if pid in parts:
            raise _err(where, f"two parts are called {pid!r}")
        pins = {}
        for role in t.pins:
            pin = _pin(p[role], f"{pid}.{role}")
            if pin in used:
                raise _err(where, f"{pin} is already used by {used[pin]}")
            if t.analog and pin not in ANALOG_PINS:
                raise _err(where, f"a {ptype} is analog and must be on A0 to A5, not {pin}")
            used[pin] = pid
            pins[role] = pin
        params: dict = {}
        if "active_low" in p:
            if ptype != "relay" or not isinstance(p["active_low"], bool):
                raise _err(where, "active_low is true or false, and only for relays")
            params["active_low"] = p["active_low"]
        if "mode" in p:
            if ptype != "button" or p["mode"] not in ("pullup", "pulldown"):
                raise _err(where, "mode is pullup or pulldown, and only for buttons")
            params["mode"] = p["mode"]
        for k in ("r0", "beta", "r_fixed"):
            if k in p:
                if ptype != "ntc":
                    raise _err(where, f"{k} is only for an ntc thermistor")
                params[k] = _num(p[k], f"{pid}.{k}", 100, 1_000_000)
        if "wiring" in p:
            if ptype != "ntc" or p["wiring"] not in ("ntc_to_ground", "ntc_to_vcc"):
                raise _err(where, "wiring is ntc_to_ground or ntc_to_vcc, and only for an ntc thermistor")
            params["wiring"] = p["wiring"]
        part = {"id": pid, "type": ptype, "pins": pins, "params": params}
        if t.kind == "input":
            part["start"] = _input_value(ptype, p.get("start", t.default), f"{pid}.start")
        elif "start" in p:
            raise _err(where, "start is only for inputs")
        parts[pid] = part
    return parts


def _input_value(ptype: str, v, where):
    t = PART_TYPES[ptype]
    if ptype == "button":
        if not isinstance(v, bool):
            raise _err(where, f"a button is true (pressed) or false (released), got {v!r}")
        return v
    return _num(v, where, t.low, t.high)


ALWAYS_OK = {"stays", "blinks_hz", "serial", "brightness_pct"}


def _load_check(c, where, parts, seen, *, always: bool) -> Check:
    _keys(c, where, {"id", "set", "expect"} if not always else {"id", "expect"}, {"id", "expect"})
    cid = c["id"]
    if not isinstance(cid, str) or not ID_RE.fullmatch(cid):
        raise _err(where, f"id {cid!r}: use lowercase letters, digits and underscores")
    if cid in seen:
        raise _err(where, f"two checks are called {cid!r}")
    seen.add(cid)
    where = f"{where} ({cid})"
    sets = {}
    for pid, v in (c.get("set") or {}).items():
        if pid not in parts:
            raise _err(where, f"set names {pid!r}, which is not a part")
        if PART_TYPES[parts[pid]["type"]].kind != "input":
            raise _err(where, f"set can only change inputs; {pid} is an output")
        sets[pid] = _input_value(parts[pid]["type"], v, f"{where} set.{pid}")
    expect = _load_expect(c["expect"], where, parts, always)
    return Check(id=cid, expect=expect, set=sets, always=always)


def _state(ptype: str, v, where):
    """An output's expected state: on/off, or a measured value with a tolerance."""
    if ptype in ONOFF_TYPES:
        if v in ("on", "off"):
            return v
        if ptype == "led" and isinstance(v, dict) and set(v) <= {"brightness_pct", "tolerance"} and "brightness_pct" in v:
            return {"brightness_pct": _num(v["brightness_pct"], where, 0, 100),
                    "tolerance": _num(v.get("tolerance", 10), where + " tolerance", 1, 50)}
        raise _err(where, 'expected "on" or "off"' + (' or {"brightness_pct": 50}' if ptype == "led" else ""))
    if ptype == "buzzer":
        if v in ("off", "silent"):
            return "silent"
        if isinstance(v, dict) and "hz" in v and set(v) <= {"hz", "tolerance"}:
            return {"hz": _num(v["hz"], where, 31, 20000), "tolerance": _num(v.get("tolerance", 5), where, 1, 50)}
        raise _err(where, 'a buzzer is "silent" or {"hz": 440}')
    if ptype == "servo":
        if isinstance(v, dict) and "angle" in v and set(v) <= {"angle", "tolerance"}:
            return {"angle": _num(v["angle"], where, 0, 180), "tolerance": _num(v.get("tolerance", 5), where, 1, 45)}
        raise _err(where, 'a servo is {"angle": 90}')
    raise _err(where, f"a {ptype} is an input and has no state to check")


def _load_expect(e, where, parts, always) -> dict:
    if not isinstance(e, dict):
        raise _err(where, "expect must be an object")
    if "serial" in e:
        _keys(e, where + " expect", {"serial", "within_ms", "after_ms"}, {"serial"})
        text = e["serial"]
        if not isinstance(text, str) or not 1 <= len(text) <= 80 or any(ord(ch) < 32 for ch in text):
            raise _err(where, "serial: the text to look for, 1 to 80 printable characters")
        out = {"serial": text}
        if always:
            out["after_ms"] = _int(e.get("after_ms", 0), where + " after_ms", 0, MAX_TIMELINE_MS)
        else:
            out["within_ms"] = _int(e.get("within_ms", 1000), where + " within_ms", 10, 30_000)
        return out
    pid = e.get("part")
    if pid not in parts:
        raise _err(where, f"expect.part {pid!r} is not a part")
    ptype = parts[pid]["type"]
    if PART_TYPES[ptype].kind != "output":
        raise _err(where, f"{pid} is an input; checks watch outputs")
    kinds = [k for k in ("becomes", "stays", "blinks_hz") if k in e]
    if len(kinds) != 1:
        raise _err(where, "expect needs exactly one of becomes, stays or blinks_hz")
    kind = kinds[0]
    if always and kind == "becomes":
        raise _err(where, "an always-check can use stays or blinks_hz, not becomes")
    out: dict = {"part": pid}
    if kind == "blinks_hz":
        if ptype not in ONOFF_TYPES:
            raise _err(where, "blinks_hz is for LEDs, relays and digital outputs")
        _keys(e, where + " expect", {"part", "blinks_hz", "tolerance_pct", "for_ms", "after_ms"}, {"blinks_hz"})
        out["blinks_hz"] = _num(e["blinks_hz"], where + " blinks_hz", 0.1, 50)
        out["tolerance_pct"] = _num(e.get("tolerance_pct", 10), where + " tolerance_pct", 1, 50)
        if always:
            out["after_ms"] = _int(e.get("after_ms", 0), where + " after_ms", 0, MAX_TIMELINE_MS)
        else:
            low = max(500, math.ceil(2500 / out["blinks_hz"]))
            out["for_ms"] = _int(e.get("for_ms", max(3000, low)), where + " for_ms", low, 30_000)
        return out
    state = _state(ptype, e[kind], f"{where} {kind}")
    out[kind] = state
    if kind == "becomes":
        _keys(e, where + " expect", {"part", "becomes", "within_ms", "hold_ms"}, {"within_ms"})
        out["within_ms"] = _int(e["within_ms"], where + " within_ms", 1, 30_000)
        out["hold_ms"] = _int(e.get("hold_ms", DEFAULT_HOLD_MS), where + " hold_ms", 20, 10_000)
    else:
        allowed = {"part", "stays", "after_ms"} if always else {"part", "stays", "for_ms"}
        _keys(e, where + " expect", allowed, set() if always else {"for_ms"})
        if always:
            out["after_ms"] = _int(e.get("after_ms", 0), where + " after_ms", 0, MAX_TIMELINE_MS)
        else:
            out["for_ms"] = _int(e["for_ms"], where + " for_ms", 50, 30_000)
    return out


def _load_unplugged(u, parts) -> dict:
    if not isinstance(u, dict):
        raise BenchError("unplugged: an object like {\"temp\": {\"fan\": \"on\"}}")
    out = {}
    for sid, states in u.items():
        if sid not in parts or parts[sid]["type"] not in SENSOR_TYPES:
            sensors = [k for k, v in parts.items() if v["type"] in SENSOR_TYPES]
            what = "is not a part" if sid not in parts else f"is a {parts[sid]['type']}, which can't be unplugged"
            fix = (f"the sensors here are {', '.join(sensors)}" if sensors
                   else "this bench has none, so leave unplugged out")
            raise BenchError(f"unplugged: {sid!r} {what} (only {', '.join(sorted(SENSOR_TYPES))} can); {fix}")
        if not isinstance(states, dict) or not states:
            raise BenchError(f"unplugged.{sid}: name the outputs and the state they must fall back to")
        out[sid] = {}
        for oid, st in states.items():
            if oid not in parts or PART_TYPES[parts[oid]["type"]].kind != "output":
                raise BenchError(f"unplugged.{sid}: {oid!r} is not an output")
            out[sid][oid] = _state(parts[oid]["type"], st, f"unplugged.{sid}.{oid}")
    return out


def _span(expect: dict) -> int:
    if "serial" in expect:
        return expect["within_ms"]
    if "becomes" in expect:
        return expect["within_ms"] + expect["hold_ms"]
    return expect["for_ms"]


# ---------------------------------------------------------------- plain English

def fmt_ms(ms: float) -> str:
    if ms < 1000:
        return f"{int(round(ms))} ms"
    s = ms / 1000
    return f"{s:.0f} s" if abs(s - round(s)) < 1e-9 else f"{s:.1f} s".replace(".0 s", " s")


def fmt_num(v) -> str:
    if isinstance(v, float) and not v.is_integer():
        return f"{v:g}"
    return str(int(v))


def describe_input(part: dict, value) -> str:
    pid, ptype = part["id"], part["type"]
    if ptype == "button":
        return f"press {pid}" if value else f"release {pid}"
    if ptype in ("lm35", "ntc"):
        return f"{pid} reads {fmt_num(value)} °C"
    if ptype == "potentiometer":
        return f"turn {pid} to {fmt_num(value)} %"
    if ptype == "hcsr04":
        return f"object {fmt_num(value)} cm from {pid}" if value < 400 else f"nothing in front of {pid}"
    return f"{pid} = {value}"


def describe_state(state) -> str:
    if state == "on":
        return "on"
    if state == "off":
        return "off"
    if state == "silent":
        return "silent"
    if "angle" in state:
        return f"at {fmt_num(state['angle'])}° (±{fmt_num(state['tolerance'])}°)"
    if "hz" in state:
        return f"sounding {fmt_num(state['hz'])} Hz (±{fmt_num(state['tolerance'])} %)"
    if "brightness_pct" in state:
        return f"at {fmt_num(state['brightness_pct'])} % brightness (±{fmt_num(state['tolerance'])})"
    return str(state)


def state_noun(state) -> str:
    """The state on its own: 'on', 'silent', '90° (±5°)', '1000 Hz (±5 %)'."""
    d = describe_state(state)
    for lead in ("at ", "sounding "):
        if d.startswith(lead):
            return d[len(lead):]
    return d


def _verb(state, becomes: bool) -> str:
    if state in ("on", "off"):
        return ("turns " if becomes else "stays ") + state
    if state == "silent":
        return "goes silent" if becomes else "stays silent"
    if "angle" in state:
        return ("moves to " if becomes else "stays at ") + describe_state(state)[3:]
    return ("is " if becomes else "stays ") + describe_state(state)


def describe_expect(expect: dict, always: bool = False) -> str:
    if "serial" in expect:
        if always:
            return f'prints "{expect["serial"]}" on Serial'
        return f'prints "{expect["serial"]}" on Serial within {fmt_ms(expect["within_ms"])}'
    pid = expect["part"]
    if "blinks_hz" in expect:
        s = f"{pid} blinks at {fmt_num(expect['blinks_hz'])} Hz (±{fmt_num(expect['tolerance_pct'])} %)"
        return s if always else s + f" for {fmt_ms(expect['for_ms'])}"
    if "becomes" in expect:
        return f"{pid} {_verb(expect['becomes'], True)} within {fmt_ms(expect['within_ms'])}"
    s = f"{pid} {_verb(expect['stays'], False)}"
    return s if always else s + f" for {fmt_ms(expect['for_ms'])}"


def describe_check(bench: Bench, c: Check) -> str:
    if c.always:
        after = c.expect.get("after_ms", 0)
        lead = "the whole time" if not after else f"from {fmt_ms(after)} on"
        return f"{lead}, {describe_expect(c.expect, True)}"
    cause = ", ".join(describe_input(bench.parts[pid], v) for pid, v in c.set.items())
    if not cause:
        cause = "at power-on" if c.window and c.window.start_ms == 0 else "with nothing changed"
    return f"{cause} → {describe_expect(c.expect)}"


def describe(bench: Bench) -> list:
    """Numbered plain-English lines: what a person approves before any firmware is judged."""
    lines = []
    n = 0
    for c in bench.checks:
        if not c.always:
            n += 1
            lines.append(f"{n}. {c.text}")
    for c in bench.checks:
        if c.always:
            n += 1
            lines.append(f"{n}. {c.text[0].upper()}{c.text[1:]}")
    for sid, states in bench.unplugged.items():
        n += 1
        falls = ", ".join(f"{oid} {describe_state(st)}" for oid, st in states.items())
        lines.append(f"{n}. If {sid} is unplugged → {falls} (fail safe)")
    return lines


def wiring(bench: Bench) -> list:
    """One line per part: what is wired to which pin."""
    out = []
    for p in bench.parts.values():
        t = PART_TYPES[p["type"]]
        pins = ", ".join(f"{role} {pin}" if role != "pin" else pin for role, pin in p["pins"].items())
        extra = " (active low)" if p["params"].get("active_low") else ""
        out.append(f"{p['id']}: {p['type']} on {pins}{extra}")
    return out
