# Helium purge with O2 feedback: design

Date: 2026-09-24
Status: design under review. No code has been written. No PV has been created or written.
Measured enclosure physics from the 17–24 Sep 2026 archiver data were added on 2026-09-24 (section 2.1).
Values they confirm or replace are tagged MEASURED where they appear.

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
- **MEASURED**: fitted to archived data from this enclosure (section 2.1 gives the source and the event count).
  Values from one or two events are marked "n=1" or "n=2".
- **DERIVED**: follows from reasoning.
- **PROVISIONAL**: a starting value to be tuned on the hardware.

## 2. Hardware and existing software

| Item | Facts |
|---|---|
| MFC | Alicat BASIS, EPICS support `epics-modules/ip` `ipApp/Db/Alicat_BC.db` + `.proto` (VERIFIED). Max purge flow 20 SLPM, resolution ~0.01 SLPM. Alicat ramp rate normally 3 SLPM/s. |
| O2 analyzer | Updates at ~1 Hz. Noise 0.8–0.9 m% O2 per 10 s sample at hold; drift +0.13 m%/min (MEASURED). A flow change reaches the reading after **60–85 s at hold flow** and **7–13 s at 20 SLPM** (MEASURED, section 2.1.3). |
| Enclosure modes | Same volume, different leak behaviour. **Mode A = normal lid** (~0.25 SLPM for ~1.0 % O2); **mode B = collimator lid** (~0.84 SLPM for 0.99 %). Mode B's O2 ingress rises steeply as flow falls (MEASURED, section 2.1.4). |
| Enclosure volume | **42.2 ± 0.7 L** from 11 purges on the weekly archive; 40.4–40.7 L from two 10 s purges (MEASURED, section 2.1.2). |
| Surface limit | Flow above ~2 SLPM may vibrate the liquid surface and prevent X-ray measurement. The exact PID ceiling is to be tested. |
| Ambient O2 | The open-lid reading is **not constant**: 19.0–19.6 %. It peaks right after a lift (19.48–19.56 %) and relaxes to ~19.2 % over 1.5 h; one dip to 18.95 % (MEASURED, section 2.1.8). Use 19.4 % as the model value. |

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

## 2.1 Measured enclosure physics (archiver data, 17–24 Sep 2026)

Source: `analysis/` in this repo (README.md, `figures/fig1`–`fig13`, raw CSVs in `analysis/data/`).
`python analysis/run_all.py` regenerates every number here from the CSVs.

| Data | PV | Sampling |
|---|---|---|
| Weekly O2, 17–24 Sep | `15IDC:D1Dmm_calc` | 600 s |
| Weekly setpoint | `15IDC:Alicat1:Setpoint_RBV` | on change |
| Weekly flow | `15IDC:Alicat1:Flow_RBV` | ~180 s |
| High-res O2, 24 Sep | `15IDC:D1Dmm_calc` | 10 s, 00:59–04:59 and 05:46–13:46 |
| High-res flow and setpoint, 24 Sep | as above | ~10 s, 09:48–13:48 |

The clocks of the three PVs agree to 0 ± 4 s.

### 2.1.1 Model

One well-mixed volume V, purged with pure helium at flow F, with O2 entering through the lid seals at rate J:

```
V · dC/dt = J(F, lid) − F · C          C in % O2, V in L, F in SLPM He, t in min
J in %·L/min;  ×10 = mL O2/min
steady state:  C_ss = J / F  =  J[mL/min] / (10 · F[SLPM])
flow off:      dC/dt = k · (C_amb − C),   k = 0.00142 /min  (τ = 11.8 h, normal lid)
```

Replaying the whole week with only these global numbers and the delivered flow gives, with no per-episode refit:
- **normal lid:** 99 % of 428 samples within ×/÷1.5, median error 8 %
- **collimator lid:** 68 % within ×/÷1.5, median error 32 %. The error is limited by purge edges known only to ±90 s.

The model's known departures are listed in 2.1.5.

### 2.1.2 Volume and purge (MEASURED)

- **V = 42.2 ± 0.7 L** from 11 exactly-timed purges at 3, 5.2 and 20 SLPM (2.7 % rms). The two 10 s purges give **40.7 and 40.4 L**.
  Use **V = 41 L** in the simulator.
- **Time constant τ = V/F:** 2.1 min at 20 SLPM, 53 min at 0.8 SLPM, **169 min at 0.25 SLPM**.
- **Helium to dilute air to C:** V·ln(19.4/C), whatever the flow; ≈ 125 L to reach 1 %. The flow sets only the time.
- **The existing timer purge** is **20 SLPM × 6.00 min**. That is 41 purges this week, fixed at 6.00 min by high-res setpoint readings caught mid-ramp.
  It ends at ~1.1 % O2 at the sensor. **Purges used 70 % of the week's helium** (5700 of 8125 L).
- **Drop at 60 s** after the purge timer starts, for a closed box starting from air: **6.8 and 7.4 %** (n=2, 10 s data). An open box has not been measured.
- The Alicat reaches 20 SLPM within one 10 s sample (a mid-ramp reading of 11.85 SLPM).

### 2.1.3 Transport delays (MEASURED, 10 s data)

| Event | Delay before O2 moves |
|---|---|
| Flow 0.25 → 0 SLPM, lid on | 71 s, 60 s (n=2) |
| Flow 0 → 0.25 SLPM, lid on | O2 keeps rising for 85 s (n=1) |
| 20 SLPM purge on | 13 s (edge ±5 s); ~7 s in the second purge, assuming the 6.00-min program |

**At hold flow the loop has ~80 s of dead time.** A flow interruption shorter than about 1 min is invisible in O2.

### 2.1.4 O2 ingress per lid (MEASURED, weekly data, 0.23–1.8 SLPM)

| Lid (mode) | Ingress J (mL O2/min) | C_ss | Flow for 0.99 % | Flow for 0.5 % |
|---|---|---|---|---|
| normal (A) | **2.6·F^+0.04** (×/÷1.27): flat | 0.26/F % | 0.25 SLPM | 0.51 SLPM |
| collimator (B) | **7.1·F^−0.87** (×/÷1.31): falls with flow | 0.71·F^−1.87 % | 0.84 SLPM | 1.21 SLPM |

- **Mode B needs over-pressure to seal.** At 0.25 SLPM its ingress is 23–30 mL/min, and O2 climbs toward ~9.5 % with τ = 2.8 h. That happened three times this week.
- **Seal force spread.** Within a mode, J scatters ×/÷1.3 between closures, attributed to how hard the lid hardware is tightened. Two episodes fit neither family.
- **Telling the lids apart** is only possible at low flow (≲ 1 SLPM). Above ~2 SLPM the two families converge.
- **Flow off, normal lid.** Ingress at 1 % O2 is 10.7 mL/min, 4× that with 0.25 SLPM flowing.
  The measured slopes are 0.024 and 0.033 %/min (n=2 at 10 s), against 0.026 %/min from the 7 h run on 21 Sep.
  The collimator lid's flow-off rate is unmeasured.

### 2.1.5 Mixing and the sensor zone (MEASURED, n=2)

- **Imperfect mixing during the purge.** The 20 SLPM decay is exponential to ±3 %, but the apparent volume F/(−dlnC/dt) rises from 39 to 42 L through the purge. The residual has the same shape in both purges.
- **Post-purge settling.** At the hold flow the reading keeps falling by **0.14–0.17 % with τ ≈ 65 s** (64 and 66 s). One well-mixed volume would take 169 min.
  So the sensor sits in a zone that lags the bulk by about a minute. **At purge end, the sensor reads ~0.15 % above where it will settle.**
- **Slow tails.** Some long post-purge tails (weekly data) decay more slowly than V/F, suggesting a slow O2 reservoir (sample, plastics). This is not modelled.

### 2.1.6 Sealed-lid O2 behaviour: what the lid-open detector must ignore (MEASURED)

| Situation | Largest rise rate | Level reached |
|---|---|---|
| Hold, any lid, flow on (10 s data, 12 h) | 0.0008 %/s (10 s difference) | — |
| Flow off, lid on, final 20 s before a lift (clamps loosened?) | **0.0039 %/s** | 1.16 % |
| Flow off, lid on, 7 h (21 Sep, weekly) | ≤ 0.0005 %/s | **9.2 %** |
| Collimator lid starved at 0.25 SLPM (weekly) | ≤ 0.002 %/s | 5.7 % and rising |
| All sealed 600 s intervals, week | 0.17 %/min = 0.0028 %/s | — |

### 2.1.7 Lid removal (MEASURED, n=2 at 10 s; 32 more lifts at 600 s)

| | Lift A (02:59) | Lift B (11:52) |
|---|---|---|
| last sealed → first big sample | 1.16 → 16.48 % | 1.85 (caught mid-rise) → 16.41 % |
| **10 s difference at the lift** | **1.53 %/s** | **1.46 %/s** |
| fast time constant | ≤ 5.6 s (bound) | **5.75 s (fitted; predicts the next sample to 0.06 %)** |
| peak instantaneous slope (model) | — | ~3.2 %/s |
| crosses 10 % after onset | ≤ 10 s | ~3.8 s (DERIVED from τ) |
| > 18.5 % / > 19.0 % after onset | 20 s / 30 s | 20 s / 30 s |
| flow switched off before the lift | 262 s | 76 s |

- The two lifts overlay almost exactly. A ~6 s exponential carries 97 % of the step, followed by a 1–2 min tail.
- In the weekly data, 31 of 32 lifts had the flow switched off first, and none of 32 was caught mid-rise at 600 s.
- **One lift was never seen at 600 s** (24 Sep, open 2.0 min, then purged). Only 10 s data show it.

### 2.1.8 Open-lid reading (MEASURED)

- It peaks right after the lift (19.48 and 19.56 %), then relaxes, not linearly, to 19.19 % over 1.5 h.
- One ~1 min dip reached **18.95 %** with the lid off (breath, or a helium pocket released).
- The weekly 600 s open-lid samples read 19.03–19.58 %, with occasional 18.7–19.0 % readings while the flow was off.
- **Any open/closed threshold near 19 % is unsafe.** The design's thresholds (10 %, 9 % arming, 18 % drop-check skip) all clear this range.

### 2.1.9 Setpoint readback caveat (MEASURED, archiver level)

- In the archiver, `Setpoint_RBV` **missed 25 of 41** timer purges and the 24 Sep 11:51:02 flow-off. `Flow_RBV` and the O2 both show them.
- In the high-res export it read **0.43 and 19.55 SLPM mid-ramp**, so it may report a ramping, not final, setpoint.
- Whether the PV itself or only its archiving misses the events is unknown. The flow-mismatch rule (5.1) compares `Setpoint_RBV` with `Flow_RBV`, so this matters (section 9).

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
   - Measured basis: a closed box starting from air has dropped **6.8–7.4 %** by this point (n=2; 2.1.2), so 1.0 % leaves ~7× margin.
     The response of an **open** box to 20 SLPM is unmeasured. The lid-open test (section 8) must confirm it stays below 1 %.
   - The 18 % skip threshold clears every open-lid reading seen (≥ 18.67 %) by at least 0.67 % (2.1.8).
5. **Early handoff**: when O2 < target − Δ continuously for 15 s, go to HANDOFF.
   Δ defaults to 0.15 %.
6. **Timeout**: at 6.5 min, go to HANDOFF anyway and raise a MINOR "purge incomplete" alarm.

**Measured timing conflict (DERIVED from 2.1.2, needs a decision; section 9).**
- From air, reaching target − Δ = 0.84 % at 20 SLPM takes **6.4–6.6 min of flow** (V = 40.5–42.2 L). Add ~13 s of sensor delay and the 15 s hold.
- With the defaults, the 6.5 min timeout will therefore often fire first, and "purge incomplete" becomes a routine alarm.
- At 6.5 min the sensor reads 0.78–0.89 %. It then settles a further ~0.15 % within ~3 min (2.1.5), so the bulk is already lower than the reading.
- Options:
  - raise the timeout to ~7.5 min;
  - hand off on target − Δ with a smaller Δ;
  - accept the alarm.
- Each extra minute at 20 SLPM costs 20 L of helium.

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

#### 4.6.1 Measured basis for inferring the lid state (MEASURED, 2.1.6–2.1.8)

The two terms separate a lift from every sealed behaviour recorded:

| Quantity | Lid lifted (n=2 at 10 s) | Sealed, worst case seen | Margin at the design threshold |
|---|---|---|---|
| 10 s rise rate | **1.46–1.53 %/s** | **0.0039 %/s** (flow off, clamps being loosened) | `LidSlope` 0.2 %/s: **7×** below lifts, **51×** above sealed |
| O2 level | > 18.8 % within 20 s, > 19 % within 30 s | 9.2 % (7 h flow off); 5.7 % and rising (collimator lid starved) | 10 %: lifts cross in ~4 s; nothing sealed reached it |
| Both together | yes, ~5–10 s after onset | never | — |

- **Neither term alone is enough.** A sealed box with the flow off does creep past 10 %: 9.2 % after 7 h on 21 Sep, heading for ambient. But it does so at ≤ 0.0005 %/s. A cracked lid or a starved collimator lid is similarly slow. The rate term rejects all of these.
- **Detection latency** (DERIVED from the measured τ = 5.75 s): O2 crosses 10 % ~4 s after the lid starts to lift, and the 5 s filter completes ~9 s after.
  At that point the trailing-60 s maximum of the 10 s difference is ~1.5 %/s, 7× over `LidSlope`.
  **Expect OPEN_STOP within ~10 s of a lift.** The 10 s archive shows the reading at > 16 % one sample after onset in both lifts.
- **`LidSlope` = 0.2 %/s is supported by the data and may stay.** Anything from ~0.05 to ~0.5 %/s keeps at least 3× margin on both sides.
  At the analyzer's 1 Hz, the 10 s difference behaves the same way. The peak instantaneous slope is ~3.2 %/s.
- **Pre-lift warning (optional, n=1).** In the 20 s before lift A, O2 rose at 0.16 %/min, 5× the flow-off leak-in. It could serve as an early "clamps released" hint, but one event is not enough to act on.
- **Not covered:** the collimator lid has no 10 s record. The lift kinetics are expected to be similar (same box and sensor), but this is unverified.

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
| No O2 drop at 60 s (confirmed open enclosure). A closed box drops 6.8–7.4 % by then (MEASURED) | **0** | MAJOR "no O2 drop, enclosure open?" | Operator closes enclosure, presses Purge |
| O2 > 10 % for 5 s AND fast rise (lid opened without Flow Zero). Fires ~10 s after a lift (DERIVED from measured kinetics, 4.6.1) | **0** | MAJOR "enclosure opened, flow stopped" | Operator closes enclosure, presses Purge |
| Operator Flow Zero. With the lid kept on, O2 starts rising 60–85 s later at 0.024–0.033 %/min, about +0.5 % in 20 min (MEASURED, normal lid) | **0** | none | Operator presses Purge |
| Lid lifted and replaced between purges faster than the archive samples (seen once: 2 min open, 24 Sep 11:52) | follows the operator's Flow Zero / Purge | none | Operator presses Purge. **Archive O2 at 1 Hz or 10 s**: a 600 s archive can miss the whole event |
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

Model (MEASURED, 2.1.4): C_ss = J(F)/F, with ingress J = a·F^b per lid. So:

```
expected_flow = mode_base_flow × (0.99 / target)^n,     n = 1 / (1 − b)
```

| Mode | `mode_base_flow` (for 0.99 %) | b | n | Flow for 0.5 % |
|---|---|---|---|---|
| A (normal lid) | 0.25 SLPM | +0.04 | **1.0** (the original linear rule) | 0.51 SLPM |
| B (collimator lid) | 0.84 SLPM | −0.87 | **0.53** | 1.21 SLPM (the linear rule would give 1.66) |

- The original rule (O2 ≈ 20.9·L/F, i.e. n = 1) holds for mode A. It **over-predicts mode B's flow change** by ~40 % at 0.5 %, because more over-pressure seals the collimator lid better.
- The sensor's ambient is 19.4 %, not 20.9 %. This only matters for absolute leak-rate numbers.
- Seal-force spread is ×/÷1.3 on J, so expect `expected_flow` to be off by up to ~30 % for a given closure. The PID absorbs that.
- **Consequence for the flow alarms (section 5).**
  - The MINOR "flow ≥ 1.5× expected" threshold sits only ~15 % above a badly sealed but normal closure in mode A. Expect occasional nuisance MINORs.
  - The case that matters, the **collimator lid with mode A selected**, needs ~0.84/0.25 = 3.4× the mode-A flow. It therefore lands in the MAJOR "flow ≥ 2× expected / PID pinned" row, which is the right outcome.
  - Two closures this week had 1.4–2.7× the normal-lid ingress without being collimator lids. They would sit between the two alarm thresholds.

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

### 6.0 Process model at the hold flow (MEASURED inputs, DERIVED gains)

| | Mode A (normal lid, 0.25 SLPM) | Mode B (collimator lid, 0.8 SLPM) |
|---|---|---|
| Steady gain K = dC_ss/dF | −3.8 %O2 per SLPM | −2.5 %O2 per SLPM |
| Time constant τ = V/F | **169 min** | 53 min |
| Dead time θ (transport ~80 s + half the 10-sample mean + scan) | ~95 s | ~95 s |
| τ/θ | ~107 (effectively integrating) | ~33 (effectively integrating) |
| Integrating slope k' = \|K\|/τ | 3.8e-4 %/s per SLPM | 8.0e-4 %/s per SLPM |
| SIMC (integrating), τc = 2θ: Kc = 1/(k'(τc+θ)) | **9.3 SLPM/%** | **4.4 SLPM/%** |
| τI = 4(τc + θ) | 1140 s | 1140 s |
| → `KP`, `KI` | **−9.3, 8.8e-4 /s** | **−4.4, 8.8e-4 /s** |

- **The model-derived `KP` is 10–20× the provisional −0.4.** The provisional gains would regulate very slowly: an upset of 0.1 % O2 would move the flow by only 0.04 SLPM at first.
- **Noise check for the high gain.** Sensor noise is ~0.001 % per sample, and less after the 10-sample mean. × 9.3 that is ≤ 0.01 SLPM, at the MFC's resolution, so the high gain is noise-safe.
- These are **starting points for the bump tests, not final values**. Treat them as an upper bound on aggressiveness until 6.2 is done.
- **Handoff transient.** At handoff the sensor still reads ~0.15 % high and settles over ~1 min (2.1.5). The loop will see O2 falling below target just after FBON = 1 and trim the flow down.
  That is harmless, but it is not a disturbance: consider holding FBON off for ~2 min after the handoff flow is reached.

### 6.0.1 Bump-test duration (DERIVED; changes 6.1)

- With τ = 169 min (mode A) and 53 min (mode B), "wait until O2 is flat again (30–60 min)" is not achievable: a flat settle needs ~3τ, i.e. 8 h and 2.7 h.
- **Instead, identify the process as integrating.** After a flow step ΔF, O2 bends away from its previous trend after θ (~80–95 s). Its new slope is ΔdC/dt ≈ −ΔF·C/V (mode A). Read k' and θ from 20–30 min of data. That is enough for SIMC.
- A full settle is only needed to confirm K. Mode B's K includes the flow-dependence of its ingress, so it is worth one long step.

### 6.1 Bump test (per mode, feedback off)

1. Settle at the mode's flow until O2 is flat within 0.01 % over 10 min.
2. Log time, setpoint and O2 at 1 Hz throughout.
3. Step the flow up by ~15 % (at least 0.03 SLPM). Record 20–30 min and fit θ and the new slope, treating the process as integrating (6.0.1).
   A flat settle takes ~3τ: 8 h in mode A, 2.7 h in mode B.
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
   - **Simulator parameters (MEASURED, 2.1):**
     - Volume: V = 41 L.
     - Ingress: J_A = 2.6·F^0.04 and J_B = 7.1·F^−0.87 mL O2/min. Draw a per-closure seal factor from ×/÷1.3.
     - Flow off: k = 0.00142 /min toward ambient (use for both lids until B is measured).
     - Transport delay: 80 s at hold flow, 10 s at 20 SLPM.
     - Sensor-zone lag: first-order, τ = 65 s, applied to the bulk C.
     - Lid lift: exponential to ambient with τ = 5.75 s.
     - Ambient: 19.5 % relaxing to 19.2 % over 1.5 h, plus an occasional −0.6 % dip lasting ~1 min.
     - Noise: 0.001 % per sample.
   - **Extra scenarios from the data:**
     - Operator Flow Zero with the lid kept on for 22 min (must not trip the lid detector).
     - A lift 76 s after Flow Zero, open 2 min, then Purge (24 Sep 11:52).
     - Collimator lid regulated at the normal-lid flow (O2 climbs slowly; flow-high alarm, not OPEN_STOP).
     - Flow off for 7 h, lid on (O2 creeps past 10 %; must not trip OPEN_STOP).
   - **Validation target:** the simulator must replay the archived 24 Sep 10 s windows (00:59–04:59, 05:46–13:46) within a few % when fed the recorded flow.
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
4. **PROVISIONAL values to confirm on hardware** (status after the 2.1 data):
   - `MinDrop` 1.0 %: a closed box drops 6.8–7.4 %. **Still needs the open-box purge measured.**
   - Ambient O2 19.4 %: **MEASURED** 19.0–19.6 %, relaxing after a lift; a model value of 19.4 % is fine.
   - Lid threshold 10 % for 5 s: **supported** (4.6.1; n=2 lifts, normal lid).
   - `LidSlope` 0.2 %/s: **supported**, 7× below lifts and 51× above sealed (n=2, normal lid).
   - Flow-mismatch tolerance: open.
   - PID ceiling: open.
   - Gains: model starting values in 6.0, 10–20× the provisional `KP`. Confirm by bump test.
   - Alicat ramp-rate-0 semantics: open.
5. **Purge timeout vs Δ** (4.2): with target 0.99 % and Δ 0.15 %, reaching target − Δ from air takes 6.4–6.6 min, about the 6.5 min timeout. Raise the timeout (~7.5 min), reduce Δ, or accept the routine alarm.
6. **Setpoint_RBV reliability** (2.1.9): the archiver shows it missing 25 of 41 purges and one flow-off, and reading mid-ramp values.
   Before relying on the flow-mismatch rule (5.1), confirm with the IOC owner whether the PV itself or only its archiving misses events. If the PV misses them, compare `Flow_RBV` against the value the sequencer last wrote instead.
7. **Mode-B expected flow**: adopt the exponent n = 0.53 (5.2) or keep the linear rule and let the PID absorb the error.
8. **Collimator-lid fast data**: no 10 s record exists for mode B. Capture one lift and one purge at 10 s (or 1 Hz) before finalising mode-B thresholds.
9. **Archiving**: record `15IDC:D1Dmm_calc` and `Flow_RBV` at ≤ 10 s, and the setpoint command (not only the RBV). The 600 s archive missed a whole lid cycle.
