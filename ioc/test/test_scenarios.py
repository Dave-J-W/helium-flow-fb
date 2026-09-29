"""test_scenarios.py: spec §14.2 scenario acceptance, in real time, against the bench IOC and the
plant simulator (Plan 4 task 2). One test method per scenario (test_sc01 .. test_sc19).

    SG_SCENARIOS=2,3,8 python -m unittest -v test_scenarios      (default: every real-time one)
    SG_NOISE=0 turns the plant noise off (default on, seeded with the scenario number)

Each run writes results/sc<NN>-<timestamp>.json (end state, log lines, trace, pass/fail,
problems) before it asserts, so an unattended run loses nothing. Scenarios 17 and 19 are
replay-only (spec §14.4) and are skipped here.
"""
import json
import os
import re
import time
import unittest

from bench import RESULTS, WORLD, Bench, BenchError
from scenarios import BY_N, REALTIME

P = 'SIM:SampleGas:'
MODES = {'A': 0, 'B': 1, 'C': 2, 'D': 3}
MODE_DEFAULTS = {'A': (0.25, 1.0), 'B': (0.84, 0.53), 'C': (0.25, 1.0), 'D': (0.25, 1.0)}
TRACE_EVERY = 5
TRACE_IOC = [('state', P + 'Sts:State'), ('o2', P + 'Sts:O2'), ('lastCmd', P + 'Sts:LastCmd')]
TRACE_PLANT = [('flow', 'SIM:Alicat1:Flow_RBV'), ('sp', 'SIM:Alicat1:Setpoint_RBV'), ('o2raw', 'SIM:O2'),
               ('bulk', WORLD + 'Bulk')]


def selected():
    s = os.environ.get('SG_SCENARIOS', '').strip()
    return [int(x) for x in s.split(',') if x.strip()] if s else list(REALTIME)


def expected_flow(b):
    f = b.get(P + 'Sts:ExpectedFlow')
    if f is None or not f > 0:                # fall back to §8.13 with the mode-slot defaults
        mode = 'ABCD'[int(b.get(P + 'Mode'))]
        base, n = MODE_DEFAULTS[mode]
        f = base * (0.99 / b.get(P + 'Par:target')) ** n
    return round(f, 2)


def apply(b, action, arg):
    """Run one scenario action; return a note for the results file."""
    if action in ('purge', 'flow_zero', 'resume_flow', 'new_cylinder', 'mark_new_run'):
        name = {'purge': 'Purge', 'flow_zero': 'FlowZero', 'resume_flow': 'ResumeFlow',
                'new_cylinder': 'NewCylinder', 'mark_new_run': 'MarkNewRun'}[action]
        b.cmd(name)
    elif action == 'target':
        b.put(P + 'Par:target', arg)
    elif action == 'mode':
        b.put(P + 'Mode', MODES[arg])
    elif action == 'world':
        b.world(*arg)
    elif action == 'preset':
        b.wait_autosaved(['Par:writeEnable', 'Mode', 'Par:target'])
        f = expected_flow(b)
        b.stop_ioc(kill=True)
        b.world('Preset', f)
        b.start_ioc()
        b.wait_state('REGULATE', 60)
        return f'preset at {f} SLPM, IOC restarted'
    elif action == 'crash':
        b.stop_ioc(kill=True)
    elif action == 'restart':
        b.start_ioc()
    else:
        raise ValueError(action)
    return ''


def sample(b, down):
    row = {'t': round(time.time(), 1)}
    for k, pv in ([] if down else TRACE_IOC) + TRACE_PLANT:
        try:
            v = b.get(pv, timeout=1.0, as_string=(k == 'state'))
        except BenchError:
            v = None
        row[k] = round(v, 4) if isinstance(v, float) else v
    return row


def extra_checks(sc, res):
    """Checks beyond the SELFTEST row (none of them weaken it)."""
    probs = []
    if sc['n'] == 14:          # the Alicat holds its flow while the IOC is down (spec §14.3)
        t0 = res['t0']
        pre = [r['flow'] for r in res['trace'] if 280 <= r['t'] - t0 < 300 and r['flow'] is not None]
        down = [r['flow'] for r in res['trace'] if 305 <= r['t'] - t0 < 895 and r['flow'] is not None]
        if pre and down and max(abs(f - pre[-1]) for f in down) > 0.03:
            probs.append(f'flow moved while the IOC was down: {pre[-1]} -> {min(down)}..{max(down)}')
        if not down:
            probs.append('no plant samples while the IOC was down')
    return probs


def run_scenario(n, noise=True):
    sc = BY_N[n]
    res = {'n': n, 'name': sc['name'], 'dur': sc['dur'], 'noise': noise, 'seed': n, 'events': [],
           'trace': [], 'problems': [], 'expected_state': sc['state'], 'has': sc['has']}
    stamp = time.strftime('%Y%m%d-%H%M%S')
    path = RESULTS / f'sc{n:02d}-{stamp}.json'
    res['started'] = stamp
    try:
        with Bench(noise=noise, seed=n, tag=f'sc{n:02d}') as b:
            for action, arg in sc['setup']:
                res['events'].append({'t': 'setup', 'action': action, 'arg': arg, 'note': apply(b, action, arg)})
            t0 = b.next_tick()
            mark = b.mark_log()
            res['t0'] = round(t0, 2)
            pending = sorted(sc['events'], key=lambda e: e[0])
            next_trace = t0
            down = False
            while True:
                now = time.time()
                while pending and now >= t0 + pending[0][0]:
                    t, action, arg = pending.pop(0)
                    at = round(time.time() - t0, 1)
                    note = apply(b, action, arg)
                    down = b.ioc_proc is None
                    res['events'].append({'t': t, 'at': at, 'done': round(time.time() - t0, 1),
                                          'action': action, 'arg': arg, 'note': note})
                if now >= next_trace:
                    res['trace'].append(sample(b, down))
                    next_trace += TRACE_EVERY
                if now >= t0 + sc['dur']:
                    break
                time.sleep(0.2)
            res['end_state'] = b.state()
            res['banner'] = b.get(P + 'Sts:Banner.VAL$', as_string=True)   # long string (> 40 chars)
            res['log'] = b.log_lines(since=mark)
            full = b.log_lines()
            res['log_setup'] = full[:len(full) - len(res['log'])]
    except Exception as e:                     # harness failure: still write the results file
        res['problems'].append(f'harness: {type(e).__name__}: {e}')
    if 'end_state' in res:
        if sc['state'] is not None and res['end_state'] != sc['state']:
            res['problems'].append(f"end state {res['end_state']}, expected {sc['state']}")
        for pat in sc['has']:
            if not any(re.search(pat, ln) for ln in res['log']):
                res['problems'].append(f'no log line matches {pat!r}')
        res['problems'] += extra_checks(sc, res)
    res['pass'] = not res['problems']
    path.write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding='utf-8')
    res['path'] = str(path)
    return res


class ScenarioDataTest(unittest.TestCase):
    def test_table(self):
        self.assertEqual(sorted(BY_N), list(range(1, 20)))
        for n, sc in BY_N.items():
            self.assertEqual(sc['replay_only'], n in (17, 19), n)
            if not sc['replay_only']:   # (17's reference events run past its 2-day duration)
                self.assertTrue(sc['has'] and sc['state'], n)
                for t, action, arg in sc['events']:
                    self.assertLess(t, sc['dur'], n)
        for n in (10, 11):
            self.assertIn('operator pressed Resume Flow', BY_N[n]['has'])


class ScenarioTest(unittest.TestCase):
    pass


def _make(n):
    def test(self):
        sc = BY_N[n]
        if sc['replay_only']:
            self.skipTest('replay only (spec §14.4)')
        if n not in selected():
            self.skipTest('not selected by SG_SCENARIOS')
        res = run_scenario(n, noise=os.environ.get('SG_NOISE', '1') != '0')
        print(f"\nsc{n:02d}: {'PASS' if res['pass'] else 'FAIL'} end={res.get('end_state')} "
              f"problems={res['problems']} -> {res['path']}", flush=True)
        self.assertTrue(res['pass'], res['problems'])
    test.__name__ = f'test_sc{n:02d}'
    return test


for _n in BY_N:
    setattr(ScenarioTest, f'test_sc{_n:02d}', _make(_n))

if __name__ == '__main__':
    unittest.main()
