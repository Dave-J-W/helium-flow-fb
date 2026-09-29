import json
import math
import os
import unittest

from plantsim.plant import DT, Plant

HERE = os.path.dirname(os.path.abspath(__file__))
GOLDEN = os.path.join(HERE, 'golden', 'plant_openloop.json')

STEPS_PER_S = round(1 / DT)


# Case setup/actions mirror ioc/test/ref/plant_openloop.js exactly (task-1-brief.md, Step 1).
# 'setup' runs once on a freshly-constructed Plant; 'actions' maps a whole second to a callable
# applied to the plant at the *start* of that second, before it is stepped.
def _preset(F):
    return lambda p: p.preset(F)


def _collimator_setup(p):
    p.lid_type = 'B'
    p.preset(0.84)


CASES = {
    'purge_from_air': {
        'n': 900,
        'setup': None,   # lid closed, air start (Plant.reset() default)
        'actions': {5: lambda p: p.put_setpoint(20)},
    },
    'hold_normal_lid': {
        'n': 3600,
        'setup': _preset(0.25),
        'actions': {},
    },
    'lid_lift': {
        'n': 600,
        'setup': _preset(0.25),
        'actions': {
            120: lambda p: p.lift_lid(),
            300: lambda p: p.close_lid(),
        },
    },
    'collimator': {
        'n': 1800,
        'setup': _collimator_setup,
        'actions': {600: lambda p: p.put_setpoint(1.2)},
    },
    'alicat_semantics': {
        'n': 400,
        'setup': _preset(0.29),
        'actions': {
            60: lambda p: p.hold('open'),
            70: lambda p: p.put_setpoint(0.5),      # held: must not reach the device
            90: lambda p: p.put_run(),
            120: lambda p: p.put_setpoint(0.5),
            200: lambda p: p.put_ramp(0),
            210: lambda p: p.put_setpoint(2),
            300: lambda p: setattr(p, 'cyl_p', 140),
            310: lambda p: p.put_setpoint(20),
        },
    },
    'analyzer_modes': {
        'n': 200,
        'setup': _preset(0.25),
        'actions': {
            50: lambda p: p.an.__setitem__('mode', 'invalid'),
            100: lambda p: p.an.__setitem__('mode', 'normal'),
            150: lambda p: p.an.__setitem__('mode', 'frozen'),
        },
    },
}


def run_case(name, n):
    """Replays the same case as plant_openloop.js in Python; returns the records."""
    case = CASES[name]
    plant = Plant(noise=False)
    if case['setup'] is not None:
        case['setup'](plant)
    actions = case['actions']
    records = []
    for sec in range(1, n + 1):
        act = actions.get(sec)
        if act is not None:
            act(plant)
        for k in range(1, STEPS_PER_S + 1):
            plant.t = (sec - 1) + k * DT
            plant.step(DT)
        plant.sample_analyzer()
        plant.poll_alicat()
        records.append({
            't': sec, 'o2': plant.an['value'], 'sevr': plant.an['sevr'], 'C': plant.C,
            'flow': plant.rbv['flow'], 'sp': plant.rbv['sp'], 'total': plant.rbv['total'],
            'running': plant.rbv['running'],
        })
    return records


class TestAgainstReference(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.exists(GOLDEN):
            raise unittest.SkipTest('run ioc/tools/plant_ref.sh first (needs node)')
        with open(GOLDEN) as f:
            cls.ref = json.load(f)

    def check(self, name):
        ref = self.ref[name]
        got = run_case(name, len(ref))
        worst = 0.0
        for r, g in zip(ref, got):
            for k in ('o2', 'C', 'flow', 'sp', 'total'):
                a, b = r[k], g[k]
                rel = abs(a - b) / max(abs(a), 1e-3)
                worst = max(worst, rel)
                self.assertLessEqual(rel, 0.005, f'{name} t={r["t"]} {k}: ref {a} got {b}')
            self.assertEqual(r['sevr'], g['sevr'], f'{name} t={r["t"]} sevr')
            self.assertEqual(bool(r['running']), bool(g['running']), f'{name} t={r["t"]} running')
        print(f'{name}: worst relative difference {worst:.2e}')

    def test_purge_from_air(self):   self.check('purge_from_air')
    def test_hold_normal_lid(self):  self.check('hold_normal_lid')
    def test_lid_lift(self):         self.check('lid_lift')
    def test_collimator(self):       self.check('collimator')
    def test_alicat_semantics(self): self.check('alicat_semantics')
    def test_analyzer_modes(self):   self.check('analyzer_modes')


def run(plant, seconds):
    """Advance `plant` by a whole number of seconds, stepping DT four times per second (as
    Station.step does -- plant.t set before each physics step), then poll once at the end:
    sample_analyzer() and poll_alicat(), mirroring the once-per-second polls in run_case() above.
    For per-second observation (e.g. checking every Flow_RBV sample), call run(plant, 1) in a loop.
    """
    n = round(seconds / DT)
    t0 = plant.t
    for k in range(1, n + 1):
        plant.t = t0 + k * DT
        plant.step(DT)
    plant.sample_analyzer()
    plant.poll_alicat()


class TestSemantics(unittest.TestCase):
    """Alicat hold/run/ramp/cylinder and analyzer semantics, spec Sec 6.1 and Sec 14.1.

    These encode documented behaviour directly (no reference JSON needed); every test starts
    from a fresh deterministic Plant preset at 0.29 SLPM, matching task-2-brief.md Step 1.
    """

    def setUp(self):
        self.plant = Plant(noise=False)
        self.plant.preset(0.29)

    # 1. Setpoint while on hold: put_setpoint() returns False (Alicat not running), VAL (sp_val)
    #    changes but nothing reaches the device (sp_dev), so Setpoint_RBV is unaffected.
    def test_setpoint_while_held_does_not_reach_device(self):
        p = self.plant
        p.hold('current')
        sp_dev_before = p.a['sp_dev']
        rbv_sp_before = p.rbv['sp']
        self.assertFalse(p.put_setpoint(0.8))
        self.assertAlmostEqual(p.a['sp_val'], 0.8, places=9)
        self.assertEqual(p.a['sp_dev'], sp_dev_before)
        run(p, 5)
        self.assertEqual(p.a['sp_dev'], sp_dev_before)
        self.assertEqual(p.rbv['sp'], rbv_sp_before)

    # 2. put_run() after a non-stuck hold: resumes the device's OLD setpoint -- a held
    #    put_setpoint() must not have reached sp_dev -- and Running_RBV goes true at the next poll.
    def test_run_after_hold_resumes_old_setpoint(self):
        p = self.plant
        p.hold('current')
        old_sp_dev = p.a['sp_dev']
        p.put_setpoint(0.8)                 # held: must not reach the device
        self.assertTrue(p.put_run())
        self.assertTrue(p.a['running'])
        self.assertEqual(p.a['sp_dev'], old_sp_dev)
        run(p, 1)
        self.assertTrue(p.rbv['running'])

    # 3. hold('stuck'): put_run() (the "C" cancel-hold command) fails and the valve stays held;
    #    clear_hold() (operator's "Resume Flow") clears the stuck flag and resumes.
    def test_stuck_hold_blocks_run_until_cleared(self):
        p = self.plant
        p.hold('stuck')
        self.assertFalse(p.put_run())
        self.assertFalse(p.a['running'])
        p.clear_hold()
        self.assertTrue(p.a['running'])

    # 4. hold('open'): the device's held target is holdOpenFlow (0.7 SLPM, defaultPlantParams),
    #    and flow relaxes there (alicatTau 0.5 s) well within 5 s.
    def test_hold_open_flow_target(self):
        p = self.plant
        p.hold('open')
        run(p, 5)
        self.assertAlmostEqual(p.a['flow'], p.pp['holdOpenFlow'], delta=0.001)

    # 5. Ramp: put_ramp(3) limits the rate to 3 SLPM/s, so after 1 s flow is bounded by the
    #    commanded ramp plus a small alicatTau margin; after 10 s (well past 20/3 s of ramping)
    #    flow is essentially at the 20 SLPM target. put_ramp(0) is instant (rate-unlimited), so
    #    from a fresh setpoint the alicatTau=0.5 s response alone settles within 0.05 SLPM by 3 s.
    def test_ramp_rate_and_instant(self):
        p = self.plant
        p.put_ramp(3)
        p.put_setpoint(20)
        run(p, 1)
        self.assertLessEqual(p.a['flow'], 0.29 + 3 + 0.5)   # ramp*t + alicatTau settling margin
        run(p, 9)
        self.assertGreaterEqual(p.a['flow'], 19.9)
        p.put_ramp(0)
        p.put_setpoint(0.29)
        run(p, 3)
        self.assertAlmostEqual(p.a['flow'], 0.29, delta=0.05)

    # 6. Quantisation: put_setpoint() rounds VAL to alicatRes (0.01 SLPM); Flow_RBV is always
    #    rounded to a multiple of 0.01 by poll_alicat(), every single second over a full minute.
    def test_setpoint_and_flow_rbv_quantisation(self):
        p = self.plant
        p.put_setpoint(0.2937)
        self.assertEqual(p.a['sp_val'], 0.29)
        for _ in range(60):
            run(p, 1)
            v = p.rbv['flow']
            self.assertLess(abs(v * 100 - round(v * 100)), 1e-9, f'flow {v} not a 0.01 multiple')

    # 7. Cylinder: the Alicat's target is capped at alicatMax * (cylP - cylPempty) /
    #    (cylPfull - cylPempty); as the cylinder empties further the cap -- and so the flow --
    #    keeps falling. cyl_p == 0 (at or below cylPempty) caps flow at 0.
    def test_cylinder_pressure_caps_flow(self):
        p = self.plant
        p.cyl_p = 140
        p.put_setpoint(20)
        cap0 = p.pp['alicatMax'] * (140 - p.pp['cylPempty']) / (p.pp['cylPfull'] - p.pp['cylPempty'])
        run(p, 15)                          # long enough for alicatTau to settle near the cap
        flow_early = p.a['flow']
        self.assertLessEqual(flow_early, cap0 + 0.05)
        self.assertAlmostEqual(flow_early, cap0, delta=0.5)
        run(p, 100)                         # cylinder keeps draining -> cap, and flow, keep falling
        self.assertLess(p.a['flow'], flow_early)
        p.cyl_p = 0
        run(p, 5)
        self.assertEqual(p.a['flow'], 0)

    # 8. Analyzer modes: 'invalid' raises sevr to INVALID (3) and freezes the last value;
    #    'frozen' repeats the same value on consecutive 1 Hz samples while the bulk (C) moves;
    #    back to 'normal' clears the severity.
    def test_analyzer_mode_semantics(self):
        # preset(0.29) sits at an exact fixed point (an['value'] never moves regardless of mode)
        # and, worse, an['value'] tracks plant.delayed, not the bulk C -- so a test that only
        # checks "value unchanged" against a static baseline, or watches C instead of delayed,
        # passes even with the freeze logic deleted (round-1 review finding). Use a purge from
        # air instead: put_setpoint(20) on a fresh Plant, then wait past the transport delay
        # (~9-10 s at 20 SLPM) so plant.delayed -- and therefore an['value'] in 'normal' mode --
        # is visibly falling every single second; then prove 'invalid' and 'frozen' hold
        # an['value'] exactly fixed while plant.delayed keeps moving underneath.
        p = Plant(noise=False)
        p.put_setpoint(20)
        run(p, 60)                          # well past the transport delay; delayed is falling

        # normal: the reading itself moves second to second (this is the signal that must
        # freeze in the other two modes -- not C, which the reading doesn't track directly).
        v0 = p.an['value']
        run(p, 1)
        v1 = p.an['value']
        self.assertEqual(p.an['sevr'], 0)
        self.assertNotEqual(v1, v0, 'reading did not move in normal mode; test cannot discriminate')

        # invalid: sevr goes to INVALID (3) and the reading freezes at its last normal value,
        # even though the underlying delayed pipeline keeps changing every second.
        p.an['mode'] = 'invalid'
        frozen_value = p.an['value']
        for _ in range(5):
            delayed_before = p.delayed
            run(p, 1)
            self.assertEqual(p.an['sevr'], 3)
            self.assertEqual(p.an['value'], frozen_value)
            self.assertNotEqual(p.delayed, delayed_before, 'delayed stopped moving; test is vacuous')

        # frozen: same freeze, no severity change, same live proof that delayed keeps moving.
        p.an['mode'] = 'frozen'
        for _ in range(5):
            delayed_before = p.delayed
            run(p, 1)
            self.assertEqual(p.an['value'], frozen_value)
            self.assertNotEqual(p.delayed, delayed_before, 'delayed stopped moving; test is vacuous')

        # normal again: severity clears and the reading resumes tracking delayed exactly
        # (noise=False, so an['value'] == delayed with no noise term).
        p.an['mode'] = 'normal'
        run(p, 1)
        self.assertEqual(p.an['sevr'], 0)
        self.assertEqual(p.an['value'], p.delayed)

    # 9. Totalizer: Total_RBV accumulates flow * dt / 60 (standard litres) and never resets;
    #    at a steady 0.5 SLPM over 600 s that is 5.0 L, matched within 1 %.
    def test_totalizer_accumulates_with_flow(self):
        p = Plant(noise=False)
        p.preset(0.5)
        total_before = p.rbv['total']
        run(p, 600)
        total_after = p.rbv['total']
        expected = total_before + 0.5 * 600 / 60
        self.assertLessEqual(abs(total_after - expected) / expected, 0.01)

    # 10. Lid lift at hold flow: with the sensor-zone lag and transport delay both collapsing to
    #     ~2 s when the lid opens (dWant drops instantly; only increases are rate-limited), and the
    #     bulk O2 relaxing to ambient with openTau = 5.75 s, the reading should be well clear of
    #     10 % O2 (vs. ~0.9 % at the 0.29 SLPM steady state) long before the 5.75 + 2 s constant
    #     would suggest, and comfortably within the 30 s bound.
    def test_lid_lift_reading_rises_past_10_percent(self):
        p = self.plant
        p.lift_lid()
        exceeded_at = None
        for sec in range(1, 31):
            run(p, 1)
            if p.an['value'] > 10:
                exceeded_at = sec
                break
        self.assertIsNotNone(exceeded_at, 'reading never exceeded 10% O2 within 30 s of lift_lid()')
        self.assertLessEqual(exceeded_at, 30)


if __name__ == '__main__':
    unittest.main()
