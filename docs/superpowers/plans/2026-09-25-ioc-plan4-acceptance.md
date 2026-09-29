# 15LSS_sample_gas IOC, Plan 4: acceptance on the bench and debugging

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove the real IOC behaves like the reference on the bench, in real time, against the
plant simulator: the 17 real-time scenarios, the quantitative comparison, the restart test, the
PV-name test and the write-enable/Resume Flow test (spec §14.2-§14.3b, §14.5), and fix every defect
they expose. The user asked (2026-09-25) to "debug everything you can after construction": this plan
is where that happens.

**Architecture:** A Python harness (`ioc/test/bench.py`) starts and stops the plant simulator and
the IOC as subprocesses with the bench CA environment, and offers PV helpers and log access (the
IOC's persistent log file). Each scenario is a Python port of the reference's `SCENARIOS` entry:
world actions become `SIM:World:*` puts, operator actions `Cmd:*`/`Par:*` puts, and
`presetRegulating` becomes a plant preset followed by an IOC restart (the same §4.7 path the
reference uses). Pass criteria are the reference `SELFTEST` rows (spec §14.2 table).

**Tech Stack:** Python 3.12 venv (pyepics, caproto, stdlib unittest), the Plan 2 IOC, the Plan 3
simulator, Node.js for noise-free reference traces.

**Spec:** §14.2, §14.3, §14.3a, §14.3b, §14.5. **Depends on:** Plans 1-3 complete on one branch.

## Global Constraints

- Plan 1-3 constraints apply (localhost only, bench prefixes, no `15IDC:*`, no heredocs, commit
  rules, no site paths).
- Tests never edit the IOC or simulator source to make a test pass unless the test proves a defect
  against the spec; every such fix is its own commit with the failing test named in the message.
- Long runs go to the background with results written to `ioc/test/results/` (gitignored), so a
  crashed session loses nothing.

## File map

```
ioc/test/
  bench.py                  process management + PV/log helpers (context manager Bench)
  scenarios.py              the 19 scenarios as data: setup + timed actions + SELFTEST criteria
  test_scenarios.py         real-time runner (unittest), selectable by scenario number
  test_compare.py           quantitative comparison vs noise-free reference (sc 1, 2, 3, 8)
  test_restart.py           spec §14.3
  test_pvnames.py           spec §14.3a
  test_write_enable.py      spec §14.3b + shadow verification (§14.5)
  results/                  (gitignored) per-run JSON + logs
ioc/test/ref/make_traces.js gains --no-noise (writes golden/nonoise/)
```

---

### Task 1: Bench harness

**Files:** Create `ioc/test/bench.py`, `ioc/test/results/.gitignore`; test `ioc/test/test_bench.py`.

**Interfaces (produced):**
```python
class Bench:                                    # with Bench(noise=False, seed=1) as b: ...
    def __init__(self, noise=True, seed=None, second_plant=False, write_enable=1): ...
    def start_plant(self) / stop_plant(self) / start_ioc(self) / stop_ioc(self, kill=False)
    def restart_ioc(self, down_s=0.0)           # stop (kill), wait, start; waits for Heartbeat
    def get(self, pv, **kw) / put(self, pv, value, wait=True)
    def cmd(self, name, station='SIM')          # Cmd:<name> = 1
    def state(self, station='SIM') -> str       # Sts:State as its string
    def wait_state(self, want, timeout) -> float  # returns elapsed seconds; raises on timeout
    def log_lines(self, station='SIM') -> list[str]   # from the persistent log file, this run only
    def watch(self, pv) -> list[tuple[float, float]]  # monitor recorder (t, value), for no-put checks
```
`start_ioc` clears nothing: autosave files persist across restarts within a Bench session, and a
fresh Bench starts from a clean autosave directory (delete `autosave/*.sav*` in `__enter__`, so each
scenario starts from database defaults, as `startScenario` resets to defaults).

- [ ] Step 1: failing test (start both, heartbeat increments, stop leaves no process).
- [ ] Step 2: implement (subprocess with the bench env from `bench_env.ps1` values; IOC via
  `ioc/tools/run_ioc.sh --background` through MSYS2; kill = terminate the process tree).
- [ ] Step 3: pass; commit (`"ioc(test): bench harness"`).

### Task 2: Scenarios as data and the real-time runner

**Files:** Create `ioc/test/scenarios.py`, `ioc/test/test_scenarios.py`.

- [ ] Step 1: Port every `SCENARIOS` entry (reference lines 1606-1700) to data:
  `{'n': 1, 'dur': 3600, 'setup': [...], 'events': [(t, action, arg), ...], 'state': 'REGULATE',
  'has': [...], 'check': None}`. Actions: `purge`, `flow_zero`, `resume_flow` (reference opResume),
  `target v`, `mode A|B`, `world <PV suffix> <value>` (LiftLid, CloseLid, CrackLid, LidType, Hold,
  ClearHold, SetRamp, CylPressure, AnalyzerMode, Preset), `new_cylinder`, `mark_new_run`, `crash`,
  `restart`. `presetRegulating` → setup `preset` (plant `World:Preset` at the mode's expected flow,
  then `restart_ioc`). Scenario 14's crash/restart are real IOC kill (at 300 s) and restart (at
  900 s). `has` patterns from spec §14.2 (`operator pressed Resume Flow`, not `Resume PID`).
  Scenarios 17 and 19 are marked `replay_only` (covered by Plan 1's replay; spec §14.4).
- [ ] Step 2: `test_scenarios.py`: one test method per scenario number; selection by env var
  `SG_SCENARIOS=1,2,3` (default: all real-time ones); each run writes
  `results/sc<NN>-<timestamp>.json` (end state, log lines, pass/fail, problems) and then asserts.
  Events are scheduled on wall-clock seconds from the scenario start (the first IOC tick after
  setup).
- [ ] Step 3: Run the short ones first (2, 3, 8, 9, 12, 13, 14: about 2.5 h) in the background;
  debug failures (systematic-debugging skill: reproduce, isolate, root-cause, fix with a test).
- [ ] Step 4: Run the long ones (1, 4, 5, 6, 7, 10, 11, 15, 16, 18) unattended; debug.
- [ ] Step 5: Commit the harness and every fix separately.

### Task 3: Quantitative comparison with the reference (noise off)

**Files:** Modify `ioc/test/ref/make_traces.js` (`--no-noise`: `pp.noise = 0`, `pp.wanderRel = 0`,
output `golden/nonoise/`); create `ioc/test/test_compare.py`.

- [ ] Step 1: For scenarios 1, 2, 3, 8 with the plant simulator `--no-noise`, record the IOC's state
  transitions (from the log) and flows at transitions; extract the same from the noise-free traces.
- [ ] Step 2: Assert transition times within ±3 s, flows within ±0.02 SLPM, and the lid-check ratio
  within ±0.05 (spec §14.2). Debug any excess (typical causes: tick alignment, readback latency of
  the 1 Hz poll, ramp handling).
- [ ] Step 3: Commit.

### Task 4: Restart, PV-name and write-enable tests

**Files:** Create `ioc/test/test_restart.py`, `ioc/test/test_pvnames.py`,
`ioc/test/test_write_enable.py`.

- [ ] Restart (§14.3): regulate, kill the IOC, wait 5 min (plant keeps running, flow held), restart:
  REGULATE resumed, first PID step moves the setpoint by ≤ 0.02 SLPM, parameters and helium state
  restored (compare a parameter changed before the kill, and `He:CumL`), the heartbeat stopped
  while down (a client-side staleness check: no change for > 10 s).
- [ ] PV names (§14.3a): with `second_plant=True`, the steps listed in the spec (reject in
  REGULATE; `SIM:O2b`; `SIM:Alicat2:` disables writes and re-baselines the ledger without a `CumL`
  jump; empty O2 → not configured; Restore defaults; names survive an IOC restart).
- [ ] Write enable (§14.3b + §14.5 shadow): the steps listed in the spec, with a `watch` on every
  `SIM:Alicat1:` writable PV proving no put reaches it while `writeEnable` = 0, including across an
  IOC restart into REGULATE.
- [ ] Debug failures; commit each fix separately.

### Task 5: Debugging sweep and report

- [ ] Run the whole suite once more end to end (unattended, background).
- [ ] Collect: pass/fail per test, open defects with root causes, and anything the tests cannot
  reach (hardware-only items from spec §15.3). Write `ioc/test/ACCEPTANCE.md` with the results and
  the date. Commit.
