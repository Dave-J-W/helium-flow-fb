"""test_compare.py: spec §14.2 quantitative comparison with the reference (Plan 4 task 3).

For scenarios 1, 2, 3 and 8 the bench IOC runs against the plant simulator with the noise off
(plant_sim.py --no-noise), in real time, and its state transitions (from the IOC log), the flows
at them (Sts:Flow, the Flow_RBV the controller used at that tick) and the lid-check ratio are
compared with the reference simulator's own noise-free run (golden/nonoise/, written by
`node ref/make_traces.js --no-noise`):

  state-transition times within ±3 s, flows at the transitions within ±0.02 SLPM,
  lid-check ratio within ±0.05.

    SG_COMPARE=2,3,8 python -m unittest -v test_compare      (default 1,2,3,8; sc01 is 1 h)
    SG_COMPARE_FROM=results/compare-sc02-<stamp>.json         re-compare a saved run, no bench

Each run writes results/compare-sc<NN>-<stamp>.json (everything recorded) and .txt (the table)
before it asserts. Other log events (lid check, frozen O2, hold) are listed with their time
differences for information; only the three quantities above are asserted.

Time base. The reference runs scenario time t from 0 and applies an event at t just before its
tick t (Station.step: sample, poll, runScenario, tick). Here t = 0 is IOC tick S0:
  * sc01: the IOC is restarted (into IDLE) so that its first tick is t = 1, as the reference's
    fresh station, S0 = that tick - 1 (preset_aligned, preset=False: an IOC that had ticked
    since the Bench came up carried that time into its per-tick state; with the noise off the
    constant air reading reached the 30-tick frozen-O2 count before the purge's fall arrived);
  * sc02: a tick picked a couple of seconds after the Bench is up; the reference setup
    (LiftLid) is applied as an event at t = 0, because the lid-lift ambient transient makes
    the lift-to-purge time matter;
  * sc03, sc08 (preset): the IOC's restart tick ("— → REGULATE (restart ...") minus 1. The IOC's
    first tick is the restart decision and a normal tick at once (sgCore.h "First tick"); the
    reference restarts at t = 0 (presetRegulating, outside a tick) and ticks first at t = 1. So
    the IOC's restart transition shows at t = 1 against the reference's 0. The plant preset is
    made a few seconds earlier, while the IOC is down (preset lead_s).
  S0 is also a multiple of Par:pidScan: the IOC steps its PID at epoch now % pidScan == 0, the
  reference at t % pidScan == 0 (for the preset, the IOC start is retimed until it is).
An event at t is put at wall S0 + t - 0.15 s: after the plant's publish for tick t (whole
second - 0.25 s, plant_sim --phase 0.75) and before the IOC's tick t, as in the reference.

Known deliberate difference, shown and not failed: after a bumpless PID start (spec §8.11) the
IOC's first output is lastCmd, the reference's lastCmd + KP·e (epid's P). A flow at a transition
out of that REGULATE may differ by that bump (read from the reference trace); it passes only if
the difference less the bump is within ±0.02 SLPM, and the table says so.
"""
import json
import math
import os
import re
import statistics
import subprocess
import time
import unittest
from pathlib import Path

import epics

from bench import HERE, RESULTS, WORLD, Bench, BenchError
from scenarios import BY_N
from test_scenarios import apply, expected_flow

P = 'SIM:SampleGas:'
GOLDEN = HERE / 'golden' / 'nonoise'
REPO = HERE.parents[1]
MSYS_BASH = r'C:\msys64\usr\bin\bash.exe'
COMPARED = [1, 2, 3, 8]
TOL_T, TOL_FLOW, TOL_RATIO = 3.0, 0.02, 0.05
LEAD = 0.15                        # s before the IOC tick an event is put
PRESET_SC = (3, 8)
# informational events: (label, regex); the n-th match in the reference pairs with the n-th here
EVENTS = [('lid check passed', r'^lid check passed'),
          ('lid check open', r'enclosure open\?$'),
          ('O2 frozen', r'^O2 reading frozen$'),
          ('O2 frozen cleared', r'^cleared: O2 reading frozen'),
          ('hold: wrote Run', r'^MFC on hold: wrote Run'),
          ('hold resumed', r'MFC was on hold, resumed'),
          ('flow mismatch', r'^flow mismatch'),
          ('settled', r'^settled: '),
          ('O2 range alarm', r'^O2 (above|below) target range|^O2 abnormally high'),
          ('PID alarm', r'^PID pinned|not reached'),
          ('lid opened', r'enclosure opened, flow stopped')]
RX_TRANS = re.compile(r'^(—|[A-Z_]+) → ([A-Z_]+) \((.*)\)$')
RX_DECAY = re.compile(r'decay (\d+) % of the lid-on rate')
MONITORS = [('hb', P + 'Sts:Heartbeat'), ('state', P + 'Sts:State'), ('flow', P + 'Sts:Flow'),
            ('lastCmd', P + 'Sts:LastCmd'), ('o2', P + 'Sts:O2'), ('lidRatio', P + 'Diag:LidRatio'),
            ('lidCurv', P + 'Diag:LidCurv'), ('onsetT', P + 'Diag:LidOnsetT'),
            ('elapsed', P + 'Diag:PurgeElapsed'), ('plantFlow', 'SIM:Alicat1:Flow_RBV'),
            ('plantO2', 'SIM:O2'), ('plantTime', WORLD + 'Time')]


def selected():
    s = os.environ.get('SG_COMPARE', '').strip()
    return [int(x) for x in s.split(',') if x.strip()] if s else list(COMPARED)


# ---------------------------------------------------------------------------------------------- reference
def ensure_reference():
    """golden/nonoise/ (gitignored): make it with node under MSYS2 MINGW64 if it is missing."""
    if (GOLDEN / 'summary.json').exists() and all((GOLDEN / f'sc{n:02d}.trace').exists() for n in COMPARED):
        return
    env = dict(os.environ, MSYSTEM='MINGW64', CHERE_INVOKING='1')
    subprocess.run([MSYS_BASH, '-l', '-c', 'node ioc/test/ref/make_traces.js --no-noise --only '
                    + ','.join(map(str, COMPARED))], cwd=str(REPO), env=env, check=False)
    if not (GOLDEN / 'summary.json').exists():
        raise RuntimeError(f'no {GOLDEN}: run node ioc/test/ref/make_traces.js --no-noise')


def load_reference(n):
    """Transitions, events and the lid check of the reference's noise-free scenario n.
    Flow/O2 at a line = the last I record before it (the readbacks the controller had then)."""
    ensure_reference()
    trans, logs, last_i = [], [], None
    bumps, p_line, want_bump = [], None, False   # epid's start bump P = KP·e (spec §8.11)
    with open(GOLDEN / f'sc{n:02d}.trace', encoding='utf-8') as fh:
        for ln in fh:
            ln = ln.rstrip('\n')
            if ln.startswith('P '):
                f = ln.split(' ')
                p_line = (int(float(f[1])), float(f[2]))
                if want_bump:                  # first PID step after entering REGULATE: no put = no bump
                    bumps.append({'t': p_line[0], 'bump': 0.0})
                    want_bump = False
            elif ln.startswith('A ') and ' sp ' in ln and p_line and bumps and bumps[-1]['t'] == p_line[0] \
                    and int(float(ln.split(' ')[1])) == p_line[0] and bumps[-1].get('put') is None:
                v = float(ln.split(' ')[3])
                bumps[-1].update(put=v, before=p_line[1], bump=v - p_line[1])
            if ln.startswith('I '):
                f = ln.split(' ')
                last_i = {'o2': float(f[2]), 'sevr': float(f[3]), 'flow': float(f[4]), 'sp': float(f[5])}
            elif ln.startswith('L '):
                _, t, sev, msg = ln.split(' ', 3)
                t = int(float(t))
                logs.append({'t': t, 'sev': int(float(sev)), 'msg': msg})
                m = RX_TRANS.match(msg)
                if m and m.group(3) != 'station reset':
                    trans.append({'t': t, 'from': m.group(1), 'to': m.group(2), 'reason': m.group(3),
                                  'flow': last_i['flow'] if last_i else math.nan,
                                  'o2': last_i['o2'] if last_i else math.nan})
                    want_bump = m.group(2) == 'REGULATE'
    summ =json.loads((GOLDEN / 'summary.json').read_text(encoding='utf-8'))[str(n)]
    if summ.get('noise') is not False:
        raise RuntimeError(f'{GOLDEN}/summary.json scenario {n} is not a --no-noise run')
    lid = summ.get('lidChecks') or []
    return {'n': n, 'transitions': trans, 'log': logs, 'lid': lid[0] if lid else None, 'bumps': bumps,
            'end_state': summ['state'], 'seed': summ.get('seed')}


# ---------------------------------------------------------------------------------------------- IOC log
def parse_ioc_log(lines):
    """[(epoch second, severity, message)] from the IOC log lines (local wall time, 1 s)."""
    out = []
    for ln in lines:
        if len(ln) < 21 or ln[4] != '-' or ln[13] != ':':
            continue
        try:
            sec = int(time.mktime(time.strptime(ln[:19], '%Y-%m-%d %H:%M:%S')))
        except ValueError:
            continue
        rest = ln[19:].strip().split(None, 1)
        if len(rest) < 2:
            continue
        body = rest[1]
        sev = ''
        for s in ('MINOR', 'MAJOR', 'INVALID'):
            if body.startswith(s + '  '):
                sev, body = s, body[len(s):]
        out.append((sec, sev, body.strip()))
    return out


def ioc_extract(rec):
    """Transitions and events in scenario time (t = IOC tick second - S0), with the flows the IOC
    had at those ticks (from the Sts:Flow monitor)."""
    s0 = rec['S0']
    parsed = [e for e in parse_ioc_log(rec['log']) if e[0] >= s0]
    flow = rec['mon'].get('flow', [])
    o2 = rec['mon'].get('o2', [])
    trans, logs = [], []
    for sec, sev, msg in parsed:
        logs.append({'t': sec - s0, 'sev': sev, 'msg': msg})
        m = RX_TRANS.match(msg)
        if m:
            trans.append({'t': sec - s0, 'from': m.group(1), 'to': m.group(2), 'reason': m.group(3),
                          'flow': value_at(flow, sec), 'o2': value_at(o2, sec)})
    return trans, logs


def value_at(series, sec):
    """The value a monitor showed at the end of IOC tick `sec`: the last update stamped before
    sec + 1 (IOC records carry the tick's time, whole second + a few ms)."""
    v = math.nan
    for ts, val in series:
        if ts < sec + 1:
            v = val
        else:
            break
    return v


def lid_from_ioc(rec, logs):
    """The IOC's lid-check ratio: Diag:LidRatio when it was published (it is the state data, so an
    OPEN_STOP in the same tick clears it before publishing), else the log's integer percent."""
    vals = [v for _, v in rec['mon'].get('lidRatio', []) if isinstance(v, float) and math.isfinite(v)]
    curv = [v for _, v in rec['mon'].get('lidCurv', []) if isinstance(v, float) and math.isfinite(v)]
    onset = [v for _, v in rec['mon'].get('onsetT', []) if isinstance(v, float) and math.isfinite(v)]
    for e in logs:
        m = RX_DECAY.search(e['msg'])
        if m:
            pct = int(m.group(1)) / 100
            if vals:
                return {'t': e['t'], 'ratio': vals[-1], 'source': 'Diag:LidRatio', 'log_ratio': pct,
                        'curv': curv[-1] if curv else None, 'onsetT': onset[-1] if onset else None}
            return {'t': e['t'], 'ratio': pct, 'source': 'log (integer %, ±0.005)', 'log_ratio': pct,
                    'curv': None, 'onsetT': onset[-1] if onset else None}
    return None


# ---------------------------------------------------------------------------------------------- bench run
def _monitor(b, rec, key, pv):
    series = rec['mon'].setdefault(key, [])

    def cb(value=None, timestamp=None, **kw):
        v = value
        if hasattr(v, 'item'):
            v = v.item()
        series.append((float(timestamp) if timestamp else time.time(), v))
    p = epics.PV(pv, form='time', auto_monitor=True, callback=cb)
    b._watches.append(p)
    return p


def _wait_until(wall):
    while True:
        d = wall - time.time()
        if d <= 0:
            return
        time.sleep(min(d, 0.05) if d < 1 else 0.5)


def _tick_second(b, timeout=10):
    """The whole second of the IOC tick just seen (the tick runs a few ms after it)."""
    return int(math.floor(b.next_tick(timeout)))


def preset_aligned(b, rec, scan, attempts=6, preset=True):
    """The reference presetRegulating: plant World:Preset while the IOC is down, then an IOC start
    whose first tick (restart decision + tick, sgCore.h "First tick") is the reference's tick 1,
    so S0 = that tick - 1. The IOC steps its PID at epoch now % pidScan == 0, the reference at
    t % pidScan == 0: the start is retimed (kill, preset, start again) until S0 % pidScan == 0.

    preset=False (sc01, sc02): the same aligned restart without the preset, into IDLE. The
    reference's station starts fresh at t = 0; an IOC that had been ticking since the Bench came up
    carried that time in its per-tick state into the scenario. With the noise off the air reading
    is exactly constant, so the frozen-O2 count (frozenTime = 30 identical ticks, §8.2) reached 30
    before the purge's O2 fall arrived whenever the Bench had been up more than ~15 s before S0
    (sc01 of 2026-09-29 18:30: "O2 reading frozen" 9 s into the purge, a blind purge, OPEN_LOOP)."""
    want = 'REGULATE' if preset else 'IDLE'
    b.wait_autosaved(['Par:writeEnable', 'Mode', 'Par:target'])
    f = expected_flow(b) if preset else None
    offset = None                              # restart tick - start time, from the last attempt
    tried = []
    for k in range(attempts):
        mark = b.mark_log()
        b.stop_ioc(kill=True)
        if offset is not None:
            r = math.ceil(time.time() + 1.0 + offset)
            r += -(r - 1) % scan
            _wait_until(r - offset - 0.3)
        t_preset = time.time()
        if preset:
            b.world('Preset', f)
            _wait_until(t_preset + 0.3)
        t_start = time.time()
        b.start_ioc()
        b.wait_state(want, 60)
        rs = [sec for sec, _, msg in parse_ioc_log(b.log_lines(since=mark))
              if msg.startswith(f'— → {want} (restart')]
        if not rs:
            raise BenchError(f'no "— → {want} (restart" line after the IOC start')
        offset = rs[-1] - t_start
        s0 = rs[-1] - 1
        tried.append({'restart_tick': rs[-1], 'start': round(t_start, 3), 'offset': round(offset, 3),
                      'phase': s0 % scan})
        if s0 % scan == 0:
            if preset:
                rec['preset'] = {'flow': f, 'wall': round(t_preset, 3), 'lead_s': round(s0 - t_preset, 3),
                                 'attempts': tried}
            else:
                rec['restart'] = {'attempts': tried}
            rec['log_mark'] = mark
            return s0
    raise BenchError(f'IOC restart tick not aligned to pidScan {scan} in {attempts} attempts: {tried}')


def run_ioc(n):
    sc = BY_N[n]
    stamp = time.strftime('%Y%m%d-%H%M%S')
    rec = {'n': n, 'name': sc['name'], 'dur': sc['dur'], 'noise': False, 'seed': n, 'started': stamp,
           'events': [], 'mon': {}, 'problems': []}
    path = RESULTS / f'compare-sc{n:02d}-{stamp}.json'
    try:
        with Bench(noise=False, seed=n, tag=f'cmp{n:02d}') as b:
            events = sorted(sc['events'], key=lambda e: e[0])
            scan = max(1, int(round(b.get(P + 'Par:pidScan'))))
            rec['pidScan'] = scan
            if n in PRESET_SC:
                if sc['setup'] != [('preset', None)]:
                    raise BenchError(f'sc{n}: unexpected setup {sc["setup"]}')
                s0 = preset_aligned(b, rec, scan)
            else:
                # the setup (sc02 LiftLid) becomes an event at t = 0 (see the module docstring)
                events = [(0, a, arg) for a, arg in sc['setup']] + events
                if events and events[0][0] >= 3:
                    # sc01: the IOC is restarted so that it starts, like the reference, at t = 0
                    # (preset_aligned, preset=False: the restart tick is t = 1, already past when
                    # it returns, so only an event a few seconds later can still be put in time)
                    s0 = preset_aligned(b, rec, scan, preset=False)
                else:
                    # sc02 (an event at t = 0): the IOC has been ticking ~10-20 s at S0; harmless
                    # there (the lid-off reading moves at once), see preset_aligned
                    s0 = _tick_second(b) + 3
                    s0 += -s0 % scan                   # PID steps at the reference's t % pidScan == 0
                    rec['log_mark'] = b.mark_log()
            for key, pv in MONITORS:
                _monitor(b, rec, key, pv)
            rec['S0'] = s0
            if time.time() > s0 + (events[0][0] if events else 0) - LEAD:
                raise BenchError('setup ran past the first event time')
            for t, action, arg in events:
                _wait_until(s0 + t - LEAD)
                a0 = time.time()
                note = apply(b, action, arg)
                a1 = time.time()
                rec['events'].append({'t': t, 'action': action, 'arg': arg, 'note': note,
                                      'put_start': round(a0 - s0, 3), 'put_done': round(a1 - s0, 3)})
                if a1 >= s0 + t:
                    rec['problems'].append(f'event {action} {arg} at t={t} finished {a1 - s0 - t:+.3f} s after '
                                           'the tick it was meant for')
            _wait_until(s0 + sc['dur'] + 1.5)
            rec['end_state'] = b.state()
            rec['log'] = b.log_lines(since=rec['log_mark'])
    except Exception as e:                     # harness failure: still write the results file
        rec['problems'].append(f'harness: {type(e).__name__}: {e}')
    rec['path'] = str(path)
    path.write_text(json.dumps(rec, indent=0, ensure_ascii=False, default=str), encoding='utf-8')
    return rec


# ---------------------------------------------------------------------------------------------- comparison
def _phase_stats(series):
    fr = [ts - math.floor(ts) for ts, _ in series if ts]
    if len(fr) < 3:
        return None
    return {'n': len(fr), 'median': round(statistics.median(fr), 3), 'min': round(min(fr), 3),
            'max': round(max(fr), 3)}


def start_bump(ref, rt, i):
    """The reference's epid start bump in force at transition i: a transition out of REGULATE whose
    REGULATE began with a PID start step before it (spec §8.11), else None."""
    if rt[i]['from'] != 'REGULATE' or i == 0 or rt[i - 1]['to'] != 'REGULATE':
        return None
    for bmp in ref.get('bumps', []):
        if rt[i - 1]['t'] <= bmp['t'] <= rt[i]['t'] and bmp['bump']:
            return bmp
    return None


def compare(ref, rec):
    """(rows, problems, info): rows for the table; problems = tolerance or structure failures."""
    probs = list(rec.get('problems', []))
    rows, info = [], {}
    if 'S0' not in rec or 'log' not in rec:
        return rows, probs or ['no IOC data'], info
    trans, logs = ioc_extract(rec)
    if 'restart' in rec:           # the aligned IOC start into IDLE is the reference's station reset
        trans = [c for c in trans if not (c['from'] == '—' and c['to'] == 'IDLE')]
    rt = ref['transitions']
    broken = False
    for i, r in enumerate(rt):
        if i >= len(trans):
            probs.append(f"transition {r['from']} → {r['to']} (ref t={r['t']}) missing on the IOC")
            rows.append(('transition', f"{r['from']} → {r['to']}", r['t'], None, None, r['flow'], None, None, 'MISSING'))
            continue
        c = trans[i]
        if (c['from'], c['to']) != (r['from'], r['to']):
            probs.append(f"transition {i + 1}: IOC {c['from']} → {c['to']} at t={c['t']}, "
                         f"reference {r['from']} → {r['to']} at t={r['t']}")
            rows.append(('transition', f"{r['from']} → {r['to']} / IOC {c['from']} → {c['to']}", r['t'], c['t'],
                         None, r['flow'], c['flow'], None, 'SEQUENCE'))
            broken = True
            break
        dt, df = c['t'] - r['t'], c['flow'] - r['flow']
        ok_t, ok_f = abs(dt) <= TOL_T, abs(df) <= TOL_FLOW + 1e-9
        note = ''
        bump = start_bump(ref, rt, i)
        if not ok_f and bump and abs(df + bump['bump']) <= TOL_FLOW + 1e-9:
            # spec §8.11 (deliberate): the IOC's first PID output is lastCmd; the reference's adds
            # epid's P = KP·e. Shown, and the rest of the difference still held to the tolerance.
            ok_f = True
            note = (f" (EXPECTED §8.11: reference start bump {bump['bump']:+.4f} SLPM at t={bump['t']}; "
                    f"Δ without it {df + bump['bump']:+.3f})")
        if not ok_t:
            probs.append(f"{r['from']} → {r['to']}: t IOC {c['t']} vs reference {r['t']} (Δ {dt:+d} s > ±{TOL_T:g})")
        if not ok_f:
            probs.append(f"{r['from']} → {r['to']}: flow IOC {c['flow']} vs reference {r['flow']} "
                         f"(Δ {df:+.3f} SLPM > ±{TOL_FLOW})")
        rows.append(('transition', f"{r['from']} → {r['to']}", r['t'], c['t'], dt, r['flow'], c['flow'], df,
                     ('ok' if ok_t and ok_f else 'FAIL') + note))
    for c in [] if broken else trans[len(rt):]:
        probs.append(f"extra IOC transition {c['from']} → {c['to']} at t={c['t']} ({c['reason']})")
        rows.append(('transition', f"(IOC only) {c['from']} → {c['to']}", None, c['t'], None, None, c['flow'],
                     None, 'EXTRA'))
    # the lid check
    rl, cl = ref['lid'], lid_from_ioc(rec, logs)
    if rl is not None or cl is not None:
        if rl is None or cl is None:
            probs.append(f'lid check only in the {"IOC" if rl is None else "reference"}')
            rows.append(('lid ratio', '', rl and rl['t'], cl and cl['t'], None, rl and rl['ratio'],
                         cl and cl['ratio'], None, 'MISSING'))
        else:
            dr = cl['ratio'] - rl['ratio']
            ok = abs(dr) <= TOL_RATIO
            if not ok:
                probs.append(f"lid-check ratio IOC {cl['ratio']:.4f} vs reference {rl['ratio']:.4f} "
                             f"(Δ {dr:+.4f} > ±{TOL_RATIO})")
            rows.append(('lid ratio', cl['source'], rl['t'], cl['t'], cl['t'] - rl['t'], rl['ratio'], cl['ratio'],
                         dr, 'ok' if ok else 'FAIL'))
            info['lid'] = {'ref': rl, 'ioc': cl}
    # informational events
    for label, rx in EVENTS:
        rr = [e for e in ref['log'] if re.search(rx, e['msg'])]
        cc = [e for e in logs if re.search(rx, e['msg'])]
        for j in range(max(len(rr), len(cc))):
            a = rr[j]['t'] if j < len(rr) else None
            c = cc[j]['t'] if j < len(cc) else None
            rows.append(('event (info)', label, a, c, None if a is None or c is None else c - a,
                         None, None, None, 'info' if a is not None and c is not None
                         else ('ref only' if c is None else 'IOC only')))
    if ref['end_state'] != rec.get('end_state'):
        probs.append(f"end state IOC {rec.get('end_state')}, reference {ref['end_state']}")
    info['end_state'] = {'ref': ref['end_state'], 'ioc': rec.get('end_state')}
    info['ioc_tick_phase_s'] = _phase_stats(rec['mon'].get('hb', []))
    info['plant_publish_phase_s'] = _phase_stats(rec['mon'].get('plantTime', []))
    if 'preset' in rec:
        info['preset_lead_s'] = rec['preset']['lead_s']
    return rows, probs, info


def _f(v, fmt):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return '-'
    return format(v, fmt)


def table(n, ref, rec, rows, probs, info):
    out = [f"sc{n:02d} {BY_N[n]['name']}: bench IOC (plant --no-noise, seed {rec.get('seed')}) vs reference "
           f"(noise 0, seed {ref.get('seed')}); t = s after IOC tick S0 / reference t",
           f"tolerances: t ±{TOL_T:g} s, flow ±{TOL_FLOW} SLPM, lid ratio ±{TOL_RATIO}",
           f"{'kind':<13} {'what':<44} {'t ref':>6} {'t IOC':>6} {'Δt':>4} {'ref':>8} {'IOC':>8} {'Δ':>8}  result"]
    for kind, what, tr, tc, dt, vr, vc, dv, res in rows:
        fmt = '.4f' if kind == 'lid ratio' else '.2f'
        dfmt = '+.4f' if kind == 'lid ratio' else '+.3f'
        out.append(f"{kind:<13} {what[:44]:<44} {_f(tr, 'd') if isinstance(tr, int) else _f(tr, '.0f'):>6} "
                   f"{_f(tc, 'd') if isinstance(tc, int) else _f(tc, '.0f'):>6} "
                   f"{_f(dt, '+d') if isinstance(dt, int) else _f(dt, '+.0f'):>4} "
                   f"{_f(vr, fmt):>8} {_f(vc, fmt):>8} {_f(dv, dfmt):>8}  {res}")
    out.append('(flows: Flow_RBV the controller used at that tick; ref = the I record, IOC = Sts:Flow)')
    for k, v in info.items():
        if k != 'lid':
            out.append(f'{k}: {v}')
    if 'lid' in info:
        r, c = info['lid']['ref'], info['lid']['ioc']
        out.append(f"lid check: ref onsetT {r.get('onsetT')} checkAt {r.get('checkAt')} (el, s) curv "
                   f"{_f(r.get('curv'), '.3f')}; IOC onsetT {c.get('onsetT')} curv {_f(c.get('curv'), '.3f')}; "
                   f"IOC log {c.get('log_ratio')}")
    out.append('PASS' if not probs else 'FAIL: ' + '; '.join(probs))
    return '\n'.join(out)


def run_and_compare(n, saved=None):
    ref = load_reference(n)
    if saved:
        rec = json.loads(Path(saved).read_text(encoding='utf-8'))
        rec['mon'] = {k: [tuple(x) for x in v] for k, v in rec.get('mon', {}).items()}
    else:
        rec = run_ioc(n)
    rows, probs, info = compare(ref, rec)
    txt = table(n, ref, rec, rows, probs, info)
    base = Path(rec['path']).with_suffix('') if rec.get('path') else RESULTS / f'compare-sc{n:02d}'
    Path(str(base) + '.txt').write_text(txt + '\n', encoding='utf-8')
    return txt, probs


# ---------------------------------------------------------------------------------------------- tests
class CompareLogicTest(unittest.TestCase):
    """No bench: the extraction and comparison on synthetic data and on the reference files."""

    def test_parse_ioc_log(self):
        lines = ['2026-09-28 15:34:03  SIM         IDLE → PRECHECK (operator pressed Purge)',
                 '2026-09-28 15:34:32  SIM  MAJOR  purge decay 11 % of the lid-on rate, curvature 0.27: enclosure open?',
                 'not a log line']
        p = parse_ioc_log(lines)
        self.assertEqual(len(p), 2)
        self.assertEqual(p[1][0] - p[0][0], 29)
        self.assertEqual(p[0][2], 'IDLE → PRECHECK (operator pressed Purge)')
        self.assertEqual(p[1][1], 'MAJOR')
        self.assertTrue(p[1][2].startswith('purge decay 11 %'))

    def test_value_at(self):
        s = [(100.004, 0.0), (105.003, 3.5), (106.002, 7.0)]
        self.assertTrue(math.isnan(value_at(s, 99)))
        self.assertEqual(value_at(s, 104), 0.0)
        self.assertEqual(value_at(s, 105), 3.5)
        self.assertEqual(value_at(s, 200), 7.0)

    def test_compare_synthetic(self):
        ref = {'transitions': [{'t': 5, 'from': 'IDLE', 'to': 'PRECHECK', 'flow': 0.0},
                               {'t': 34, 'from': 'PURGE', 'to': 'OPEN_STOP', 'flow': 20.0}],
               'log': [], 'lid': {'t': 34, 'ratio': 0.1058}, 'end_state': 'OPEN_STOP', 'seed': 1}
        s0 = int(time.mktime(time.strptime('2026-09-28 15:34:00', '%Y-%m-%d %H:%M:%S')))
        rec = {'S0': s0, 'end_state': 'OPEN_STOP', 'problems': [],
               'log': ['2026-09-28 15:34:05  SIM         IDLE → PRECHECK (operator pressed Purge)',
                       '2026-09-28 15:34:38  SIM         PURGE → OPEN_STOP (purge decay 11 % of the '
                       'lid-on rate, curvature 0.27: enclosure open?)'],
               'mon': {'flow': [(s0 + 5.01, 0.0), (s0 + 30.01, 20.01)]}}
        rows, probs, _ = compare(ref, rec)
        self.assertEqual(len(probs), 1, probs)          # t 38 vs 34: beyond ±3 s
        self.assertIn('Δ +4 s', probs[0])
        self.assertEqual([r[-1] for r in rows[:3]], ['ok', 'FAIL', 'ok'])   # the lid ratio from the log, 0.11

    def test_aligned_restart_line_is_not_a_transition(self):
        ref = {'transitions': [{'t': 5, 'from': 'IDLE', 'to': 'PRECHECK', 'flow': 0.0}],
               'log': [], 'lid': None, 'end_state': 'PRECHECK', 'seed': 1}
        s0 = int(time.mktime(time.strptime('2026-09-28 15:34:00', '%Y-%m-%d %H:%M:%S')))
        log = ['2026-09-28 15:34:01  SIM         — → IDLE (restart: setpoint is 0 (§4.7))',
               '2026-09-28 15:34:05  SIM         IDLE → PRECHECK (operator pressed Purge)']
        rec = {'S0': s0, 'end_state': 'PRECHECK', 'problems': [], 'log': log,
               'mon': {'flow': [(s0 + 1.01, 0.0)]}}
        self.assertTrue(compare(ref, rec)[1])             # without the aligned restart: SEQUENCE
        rec['restart'] = {'attempts': []}
        self.assertEqual(compare(ref, rec)[1], [])

    def test_reference_files(self):
        try:
            ensure_reference()
        except RuntimeError as e:
            self.skipTest(str(e))
        r1 = load_reference(1)
        self.assertEqual([(x['from'], x['to']) for x in r1['transitions']],
                         [('IDLE', 'PRECHECK'), ('PRECHECK', 'PURGE'), ('PURGE', 'HANDOFF'), ('HANDOFF', 'REGULATE')])
        self.assertEqual(r1['transitions'][0]['t'], 5)
        self.assertAlmostEqual(r1['lid']['ratio'], 0.952, places=3)
        r2 = load_reference(2)
        self.assertEqual(r2['transitions'][-1]['to'], 'OPEN_STOP')
        self.assertEqual(r2['transitions'][-1]['flow'], 20.0)
        self.assertLess(r2['lid']['ratio'], 0.5)
        for n in PRESET_SC:
            r = load_reference(n)
            self.assertEqual((r['transitions'][0]['from'], r['transitions'][0]['to'], r['transitions'][0]['t']),
                             ('—', 'REGULATE', 0))
            self.assertIsNone(r['lid'])
            self.assertEqual(r['bumps'][0]['t'], 10)          # the first PID step, pidScan 10
            self.assertLess(r['bumps'][0]['bump'], -0.02)     # O2 below target: less flow
        self.assertEqual(len(r1['bumps']), 1)                 # HANDOFF → REGULATE

    def test_bump_allowance(self):
        ref = {'transitions': [{'t': 0, 'from': '—', 'to': 'REGULATE', 'flow': 0.25},
                               {'t': 31, 'from': 'REGULATE', 'to': 'OPEN_LOOP', 'flow': 0.22}],
               'log': [], 'lid': None, 'end_state': 'OPEN_LOOP', 'bumps': [{'t': 10, 'bump': -0.0495}]}
        s0 = int(time.mktime(time.strptime('2026-09-28 15:34:00', '%Y-%m-%d %H:%M:%S')))
        log = ['2026-09-28 15:34:01  SIM         — → REGULATE (restart: O2 valid and setpoint > 0)',
               '2026-09-28 15:34:31  SIM         REGULATE → OPEN_LOOP (O2 reading frozen)']
        for ioc_flow, n_probs in ((0.27, 0), (0.30, 1), (0.245, 1), (0.23, 0)):
            rec = {'S0': s0, 'end_state': 'OPEN_LOOP', 'problems': [], 'log': log,
                   'mon': {'flow': [(s0 + 1.01, 0.25), (s0 + 17.01, ioc_flow)]}}
            rows, probs, _ = compare(ref, rec)
            self.assertEqual(len(probs), n_probs, (ioc_flow, probs))
            self.assertEqual('EXPECTED §8.11' in rows[1][-1], ioc_flow == 0.27, rows[1])


class CompareTest(unittest.TestCase):
    pass


def _make(n):
    def test(self):
        if n not in selected():
            self.skipTest('not selected by SG_COMPARE')
        saved = os.environ.get('SG_COMPARE_FROM', '').strip() or None
        if saved and f'compare-sc{n:02d}-' not in Path(saved).name:
            self.skipTest('SG_COMPARE_FROM is another scenario')
        txt, probs = run_and_compare(n, saved)
        print('\n' + txt, flush=True)
        self.assertFalse(probs, probs)
    test.__name__ = f'test_sc{n:02d}'
    return test


for _n in COMPARED:
    setattr(CompareTest, f'test_sc{_n:02d}', _make(_n))

if __name__ == '__main__':
    unittest.main()
