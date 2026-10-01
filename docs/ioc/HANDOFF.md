# 15LSS_sample_gas IOC: handoff to finish and install (2026-09-28)

Read this first. General lessons (bench traps, Phoebus, production conventions, porting method)
are in the Claude skills `cmc-epics-ioc` and `reference-trace-replay-port`; this file is only
about finishing THIS project. The spec (`docs/ioc/15LSS_sample_gas_IOC_spec.md`) is the
authority; the user's decisions of 2026-09-28 are written into it.

## 0b. State and findings, 2026-09-30 to 10-01 (supersedes 0a where they differ)

**Install and first live run**
- **Install location:** the user installed at `support/lssSampleGas` (no `ChemMat/`). `make` and the
  unit tests passed on chemmat-C92.
- **First live run:** on 30 Sep at 13:24:51 CDT the user started `startLSSSampleGas` by accident (not the Test start).
  - **What happened:** O2 was 0.929 % and the flow about 0.24 SLPM. The restart rule went straight to REGULATE (designed), the bumpless seed held 0.24, and then four PID steps took it to about 0.19 SLPM in 3.4 min. The user exited at about 13:29 and set 0.24 by hand.
  - **Replay:** feeding the archived O2 into the C core plus a port of the real `devEpidSoft.c` reproduces those commands to within 0.003-0.006 SLPM, so the live IOC did what its code says.
  - **Simulated, had the IOC kept running:** the flow would have bottomed at 0.14-0.18 SLPM, O2 reached 0.99 % in about 1 h, peaked at 0.993 % with no real overshoot, and then held at about 0.26 SLPM. Today's need is 0.255-0.27 SLPM, so the user's 0.24 lets O2 drift to 1.05-1.10 %.
- **Its autosave:** that run wrote `autosave/` on the host with every default of the time, including `dropSkipLevel` 18. The user should delete it before the next start.
- **Log text:** "IOC started (autosaved settings restored)" is printed even when no `.sav` existed. To fix (wording only).

**Archive data (read before trusting any export)**
- **The PV:** "oxygen level rbv" in the archive IS `15IDC:D1Dmm_calc`.
- **Raw sampling, from the archiver's own PV config:**
  - O2 was about every 5 s from 2026-08-25T20:28Z to 2026-09-24T18:17:45Z, and about every 1.005 s since.
  - Flow_RBV is about every 10 s throughout.
- **Resampled exports:** the "1Hz 10d" exports were resampled by the archiver (mean_1, forward-filled). Their Flow_RBV is mostly filled values, and part of their repeated O2 values are resampling artefacts. Use the RAW files and `HANDOFF_archiver_pulls_2026-10-01.md` in `o2-purge-plan2\analysis\data\`, and the `pv-retrieval` skill.
- **Sensor rate:** on 2026-10-01 the user raised the O2 IOC's update rate to 2 Hz.

**Lid-open O2 (116 h of 1 Hz data)**
- **Baseline:** median 19.29 %. Below 18.4 % for 0.46 % of the time (6 dips), below 18 % three times, below 17 % only once (42 s during a lift).
- **Skip level:** `dropSkipLevel` is now 17 % (user's decision, pushed `46fb936`). No archived purge started between 7 and 18.6 %.
- **Unexplained:** on 25 Sep from 13:43 to 14:14, O2 sat at 17.6 % with the flow off (lid ajar? asked).

**Lid check (spec §8.9): the no-false-stop question**
- **Coverage:** 48 purges at 20 SLPM in 10 days, all with the lid on; none judged open.
- **Margins:** curvature, not ratio, is the binding test. The minimum curvature margin was +0.023 (30 Sep 12:51), against +0.49 for ratio.
- **Missed-update sensitivity (old check, resampled data):** with missed updates added at random, false stops ran 0.9 / 1.7 / 3.1 % at 10 / 14 / 20 % missed. An O2 PV updating every 10 s would be judged open on every purge.
- **User's decision (1 Oct):** least-squares slopes that skip repeated samples, and `lidWindow` 15 → 30 s. A new parameter `lidMinSamples` (4): with too few updates, the check falls back to ratio only.
  - **Committed** on `ioc-fixes` (`3f228a8`, `99cb704`, `0523246` and later), but **not pushed**.
  - **Validation in progress** against the raw O2: the real gaps, the real purges, and the missed-update Monte Carlo.
  - **Golden traces:** they must be regenerated with a backup first (they are untracked). Do that, then push.
- **Detection untested:** the record has no open-lid 20 SLPM purge, so open-lid detection on real data is untested; a supervised test is for the user.

**Regulation findings (to decide later, not changed)**
- **Keep the integral:** the current integral approaches the target from below well. Integrating only near the target leaves O2 stuck below target with no alarm. Switching the integral off while approaching doubles the time to target.
- **Optional:** a floor at 0.5× the expected flow after a restart in the post-purge dip, and `fbDelay` 60 s at the handoff (removes a 10-20 s spike to about DRVH).
- **Bumpless seed:** it is bumpless only for O2 between about 0.91 and 1.01 % (the integral term clamps). The fix would be to ramp the PID target from the current O2. Entering the fine band jumps the flow by about +0.05 SLPM (the user is not worried).
- **Flow needed at 0.99 %, normal lid:** median 0.254 SLPM (0.20-0.37). Mode A's defaults fit.

**Done on 1 Oct (merged into `ioc-fixes`, pushed)**
- **Lid check** made robust (least squares, 30 s window), validated on raw data; golden traces regenerated (backup in the session scratch).
- **O2 update diagnostic** (`ioc-o2diag`): `Diag:O2Fresh10m/24h`, `MaxGap10m/24h`, `Interval`, `Ticks10m`, `Disconn10m` on the Admin screen.
- **Missed-update robustness** (`ioc-robust`): the audit plus the offline stress harness (`sgReplay` stress mode, `ioc/test/stress_o2.py`, `make_traces --o2-drop`), and the plant sim's `--o2-drop/--o2-jitter`. Four fixes, neutral without repeats:
  - "no onset" is judged on a fresh sample;
  - `lidFit` drops a repeated onset sample;
  - Pinned and the minimum-flow note allow ODEL;
  - `aboveCount` counts fresh readings only.

  False stops under stress went from 19 to 0. Bench with 15 % drops (sc01/03/10/15) gives the same decisions as clean. On the merged build: test_fixes 9/9, unit 59/59, glue 36/36, replay 19/19.
- **Open decisions for the user:**
  - should `frozenTime` scale with the O2 update period, and should regulation resume by itself after a frozen O2 recovers? (Only a slow PV can cause this.)
  - FlowHigh can come up to 300 s later under missed updates (never earlier).

## 0a. State on 2026-09-30 (supersedes 0 where they differ)

- **Everything in 0's "still to do" 1-4 is done:** merged; bench regression all green (new
  `ioc/test/test_fixes.py`, 7/7); `test_compare` sc01/02/03/08 pass; short scenario set 7/7; long
  set running since 00:15 (log `ioc/test/results/long-set-20260930.log`; 8/10 passed by 11:00).
  The bumpless PID start is scoped to restarts and Resume Flow (spec §8.11, `4a143a0`).
- **GitHub `main` = `3ba65b9`,** then this update. The user said "keep pushing": push each set of
  `ioc-fixes` changes as a squash behind the scan gate.
- **Host compiler:** `make` ran on chemmat-C92, whose `/usr/bin/gcc` is 11.5 and whose base
  `CONFIG_SITE` has no `-std`/`-ansi`. The errors were gcc ≤ 4.4's (no columns, "used outside
  C99 mode"), so they came from a cross target that base is built for, compiled by an old cross
  gcc. Fixed: `CROSS_COMPILER_TARGET_ARCHS =` in `configure/CONFIG_SITE` (host only). Checked in
  WSL with base told it has a cross target: the old tree builds `O.linux-x86_64-debug` and
  fails, the new one builds host only, and its tests pass.
- **Not tested yet** (bench-regression concerns): an Alicat-only outage with the O2 still up (the
  plant sim serves both from one process); a second writer that lands late in the PID period;
  D1a, D3, G3.

- **Branches.** `ioc-fixes` (worktree `Documents\Claude Locals\o2-purge-fixes`) is now the most complete. It contains all of `ioc-plan2` plus:
  - the conformance-audit fix round (`docs/ioc/CONFORMANCE.md` §2, with the user's decisions G1, G2 = MAJOR, G3 and D5);
  - the setpoint-follow ramp allowance;
  - the production paths in `configure/RELEASE` (committed by the user, `7fa7342`);
  - the simplified sparse-clone `INSTALL.md`;
  - `-std=gnu99` in the Makefile: the production host's gcc defaults to gnu89 and stopped on `sgFmt.c`.
- **Not yet merged:** `ioc-plan2` has the uncommitted `ioc/test/test_compare.py` of the Plan 4 Task 3 agent (the noise-free comparison, still running at 17:40). When it reports, commit its work on `ioc-plan2`, then `git merge ioc-plan2` in the `ioc-fixes` worktree, and continue there.
- **GitHub `main` = `d7f2145`:** a squash of `ioc-fixes` (it doesn't have test_compare.py). Publish only by the squash-and-scan procedure (step 10 below and the `github-access-dave-j-w` skill).
- **Verified on `ioc-fixes`:** unit tests 45/45, glue 30/30, replay 19/19 exact (Windows and WSL Linux, gcc 11.4, the production module versions), the WSL smoke start, and the INSTALL steps as written in WSL.
- **Still to do, in order:**
  1. The merge above.
  2. Rebuild on Windows (the running bench IOC is plan2's install; fixes has its own).
  3. The bench regression listed in `.superpowers/sdd/conformance-fixes/report.md` ("Bench tests to run after the merge"): a second writer, G1 kill mid-purge, D2/D8 during the wait, the shadow alarm, a plant-sim dropout, test_tickage, test_pvnames, test_write_enable.
  4. The full scenario suite on the final build (short set, then long set, about 13 h).
  5. Tell the user before they go Live on the host.
- **Host install:** the user is installing per `INSTALL.md`. Their `make` failed in C89 mode (fixed by `-std=gnu99`, pushed). We asked them which gcc `make` uses: it looks like gcc 4.x, not the reported 11.5.

## 1. Where things are

| What | Where |
|---|---|
| Integration branch (everything merged) | `ioc-plan2`, worktree `Documents\Claude Locals\o2-purge-plan2` |
| Other worktrees (done, merged into ioc-plan2) | `o2-purge` (ioc-plan1: core), `o2-purge-plan3` (plant sim), `o2-purge-plan5` (screens) |
| Plans 1-5 | `docs/superpowers/plans/2026-09-25-ioc-plan{1..5}-*.md` |
| Progress ledgers with every ruling | `.superpowers/sdd/<plan>/progress.md` in each worktree (gitignored) |
| Controller core (C) | `ioc/lssSampleGas/lssSampleGasApp/src/sgCore.c` (+ sgChecks, sgPid, sgLedger) |
| IOC glue / SNL / write gate / start-wait | `src/sgIoc.c`, `src/sampleGas.st`, `src/sgGate.c`, `src/sgStart.c` |
| PV table → db, .req, screens | `ioc/tools/sg_pvs.py`, `gen_db.py`, `gen_screens.py` (`--check`) |
| Start-ups | `iocBoot/iocLSS_sample_gas/st.cmd` (bench), `st.cmd.pc` (this PC vs real PVs), `st.cmd.production` + `startLSSSampleGas` (Linux) |
| Scripts | `ioc/tools/build.sh`, `run_ioc.sh` (bench), `run_ioc_pc.sh` (PC), `replay.sh`, `check_ioc.py` |
| Plant simulator + tests | `ioc/test/plant_sim.py`, `ioc/test/README.md`, `test_plant_*.py`, `bench.py`, `scenarios.py`, `test_scenarios.py` |
| Screens, alarms, archive list | `ioc/screens/*.bob`, `sampleGas_alarms*.xml`, `archive_pvs.txt`, `pc_phoebus.ini` |
| Release notes, production loadout | `ioc/RELEASE.md`, `configure/RELEASE`, `configure/RELEASE.local.production.example` |

## 2. State at handoff

- **Done and verified:**
  - Core: replay of 19 reference scenarios is exact, including at epoch-time offsets. Unit tests: core 35, glue 24, fmt.
  - Plant simulator: 45+ tests.
  - Database and screens: generated from one table, with tests.
  - IOC: builds and runs on the bench.
  - Real hardware, from this PC: read-only phase OK, and one supervised gas-off write test OK. Purge → flow mismatch → OPEN_STOP → Flow Zero, Alicat back at 0.
- **Committed but only unit-tested (not yet run in an IOC):**
  - Fix round 1 safety changes:
    - write target pinned (PC trial only)
    - READONLY handling
    - forced shadow at the PC start
    - PC-IOC pidfile/duplicate guard
  - Production-acts changes (78fef47):
    - wait for a late Alicat with a MAJOR alarm instead of IDLE
    - writeEnable default 1
    - trial-only locks
    - "not configured" alarm
- **Running at handoff:** a Plan 4 agent (acceptance) on the bench, IOC port 5076, simulator 5066. It was to:
  1. run the short scenarios (2, 3, 8, 9, 12, 13, 14);
  2. stop its IOC and do a full rebuild with all of the above;
  3. rerun the unit tests and the replay;
  4. run the PC-mode safety check and the wait-for-Alicat check on the bench;
  5. start the long run (1, 4, 5, 6, 7, 10, 11, 15, 16, 18; about 12 h).

  Its report: `.superpowers/sdd/2026-09-25-ioc-plan4-acceptance/task-1-report.md`; results in `ioc/test/results/`. **Read these first.** If the agent died, repeat its steps 2-5.
- **The PC IOC is stopped.** Do not restart it until step 4 above has passed on the bench.
- **Update, 2026-09-28 18:00:** steps 1-5 above are done.
  - The short run passed 7/7. State-change times were within 1-2 s of the reference, and sc14 was a real kill and restart.
  - The full rebuild passed: unit tests 35/35 and 24/24, replay 19/19.
  - The PC-mode safety check passed 20/20: wait-for-Alicat, READONLY, FORCE_SHADOW, WRITE_MFC and console.
  - The **long run started 17:51:54**, ending about 07:00 next day. Runner PID 37496; results in `ioc/test/results/run-long-20260928-175154.out`, unittest summary in the `.log` next to it; stop it with `taskkill /PID 37496 /T /F`.
  - The PC IOC may now be restarted (read-only or `--allow-writes`), even while the long run uses the bench: the bench is on ports 5076 and 5066, the PC IOC on 5064. Do not relink the IOC exe while either of them runs.
- **Open IOC-side findings from the acceptance run** (fix with tests, then rerun the affected scenarios):
  1. **Restart bump: fixed 2026-09-29** (the user chose a fully bumpless start). The first PID step after a restart moved the setpoint by KP·e: +0.044 SLPM in sc14, against the §14.3 criterion of ±0.02 SLPM.
     - Now, on epid's FBON 0 → 1 the glue writes `PID:Out = clamp(lastCmd − KP·e, DRVL, DRVH)` and ODEL 0 for that one processing, so the first output is lastCmd. Code: `sg_pid_bumpless_i` in `sgPid.c`, used by `epidStep` in `sgIoc.c`. Spec: §8.11 step 3.
     - Tests: unit test `F3b` against a devEpidSoft first-step model; replay still 19/19 (it models epid's plain start, as the reference).
     - Bench: sc14 PASS (2026-09-29). The first step after the restart held 0.27 → 0.27, and after the preset 0.25 → 0.25 (before the fix: +0.044 and −0.05).
     - Left as is (the user isn't worried about the early steps): the *second* step after a start can still move, e.g. 0.27 → 0.31 in sc14. The fine band is judged on the previous step's CVAL, which is unknown at the first step. So the fine-band KP (×0.5) first applies at step 2, while I was seeded with the full KP. The reference model does the same. A fix would be to judge the fine band on the current CVAL at a start; that would need a KNOWN_DIFFS entry in the replay.
  2. **sc08 mismatch spike: fixed 2026-09-29.** A 1 s MAJOR `flow mismatch` appeared right after the hold resumed; the reference had none. The cause was as suspected: the unchanged setpoint didn't restart the mismatch timer, so its ramp allowance had long expired.
     - Now, `holdMonitor` (`sgCore.c`) sets `spChangeT = now` and `flowAtSpChange = Flow_RBV` when it re-sends the setpoint. Spec: §8.7 and §8.14.
     - Unit test `hold resume restarts the mismatch timer` fails without the fix and passes with it; replay still 19/19.
     - Bench: sc08 PASS (2026-09-29). The log after the resume shows only the MINOR override; no mismatch.
  3. **Spurious `totalizer went backwards` MINOR after a Cfg:MFC change: fixed 2026-09-29.**
     - Cause: Cfg:Apply runs mid-tick, after the inputs are read, so that tick's `sg_tick` re-baselined on the OLD Alicat's `Total_RBV`. A lower new total then logged "went backwards"; a higher one would have been counted as usage.
     - Now, `sg_channels_changed` drops the old reading (`c->in.total = NAN`). Spec: §8.21 step 5.
     - Unit test `F4b MFC change mid-tick` (the glue's order, lower and higher new totals) fails without the fix and passes with it; replay still 19/19.
     - Bench: not rerun. The 14.3a PV-name test (Plan 4 Task 4) will cover it.

## 3. To finish (in order)

1. **Acceptance results.** Fix every failure at its root cause. IOC-side defects go in the glue or core with a test. Rerun the replay after any core change.
2. **Runtime checks of the new behaviour** (if not done by the Plan 4 agent):
   - READONLY 0/1
   - FORCE_SHADOW start
   - WRITE_MFC mismatch refused (PC trial only)
   - the wait for a late Alicat: start the IOC before the simulator; the MAJOR alarm, then the §8.15 decision
   - "not configured" alarm
   - pidfile and duplicate guard
   - the interactive console under `run_ioc_pc.sh`
3. **Quantitative comparison and restart/PV-name/write-enable tests** (Plan 4 Tasks 3-4; spec §14.2-§14.3b).
   - **Task 4 done 2026-09-29:** `ioc/test/test_pvnames.py` (§14.3a) and `test_write_enable.py` (§14.3b + §14.5): OK. They take 877 s together. Report: `.superpowers/sdd/2026-09-25-ioc-plan4-acceptance/task-4-report.md`.
   - **IOC defect found and fixed:** after a Cfg:MFC change, a restart before the next 300 s helium autosave restored the old Alicat's totalizer state (+115.7 L on the bench). The fix saves at once (`a8d0c0b`, glue test C8, sgIocTest 25/25).
   - **Harness fix:** `Bench.watch` (`6e30c09`).
   - **Still open:** Task 3, the noise-free quantitative comparison with the reference.
4. **Heartbeat staleness: done 2026-09-29.**
   - `Sts:TickAge` (calc, SCAN 1 second, `A#B?0:C+1`, HIHI 10 MAJOR) plus `Sts:HbLast` (the previous heartbeat, updated by TickAge's FLNK), in `sg_pvs.py`.
   - TickAge is in both alarm XMLs. A red "controller not ticking" overlay covers the banner on both panels when TickAge > 10.
   - Spec §7.1 and §13.4.
   - Bench test `ioc/test/test_tickage.py` (seqStop with the IOC up): OK.
5. **Screens rendered** (Plan 5 Task 3): the user launches a separate Phoebus.
   - Command: see the `phoebus` skill. Settings: `ioc/screens/pc_phoebus.ini`, with the macro `P` set there.
   - Check all four displays in IDLE, REGULATE and with an alarm.
   - Open both confirmation dialogs and cancel them.
   - Check the 30-min trend and the dead-IOC colours.
6. **Documentation (2026-09-29, done):**
   - `ioc/README.md`: build, bench, tests, screens, PC trial, production install.
   - `docs/ioc/OPERATOR_GUIDE.md`: screens, tasks, states, alarms, shadow mode, IOC down, Admin, Deep admin; screenshots in `docs/ioc/img/`.
   - The screenshots are made by `ioc/test/doc_session.py`, which runs the bench and `ioc/tools/doc_screens.py`. The latter runs its own Phoebus instances, one per display, on ports 4994–4997, placed on a portrait monitor if there is one. It never saves a shot that shows the Phoebus frame, whose status bar shows the account name.
   - Rerun the session after any screen change. The confirmation dialogs aren't captured; the guide quotes them.
7. **Gas-on test procedure** (Plan 2 Task 6 Step 4). It is now `docs/ioc/INSTALL.md` §6, for the IOC host, so no separate `ioc/HARDWARE_TEST.md` is needed. The items below, beyond the basic sequence, are still for the user to choose.
   - Purge from air → handoff → regulation.
   - Flow Zero.
   - Restart in REGULATE: a bumpless resume.
   - Then, as the user decides: open-lid purge (confirm the lid-check thresholds), surface-vibration ceiling (`hardCeiling`/`drvh`), bump tests.
   - Stop condition for each: Flow Zero, then Shadow, then `exit`.
8. **Spec-conformance audit** (user request): a table, spec §5-§14 → file:line or test → met or deviation. Fix each deviation, or write it into the spec as a user decision. Then the final whole-branch review of `ioc-plan2`.
9. **Regenerate the spec PDF** (`tools/md2pdf.py`, run from PowerShell) — it is stale.
10. **Publishing (first push done 2026-09-29, `ff538a5` on `main`, as the user chose: one squashed commit).** To update GitHub later, never push `ioc-plan2` itself (its history holds the name). Instead:
    1. Scan the tree for the private names: the Windows account, the production account and the PC's host name. They are kept in the local memory, never in the repo, since a file naming them would itself leak them. Use `git grep -I -i -E '<names>' HEAD`, and for binaries `git show HEAD:<f> | grep -a`. **Stop on any hit: don't push.**
    2. Build the squash: `NEW=$(git commit-tree 'HEAD^{tree}' -p origin/main -F msg.txt)`.
    3. Check its author is the noreply address, then `git push origin $NEW:refs/heads/main` and `git branch -f main origin/main`.

    Push only with the user's go. The original instruction, kept for the record: **Merge and push** only when the user says so. Branch `ioc-plan3`'s history contains the Windows account name in commit `e61328b` (a plan file). The pushed history must not contain it: squash-merge into `main`, or rewrite that commit, then grep `git log -p` for the name before pushing to the public `Dave-J-W/helium-flow-fb`.

## 4. Production install (Linux soft-IOC host, spec §11, §15)

1. **Get the code there** (2026-09-29, `docs/ioc/INSTALL.md` §1). Make a sparse `git clone` of the repo into `/home/chem_epics/chemmatCARS/synApps/support/lssSampleGas`, checking out only `ioc/lssSampleGas` and `ioc/screens`. The EPICS top is then `lssSampleGas/ioc/lssSampleGas`. Updates are `git pull` there. The user asked for a direct clone instead of copying.
2. **No `configure/RELEASE.local` needed.** Since 2026-09-29, at the user's direction, `configure/RELEASE` carries the production host's real paths: `SUPPORT=/home/chem_epics/chemmatCARS/synApps/support`, base 7.0.8.1 at `/usr/local/epics/base`, seq R2-2-9, std R3-6-4, calc R3-7-5, asyn R4-44-2, autosave R5-11, sscan R2-11-6. `RELEASE.local.production.example` is only for a moved tree. No `CONFIG_SITE.local` is needed there: that is only for the bench's space-in-path problem.
3. **Build:** `make` in the top (gcc 11.5, linux-x86_64). It builds without warnings with gcc 11.4 and the same module versions in WSL (`ioc/tools/linux_build_check.sh`). The user reports a `make` problem on the host whose error text is still to come.
4. **The `start_ioc` table line** (the user or beamline staff add it):
   `15LSS_sample_gas   20125   1  /home/chem_epics/chemmatCARS/synApps/support/lssSampleGas/ioc/lssSampleGas/iocBoot/iocLSS_sample_gas/startLSSSampleGas`.
   Check that port 20125 is still free in `IOCLIST`.
5. **First start:** `start_ioc 15LSS_sample_gas`. It acts from the start (writeEnable default 1). On the first start with no autosave file:
   - it reads 15IDC:Alicat1: and 15IDC:D1Dmm_calc;
   - it enters IDLE if the setpoint is 0;
   - it waits under a MAJOR alarm if the Alicat IOC is not up yet.
6. **Stop the old timer script** before the IOC takes over the Alicat. Two writers fight over the setpoint.
7. **Alarm server:** load `ioc/screens/sampleGas_alarms.xml`, the production file with 15IDC. Give the archiver the PVs in `archive_pvs.txt`.
8. **Screens:** open with macro `P=15IDC:SampleGas:` in the beamline Phoebus. This is the user's installation: they place the .bob files.

## 5. Rules that still bind

- **Writes:** only to `15IDC:Alicat1:Setpoint/RampRate/Run`, only through the IOC's write gate, and with the user present for hardware tests. The first write of a session needs the user's explicit go.
- **Reads:** of the live PVs are approved.
- **Production must act.** In production the failure to avoid is the controller silently not acting. Keep write locks to the PC trial only. When the controller cannot act, raise a banner alarm.
- **The user starts real-hardware IOCs.** Claude's permission check blocks it. Give the exact command, then verify from outside with `ioc/tools/check_ioc.py`.
- **The user's own Phoebus:** never touch it (a `java.exe` with the beamline `settings.ini`).
- **Committed files:** no account names, host names or site IPs. `ioc/tools/beamline_env.local.sh` (the beamline CA list) is gitignored.
- **Python:** `%USERPROFILE%\.venvs\bluesky\Scripts\python.exe` with `PYTHONIOENCODING=utf-8`.
- **MSYS2 scripts:** run them via `ioc/tools/msys.ps1` from PowerShell.
- **Heredocs:** never write code through them.
