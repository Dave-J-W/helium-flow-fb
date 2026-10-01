# Sample-gas controller simulator: user guide

`simulator/sample_gas_simulator.html`. It is a single file: open it in any browser (Edge, Chrome,
Firefox) with nothing to install. Hover over almost anything to see a tooltip. **? Help** (top
right) shows this guide, the guide for building a similar simulator, and the self-test.

## What it is, and what it is not

- **It is** an executable version of the design spec
  (`docs/superpowers/specs/2026-09-24-o2-purge-feedback-design.md`). The helium purge and O2
  feedback controller for the `15LSS_sample_gas` IOC runs against a model of the real enclosure,
  O2 analyzer, Alicat MFC and helium cylinder.
- **Its purpose** is to let you try the design before any EPICS code exists: press buttons, break
  things, and watch what the controller does.
- **It is not** the IOC. The real controller will be an `epid` record plus an SNL state machine.
  Anything you change here should go back into the spec, and the simulator's scenarios become the
  tests the real IOC must pass.
- **The physics comes from your data** (spec §2.1):
  - enclosure volume 41 L
  - leak rates per lid, and ~80 s dead time at hold flow
  - lid-lift kinetics
  - the Alicat's ramp and hold behaviour
  - the 1 Hz analyzer noise (white 0.84 m% plus a slow 0.67 m% component that is not bulk O2)

## Quick start

1. Open the file. Station **15IDC** is selected; it starts IDLE, lid on, box full of air.
2. Press **Purge**. Watch the O2 graph fall on the log axis, and the flow jump to 20 SLPM.
3. Set **Speed** to 300× or 1000× to see the handoff to the PID and the regulation at 0.99 %.
4. In the right-hand column, press **Lift lid**. Within ~10 s the controller stops the flow
   (state OPEN_STOP, red banner).
5. Press **Close lid**, then **Purge** again.

To try a prepared case, pick a **Scenario** at the bottom right and press **Reset station & run
scenario**. Each scenario's description says what to watch for.

## Screen layout

| Area | What it is |
|---|---|
| **Header** | Station tabs (15IDC, 15IDE; the dot shows the worst alarm), Pause/Run, +10 s, Speed, simulated clock, **? Help** |
| **Left column: main panel** | What the operator's Phoebus main panel will show: alarm banner, the four big readouts, state, controls, buttons, Admin |
| **Centre: graphs** | O2, helium flow, helium left in the cylinder, and a strip showing the controller state over time |
| **Right column: physical world** | Simulator only. Change the lid, the Alicat, the cylinder, the analyzer, crash the IOC, run scenarios, edit the plant physics |
| **Bottom: event log** | Everything that happened, newest first. MINOR alarms are yellow, MAJOR red, world actions in italics |

## The main panel (what operators will see)

### Readouts

- **Oxygen:** the analyzer reading (`15IDC:D1Dmm_calc`), updated every second. It shows
  "INVALID" if the analyzer reports an invalid value. Below it: target ± tolerance.
- **Helium flow:** measured by the Alicat (`Flow_RBV`), with the expected flow for the selected
  mode. "MFC STATUS: HLD" means the Alicat is on hold.
- **Flow setpoint:** the Alicat's setpoint readback (`Setpoint_RBV`) and the value the controller
  last commanded ("(PID)" when feedback is on).
- **Helium cylinder:** litres left, counted from the Alicat's totalizer since the last "New He
  cylinder fitted". Below it: the run-out forecast range. Once a cylinder-pressure PV exists, the
  pressure is shown too.

### State, progress and controls

- **State** badge and a one-line description (see *States* below).
- **In range** light: green when O2 is within target ± tolerance.
- **Progress line:**
  - *purge:* elapsed / timeout, lid-check result, the lag-corrected O2 and the handoff level
  - *handoff:* waiting for the flow to settle
  - *regulation:* "SETTLING ↓/↑" while moving to a new target, and the PID internals
- **Enclosure mode:** A = normal lid, B = collimator lid. It sets the expected flow, gains and
  flow limits.
- **O2 target** (0.2–5 %).
- **Buttons:**
  - **Purge:** start a purge.
  - **Flow Zero:** stop the helium; use it before opening the enclosure.
  - **Resume PID:** only enabled in OPEN_LOOP, once the O2 reading is valid again.
  - **New He cylinder fitted:** press after changing the cylinder.
  - **Admin settings…**
- **Last action:** why the controller did the last thing it did.

## States

| State | Flow | Meaning |
|---|---|---|
| IDLE | not touched | The controller is not managing the flow |
| PRECHECK | not touched | Checks and fixes the Alicat before a purge: resumes it from hold, clamps a bad ramp rate, warns about the gas table |
| PURGE | 20 SLPM | Purging; the lid check runs ~40 s after full flow |
| HANDOFF | expected flow for the mode | Stepping down, then feedback on |
| REGULATE | PID | Holding O2 at the target |
| OPEN_LOOP | expected flow, fixed | The O2 reading was lost (invalid or frozen); no feedback possible |
| FLOW_ZERO | 0 | The operator pressed Flow Zero |
| OPEN_STOP | 0 | The enclosure is confirmed open: helium stopped to save it |
| IOC DOWN | Alicat holds its last value | The controller IOC is not running |

## How the controller behaves (defaults)

### Purge

- 20 SLPM.
- **Lid check:** waits for the O2 decay to start (a 2 % drop). It then judges 30 s of decay
  (15 s until 2026-09-30).
  - **Lid open** if the decay rate is below half the lid-on rate (flow ÷ volume), or if the decay
    levels off (curvature below 0.8: the second half's rate over the first half's).
  - The rates are least-squares fits over all the readings in the window. A reading equal to the
    one before it means no new analyzer update reached the controller that second, and is left
    out (user's decision 2026-09-30: such repeats had brought a closed-lid purge within 0.02 of
    the curvature limit).
  - Each half needs at least 4 updates (`lidMinSamples`) for the curvature test. With fewer, the
    check waits, up to 60 s after the onset; then it judges on the rate alone and logs
    "too few O2 updates for the curvature test … ratio only" (MINOR).
  - If no decay starts within 30 s, the lid is also open.
  - The check is skipped if the purge starts below 17 % O2, which proves the lid was on
    (lowered from 18 %, user's decision 2026-09-30: lid-open handling dips to ~18.4 % are
    common, below the archive's floor of 18.67 % for a steady open-lid reading).
- **Handoff:** when the **lag-corrected** O2 has been below target − Δ for 5 s.
  - Δ = −0.04 %, i.e. hand off slightly *above* target.
  - The correction removes the analyzer's ~12 s lag at full flow, during which it reads ~10 %
    high.
- **Timeout:** 1.3 × the purge time the kinetics predict for this target, recomputed from the
  measured decay (120–1800 s). If it runs out, the controller hands off anyway and raises
  "purge incomplete".

### Regulation

- `epid` every 10 s on a 10-sample O2 mean.
- **Gains per lid**, stated at 0.99 % and scaled by 0.99/target (gain scheduling):
  - normal lid: KP −9.3, KI 0.00088
  - collimator lid: KP −10, KI 0.0014
- **Near target** (within 0.01 % absolute), KP is halved. This cuts MFC moves about 4× at almost
  no cost to regulation.
- **Flow limits per lid:** normal 0.05–1.0 SLPM, collimator 0.3–2.0 SLPM. The hard ceiling is
  2.0 SLPM (surface vibration).

### Protection

- **Lid lifted while regulating:** O2 > 10 % for 5 s **and** a rise of ≥ 0.2 %/s → OPEN_STOP.
  A slow creep (cracked lid, flow off for hours) does not trip it.
- **Alicat on hold** (in any state where the controller owns the flow):
  - it writes Run and re-sends the setpoint
  - after 3 failed tries it raises MAJOR "cannot resume" and keeps retrying every 60 s
- **O2 reading frozen (30 s) or invalid:** switches to OPEN_LOOP at the expected flow.
- **IOC restart:** resumes regulation if O2 is valid, below 10 % and the setpoint is above 0.

## Alarms

MINOR is yellow, MAJOR red; the banner lists them most severe first.

| Alarm | When |
|---|---|
| enclosure open? / enclosure opened, flow stopped | Lid check at purge start, or a lid lift while regulating (MAJOR; flow to 0) |
| purge incomplete | Purge timeout reached before target − Δ (MINOR) |
| O2 reading invalid / frozen; running blind | Analyzer problems (MAJOR) |
| MFC was on hold, resumed / cannot resume | Hold monitor (MINOR / MAJOR) |
| flow mismatch: cylinder empty or MFC fault? | Measured flow ≠ `Setpoint_RBV` after the ramp time (MAJOR) |
| flow ≥ 1.5× / 2× expected: check enclosure (seal) | Steady flow well above what the lid needs (MINOR / MAJOR) |
| flow ≤ 0.5× expected: wrong enclosure mode selected? | Steady PID demand well below it (MINOR) |
| PID pinned at max flow | At the flow ceiling for 10 min (MAJOR) |
| O2 above target range / abnormally high | Above target + tolerance, or + 0.3 % (MINOR / MAJOR) |
| target not reached within 2:00:00 | Still settling after 2 h (MINOR) |
| helium cylinder empty in < 24 h / < 6 h | Run-out forecast (MINOR / MAJOR) |
| ramp rate / gas table overrides | Automatic corrections at purge start and handoff (MINOR, logged) |

### Why target changes don't raise false alarms

- After feedback turns on or the target changes, the loop is **settling**. The flow, PID-pinned
  and O2-range alarms stay quiet as long as O2 keeps moving toward the target. The slope is
  measured over 2 min with 20-sample means, and judged after a 5 min grace.
- **If it stalls, or 2 h pass,** the alarms apply at once.
- **Once O2 reaches the target,** settling ends, and any later excursion is a real disturbance.
- **Flow alarms also need a steady process** (steady flow and steady O2), and flow/O2-range
  alarms must persist 5 min.

## Admin and Deep admin

**Admin settings…** (beamline staff):
- tolerance, Δ, purge flow and timeout margin, feedback delay
- flow and O2 alarm thresholds, settle timeout, cylinder alarm levels
- PID gains and flow limits per lid
- the override log
- the **helium usage report**, with **Mark start of new user run**
- live internals: the epid fields, lid detector, hold monitor, settling state and cylinder
  forecast per window

**Deep admin settings…** (instrument scientist): every physical and threshold number the
controller uses, including:
- lid check, lid detector, ramp clamps, flow mismatch, hold monitor
- O2 validity, averaging, epid scan and deadband
- gain scheduling and the near-target fine band
- slope and settling logic, cylinder capacity, report window, run gap
- the linked PV names, read-only

Every field has limits; out-of-range entries are clamped (the field flashes red). Changes apply
immediately and are logged. In the real IOC these are autosaved records.

## The physical world (right column; simulator only)

- **Enclosure lid:**
  - Close / Lift / Crack (slow leak)
  - which lid is fitted
  - Re-seat: random seal quality ×/÷1.3, as measured between real closures
  - **Fitted lid follows the selected mode** (ticked by default). Untick it to test a wrong-mode
    selection.
- **Alicat MFC:**
  - hold at the current valve opening, hold at 100 % (as seen in the 2026-06-18 trace), stuck
    hold, clear hold
  - ramp rate (0 = instant)
  - gas table
- **Helium cylinder:** refill, nearly empty, empty; simulate a pressure PV.
- **O2 analyzer:** normal, frozen value, INVALID; a breath dip (ambient −0.6 % for 1 min).
- **Controller IOC:** crash, restart.
- **Scenarios:** see below.
- **Plant model parameters:** the physics. The controller never sees these.

## Scenarios

Each scenario resets its station first, then sets the target to 0.99 % and mode A unless the
scenario says otherwise. Your other settings are kept.

| # | Scenario | What to watch |
|---|---|---|
| 1 | Normal purge from air, normal lid | Lid check passes; handoff; regulation |
| 2 | Purge with the lid left open | OPEN_STOP at the lid check (~40 s) |
| 3 | Lid lifted during regulation | OPEN_STOP ~10 s after the lift |
| 4 | Flow Zero, lid kept on 22 min, then Purge | No false trip; lid check skipped (O2 < 17 %) |
| 5 | Flow Zero, lift 76 s later, 2 min open, re-purge | The measured 24 Sep sequence |
| 6 | Collimator lid fitted, mode A selected | Flow ≥ 2× expected once settled |
| 7 | Cylinder runs dry during regulation | Flow mismatch; O2 creeps past 10 % without tripping OPEN_STOP |
| 8 | MFC on hold, valve 100 % | Automatic Run and setpoint re-send |
| 9 | MFC stuck on hold | "Cannot resume" after 3 tries |
| 10 | Analyzer freezes, recovers, operator resumes | OPEN_LOOP, then Resume PID |
| 11 | Analyzer INVALID at purge start | Blind purge on the timer, then OPEN_LOOP |
| 12 | Bad ramp-rate settings | Two automatic overrides |
| 13 | Cylinder nearly empty at purge | Flow mismatch |
| 14 | IOC crash and restart | Heartbeat alarm; bumpless resume |
| 15 | Cracked lid during regulation | PID pinned, O2 high; no OPEN_STOP |
| 16 | Target change 0.99 → 0.50 % | Settling without false alarms |
| 17 | Cylinder run-out forecast (4 days) | Forecast range narrowing; run at 3000× |
| 18 | Collimator lid, mode B, purge | Regulation at ~0.84 SLPM |
| 19 | Helium ledger: three users, a 48 h downtime | Admin → usage report: 3 runs |

## Helium accounting

- **Litres left:** cylinder capacity (default 8000 L usable) minus the litres the Alicat
  totalizer has counted since **New He cylinder fitted**. No pressure PV is needed.
- **Run-out forecast:**
  - The usage rate is fitted over the last 3, 2, 1, 0.5 and 0.25 days; each window gives a
    run-out time.
  - The spread is shown as a range, e.g. "empty in 5.5–5.6 d (median 5.5 d, 5 windows)".
  - The median drives the < 24 h / < 6 h alarms.
  - It needs about 6 h of data before the first estimate.
- **Usage report** (Admin), covering the 60 days ending at the end of the latest **user run**:
  - helium dispensed, litres during runs, cylinders fitted, cylinder-equivalents used, and a
    table of runs
  - **A user run** is a series of purges. It ends at the first Flow Zero after its last purge,
    and closes after 60 h with no purge. That survives the 24 h and 48 h beam downtimes, plus up
    to 10 h before the user's next purge.
  - **Mark start of new user run** splits runs at a user changeover.

## Two stations

15IDC and 15IDE are independent controllers, as they will be in the one real IOC. Both run all
the time; the tab chooses which one you see. Each has its own settings, scenario, history and
log. Tick **show both stations** in the log to see both.

## Time and graphs

- **Speed:** 1× to 3000× simulated seconds per real second. **Pause** freezes both stations;
  **+10 s** steps.
- **Mode A settles slowly** (the box's time constant at 0.25 SLPM is ~2.7 h); use 1000× or
  3000× for long cases.
- **Window:** 10 min to 12 h, or all. The O2 and flow axes can be logarithmic.
- **True bulk O2** (grey dashed) is what only the simulator knows. The black analyzer reading
  lags behind it and carries the measurement noise.

## Self-test

**? Help → Self-test → Run self-test** runs every scenario on a hidden station with the default
settings. It checks the end state and key log messages, takes about 10–30 s, and leaves your
stations untouched. Run it after any change to the simulator. A failing row says exactly what
differed.

## Common questions

- **"The PID won't settle / O2 keeps drifting down."**
  - Check that the fitted lid matches the mode. With collimator mode and the normal lid, the PID
    is held at the collimator's minimum flow (0.3 SLPM) and O2 sits below target.
  - The warning "flow ≤ 0.5× expected: wrong enclosure mode selected?" appears after settling.
- **"The O2 reading wiggles near target."** That is the analyzer's noise, about 1 m% (0.001 %
  O2), not the loop oscillating. The true bulk O2 (grey) is much steadier.
- **"Reset didn't restore my settings."** Reset restores the plant and the controller's runtime
  state, not your settings: those are like autosaved records. Reload the page to get all
  defaults back.
- **"Where are the numbers from?"** Spec §2.1 (archive data) and §2.1.10 (1 Hz noise). The
  analysis is regenerated by `analysis/run_all.py`.

## Files and versions

- **Simulator:** `simulator/sample_gas_simulator.html`. Git tags mark known-good versions
  (`git tag -n1`), e.g. `sim-v0.9`.
- **These guides:** `docs/simulator/`. The Help page text is embedded from the Markdown files by
  `tools/embed_help.py`; run it after editing a guide.
- **Illustrated guide:** `docs/simulator/USER_GUIDE_ILLUSTRATED.md`. Its screen captures are
  regenerated by `tools/make_screenshots.py`, which uses the simulator's screenshot mode
  (`#shot=<scenario>&t=<seconds>&panel=…`).
