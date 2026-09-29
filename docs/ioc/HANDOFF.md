# 15LSS_sample_gas IOC: handoff to finish and install (2026-09-28)

Read this first. General lessons (bench traps, Phoebus, production conventions, porting method)
are in the Claude skills `cmc-epics-ioc` and `reference-trace-replay-port`; this file is only
about finishing THIS project. The spec (`docs/ioc/15LSS_sample_gas_IOC_spec.md`) is the
authority; the user's decisions of 2026-09-28 are written into it.

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
7. **`ioc/HARDWARE_TEST.md`** (Plan 2 Task 6 Step 4): the gas-on procedure with the user.
   - Purge from air → handoff → regulation.
   - Flow Zero.
   - Restart in REGULATE: a bumpless resume.
   - Then, as the user decides: open-lid purge (confirm the lid-check thresholds), surface-vibration ceiling (`hardCeiling`/`drvh`), bump tests.
   - Stop condition for each: Flow Zero, then Shadow, then `exit`.
8. **Spec-conformance audit** (user request): a table, spec §5-§14 → file:line or test → met or deviation. Fix each deviation, or write it into the spec as a user decision. Then the final whole-branch review of `ioc-plan2`.
9. **Regenerate the spec PDF** (`tools/md2pdf.py`, run from PowerShell) — it is stale.
10. **Merge and push** only when the user says so. Branch `ioc-plan3`'s history contains the Windows account name in commit `e61328b` (a plan file). The pushed history must not contain it: squash-merge into `main`, or rewrite that commit, then grep `git log -p` for the name before pushing to the public `Dave-J-W/helium-flow-fb`.

## 4. Production install (Linux soft-IOC host, spec §11, §15)

1. **Get the code there.** After step 3.10, `git clone` from GitHub; or copy a tarball of the `ioc/lssSampleGas` top. Put it at `<support>/ChemMat/lssSampleGas/` (its own top; the user decided this).
2. **`configure/RELEASE.local`:** copy `RELEASE.local.production.example` and set `SUPPORT=<synApps support dir>`. `configure/RELEASE` already lists the production versions (base 7.0.8.1, seq R2-2-9, std R3-6-4, calc R3-7-5, asyn R4-44-2, autosave R5-11, sscan R2-11-6). No `CONFIG_SITE.local` is needed there: that is only for the bench's space-in-path problem.
3. **Build:** `make` in the top (gcc 11.5, linux-x86_64). The code is plain C17, but it has never been compiled with gcc 11. Expect to fix small warnings; C23 features are not used.
4. **The `start_ioc` table line** (the user or beamline staff add it):
   `15LSS_sample_gas   20125   1  <support>/ChemMat/lssSampleGas/iocBoot/iocLSS_sample_gas/startLSSSampleGas`.
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
