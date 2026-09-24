# Enclosure O2 kinetics and lid identification (helium purge), 17–24 Sep 2026

Raw archiver exports in `data/` (byte-exact copies of the files downloaded on 2026-09-24):
* `oxygen level rbv Sep.csv`: `15IDC:D1Dmm_calc`, % O2, 600 s grid
* `alicat setpoint rbv Sep.csv`: `15IDC:Alicat1:Setpoint_RBV`, SLPM He, logged on change
* `alicat flow rbv Sep.csv`: `15IDC:Alicat1:Flow_RBV`, SLPM He, about every 180 s
* `oxygen level rbv Sep high res 4 hours.csv`: O2 at 10 s, 24 Sep 00:59–04:59 (window A)
* `oxygen level rbv sep high res 8 hours.csv`: O2 at 10 s, 24 Sep 05:46–13:46 (window B)
* `alicat flow/setpoint rbv Sep high res 4 hours.csv`: ~10 s, 24 Sep 09:48–13:48

Regenerate every number and figure (Python 3.12, versions pinned in `requirements.txt`):

    python run_all.py     # results.json, hr_results.json, figures/fig1-fig13
    python events.py      # event table to console
    python classify.py    # per-episode lid table to console

The controller that uses these numbers is specified in
`../docs/superpowers/specs/2026-09-24-o2-purge-feedback-design.md` (its section 2.1).

The flow readbacks are assumed to be in helium units, with the Alicat's gas table set to He.
If it were set to N2 while flowing He, every flow would carry a gas-correction error, and so
would V, which is derived from flow × time.

## 1. Time link: no interpolation needed
* O2 (600 s grid), setpoint (on change) and flow (about 180 s) are joined by **zero-order hold**. Nothing is interpolated.
* The clocks agree. Fitting 11 exactly-timed purges with a free offset gives **0 ± 4 s** between the O2 and He-flow PVs (fig 3b).

## 2. The setpoint readback misses most purges (figs 1, 3c, 7)
* `Flow_RBV` shows **41 purges at 20 SLPM**. `Setpoint_RBV` records only **16** of them.
  In the other 25, the setpoint readback goes 0 → hold value while the flow readback shows 20 SLPM in between.
  71 of 3363 flow samples disagree with the setpoint readback.
* This explains the first pass's "unexplained" closures. Using V from the logged purges, the O2 drop gives each of the
  **20 unlogged closure purges a length of 5.69–6.74 min (median 6.21)**. All 20 fall inside what the ~180 s flow
  sampling allows. The logged purges last 6.00 min. So it is **one 6-min program**, and the setpoint readback records it only sometimes.
* Delivered flow is therefore reconstructed as the setpoint readback, overridden by `Flow_RBV` where they disagree.
  Edges snap to setpoint events when possible. Otherwise they sit at the gap midpoint (±90 s), and each edge is flagged exact or not.
* Recommendation: **archive the setpoint command PV (not just the RBV) and `Flow_RBV` on monitor.**

## 3. Enclosure volume and purge kinetics (fig 3)
* One well-mixed volume, **V = 42.2 ± 0.7 L**, fits 11 exactly-timed purges at 3, 5.2 and 20 SLPM with 2.7 % rms.
  The time constant is τ = V/F: **2.1 min at 20 SLPM**, 53 min at 0.8 SLPM, 2.8 h at 0.25 SLPM.
* Diluting air to 1 % costs V·ln(19.4/1) ≈ **125 L of He**, whatever the flow; the flow only sets the time.
  The 20 SLPM × 6 min program predicts **1.1 %** (fig 6b).

## 4. Which lid (fig 4)
Classification is per closed episode, because the lid cannot change without O2 going to air.
The evidence is the O2 ingress **J = F·C + V·dC/dt** (mL O2/min) on each 10-min interval, using the delivered flow:

| lid | ingress family (fitted 0.23–1.8 SLPM He) | steady O2 at hold flow |
|---|---|---|
| normal | J = 2.6·F^+0.04 (×/÷1.27): flat, 2–4 mL/min | 0.25 SLPM → ~1.0 % |
| collimator | J = 7.1·F^−0.87 (×/÷1.31): falls with flow | 0.8 SLPM → ~1.1 %; **0.25 SLPM → ~9.5 %** |

Result: **24 normal, 10 collimator, 2 atypical** episodes, plus 3 single samples at 18.9 % with the flow off.
Those three are the ambient reading dipping under the 19 % threshold, not sealed lids. The result is **unchanged from the setpoint-only analysis**.
Five episodes disagree with their hold-flow band. Three are collimator lids at 0.25 SLPM (18 Sep 02:09, 19 Sep 06:19 and 10:09).
In those, O2 climbs 1 → 5 % between purges, and the flow readback confirms the flow really was 0.25 SLPM.
The drops that end each saw-tooth are **unlogged 20 SLPM purges** (18 Sep 04:43, 19 Sep 08:31), not the 0.7–0.8 setpoints.
Atypical: 18 Sep 07:19 (J ≈ 7 at 0.25, 3× normal) and 20 Sep 06:19 (J ≈ 3.9 at 0.5, marginal).
The two lids can only be told apart at low flow (≲ 1 SLPM).

## 5. Whole-week replay (fig 8)
Five global numbers (V, two ingress families, the flow-off leak rate) plus the delivered flow replay every episode from its first sample:
* **normal lid: 99 % of 428 samples within ×/÷1.5, median error 8 %**
* collimator lid: 68 % within ×/÷1.5, median error 32 %. The depth of its modelled purge dips depends on
  unlogged purge edges known only to ±90 s (±30 L He, a factor ~2).
* Before each mid-episode purge, the replay tests "lid stayed on" against "lid was lifted". It finds **one unsampled
  lid lift: 24 Sep 11:51–11:54** (flow off 11:51, purge 11:54). It fell between two 600 s O2 samples; only an air start fits
  the 1.4 % seen after the purge. **Confirmed by the 10 s re-export (section 7): lift at 11:52:19, 2 min open.**

## 6. Lid removal and He use (figs 5, 7)
* **The 10 steepest rises in the record are all lid removals**, at 1.85–1.90 %/min (0.031 %/s). All 32 removals: median
  1.83 %/min, range 1.03–1.90. **These are sampling-limited lower bounds.** Every lift completes within one 600 s interval.
  **0 of 32** were caught mid-rise, so the 95 % upper bound on the 3 → 18.5 % transit is **54 s** and the true slope is at least ~17 %/min.
  Section 7 measures it directly at 10 s.
* The fastest rise with the lid on was 0.17 %/min. Flow-off leak-in with the normal lid on: τ = 11.8 h, starting ingress 10.7 mL O2/min,
  about 4× the ingress with 0.25 SLPM flowing.
* Operators switch the flow off before lifting (31 of 32 lifts, a median 9.7 min before the first open sample).
* **He this week: 8125 L delivered** (the setpoint readback alone implies 5445 L).
  **5697 L (70 %) went into 20 SLPM purges.** Flow with the lid definitely open was **10 L**, plus at most 23 L inside lift intervals.
  **Correction to the first pass:** its "980 L flowed with the lid open" was purge gas charged to the wrong side of the closure.
  Automatic flow cut-off on lift saves almost no helium, because it is already done by hand. Its value is automation and safety.
* The helium lever is the purge. 41 purges × ~125 L is well-mixed dilution, which needs ln(19.4) ≈ 3 volume changes.
  He is lighter than air, so feeding He at the top and venting at the bottom could approach displacement (about 1–1.5 volumes).
  **That is untested here.** The other levers are fewer lid cycles, a smaller V, or a higher post-purge target (e.g. 2 % needs 96 L).

## 7. High-res tranche: fast kinetics, normal lid (figs 9–13; `python hr_analysis.py`)
There are two 10 s O2 windows on 24 Sep. Both match the 600 s archive exactly at every shared timestamp, and neither repeats values.
* **Window A:** `...high res 4 hours.csv`, 00:59–04:59. Paired with the weekly Flow_RBV (~180 s) and the setpoint events.
* **Window B:** `...high res 8 hours.csv` (the re-export), 05:46–13:46. It overlaps the high-res Flow_RBV and Setpoint_RBV (~10 s, from 09:48).
* `download.csv` is another 600 s O2 export and is not used.
* The weekly setpoint export is also missing the 11:51:02 flow-off event; only the high-res setpoint file has it.

Events: **A** is flow off → lift → 1.7 h open → unlogged purge. **C** is a 22-min flow-off with the lid kept on (08:47–09:10).
**B** is flow off → lift → 2 min open → purge (11:51–12:00). B is the lift no 600 s sample caught. **All normal lid.**

| | A | B | C |
|---|---|---|---|
| delay from flow off to leak-in reaching the sensor | 71 s | — (lifted at +76 s, before any leak-in) | 60 s |
| sealed leak-in slope (21 Sep model: 0.026 %/min) | 0.033 %/min | — | 0.024 %/min |
| lift: fast time constant | ≤ 5.6 s (bound) | **5.75 s (fitted)** | — |
| lift: last sealed → first big sample | 1.16 → 16.48 % | 1.85 (mid-rise) → 16.41 % | — |
| > 18.5 % / > 19.0 % after onset | 20 s / 30 s | 20 s / 30 s | — |
| peak 10 s slope | 92 %/min | 87 %/min | — |
| purge: delay from flow-on to O2 falling | ~7 s (6.00-min program assumed) | **13 s** (edge ±5 s) | — |
| purge: V from the decay | 40.7 L | 40.4 L | — |
| settling at 0.25 SLPM after the purge | 0.14 %, τ 64 s | 0.17 %, τ 66 s | — |

* **Lift kinetics.** A single ~6 s exponential carries 97 % of the step. B's fit uses three rising samples and predicts the fourth
  (18.94 % against 18.88 % observed). A's bound is consistent with a lift at its last sealed sample. The rest is a slower tail over 1–2 min.
  The two lifts overlay almost exactly when aligned at onset (fig 10b, c). The 600 s record could only say "≥ 1.9 %/min, transit ≤ 54 s".
* **Pre-lift signature (A only).** In the last 20 s before the lift, O2 rises at 0.16 %/min, 5× the leak-in rate; possibly the clamps being released.
* **Open lid (A).** The reading peaks at 19.56 %, then relaxes to 19.19 % over 1.5 h. A ~1 min dip at 03:04 reaches **18.95 %, below the 19 % threshold**.
* **Purges.** They are reproducible: both give V ≈ 40.5 L, a few % below the weekly 42.2 L. In both, the apparent volume rises from 39 to 42 L through the purge,
  with the same structured ±3 % residual. Mixing is good but not perfect, and the sensor zone lags the bulk,
  hence the ~65 s settling afterwards (a single well-mixed volume at 0.25 SLPM would take 169 min).
  B's edges come from Setpoint_RBV readings caught mid-ramp: **11:54:19 → 12:00:19, exactly 6.00 min.**
* **Transport delays** (fig 12). At hold flow, **~60–85 s** separates a flow change from the O2 response, in both directions (C: 85 s after the flow came back on).
  At 20 SLPM the delay is 7–13 s. **An O2 feedback loop at hold flow has a dead time of roughly 80 s.**
* **The unsampled lift is confirmed directly.** The 10-min replay had inferred it from the O2 after the purge
  (1.34 % seen; 1.31 % predicted for an air start, 0.07 % for a sealed box). The 10 s data show the lift at 11:52:19, a peak of 19.48 %, and 2.0 min open.
* **Step detection (PELT on log10 O2, window B).** Tuned to find nothing on two quiet hold stretches, it finds the lift (+1.19 decades) and the purge
  (−1.29, merged). It also flags two slow drifts: C's leak-in at 08:57 (+0.14) and the recovery at 10:25 (−0.06).
  Those are gradual changes that a piecewise-constant model fragments, not steps.

## 8. Programming thresholds (figs 6, 12)
* **Lid lift:** **O2 > 10 %** or a **30 s slope > 5 %/min**. Both fire on the first 10 s sample after each lift.
  Over 12 h of 10 s data, the largest sealed-lid rate was 0.23 / 0.13 / 0.085 %/min over 10 / 30 / 60 s windows,
  set by flow-off leak-in and pre-lift acceleration. The lifts gave 87–92 / 28 / 10 %/min, so at least **117×** separation, and 5 %/min sits 37× above anything sealed.
  **Do not use 19 %:** the open reading sits 0.2 % above it and dipped below it once.
* **Closure** can't be seen in O2 while the flow is off. It needs the operator or a lid switch.
* **Purge:** the 20 SLPM × 6.00 min program takes the box from air to ~1.1 % (≈ 120 L He). Expect a 7–13 s delay before O2 moves,
  and ~1 min of further settling at the sensor afterwards.
* **Hold:** normal lid 0.25–0.35 SLPM, collimator lid ≥ 0.8 SLPM. **Controller dead time at hold flow ≈ 80 s.** A flow-off shorter than about a minute
  won't show in O2 at all.
* **Lid ID at hold:** at 0.25 SLPM a normal lid holds flat; a collimator lid climbs at about 0.1 %/min (J > ~8 mL/min).
* **Caveat:** the fast numbers come from **two lifts, two purges and one sealed flow-off, all with the normal lid**.
  The collimator lid needs its own 10 s record before these thresholds are hard-coded for it.

## Limits
* The weekly O2 is sampled every 10 min, so faster kinetics there are bounded, not measured, and short lid lifts can be missed (24 Sep 11:52, now measured at 10 s).
* The one-volume model does not include slow post-purge tails (e.g. 19 Sep 00:39–05:19). A second, slow O2 reservoir is likely.
  At 10 s, the sensor zone also lags the bulk by about a minute.
* The flow-off leak rate comes from normal-lid runs only (21 Sep, plus A and C on 24 Sep). The collimator lid's rate is unmeasured, and the replay borrows the normal-lid value.
* PELT on the weekly record (ruptures, l2 on log10 O2, penalty 200·σ²·ln n, 0.3-decade floor) found no steps on three quiet stretches and matched
  70 of 76 threshold events. Every miss is an 18.7–19.0 % ambient flicker across the 19 % line.
