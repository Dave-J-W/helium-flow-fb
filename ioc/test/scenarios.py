"""scenarios.py: the reference simulator's 19 SCENARIOS (simulator/sample_gas_simulator.html
lines 1606-1700) as data, with the SELFTEST pass criteria (lines 2225-2245; spec §14.2).

Each entry: n, name, dur (s), setup [(action, arg)], events [(t, action, arg)] (t in seconds after
the first IOC tick following setup), state (expected end state), has (regexes that must match a
log line written after the scenario start), replay_only (17, 19: accepted by the Plan 1 replay,
spec §14.4).

Actions (run by test_scenarios.apply):
  purge, flow_zero, resume_flow (the reference opResume; IOC Cmd:ResumeFlow), new_cylinder,
  mark_new_run: operator commands (Cmd:*)
  target <v>, mode <A|B|C|D>: operator parameters
  world <Suffix> <value>: plant world control SIM:World:<Suffix>
  preset: the reference presetRegulating -- plant World:Preset at the IOC's Sts:ExpectedFlow, via
          an IOC stop/start (the §8.15 restart path, as the reference's ctl.restart())
  crash / restart: a real IOC kill / start (scenario 14, spec §14.3)
"""

PRESET = [('preset', None)]


def _sc(n, name, dur, state, has, setup=(), events=(), replay_only=False):
    return {'n': n, 'name': name, 'dur': dur, 'setup': list(setup), 'events': list(events),
            'state': state, 'has': list(has), 'replay_only': replay_only}


def _lid_cycle(t):
    return [(t, 'flow_zero', None), (t + 60, 'world', ('LiftLid', 1)),
            (t + 660, 'world', ('CloseLid', 1)), (t + 700, 'purge', None)]


def _sc17_events():
    e = []
    for k in range(16):
        e += _lid_cycle(3600 + k * 6 * 3600)
    return e


def _sc19_events():
    e = []

    def run(d0, d1):
        t = round(d0 * 86400) + 300
        while t < d1 * 86400:
            e.extend(_lid_cycle(t))
            t += 6 * 3600
        e.append((round(d1 * 86400), 'flow_zero', None))
    run(0, 3); run(5 + 10 / 24, 8); run(8.5, 11); run(14.5, 15.5)
    e.append((round(8.1 * 86400), 'mark_new_run', None))
    e.append((round(9.5 * 86400) + 1000, 'world', ('CylPressure', 2000)))   # plant cylP0 (full cylinder)
    e.append((round(9.5 * 86400) + 1000, 'new_cylinder', None))
    return sorted(e, key=lambda x: x[0])


SCENARIOS = [
    _sc(1, 'Normal purge from air, normal lid (mode A)', 3600, 'REGULATE',
        ['lid check passed', '→ REGULATE'], events=[(5, 'purge', None)]),
    _sc(2, 'Purge with the lid left open', 600, 'OPEN_STOP', [r'enclosure open\?'],
        setup=[('world', ('LiftLid', 1))], events=[(5, 'purge', None)]),
    _sc(3, 'Lid lifted during regulation (Flow Zero forgotten)', 1200, 'OPEN_STOP',
        ['enclosure opened, flow stopped'], setup=PRESET, events=[(600, 'world', ('LiftLid', 1))]),
    _sc(4, 'Flow Zero, lid kept on 22 min, then Purge', 1800, 'REGULATE', ['lid check skipped'],
        setup=PRESET, events=[(60, 'flow_zero', None), (60 + 1320, 'purge', None)]),
    _sc(5, 'Flow Zero, lift 76 s later, open 2 min, re-purge (24 Sep 11:52)', 1800, 'REGULATE',
        ['lid check passed'], setup=PRESET,
        events=[(60, 'flow_zero', None), (136, 'world', ('LiftLid', 1)), (256, 'world', ('CloseLid', 1)),
                (270, 'purge', None)]),
    # deliberate mismatch: collimator lid fitted, mode A selected (reference lidLinked = false)
    _sc(6, 'Collimator lid fitted but mode A selected', 14400, 'REGULATE', ['flow ≥ 2× expected'],
        setup=[('world', ('LidType', 'B')), ('mode', 'A'), ('preset', None)],
        events=[(1, 'world', ('LidType', 'B'))]),
    _sc(7, 'Cylinder runs dry during regulation (7 h creep)', 3600, 'REGULATE', ['flow mismatch'],
        setup=PRESET, events=[(60, 'world', ('CylPressure', 0))]),
    _sc(8, 'MFC goes on hold during regulation (valve 100 %)', 1200, 'REGULATE',
        ['MFC was on hold, resumed'], setup=PRESET, events=[(300, 'world', ('Hold', 'open'))]),
    _sc(9, 'MFC stuck on hold', 1200, 'REGULATE', ['MFC on hold, cannot resume'], setup=PRESET,
        events=[(300, 'world', ('Hold', 'stuck'))]),
    _sc(10, 'O2 analyzer freezes, recovers, operator resumes', 2400, 'REGULATE',
        ['O2 reading frozen', 'operator pressed Resume Flow'], setup=PRESET,
        events=[(300, 'world', ('AnalyzerMode', 'frozen')), (1500, 'world', ('AnalyzerMode', 'normal')),
                (1560, 'resume_flow', None)]),
    _sc(11, 'Analyzer INVALID at purge start (blind purge)', 1200, 'REGULATE',
        ['blind purge', 'operator pressed Resume Flow'], setup=[('world', ('AnalyzerMode', 'invalid'))],
        events=[(5, 'purge', None), (600, 'world', ('AnalyzerMode', 'normal')), (660, 'resume_flow', None)]),
    _sc(12, 'Bad ramp-rate settings', 1200, 'REGULATE', ['ramp rate 0', 'handoff minimum'],
        setup=[('world', ('SetRamp', 0))], events=[(5, 'purge', None), (120, 'world', ('SetRamp', 0.5))]),
    _sc(13, 'Cylinder nearly empty at purge', 1200, 'REGULATE', ['flow mismatch'],
        setup=[('world', ('CylPressure', 140))], events=[(5, 'purge', None)]),
    # the reference's crash()/restart() become a real IOC kill and start (spec §14.2, §14.3);
    # 'IOC crashed' (a reference-only log line) is replaced by the restart's own line
    _sc(14, 'IOC crash and restart during regulation', 1200, 'REGULATE', ['resume regulation'],
        setup=PRESET, events=[(300, 'crash', None), (900, 'restart', None)]),
    _sc(15, 'Cracked lid during regulation', 3600, 'REGULATE', ['PID pinned at max flow|O2 abnormally high'],
        setup=PRESET, events=[(300, 'world', ('CrackLid', 1))]),
    _sc(16, 'Target change 0.99 → 0.50 %', 10800, 'REGULATE', ['settled: O2 reached 0.500'],
        setup=PRESET, events=[(300, 'target', 0.5)]),
    _sc(17, 'Cylinder run-out forecast: 4 days of use', 172800, None, [],
        setup=PRESET + [('new_cylinder', None)], events=_sc17_events(), replay_only=True),
    _sc(18, 'Collimator lid, mode B, normal purge', 3600, 'REGULATE', ['lid check passed'],
        setup=[('world', ('LidType', 'B')), ('mode', 'B')], events=[(5, 'purge', None)]),
    _sc(19, 'Helium ledger: three users, a 48 h downtime, a marked changeover', 777600, None, [],
        setup=[('new_cylinder', None)], events=_sc19_events(), replay_only=True),
]

BY_N = {s['n']: s for s in SCENARIOS}
REALTIME = [s['n'] for s in SCENARIOS if not s['replay_only']]
SHORT = [2, 3, 8, 9, 12, 13, 14]
LONG = [1, 4, 5, 6, 7, 10, 11, 15, 16, 18]
