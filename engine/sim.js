#!/usr/bin/env node
// Command-line wrapper: reads one bench spec as JSON on stdin, writes the trace as JSON on stdout.
'use strict';
const { runBench } = require('./bench');

let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (c) => {
  input += c;
  if (input.length > 4 * 1024 * 1024) {
    process.stdout.write(JSON.stringify({ ok: false, error: 'Input too large' }));
    process.exit(2);
  }
});
process.stdin.on('end', () => {
  try {
    const spec = JSON.parse(input);
    process.stdout.write(JSON.stringify(runBench(spec)));
  } catch (e) {
    process.stdout.write(JSON.stringify({ ok: false, error: String((e && e.message) || e) }));
    process.exitCode = 1;
  }
});
