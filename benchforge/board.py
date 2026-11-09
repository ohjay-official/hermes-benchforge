"""Draw the bench as an animated GIF: the Uno, every part wired to its pin, and the run playing back.

Everything comes from the recorded trace, so the animation shows exactly what the firmware did on
the simulated chip: the fan relay clicking on when the temperature passes 30 °C, the LED blinking,
the servo swinging, and each check ticking green or red as its step plays.
"""

from __future__ import annotations

import math

from .checks import Trace
from .gif import Canvas, write_gif
from .spec import PART_TYPES, fmt_ms

W, H = 640, 360

PALETTE = [
    (13, 17, 23),     # 0 background
    (22, 27, 34),     # 1 card
    (48, 54, 61),     # 2 card border / dim lines
    (230, 237, 243),  # 3 text
    (125, 133, 144),  # 4 dim text
    (0, 129, 138),    # 5 Uno teal
    (0, 95, 102),     # 6 Uno shade
    (33, 38, 45),     # 7 chip
    (190, 198, 206),  # 8 pin silver
    (63, 185, 80),    # 9 green
    (248, 81, 73),    # 10 red
    (210, 153, 34),   # 11 amber
    (255, 92, 92),    # 12 LED on
    (80, 30, 30),     # 13 LED off
    (255, 170, 170),  # 14 LED glow
    (88, 166, 255),   # 15 blue wire
    (240, 136, 62),   # 16 orange wire
    (188, 140, 255),  # 17 purple wire
    (227, 179, 65),   # 18 yellow wire
    (57, 197, 207),   # 19 cyan accent
    (255, 255, 255),  # 20 white
    (35, 134, 54),    # 21 dark green
    (110, 40, 36),    # 22 dark red
    (16, 64, 70),     # 23 deep teal
]
BG, CARD, LINE, TEXT, DIM, UNO, UNO2, CHIP, PIN = 0, 1, 2, 3, 4, 5, 6, 7, 8
GREEN, RED, AMBER, LEDON, LEDOFF, GLOW, CYAN, WHITE, DKGREEN, DKRED, DEEP = 9, 10, 11, 12, 13, 14, 19, 20, 21, 22, 23
WIRES = [16, 15, 17, 18, 19, 9]

SHORT = {"lm35": "LM35", "ntc": "NTC", "potentiometer": "pot", "hcsr04": "HC-SR04", "relay": "relay",
         "led": "LED", "buzzer": "buzzer", "servo": "servo", "button": "button", "digital_out": "output"}

BOARD = (24, 92, 250, 176)          # x, y, w, h
CARD_X, CARD_W, CARD_H = 330, 290, 46


def _pin_xy(pin: str):
    """Where a pin sits on the drawn Uno: digital header along the top, analog along the bottom."""
    bx, by, bw, bh = BOARD
    if pin.startswith("D"):
        n = int(pin[1:])
        return bx + bw - 18 - (13 - n) * 15, by + 10
    n = int(pin[1:])
    return bx + bw - 18 - (5 - n) * 15, by + bh - 10


def _draw_board(c: Canvas, used: dict) -> None:
    bx, by, bw, bh = BOARD
    c.round_rect(bx, by, bw, bh, 10, UNO)
    c.round_rect(bx + 3, by + bh - 8, bw - 6, 5, 2, UNO2)
    c.rect(bx - 10, by + 30, 30, 34, PIN)                 # USB
    c.rect(bx - 6, by + 112, 22, 30, CHIP)                # power jack
    c.rect(bx + 70, by + 72, 112, 34, CHIP)               # the ATmega328P
    for i in range(14):
        c.rect(bx + 74 + i * 7.6, by + 68, 3, 4, PIN)
        c.rect(bx + 74 + i * 7.6, by + 106, 3, 4, PIN)
    c.text(bx + 84, by + 82, "ATmega328P", DIM)
    c.text(bx + 18, by + 120, "UNO", WHITE, "big")
    c.text(bx + 20, by + 146, "16 MHz", DEEP)
    for n in range(2, 14):
        x, y = _pin_xy(f"D{n}")
        on = f"D{n}" in used
        c.rect(x - 4, y - 4, 9, 9, WIRES[used[f'D{n}'] % len(WIRES)] if on else CHIP)
        if on:
            label = f"D{n}"
            c.text(x - Canvas.text_width(label) // 2 + 1, y + 7, label, WHITE)
    for n in range(6):
        x, y = _pin_xy(f"A{n}")
        on = f"A{n}" in used
        c.rect(x - 4, y - 4, 9, 9, WIRES[used[f'A{n}'] % len(WIRES)] if on else CHIP)
        if on:
            label = f"A{n}"
            c.text(x - Canvas.text_width(label) // 2 + 1, y - 21, label, WHITE)


def _wire(c: Canvas, pin: str, card_y: int, color: int, slot: int) -> None:
    px, py = _pin_xy(pin)
    tx, ty = CARD_X - 2, card_y + CARD_H // 2
    lane = BOARD[0] + BOARD[2] + 14 + slot * 6
    if pin.startswith("D"):
        c.line(px, py, px, BOARD[1] - 10 - slot * 3, color, 2)
        c.line(px, BOARD[1] - 10 - slot * 3, lane, BOARD[1] - 10 - slot * 3, color, 2)
        top = BOARD[1] - 10 - slot * 3
    else:
        c.line(px, py, px, BOARD[1] + BOARD[3] + 10 + slot * 3, color, 2)
        c.line(px, BOARD[1] + BOARD[3] + 10 + slot * 3, lane, BOARD[1] + BOARD[3] + 10 + slot * 3, color, 2)
        top = BOARD[1] + BOARD[3] + 10 + slot * 3
    c.line(lane, top, lane, ty, color, 2)
    c.line(lane, ty, tx, ty, color, 2)


def _fan(c: Canvas, cx: int, cy: int, angle: float, on: bool) -> None:
    c.ring(cx, cy, 16, GREEN if on else LINE, 2)
    for k in range(3):
        a = angle + k * 2 * math.pi / 3
        for r in range(3, 13):
            w = 1 + r // 4
            x, y = cx + r * math.cos(a), cy + r * math.sin(a)
            c.rect(round(x - w / 2), round(y - w / 2), w, w, WHITE if on else DIM)
    c.circle(cx, cy, 3, CARD)


def _card(c: Canvas, part: dict, y: int, state: dict, color: int, t_frame: int) -> None:
    c.round_rect(CARD_X, y, CARD_W, CARD_H, 8, CARD)
    c.rect(CARD_X, y + 8, 3, CARD_H - 16, color)
    pid, ptype = part["id"], part["type"]
    pins = "/".join(part["pins"].values())
    c.text(CARD_X + 62, y + 7, pid, TEXT)
    c.text(CARD_X + 62, y + 25, f"{SHORT.get(ptype, ptype)} · {pins}", DIM)
    ix, iy = CARD_X + 30, y + CARD_H // 2
    vx = CARD_X + CARD_W - 12
    value, vcolor = "", TEXT
    if ptype in ("led",):
        a = state.get("active")
        if a is None:
            c.circle(ix, iy, 11, LINE)
            value, vcolor = "not set", AMBER
        elif a >= 0.5 or 0 < a < 0.5 and state.get("pwm"):
            if a >= 0.5:
                c.circle(ix, iy, 15, GLOW if a > 0.9 else LEDOFF)
            c.circle(ix, iy, 10, LEDON if a > 0.25 else LEDOFF)
            value, vcolor = ("ON" if not state.get("pwm") else f"{a * 100:.0f} %"), LEDON
        else:
            c.circle(ix, iy, 10, LEDOFF)
            value, vcolor = "off", DIM
    elif ptype in ("relay", "digital_out"):
        a = state.get("active")
        on = a is not None and a >= 0.5
        if "fan" in pid:
            _fan(c, ix, iy, state.get("spin", 0.0), on)
        else:
            c.round_rect(ix - 14, iy - 12, 28, 24, 4, DKGREEN if on else CHIP)
            c.line(ix - 8, iy + 5, ix + 8, iy - (6 if on else 0), WHITE if on else DIM, 2)
        if a is None:
            value, vcolor = "not set", AMBER
        else:
            value, vcolor = ("ON" if on else "off"), (GREEN if on else DIM)
        if state.get("clicks", 0) >= 3:
            value, vcolor = f"{state['clicks']} clicks!", RED
    elif ptype == "buzzer":
        hz = state.get("hz", 0)
        c.round_rect(ix - 12, iy - 8, 12, 16, 2, PIN)
        if hz >= 20:
            for r in (8, 13, 18):
                for k in range(-r // 2, r // 2 + 1):
                    c.set(ix + 2 + int(math.sqrt(max(0, r * r - k * k)) * 0.6), iy + k, AMBER)
            value, vcolor = f"{hz:.0f} Hz", AMBER
        else:
            value, vcolor = "silent", DIM
    elif ptype == "servo":
        ang = state.get("angle", -1)
        c.round_rect(ix - 15, iy - 6, 30, 16, 3, CHIP)
        if ang >= 0:
            a = math.radians(180 - ang)
            c.line(ix, iy, ix + 18 * math.cos(a), iy - 18 * math.sin(a), WHITE, 3)
            c.circle(ix, iy, 4, PIN)
            value = f"{ang:.0f}°"
        else:
            value, vcolor = "no signal", AMBER
    elif ptype in ("lm35", "ntc"):
        v = state.get("value", 0)
        c.round_rect(ix - 4, iy - 16, 9, 26, 4, LINE)
        fill = max(0.0, min(1.0, (v - 0) / 50))
        hot = RED if v >= 30 else (AMBER if v >= 25 else CYAN)
        c.rect(ix - 2, iy + 8 - int(22 * fill), 5, int(22 * fill), hot)
        c.circle(ix + 0.5, iy + 12, 6, hot)
        value, vcolor = (f"{v:.1f} °C", hot) if not state.get("unplugged") else ("unplugged", RED)
    elif ptype == "potentiometer":
        v = state.get("value", 0)
        c.circle(ix, iy, 13, PIN)
        c.circle(ix, iy, 10, CHIP)
        a = math.radians(225 - 270 * v / 100)
        c.line(ix, iy, ix + 9 * math.cos(a), iy - 9 * math.sin(a), WHITE, 2)
        value = f"{v:.0f} %" if not state.get("unplugged") else "unplugged"
        vcolor = TEXT if not state.get("unplugged") else RED
    elif ptype == "button":
        pressed = bool(state.get("value"))
        c.round_rect(ix - 13, iy - 13, 26, 26, 4, PIN)
        c.circle(ix, iy + (2 if pressed else 0), 8, RED if pressed else DKRED)
        value, vcolor = ("pressed", AMBER) if pressed else ("released", DIM)
    elif ptype == "hcsr04":
        cm = state.get("value", 400)
        c.round_rect(ix - 18, iy - 9, 36, 18, 3, CYAN)
        c.circle(ix - 9, iy, 6, CHIP)
        c.circle(ix + 9, iy, 6, CHIP)
        value = "unplugged" if state.get("unplugged") else (f"{cm:.0f} cm" if cm < 400 else "clear")
        vcolor = RED if state.get("unplugged") else (AMBER if cm < 40 else TEXT)
    c.text(vx - Canvas.text_width(value, "big"), y + 13, value, vcolor, "big")


def _pwm_parts(bench, trace: Trace) -> dict:
    """LEDs driven with analogWrite: most milliseconds are partly on. A digital LED that merely switched
    mid-millisecond a few times is not PWM."""
    out = {}
    for pid, part in bench.parts.items():
        duty = trace.pin(pid).get("duty") if part["type"] == "led" else None
        if duty:
            frac = sum(1 for d in duty if 0.02 < d < 0.98)
            out[pid] = frac > 0.3 * len(duty)
    return out


def _states(bench, trace: Trace, t: int, prev_t: int, spins: dict, pwm: dict) -> dict:
    out = {}
    for pid, part in bench.parts.items():
        ptype = part["type"]
        st: dict = {}
        if ptype in ("led", "relay", "digital_out"):
            act = trace.active(pid)
            st["active"] = act[t]
            st["pwm"] = pwm.get(pid, False)
            if ptype == "relay":
                edges = [us for us, _v in trace.pin(pid)["edges"] if prev_t * 1000 < us <= t * 1000]
                st["clicks"] = len(edges)
            if st["active"] is not None and st["active"] >= 0.5:
                spins[pid] = spins.get(pid, 0.0) + 0.9
            st["spin"] = spins.get(pid, 0.0)
        elif ptype == "buzzer":
            st["hz"] = trace.signal(pid, "hz")[t]
        elif ptype == "servo":
            st["angle"] = trace.signal(pid, "angle")[t]
        else:
            sig = PART_TYPES[ptype].signal
            st["value"] = trace.signal(pid, sig)[t]
        out[pid] = st
    return out


def render(bench, run: dict, results: list, *, title: str, verdict_ok: bool, score: str,
           unplugged_at: dict | None = None, max_frames: int = 120) -> bytes:
    """The main run as a GIF. ``results`` are that run's check results (to colour each step)."""
    trace = Trace(bench, run)
    dur = run["duration_ms"]
    step_ms = max(100, int(math.ceil(dur / max_frames / 100.0)) * 100)
    speed = step_ms / 100
    by_id = {r.id: r for r in results}
    used = {}
    for i, p in enumerate(bench.parts.values()):
        for pin in p["pins"].values():
            used[pin] = i

    base = Canvas(W, H, BG)
    base.text(20, 12, "BenchForge", CYAN, "big")
    base.text(20 + Canvas.text_width("BenchForge ", "big"), 17, bench.name, DIM)
    badge = f"{'✓' if verdict_ok else '✗'} {score}"
    bw = Canvas.text_width(badge, "big") + 20
    base.round_rect(W - bw - 20, 9, bw, 28, 8, DKGREEN if verdict_ok else DKRED)
    base.text(W - bw - 10, 12, badge, WHITE, "big")
    base.text(W - bw - 20 - Canvas.text_width(title) - 10, 17, title, DIM)
    _draw_board(base, used)
    parts = list(bench.parts.values())[:5]
    gap = 6 if len(parts) > 4 else 10
    ys = [52 + i * (CARD_H + gap) for i in range(len(parts))]
    for i, (p, y) in enumerate(zip(parts, ys)):
        for slot, pin in enumerate(p["pins"].values()):
            _wire(base, pin, y, WIRES[i % len(WIRES)], i + slot)
    # timeline with one segment per step, coloured by its result
    tl_x, tl_y, tl_w = 20, 314, W - 40
    base.rect(tl_x, tl_y, tl_w, 6, LINE)
    for c in bench.steps:
        r = by_id.get(c.id)
        x0 = tl_x + tl_w * c.window.start_ms / dur
        x1 = tl_x + tl_w * c.window.end_ms / dur
        base.rect(x0 + 1, tl_y, max(1, x1 - x0 - 2), 6, (GREEN if r.passed else RED) if r else DIM)

    frames, spins = [], {}
    pwm = _pwm_parts(bench, trace)
    serial = run.get("serial", {}).get("lines", [])
    prev = 0
    for t in range(0, dur, step_ms):
        c = base.copy()
        states = _states(bench, trace, min(t, dur - 1), prev, spins, pwm)
        for sid, when in (unplugged_at or {}).items():
            if t >= when and sid in states:
                states[sid]["unplugged"] = True
        for i, (p, y) in enumerate(zip(parts, ys)):
            _card(c, p, y, states[p["id"]], WIRES[i % len(WIRES)], t)
        last = next((txt for at, txt in reversed(serial) if at <= t), None)
        if last is not None:
            shown = "".join(ch if 32 <= ord(ch) < 127 else "?" for ch in last)[:52]
            c.text(20, 290, "Serial: " + shown, DIM)
        x = tl_x + tl_w * t / dur
        c.rect(x - 1, tl_y - 5, 3, 16, WHITE)
        clock = f"{t / 1000:.1f} s" + (f"  ×{speed:g}" if speed > 1 else "")
        c.text(tl_x, tl_y + 14, clock, DIM)
        step = next((s for s in bench.steps if s.window.start_ms <= t < s.window.end_ms), None)
        if step is not None:
            n = bench.steps.index(step) + 1
            r = by_id.get(step.id)
            mark = "" if r is None else (" ✓" if r.passed else " ✗")
            line = f"{n}. {step.text}"
            room = (W - 40 - Canvas.text_width(clock) - 20) // 7 - 3
            if len(line) > room:
                line = line[:room - 1] + "…"
            lx = tl_x + Canvas.text_width(clock) + 20
            end = c.text(lx, tl_y + 14, line, TEXT)
            if mark:
                c.text(end, tl_y + 14, mark, GREEN if r.passed else RED)
        frames.append(c.px)
        prev = t
    delays = [10] * len(frames)
    if delays:
        delays[-1] = 200
    return write_gif(frames, PALETTE, delays, W, H)


def render_report(report, build_ok: bool = True) -> bytes | None:
    """The animation for a whole bench report: its main run, coloured by that run's results."""
    main = report.main
    if main.trace is None or main.error:
        return None
    good, total = report.counts()
    return render(report.bench, main.trace, main.results, title="simulated run",
                  verdict_ok=report.passed, score=f"{good}/{total}")
