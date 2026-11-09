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

class Layout:
    """Where things go. Portrait and phone first: the Uno on top, one card per part under it, then the
    timeline and the step being checked in large type. Drawn 400 px wide and doubled, so text stays sharp
    after a chat app re-encodes the animation and shrinks it to a phone's width."""

    W = 400
    CARD_X, CARD_W, CARD_H, GAP = 36, 350, 46, 8
    LANES = 7                                              # wire lanes in the left gutter

    def __init__(self, n_parts: int):
        self.board = (80, 80, 250, 176)                    # x, y, w, h
        self.cards_y = 288
        self.cards_end = self.cards_y + n_parts * (self.CARD_H + self.GAP) - self.GAP
        self.serial_y = self.cards_end + 10
        self.tl_y = self.cards_end + 38
        self.step_y = self.tl_y + 32
        self.H = self.step_y + 50

    def card_y(self, i: int) -> int:
        return self.cards_y + i * (self.CARD_H + self.GAP)

    def pin_xy(self, pin: str):
        """Where a pin sits on the drawn Uno: digital header along the top, analog along the bottom."""
        bx, by, bw, bh = self.board
        n = int(pin[1:])
        if pin.startswith("D"):
            return bx + bw - 18 - (13 - n) * 15, by + 10
        return bx + bw - 18 - (5 - n) * 15, by + bh - 10


def _wrap(text: str, per_line: int, lines: int) -> list:
    """Split on spaces into at most ``lines`` lines; the last one ends in … if text is left over."""
    words, out, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + (1 if cur else 0) <= per_line:
            cur = f"{cur} {w}" if cur else w
            continue
        if cur:
            out.append(cur)
        cur = w[:per_line]
        if len(out) == lines:
            break
    if len(out) < lines and cur:
        out.append(cur)
        cur = ""
    if len(out) > lines or (len(out) == lines and cur):
        out = out[:lines]
        last = out[-1]
        out[-1] = (last[:per_line - 1] if len(last) >= per_line else last) + "…"
    return out


def _draw_board(c: Canvas, L: Layout, used: dict) -> None:
    bx, by, bw, bh = L.board
    c.round_rect(bx, by, bw, bh, 10, UNO)
    c.round_rect(bx + 3, by + bh - 8, bw - 6, 5, 2, UNO2)
    c.rect(bx - 10, by + 30, 30, 34, PIN)                 # USB
    c.rect(bx - 6, by + 112, 22, 30, CHIP)                # power jack
    c.rect(bx + 70, by + 72, 112, 34, CHIP)               # the ATmega328P
    for i in range(14):
        c.rect(bx + 74 + i * 7.6, by + 68, 3, 4, PIN)
        c.rect(bx + 74 + i * 7.6, by + 106, 3, 4, PIN)
    c.text(bx + 84, by + 82, "ATmega328P", DIM)
    c.text(bx + 26, by + 120, "UNO", WHITE, "big")
    c.text(bx + 26, by + 146, "16 MHz", DEEP)
    for prefix, pins, dy in (("D", range(2, 14), 7), ("A", range(6), -21)):
        row = 0
        for n in pins:
            pin = f"{prefix}{n}"
            x, y = L.pin_xy(pin)
            on = pin in used
            c.rect(x - 4, y - 4, 9, 9, WIRES[used[pin] % len(WIRES)] if on else CHIP)
            if not on:
                row = 0
                continue
            # neighbouring pins are 15 px apart: stagger their labels so "D9" and "D10" don't run together
            c.text(x - Canvas.text_width(pin) // 2 + 1, y + dy + (row * 15 if dy > 0 else -row * 15), pin, WHITE)
            row = 1 - row


def _wire(c: Canvas, L: Layout, pin: str, card_y: int, color: int, slot: int) -> None:
    """Pin → out of the board → down the left gutter → into the part's card."""
    px, py = L.pin_xy(pin)
    bx, by, bw, bh = L.board
    ty = card_y + L.CARD_H // 2
    slot = slot % L.LANES
    lane = 6 + slot * 4
    exit_y = by - 8 - slot * 3 if pin.startswith("D") else by + bh + 8 + slot * 3
    c.line(px, py, px, exit_y, color, 2)
    c.line(px, exit_y, lane, exit_y, color, 2)
    c.line(lane, exit_y, lane, ty, color, 2)
    c.line(lane, ty, L.CARD_X - 2, ty, color, 2)


def _fan(c: Canvas, cx: int, cy: int, angle: float, on: bool) -> None:
    c.ring(cx, cy, 16, GREEN if on else LINE, 2)
    for k in range(3):
        a = angle + k * 2 * math.pi / 3
        for r in range(3, 13):
            w = 1 + r // 4
            x, y = cx + r * math.cos(a), cy + r * math.sin(a)
            c.rect(round(x - w / 2), round(y - w / 2), w, w, WHITE if on else DIM)
    c.circle(cx, cy, 3, CARD)


def _card(c: Canvas, L: Layout, part: dict, y: int, state: dict, color: int) -> None:
    CARD_X, CARD_W, CARD_H = L.CARD_X, L.CARD_W, L.CARD_H
    c.round_rect(CARD_X, y, CARD_W, CARD_H, 8, CARD)
    c.rect(CARD_X, y + 8, 3, CARD_H - 16, color)
    pid, ptype = part["id"], part["type"]
    pins = "/".join(part["pins"].values())
    c.text(CARD_X + 62, y + 4, pid, TEXT, "big")
    c.text(CARD_X + 62, y + 27, f"{SHORT.get(ptype, ptype)} · {pins}", DIM)
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
           unplugged_at: dict | None = None, max_frames: int = 120, scale: int = 2,
           crash_failures: list | None = None) -> bytes:
    """The main run as a GIF. ``results`` are that run's check results (to colour each step);
    ``crash_failures`` names the crash tests that failed, for the closing frame."""
    crash_failures = crash_failures or []
    trace = Trace(bench, run)
    dur = run["duration_ms"]
    step_ms = max(100, int(math.ceil(dur / max_frames / 100.0)) * 100)
    speed = step_ms / 100
    by_id = {r.id: r for r in results}
    used = {}
    for i, p in enumerate(bench.parts.values()):
        for pin in p["pins"].values():
            used[pin] = i

    parts = list(bench.parts.values())[:6]
    L = Layout(len(parts))
    W, H = L.W, L.H
    base = Canvas(W, H, BG)
    base.text(14, 12, "BenchForge", CYAN, "big")
    sub = f"{bench.name} · {title}"
    base.text(14, 38, sub if len(sub) <= 52 else sub[:51] + "…", DIM)
    badge = f"{'✓' if verdict_ok else '✗'} {score}"
    bw = Canvas.text_width(badge, "big") + 20
    base.round_rect(W - bw - 14, 12, bw, 28, 8, DKGREEN if verdict_ok else DKRED)
    base.text(W - bw - 4, 15, badge, WHITE, "big")
    _draw_board(base, L, used)
    ys = [L.card_y(i) for i in range(len(parts))]
    slot = 0
    for i, (p, y) in enumerate(zip(parts, ys)):
        for pin in p["pins"].values():
            _wire(base, L, pin, y, WIRES[i % len(WIRES)], slot)
            slot += 1
    # the timeline: one segment per step, coloured by its result
    tl_x, tl_y, tl_w = 14, L.tl_y, W - 28
    base.rect(tl_x, tl_y, tl_w, 6, LINE)
    for c in bench.steps:
        r = by_id.get(c.id)
        x0 = tl_x + tl_w * c.window.start_ms / dur
        x1 = tl_x + tl_w * c.window.end_ms / dur
        base.rect(x0 + 1, tl_y, max(1, x1 - x0 - 2), 6, (GREEN if r.passed else RED) if r else DIM)

    frames, spins = [], {}
    pwm = _pwm_parts(bench, trace)
    serial = run.get("serial", {}).get("lines", [])
    per_line = (W - 28) // Canvas.text_width("x", "big")
    prev = 0
    for t in range(0, dur, step_ms):
        c = base.copy()
        states = _states(bench, trace, min(t, dur - 1), prev, spins, pwm)
        for sid, when in (unplugged_at or {}).items():
            if t >= when and sid in states:
                states[sid]["unplugged"] = True
        for i, (p, y) in enumerate(zip(parts, ys)):
            _card(c, L, p, y, states[p["id"]], WIRES[i % len(WIRES)])
        last = next((txt for at, txt in reversed(serial) if at <= t), None)
        if last is not None:
            shown = "".join(ch if 32 <= ord(ch) < 127 else "?" for ch in last)
            room = (W - 28) // Canvas.text_width("x") - 8
            c.text(14, L.serial_y, "Serial: " + (shown if len(shown) <= room else shown[:room - 1] + "…"), DIM)
        x = tl_x + tl_w * t / dur
        c.rect(x - 1, tl_y - 5, 3, 16, WHITE)
        clock = f"{t / 1000:.1f} s" + (f"  ×{speed:g}" if speed > 1 else "")
        c.text(W - 14 - Canvas.text_width(clock), tl_y + 12, clock, DIM)
        step = next((s for s in bench.steps if s.window.start_ms <= t < s.window.end_ms), None)
        if step is not None:
            n = bench.steps.index(step) + 1
            r = by_id.get(step.id)
            end = c.text(tl_x, tl_y + 12, f"Check {n} of {len(bench.steps)}", DIM)
            if r is not None:
                c.text(end + 7, tl_y + 12, "✓ pass" if r.passed else "✗ FAIL", GREEN if r.passed else RED)
            for k, line in enumerate(_wrap(step.text, per_line, 2)):
                c.text(tl_x, L.step_y + k * 23, line, TEXT, "big")
        elif bench.steps and t >= bench.steps[-1].window.end_ms:
            # the tail of the run, which the last frame holds: say how it went
            failed = [s for s in bench.steps if by_id.get(s.id) is not None and not by_id[s.id].passed]
            c.text(tl_x, tl_y + 12, "Result", DIM)
            if failed:
                c.text(tl_x, L.step_y, f"✗ Check {bench.steps.index(failed[0]) + 1} failed", RED, "big")
                c.text(tl_x, L.step_y + 23, _wrap(failed[0].text, per_line, 1)[0], TEXT, "big")
            else:
                c.text(tl_x, L.step_y, f"✓ All {len(bench.steps)} checks passed", GREEN, "big")
                if crash_failures:
                    first = crash_failures[0].split(" (")[0]           # "Bouncy button (5 ms ...)" → "Bouncy button"
                    more = f" +{len(crash_failures) - 1} more" if len(crash_failures) > 1 else ""
                    why = f"✗ Failed: {first}{more}"
                    c.text(tl_x, L.step_y + 23, _wrap(why, per_line, 1)[0], RED, "big")
                else:
                    c.text(tl_x, L.step_y + 23, "on the simulated chip", TEXT, "big")
        frames.append(c.px)
        prev = t
    delays = [10] * len(frames)
    if delays:
        delays[-1] = 200
    if scale == 2:
        frames = [_double(f, W, H) for f in frames]
    return write_gif(frames, PALETTE, delays, W * scale, H * scale)


def _double(px: bytearray, w: int, h: int) -> bytearray:
    """Nearest-neighbour 2x: chat apps re-encode animations, and crisp double-size pixels survive it."""
    out = bytearray(w * h * 4)
    row2 = bytearray(w * 2)
    for y in range(h):
        row = px[y * w:(y + 1) * w]
        row2[0::2] = row
        row2[1::2] = row
        o = y * 4 * w
        out[o:o + 2 * w] = row2
        out[o + 2 * w:o + 4 * w] = row2
    return out


def render_report(report, build_ok: bool = True) -> bytes | None:
    """The animation for a whole bench report: its main run, coloured by that run's results."""
    main = report.main
    if main.trace is None or main.error:
        return None
    good, total = report.counts()
    crashes = [run.title for run in report.runs[1:] if not run.passed and not run.skipped]
    return render(report.bench, main.trace, main.results, title="simulated run",
                  verdict_ok=report.passed, score=f"{good}/{total}", crash_failures=crashes)
