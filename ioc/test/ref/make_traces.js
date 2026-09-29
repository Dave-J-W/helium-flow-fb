#!/usr/bin/env node
// Reference trace generator for the C port of the sample-gas controller.
//
// Runs the simulator's own JavaScript (sliced out of simulator/sample_gas_simulator.html, never modified) with a
// seeded Math.random, and records for every scenario each input the Controller saw and each output it produced:
//
//   R                                   resetAll(): reinit, enter IDLE "station reset", cylinder/ledger cleared
//   S <mode> <target>                   parameter snapshot (before every C; before a T when changed)
//   I <t> <o2> <sevr> <flow> <sp> <running 0/1> <ramp> <total> <gas>   analyzer + polled Alicat readbacks
//   C <t> <name> [arg]                  outside call into the controller
//   T <t>                               tick(t)
//   P <t> <spVal>                       processEpid(); spVal = Setpoint record VAL at that moment
//   A <t> sp <v> | A <t> ramp <v> | A <t> run    put made by the controller
//   L <t> <sev> <msg>                   log line made by the controller
//   X <t> <state> <lastCmd> <OVAL> <FBON 0/1> <worstSev> <overrideCount> <cylLeftL> <cumL>
//                                       controller state after a tick (on change, and every 600 s)
//   H <t> <wStart> <wEnd> <dispensed> <inRuns> <cyls> <cylEquiv> <nRuns>
//         { <start> <end> <purges> <L> <cylinders> <finished 0/1> } x nRuns
//         <nEst> { <h> <rate> } x nEst <cylMedianH>
//                                       usageReport() and the cylForecast() results, after the
//                                       tick of every t % 3600 == 0 and once at the end of the
//                                       scenario (not while the IOC is down); end/h/rate/median
//                                       may be null
//
// Numbers are printed with String(v) (shortest round-trip), null as "null", NaN as "NaN".
//
// Usage: node make_traces.js [--html <simulator.html>] [--out <dir>] [--seed <n>] [--only <n,n,...>] [--no-noise]
//
// --no-noise sets the reference plant's analyzer white noise (pp.noise) and slow reading component
// (pp.wanderRel) to 0, as the bench plant simulator's --no-noise does, and writes to golden/nonoise/
// unless --out is given (spec §14.2 quantitative comparison). gauss() is still drawn, so the random
// stream (e.g. a reseat) is the same as with noise on.

'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

// ---------------------------------------------------------------------------------------------- arguments
const args = { html: path.join(__dirname, '..', '..', '..', 'simulator', 'sample_gas_simulator.html'),
               out: null, seed: 12345, only: null, noise: true };
for (let i = 2; i < process.argv.length; i++) {
  const a = process.argv[i], v = process.argv[i + 1];
  if (a === '--html') { args.html = v; i++; }
  else if (a === '--out') { args.out = v; i++; }
  else if (a === '--seed') { args.seed = Number(v); i++; if (!Number.isInteger(args.seed)) die(`bad --seed ${v}`); }
  else if (a === '--only') { args.only = v.split(',').map(Number); i++; }
  else if (a === '--no-noise') { args.noise = false; }
  else die(`unknown argument ${a}`);
}
if (args.out === null) args.out = path.join(__dirname, '..', 'golden', ...(args.noise ? [] : ['nonoise']));
function die(msg) { process.stderr.write(`make_traces: ${msg}\n`); process.exit(2); }

// ---------------------------------------------------------------------------------------------- extract the reference
const html = fs.readFileSync(args.html, 'utf8').replace(/\r\n/g, '\n');
const lines = html.split('\n');
const iStart = lines.findIndex(l => l.startsWith('const clamp = '));
const iEnd = lines.findIndex((l, i) => i > iStart && l.includes('//  Charts'));
if (iStart < 0) die('marker "const clamp = " not found');
if (iEnd < 0) die('marker "//  Charts" not found after "const clamp = "');
// the banner line before "//  Charts" is a comment, harmless to include
const core = lines.slice(iStart, iEnd).join('\n');
const iST = lines.findIndex(l => l.startsWith('const SELFTEST = ['));
if (iST < 0) die('marker "const SELFTEST = [" not found');
const iSTEnd = lines.findIndex((l, i) => i > iST && l.trim() === '];');
if (iSTEnd < 0) die('closing "];" of SELFTEST not found');
const selftest = lines.slice(iST, iSTEnd + 1).join('\n');

// ---------------------------------------------------------------------------------------------- seeded Math.random
let rngState = 0;
function seedRng(s) { rngState = s >>> 0; }
function mulberry32() {
  rngState = (rngState + 0x6D2B79F5) >>> 0;
  let t = rngState;
  t = Math.imul(t ^ (t >>> 15), t | 1);
  t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
}
seedRng(args.seed);
Math.random = mulberry32;

// The slice defines two display stations (harmless) and references no DOM. resetGauss clears the cached second
// Box-Muller deviate so each scenario's random stream depends only on the seed, not on the scenarios run before it.
const wrapper = `(function () {\n${core}\n${selftest}\n` +
  `return { Station, Controller, Plant, SCENARIOS, SELFTEST, fmtT, resetGauss: () => { _g = null; } };\n})`;
const REF = vm.runInThisContext(wrapper, { filename: 'sample_gas_simulator.html(core)' })();
const { Station, Controller, Plant, SCENARIOS, SELFTEST } = REF;

// ---------------------------------------------------------------------------------------------- trace writer
const CHUNK = 1 << 20;
let out = null;   // { fd, buf: [], len, lines }
function openTrace(file) { out = { fd: fs.openSync(file, 'w'), buf: [], len: 0, lines: 0 }; }
function flush() { if (out.buf.length) { fs.writeSync(out.fd, out.buf.join('')); out.buf = []; out.len = 0; } }
function closeTrace() { flush(); fs.closeSync(out.fd); const n = out.lines; out = null; return n; }
function emit(line) {
  if (line.includes('\n')) throw new Error(`newline inside a trace record: ${JSON.stringify(line)}`);
  out.buf.push(line, '\n'); out.len += line.length + 1; out.lines++;
  if (out.len >= CHUNK) flush();
}
function num(v) {
  if (v === null) return 'null';
  if (typeof v === 'boolean') return v ? '1' : '0';
  if (typeof v !== 'number') throw new Error(`expected a number, got ${typeof v} ${String(v)}`);
  return String(v);
}
function str(v) {   // a string field that is last on its line
  if (typeof v !== 'string') throw new Error(`expected a string, got ${typeof v} ${String(v)}`);
  return v;
}
function word(v) {   // a string field that is not last on its line must be a single token
  const s = String(v);
  if (!s.length || /\s/.test(s)) throw new Error(`field is not a single token: ${JSON.stringify(s)}`);
  return s;
}

// ---------------------------------------------------------------------------------------------- instrumentation
let recording = false;
let ctlDepth = 0;
let station = null;   // the station being recorded
let lastS = null, lastX = null, expectResetEnter = false;

const RECORDED = new Set(['opPurge', 'opFlowZero', 'opResume', 'opIdle', 'setTarget', 'newCylinder', 'ledgerEvent',
                          'crash', 'restart']);
// depth-0 calls that are neither recorded nor allowed to change controller state
const PURE_AT_TOP = new Set(['expectedFlow', 'worstSev', 'usageReport']);

function snapS(ctl) { return `S ${word(ctl.p.mode)} ${num(ctl.p.target)}`; }
function emitS(ctl, force) { const s = snapS(ctl); if (force || s !== lastS) { emit(s); lastS = s; } }
function emitI(ctl, t) {
  const pl = ctl.st.plant, an = pl.an, r = pl.rbv;
  emit(`I ${num(t)} ${num(an.value)} ${num(an.sevr)} ${num(r.flow)} ${num(r.sp)} ${num(!!r.running)} ` +
       `${num(r.ramp)} ${num(r.total)} ${str(r.gas)}`);
}
function emitX(ctl, t) {
  const body = `${word(ctl.state)} ${num(ctl.lastCmd)} ${num(ctl.epid.OVAL)} ${num(!!ctl.epid.FBON)} ` +
               `${num(ctl.worstSev())} ${num(ctl.overrideCount)} ${num(ctl.cylLeftL)} ${num(ctl.cumL)}`;
  if (body !== lastX || t % 600 === 0) { emit(`X ${num(t)} ${body}`); lastX = body; }
}

function hnum(v) {   // H-line number: null/undefined -> "null", boolean -> 0/1, else String(v)
  if (v === null || v === undefined) return 'null';
  if (typeof v === 'boolean') return v ? '1' : '0';
  if (typeof v !== 'number') throw new Error(`H line: expected a number, got ${typeof v} ${String(v)}`);
  return String(v);
}
let lastH = null;   // t of the last H line
function emitH(ctl, t) {
  if (ctl.down) return;
  recording = false;                         // a pure read, outside the recorded call tree
  let u;
  try { u = ctl.usageReport(); } finally { recording = true; }
  const f = [t, u.wStart, u.wEnd, u.dispensed, u.inRuns, u.cyls, u.cylEquiv, u.runs.length];
  for (const r of u.runs) f.push(r.start, r.end, r.purges, r.L, r.cylinders, r.finished);
  f.push(ctl.cylEst.length);
  for (const e of ctl.cylEst) f.push(e.h, e.rate);
  f.push(ctl.cylMedianH);
  emit('H ' + f.map(hnum).join(' '));
  lastH = t;
}

function beforeTop(name, ctl, a) {
  const t = ctl.st.t;
  if (RECORDED.has(name)) {
    emitS(ctl, true); emitI(ctl, t);
    let arg = '';
    if (name === 'setTarget') arg = ' ' + num(a[0]);
    else if (name === 'newCylinder' || name === 'ledgerEvent') arg = ' ' + word(a[0]);
    emit(`C ${num(t)} ${name}${arg}`);
  } else if (name === 'tick') {
    emitS(ctl, false); emitI(ctl, t); emit(`T ${num(a[0])}`);
  } else if (name === 'processEpid') {
    emit(`P ${num(t)} ${num(ctl.st.plant.a.spVal)}`);
  } else if (name === 'reinit') {
    emit('R'); lastS = null; lastX = null; expectResetEnter = true;
    return;
  } else if (name === 'enter') {
    if (!(expectResetEnter && a[0] === 'IDLE' && a[1] === 'station reset'))
      throw new Error(`unrecorded top-level enter(${a[0]}, ${a[1]}) at t=${t}`);
  } else if (!PURE_AT_TOP.has(name)) {
    throw new Error(`unrecorded top-level controller call ${name}() at t=${t}`);
  }
  if (name !== 'enter') expectResetEnter = false;
}
function afterTop(name, ctl, a) {
  if (name === 'enter') expectResetEnter = false;
  if (name === 'tick') {
    emitX(ctl, a[0]);
    if (a[0] % 3600 === 0) emitH(ctl, a[0]);
  }
}

for (const name of Object.getOwnPropertyNames(Controller.prototype)) {
  if (name === 'constructor') continue;
  const d = Object.getOwnPropertyDescriptor(Controller.prototype, name);
  if (typeof d.value !== 'function') continue;   // skip accessors (get plant)
  const orig = d.value;
  Controller.prototype[name] = function (...a) {
    const top = recording && ctlDepth === 0 && this.st === station;
    if (top) beforeTop(name, this, a);
    const sd = name === 'doPurge' && recording && this.st === station ? this.sd : null;
    ctlDepth++;
    let ret;
    try { ret = orig.apply(this, a); } finally { ctlDepth--; }
    if (sd) noteLidCheck(this, sd);
    if (top) afterTop(name, this, a);
    return ret;
  };
}

// The lid-check decision (spec §8.9), for the --no-noise summary: doPurge keeps it in its state
// data (sd), which a stopOpen replaces within the same call, hence the capture around doPurge.
// Read-only: sd is never modified.
let lidChecks = [];
const lidSeen = new WeakSet();
function noteLidCheck(ctl, sd) {
  if (typeof sd.checkAt !== 'number' || lidSeen.has(sd)) return;
  lidSeen.add(sd);
  lidChecks.push({ t: ctl.now, ratio: sd.kin, curv: sd.curv, kObs: sd.kObs, onsetT: sd.onsetT, checkAt: sd.checkAt,
                   cOn: sd.cOn, cCheck: sd.cCheck, o2Start: sd.o2Start, timerT0: sd.timerT0 });
}

function wrapPut(name, fmt) {
  const orig = Plant.prototype[name];
  if (typeof orig !== 'function') die(`Plant.prototype.${name} missing`);
  Plant.prototype[name] = function (...a) {
    if (recording && ctlDepth > 0 && station && this === station.plant) emit(`A ${num(station.t)} ${fmt(a)}`);
    return orig.apply(this, a);
  };
}
wrapPut('putSetpoint', a => `sp ${num(a[0])}`);
wrapPut('putRamp', a => `ramp ${num(a[0])}`);
wrapPut('putRun', () => 'run');

const origLog = Station.prototype.log;
Station.prototype.log = function (sev, msg, kind) {
  if (recording && ctlDepth > 0 && this === station) emit(`L ${num(this.t)} ${num(sev)} ${String(msg)}`);
  return origLog.call(this, sev, msg, kind);
};

// ---------------------------------------------------------------------------------------------- run the scenarios
const STEPS_PER_S = 4;   // = Math.round(1 / DT) in the reference
fs.mkdirSync(args.out, { recursive: true });
const summaryFile = path.join(args.out, 'summary.json');
const summary = (args.only && fs.existsSync(summaryFile)) ? JSON.parse(fs.readFileSync(summaryFile, 'utf8')) : {};

const runs = SCENARIOS.map(sc => {
  const m = /^(\d+)\./.exec(sc.name);
  if (!m) die(`scenario name without a number: ${sc.name}`);
  const T = SELFTEST.find(r => sc.name.startsWith(r.n));
  if (!T) die(`no SELFTEST row for ${sc.name}`);
  return { n: Number(m[1]), sc, T };
}).sort((x, y) => x.n - y.n);
if (runs.length !== SELFTEST.length) die(`${runs.length} scenarios but ${SELFTEST.length} SELFTEST rows`);

let fails = 0;
for (const { n, sc, T } of runs) {
  if (args.only && !args.only.includes(n)) continue;
  const t0 = Date.now();
  seedRng(args.seed); REF.resetGauss();
  const st = new Station('SELFTEST', 'TEST:SampleGas:', 'TEST:Alicat:', 'TEST:O2', '');
  st.silent = true;
  if (!args.noise) {   // st.pp is the plant's own parameter object (Station constructor)
    for (const k of ['noise', 'wanderRel']) {
      if (typeof st.pp[k] !== 'number') die(`plant parameter ${k} missing from defaultPlantParams()`);
      st.pp[k] = 0;
    }
  }
  const file = path.join(args.out, `sc${String(n).padStart(2, '0')}.trace`);
  openTrace(file);
  station = st; lastS = null; lastX = null; lastH = null; expectResetEnter = false; lidChecks = []; recording = true;
  st.startScenario(sc);
  for (let k = 0; k < T.dur * STEPS_PER_S; k++) st.step();
  const tEnd = Math.round(st.t);
  if (lastH !== tEnd) emitH(st.ctl, tEnd);   // the end-of-scenario report
  recording = false;
  if (n === 19) {   // spec §8.17 validation: Dispensed = Σ runs within 1 L
    const u = st.ctl.usageReport(), sum = u.runs.reduce((a, r) => a + r.L, 0);
    console.log(`sc19  dispensed ${u.dispensed}  Σ runs ${sum}  inRuns ${u.inRuns}  ` +
                `|dispensed − Σ runs| ${Math.abs(u.dispensed - sum)}`);
  }
  if (ctlDepth !== 0) throw new Error(`ctlDepth ${ctlDepth} after scenario ${n}`);
  const nLines = closeTrace();
  // evaluate exactly as runSelfTest does
  const msgs = (st.privateLog || []).map(e => e.msg).join('\n');
  const probs = [];
  if (T.state && st.ctl.state !== T.state) probs.push(`ended ${st.ctl.state}, expected ${T.state}`);
  for (const h of T.has) if (!new RegExp(h).test(msgs)) probs.push(`no log matching /${h}/`);
  if (T.check) { const msg = T.check(st); if (msg) probs.push(msg); }
  if (probs.length) fails++;
  station = null;
  summary[String(n)] = { state: st.ctl.state, selftest: probs.length ? probs : 'pass' };
  if (!args.noise) Object.assign(summary[String(n)], { noise: false, seed: args.seed, lidChecks });
  const secs = ((Date.now() - t0) / 1000).toFixed(1);
  console.log(`sc${String(n).padStart(2, '0')}  ${String(st.ctl.state).padEnd(9)}  ${probs.length ? 'FAIL ' + probs.join('; ') : 'pass'}` +
              `  ${T.dur} s  ${nLines} lines  (${secs} s)`);
}
fs.writeFileSync(summaryFile, JSON.stringify(summary, null, 1) + '\n');
process.exit(fails ? 1 : 0);
