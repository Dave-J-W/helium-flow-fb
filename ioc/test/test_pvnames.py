"""Bench acceptance test of the linked PV names, spec §14.3a (§8.21), in real time.

The plant simulator runs two plants (Bench(second_plant=True)): SIM:Alicat1: / SIM:O2 and
SIM:Alicat2: / SIM:O2b. The IOC starts on plant 1, is moved to plant 2 by Cfg:* + Cfg:Apply, and
must then read and write plant 2 only. Every put the IOC makes to either Alicat is seen by a CA
monitor on its writable PVs (Setpoint, RampRate, Run): the simulator never writes those itself,
so each monitor event after the first (the value on connection) is a put. About 11 min.

    python -m unittest -v test_pvnames      (in ioc/test, bench Python, PYTHONIOENCODING=utf-8)

The helpers here (PutWatch, wait_until, wait_log, follows) are also used by test_write_enable.
"""
import re
import statistics
import time
import unittest

from bench import IOC_BOOT, Bench, BenchError
from test_scenarios import apply

P = 'SIM:SampleGas:'
A1, A2 = 'SIM:Alicat1:', 'SIM:Alicat2:'
WRITABLE = ('Setpoint', 'RampRate', 'Run')
DEFAULTS = {'MFC': 'SIM:Alicat1:', 'O2': 'SIM:O2', 'CYL': '', 'STN': 'SIM'}   # st.cmd macros
ARROW = '→'


def step(msg):
    print(time.strftime('%H:%M:%S'), msg, flush=True)


class PutWatch:
    """CA monitors on the given writable PVs; mark() and since() isolate the puts after a point."""

    def __init__(self, b, pvs, timeout=10.0):
        self.rec = {pv: b.watch(pv) for pv in pvs}
        t_end = time.time() + timeout
        while time.time() < t_end and not all(self.rec.values()):
            time.sleep(0.2)
        missing = [pv for pv, r in self.rec.items() if not r]
        if missing:
            raise BenchError(f'no connection value from {missing}')

    def mark(self):
        return {pv: len(r) for pv, r in self.rec.items()}

    def since(self, mark, prefix=''):
        return [(pv, round(t, 1), v) for pv, r in self.rec.items() if pv.startswith(prefix)
                for t, v in r[mark[pv]:]]


def wait_until(fn, timeout, what, poll=0.5):
    t_end = time.time() + timeout
    while True:
        v = fn()
        if v:
            return v
        if time.time() > t_end:
            raise AssertionError(f'{what}: not within {timeout} s')
        time.sleep(poll)


def wait_log(b, mark, pattern, timeout=10.0):
    """Wait for a log line (written after mark) matching pattern; return the re match."""
    rx = re.compile(pattern)

    def find():
        for ln in b.log_lines(since=mark):
            m = rx.search(ln)
            if m:
                return m
        return None
    return wait_until(find, timeout, f'log line {pattern!r}')


def log_matches(b, mark, pattern):
    rx = re.compile(pattern)
    return [ln for ln in b.log_lines(since=mark) if rx.search(ln)]


def follows(b, src, n=5, tol=0.05):
    """Sts:O2 over n ticks against the plant PV src (read just after each tick: Sts:O2 is src's
    latest 1 Hz value at the tick). Returns the median |difference| and the median of src."""
    diffs, vals = [], []
    for _ in range(n):
        b.next_tick()
        time.sleep(0.1)
        s, v = b.get(P + 'Sts:O2'), b.get(src)
        diffs.append(abs(s - v))
        vals.append(v)
    return statistics.median(diffs), statistics.median(vals)


def active(b):
    return {f: b.get(P + f'Cfg:Active:{f}', as_string=True) for f in ('MFC', 'O2', 'CYL', 'STN')}


def fields(b, kind=''):
    return {f: b.get(P + f'Cfg:{kind}{f}', as_string=True) for f in ('MFC', 'O2', 'CYL', 'STN')}


def status(b):
    return b.get(P + 'Cfg:Status.VAL$', as_string=True)


def he_saved(suffix):
    """A value in the helium autosave file (NaN if absent or unset)."""
    try:
        txt = (IOC_BOOT / 'autosave' / 'sampleGas_helium.sav').read_text(encoding='utf-8', errors='replace')
    except OSError:
        return float('nan')
    for ln in txt.splitlines():
        if ln.startswith(P + 'He:' + suffix + ' '):
            try:
                return float(ln.split(' ', 1)[1])
            except ValueError:
                return float('nan')
    return float('nan')


def names_line(mfc, o2, cyl='none'):
    return re.escape(f'operator: PV names {ARROW} MFC {mfc}, O2 {o2}, CYL {cyl}')


class PvNamesTest(unittest.TestCase):
    def assertNoPuts(self, w, mark, prefix, what):
        self.assertEqual(w.since(mark, prefix), [], f'puts to {prefix} {what}')

    def assertFollows(self, b, src, other):
        d, v = follows(b, src)
        o = b.get(other)
        self.assertGreater(abs(v - o), 1.0, f'{src} {v:.3f} and {other} {o:.3f} too close to tell apart')
        self.assertLess(d, 0.05, f'Sts:O2 does not follow {src} (median |diff| {d:.3f})')

    def test_pv_names(self):
        with Bench(noise=True, seed=21, second_plant=True, tag='pvnames') as b:
            w = PutWatch(b, [a + s for a in (A1, A2) for s in WRITABLE])
            m_start = w.mark()
            self.assertEqual(active(b), DEFAULTS)
            self.assertEqual(b.get(P + 'Par:writeEnable'), 1)

            # Plant 1 regulating on the collimator lid (0.84 SLPM), so its totalizer is well past
            # 1 L when the MFC changes: a missed re-baseline would then log "went backwards".
            step('preset plant 1 and restart into REGULATE')
            b.world('LidType', 'B')
            b.put(P + 'Mode', 1)
            apply(b, 'preset', None)

            # --- Apply in REGULATE is rejected and nothing changes
            step('Apply in REGULATE')
            mk = b.mark_log()
            b.put(P + 'Cfg:MFC', A2)
            b.put(P + 'Cfg:O2', 'SIM:O2b')
            wait_until(lambda: b.get(P + 'Cfg:Pending') == 1, 5, 'Cfg:Pending')
            b.put(P + 'Cfg:Apply', 1)
            wait_log(b, mk, r'PV name change ignored in REGULATE')
            b.next_tick()
            b.next_tick()
            self.assertEqual(status(b), 'rejected: release control first (state REGULATE)')
            self.assertEqual(active(b), DEFAULTS)
            self.assertEqual(b.state(), 'REGULATE')
            self.assertEqual(b.get(P + 'Cfg:Pending'), 1)
            self.assertEqual(b.get(P + 'Par:writeEnable'), 1)
            self.assertFollows(b, 'SIM:O2', 'SIM:O2b')
            self.assertEqual(log_matches(b, mk, r'Alicat PV changed|PV names ' + ARROW), [])
            self.assertNoPuts(w, m_start, A2, 'before any change of Cfg:MFC')

            # --- In IDLE, applying SIM:O2b makes Sts:O2 follow SIM:O2b
            step('IDLE, Apply SIM:O2b')
            b.cmd('ReleaseIdle')
            b.wait_state('IDLE', 5)
            b.put(P + 'Cfg:MFC', A1)
            mk = b.mark_log()
            b.put(P + 'Cfg:Apply', 1)
            wait_log(b, mk, names_line(A1, 'SIM:O2b'))
            self.assertRegex(status(b), r'^applied .+: all connected$')
            self.assertEqual(active(b), dict(DEFAULTS, O2='SIM:O2b'))
            self.assertEqual(b.get(P + 'Cfg:Pending'), 0)
            self.assertFollows(b, 'SIM:O2b', 'SIM:O2')
            self.assertEqual(log_matches(b, mk, r'Alicat PV changed'), [])

            # --- SIM:Alicat2: with writes enabled: ledger re-baselined, writeEnable kept
            wait_until(lambda: b.get(A1 + 'Total_RBV') > 1.5, 180, 'plant 1 totalizer > 1.5 L')
            b.put(P + 'Mode', 0)                 # plant 2 has the normal lid
            step('Apply SIM:Alicat2: (totalizers: 1 = %.2f L, 2 = %.2f L)'
                 % (b.get(A1 + 'Total_RBV'), b.get(A2 + 'Total_RBV')))
            b.put(P + 'Cfg:MFC', A2)
            b.next_tick()
            mk_mfc = b.mark_log()
            m_mfc = w.mark()
            cum0, tot0 = b.get(P + 'He:CumL'), b.get(A2 + 'Total_RBV')
            b.put(P + 'Cfg:Apply', 1)
            wait_log(b, mk_mfc, names_line(A2, 'SIM:O2b'))
            self.assertTrue(log_matches(b, mk_mfc, r'MINOR +Alicat PV changed: totalizer re-baselined; '
                                                   r'press New He cylinder if the cylinder differs'))
            self.assertEqual(log_matches(b, mk_mfc, r'writes disabled'), [])
            self.assertRegex(status(b), r'^applied .+: all connected$')
            self.assertEqual(active(b), dict(DEFAULTS, MFC=A2, O2='SIM:O2b'))
            self.assertEqual(b.get(P + 'Par:writeEnable'), 1)
            self.assertEqual(b.get(P + 'Sts:WriteEnable'), 1)
            b.world('LiftLid', 1)                # plant 1 is no longer ours: the IOC must not react
            for _ in range(15):
                b.next_tick()
            last, tot2 = b.get(P + 'He:LastTotal'), b.get(A2 + 'Total_RBV')
            self.assertLess(abs(last - tot2), 0.1, f'He:LastTotal {last} is not the new totalizer {tot2}')
            dcum = b.get(P + 'He:CumL') - cum0
            self.assertTrue(-1e-6 <= dcum <= (tot2 - tot0) + 0.05,
                            f'CumL moved {dcum:.3f} L, new totalizer {tot2 - tot0:.3f} L')
            self.assertEqual(log_matches(b, mk_mfc, r'(?i)totalizer went backwards'), [])

            # --- a following purge writes only SIM:Alicat2:
            step('Purge on plant 2')
            b.cmd('Purge')
            b.wait_state('PURGE', 10)
            b.wait_state('REGULATE', 780)
            step('REGULATE on plant 2')
            time.sleep(40)
            self.assertNoPuts(w, m_mfc, A1, 'after Cfg:MFC changed to SIM:Alicat2:')
            sp = [v for pv, t, v in w.since(m_mfc, A2 + 'Setpoint')]
            self.assertTrue(sp and max(sp) > 0.5, f'purge setpoints on {A2}: {sp}')
            self.assertGreater(b.get(A2 + 'Flow_RBV'), 0)
            self.assertEqual(log_matches(b, mk_mfc, r'(?i)totalizer went backwards'), [])
            dcum, dtot = b.get(P + 'He:CumL') - cum0, b.get(A2 + 'Total_RBV') - tot0
            self.assertLess(abs(dcum - dtot), 0.25, f'CumL moved {dcum:.2f} L, SIM:Alicat2: {dtot:.2f} L')

            # --- empty Cfg:O2: not configured, Purge rejected
            step('empty Cfg:O2')
            b.cmd('ReleaseIdle')
            b.wait_state('IDLE', 5)
            mk = b.mark_log()
            m = w.mark()
            b.put(P + 'Cfg:O2', '')
            b.put(P + 'Cfg:Apply', 1)
            wait_log(b, mk, names_line(A2, 'none'))
            b.next_tick()
            b.next_tick()
            self.assertEqual(status(b), 'not configured: Cfg:O2 is empty')
            self.assertEqual(b.get(P + 'Sts:StateDesc.VAL$', as_string=True), 'not configured: Cfg:O2 is empty')
            self.assertEqual(active(b)['O2'], '')
            b.cmd('Purge')
            wait_log(b, mk, r'Purge ignored: not configured: Cfg:O2 is empty')
            time.sleep(3)
            self.assertEqual(b.state(), 'IDLE')
            self.assertNoPuts(w, m, 'SIM:', 'while not configured')

            # --- Restore defaults puts the macro values back (fields only, until Apply)
            step('Restore defaults')
            mk = b.mark_log()
            b.put(P + 'Cfg:RestoreDefaults', 1)
            wait_log(b, mk, r'admin: PV name fields set to the defaults \(not applied\)')
            self.assertEqual(fields(b), DEFAULTS)
            self.assertEqual(fields(b, 'Default:'), DEFAULTS)
            self.assertEqual(active(b), dict(DEFAULTS, MFC=A2, O2=''))
            wait_until(lambda: b.get(P + 'Cfg:Pending') == 1, 5, 'Cfg:Pending')
            b.put(P + 'Cfg:Apply', 1)
            wait_log(b, mk, names_line(A1, 'SIM:O2'))
            self.assertRegex(status(b), r'^applied .+: all connected$')
            self.assertEqual(active(b), DEFAULTS)
            self.assertEqual(b.get(P + 'Par:writeEnable'), 1)
            self.assertFollows(b, 'SIM:O2', 'SIM:O2b')      # plant 1's lid is open: far apart

            # The helium state on disk now has plant 1's totalizer as its reference (Mark new run
            # saves it at once). After the change back to plant 2 and a restart, that reference
            # must not be restored against plant 2's totalizer (its CumL would jump).
            wait_until(lambda: abs(b.get(P + 'He:LastTotal') - b.get(A1 + 'Total_RBV')) < 0.1, 10,
                       'He:LastTotal on plant 1')
            b.cmd('MarkNewRun')
            wait_until(lambda: abs(he_saved('LastTotal') - b.get(A1 + 'Total_RBV')) < 0.2, 15,
                       'helium autosave with plant 1 reference')

            # --- after an IOC restart the edited, applied names are still in use
            step('edit + apply SIM:Alicat2: / SIM:O2b, then restart the IOC')
            b.put(P + 'Cfg:MFC', A2)
            b.put(P + 'Cfg:O2', 'SIM:O2b')
            mk = b.mark_log()
            b.put(P + 'Cfg:Apply', 1)
            wait_log(b, mk, names_line(A2, 'SIM:O2b'))
            for _ in range(3):
                b.next_tick()
            cum1, tot1 = b.get(P + 'He:CumL'), b.get(A2 + 'Total_RBV')
            b.wait_autosaved(['Cfg:MFC', 'Cfg:O2'])
            step('helium autosave before the restart: LastTotal %s (SIM:Alicat1: %.2f L, SIM:Alicat2: %.2f L)'
                 % (he_saved('LastTotal'), b.get(A1 + 'Total_RBV'), b.get(A2 + 'Total_RBV')))
            mk = b.mark_log()
            b.restart_ioc()
            wait_log(b, mk, re.escape('PV names: MFC SIM:Alicat2:, O2 SIM:O2b, CYL none'), 30)
            wait_log(b, mk, r'restart: ', 60)
            self.assertEqual(active(b), dict(DEFAULTS, MFC=A2, O2='SIM:O2b'))
            self.assertEqual(fields(b), dict(DEFAULTS, MFC=A2, O2='SIM:O2b'))
            self.assertEqual(b.get(P + 'Cfg:Pending'), 0)
            self.assertEqual(b.get(P + 'Par:writeEnable'), 1)
            self.assertFollows(b, 'SIM:O2b', 'SIM:O2')
            self.assertEqual(b.state(), 'REGULATE')        # plant 2 still flowing, O2 low
            time.sleep(30)
            self.assertLess(abs(b.get(P + 'Sts:SetpointRBV') - b.get(A2 + 'Setpoint_RBV')), 0.005)
            self.assertNoPuts(w, m_mfc, A1, 'after Cfg:MFC changed to SIM:Alicat2:')
            self.assertEqual(log_matches(b, mk_mfc, r'(?i)totalizer went backwards'), [])
            dcum, dtot = b.get(P + 'He:CumL') - cum1, b.get(A2 + 'Total_RBV') - tot1
            self.assertLess(abs(dcum - dtot), 0.1,
                            f'across the restart CumL moved {dcum:.3f} L, SIM:Alicat2: {dtot:.3f} L')
            # the cylinder baseline and the usage log restored are plant 2's, not plant 1's
            base, tot2 = b.get(P + 'He:CylBase'), b.get(A2 + 'Total_RBV')
            self.assertTrue(tot1 - 0.1 <= base <= tot2 + 0.1,
                            f'He:CylBase {base} is not on SIM:Alicat2: ({tot1:.2f}..{tot2:.2f} L)')
            n = b.get(P + 'He:HistN')
            used = list(b.get(P + 'He:HistUsed', count=n)) if n else []
            self.assertTrue(all(-0.1 <= u <= tot2 - base + 0.1 for u in used),
                            f'usage log after the restart holds another totalizer: {used}')
            step('done')


if __name__ == '__main__':
    unittest.main()
