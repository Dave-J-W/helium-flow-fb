"""Bench tests of the conformance fix round (docs/ioc/CONFORMANCE.md; the round's report lists
them under "Bench tests to run after the merge"), in real time, one test per item:

  test_d1b_second_writer        D1b: a second writer's setpoint is detected (MAJOR), re-sent, cleared
  test_g2_shadow_alarm          G2:  shadow mode is a MAJOR alarm on the banner; Live clears it (IDLE)
  test_g1_restart_mid_purge     G1:  an IOC restart mid-purge at 20 SLPM resumes the purge
  test_d2_live_in_start_wait    D2:  Live switched on while the start-up waits for the Alicat -> IDLE, no put
  test_d8_release_in_start_wait D8:  Release control pressed during that wait -> IDLE, no put
  test_mfc_dropout              §8.18: the plant (Alicat) gone ~10 s in REGULATE: alarm, no put, re-send, no D6 mismatch
  test_start_heartbeat          af476b9: Sts:Heartbeat advances and Sts:TickAge <= 1 during the start-up wait
  test_dropskip_open_lid_caught      dropSkipLevel 17 % (2026-09-30): open lid at o2Start ~17.5 % is caught
  test_dropskip_closed_lid_passes    dropSkipLevel 17 % (2026-09-30): closed lid at o2Start ~17.5 % still passes

    python -m unittest -v test_fixes            (in ioc/test, bench Python, PYTHONIOENCODING=utf-8)
    SG_FIXES=d1b,g2 python -m unittest -v test_fixes      (a selection, by the names above)

Puts to the Alicat are counted by the plant itself (SIM:World:AlicatPuts, every put to Setpoint,
RampRate or Run; plant_sim.py also logs each to results/fix*-plant.txt), so "no put" does not
depend on when a test's own CA monitor connected. Each test writes results/fixes-<item>-<stamp>.json
(its observations and the IOC log lines) before it asserts. About 35 min in all.
"""
import json
import math
import os
import re
import time
import unittest

from bench import RESULTS, WORLD, Bench
from test_pvnames import A1, P, WRITABLE, PutWatch, log_matches, step, wait_log, wait_until
from test_scenarios import apply

PUTS = WORLD + 'AlicatPuts'
WAIT_ALARM = r'MAJOR +waiting for the Alicat PVs \('
NUM = r'(\d+\.\d+)'


def selected(method):
    """SG_FIXES=d1b,g2,g1,d2,d8,dropout,heartbeat (any word of the test method's name)."""
    s = os.environ.get('SG_FIXES', '').strip()
    return not s or bool(set(method.split('_')[1:]) & {x.strip() for x in s.split(',')})


def save(item, rec, b=None):
    """results/fixes-<item>-<stamp>.json: the observations and the IOC log of this Bench."""
    if b is not None:
        rec['log'] = b.log_lines()
    path = RESULTS / f"fixes-{item}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(rec, indent=1, ensure_ascii=False, default=str), encoding='utf-8')
    return path


def log_time(ln):
    """Epoch second of an IOC log line (local time, 1 s)."""
    return time.mktime(time.strptime(ln[:19], '%Y-%m-%d %H:%M:%S'))


def wait_pid_phase(scan, frac, lead=2.0):
    """Sleep until wall second S + frac with S % scan == 0 and S at least `lead` s away; return S.
    The IOC steps its PID in the tick at the whole second where epoch % pidScan == 0."""
    s = math.ceil(time.time() + lead)
    s += -s % scan
    while time.time() < s + frac:
        time.sleep(min(0.05, max(0.0, s + frac - time.time())))
    return s


class FixesTest(unittest.TestCase):

    def setUp(self):
        if not selected(self._testMethodName):
            self.skipTest('not selected by SG_FIXES')

    def assertStaysIn(self, b, state, seconds):
        t_end = time.time() + seconds
        while time.time() < t_end:
            self.assertEqual(b.state(), state)
            time.sleep(1.0)

    # ------------------------------------------------------------------------------------ D1b
    def test_d1b_second_writer(self):
        rec = {'item': 'D1b second writer'}
        with Bench(noise=True, seed=31, tag='fixd1b') as b:
            try:
                apply(b, 'preset', None)                  # restart into REGULATE at the expected flow
                w = PutWatch(b, [A1 + 'Setpoint'])
                cval = b.watch(P + 'PID:CVAL')            # one event per PID step
                scan = int(round(b.get(P + 'Par:pidScan')))
                time.sleep(25)                            # past the bumpless first PID step
                self.assertEqual(b.state(), 'REGULATE')

                # (a) a one-shot put in the middle of a PID period: the next PID step's own put
                # restores the setpoint before the follow check's 8 s are up (information)
                step('second writer mid-period')
                mk = b.mark_log()
                s = wait_pid_phase(scan, 4.3)
                m = w.mark()
                b.put(A1 + 'Setpoint', 5)
                wait_until(lambda: abs(b.get(A1 + 'Setpoint_RBV') - 5) < 0.005, 3, 'Setpoint_RBV at 5', poll=0.1)
                wait_until(lambda: abs(b.get(A1 + 'Setpoint_RBV') - 5) > 0.5, 15,
                           'Setpoint_RBV restored after the mid-period put', poll=0.2)
                t_back = time.time() - (s + 4.3)
                rec['mid_period'] = {'restored_after_s': round(t_back, 1),
                                     'puts': w.since(m), 'log': b.log_lines(since=mk)}
                step(f'mid-period put restored after {t_back:.1f} s by the IOC')
                self.assertLess(t_back, scan + 2.5)
                time.sleep(20)                            # let the O2 and the flow settle again

                # (b) a put just after a PID step: 9 ticks off before the next one -> the alarm
                step('second writer just after a PID step')
                mk = b.mark_log()
                s = wait_pid_phase(scan, 0.3)
                n_cval = len([1 for t, v in cval if t < s + 0.3])
                m = w.mark()
                lc = b.get(P + 'Sts:LastCmd')
                t_put = time.time()
                b.put(A1 + 'Setpoint', 5)
                ma = wait_log(b, mk, r'MAJOR +Alicat setpoint 5\.00 SLPM does not follow the controller \('
                              + NUM + r' SLPM\): write lost or another writer', 20)
                alarm_ln = log_matches(b, mk, r'does not follow the controller')[0]
                t_alarm = log_time(alarm_ln) - t_put
                self.assertEqual(b.get(P + 'Alm:Mismatch'), 2)
                self.assertIn('does not follow the controller', b.get(P + 'Sts:Banner.VAL$', as_string=True))
                cl = wait_log(b, mk, r'cleared: Alicat setpoint 5\.00 SLPM does not follow', 15)
                cleared_ln = log_matches(b, mk, r'cleared: Alicat setpoint')[0]
                time.sleep(2)
                puts = w.since(m)
                rec['after_pid_step'] = {
                    'pid_step_second': s, 'cval_events_before': n_cval, 'lastCmd': lc,
                    'alarm_after_s': round(t_alarm, 1), 'alarm_line': alarm_ln, 'cleared_line': cleared_ln,
                    'alarm_lastcmd': float(ma.group(1)), 'puts': puts, 'log': b.log_lines(since=mk)}
                step(f'alarm {t_alarm:.1f} s after the put; puts {puts}')
                # the controller's re-send: a Setpoint put of its own lastCmd at the alarm tick
                resend = [(t, v) for pv, t, v in puts if abs(v - 5) > 1e-6 and abs(v - float(ma.group(1))) <= 0.006
                          and log_time(alarm_ln) - 0.5 <= t <= log_time(alarm_ln) + 1.5]
                rec['after_pid_step']['resend'] = resend
                self.assertTrue(resend, f'no re-send of {ma.group(1)} at the alarm tick: {puts}')
                self.assertLessEqual(t_alarm, 15, 'alarm later than ~15 s')
                self.assertLessEqual(log_time(cleared_ln) - log_time(alarm_ln), 5, 'alarm not cleared soon after the re-send')
                self.assertEqual(b.get(P + 'Alm:Mismatch'), 0)
                self.assertEqual(b.state(), 'REGULATE')
            finally:
                rec['path'] = str(save('d1b', rec, b))

    # ------------------------------------------------------------------------------------ G2
    def test_g2_shadow_alarm(self):
        rec = {'item': 'G2 shadow alarm'}
        msg = 'shadow mode: the controller is not writing to the Alicat'
        with Bench(noise=True, seed=32, tag='fixg2') as b:
            try:
                apply(b, 'preset', None)
                self.assertEqual(b.get(P + 'Alm:Shadow'), 0)
                step('Par:writeEnable 0 in REGULATE')
                mk = b.mark_log()
                b.put(P + 'Par:writeEnable', 0)
                wait_log(b, mk, r'MAJOR +' + re.escape(msg))
                b.next_tick()
                rec['shadow'] = {'alm': b.get(P + 'Alm:Shadow'), 'msg': b.get(P + 'Alm:Shadow:Msg.VAL$', as_string=True),
                                 'banner': b.get(P + 'Sts:Banner.VAL$', as_string=True), 'state': b.state(),
                                 'worst': b.get(P + 'Sts:WorstSevr')}
                self.assertEqual(rec['shadow']['alm'], 2)
                self.assertEqual(rec['shadow']['msg'], msg)
                self.assertIn(msg, rec['shadow']['banner'])
                self.assertEqual(rec['shadow']['state'], 'REGULATE')
                self.assertStaysIn(b, 'REGULATE', 10)
                self.assertEqual(b.get(P + 'Alm:Shadow'), 2)          # it stays up

                step('Par:writeEnable 1')
                mk = b.mark_log()
                b.put(P + 'Par:writeEnable', 1)
                b.wait_state('IDLE', 5)
                wait_log(b, mk, r'cleared: ' + re.escape(msg))
                b.next_tick()
                rec['live'] = {'alm': b.get(P + 'Alm:Shadow'), 'banner': b.get(P + 'Sts:Banner.VAL$', as_string=True),
                               'state': b.state(), 'log': b.log_lines(since=mk)}
                self.assertEqual(rec['live']['alm'], 0)
                self.assertNotIn(msg, rec['live']['banner'])
                self.assertTrue(log_matches(b, mk, r'writes enabled: Alicat left at'))
            finally:
                rec['path'] = str(save('g2', rec, b))

    # ------------------------------------------------------------------------------------ G1
    def test_g1_restart_mid_purge(self):
        rec = {'item': 'G1 restart mid-purge'}
        with Bench(noise=True, seed=33, tag='fixg1') as b:
            try:
                b.wait_autosaved(['Par:writeEnable'])
                flow = b.watch(A1 + 'Flow_RBV')
                step('Purge from air')
                b.cmd('Purge')
                b.wait_state('PURGE', 30)
                wait_until(lambda: b.get(A1 + 'Flow_RBV') >= 19.9, 60, 'Flow_RBV at 20 SLPM')
                o2_kill = b.get('SIM:O2')
                rec['at_kill'] = {'o2': o2_kill, 'flow': b.get(A1 + 'Flow_RBV'), 'state': b.state(),
                                  'elapsed': b.get(P + 'Diag:PurgeElapsed')}
                step(f'kill the IOC (O2 {o2_kill:.2f} %, Flow_RBV {rec["at_kill"]["flow"]:.2f})')
                self.assertGreaterEqual(o2_kill, 10)
                mk = b.mark_log()
                t_kill = time.time()
                b.stop_ioc(kill=True)
                b.start_ioc()
                m = wait_log(b, mk, r'— → ([A-Z_]+) \((restart: [^)]*)\)', 60)
                rec['restart'] = {'down_s': round(time.time() - t_kill, 1), 'to': m.group(1), 'reason': m.group(2),
                                  'o2': b.get(P + 'Sts:O2'), 'flow': b.get(A1 + 'Flow_RBV')}
                step(f'restart: {m.group(1)} ({m.group(2)})')
                self.assertEqual(m.group(1), 'PRECHECK')
                self.assertEqual(m.group(2), 'restart: O2 above lid threshold with the Alicat flowing: purge resumed')
                wait_log(b, mk, r'PRECHECK → PURGE', 30)
                # the resumed purge must finish (not stop on the lid check of a half-purged enclosure)
                t0 = time.time()
                while b.state() not in ('REGULATE', 'OPEN_STOP', 'IDLE', 'OPEN_LOOP') and time.time() - t0 < 900:
                    time.sleep(2)
                rec['end'] = {'state': b.state(), 'after_s': round(time.time() - t0),
                              'transitions': log_matches(b, mk, ' → ')}
                low = [v for t, v in flow if t_kill < t < t_kill + rec['restart']['down_s'] and v is not None]
                rec['flow_while_down'] = [min(low), max(low)] if low else None
                step(f"end state {rec['end']['state']} after {rec['end']['after_s']} s")
                self.assertEqual(rec['end']['state'], 'REGULATE', rec['end']['transitions'])
            finally:
                rec['path'] = str(save('g1', rec, b))

    # ------------------------------------------------------------------------------------ D2 / D8
    def _start_wait_then_plant(self, item, ioc_env, action, want_reason):
        rec = {'item': item}
        with Bench(noise=True, seed=34, tag=f'fix{item}', plant_first=False, write_enable=0, ioc_env=ioc_env) as b:
            try:
                mk0 = dict(b._log_offsets)                # the log as it was before the IOC started
                wait_log(b, mk0, WAIT_ALARM, 60)          # the tick loop is waiting for the Alicat
                rec['wait'] = {'writeEnable': b.get(P + 'Par:writeEnable'), 'alm': b.get(P + 'Alm:Mismatch'),
                               'msg': b.get(P + 'Alm:Mismatch:Msg.VAL$', as_string=True)}
                self.assertEqual(rec['wait']['alm'], 2)
                step(f'{item}: {action} during the start-up wait')
                if action == 'live':
                    self.assertEqual(rec['wait']['writeEnable'], 0, 'FORCE_SHADOW did not start in shadow')
                    self.assertTrue(log_matches(b, mk0, r'start in shadow mode \(FORCE_SHADOW\)'))
                    b.put(P + 'Par:writeEnable', 1)
                else:
                    self.assertEqual(rec['wait']['writeEnable'], 1, 'expected a live start')
                    b.cmd('ReleaseIdle')
                    wait_log(b, mk0, r'Release control noted', 10)
                for _ in range(3):
                    b.next_tick()
                rec['wait']['state_before_plant'] = b.state()   # (the published state while waiting)
                rec['wait']['writeEnable_before_plant'] = b.get(P + 'Par:writeEnable')
                step('start the plant preset regulating (0.29 SLPM)')
                b.start_plant(preset=0.29)
                m = wait_log(b, mk0, r'— → ([A-Z_]+) \(([^)]*)\)', 60)
                rec['decision'] = {'to': m.group(1), 'reason': m.group(2)}
                step(f'decision: {m.group(1)} ({m.group(2)})')
                w = PutWatch(b, [A1 + s for s in WRITABLE])
                m_w = w.mark()
                t_end = time.time() + 45                   # > 4 PID periods
                states = set()
                while time.time() < t_end:
                    states.add(b.state())
                    time.sleep(1)
                rec['after'] = {'states': sorted(states), 'alicat_puts': b.get(PUTS), 'watch': w.since(m_w),
                                'writeEnable': b.get(P + 'Par:writeEnable'),
                                'setpoint_rbv': b.get(A1 + 'Setpoint_RBV'), 'alm_mismatch': b.get(P + 'Alm:Mismatch'),
                                'log': b.log_lines(since=mk0)}
                self.assertEqual((m.group(1), m.group(2)), ('IDLE', want_reason))
                self.assertEqual(rec['after']['states'], ['IDLE'])
                self.assertEqual(rec['after']['alicat_puts'], 0, 'the plant counted puts to the Alicat')
                self.assertEqual(rec['after']['watch'], [])
                self.assertAlmostEqual(rec['after']['setpoint_rbv'], 0.29, places=3)
                self.assertEqual(rec['after']['alm_mismatch'], 0, 'the wait alarm did not clear')
            finally:
                rec['path'] = str(save(item, rec, b))

    def test_d2_live_in_start_wait(self):
        self._start_wait_then_plant('d2', {'FORCE_SHADOW': '1'}, 'live',
                                    'restart: writes enabled during the start-up wait')

    def test_d8_release_in_start_wait(self):
        # a live start (writes enabled): with FORCE_SHADOW "no put" would hold whatever the state
        self._start_wait_then_plant('d8', {}, 'release', 'admin released control')

    # ------------------------------------------------------------------------------------ §8.18
    def test_mfc_dropout(self):
        rec = {'item': '§8.18 MFC dropout (plant sim stopped ~10 s)'}
        with Bench(noise=True, seed=35, tag='fixdrop') as b:
            try:
                apply(b, 'preset', None)
                time.sleep(30)
                self.assertEqual(b.state(), 'REGULATE')
                f = round(b.get(A1 + 'Flow_RBV'), 2)
                lc = b.get(P + 'Sts:LastCmd')
                rec['before'] = {'flow': f, 'lastCmd': lc, 'puts_old_plant': b.get(PUTS)}
                mk = b.mark_log()
                step(f'stop the plant (Flow_RBV {f}, LastCmd {lc:.4f})')
                t_stop = time.time()
                b.stop_plant()
                wait_log(b, mk, r'MAJOR +MFC not responding \(CA disconnected\)', 10)
                t_alarm = time.time() - t_stop
                self.assertEqual(b.get(P + 'Alm:Mismatch'), 2)
                self.assertIn('MFC not responding (CA disconnected)', b.get(P + 'Sts:Banner.VAL$', as_string=True))
                while time.time() < t_stop + 10:
                    time.sleep(0.2)
                rec['down'] = {'alarm_after_s': round(t_alarm, 1), 'state': b.state(),
                               'log': b.log_lines(since=mk)}
                step(f'down: state {b.state()}; restart the plant preset at {f}')
                t_up = time.time()
                b.start_plant(preset=f)
                ma = wait_log(b, mk, r'MFC reconnected: setpoint ' + NUM + ' re-sent', 60)
                t_re = time.time() - t_up
                mk_up = b.mark_log()
                wait_log(b, mk, r'cleared: MFC not responding \(CA disconnected\)', 5)
                t_end = time.time() + 60
                while time.time() < t_end:
                    time.sleep(1)
                lines = b.log_lines(since=mk)
                plant_log = (RESULTS / f'{b.tag}-{b.stamp}-plant.txt').read_text(encoding='utf-8', errors='replace')
                new_puts = plant_log.split('plant 0 preset at')[-1]
                put_lines = re.findall(r'put (\w+) = (\S+)', new_puts)
                rec['up'] = {'reconnect_after_s': round(t_re, 1), 'resent': float(ma.group(1)), 'state': b.state(),
                             'puts_new_plant': put_lines, 'alm_mismatch': b.get(P + 'Alm:Mismatch'),
                             'log': lines}
                step(f'reconnected {t_re:.1f} s after the plant start; re-sent {ma.group(1)}; '
                     f'state {b.state()}; puts to the new plant: {put_lines[:6]}')
                # no put failed while down (the gate drops them), none queued and flushed
                self.assertEqual([ln for ln in lines if re.search(r'put to .* failed|MFC write failed', ln)], [])
                self.assertTrue(put_lines and put_lines[0][0] == 'Setpoint'
                                and abs(float(put_lines[0][1]) - float(ma.group(1))) < 0.006, put_lines[:3])
                self.assertEqual([p for p in put_lines if p[0] != 'Setpoint'], [], 'RampRate/Run put after the reconnect')
                # D6: no instant flow mismatch at (or after) the reconnect
                self.assertEqual(log_matches(b, mk_up, r'flow mismatch'), [])
                self.assertEqual([ln for ln in lines if 'flow mismatch' in ln], [])
            finally:
                rec['path'] = str(save('dropout', rec, b))

    # ------------------------------------------------------------------------------------ heartbeat
    def test_start_heartbeat(self):
        rec = {'item': 'start-up heartbeat (no plant)'}
        with Bench(noise=True, seed=36, tag='fixhb', plant_first=False, write_enable=0) as b:
            try:
                samples = []
                t_end = time.time() + 45                  # the SNL's 30 s wait state, then ticks
                while time.time() < t_end:
                    pv = b._pv(P + 'Sts:TickAge')
                    age = pv.get(use_monitor=False)
                    pv.get_ctrlvars()                     # refreshes pv.severity (as test_tickage)
                    samples.append((round(time.time(), 1), b.heartbeat(), age, pv.severity, b.state()))
                    time.sleep(1.0)
                rec['samples'] = samples
                rec['alm_mismatch'] = (b.get(P + 'Alm:Mismatch'), b.get(P + 'Alm:Mismatch:Msg.VAL$', as_string=True))
                hbs = [s[1] for s in samples]
                ages = [s[2] for s in samples]
                step(f'heartbeat {hbs[0]} -> {hbs[-1]} in {len(samples)} samples; TickAge max {max(ages)}')
                self.assertTrue(all(b2 >= b1 for b1, b2 in zip(hbs, hbs[1:])), hbs)
                self.assertGreaterEqual(hbs[-1] - hbs[0], len(samples) - 4, hbs)
                self.assertTrue(all(a is not None and a <= 1 for a in ages), ages)
                self.assertTrue(all(s[3] == 0 for s in samples), 'TickAge severity')
                self.assertEqual(rec['alm_mismatch'][0], 2, 'the wait is not on the banner')
                self.assertTrue([ln for ln in b.log_lines() if re.search(WAIT_ALARM, ln)])
            finally:
                rec['path'] = str(save('heartbeat', rec, b))

    # ------------------------------------------------------------------------------------ dropSkipLevel 17 %
    #
    # user's decision 2026-09-30 (docs/ioc/15LSS_sample_gas_IOC_spec.md value table;
    # 2026-09-24-o2-purge-feedback-design.md §2.1.8): dropSkipLevel lowered from 18 % to 17 %,
    # because lid-open handling dips reach ~18.4 % in the extreme, below the archive's 18.67 %
    # floor. At the old default, a purge started during such a dip would have skipped the lid
    # check and sent helium into an open enclosure.
    #
    # The two tests below show the decision at o2Start ~= 17.5 % depends on how O2 *moves* during
    # the purge, not on the starting level: open lid -> caught (OPEN_STOP); closed lid -> passed,
    # purge continues (spec §8.9 step 5). World:HandlingDip (ioc/test/plantsim/plant.py) dips
    # ambient O2 by 1.7 % for 60 s (deeper than the reference's World:BreathDip, -0.6 %); combined
    # with a lid lift, O2 tracks the dipped ambient within ~15-20 s (openTau), landing near 17.5-
    # 17.9 %, close enough to either leave the lid open (test a) or reclose it there (test b) --
    # a realistic "handling dip during a brief look/adjustment" in both cases.
    def _dip_to_17_5(self, b, mk):
        """Trigger the deep dip and open the lid; wait until Sts:O2 is in [17.3, 17.9] % (close
        to the dropSkipLevel boundary of 17 %, comfortably above it). Returns that O2 reading."""
        b.world('HandlingDip', 1)
        b.world('LiftLid', 1)
        o2 = wait_until(lambda: (lambda v: v if v is not None and 17.3 <= v <= 17.9 else None)(b.get(P + 'Sts:O2')),
                         45, 'Sts:O2 in [17.3, 17.9] % after HandlingDip + LiftLid', poll=0.5)
        step(f'O2 at {o2:.3f} % with the lid open (target ~17.5 %)')
        return o2

    # (a) Lid left open through the dip: the lid check must run (dropSkipLevel 17 % does not
    # skip it at o2Start ~17.5 %) and must catch the open lid -- either "no O2 decay" (§8.9 step
    # 5.2) or the ratio/curvature test (§8.9 step 5.3, ratio < 50 % or curvature < 0.8) -- ending
    # in OPEN_STOP with the flow stopped. Never "lid check skipped".
    def test_dropskip_open_lid_caught(self):
        rec = {'item': 'dropSkipLevel 17 %: open lid at o2Start ~17.5 % is caught'}
        with Bench(noise=False, tag='fixdropopen') as b:
            try:
                mk = b.mark_log()
                o2_before = self._dip_to_17_5(b, mk)
                step('Purge with the lid still open')
                mp = b.mark_log()
                b.cmd('Purge')
                b.wait_state('PURGE', 15)
                o2_start = b.get(P + 'Sts:O2')
                rec['o2_before_purge'] = o2_before
                rec['o2Start'] = o2_start
                self.assertGreaterEqual(o2_start, 17.0, 'o2Start below dropSkipLevel: dip missed the target')
                self.assertLess(o2_start, 18.0, 'o2Start at/above the old default: not the case under test')
                self.assertEqual(log_matches(b, mp, 'lid check skipped'), [],
                                  'lid check wrongly skipped at o2Start >= dropSkipLevel (17 %)')
                m = wait_log(b, mp,
                             r'(no O2 decay within \d+ s of full flow: enclosure open\?)'
                             r'|(purge decay (\d+) % of the lid-on rate, curvature (\d+\.\d+): enclosure open\?)',
                             60)
                caught_line = log_matches(b, mp, 'enclosure open\\?')[0]
                rec['caught'] = caught_line
                if m.group(3) is not None:
                    ratio_pct, curv = int(m.group(3)), float(m.group(4))
                    rec['ratio_pct'], rec['curv'] = ratio_pct, curv
                    step(f'caught by kinetics: decay {ratio_pct} % of lid-on rate, curvature {curv}')
                    self.assertTrue(ratio_pct < 50 or curv < 0.8,
                                     f'ratio {ratio_pct} %, curvature {curv}: neither below its open threshold')
                else:
                    step('caught: no O2 decay within lidOnsetMax of full flow')
                b.wait_state('OPEN_STOP', 20)
                time.sleep(2)
                rec['end'] = {'state': b.state(), 'lastCmd': b.get(P + 'Sts:LastCmd'),
                              'setpoint_rbv': b.get(A1 + 'Setpoint_RBV'), 'alm_openstop': b.get(P + 'Alm:OpenStop'),
                              'ratio_pv': b.get(P + 'Diag:LidRatio'), 'curv_pv': b.get(P + 'Diag:LidCurv')}
                step(f"end state {rec['end']['state']}, lastCmd {rec['end']['lastCmd']}, "
                     f"Setpoint_RBV {rec['end']['setpoint_rbv']}")
                self.assertEqual(rec['end']['state'], 'OPEN_STOP')
                self.assertEqual(rec['end']['lastCmd'], 0)
                self.assertAlmostEqual(rec['end']['setpoint_rbv'], 0, delta=0.05)
                self.assertEqual(rec['end']['alm_openstop'], 2)
            finally:
                rec['path'] = str(save('dropskip-open', rec, b))

    # (b) Lid lifted to reach the dip, then reclosed before Purge: the lid check must still run
    # (o2Start ~17.5 % >= dropSkipLevel 17 %), and -- because the enclosure genuinely IS sealed --
    # must pass ("lid check passed ... decay ~100 % of the lid-on rate") and let the purge
    # continue to HANDOFF, not trip OPEN_STOP. A false OPEN_STOP here would stop helium flowing
    # into a sealed box for no reason: the user's "no failure to release gas" requirement
    # (2026-09-30) makes this the more important of the two tests.
    def test_dropskip_closed_lid_passes(self):
        rec = {'item': 'dropSkipLevel 17 %: closed lid at o2Start ~17.5 % still passes'}
        with Bench(noise=False, tag='fixdropclosed') as b:
            try:
                mk = b.mark_log()
                o2_before = self._dip_to_17_5(b, mk)
                step('reclosing the lid, then Purge immediately')
                b.world('CloseLid', 1)
                mp = b.mark_log()
                b.cmd('Purge')
                b.wait_state('PURGE', 15)
                o2_start = b.get(P + 'Sts:O2')
                rec['o2_before_close'] = o2_before
                rec['o2Start'] = o2_start
                self.assertGreaterEqual(o2_start, 17.0, 'o2Start below dropSkipLevel: dip missed the target')
                self.assertLess(o2_start, 18.0, 'o2Start at/above the old default: not the case under test')
                self.assertEqual(log_matches(b, mp, 'lid check skipped'), [],
                                  'lid check wrongly skipped at o2Start >= dropSkipLevel (17 %)')
                m = wait_log(b, mp, r'lid check passed at (\d+) s: decay (\d+) % of the lid-on rate '
                                    r'\(open < \d+ %\), curvature (\d+\.\d+) \(open < [\d.]+\)', 90)
                ratio_pct, curv = int(m.group(2)), float(m.group(3))
                rec['ratio_pct'], rec['curv'] = ratio_pct, curv
                step(f'lid check passed: decay {ratio_pct} % of lid-on rate, curvature {curv}')
                self.assertEqual(log_matches(b, mp, 'enclosure open\\?'), [],
                                  'a sealed box was wrongly judged open')
                # The kinetic purge from ~17.5-17.9 % down to target - delta (~1.03 %) at F/V ~=
                # 0.49 /min takes ~350-400 s (ln(17.7/1.03)/0.0081 s^-1); 600 s leaves margin.
                b.wait_state('HANDOFF', 600)
                rec['end'] = {'state': b.state(), 'ratio_pv': b.get(P + 'Diag:LidRatio'),
                              'curv_pv': b.get(P + 'Diag:LidCurv'), 'alm_openstop': b.get(P + 'Alm:OpenStop')}
                step(f"end state {rec['end']['state']} (HANDOFF reached; helium kept flowing as it should)")
                self.assertIn(rec['end']['state'], ('HANDOFF', 'REGULATE'))
                self.assertEqual(rec['end']['alm_openstop'], 0)
            finally:
                rec['path'] = str(save('dropskip-closed', rec, b))


if __name__ == '__main__':
    unittest.main()
