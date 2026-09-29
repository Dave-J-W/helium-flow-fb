# 15LSS_sample_gas IOC, Plan 3: PV-level plant simulator for the bench

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `ioc/test/plant_sim.py`, a caproto server that plays the enclosure, the O2 analyzer and
the Alicat on localhost, with the physics of the simulator's `Plant` class and the PV semantics of
`Alicat_BC.db`, so the real IOC can be tested on the bench without hardware.

**Architecture:** A pure-Python `Plant` model (`ioc/test/plantsim/plant.py`), a line-for-line port of
the reference `Plant` class, validated open-loop against the reference JavaScript with noise off.
A caproto `PVGroup` wraps one or more plants and serves the Alicat PVs, the O2 PV and `World:`
control PVs, stepping the physics at 4 Hz and publishing readbacks at 1 Hz aligned to wall-clock
seconds. The server listens on 127.0.0.1 only, on its own CA server port (5066), because two CA
servers on one Windows host sharing port 5064 do not both receive unicast name searches.

**Tech Stack:** Python 3.12 in `%USERPROFILE%\.venvs\bluesky` (caproto 1.3.0, pyepics 3.5.10,
numpy 2.5; **no pytest**: tests use stdlib `unittest`), Node.js in MSYS2 for the reference export.

**Spec:** `docs/ioc/15LSS_sample_gas_IOC_spec.md` §14.1 (plant simulator), §14.3a (second O2 PV
and Alicat), §6.1-§6.2 (PV semantics), §2.1 (bench confinement). Reference: `Plant` in
`simulator/sample_gas_simulator.html` (lines 881-1024; `defaultPlantParams` 881-903,
`presetRegulating` 1585-1592).

## Global Constraints

- Bench confinement (spec §2.1): serve on 127.0.0.1 only (`EPICS_CAS_INTF_ADDR_LIST=127.0.0.1`,
  beacons to 127.0.0.1, no auto beacon list). PV prefixes never start with `15ID`: defaults
  `SIM:Alicat1:`, `SIM:O2`, `SIM:World:`; the second plant `SIM:Alicat2:`, `SIM:O2b`, `SIM:World2:`.
- Never put to any PV outside the served set; the simulator is a server only.
- Python: `%USERPROFILE%\.venvs\bluesky\Scripts\python.exe` (absolute path; `python` on PATH is a
  broken stub). Do not install into Anaconda. No new packages.
- Never write code through shell heredocs.
- Commits: repo-local identity; messages end with `Co-Authored-By: Claude Opus 5.5
  <noreply@anthropic.com>`; stage explicit paths only. Branch `ioc-plan3` in the
  `o2-purge-plan3` worktree next to the main checkout. Do not push.
- No site paths, account names or IPs other than 127.0.0.1 in committed files.

## File map

```
ioc/test/
  plantsim/__init__.py
  plantsim/plant.py            Plant model: pure Python port of the reference Plant (no CA)
  plant_sim.py                 caproto server (CLI): one or more plants, Alicat/O2/World PVs
  ref/load_reference.js        extracts the reference sim core from the HTML (node helper)
  ref/plant_openloop.js        drives the reference Plant open loop, writes JSON (noise off)
  golden/                      (gitignored) plant_openloop.json
  test_plant_model.py          unittest: model vs reference, and Alicat semantics
  test_plant_server.py         unittest: server smoke test over Channel Access
  README.md                    how to run the simulator and the tests
ioc/tools/
  bench_env.sh                 sources ~/epics-sim-env.sh, then sets the two-server CA lists
  bench_env.ps1                the same for PowerShell sessions (pyepics clients)
```

---

### Task 1: Plant model, validated against the reference

**Files:** Create `ioc/test/plantsim/__init__.py`, `ioc/test/plantsim/plant.py`,
`ioc/test/ref/load_reference.js`, `ioc/test/ref/plant_openloop.js`, `ioc/test/test_plant_model.py`
(model-vs-reference part).

**Interfaces (produced):**
```python
# ioc/test/plantsim/plant.py
DT = 0.25                          # physics step, s
def default_plant_params() -> dict # every key of defaultPlantParams(), same names and values
class Plant:
    def __init__(self, pp: dict | None = None, seed: int | None = None, noise: bool = True): ...
    t: float                       # simulated time, s (the server sets it from its own clock)
    C: float                       # true bulk O2, %
    lid: str                       # 'closed' | 'open'
    lid_type: str                  # 'A' | 'B'
    crack: bool; seal: float
    a: dict                        # Alicat state: sp_val, sp_dev, sp_ramped, flow, running, stuck,
                                   #   hold_flow, ramp, gas, total  (JS a.spVal → a['sp_val'] etc.)
    cyl_p: float
    an: dict                       # analyzer: mode ('normal'|'invalid'|'frozen'), value, sevr
    rbv: dict                      # polled readbacks: flow, sp, running, ramp, gas, units, total
    def reset(self) -> None
    def ambient(self) -> float
    def step(self, dt: float = DT) -> None
    def sample_analyzer(self) -> None      # 1 Hz
    def poll_alicat(self) -> None          # 1 Hz
    def put_setpoint(self, v: float) -> bool
    def put_run(self) -> bool
    def put_ramp(self, v: float) -> None
    def hold(self, kind: str) -> None      # 'current' | 'open' | 'stuck'
    def clear_hold(self) -> None
    def lift_lid(self) -> None; def close_lid(self) -> None; def crack_lid(self) -> None
    def reseat(self) -> None
    def breath_dip(self) -> None           # ambient -0.6 % for 60 s (dipUntil = t + 60)
    def steady_at(self, F: float) -> float
    def set_o2_everywhere(self, C: float) -> None
    def preset(self, F: float) -> None     # presetRegulating's plant part: O2 steady at F,
                                           #   Alicat running at F (sp_val = sp_dev = sp_ramped =
                                           #   flow = F), dEff = delayMax, poll_alicat()
```
Noise: `noise=False` makes both `noise` (white) and the slow `wander` component zero, so the
model is deterministic. Randomness uses `random.Random(seed)` with the reference's Box-Muller
`gauss()` (cache the second value exactly as the reference does).

- [ ] **Step 1: Reference loader and open-loop exporter**

`load_reference.js`: `module.exports = function loadReference(htmlPath) { ... }`: read the HTML,
slice from the line starting `const clamp = ` to before the line containing `//  Charts`, evaluate
with `vm.runInThisContext` inside a function wrapper returning `{ Plant, defaultPlantParams, DT,
STEPS_PER_S }`. Fail loudly if a marker is missing. (Same slicing rule as
`ioc/test/ref/make_traces.js` on the other branch; kept separate so the two plans do not touch the
same file.)

`plant_openloop.js --html <path> --out <file>`: for each case below, build a `Plant` with
`pp.noise = 0` and `pp.wanderRel = 0`, apply the setup, then for each second t = 1..N: apply the
case's actions for that second (before stepping), step 4 × `DT` (setting `plant.t` before each
step, as `Station.step` does), then `sampleAnalyzer()` and `pollAlicat()`, and record
`{t, o2: an.value, sevr: an.sevr, C, flow: rbv.flow, sp: rbv.sp, total: rbv.total, running}`.
Cases (N in seconds):
1. `purge_from_air` (900): lid closed, `putSetpoint(20)` at t = 5.
2. `hold_normal_lid` (3600): `preset(0.25)` equivalent (steadyAt + Alicat state, as
   `presetRegulating` does, with F = 0.25), nothing else.
3. `lid_lift` (600): preset 0.25; `liftLid()` at t = 120; `closeLid()` at t = 300.
4. `collimator` (1800): `lidType = 'B'`; preset 0.84; `putSetpoint(1.2)` at t = 600.
5. `alicat_semantics` (400): preset 0.29; `hold('open')` at 60; `putSetpoint(0.5)` at 70 (must not
   reach the device); `putRun()` at 90; `putSetpoint(0.5)` at 120; `putRamp(0)` at 200;
   `putSetpoint(2)` at 210; `cylP = 140` at 300; `putSetpoint(20)` at 310.
6. `analyzer_modes` (200): preset 0.25; `an.mode = 'invalid'` at 50; `'normal'` at 100; `'frozen'`
   at 150.
Write `{case: [records...]}` as JSON.

- [ ] **Step 2: Write the failing test** (`test_plant_model.py`, class `TestAgainstReference`)

```python
import json, math, os, subprocess, unittest
from plantsim.plant import Plant, default_plant_params, DT

HERE = os.path.dirname(os.path.abspath(__file__))
GOLDEN = os.path.join(HERE, 'golden', 'plant_openloop.json')

def run_case(name, n):
    """Replays the same case as plant_openloop.js in Python; returns the records."""
    # setup and actions exactly as listed in Step 1 (write them as a dict: second -> callable)
    ...

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

if __name__ == '__main__':
    unittest.main()
```
The spec's pass criterion is 0.5 % relative; with identical arithmetic the worst difference is
expected near 1e-12, so print it and report it.

`ioc/tools/plant_ref.sh` (MSYS2, via `ioc/tools/msys.ps1` from Plan 1; if that file is not on this
branch, add the same 5-line `msys.ps1`):
```bash
#!/bin/bash
set -e
cd "$(dirname "$0")/.."
mkdir -p test/golden
node test/ref/plant_openloop.js --html ../simulator/sample_gas_simulator.html --out test/golden/plant_openloop.json
```
`ioc/test/golden/.gitignore`: `*` and `!.gitignore` (if not already present on this branch).

- [ ] **Step 3: Run; expect import failure**

Run (PowerShell, repo root): `powershell -ExecutionPolicy Bypass -File ioc/tools/msys.ps1 ioc/tools/plant_ref.sh`
then `& "$env:USERPROFILE\.venvs\bluesky\Scripts\python.exe" -m unittest -v ioc/test/test_plant_model.py`
(run from `ioc/test` so `plantsim` imports: `cd ioc/test; python -m unittest -v test_plant_model`).
Expected: ImportError / failures.

- [ ] **Step 4: Port `Plant`** (lines 908-1024) to `plant.py`, plus `default_plant_params`
  (881-903), `clamp`, and `preset` (the plant half of `presetRegulating`, 1585-1592, given the flow).
  Keep the operation order of every expression (floating-point equality with the reference depends
  on it). `Math.round(x)` in JS rounds half up (toward +∞): use `math.floor(x + 0.5)`, not Python's
  banker's `round`. The history buffer is a fixed-length list (`ceil(200 / DT)` entries) with a
  write index, as in the reference.

- [ ] **Step 5: Run the tests; expect 6/6 PASS, and report the worst differences**

- [ ] **Step 6: Commit**
```bash
git add ioc/test/plantsim ioc/test/ref/load_reference.js ioc/test/ref/plant_openloop.js ioc/test/test_plant_model.py ioc/tools/plant_ref.sh ioc/test/golden/.gitignore
git commit -F <msgfile>   # "ioc(test): plant model ported from the reference, validated open loop"
```

---

### Task 2: Alicat and analyzer semantics tests (no reference needed)

**Files:** Modify `ioc/test/test_plant_model.py` (add class `TestSemantics`).

- [ ] **Step 1: Write the tests** (all with `noise=False`, preset 0.29, then stepping whole
  seconds with a helper `run(plant, seconds)` that steps 4 × DT per second and polls at the end):
  1. Setpoint while on hold (`hold('current')`): `put_setpoint(0.8)` returns False, `a['sp_val']`
     is 0.8, `a['sp_dev']` unchanged, `rbv['sp']` unchanged after 5 s.
  2. `put_run()` after a non-stuck hold: running again, device resumes its **old** setpoint
     (`sp_dev` unchanged by the held put), `rbv['running']` True after the next poll.
  3. `hold('stuck')`: `put_run()` returns False and the valve stays held; `clear_hold()` resumes.
  4. `hold('open')`: flow goes to `holdOpenFlow` (0.7) within 5 s.
  5. Ramp: `put_ramp(3)`, `put_setpoint(20)` from 0.29: after 1 s flow ≤ 0.29 + 3 + small τ
     margin; after 10 s flow ≥ 19.9. `put_ramp(0)`: instant (after 3 s within 0.05 of target).
  6. Quantisation: `put_setpoint(0.2937)` → `a['sp_val'] == 0.29`; `rbv['flow']` is always a
     multiple of 0.01 (check `abs(v*100 - round(v*100)) < 1e-9` over 60 s).
  7. Cylinder: `cyl_p = 140` with setpoint 20: flow limited to
     `alicatMax * (140-30)/(150-30)` ≈ 18.3 and falling as pressure drops; `cyl_p = 0` → flow 0.
  8. Analyzer: mode `'invalid'` → `sevr == 3`, value unchanged; `'frozen'` → value identical on
     consecutive samples while the bulk changes; back to `'normal'` → `sevr == 0`.
  9. Totalizer: `rbv['total']` increases by ≈ flow × t / 60 (within 1 %) over 600 s at 0.5 SLPM.
  10. Lid lift at hold flow: the reading exceeds 10 % within 30 s of `lift_lid()` (the reference
      lid-lift time constant is 5.75 s plus the open-lid 2 s delay).

- [ ] **Step 2: Run; they should pass immediately** if Task 1's port is faithful (these encode
  documented behaviour, spec §6.1 and §14.1). A failure is a port bug or a misreading of the spec:
  investigate before changing a test.

- [ ] **Step 3: Commit** (`"ioc(test): Alicat hold/run/ramp/cylinder and analyzer semantics tests"`).

---

### Task 3: caproto server and bench CA configuration

**Files:** Create `ioc/test/plant_sim.py`, `ioc/tools/bench_env.sh`, `ioc/tools/bench_env.ps1`,
`ioc/test/test_plant_server.py`.

**Interfaces (produced):**
- CLI: `python plant_sim.py [--plant ALICAT_PREFIX,O2_PV,WORLD_PREFIX ...] [--seed N] [--no-noise]
  [--port 5066]`. Default one plant: `SIM:Alicat1:,SIM:O2,SIM:World:`. `--second` adds
  `SIM:Alicat2:,SIM:O2b,SIM:World2:`.
- The server refuses to start (exit 2, message) unless `EPICS_CAS_INTF_ADDR_LIST` is `127.0.0.1`
  (it sets it itself if unset) and no prefix starts with `15ID`.

**PVs per plant** (names are the Alicat_BC.db names, spec §6.1):

| PV | Type | Behaviour |
|---|---|---|
| `<A>Setpoint` | float, PREC 3, EGU SLPM | put → `put_setpoint(v)`; the PV value always becomes `sp_val` (the record's VAL changes even on hold; the device does not) |
| `<A>Setpoint_RBV` | float, read-only | `rbv['sp']`, 1 Hz |
| `<A>Flow_RBV` | float, read-only, PREC 2 | `rbv['flow']`, 1 Hz |
| `<A>Total_RBV` | float, read-only | `rbv['total']`, 1 Hz |
| `<A>Running_RBV` | enum `['Paused', 'Running']`, read-only | `rbv['running']`, 1 Hz |
| `<A>Status` | string, read-only | `'HLD'` when not running, else `''` |
| `<A>RampRate` | float | put → `put_ramp(v)` |
| `<A>RampRate_RBV` | float, read-only | `rbv['ramp']` |
| `<A>Run` | int | put (any value) → `put_run()` |
| `<A>Gas_RBV` | enum, 16 Alicat gas states, index 7 = `He` | `rbv['gas']` |
| `<A>FlowUnits_RBV` | string | `'SLPM'` |
| `<O2>` | float, PREC 4, EGU % | `an['value']` at 1 Hz; alarm severity INVALID (status UDF) when `an['sevr'] == 3`; in frozen mode the value is re-posted unchanged |
| `<W>LiftLid`, `CloseLid`, `CrackLid`, `Reseat`, `ClearHold`, `BreathDip` | int, put 1 → action | |
| `<W>LidType` | enum `['A', 'B']` | sets `lid_type` |
| `<W>Hold` | enum `['current', 'open', 'stuck']` | put → `hold(kind)` |
| `<W>SetRamp` | float | put → `put_ramp(v)` (a world change of the device ramp) |
| `<W>Gas` | enum (same states) | sets `a['gas']` |
| `<W>CylPressure` | float | put sets `cyl_p`; reads back the current value at 1 Hz |
| `<W>AnalyzerMode` | enum `['normal', 'invalid', 'frozen']` | sets `an['mode']` |
| `<W>Preset` | float | put F → `preset(F)` |
| `<W>Seed` | int | put → reseed the RNG |
| `<W>Noise` | enum `['Off', 'On']` | noise switch |
| `<W>Bulk` | float, read-only | true bulk O2 `C` (for test diagnostics only) |
| `<W>Time` | float, read-only | plant time, s |

**Timing:** one asyncio task per server: at each 0.25 s tick (scheduled against
`time.monotonic()`, drift-compensated: next = start + k × 0.25), set each plant's `t` and `step()`.
On the tick that completes the 4th physics step of each group (matching the reference's own
cadence, `Station.step`, html 1534-1541: sample after step count 4, 8, 12, ..., not 1, 5, 9):
`sample_analyzer()`, `poll_alicat()` and write every readback PV (caproto `write` on the
pvproperty, which posts monitors). That publish tick is aligned to whole wall-clock second +
`--phase` seconds (default 0.75), **not** to the whole second itself -- publishing on the same
instant an IOC's own 1 Hz tick samples its inputs (spec Sec 8.1) would make the IOC randomly see
this second's or the previous second's readback (jitter ~15 ms + CA latency); the phase offset
gives it a fixed margin instead.

- [ ] **Step 1: bench CA configuration**

`ioc/tools/bench_env.sh`:
```bash
# Source after nothing else: confines CA to this PC and lets clients see both bench servers.
# The plant simulator (caproto) serves on port 5066; the IOC on the default 5064. Two CA servers on
# one Windows host cannot share 5064 for unicast name searches, so they use separate ports.
source ~/epics-sim-env.sh
export EPICS_CA_ADDR_LIST="127.0.0.1:5064 127.0.0.1:5066"
export EPICS_CA_AUTO_ADDR_LIST=NO
```
`ioc/tools/bench_env.ps1`: the same variables for PowerShell (including every variable
`~/epics-sim-env.sh` sets: read it and copy the names and values).

- [ ] **Step 2: Write the server test** (`test_plant_server.py`): `setUpClass` starts
  `plant_sim.py --no-noise --port 5066` as a subprocess with the bench env applied to
  `os.environ` (from a dict in the test mirroring `bench_env.ps1`), waits until `SIM:O2` connects
  (pyepics `epics.PV(...).wait_for_connection(10)`), and `tearDownClass` terminates it. Tests:
  1. `SIM:Alicat1:Flow_RBV` updates about once a second (count monitor callbacks over 5 s: 4-6).
  2. `caput SIM:World:Preset 0.29` then after 3 s `Flow_RBV` ≈ 0.29 and `SIM:O2` ≈
     `steady_at(0.29)` (within 5 %).
  3. `caput SIM:World:Hold current` → `Running_RBV` = 0 and `Status` = `HLD` within 2 s; a put to
     `Setpoint` 0.8 leaves `Setpoint_RBV` unchanged; `caput Run 1` → running, `Setpoint_RBV`
     still the old value.
  4. `caput SIM:World:AnalyzerMode invalid` → `SIM:O2` severity 3 within 2 s.
  5. The server's TCP listener is bound to 127.0.0.1 only: check with
     `psutil`-free means, e.g. `subprocess.run(['netstat','-ano'])` output lines containing the
     child's PID show only `127.0.0.1:` local addresses.

- [ ] **Step 3: Run; expect failure (no server yet).** `cd ioc/test; python -m unittest -v test_plant_server`

- [ ] **Step 4: Implement `plant_sim.py`** with caproto's `PVGroup`/`pvproperty` and
  `caproto.server.run` (asyncio). Build one `PVGroup` subclass per plant dynamically, or one class
  with `SubGroup`s per plant; either way, names exactly as in the table. Use `ChannelType.ENUM`
  with `enum_strings` for the enum PVs. Read-only PVs: `read_only=True`. Severity: write the O2 value
  with `alarm_status`/`severity` via `await pv.write(value, status=AlarmStatus.UDF,
  severity=AlarmSeverity.INVALID_ALARM)` in invalid mode and `NO_ALARM` otherwise.

- [ ] **Step 5: Run the server test: 5/5 PASS. Also rerun `test_plant_model`.**

- [ ] **Step 6: Commit** (`"ioc(test): caproto plant simulator on localhost:5066, bench CA env"`).

---

### Task 4: Usage documentation

**Files:** Create `ioc/test/README.md`.

- [ ] **Step 1: Write it**: how to start the simulator (PowerShell and MSYS2 forms, with the bench
  env), the PV table (copy from Task 3), the `World:` controls with examples (`caput
  SIM:World:LiftLid 1`), the port-5066 reason, how to regenerate the reference and run both test
  files, and the rule that the simulator is a server only and never runs with a `15ID` prefix.
- [ ] **Step 2: Commit** (`"ioc(test): plant simulator README"`).

## Self-review notes

- Spec §14.1 coverage: Alicat_BC.db semantics (Task 2 + 3), analyzer modes (Task 2, 3), physics
  (Task 1), world controls incl. Seed and noise switch (Task 3), validation against the reference
  (Task 1, noise off, 0.5 % criterion). §14.3a: second O2 PV and Alicat (`--second`).
- The IOC side (Plan 2) must use `bench_env.sh` so its CA client searches both ports.
