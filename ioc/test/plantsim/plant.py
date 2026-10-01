"""Pure-Python port of the reference simulator's Plant class.

Ported from simulator/sample_gas_simulator.html: Plant (lines 908-1024), defaultPlantParams
(881-903), clamp (794), DT (795), and the plant half of Station.prototype.presetRegulating
(1585-1592). The reference is never modified; this module reproduces its arithmetic in the exact
operation order of the original expressions so floating-point results match to machine precision
(see ioc/test/test_plant_model.py, TestAgainstReference, and ioc/test/ref/plant_openloop.js for
the JS side of the comparison).

pp (plant parameters) keeps the reference's own camelCase key names -- see default_plant_params().
Top-level Plant attributes are snake_case per the Python interface (lid_type, cyl_p, ...); the
nested dicts (a, an, rbv) also use snake_case keys (JS a.spVal -> a['sp_val']).
"""

import math
import random

DT = 0.25   # physics step, s (simulator/sample_gas_simulator.html:795)


def clamp(x, a, b):
    """simulator/sample_gas_simulator.html:794 -- const clamp = (x, a, b) => Math.min(b, Math.max(a, x));"""
    return min(b, max(a, x))


def _js_round(x):
    """JS Math.round: rounds half up (toward +infinity), unlike Python's banker's round()."""
    return math.floor(x + 0.5)


def default_plant_params():
    """simulator/sample_gas_simulator.html:881-903 -- every key of defaultPlantParams(), verbatim."""
    return {
        'V': 41,                                   # L (MEASURED 40.4-42.2)
        'ingressA_a': 0.26, 'ingressA_b': 0.04,     # normal lid: J = a*F^b  [%*L/min]
        'ingressB_a': 0.71, 'ingressB_b': -0.87,    # collimator lid
        'ingressFmin': 0.2,                         # SLPM floor for the power laws
        'kOffA': 0.00142, 'kOffB': 0.005,           # /min, flow off, lid on (B unmeasured)
        'Fblend': 0.05,                              # SLPM, scale of the flow-off <-> with-flow ingress blend (unmeasured)
        'crackIngress': 3.0,                         # %*L/min extra with a cracked lid
        'openTau': 5.75,                             # s, lid-lift time constant (MEASURED)
        'openMix': 1.0,                              # multiplier on open-lid air exchange (open box at 20 SLPM unmeasured)
        'zoneTauHold': 65,                           # s, sensor-zone lag at hold flow (MEASURED)
        'delayA': 9, 'delayB': 17.7, 'delayMax': 75,  # transport delay = min(max, A + B/F) s
        'ambientBase': 19.2, 'ambientPeak': 19.5, 'ambientRelax': 1800,
        'noise': 0.00084,                            # % per 1 Hz sample, white (MEASURED 2026-09-25, 1 Hz record, normal lid)
        'wanderRel': 7.3e-4,                          # slow correlated reading component, relative sigma
        'wanderTau': 79,                              # s, its correlation time
        'alicatTau': 0.5, 'alicatRes': 0.01, 'alicatMax': 20, 'holdOpenFlow': 0.7,
        'cylP0': 2000, 'cylLitersPerPsi': 4.25, 'cylPempty': 30, 'cylPfull': 150,
    }


class Plant:
    """Enclosure + O2 analyzer + Alicat + cylinder (sample_gas_simulator.html:908-1024)."""

    def __init__(self, pp=None, seed=None, noise=True):
        self.pp = dict(pp) if pp is not None else default_plant_params()
        if not noise:
            # Makes the model deterministic: the white-noise and wander terms multiply by zero,
            # but gauss() is still called from step()/sample_analyzer() below so the RNG sequence
            # would match a noise=True run using the same seed.
            self.pp['noise'] = 0
            self.pp['wanderRel'] = 0
        self._rng = random.Random(seed)
        self._gauss_cache = None
        self.reset()

    # ---- Box-Muller gauss(), matching simulator/sample_gas_simulator.html:800-805 exactly,
    # including the second-value cache.
    def _gauss(self):
        if self._gauss_cache is not None:
            v = self._gauss_cache
            self._gauss_cache = None
            return v
        u = 0.0
        while u == 0:
            u = self._rng.random()
        v = self._rng.random()
        r = math.sqrt(-2 * math.log(u))
        self._gauss_cache = r * math.sin(2 * math.pi * v)
        return r * math.cos(2 * math.pi * v)

    def reset(self):
        pp = self.pp
        self.t = 0
        self.lid = 'closed'; self.lid_type = 'A'; self.crack = False; self.seal = 1.0
        self.last_lift = -1e9; self.dip_until = -1; self.dip_amount = 0.6
        amb = self.ambient()
        self.C = amb; self.zone = amb
        self.hist_len = math.ceil(200 / DT)
        self.hist = [amb] * self.hist_len; self.hi = 0
        self.d_eff = pp['delayMax']; self.delayed = amb; self.wander = 0
        self.a = {'sp_val': 0, 'sp_dev': 0, 'sp_ramped': 0, 'flow': 0, 'running': True, 'stuck': False,
                  'hold_flow': 0, 'ramp': 3, 'gas': 'He', 'total': 0}
        self.cyl_p = pp['cylP0']; self.cyl_present = False   # the cylinder-pressure PV does not exist yet
        self.an = {'mode': 'normal', 'value': amb, 'sevr': 0}
        self.rbv = {'flow': 0, 'sp': 0, 'running': True, 'ramp': 3, 'gas': 'He', 'units': 'SLPM', 'total': 0}

    def ambient(self):
        pp = self.pp
        a = pp['ambientBase'] + (pp['ambientPeak'] - pp['ambientBase']) * math.exp(-(self.t - self.last_lift) / pp['ambientRelax'])
        if self.t < self.dip_until:
            a -= self.dip_amount
        return a

    def _ingress(self, F):
        pp = self.pp
        Fe = max(F, pp['ingressFmin'])
        if self.lid_type == 'A':
            J = pp['ingressA_a'] * (Fe ** pp['ingressA_b'])
        else:
            J = pp['ingressB_a'] * (Fe ** pp['ingressB_b'])
        return J * self.seal

    def _cyl_capacity(self):
        pp = self.pp
        return pp['alicatMax'] * clamp((self.cyl_p - pp['cylPempty']) / (pp['cylPfull'] - pp['cylPempty']), 0, 1)

    def step(self, dt=DT):
        pp = self.pp; a = self.a
        # --- Alicat: ramp toward the device setpoint unless the valve is held
        if a['running']:
            if a['ramp'] <= 0:
                a['sp_ramped'] = a['sp_dev']
            else:
                s = a['ramp'] * dt
                a['sp_ramped'] += clamp(a['sp_dev'] - a['sp_ramped'], -s, s)
            tgt = a['sp_ramped']
        else:
            tgt = a['hold_flow']
        tgt = min(tgt, self._cyl_capacity())
        a['flow'] += (tgt - a['flow']) * min(1, dt / pp['alicatTau'])
        if a['flow'] < 1e-4:
            a['flow'] = 0
        self.cyl_p = max(0, self.cyl_p - a['flow'] * dt / 60 / pp['cylLitersPerPsi'])
        a['total'] += a['flow'] * dt / 60           # Alicat totalizer, standard litres
        # --- Enclosure O2 (well-mixed bulk, spec Sec 2.1.1)
        F = a['flow']; V = pp['V']; amb = self.ambient()
        if self.lid == 'open':
            dC = pp['openMix'] * (amb - self.C) / pp['openTau'] - (F / 60) * self.C / V
        else:
            f = max(0, (amb - self.C) / (amb - 1))          # ingress shrinks as C approaches ambient
            crackJ = pp['crackIngress'] * f if self.crack else 0
            Joff = pp['kOffA' if self.lid_type == 'A' else 'kOffB'] * V * (amb - self.C)   # %*L/min
            w = math.exp(-F / pp['Fblend'])
            dC = ((1 - w) * self._ingress(F) * f + w * Joff + crackJ - F * self.C) / V / 60
        self.C = clamp(self.C + dC * dt, 0, 21)
        # --- Sensor zone lag (Sec 2.1.5) and transport delay (Sec 2.1.3)
        tz = 2 if self.lid == 'open' else max(3, pp['zoneTauHold'] * min(1, 0.25 / max(F, 0.01)))
        self.zone += (self.C - self.zone) * min(1, dt / tz)
        self.hi = (self.hi + 1) % self.hist_len; self.hist[self.hi] = self.zone
        dWant = 2 if self.lid == 'open' else min(pp['delayMax'], pp['delayA'] + pp['delayB'] / max(F, 0.2))
        if dWant < self.d_eff:
            self.d_eff = dWant
        else:
            self.d_eff = min(dWant, self.d_eff + 0.5 * dt)
        lag = min(self.hist_len - 1, _js_round(self.d_eff / DT))
        # Slow correlated reading component (Ornstein-Uhlenbeck, relative) seen in the 1 Hz data; not bulk O2
        self.wander += -self.wander * dt / pp['wanderTau'] + pp['wanderRel'] * math.sqrt(2 * dt / pp['wanderTau']) * self._gauss()
        self.delayed = self.hist[(self.hi - lag + self.hist_len) % self.hist_len] * (1 + (0 if self.lid == 'open' else self.wander))

    def sample_analyzer(self):                     # 1 Hz
        an = self.an
        if an['mode'] == 'normal':
            an['value'] = self.delayed + self.pp['noise'] * self._gauss()
            an['sevr'] = 0
        elif an['mode'] == 'invalid':
            an['sevr'] = 3
        # 'frozen': value and severity stay exactly as they were

    def poll_alicat(self):                         # stream poll: Flow_RBV, Setpoint_RBV, Running_RBV, RampRate_RBV, Gas_RBV
        a = self.a; r = self.rbv
        r['flow'] = _js_round(a['flow'] * 100) / 100
        r['sp'] = a['sp_dev']; r['running'] = a['running']; r['ramp'] = a['ramp']; r['gas'] = a['gas']
        r['total'] = _js_round(a['total'] * 100) / 100   # Total_RBV

    # ---- Channel Access puts from the controller
    def put_setpoint(self, v):                     # Setpoint record has SDIS=Running_RBV, DISV=0
        a = self.a
        a['sp_val'] = _js_round(v / self.pp['alicatRes']) * self.pp['alicatRes']
        if a['running']:
            a['sp_dev'] = a['sp_val']               # otherwise VAL changes but nothing reaches the device
        return a['running']

    def put_run(self):                              # "C": cancel hold; device resumes its own (old) setpoint
        a = self.a
        if a['stuck']:
            return False
        if not a['running']:
            a['running'] = True; a['sp_ramped'] = a['flow']
        return True

    def put_ramp(self, v):
        self.a['ramp'] = v

    # ---- World actions
    def hold(self, kind):
        a = self.a
        a['running'] = False; a['stuck'] = (kind == 'stuck')
        a['hold_flow'] = self.pp['holdOpenFlow'] if kind == 'open' else a['flow']

    def clear_hold(self):
        self.a['stuck'] = False
        self.put_run()

    def lift_lid(self):
        if self.lid != 'open':
            self.lid = 'open'; self.crack = False; self.last_lift = self.t

    def close_lid(self):
        self.lid = 'closed'; self.crack = False

    def crack_lid(self):
        self.lid = 'closed'; self.crack = True

    def reseat(self):
        self.seal = clamp(math.exp(math.log(1.3) * self._gauss()), 0.6, 1.8)

    def breath_dip(self, amount=0.6):                # ambient -amount % for 60 s (sample_gas_simulator.html:2073;
        self.dip_until = self.t + 60                 # default amount matches the reference exactly)
        self.dip_amount = amount

    def handling_dip(self):
        # Deeper than breath_dip: models an open-lid *handling* dip (user, 2026-09-30), not the
        # reference's breath/He-pocket dip. No JS reference equivalent -- test-only, for exercising
        # dropSkipLevel (17 %) at a purge-start O2 around 17.5 %, from an ambientBase of 19.2 %.
        self.breath_dip(amount=1.7)

    def steady_at(self, F):                          # O2 steady state for a given flow (lid closed)
        C = 1
        for _ in range(60):
            amb = self.ambient()
            f = max(0, (amb - C) / (amb - 1))
            C = self._ingress(F) * f / F
        return C

    def set_o2_everywhere(self, C):
        self.C = C; self.zone = C
        for i in range(self.hist_len):
            self.hist[i] = C
        self.delayed = C; self.an['value'] = C; self.an['sevr'] = 0

    def preset(self, F):
        # The plant half of Station.prototype.presetRegulating (sample_gas_simulator.html:1585-1592):
        # start already at steady state for a given flow, Alicat already running at that flow.
        self.set_o2_everywhere(self.steady_at(F))
        self.a['sp_val'] = F; self.a['sp_dev'] = F; self.a['sp_ramped'] = F; self.a['flow'] = F; self.a['running'] = True
        self.d_eff = self.pp['delayMax']
        self.poll_alicat()
