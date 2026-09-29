"""Bench acceptance test of write enable (shadow mode) and Resume Flow, spec §14.3b (§8.20, §8.5),
plus the §14.5 shadow check, in real time.

A CA monitor on each writable SIM:Alicat1: PV (Setpoint, RampRate, Run) records every put the IOC
makes (the simulator never writes those PVs itself; the first event is the value on connection).
While Par:writeEnable = 0 there must be none, including across an IOC restart into REGULATE.
About 12 min.

    python -m unittest -v test_write_enable   (in ioc/test, bench Python, PYTHONIOENCODING=utf-8)
"""
import re
import statistics
import time
import unittest

from bench import Bench
from test_pvnames import A1, P, WRITABLE, PutWatch, log_matches, step, wait_log, wait_until
from test_scenarios import apply

BUMP = 0.02            # SLPM: the largest setpoint change allowed at the first PID step (§14.3b)
KP_A, DRVL_A, DRVH_A = -9.3, 0.05, 1.0     # mode A defaults (spec §9.2)
NUM = r'(\d+\.\d+)'


def mean_o2(b, n=10):
    vals = []
    for _ in range(n):
        b.next_tick()
        vals.append(b.get(P + 'Sts:O2'))
    return statistics.mean(vals)


class WriteEnableTest(unittest.TestCase):
    def assertNoPuts(self, w, mark, what):
        self.assertEqual(w.since(mark), [], f'puts to {A1} {what}')

    def assertStaysIn(self, b, state, seconds):
        t_end = time.time() + seconds
        while time.time() < t_end:
            self.assertEqual(b.state(), state)
            time.sleep(1.0)

    def test_write_enable_and_resume_flow(self):
        with Bench(noise=True, seed=22, tag='writeen') as b:
            w = PutWatch(b, [A1 + s for s in WRITABLE])
            cval = b.watch(P + 'PID:CVAL')            # written once per PID step, just before it

            # --- shadow mode, then an IOC restart into REGULATE (§14.5)
            step('shadow mode, restart into REGULATE')
            mk = b.mark_log()
            b.put(P + 'Par:writeEnable', 0)
            wait_log(b, mk, r'MAJOR +writes disabled: shadow mode, Alicat holds \d+\.\d\d SLPM')
            m_shadow = w.mark()
            apply(b, 'preset', None)                  # autosave, stop, plant preset, start
            wait_log(b, mk, r'restart: O2 valid and setpoint > 0, resume regulation')
            self.assertEqual(b.get(P + 'Par:writeEnable'), 0)
            self.assertEqual(b.get(P + 'Sts:WriteEnable'), 0)

            # shadow regulation away from the Alicat's flow: a target 0.04 % above the O2 makes
            # epid lower its (unsent) output; the Alicat stays where it is
            o2 = mean_o2(b)
            target = round(o2 + 0.04, 3)
            b.put(P + 'Par:target', target)
            step(f'target {target} (O2 {o2:.3f})')
            sp_rbv = b.get(A1 + 'Setpoint_RBV')
            wait_until(lambda: abs(b.get(P + 'Sts:LastCmd') - b.get(A1 + 'Setpoint_RBV')) >= 0.05, 120,
                       'shadow LastCmd 0.05 SLPM away from Setpoint_RBV')
            self.assertEqual(b.state(), 'REGULATE')
            self.assertTrue(log_matches(b, mk, r'shadow mode: would write Setpoint = '))
            self.assertNoPuts(w, m_shadow, 'in shadow mode, across the restart into REGULATE')
            self.assertLess(abs(b.get(A1 + 'Setpoint_RBV') - sp_rbv), 0.005)
            step('shadow LastCmd %.3f, Alicat %.3f' % (b.get(P + 'Sts:LastCmd'), b.get(A1 + 'Setpoint_RBV')))

            # --- switch to live: IDLE, no put at the switch or while IDLE
            step('switch to live')
            mk = b.mark_log()
            sp_rbv = b.get(A1 + 'Setpoint_RBV')
            b.put(P + 'Par:writeEnable', 1)
            b.wait_state('IDLE', 5)
            m = wait_log(b, mk, r'writes enabled: Alicat left at ' + NUM + ' SLPM')
            self.assertLess(abs(float(m.group(1)) - sp_rbv), 0.006)
            self.assertStaysIn(b, 'IDLE', 30)
            self.assertNoPuts(w, m_shadow, 'at the switch to live or while IDLE')
            self.assertLess(abs(b.get(A1 + 'Setpoint_RBV') - sp_rbv), 0.005)

            # --- Resume Flow: REGULATE, first PID step within 0.02 SLPM of Setpoint_RBV
            step('Resume Flow')
            sp0 = b.get(A1 + 'Setpoint_RBV')
            e = target - mean_o2(b, 5)
            kp = KP_A * 0.99 / target                 # gain schedule (fine band is off: |e| > 0.02)
            self.assertGreater(abs(kp * e), 5 * BUMP, f'error {e:.3f} too small to test the bumpless start')
            self.assertTrue(DRVL_A < sp0 - kp * e < DRVH_A, f'PID:Out {sp0 - kp * e:.3f} would clamp')
            mk = b.mark_log()
            m_live = w.mark()
            n_cval = len(cval)
            t_res = time.time()
            b.cmd('ResumeFlow')
            b.wait_state('REGULATE', 5)
            wait_log(b, mk, r'IDLE → REGULATE \(operator pressed Resume Flow\)')
            wait_until(lambda: len(cval) > n_cval, 15, 'first PID step (PID:CVAL written)')
            t1 = cval[n_cval][0]
            time.sleep(4)                             # the next PID step is 10 s after the first
            first = [(t, v) for pv, t, v in w.since(m_live) if pv == A1 + 'Setpoint' and t < t1 + 4]
            lc = b.get(P + 'Sts:LastCmd')
            step(f'first PID step {t1 - t_res:.1f} s after Resume Flow: Setpoint puts {first}, '
                 f'LastCmd {lc:.4f}, Setpoint_RBV at resume {sp0:.3f}')
            self.assertLessEqual(abs(lc - sp0), BUMP, f'first PID step LastCmd {lc:.4f}, from {sp0:.3f}')
            for t, v in first:
                self.assertLessEqual(abs(v - sp0), BUMP + 1e-9, f'first-step setpoint {v} from {sp0}')
            # live regulation does write (the watch sees the IOC's puts)
            wait_until(lambda: [1 for pv, t, v in w.since(m_live) if pv == A1 + 'Setpoint'], 90,
                       'a Setpoint put while regulating live')

            # --- live -> shadow while regulating: REGULATE kept, no more puts
            step('live -> shadow in REGULATE')
            mk = b.mark_log()
            b.put(P + 'Par:writeEnable', 0)
            wait_log(b, mk, r'MAJOR +writes disabled: shadow mode, Alicat holds \d+\.\d\d SLPM')
            m_shadow = w.mark()
            lc0 = b.get(P + 'Sts:LastCmd')
            self.assertStaysIn(b, 'REGULATE', 60)
            self.assertNoPuts(w, m_shadow, 'after live -> shadow in REGULATE')
            lc1 = b.get(P + 'Sts:LastCmd')
            step('shadow LastCmd %.3f -> %.3f over 60 s' % (lc0, lc1))
            # epid kept moving the (unsent) command, so live mode would have written in this window
            self.assertGreater(abs(lc1 - lc0), 0.01, 'shadow LastCmd did not move: no-put check is vacuous')

            # --- and across an IOC restart into REGULATE while shadow regulating (§14.5)
            step('shadow restart into REGULATE')
            b.wait_autosaved(['Par:writeEnable'])
            mk = b.mark_log()
            b.restart_ioc()
            wait_log(b, mk, r'restart: O2 valid and setpoint > 0, resume regulation', 60)
            self.assertEqual(b.get(P + 'Par:writeEnable'), 0)
            self.assertStaysIn(b, 'REGULATE', 30)
            self.assertNoPuts(w, m_shadow, 'in shadow mode, across the restart into REGULATE')

            # --- Resume Flow from IDLE rejected: O2 invalid, O2 above lidLevel
            step('Resume Flow rejections')
            mk = b.mark_log()
            b.put(P + 'Par:writeEnable', 1)
            b.wait_state('IDLE', 5)
            m_idle = w.mark()
            b.world('AnalyzerMode', 'invalid')
            wait_until(lambda: b.get(P + 'Sts:O2Valid') == 0, 10, 'Sts:O2Valid 0')
            b.cmd('ResumeFlow')
            wait_log(b, mk, r'Resume Flow ignored: O2 invalid')
            self.assertStaysIn(b, 'IDLE', 3)
            b.world('AnalyzerMode', 'normal')
            wait_until(lambda: b.get(P + 'Sts:O2Valid') == 1, 10, 'Sts:O2Valid 1')

            b.put(P + 'Par:lidLevel', 2)
            b.world('LiftLid', 1)
            wait_until(lambda: b.get(P + 'Sts:O2') > 2.3, 420, 'O2 above 2.3 % with the lid off')
            mk = b.mark_log()
            b.cmd('ResumeFlow')
            m = wait_log(b, mk, r'Resume Flow ignored: O2 ' + NUM
                         + r' % above the lid threshold 2 %: purge first')
            self.assertGreater(float(m.group(1)), 2.0)
            self.assertStaysIn(b, 'IDLE', 3)
            self.assertNoPuts(w, m_idle, 'for a rejected Resume Flow')
            self.assertEqual(log_matches(b, mk, r'operator pressed Resume Flow'), [])
            step('done')


if __name__ == '__main__':
    unittest.main()
