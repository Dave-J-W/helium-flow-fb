# Helium purge with O2 feedback: design

Date: 2026-09-24
Status: design under review. No code has been written. No PV has been created or written.

## 1. Purpose

Replace the timer-driven helium purge (a Python script run inside a Phoebus display) with an
IOC-resident controller that:

1. Purges the sample enclosure at full flow until O2 is safely below target.
2. Hands off to a PID loop that holds O2 at an operator-selected target on the least helium.
3. Keeps helium flowing through almost every fault. Zero flow is reserved for a confirmed
   open enclosure.

The controller runs in an IOC, so it keeps running when no Phoebus screen is open. Phoebus
becomes a panel on top of it.

Evidence tags used below:
- **VERIFIED**: read from source or documentation.
- **DERIVED**: follows from reasoning.
- **PROVISIONAL**: a starting value to be tuned on the hardware.

## 2. Hardware and existing software

| Item | Facts |
|---|---|
| MFC | Alicat BASIS, EPICS support `epics-modules/ip` `ipApp/Db/Alicat_BC.db` + `.proto` (VERIFIED). Max purge flow 20 SLPM, resolution ~0.01 SLPM. Alicat ramp rate normally 3 SLPM/s. |
| O2 analyzer | Updates at ~1 Hz. Stable to ~0.01 % O2 over 10 min. ~2 min before a slope change is confidently visible (slower at maintenance flows; a purge-start drop is visible after ~1 min). |
| Enclosure modes | Same volume, different leak rates. Mode A needs ~0.25 SLPM, mode B ~0.8 SLPM, to hold ~0.99 % O2. |
| Surface limit | Flow above ~2 SLPM may vibrate the liquid surface and prevent X-ray measurement. The exact PID ceiling is to be tested. |
| Ambient O2 | ~19.4 % (PROVISIONAL, local reading). |

Live PV names (from the user, 2026-09-24):

| Signal | PV | Notes |
|---|---|---|
| O2 level | `15IDC:D1Dmm_calc.VAL` | A calc record, apparently the analyzer's output read through a DMM channel |
| MFC | `15IDC:Alicat1:` | So `$(P)$(R)` = `15IDC:Alicat1:`. Writes go to `15IDC:Alicat1:Setpoint`; `Setpoint_RBV` is the readback. |

The user owns the Alicat IOC and approves program writes to it. **Until further notice, no
tool may read-modify or write these PVs**: an experiment is running. All development and
testing happen against a simulator on non-colliding PV names (section 8).

Facts from `Alicat_BC.db` that the design depends on (VERIFIED):
- `$(P)$(R)Setpoint` is disabled while `Running_RBV = 0` (`SDIS`/`DISV`). While the controller
  is on hold (HLD/EXH), setpoint writes are silently dropped.
- `Flow_RBV`, `Setpoint_RBV`, `Valve_RBV`, `Gas_RBV` and `Status` update at 1 Hz from one poll.
- `RampRate` is written as `SR <value> 4`, and `RampRate_RBV` is readable.
- `Run` sends `C`, which resumes from hold.
- `FlowUnits_RBV` reports the units string.

Writes this design makes to **existing** PVs. Both need sign-off from the owner of the Alicat IOC:
- `$(P)$(R)Setpoint`: purge, handoff, PID output and zero flow.
- `$(P)$(R)RampRate`: clamped only when out of range (section 5.3).
- `$(P)$(R)Run`: resume from hold.

Nothing else existing is modified.

## 3. Architecture

```
           operator panel (Phoebus .bob) - optional, loop runs without it
                              |
   +--------------------------+-----------------------------------+
   |  new IOC records, prefix $(PP) (pending approval)            |
   |                                                              |
   |  config  --->  SNL sequencer (state machine, watchdog)  ---+ |
   |  (mode,          |        ^                             | | |
   |   target,        | FBON   | O2 avg, flow, status        | | |
   |   params)        v        |                             | | |
   |               epid  <-- compress (10 x 1 Hz mean) <-- O2 | | |
   |                 | OUTL                                   | | |
   |  alarms/status calcs (O2 high, flow high, pinned)        | | |
   +-----------------|----------------------------------------|-+
                     v                                        v
          Alicat $(P)$(R)Setpoint                  Alicat RampRate / Run
```

| Module | Implementation | Responsibility |
|---|---|---|
| Configuration | ao/mbbo records, autosaved | Mode, target, tolerance, Δ, per-mode gains, limits, thresholds |
| Averaging | base `compress` record, N-to-1 mean, N=10 | Turns the 1 Hz O2 reading into the 0.1 Hz input for `epid` |
| PID | synApps std `epid` record | Regulation. `FBON` is driven only by the sequencer. |
| Sequencer | SNL program (seq module) | States, transitions, pre-checks, overrides, watchdog |
| Status/alarms | calc/calcout records with HIGH/HIHI | O2 high, flow high, PID pinned at its limit, last action, override counter |
| Persistence | autosave | Parameters survive IOC reboot |

Keeping the watchdog in the sequencer keeps every timing rule in one readable file.

## 4. States

```
            +---------------- operator: Purge ------------------------+
            v                                                         |
  IDLE -> PRECHECK -> PURGE ---- O2 < target-Δ held 15 s ----> HANDOFF -> REGULATE
                        |  \                                             |   |
                        |   '-- 6.5 min elapsed --> HANDOFF (warn) ------'   |
                        |                                                    |
                        '-- no drop at 60 s --> OPEN_STOP (zero)             |
                                                     ^                       |
      any O2-valid state: O2 > 10 % & fast rise -----'                       |
      operator: Flow Zero --> FLOW_ZERO (zero)                               |
      O2 invalid/frozen --> OPEN_LOOP (fixed flow) -- operator: Resume PID ->'
```

| State | Flow | `epid` FBON |
|---|---|---|
| IDLE | unchanged (sequencer does not write) | 0 |
| PRECHECK | unchanged | 0 |
| PURGE | 20 SLPM | 0 |
| HANDOFF | expected flow for the mode, then FBON=1 | 0 → 1 |
| REGULATE | PID output, within `DRVL`..`DRVH` | 1 |
| OPEN_LOOP | expected flow for the mode, fixed | 0 |
| FLOW_ZERO | 0 (operator request) | 0 |
| OPEN_STOP | 0 (enclosure confirmed open) | 0 |

### 4.1 PRECHECK: correct and warn, never refuse

A purge must not fail because of a bad configuration. Each check either corrects the problem
or warns, then the purge proceeds.

| Check | Action |
|---|---|
| `Running_RBV = 0` (hold) | Write `Run`, wait 3 s, re-check. If still on hold: MAJOR alarm, but stay in PURGE so that flow starts the moment hold clears. |
| `RampRate_RBV` > 5 or = 0 | Write 5 to `RampRate`. MINOR alarm, log. |
| `Gas_RBV` ≠ He | MINOR alarm only. Do not change the gas table. |
| `FlowUnits_RBV` not SLPM | MAJOR alarm. The ramp and flow numbers would be in the wrong units. The purge still proceeds. |
| O2 invalid or frozen | Proceed in blind purge mode (section 4.3). |

Treating 0 as "fastest" assumes the Alicat's 0 means ramping is off. **Confirm this in the
BASIS manual.**

### 4.2 PURGE

1. Record the O2 reading at the start, `O2_start`.
2. Write 20 SLPM.
3. Start the purge timer when `Flow_RBV` first reaches 95 % of 20 SLPM. At 3 SLPM/s that takes
   about 6.5 s.
4. **Drop check at 60 s** (on the purge timer):
   - Skip it if `O2_start` < 18 %. O2 that low proves the enclosure was closed.
   - Otherwise require `O2_start − O2_now` ≥ `MinDrop`. Default `MinDrop` = 1.0 % O2
     (PROVISIONAL; tune from logged purges).
   - If the check fails: **OPEN_STOP**.
5. **Early handoff**: when O2 < target − Δ continuously for 15 s, go to HANDOFF.
   Δ defaults to 0.15 %.
6. **Timeout**: at 6.5 min, go to HANDOFF anyway and raise a MINOR "purge incomplete" alarm.

### 4.3 Blind purge (O2 unavailable)

Run 20 SLPM for the full 6.5 min, with no drop check and no early handoff. Then go to
OPEN_LOOP. This reproduces today's timer behaviour.

### 4.4 HANDOFF

1. If `RampRate_RBV` < 2 and ≠ 0, write 2. MINOR alarm, log.
2. Write the mode's expected flow to `Setpoint`.
3. Wait until `Flow_RBV` is within tolerance of it.
4. Set `FBON = 1`.

`epid` initialises its integral from `OUTL` when `FBON` goes 0→1, so the loop starts at the
expected flow instead of 20 SLPM (VERIFIED, `devEpidSoft.c`).

### 4.5 REGULATE

`epid` runs every 10 s on the 10-sample O2 mean. The sequencer watches alarms and the
lid-open condition. There is **no automatic re-purge**: a re-purge happens only when an
operator presses Purge. Headroom for upsets comes from the tested PID ceiling (section 6).

### 4.6 Lid-open detection

- **Trigger**: (O2 > 10 % for 5 consecutive seconds) **AND** (rise rate ≥ `LidSlope` at any
  point in the trailing 60 s). It applies in any state where O2 is valid and the sequencer is
  controlling flow.
  - The 5 s filter rejects single-sample glitches.
  - An opened lid raises O2 faster than any other process, so the rate term rejects slow
    drifts that happen to cross 10 %.
  - Rise rate = (O2 now − O2 10 s ago) / 10 s, computed at 1 Hz.
  - The rate is taken as a **trailing-60 s maximum** because O2 plateaus near ambient after
    the lid opens. The instantaneous rate may already be ~0 by the time the 5 s threshold
    filter completes.
  - `LidSlope` default: 0.2 % O2/s (PROVISIONAL; set from the deliberate lid-open test,
    section 8).
- **Consequence of the AND:** a lid only cracked open, leaking slowly, will not trip this
  detector. Instead the PID rises to `DRVH`, and the "flow too high / PID pinned" MAJOR alarm
  fires. Helium use in that case is bounded by `DRVH` (≤ 2 SLPM), not 20 SLPM.
- **During PURGE**, the detector arms only after O2 has first fallen below 9 %. Otherwise a
  purge starting from air would trip it immediately.
- **Action**: OPEN_STOP.

This covers the case where the operator forgets Flow Zero before opening the enclosure.

### 4.7 IOC restart

The Alicat holds its last setpoint on its own, so helium keeps flowing if the IOC dies
(DERIVED from the device being a standalone controller). On restart:

| Condition | Result |
|---|---|
| O2 valid, < 10 %, and `Setpoint_RBV` > 0 | REGULATE. `epid` picks up from the current setpoint without a bump. |
| Setpoint is 0 | IDLE |
| O2 invalid | OPEN_LOOP if setpoint > 0, otherwise IDLE |

## 5. End-state table (living; add to it as the design evolves)

| Situation | Flow | Alarm | Recovery |
|---|---|---|---|
| No O2 drop at 60 s (confirmed open enclosure) | **0** | MAJOR "no O2 drop, enclosure open?" | Operator closes enclosure, presses Purge |
| O2 > 10 % for 5 s AND fast rise (lid opened without Flow Zero) | **0** | MAJOR "enclosure opened, flow stopped" | Operator closes enclosure, presses Purge |
| Operator Flow Zero | **0** | none | Operator presses Purge |
| Target − Δ not reached at 6.5 min | PID (via HANDOFF) | MINOR "purge incomplete". The flow-high alarm will likely follow. | Automatic |
| O2 analyzer invalid or frozen | Expected flow, fixed (OPEN_LOOP) | MAJOR "O2 lost, running blind" | Operator presses Resume PID once O2 returns |
| MFC on hold and `Run` did not clear it | none possible | MAJOR "MFC on hold" | Operator |
| Ramp rate out of range | unaffected (clamped) | MINOR, logged | Automatic |
| Gas table ≠ He | unaffected | MINOR | Operator, optional |
| Flow ≥ 1.5× expected | PID | MINOR "flow high, check enclosure" | Automatic |
| Flow ≥ 2× expected, or PID pinned at `DRVH` for 10 min | PID | MAJOR "flow too high, check enclosure seal" | Operator |
| Flow not matching setpoint (see 5.1) | PID continues | MAJOR "flow mismatch: cylinder empty or MFC fault?" | Operator |

Zero flow happens only for a confirmed open enclosure or an explicit operator request.

### 5.1 Flow-mismatch rule

When the setpoint changes, compute:

```
allowed_s = |new_setpoint − Flow_RBV| / RampRate_RBV + 5 s
```

(If `RampRate_RBV` = 0, use 5 s.)

Alarm if `|Setpoint_RBV − Flow_RBV|` > max(0.05 SLPM, 5 % of setpoint) is still true after
`allowed_s`. The tolerance is PROVISIONAL.

### 5.2 Expected flow

Model (DERIVED): O2 ≈ 20.9·L/F, where L is the air leak rate and F the helium flow. So:

```
expected_flow = mode_base_flow × (0.99 / target)
```

`mode_base_flow` is 0.25 SLPM (A) or 0.8 SLPM (B), both measured near 0.99 %.

Uses of the expected flow: the HANDOFF flow, the OPEN_LOOP flow and the flow-alarm thresholds.

### 5.3 Ramp-rate overrides

| When | Condition | Write |
|---|---|---|
| PRECHECK | `RampRate_RBV` > 5 or = 0 | 5 |
| HANDOFF | 0 < `RampRate_RBV` < 2 | 2 |

Both overrides are logged to `$(PP)LastAction` and increment `$(PP)OverrideCount`.

## 6. PID configuration (from the tuning analysis)

- **Error sign.** `epid` error = `VAL − CVAL` (VERIFIED). Too much O2 gives a negative error,
  which must raise flow, so **`KP` < 0**. `KI` > 0. `KD` = 0.
- **Units.** `KI` is in repeats/s, i.e. 1/τI (VERIFIED).
- **Scan and deadband.** `SCAN` = 10 s. `ODEL` = 0.01 SLPM suppresses writes below the MFC's
  resolution. `epid` keeps accumulating the integral internally (VERIFIED). There is no error
  deadband: the 0.01 % sensor noise × `KP` is already below the MFC's resolution (DERIVED).
- **Limits.** `DRVL` = max(0.1, 0.5 × expected flow). `DRVH` = mode ceiling, **hard maximum
  2.0 SLPM**, to be raised or lowered by the surface-vibration test.
- **Starting gains (PROVISIONAL).** `KP` ≈ −0.4 SLPM per %O2, `KI` ≈ 0.0033 /s. Replace with
  per-mode values from bump tests, using SIMC tuning with τc = 2θ.

### 6.1 Bump test (per mode, feedback off)

1. Settle at the mode's flow until O2 is flat within 0.01 % over 10 min.
2. Log time, setpoint and O2 at 1 Hz throughout.
3. Step the flow up by ~15 % (at least 0.03 SLPM). Wait until O2 is flat again (30–60 min).
4. Step back down and wait again.
5. Read off gain K, dead time θ and time constant τ. Then:
   - Kc = τ / (|K|·(τc + θ))
   - τI = min(τ, 4(τc + θ))
   - `KP` = −Kc
   - `KI` = 1/τI

### 6.2 First closed-loop trial

1. Step the target down 0.1 %.
2. Expect 63 % of the response in ~τc + θ, and less than 10 % overshoot.
3. Adjust:
   - oscillation with a period of ~4θ: halve |`KP`|
   - sluggish response: raise |`KP`| by 1.5×
   - slow wander: halve `KI`

Change one knob at a time.

## 7. Operator interface

| PV | Type | Purpose |
|---|---|---|
| `$(PP)Mode` | mbbo | A / B |
| `$(PP)Target` | ao | % O2, default 0.99 |
| `$(PP)Tolerance` | ao | % O2, default 0.02. Sets the "in range" indicator only, not a deadband. |
| `$(PP)PurgeDelta` | ao | % O2, default 0.15 |
| `$(PP)Purge` | bo | Start purge |
| `$(PP)FlowZero` | bo | Set flow to zero, feedback off |
| `$(PP)ResumePID` | bo | OPEN_LOOP → HANDOFF |
| `$(PP)State` | mbbi | Current state |
| `$(PP)LastAction` | stringin | Most recent override or transition reason |
| `$(PP)OverrideCount` | longin | Running count of automatic corrections |
| `$(PP)O2PID` | epid | Loop. Gains and limits are loaded from the mode. |

Per-mode parameters (base flow, `KP`, `KI`, `DRVH`, alarm multipliers) are autosaved.

## 8. Testing strategy

"Written correctly" means proven against a simulator before it touches the enclosure.

1. **Simulation bench, no hardware.**
   - A caproto soft IOC impersonates the Alicat PVs (setpoint, ramp, hold, `Flow_RBV`) and the
     O2 analyzer. It uses the leak/dilution model with dead time and noise.
   - The real IOC (epid + SNL) runs against it.
   - Scripted scenarios cover every row of the end-state table: normal purge, open lid at
     start, lid opened in REGULATE, analyzer freeze, MFC hold, bad ramp rates, empty cylinder,
     IOC restart mid-REGULATE.
2. **SNL build.** Compile with `snc` in a real synApps build. Nothing is declared done until it
   compiles and passes the bench.
3. **Hardware, supervised.**
   - Bump tests per mode, feedback off.
   - A PID ceiling test for surface vibration.
   - The first closed-loop trial.
   - A deliberate lid-open test.

## 9. Open decisions

1. **Host IOC.** Either the existing Alicat or analyzer IOC, if it includes seq, std, calc and
   autosave, or a new soft IOC. Decides the build and who maintains it.
2. **PV prefix `$(PP)`.** Needs approval before any record is created.
3. **Owner sign-off** for writes to Alicat `Setpoint`, `RampRate` and `Run`.
4. **PROVISIONAL values to confirm on hardware:**
   - `MinDrop`: 1.0 %
   - ambient O2: 19.4 %
   - lid threshold: 10 % for 5 s
   - lid rise rate `LidSlope`: 0.2 %/s
   - flow-mismatch tolerance
   - PID ceiling
   - gains
   - Alicat ramp-rate-0 semantics
