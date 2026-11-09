// BenchForge simulation engine.
//
// Runs a compiled Arduino Uno program (Intel HEX) instruction by instruction on avr8js's ATmega328P,
// with simulated parts wired to its pins, and records what every watched pin and part did.
// Deterministic: no wall clock, no Math.random (a seeded generator drives sensor noise).
'use strict';

const avr = require('./vendor/avr8js');

const F_CPU = 16e6;
const CYCLES_PER_MS = F_CPU / 1000;
const MAX_EDGES_PER_PIN = 20000;

// Uno pin names -> [port, bit, adc channel or null]
const UNO_PINS = {};
for (let i = 0; i <= 7; i++) UNO_PINS[`D${i}`] = ['D', i, null];
for (let i = 8; i <= 13; i++) UNO_PINS[`D${i}`] = ['B', i - 8, null];
for (let i = 0; i <= 5; i++) {
  UNO_PINS[`A${i}`] = ['C', i, i];
  UNO_PINS[`D${14 + i}`] = ['C', i, i];
}

function normalizePin(name) {
  const p = String(name).trim().toUpperCase();
  if (/^\d+$/.test(p)) return `D${Number(p)}`;
  if (!(p in UNO_PINS)) throw new Error(`Unknown Uno pin "${name}". Use D0-D13 or A0-A5.`);
  return p;
}

function loadHex(source, target) {
  let base = 0;
  let sawData = false;
  for (const raw of String(source).split(/\r?\n/)) {
    const line = raw.trim();
    if (!line) continue;
    if (line[0] !== ':') throw new Error('Not an Intel HEX file');
    const count = parseInt(line.substr(1, 2), 16);
    const addr = parseInt(line.substr(3, 4), 16);
    const type = parseInt(line.substr(7, 2), 16);
    let sum = 0;
    for (let i = 1; i < line.length - 1; i += 2) sum = (sum + parseInt(line.substr(i, 2), 16)) & 0xff;
    if (sum !== 0) throw new Error('Intel HEX checksum mismatch');
    if (type === 0) {
      for (let i = 0; i < count; i++) {
        const at = base + addr + i;
        if (at >= target.length) throw new Error('Program does not fit in 32 KB of flash');
        target[at] = parseInt(line.substr(9 + i * 2, 2), 16);
      }
      sawData = true;
    } else if (type === 2) {
      base = parseInt(line.substr(9, 4), 16) * 16;
    } else if (type === 4) {
      base = parseInt(line.substr(9, 4), 16) * 65536;
    }
  }
  if (!sawData) throw new Error('Intel HEX file has no data');
}

// Small deterministic PRNG (mulberry32).
function prng(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// ---------------------------------------------------------------- parts

const PART_TYPES = {
  led: { pins: ['pin'], signals: [], output: true },
  relay: { pins: ['pin'], signals: [], output: true },
  digital_out: { pins: ['pin'], signals: [], output: true },
  buzzer: { pins: ['pin'], signals: ['hz'], output: true },
  servo: { pins: ['pin'], signals: ['angle'], output: true },
  button: { pins: ['pin'], signals: ['pressed'], input: true },
  potentiometer: { pins: ['pin'], signals: ['percent'], analog: true },
  lm35: { pins: ['pin'], signals: ['celsius'], analog: true },
  ntc: { pins: ['pin'], signals: ['celsius'], analog: true },
  hcsr04: { pins: ['trig', 'echo'], signals: ['cm'], input: true },
};

function analogVolts(part, value) {
  const p = part.params || {};
  if (part.type === 'potentiometer') return Math.max(0, Math.min(5, (value / 100) * 5));
  if (part.type === 'lm35') return Math.max(0, Math.min(1.5, value * 0.01));
  if (part.type === 'ntc') {
    const r0 = p.r0 ?? 10000, beta = p.beta ?? 3950, rFixed = p.r_fixed ?? 10000;
    const t = value + 273.15, t0 = 298.15;
    const rNtc = r0 * Math.exp(beta * (1 / t - 1 / t0));
    // Default wiring: fixed resistor from 5 V to the pin, thermistor from the pin to ground.
    return p.wiring === 'ntc_to_vcc' ? (5 * rFixed) / (rNtc + rFixed) : (5 * rNtc) / (rNtc + rFixed);
  }
  return 0;
}

function disconnectedVolts(part, rand) {
  const p = part.params || {};
  // A divider's fixed resistor pulls an unplugged thermistor's pin hard to one rail.
  if (part.type === 'ntc') return p.wiring === 'ntc_to_vcc' ? 0 : 5;
  // Anything else leaves the pin floating: it wanders and jumps, the way a cut wire reads on a real board.
  const f = part.floating || (part.floating = { v: 2.5 });
  f.v += (rand() - 0.5) * 0.6;
  if (rand() < 0.02) f.v = rand() * 5;
  f.v = Math.max(0, Math.min(5, f.v));
  return f.v;
}

// ---------------------------------------------------------------- the board

// Build the chip and its parts. Returns a board you advance one simulated millisecond at a time:
// the batch bench (runBench, below) and the live page in the browser share this exact model.
function createBoard(spec, opts = {}) {
  const record = opts.record !== false;
  const durationMs = Math.round(spec.duration_ms ?? 5000);
  if (!(durationMs > 0 && durationMs <= 120000)) throw new Error('duration_ms must be between 1 and 120000');

  const program = new Uint16Array(0x4000);
  if (spec.bin) {
    // a flat flash image (the browser page loads firmware this way)
    if (spec.bin.length > 0x8000) throw new Error('Program does not fit in 32 KB of flash');
    new Uint8Array(program.buffer).set(spec.bin);
  } else {
    loadHex(spec.hex, new Uint8Array(program.buffer));
  }
  const cpu = new avr.CPU(program);
  new avr.AVRTimer(cpu, avr.timer0Config);
  new avr.AVRTimer(cpu, avr.timer1Config);
  new avr.AVRTimer(cpu, avr.timer2Config);
  const ports = {
    B: new avr.AVRIOPort(cpu, avr.portBConfig),
    C: new avr.AVRIOPort(cpu, avr.portCConfig),
    D: new avr.AVRIOPort(cpu, avr.portDConfig),
  };
  const usart = new avr.AVRUSART(cpu, avr.usart0Config, F_CPU);
  const adc = new avr.AVRADC(cpu, avr.adcConfig);
  new avr.AVREEPROM(cpu, new avr.EEPROMMemoryBackend(1024));
  const clock = new avr.AVRClock(cpu, F_CPU);
  new avr.AVRWatchdog(cpu, avr.watchdogConfig, clock);
  const rand = prng(spec.seed ?? 1);

  // ---- parts and signals
  const parts = {};
  const signals = {}; // "part.signal" -> current value
  const watched = new Set((spec.watch || []).map(normalizePin));
  for (const raw of spec.parts || []) {
    const def = PART_TYPES[raw.type];
    if (!def) throw new Error(`Unknown part type "${raw.type}"`);
    if (!/^[a-z][a-z0-9_]{0,31}$/.test(raw.id || '')) throw new Error(`Bad part id "${raw.id}"`);
    if (parts[raw.id]) throw new Error(`Duplicate part id "${raw.id}"`);
    const pins = {};
    for (const key of def.pins) {
      if (!raw.pins || raw.pins[key] === undefined) throw new Error(`Part "${raw.id}" needs pin "${key}"`);
      pins[key] = normalizePin(raw.pins[key]);
    }
    if (def.analog && UNO_PINS[pins.pin][2] === null) throw new Error(`Part "${raw.id}" is analog and must use A0-A5`);
    const part = { id: raw.id, type: raw.type, pins, params: raw.params || {}, def, faults: {} };
    parts[raw.id] = part;
    for (const s of def.signals) signals[`${raw.id}.${s}`] = (raw.initial && raw.initial[s]) ?? 0;
    for (const key of Object.keys(pins)) if (def.output || key === 'trig') watched.add(pins[key]);
  }

  // ---- pin recording
  const rec = {};
  for (const pin of watched) {
    rec[pin] = { level: [], duty: [], edges: [], truncated: false, highCycles: 0, highSince: 0, windowStart: 0, last: null, pulseStart: null, lastPulseUs: null, rising: [], changes: 0, dutyNow: 0 };
  }
  const levelOf = (pin) => {
    const [port, bit] = UNO_PINS[pin];
    const st = ports[port].pinState(bit);
    if (st === avr.PinState.High) return 1;
    if (st === avr.PinState.Low) return 0;
    return null; // input
  };
  const onPinChange = (pin) => {
    const r = rec[pin];
    const now = cpu.cycles;
    const lv = levelOf(pin);
    if (lv === r.last) return;
    if (r.last === 1) r.highCycles += now - Math.max(r.highSince, r.windowStart);
    if (lv === 1) {
      r.highSince = now;
      r.pulseStart = now;
      r.rising.push(now);
    } else if (r.last === 1 && r.pulseStart !== null) {
      r.lastPulseUs = (now - r.pulseStart) / 16;
    }
    r.last = lv;
    r.changes++;
    if (r.edges.length < MAX_EDGES_PER_PIN) r.edges.push([Math.round(now / 16), lv]);
    else r.truncated = true;
    const sonar = sonarByTrig[pin];
    if (sonar) sonar(lv, now);
  };
  for (const [name, port] of Object.entries(ports)) {
    const pinsOnPort = [...watched].filter((p) => UNO_PINS[p][0] === name);
    if (pinsOnPort.length) port.addListener(() => pinsOnPort.forEach(onPinChange));
  }

  // ---- inputs
  const setDigitalInput = (pin, high) => {
    const [port, bit] = UNO_PINS[pin];
    ports[port].setPin(bit, high);
  };
  const buttonLevel = (part, pressed) => ((part.params.mode || 'pullup') === 'pullup' ? !pressed : pressed);

  const sonarByTrig = {};
  for (const part of Object.values(parts)) {
    if (part.type === 'button') setDigitalInput(part.pins.pin, buttonLevel(part, !!signals[`${part.id}.pressed`]));
    if (part.type === 'hcsr04') {
      const echo = part.pins.echo;
      setDigitalInput(echo, false);
      let trigHighAt = null;
      let busy = false;
      sonarByTrig[part.pins.trig] = (lv, now) => {
        if (lv === 1) trigHighAt = now;
        else if (lv === 0 && trigHighAt !== null && now - trigHighAt >= 10 * 16 && !busy) {
          busy = true;
          const cm = part.faults.disconnect ? null : signals[`${part.id}.cm`];
          const echoUs = cm === null ? 0 : cm > 400 ? 38000 : Math.max(150, cm * 58);
          cpu.addClockEvent(() => {
            if (echoUs > 0) setDigitalInput(echo, true);
            cpu.addClockEvent(() => {
              setDigitalInput(echo, false);
              busy = false;
            }, Math.max(1, Math.round(echoUs * 16)));
          }, 250 * 16);
        }
      };
    }
  }

  // The pin voltage an analog part presents. Noise is drawn fresh for every ADC conversion (as real ADC
  // noise is), so firmware that averages several readings really does see a steadier value.
  const partVolts = (part, fresh) => {
    if (part.faults.short === 'gnd') return 0;
    if (part.faults.short === 'vcc') return 5;
    if (part.faults.disconnect) return fresh ? (part.floating ? part.floating.v : disconnectedVolts(part, rand)) : disconnectedVolts(part, rand);
    let value = signals[`${part.id}.${part.def.signals[0]}`];
    const noise = part.faults.noise;
    if (noise && fresh) value += (rand() * 2 - 1) * noise;
    return analogVolts(part, value);
  };
  const analogByChannel = {};
  const applyAnalog = () => {
    for (const part of Object.values(parts)) {
      if (!part.def.analog) continue;
      const ch = UNO_PINS[part.pins.pin][2];
      analogByChannel[ch] = part;
      adc.channelValues[ch] = partVolts(part, false);   // advances a floating pin's wander once per ms
    }
  };
  const baseRead = adc.onADCRead;
  adc.onADCRead = (input) => {
    if (input.type === avr.ADCMuxInputType.SingleEnded && analogByChannel[input.channel]) {
      adc.channelValues[input.channel] = partVolts(analogByChannel[input.channel], true);
    }
    baseRead(input);
  };

  // ---- stimuli and faults, applied once per simulated millisecond
  const steps = (spec.stimuli || []).map((s) => ({ ...s }));
  for (const s of steps) {
    if (!parts[s.part]) throw new Error(`Stimulus names unknown part "${s.part}"`);
    for (const k of Object.keys(s.ramp || {})) {
      if (!parts[s.part].def.signals.includes(k)) throw new Error(`Part "${s.part}" has no signal "${k}"`);
      if (!(s.to_ms >= s.from_ms)) throw new Error('A ramp needs from_ms <= to_ms');
    }
  }
  const bounce = (part, pressed) => {
    // A mechanical button chatters for a few ms before settling.
    const ms = part.faults.bounce || 0;
    const pin = part.pins.pin;
    if (!ms) return setDigitalInput(pin, buttonLevel(part, pressed));
    let flips = Math.max(2, Math.round(ms * 2));
    let state = !pressed;
    const flip = () => {
      state = !state;
      setDigitalInput(pin, buttonLevel(part, state));
      if (--flips > 0) cpu.addClockEvent(flip, Math.round((ms * CYCLES_PER_MS) / Math.max(2, ms * 2)) + Math.round(rand() * 800));
      else setDigitalInput(pin, buttonLevel(part, pressed));
    };
    flip();
  };
  const applyStep = (s, ms) => {
    const part = parts[s.part];
    if (s.fault) {
      if (s.fault === 'clear') part.faults = {};
      else part.faults[s.fault] = s.value ?? true;
      return;
    }
    const values = s.set || {};
    for (const [k, v] of Object.entries(values)) {
      if (!part.def.signals.includes(k)) throw new Error(`Part "${part.id}" has no signal "${k}"`);
      const key = `${part.id}.${k}`;
      const changed = signals[key] !== v;
      signals[key] = v;
      if (part.type === 'button' && k === 'pressed' && changed) bounce(part, !!v);
    }
    if (s.ramp) {
      for (const [k, [a, b]] of Object.entries(s.ramp)) {
        const span = Math.max(1, s.to_ms - s.from_ms);
        const f = Math.min(1, Math.max(0, (ms - s.from_ms) / span));
        signals[`${part.id}.${k}`] = Math.round((a + (b - a) * f) * 1000) / 1000;
      }
    }
  };

  // ---- serial
  // Each line is recorded with the millisecond its first character went out.
  const serial = { text: '', lines: [], truncated: false };
  let lineBuf = '';
  let lineAt = null;
  usart.onByteTransmit = (b) => {
    const ch = String.fromCharCode(b);
    if (serial.text.length < 200000) serial.text += ch;
    else serial.truncated = true;
    if (lineAt === null) lineAt = Math.floor(cpu.cycles / CYCLES_PER_MS);
    if (ch === '\n') {
      if (serial.lines.length < 5000) serial.lines.push([lineAt, lineBuf.replace(/\r$/, '')]);
      else serial.truncated = true;
      lineBuf = '';
      lineAt = null;
    } else if (lineBuf.length < 1000) {
      lineBuf += ch;
    }
  };

  // ---- pokes (e.g. set millis() close to its 49.7-day rollover) once main() starts, after the C runtime
  // has cleared memory and before the Arduino core or the sketch reads the clock
  const pokes = spec.pokes || [];
  const setupPc = spec.poke_at_pc ?? null;
  if (pokes.length && setupPc === null) throw new Error('pokes need poke_at_pc');
  let poked = pokes.length === 0;
  const doPokes = () => {
    for (const p of pokes) {
      const base = p.addr >= 0x800000 ? p.addr - 0x800000 : p.addr;
      p.bytes.forEach((b, i) => {
        if (base + i < 0x100 || base + i >= cpu.data.length) throw new Error('Poke outside SRAM');
        cpu.data[base + i] = b & 0xff;
      });
    }
    poked = true;
  };

  // ---- the run
  const sig = {}; // per-ms signal history
  for (const k of Object.keys(signals)) sig[k] = [];
  const totalCycles = durationMs * CYCLES_PER_MS;
  let ms = 0;
  applyAnalog();
  for (const pin of watched) {
    rec[pin].last = levelOf(pin);
    rec[pin].windowStart = 0;
    rec[pin].highSince = 0;
  }
  const t0 = Date.now();
  const stepMs = () => {
    for (const s of steps) {
      if (s.at_ms !== undefined && s.at_ms === ms) applyStep(s, ms);
      else if (s.ramp && ms >= s.from_ms && ms <= s.to_ms) applyStep(s, ms);
    }
    applyAnalog();
    const end = (ms + 1) * CYCLES_PER_MS;
    if (!poked && setupPc !== null) {
      while (cpu.cycles < end) {
        if (cpu.pc === setupPc) {
          doPokes();
          break;
        }
        avr.avrInstruction(cpu);
        cpu.tick();
      }
    }
    while (cpu.cycles < end) {
      avr.avrInstruction(cpu);
      cpu.tick();
    }
    // sample the millisecond that just ended
    for (const pin of watched) {
      const r = rec[pin];
      const windowStart = ms * CYCLES_PER_MS;
      if (r.last === 1) r.highCycles += cpu.cycles - Math.max(r.highSince, windowStart);
      const span = cpu.cycles - windowStart;
      r.dutyNow = Math.round((r.highCycles / span) * 1000) / 1000;
      if (record) {
        r.level.push(r.last === null ? -1 : r.last);
        r.duty.push(r.dutyNow);
      } else if (r.edges.length > 2000) {
        r.edges.splice(0, r.edges.length - 1000);
      }
      r.highCycles = 0;
      r.windowStart = cpu.cycles;
    }
    for (const part of Object.values(parts)) {
      if (part.type === 'servo') {
        const r = rec[part.pins.pin];
        const us = r.lastPulseUs;
        signals[`${part.id}.angle`] = us === null ? -1 : Math.round(Math.max(0, Math.min(180, ((us - 544) / (2400 - 544)) * 180)));
      }
      if (part.type === 'buzzer') {
        const r = rec[part.pins.pin];
        const from = cpu.cycles - 50 * CYCLES_PER_MS;
        r.rising = r.rising.filter((c) => c >= from);
        signals[`${part.id}.hz`] = ms >= 50 ? Math.round(r.rising.length * 20) : 0;
      }
    }
    if (record) for (const k of Object.keys(signals)) sig[k].push(signals[k]);
    ms++;
    for (const pin of watched) {
      const r = rec[pin];
      if (r.rising.length > 4096) r.rising.splice(0, r.rising.length - 2048);
    }
  };

  // What a live view needs right now: each watched pin's level and duty, and every signal.
  const now = () => {
    const pins = {};
    for (const pin of watched) {
      const r = rec[pin];
      pins[pin] = { level: r.last === null ? -1 : r.last, duty: r.dutyNow, changes: r.changes };
    }
    return { ms, pins, signals: { ...signals }, serial };
  };
  // Change an input or inject a fault immediately (the live page's sliders and buttons).
  const apply = (stimulus) => {
    if (!parts[stimulus.part]) throw new Error(`Unknown part "${stimulus.part}"`);
    applyStep(stimulus, ms);
  };

  const result = () => {
  const pins = {};
  for (const pin of watched) {
    const r = rec[pin];
    const pwm = r.duty.some((d) => d > 0 && d < 1);
    pins[pin] = { level: r.level, edges: r.edges, truncated: r.truncated };
    if (pwm) pins[pin].duty = r.duty;
  }
  if (lineBuf) serial.lines.push([lineAt ?? durationMs, lineBuf]);
  return {
    ok: true,
    engine: 'avr8js',
    mcu: 'atmega328p',
    f_cpu: F_CPU,
    duration_ms: durationMs,
    cycles: cpu.cycles,
    poked,
    pins,
    signals: sig,
    serial,
    wall_ms: Date.now() - t0,
  };
  };

  return { stepMs, now, apply, result, parts, get ms() { return ms; }, durationMs, cpu };
}

// ---------------------------------------------------------------- the bench

// Run a whole bench in one go and return the recorded trace.
function runBench(spec) {
  const board = createBoard(spec);
  const t0 = Date.now();
  while (board.ms < board.durationMs) {
    board.stepMs();
    if (spec.max_wall_ms && Date.now() - t0 > spec.max_wall_ms) throw new Error('Simulation took too long');
  }
  return board.result();
}

module.exports = { runBench, createBoard, normalizePin, PART_TYPES, UNO_PINS, loadHex };
