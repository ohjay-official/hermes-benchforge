// BenchForge live board: runs a tested firmware on the simulated ATmega328P, in the browser.
'use strict';
(function () {
  var E = window.BenchForgeEngine;
  var NS = 'http://www.w3.org/2000/svg';
  var WIRES = ['#f0883e', '#58a6ff', '#bc8cff', '#e3b341', '#39c5cf', '#3fb950'];
  var SHORT = { lm35: 'LM35', ntc: 'NTC', potentiometer: 'pot', hcsr04: 'HC-SR04', relay: 'relay', led: 'LED',
    buzzer: 'buzzer', servo: 'servo', button: 'button', digital_out: 'output' };
  // Two layouts: wide (board left, parts right) and phone (board on top, parts below it).
  var BOARD = { x: 24, y: 70, w: 250, h: 176 };
  var CARD = { x: 330, w: 290, h: 46 };
  var PORTRAIT = false;
  function layout(n) {
    PORTRAIT = window.innerWidth < 600;
    if (PORTRAIT) {
      BOARD = { x: 70, y: 22, w: 250, h: 176 };
      CARD = { x: 28, w: 324, h: 46, y0: 232, gap: 8 };
      return [360, 232 + n * (46 + 8) + 4];
    }
    BOARD = { x: 24, y: 70, w: 250, h: 176 };
    CARD = { x: 330, w: 290, h: 46, y0: 10, gap: n > 5 ? 4 : 10 };
    return [640, Math.max(330, 10 + n * (46 + CARD.gap) + 4)];
  }
  var $ = function (id) { return document.getElementById(id); };

  var P = null;          // the payload: bench, firmware, results
  var board = null;      // the engine's board
  var mode = 'idle';     // 'bench' | 'live' | 'done'
  var acc = 0, last = 0, raf = 0;
  var views = {};        // per-part update functions
  var clicks = {};       // relay change history for chatter detection

  // ------------------------------------------------------------ loading

  function b64urlBytes(s) {
    s = s.replace(/-/g, '+').replace(/_/g, '/');
    while (s.length % 4) s += '=';
    var bin = atob(s), out = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }
  function inflate(bytes) {
    var ds = new DecompressionStream('deflate-raw');
    return new Response(new Blob([bytes]).stream().pipeThrough(ds)).arrayBuffer();
  }
  async function fromHash() {
    var h = location.hash.slice(1);
    var f = /(?:^|&)f=([^&]+)/.exec(h);
    if (f) return JSON.parse(new TextDecoder().decode(await inflate(b64urlBytes(f[1]))));
    var d = /(?:^|&)demo=([a-z0-9-]+)/.exec(h);
    var name = d ? d[1] : 'greenhouse-fan';
    var r = await fetch('demos/' + name + '.json');
    if (!r.ok) throw new Error('No demo called ' + name);
    return r.json();
  }
  async function readZip(buf) {
    // enough of the zip format to find live.json in a BenchForge bundle
    var v = new DataView(buf), u8 = new Uint8Array(buf), eocd = -1;
    for (var i = buf.byteLength - 22; i >= Math.max(0, buf.byteLength - 70000); i--) {
      if (v.getUint32(i, true) === 0x06054b50) { eocd = i; break; }
    }
    if (eocd < 0) throw new Error('Not a zip file');
    var n = v.getUint16(eocd + 10, true), off = v.getUint32(eocd + 16, true);
    for (var k = 0; k < n; k++) {
      var method = v.getUint16(off + 10, true), csize = v.getUint32(off + 20, true);
      var nlen = v.getUint16(off + 28, true), elen = v.getUint16(off + 30, true), clen = v.getUint16(off + 32, true);
      var local = v.getUint32(off + 42, true);
      var name = new TextDecoder().decode(u8.subarray(off + 46, off + 46 + nlen));
      if (/(^|\/)live\.json$/.test(name)) {
        var start = local + 30 + v.getUint16(local + 26, true) + v.getUint16(local + 28, true);
        var data = u8.subarray(start, start + csize);
        var raw = method === 8 ? new Uint8Array(await inflate(data)) : data;
        return JSON.parse(new TextDecoder().decode(raw));
      }
      off += 46 + nlen + elen + clen;
    }
    throw new Error('This zip has no live.json (is it a BenchForge bundle?)');
  }

  // ------------------------------------------------------------ drawing

  function el(tag, attrs, parent, text) {
    var e = document.createElementNS(NS, tag);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    if (text !== undefined) e.textContent = text;
    if (parent) parent.appendChild(e);
    return e;
  }
  function pinXY(pin) {
    var n = parseInt(pin.slice(1), 10);
    if (pin[0] === 'D') return [BOARD.x + BOARD.w - 18 - (13 - n) * 15, BOARD.y + 10];
    return [BOARD.x + BOARD.w - 18 - (5 - n) * 15, BOARD.y + BOARD.h - 10];
  }
  function drawBoard(svg, used) {
    var b = BOARD;
    el('rect', { x: b.x - 10, y: b.y + 30, width: 30, height: 34, fill: '#bec6ce', rx: 2 }, svg);
    el('rect', { x: b.x, y: b.y, width: b.w, height: b.h, rx: 10, fill: '#00818a' }, svg);
    el('rect', { x: b.x - 6, y: b.y + 112, width: 22, height: 30, fill: '#21262d', rx: 2 }, svg);
    el('rect', { x: b.x + 70, y: b.y + 72, width: 112, height: 34, fill: '#21262d', rx: 3 }, svg);
    el('text', { x: b.x + 126, y: b.y + 93, fill: '#7d8590', 'font-size': 11, 'text-anchor': 'middle' }, svg, 'ATmega328P');
    el('text', { x: b.x + 18, y: b.y + 138, fill: '#fff', 'font-size': 22, 'font-weight': 700 }, svg, 'UNO');
    el('text', { x: b.x + 20, y: b.y + 158, fill: '#105058', 'font-size': 11 }, svg, '16 MHz');
    var pins = [];
    for (var n = 2; n <= 13; n++) pins.push('D' + n);
    for (var a = 0; a <= 5; a++) pins.push('A' + a);
    pins.forEach(function (pin) {
      var xy = pinXY(pin), i = used[pin];
      el('rect', { x: xy[0] - 4, y: xy[1] - 4, width: 9, height: 9, fill: i === undefined ? '#21262d' : WIRES[i % WIRES.length] }, svg);
      if (i !== undefined) {
        el('text', { x: xy[0] + 1, y: pin[0] === 'D' ? xy[1] + 18 : xy[1] - 10, fill: '#fff', 'font-size': 11,
          'text-anchor': 'middle' }, svg, pin);
      }
    });
  }
  function wire(svg, pin, cy, color, slot) {
    var xy = pinXY(pin), lane = PORTRAIT ? 6 + slot * 4 : BOARD.x + BOARD.w + 14 + slot * 6;
    var y0 = pin[0] === 'D' ? BOARD.y - 8 - slot * 3 : BOARD.y + BOARD.h + 8 + slot * 3;
    el('polyline', { points: [xy[0], xy[1], xy[0], y0, lane, y0, lane, cy, CARD.x - 2, cy].join(' '),
      fill: 'none', stroke: color, 'stroke-width': 2 }, svg);
  }

  function card(svg, part, y, color) {
    var g = el('g', {}, svg);
    el('rect', { x: CARD.x, y: y, width: CARD.w, height: CARD.h, rx: 8, fill: '#161b22', stroke: '#30363d' }, g);
    el('rect', { x: CARD.x, y: y + 8, width: 3, height: CARD.h - 16, fill: color }, g);
    el('text', { x: CARD.x + 62, y: y + 19, fill: '#e6edf3', 'font-size': 13 }, g, part.id);
    var pins = Object.keys(part.pins).map(function (k) { return part.pins[k]; }).join('/');
    el('text', { x: CARD.x + 62, y: y + 36, fill: '#7d8590', 'font-size': 11 }, g, (SHORT[part.type] || part.type) + ' · ' + pins);
    var value = el('text', { x: CARD.x + CARD.w - 12, y: y + 30, fill: '#e6edf3', 'font-size': 17,
      'font-weight': 700, 'text-anchor': 'end' }, g, '');
    var ix = CARD.x + 30, iy = y + CARD.h / 2, icon = el('g', {}, g);
    var set = function (text, color) { value.textContent = text; value.setAttribute('fill', color || '#e6edf3'); };
    var t = part.type;
    if (t === 'led') {
      var halo = el('circle', { cx: ix, cy: iy, r: 15, fill: '#ff5c5c', opacity: 0 }, icon);
      var dot = el('circle', { cx: ix, cy: iy, r: 10, fill: '#501e1e' }, icon);
      return function (s) {
        if (s.active === null) { dot.setAttribute('fill', '#30363d'); halo.setAttribute('opacity', 0); return set('not set', '#d29922'); }
        dot.setAttribute('fill', s.active > 0.25 ? '#ff5c5c' : '#501e1e');
        halo.setAttribute('opacity', (0.35 * s.active).toFixed(2));
        if (s.pwm) return set(Math.round(s.active * 100) + ' %', '#ff5c5c');
        set(s.active >= 0.5 ? 'ON' : 'off', s.active >= 0.5 ? '#ff5c5c' : '#7d8590');
      };
    }
    if (t === 'relay' || t === 'digital_out') {
      var fan = /fan/.test(part.id), blades = el('g', {}, icon), ring = el('circle', { cx: ix, cy: iy, r: 16, fill: 'none', stroke: '#30363d', 'stroke-width': 2 }, icon);
      if (fan) for (var k = 0; k < 3; k++) el('path', { d: 'M0 0 L13 -3 Q15 0 13 3 Z', fill: '#7d8590', transform: 'rotate(' + k * 120 + ')' }, blades);
      else el('rect', { x: -12, y: -10, width: 24, height: 20, rx: 4, fill: '#21262d' }, blades);
      blades.setAttribute('transform', 'translate(' + ix + ' ' + iy + ')');
      var spin = 0;
      return function (s) {
        var on = s.active !== null && s.active >= 0.5;
        ring.setAttribute('stroke', on ? '#3fb950' : '#30363d');
        if (on) spin = (spin + 25) % 360;
        blades.setAttribute('transform', 'translate(' + ix + ' ' + iy + ') rotate(' + spin + ')');
        [].forEach.call(blades.children, function (c) { c.setAttribute('fill', on ? '#ffffff' : (fan ? '#7d8590' : '#21262d')); });
        if (s.active === null) return set('not set', '#d29922');
        if (s.chatter >= 5) return set(s.chatter + ' clicks/s!', '#f85149');
        set(on ? 'ON' : 'off', on ? '#3fb950' : '#7d8590');
      };
    }
    if (t === 'buzzer') {
      el('rect', { x: ix - 12, y: iy - 8, width: 12, height: 16, rx: 2, fill: '#bec6ce' }, icon);
      var waves = el('path', { d: 'M' + (ix + 4) + ' ' + (iy - 6) + ' q5 6 0 12 M' + (ix + 9) + ' ' + (iy - 10) + ' q8 10 0 20',
        stroke: '#d29922', fill: 'none', 'stroke-width': 2, opacity: 0 }, icon);
      return function (s) {
        waves.setAttribute('opacity', s.hz >= 20 ? 1 : 0);
        s.hz >= 20 ? set(Math.round(s.hz) + ' Hz', '#d29922') : set('silent', '#7d8590');
      };
    }
    if (t === 'servo') {
      el('rect', { x: ix - 15, y: iy - 6, width: 30, height: 16, rx: 3, fill: '#21262d' }, icon);
      var arm = el('line', { x1: ix, y1: iy, x2: ix + 18, y2: iy, stroke: '#fff', 'stroke-width': 3, 'stroke-linecap': 'round' }, icon);
      return function (s) {
        if (s.angle < 0) return set('no signal', '#d29922');
        var a = (180 - s.angle) * Math.PI / 180;
        arm.setAttribute('x2', ix + 18 * Math.cos(a)); arm.setAttribute('y2', iy - 18 * Math.sin(a));
        set(Math.round(s.angle) + '°');
      };
    }
    if (t === 'lm35' || t === 'ntc') {
      el('rect', { x: ix - 4, y: iy - 16, width: 9, height: 26, rx: 4, fill: '#30363d' }, icon);
      var col = el('rect', { x: ix - 2, y: iy, width: 5, height: 8, fill: '#39c5cf' }, icon);
      var bulb = el('circle', { cx: ix + 0.5, cy: iy + 12, r: 6, fill: '#39c5cf' }, icon);
      return function (s) {
        if (s.unplugged) return set('wire cut', '#f85149');
        var v = s.value, f = Math.max(0, Math.min(1, v / 50)), c = v >= 30 ? '#f85149' : v >= 25 ? '#d29922' : '#39c5cf';
        col.setAttribute('y', iy + 8 - 22 * f); col.setAttribute('height', 22 * f); col.setAttribute('fill', c); bulb.setAttribute('fill', c);
        set(v.toFixed(1) + ' °C', c);
      };
    }
    if (t === 'potentiometer') {
      el('circle', { cx: ix, cy: iy, r: 13, fill: '#bec6ce' }, icon);
      el('circle', { cx: ix, cy: iy, r: 10, fill: '#21262d' }, icon);
      var ptr = el('line', { x1: ix, y1: iy, x2: ix, y2: iy - 9, stroke: '#fff', 'stroke-width': 2 }, icon);
      return function (s) {
        if (s.unplugged) return set('wire cut', '#f85149');
        var a = (225 - 270 * s.value / 100) * Math.PI / 180;
        ptr.setAttribute('x2', ix + 9 * Math.cos(a)); ptr.setAttribute('y2', iy - 9 * Math.sin(a));
        set(Math.round(s.value) + ' %');
      };
    }
    if (t === 'button') {
      el('rect', { x: ix - 13, y: iy - 13, width: 26, height: 26, rx: 4, fill: '#bec6ce' }, icon);
      var cap = el('circle', { cx: ix, cy: iy, r: 8, fill: '#6e2824' }, icon);
      return function (s) {
        cap.setAttribute('fill', s.value ? '#f85149' : '#6e2824');
        s.value ? set('pressed', '#d29922') : set('released', '#7d8590');
      };
    }
    if (t === 'hcsr04') {
      el('rect', { x: ix - 18, y: iy - 9, width: 36, height: 18, rx: 3, fill: '#39c5cf' }, icon);
      el('circle', { cx: ix - 9, cy: iy, r: 6, fill: '#21262d' }, icon);
      el('circle', { cx: ix + 9, cy: iy, r: 6, fill: '#21262d' }, icon);
      return function (s) {
        if (s.unplugged) return set('wire cut', '#f85149');
        s.value < 400 ? set(Math.round(s.value) + ' cm', s.value < 40 ? '#d29922' : '#e6edf3') : set('clear', '#7d8590');
      };
    }
    return function () {};
  }

  function buildStage() {
    var svg = $('board');
    svg.innerHTML = '';
    var size = layout(P.parts.length);
    svg.setAttribute('viewBox', '0 0 ' + size[0] + ' ' + size[1]);
    var used = {};
    P.parts.forEach(function (p, i) { Object.keys(p.pins).forEach(function (k) { used[p.pins[k]] = i; }); });
    drawBoard(svg, used);
    views = {};
    P.parts.forEach(function (p, i) {
      var y = CARD.y0 + i * (CARD.h + CARD.gap), color = WIRES[i % WIRES.length];
      Object.keys(p.pins).forEach(function (k, slot) { wire(svg, p.pins[k], y + CARD.h / 2, color, i + slot); });
    });
    P.parts.forEach(function (p, i) {
      var y = CARD.y0 + i * (CARD.h + CARD.gap);
      views[p.id] = card(svg, p, y, WIRES[i % WIRES.length]);
    });
  }

  // ------------------------------------------------------------ text panels

  function panels() {
    $('bench-name').textContent = P.name;
    var badge = $('badge');
    badge.textContent = (P.verdict === 'pass' ? '✓ ' : '✗ ') + P.score;
    badge.className = 'badge ' + (P.verdict === 'pass' ? 'pass' : 'fail');
    $('goal').textContent = P.goal || '';
    var ol = $('checks');
    ol.innerHTML = '';
    P.steps.forEach(function (s) {
      var li = document.createElement('li');
      li.id = 'step-' + s[0];
      li.innerHTML = '<span class="' + (s[4] ? 'ok' : 'bad') + '">' + (s[4] ? '✓' : '✗') + '</span> ';
      li.appendChild(document.createTextNode(s[1]));
      if (s[5]) { var saw = document.createElement('span'); saw.className = 'saw'; saw.textContent = 'saw: ' + s[5]; li.appendChild(saw); }
      ol.appendChild(li);
    });
    P.always.forEach(function (a) {
      var li = document.createElement('li');
      li.innerHTML = '<span class="' + (a[2] ? 'ok' : 'bad') + '">' + (a[2] ? '✓' : '✗') + '</span> ';
      li.appendChild(document.createTextNode(a[1]));
      ol.appendChild(li);
    });
    var runs = $('runs');
    runs.innerHTML = '';
    P.runs.slice(1).forEach(function (r) {
      var li = document.createElement('li');
      li.innerHTML = r[2] ? '<span class="dim">–</span> ' : '<span class="' + (r[1] ? 'ok' : 'bad') + '">' + (r[1] ? '✓' : '✗') + '</span> ';
      li.appendChild(document.createTextNode(r[0] + (r[2] ? ' (skipped)' : '')));
      runs.appendChild(li);
    });
    $('fw').textContent = 'Firmware sha256 ' + P.firmware.slice(0, 16) + '…';
    var tl = $('timeline');
    tl.innerHTML = '';
    P.steps.forEach(function (s) {
      var d = document.createElement('div');
      d.className = 'seg';
      d.style.flex = String(s[3] - s[2]);
      d.style.background = s[4] ? '#3fb950' : '#f85149';
      tl.appendChild(d);
    });
    var head = document.createElement('div');
    head.className = 'head'; head.id = 'head';
    tl.appendChild(head);
  }

  function controls() {
    var box = $('controls');
    box.innerHTML = '';
    var inputs = P.parts.filter(function (p) { return ['lm35', 'ntc', 'potentiometer', 'hcsr04', 'button'].indexOf(p.type) >= 0; });
    box.hidden = !inputs.length;
    inputs.forEach(function (p) {
      var sig = { lm35: 'celsius', ntc: 'celsius', potentiometer: 'percent', hcsr04: 'cm', button: 'pressed' }[p.type];
      var d = document.createElement('div');
      d.className = 'ctl';
      d.innerHTML = '<h3>' + p.id + '</h3>';
      if (p.type === 'button') {
        var b = document.createElement('button');
        b.className = 'hold'; b.textContent = 'Hold to press';
        var press = function (v) { return function (e) { e.preventDefault(); b.classList.toggle('down', v); if (board) board.apply({ part: p.id, set: { pressed: v } }); }; };
        b.addEventListener('pointerdown', press(true));
        ['pointerup', 'pointerleave', 'pointercancel'].forEach(function (ev) { b.addEventListener(ev, press(false)); });
        d.appendChild(b);
      } else {
        var range = { lm35: [-10, 60, 0.5], ntc: [-10, 60, 0.5], potentiometer: [0, 100, 1], hcsr04: [2, 400, 1] }[p.type];
        var start = (p.initial && p.initial[sig] !== undefined) ? p.initial[sig] : range[0];
        var row = document.createElement('div');
        row.className = 'row';
        var input = document.createElement('input');
        input.type = 'range'; input.min = range[0]; input.max = range[1]; input.step = range[2]; input.value = start;
        input.addEventListener('input', function () { if (board) board.apply({ part: p.id, set: (function () { var o = {}; o[sig] = Number(input.value); return o; })() }); });
        row.appendChild(input);
        d.appendChild(row);
        var cut = document.createElement('label');
        cut.innerHTML = '<input type="checkbox"> cut the wire';
        cut.firstChild.addEventListener('change', function (e) {
          if (board) board.apply({ part: p.id, fault: e.target.checked ? 'disconnect' : 'clear' });
          unplugged[p.id] = e.target.checked;
        });
        d.appendChild(cut);
      }
      box.appendChild(d);
    });
  }

  // ------------------------------------------------------------ running

  var unplugged = {};
  var serialSeen = 0;

  function start(which) {
    cancelAnimationFrame(raf);
    var bin = Uint8Array.from(atob(P.bin), function (c) { return c.charCodeAt(0); });
    var spec = { bin: bin, parts: P.parts, seed: 20261111 };
    if (which === 'bench') {
      spec.duration_ms = P.duration_ms;
      spec.stimuli = P.stimuli;
    } else {
      spec.duration_ms = 120000;
      spec.stimuli = [];
    }
    board = E.createBoard(spec, { record: false });
    mode = which;
    unplugged = {};
    clicks = {};
    serialSeen = 0;
    $('serial').textContent = '';
    $('controls').hidden = which !== 'live';
    if (which === 'live') controls();
    $('play').textContent = which === 'bench' ? '⏸ Playing the bench…' : '▶ Play the bench';
    acc = 0; last = performance.now();
    raf = requestAnimationFrame(tick);
  }

  function tick(t) {
    var speed = Number($('speed').value) || 1;
    acc += Math.min(250, t - last) * speed;
    last = t;
    var n = Math.min(Math.floor(acc), 400);
    acc -= n;
    for (var i = 0; i < n && board.ms < board.durationMs; i++) board.stepMs();
    render();
    if (board.ms >= board.durationMs) {
      if (mode === 'bench') {
        mode = 'done';
        $('play').textContent = '↺ Play the bench again';
        $('now').innerHTML = '<b>' + (P.verdict === 'pass' ? '<span class="ok">✓ Every check passed</span>' :
          '<span class="bad">✗ Some checks failed</span>') + '</b> · now try it yourself';
        return;
      }
      start('live');
      return;
    }
    raf = requestAnimationFrame(tick);
  }

  function render() {
    var now = board.now(), ms = now.ms;
    P.parts.forEach(function (p) {
      var s = {}, t = p.type;
      if (t === 'led' || t === 'relay' || t === 'digital_out') {
        var r = now.pins[p.pins.pin];
        if (r.level === -1) s.active = null;
        else {
          var frac = r.duty > 0.02 && r.duty < 0.98;
          var v = frac ? r.duty : r.level;
          if (p.params && p.params.active_low) v = 1 - v;
          s.active = v;
          s.pwm = t === 'led' && frac && pwmSeen(p.id);
        }
        if (t === 'relay') {
          var h = clicks[p.id] || (clicks[p.id] = []);
          h.push([ms, r.changes]);
          while (h.length > 1 && h[0][0] < ms - 1000) h.shift();
          s.chatter = h[h.length - 1][1] - h[0][1];
        }
      } else if (t === 'buzzer') s.hz = now.signals[p.id + '.hz'];
      else if (t === 'servo') s.angle = now.signals[p.id + '.angle'];
      else {
        var sig = { lm35: 'celsius', ntc: 'celsius', potentiometer: 'percent', hcsr04: 'cm', button: 'pressed' }[t];
        s.value = now.signals[p.id + '.' + sig];
        s.unplugged = !!unplugged[p.id];
      }
      views[p.id](s);
    });
    $('clock').textContent = (ms / 1000).toFixed(1) + ' s' + (mode === 'live' ? ' · live' : '');
    var lines = now.serial.lines;
    if (lines.length !== serialSeen) {
      serialSeen = lines.length;
      $('serial').textContent = lines.slice(-8).map(function (l) { return (l[0] / 1000).toFixed(2) + ' s  ' + l[1]; }).join('\n');
    }
    var head = $('head');
    if (mode === 'bench' || mode === 'done') {
      head.style.left = (100 * Math.min(ms, P.duration_ms) / P.duration_ms) + '%';
      var cur = P.steps.filter(function (s) { return s[2] <= ms && ms < s[3]; })[0];
      document.querySelectorAll('.checks li').forEach(function (li) { li.classList.remove('current'); });
      if (cur) {
        var li = $('step-' + cur[0]);
        if (li) li.classList.add('current');
        $('now').innerHTML = '';
        var mark = document.createElement('span');
        mark.className = cur[4] ? 'ok' : 'bad'; mark.textContent = cur[4] ? '✓ ' : '✗ ';
        $('now').appendChild(mark);
        $('now').appendChild(document.createTextNode((P.steps.indexOf(cur) + 1) + '. ' + cur[1]));
      }
    } else {
      head.style.left = '0';
      $('now').textContent = 'Live: drag the sliders, hold the button, cut a sensor wire. The firmware reacts exactly as it would on the chip.';
    }
  }
  var pwmCount = {};
  function pwmSeen(id) { pwmCount[id] = (pwmCount[id] || 0) + 1; return pwmCount[id] > 30; }

  // ------------------------------------------------------------ wiring up the page

  function show(p) {
    P = p;
    pwmCount = {};
    buildStage();
    panels();
    $('controls').hidden = true;
    mode = 'idle';
    board = null;
    start('bench');
  }

  var wasPortrait = null;
  window.addEventListener('resize', function () {
    if (!P || (window.innerWidth < 600) === wasPortrait) return;
    wasPortrait = window.innerWidth < 600;
    buildStage();
    if (board) render();
  });
  $('play').addEventListener('click', function () { start('bench'); });
  $('live').addEventListener('click', function () { start('live'); });
  $('file').addEventListener('change', async function (e) {
    var f = e.target.files[0];
    if (!f) return;
    try {
      var buf = await f.arrayBuffer();
      show(/\.json$/i.test(f.name) ? JSON.parse(new TextDecoder().decode(buf)) : await readZip(buf));
    } catch (err) { alert(err.message); }
  });
  window.addEventListener('hashchange', function () { fromHash().then(show).catch(function (err) { $('now').textContent = err.message; }); });

  (window.BENCHFORGE_PAYLOAD ? Promise.resolve(window.BENCHFORGE_PAYLOAD) : fromHash())
    .then(show)
    .catch(function (err) { $('bench-name').textContent = ''; $('now').textContent = 'Could not load: ' + err.message; });
})();
