# 15LSS_sample_gas IOC: spec-conformance audit (§5-§14)

| | |
|---|---|
| Date | 2026-09-29 |
| Branch / commit audited | `ioc-plan2` at `eeeb7d0` (the working tree also had an uncommitted `ioc/test/ref/make_traces.js` from the Plan 4 Task 3 agent; it is not part of the IOC) |
| Authority | `docs/ioc/15LSS_sample_gas_IOC_spec.md` §5-§14, with its dated user decisions |
| Method | Read-only. Code, database, start-ups, screens and tests were read and compared with the spec. Prescribed texts were compared character by character. Nothing was built or run. |
| Weighting | The user's production rule: the failure to avoid is the controller silently **not** acting. Write locks are for the PC trial only. A controller that cannot act must say so on the banner and to the alarm server. Silent inaction ranks with unsafe writes. |

## Summary

**140 requirements:** 112 met, 13 deviation, 13 untested, 2 n/a.

There are also **3 spec gaps**, marked G1-G3. In each, the code does what the spec says, but the spec breaks the production rule. Their table rows count as met.

The core (`sgCore.c`, `sgChecks.c`, `sgPid.c`, `sgLedger.c`, `sgAlarms.c`) replays the 19 reference traces exactly. All the core texts I compared match the spec. So the findings are in the glue, the start-up paths, the test evidence and the spec itself.

**Top five findings**

1. **A failed Alicat write is silent (D1).** A failed `pvPut` logs one MINOR line and raises no alarm. The flow-mismatch check compares `Flow_RBV` with the Alicat's own `Setpoint_RBV`, so a setpoint that never arrived looks healthy. FLOW_ZERO and OPEN_STOP send 0 only once, on entry. If that one put is lost, the panel reads "Flow stopped" while helium keeps flowing.
2. **An IOC restart during a purge ends in a silent IDLE at full flow (G1).** Spec §8.15 sends a restart with O2 ≥ `lidLevel` and `Setpoint_RBV` > 0 to IDLE. In IDLE the hold monitor, the lid detector and the mismatch check are all off, and the banner says `No alarms`. The Alicat stays at up to 20 SLPM indefinitely.
3. **Most acceptance evidence is older than the current PID start.** 15 of the 17 real-time scenario passes ran before `b2b846e` (the fully bumpless start, which changes every HANDOFF → REGULATE). Several other checks have never run:
   - §14.2's quantitative comparison (Task 3 is in progress);
   - §14.3's assertions that parameters and helium state are restored, and that the client raises a stale-heartbeat alarm;
   - any production build (gcc 11, autosave R5-11).
4. **Switching Live during the start-up wait breaks the "changes nothing" promise (D2).** If Live is switched on while the IOC is still waiting for the Alicat, the later restart decision can go to REGULATE or OPEN_LOOP and write with no operator press. That contradicts §8.20 and the confirmation text the operator just accepted. It can only happen with FORCE_SHADOW: the PC trial and the commissioning start.
5. **Two more "cannot act" states stay off the banner.**
   - Shadow mode (G2): `writeEnable` is autosaved, so a production IOC left in Shadow stays silent across restarts. It shows only as a badge on the panels.
   - The flow-units check (D3): §8.8 asks for a MAJOR alarm, but the code only writes a MAJOR log line.

## Legend

- **met**: the code does what the spec says, and a test (or the replay) exercises it. "met (structural)" means the property follows from a closed list in the code, and a test would add nothing.
- **deviation**: the code differs from the spec (D#). A spec gap (G#) is listed separately; its row counts as met.
- **untested**: implemented, but no automated test covers it (U#).
- **n/a**: not applicable.
- Paths are relative to `ioc/lssSampleGas/lssSampleGasApp/src/` unless they start with `ioc/`, `docs/` or `Db/`. `Db/` is `ioc/lssSampleGas/lssSampleGasApp/Db/`, and `iocBoot/` is `ioc/lssSampleGas/iocBoot/iocLSS_sample_gas/`.
- Bench run files are in `ioc/test/results/`. "Replay" means `sgReplay` 19/19 against `ioc/test/golden/sc*.trace`.

## 1. Requirement table

| spec § | requirement (short) | implemented at | verified by | status |
|---|---|---|---|---|
| 5.1 | Names: IOC `15LSS_sample_gas`, top `lssSampleGas`, app, binary/dbd `lssSampleGas`, iocBoot `iocLSS_sample_gas`, SNL program `sampleGas` | `Makefile` (PROD_IOC, DBD); `sampleGas.st:22`; `iocBoot/` | every bench run (e.g. bench-20260929-134412-ioc.txt) | met |
| 5.2 | Modules, dbd list (base, asyn, stdSupport, calcSupport, asSupport, SNL registrar), libraries | `Makefile` (lssSampleGas_DBD, _LIBS, sampleGasSupport_LIBS) | bench build 2026-09-28 17:48 (Plan 4 task-1-report §6) | met |
| 5.3 | `sampleGas.db` station template incl. mode slots; settings and helium `.req` | `Db/sampleGas.db`, `Db/*.req`, from `ioc/tools/gen_db.py` | test_gen_db test_check_exits_zero_when_up_to_date, test_every_spec_pv_is_in_generated_db | met |
| 5.4 | Macros P/MFC/O2/CYL/STN; the last four are only the defaults of `Cfg:*`; a saved name wins; RestoreDefaults copies the macros | `ioc/tools/sg_pvs.py:405-411`; `iocBoot/st.cmd.production`; `sgIoc.c:1100-1113` | test_pvnames (DEFAULTS, Restore defaults, applied names kept across a restart); test_gen_db test_macros_other_than_p_only_in_cfg_default_vals | met |
| 5.4 | Empty `Cfg:MFC`/`Cfg:O2` → stays IDLE, "not configured" | `sgIoc.c:873-877, 1419-1423`; `sgCfg.c:67-78` | test_pvnames (empty Cfg:O2); sgIocTest t_cfg_not_configured, t_start_decision | met |
| 5.5 | Start-up order: dbLoadRecords, iocInit, create_monitor_set 30 s / 300 s, seq | `iocBoot/st.cmd.production`, `st.cmd`, `st.cmd.pc` | every bench run | met |
| 5.5 | Parameters and names restored in pass 0; the SNL does not act before they are valid | `iocBoot/save_restore.cmd:15-18`; `sgIoc.c:816-856` (read at create, before the first tick) | test_pvnames (restart: applied names in use); test_write_enable (writeEnable 0 restored) | met |
| 5.5 | "The SNL gets only P"; names come from `Cfg:*` via pvAssign, never from st.cmd | `sampleGas.st:49-52, 92-102`; st.cmd lines pass LOGDIR, READONLY, FORCE_SHADOW (st.cmd.pc also WRITE_MFC) | test_pvnames | **deviation** (D13) |
| 6.1 | Alicat PVs = `Cfg:MFC` + fixed Alicat_BC.db suffixes | `sgIoc.c:235-238, 591-615` | test_pvnames (moves to SIM:Alicat2:) | met |
| 6.1 | Reads by monitor; connection and severity carried into the logic | `sampleGas.st:31-36, 105-116`; `sgIoc.c:988-1007` | bench sc10, sc11 (sc11-20260929-013607.json); test_write_enable (AnalyzerMode invalid) | met (see G3) |
| 6.1 | Writes only Setpoint, RampRate, Run, only from the SNL | `sampleGas.st:37-39, 76-80`; `sgIoc.c:238` | test_write_enable, test_pvnames (PutWatch on the three writable PVs); bench sc12 | met |
| 6.1 | Never reset the totalizer | `sgIoc.c:238`: the only put channels are Setpoint, RampRate, Run | none | met (structural) |
| 6.2 | O2: latest value and severity at each tick; disconnected or INVALID = invalid; frozen rule | `sgIoc.c:997-998`; `sgCore.c:331-354` | replay; bench sc10 | met |
| 6.3 | CYL optional; not used in any logic; "n/a" when empty | `sgIoc.c:1324` (publish only; the core never reads it) | bench runs with CYL empty (test_pvnames `CYL none`) | met (text: D12) |
| 7 | Par/Mode records with DRVL/DRVH = §9 limits, autosaved, read every tick | `ioc/tools/sg_pvs.py:153-194`; `sgIoc.c:728-759` | test_gen_db test_param_fields_match_spec | met |
| 7 | `Cmd:*` are bo, reset to 0 after acting | `sgIoc.c:1025-1031` | every bench command | met |
| 7 | `Alm:*` mbbi 0/1/2 with ZRSV/ONSV/TWSV, VAL 0 + PINI; `:Msg` lsi 256 | `ioc/tools/sg_pvs.py:230-238, 320-323` | test_gen_db; test_tickage (severity read over CA) | met |
| 7.1 | `Mode` mbbo; state strings copied from `Mode:X:name` at start and on change; autosaved | `sgIoc.c:713-723, 741-752`; `Db/sampleGas_settings.req` | bench sc06 (mode change); test_gen_screens test_mode_names_base_flow_and_n | met |
| 7.1 | `Par:target` 0.2-5, autosaved; a change in REGULATE starts settling | `ioc/tools/sg_pvs.py:20`; `sgCore.c:239-245` | replay (sc16); bench sc16 | met |
| 7.1 | `Cmd:Purge`, `FlowZero`, `ResumeFlow`, `NewCylinder` | `sgIoc.c:1116-1138` | bench scenarios; test_write_enable | met |
| 7.1 | `Sts:State`, `StateDesc` (reference STATES texts), `LastAction`, `Progress` | `sgIoc.c:1305-1313`; `sgCore.c:11-23, 732-767` | replay (state, lastAction); test_pvnames (StateDesc) | met |
| 7.1 | `Sts:InRange`, `Sts:O2` (NaN/INVALID when invalid), `Sts:O2Valid` | `sgIoc.c:1314-1316` (NaN → UDF/INVALID in aiRecord) | test_pvnames (Sts:O2 follows), test_write_enable (O2Valid 0) | met |
| 7.1 | `Sts:ExpectedFlow`, `LastCmd` (NaN before the first command), `Flow` (INVALID when disconnected), `SetpointRBV`, `MfcRunning`, `MfcStatus` | `sgIoc.c:1317-1322` | test_write_enable (LastCmd), test_pvnames (SetpointRBV) | met |
| 7.1 | `Sts:Heartbeat` +1 every tick (also while waiting or not configured) | `sgIoc.c:1381, 1402` | test_tickage; Bench.next_tick | met |
| 7.1 | `Sts:TickAge` calc, SCAN 1 s, HIHI 10 MAJOR, database-only; `Sts:HbLast` via FLNK | `ioc/tools/sg_pvs.py:297-303` | test_tickage (tickage-20260929-121901) | met (MAJOR at ≥ 10 s, spec says "> 10 s": immaterial) |
| 7.1 | `Sts:Banner` (most severe first, `"; "`, or `No alarms`); `Sts:WorstSevr` | `sgAlarms.c:108-133`; `sgIoc.c:1378-1380`; `ioc/tools/sg_pvs.py:304` | bench (results `banner`) | **deviation** (D7) |
| 7.1 | `Sts:WriteEnable` mirrors `Par:writeEnable` | `sgIoc.c:1323` | test_write_enable | met |
| 7.1 | `He:LeftL`, `EmptyMin/Max/MedianH`, `ForecastText` (both prescribed texts) | `sgLedger.c:287-296, 301-371`; `sgIoc.c:1238-1248` | replay (H lines); sgUnitTest t_forecast_text, t_cyl_minmax | met |
| 7.1 | `Sts:CylPressure`: copy of CYL, else "INVALID with text n/a" | `sgIoc.c:1324` (NaN only) | test_gen_screens test_cylinder_pressure_line | **deviation** (D12) |
| 7.2 | Every §9 key as `Par:<key>`, DRVL/DRVH, VAL = default in the db, integers longout, `writeEnable` bo | `ioc/tools/sg_pvs.py:19-169` | test_gen_db test_param_count_matches_spec, test_param_fields_match_spec | met |
| 7.3 | `Cmd:ReleaseIdle`, `MarkNewRun`, `ResetOverrideCount` | `sgIoc.c:1126, 1131-1135` | test_pvnames (ReleaseIdle, MarkNewRun); sgUnitTest t_mark_new_run (also resets the count) | met |
| 7.4 | Mode slots A-D: PVs, §9.2 limits, §7.4 defaults, autosaved | `ioc/tools/sg_pvs.py:174-194`; `Db/sampleGas_settings.req` | test_gen_db; test_gen_screens test_mode_names_base_flow_and_n | met |
| 7.5 | The 14 alarms with their levels | `sgCore.h:60-62`; `sgIoc.c:228-231` | replay (keys and levels); test_gen_db | met |
| 7.6 | `Diag:*` (lid detector, hold, settling, gating, purge, overrides, OverrideLog `hh:mm:ss  text`) | `sgIoc.c:1212-1228, 1327-1355` | test_gen_screens test_admin_displays; replay (the core fields) | met |
| 7.6 | `Log:Text`, newest first, `YYYY-MM-DD hh:mm:ss  <STN>  [MINOR\|MAJOR]  msg`, ~200 lines | `sgLogFile.c:43-102`; `ioc/tools/sg_pvs.py:356` | sgIocTest t_logfile | met |
| 7.7 | `He:*` ledger scalars, arrays (NELM 6000/4000/2000), report PVs, `He:Rep:Text` | `ioc/tools/sg_pvs.py:360-393`; `sgIoc.c:1230-1292`; `sgText.c` | replay (report); test_pvnames (CumL, LastTotal, CylBase, HistUsed); sgIocTest t_report_text | met |
| 7.8 | epid `PID` (Passive, INP/OUTL local NPP, KD 0, FMOD PID, PREC 4, EGU); `PID:CVAL`; `PID:Out` with no OUT link | `ioc/tools/sg_pvs.py:396-402` | test_gen_db; test_write_enable (PID:CVAL watched) | met |
| 7.9 | `Cfg:*` fields, Apply, RestoreDefaults, Default:* (not saved), Active:*, Pending, Conn:*, Status; only the 4 fields autosaved | `ioc/tools/sg_pvs.py:405-417`; `Db/sampleGas_settings.req` | test_pvnames | met |
| 8.1 | 1 Hz tick at whole wall-clock seconds, drift-compensated (epicsTime, not accumulated delays) | `sgIoc.c:966-985`; `sampleGas.st:72-83` | test_bench test_start_tick_stop; test_tickage | met |
| 8.1 | Late ticks caught up (≤ 60 s), skipped beyond; wall clock stepping back | `sgIoc.c:966-985` | none | **untested** (U8) |
| 8.1 | Inputs sampled once per tick (one snapshot) | `sampleGas.st:23` (`+s`), `:74`; `sgIoc.c:988-1007` | replay (core); bench | met |
| 8.1 | Tick order, steps 1-10 | `sgCore.c:592-615`; `sgIoc.c:1390-1466` | replay 19/19 | met (the §8.18 step `mfcLink` runs after step 2; the heartbeat is counted at tick start: no effect) |
| 8.1 | Commands after step 1, in the order FlowZero … Cfg:Apply, each reset | `sgIoc.c:1116-1138, 1444-1446` | bench | **deviation** (D9) |
| 8.1 | Parameter changes take effect at the next use | `sgIoc.c:728-759` (every tick) | bench sc16 (target), test_write_enable (lidLevel 2) | met |
| 8.2 | readInputs 1-8 (frozen, validity, history ≥ max(90, W+A+5), rate, level, average, O2Bad texts) | `sgCore.c:331-354` | replay; sgUnitTest t_o2_nan | met |
| 8.3 | `command(v)`: max(0, v), lastCmd bookkeeping, put | `sgCore.c:188-201` | replay; sgUnitTest t_command_nan | met |
| 8.3 | Writes only when enabled; one gated put function; no database link to any Alicat PV | `sgGate.c:39-86`; `sgIoc.c:444-446`; `sampleGas.st:76-80`; db has no MFC link | sgIocTest t_gate_* (10 tests); test_write_enable; test_gen_db test_macros_other_than_p_only_in_cfg_default_vals | met |
| 8.3 | FBON 1: `command(PID.OVAL)` after each step; FBON 0: `PID:Out` = lastCmd | `sgPid.c:73-77`; `sgIoc.c:1449-1451` | test_write_enable (first PID step); bench sc14 | met |
| 8.3 / 12 | A failed Alicat write is seen (§12: `pvPut(..., SYNC)` "so a failure is seen"; production rule) | `sampleGas.st:79`; `sgIoc.c:1477-1485` | none | **deviation** (D1) |
| 8.4 | Common entry actions (FBON 0, silent clears, pinned reset, settling, LastAction, `<prev> → <STATE> (<reason>)`) | `sgCore.c:249-267` | replay | met |
| 8.4 | State entry actions (PRECHECK clears; PURGE; HANDOFF ramp clamp + override text; OPEN_LOOP alarm text; FLOW_ZERO/OPEN_STOP ledger + command(0)) | `sgCore.c:268-301` | replay; bench sc12 (`handoff minimum`), sc10, sc11 | met |
| 8.4 | Per-tick handlers (HANDOFF phases and 30 s; REGULATE → OPEN_LOOP; OPEN_LOOP tracks) | `sgCore.c:533-549, 600-611` | replay; bench | met |
| 8.5 | Purge (not in PRECHECK/PURGE); FlowZero, ReleaseIdle always; NewCylinder; MarkNewRun + `admin: start of a new user run marked` | `sgCore.c:645-662`; `sgLedger.c:198-207, 278-282` | replay; bench sc04, sc05; test_pvnames | met |
| 8.5 | ResumeFlow from OPEN_LOOP → HANDOFF, `operator pressed Resume Flow` | `sgCore.c:674-682` | replay (KNOWN_DIFFS); bench sc10, sc11 | met |
| 8.5 | ResumeFlow from IDLE (configured, o2ok, o2 < lidLevel): REGULATE from Setpoint_RBV, no valve move at the switch | `sgCore.c:683-697`; `sgIoc.c:1123-1125` | test_write_enable (≤ 0.02 SLPM, no put at the switch); sgUnitTest t_resume_idle | met |
| 8.5 | The four rejection texts | `sgCore.c:675-699` | test_write_enable (O2 invalid; above the lid threshold); sgUnitTest t_resume_invalid, t_resume_idle_high, t_resume_mfc, t_resume_purge | met |
| 8.5 | Resume Flow in shadow mode works, without writing | the gate, `sgGate.c:59-70` | sgIocTest t_gate_shadow | met |
| 8.5 | `operator: O2 target → <v> %`, `operator: enclosure mode → <name>` | `sgCore.c:239-245, 706-711` | replay; sgUnitTest t_set_mode | met |
| 8.5 | Rejected commands are reset and logged | `sgIoc.c:1120-1135` | test_pvnames (`Purge ignored: not configured: …`) | **deviation** (D8) |
| 8.6 | Lid detector: arming, armed states, trip, text, handler skipped | `sgCore.c:366-380` | replay; bench sc03 | met |
| 8.7 | Hold monitor: retries, HoldStuck, re-send with processing, mismatch-timer restart, override text | `sgCore.c:383-413` | replay; bench sc08 (sc08-20260929-101646), sc09; sgUnitTest t_hold_resume_mismatch | met |
| 8.8 | PRECHECK steps 1-5 (Run, ramp clamp text, Gas, blind, reasons) | `sgCore.c:416-440` | replay; bench sc12; sgUnitTest t_precheck_hold, t_gas; PC trial on the real PVs 2026-09-28 (`iocBoot/logs-pc/`, no Gas alarm) | met |
| 8.8 | Optional: FlowUnits ≠ SLPM → MAJOR alarm `MFC flow units are <u>, not SLPM` | `sgIoc.c:1203-1209` | none | **deviation** (D3) |
| 8.9 | Purge timer, O2 lost → blind, timeout published, blind completion, lid check skip/onset/no onset/decision with their texts | `sgCore.c:450-512` | replay; bench sc02, sc04, sc05, sc11, sc18; sgUnitTest t_no_decay, t_blind_purge | met |
| 8.10 | Lag-corrected O2, handoff (text), timeout latch (text), `purgeTimeout()` | `sgCore.c:204-220, 513-530` | replay; sgUnitTest t_purge_timeout | met |
| 8.11 | Skip if avgBuf empty; configEpid (gain schedule, fine band with hysteresis, DRVH/DRVL) | `sgPid.c:20-55` | replay | met |
| 8.11 | Fully bumpless start (user decision 2026-09-29): `PID:Out` = clamp(lastCmd − KP·e), ODEL 0 once, on FBON 0 → 1 | `sgPid.c:63-69`; `sgIoc.c:1149-1157` | sgUnitTest t_pid_bumpless, t_pid_bumpless_seed; test_write_enable (IDLE resume); bench sc14-20260929-095218 (read by hand) | met |
| 8.11 | Process epid, then `command(OVAL)`: the only path to the Alicat | `sgIoc.c:1158-1189` | bench | met |
| 8.11 | A PID failure is loud (production rule; rulings R12 and "epid output is not a number") | `sgIoc.c:1173-1187`; `sgCore.c:190-193` | sgUnitTest t_command_nan | **deviation** (D4) |
| 8.11 | Bench-verify that the real epid matches the reference model | – | none (Plan 4 Task 3 in progress) | **untested** (U4) |
| 8.12 | startSettling, slope, settled, settle timeout, stalled, alarm gate (all texts) | `sgCore.c:223-236`; `sgChecks.c:48-91` | replay; bench sc16; sgUnitTest t_not_reached | met |
| 8.13 | expectedFlow = baseFlow·(0.99/target)^n | `sgCore.c:177-181` | replay | met |
| 8.14 | setAlarm, clearAlarm, latch, debounce, override | `sgAlarms.c:14-104` | replay | met |
| 8.14 | Flow mismatch (tracking, allowance, tolerance, text, silent clear outside) | `sgChecks.c:30-45` | replay; bench sc07, sc13 | met |
| 8.14 | REGULATE alarms FlowHigh/FlowLow/Pinned/O2High, flowSteady, minimum-flow note (all texts) | `sgChecks.c:92-121` | replay; bench sc06, sc15; sgUnitTest t_flow_high, t_flow_low, t_pinned_low, t_flow_steady | met |
| 8.14 | Publishing: `Alm:*` and `:Msg`, then Banner and WorstSevr | `sgIoc.c:1372-1380` | bench (results `banner`) | met |
| 8.15 | Step 0: read `Cfg:*`, connect; not configured → IDLE `restart: not configured` | `sgIoc.c:802-809, 1419-1423` | sgIocTest t_start_decision | met |
| 8.15 | Step 1: wait for O2 and Alicat, or 30 s; no time limit for the Alicat, with the MAJOR alarm `waiting for the Alicat PVs …: controller not acting yet` | `sampleGas.st:61-69`; `sgIoc.c:1410-1431`; `sgStart.c:14-31` | sgIocTest t_start_decision, t_start_wait_alarm; bench PC-mode check 2026-09-28 (task-1-report §6; its script is in a scratchpad, not in the repo) | met |
| 8.15 | Step 1: "log the wait" | `sgIoc.c:877` | PC-trial log 2026-09-28 | **deviation** (D10) |
| 8.15 | Steps 2-4: `IOC started (autosaved settings restored)` and the decision | `sgCore.c:623-640` | replay; bench sc14 and every preset restart; test_pvnames; test_write_enable; sgUnitTest t_restart_mfc | met (see G1) |
| 8.15 | Restored / not restored lists | `sgIoc.c:816-856`; `sgCore.c:126-145` | test_pvnames (names, helium), test_write_enable (writeEnable), presets (wait_autosaved Mode, target) | met |
| 8.16 | Every tick: CylBase unset, totalizer backwards (text), used, LeftL | `sgLedger.c:301-314` | replay; sgUnitTest t_total_backwards, t_total_nan | met |
| 8.16 | Every 60 s: usage log, five windows, lower median, CylLow texts | `sgLedger.c:315-370` | replay (sc17 check); sgUnitTest t_cyl_low | met |
| 8.16 | NewCylinder: ledger event, rebase, clear CylLow silently, text | `sgLedger.c:198-207` | replay (sc19); sgUnitTest t_new_cylinder_disconnected | met |
| 8.17 | Ledger per tick, events, litresAt, run grouping, report, sc19 validation | `sgLedger.c:22-193` | replay sc19 (Dispensed = Σ runs within 1 L); sgUnitTest t_ledger_seq; test_pvnames (CumL tracks the totalizer) | met |
| 8.18 | Alicat disconnected > holdDetect in a flow-owning state → Mismatch MAJOR `MFC not responding (CA disconnected)`; no writes meanwhile | `sgCore.c:560-575`; `sgGate.c:47`; `sgIoc.c:1006` | sgUnitTest t_mfc_disconnect (no runtime test: U3) | met |
| 8.18 | On reconnect, re-send lastCmd "as in §8.7" (short blips too) and log | `sgCore.c:576-586` | sgUnitTest t_mfc_blip | **deviation** (D6) |
| 8.19 | Reference message texts verbatim | core throughout | replay (L lines; 2 KNOWN_DIFFS, both specified) | met |
| 8.19 | Line format; monthly file `logs/sampleGas_<STN>_YYYY-MM.log`, flushed per line, survives restarts | `sgLogFile.c:43-86`; `sgIoc.c:811-814` | sgIocTest t_logfile, t_logfile_month_boundary, t_logfile_write_error; every bench test reads the file (`ioc/test/bench.py:329-346`) | met |
| 8.20 | `Par:writeEnable` bo, autosaved, default 1; st.cmd.pc FORCE_SHADOW + WRITE_MFC; production has neither | `ioc/tools/sg_pvs.py:88`; `iocBoot/st.cmd.pc`, `st.cmd.production`; `sgIoc.c:843-856` | test_gen_db; bench PC-mode check (task-1-report §6) | met |
| 8.20 | `startLSSSampleGasTest`: production database in shadow, own autosave directory | `iocBoot/startLSSSampleGasTest`; `st.cmd.production:21, 36` | none (never run on Linux) | **untested** (U6) |
| 8.20 | Shadow: no puts; `shadow mode: would write <PV> = <v>` on a change only; LastCmd visible | `sgGate.c:59-70` | test_write_enable; sgIocTest t_gate_shadow | **deviation** (D11, text only) |
| 8.20 | Safe by default: a restart into REGULATE with writeEnable 0 cannot write | `sgIoc.c:856`; `sgGate.c:78-86` | test_write_enable (two restarts in shadow, PutWatch) | met |
| 8.20 | 0 → 1: `writes enabled: Alicat left at <sp:.2f> SLPM`, IDLE, no put at the switch or in IDLE | `sgGate.c:99-102`; `sgIoc.c:1010-1023` | test_write_enable | met |
| 8.20 | 0 → 1 during the start-up wait still leaves the valve alone until an operator press | `sgIoc.c:1410-1443` | none | **deviation** (D2) |
| 8.20 | 1 → 0: state unchanged, MAJOR `writes disabled: shadow mode, Alicat holds <sp:.2f> SLPM` | `sgGate.c:103-109` | test_write_enable | met (see G2) |
| 8.20 | Both directions confirmed on the screen | `ioc/screens/sampleGas_admin.bob:159-208` | test_gen_screens test_write_enable_confirms_both_ways | met |
| 8.21 | At start: read, pvAssign, `Cfg:Active:*`, `Cfg:Status`, `PV names: MFC <..>, O2 <..>, CYL <.. or none>` | `sgIoc.c:802-864`; `sampleGas.st:92-102` | test_pvnames (after the restart) | met |
| 8.21 | Not configured: IDLE, no state machine, no puts, Status and StateDesc text, Purge/FlowZero/ResumeFlow rejected | `sgIoc.c:1035-1048, 1306-1310, 1445` | test_pvnames; sgIocTest t_cfg_not_configured, t_not_configured_alarm | met |
| 8.21 | Apply step 1: IDLE only; `rejected: release control first (state <STATE>)`, `PV name change ignored in <STATE>` | `sgCfg.c:50-56`; `sgIoc.c:1061-1065` | test_pvnames; sgIocTest t_cfg_reject_state | met |
| 8.21 | Step 2: trim; `PV names unchanged` | `sgCfg.c:58-63`; `sgIoc.c:1066` | sgIocTest t_cfg_unchanged, t_cfg_trim | met |
| 8.21 | Step 3: writes off on an MFC change in the PC trial only | `sgIoc.c:1072-1076` | test_pvnames (production: kept); PC-mode check | met |
| 8.21 | Steps 4-5: reset derived state, silent clears, ledger re-baseline, drop the old Total_RBV (+ immediate helium save) | `sgCore.c:152-173`; `sgIoc.c:1077-1083, 1457` | test_pvnames; sgUnitTest t_channels_changed, t_channels_changed_midtick; sgIocTest t_cfg_he_save | met |
| 8.21 | Step 6: status after ≤ 5 s (`applied <time>: all connected` / `not connected: <names>`), `operator: PV names → …` | `sgIoc.c:702-710, 1192-1201` | test_pvnames | met |
| 8.21 | Editing alone does nothing; `Cfg:Pending`; `Cfg:STN` = log label only | `sgIoc.c:1084-1085, 1360-1362` | test_pvnames (Pending) | met |
| 9.1 | 65 parameters: defaults, limits, levels (ramp clamps A-level 2026-09-29; writeEnable 1) | `ioc/tools/sg_pvs.py:19-89` | test_gen_db test_param_fields_match_spec; test_gen_screens test_each_parameter_on_its_screen | met |
| 9.2 | Mode-slot limits | `ioc/tools/sg_pvs.py:174-181` | test_gen_db | met |
| 9.2 | Effective DRVL ≤ DRVH | `sgPid.c:35-36` | replay | met |
| 9.2 | "KP must be negative" | `ioc/tools/sg_pvs.py:177` (max 0) | none | **deviation** (D5) |
| 10 | `sampleGas_settings.req` contents; saved on change (30 s) | `Db/sampleGas_settings.req` (65 Par, 28 Mode:*, Mode + 4 state strings, OverrideCount, 4 Cfg) | presets (wait_autosaved writeEnable/Mode/target); test_pvnames (Cfg) | met |
| 10 | `sampleGas_helium.req` contents; 300 s; `manual_save` after NewCylinder and MarkNewRun | `Db/sampleGas_helium.req`; `sgIoc.c:1127-1134, 1459-1464` | test_pvnames (MarkNewRun saves at once, checked in the .sav) | met |
| 10 | Restore in pass 0; arrays restore completely (bench) | `iocBoot/save_restore.cmd:15-18` | test_pvnames (restart: HistUsed, CylBase) | met |
| 10 | The same under production autosave R5-11 (manual_save, NELM 6000, Cfg strings in pass 0; §2.3) | `configure/RELEASE` | none | **untested** (U5) |
| 11.1 | Own top at `support/lssSampleGas/` on the Linux host (2026-09-30; was `ChemMat/lssSampleGas/`) | `docs/ioc/INSTALL.md` | built on the host, unit tests pass (user, 2026-09-30, at the old path) | **partly** (U6) |
| 11.2 | `configure/RELEASE` names only the modules used, at production versions; site paths in an uncommitted RELEASE.local; example committed | `configure/RELEASE`; `ioc/lssSampleGas/.gitignore:12`; `RELEASE.local.production.example` | none | met (structural) |
| 11.2 | Builds on gcc 11 / linux-x86_64 with the production modules; plain C17 | – | none (`ioc/tools/linux_build_check.sh` added at eeeb7d0; no result recorded) | **untested** (U5) |
| 11.3 | `start_ioc` line, port 20125; `startLSSSampleGas` is `#!/bin/bash`, `cd "$(dirname "$0")"`, executable | `iocBoot/startLSSSampleGas` (git mode 100755); `docs/ioc/INSTALL.md:168-169` | none (never run on the host) | **untested** (U6) |
| 11.3 | The event log survives start_ioc's log deletion | see §8.19 | as §8.19 | met |
| 11.4 | Existing screens and IOCs untouched | nothing in the repo edits them | – | n/a |
| 12 | Recommended structure (guidance) | – | – | n/a (not normative; its pvPut point is the §8.3/12 row) |
| 13.0 | Simple panel: contents, controls, mode shown not editable, not shown | `ioc/screens/sampleGas_simple.bob` | test_gen_screens test_exactly_the_13_0_pvs, test_not_shown, test_opens_full_panel, test_helium_left_in_days, test_shadow_badge; rendered (docs-20260929-110835) | met (see G2) |
| 13.1 | Only macro P; the Alicat only through the `Sts:` mirrors | `sampleGas_main.bob` | test_only_macro_p, test_no_direct_alicat_or_o2_pvs | met |
| 13.1 | Banner by WorstSevr, four readouts, state, controls, Resume rule and tooltip (`resume feedback from the current flow, without a purge`), LastAction, trend, badge `SHADOW MODE: no writes` | `sampleGas_main.bob` (Resume rule at 945-975) | test_13_1_pvs, test_controls, test_one_resume_flow_button_with_the_13_1_rule, test_trend, test_shadow_badge, test_banner_coloured_by_worst_sevr, test_state_label_colours, test_in_range_led | met |
| 13.2 | Admin: A-level parameters, per-mode KP/KI/drvh/drvl, controls, displays, Deep admin button | `sampleGas_admin.bob` | test_each_parameter_on_its_screen, test_admin_commands, test_admin_displays, test_admin_links_to_deep | met |
| 13.2 | Both confirmation texts, as prescribed (compared character by character: equal) | `sampleGas_admin.bob:184-192` and the shadow button | test_write_enable_confirms_both_ways | met |
| 13.2 | Phoebus actually shows the rule-built dialog text | – | none (HANDOFF §3 step 5 open) | **untested** (U7) |
| 13.3 | Deep admin: 49 parameters in nine groups, mode names/baseFlow/n, Cfg rows, Apply (confirm, IDLE only) and Restore, Pending, Status | `sampleGas_deep.bob` | test_nine_groups_in_order_with_keys, test_49_d_level_fields, test_linked_pv_rows, test_apply_and_restore_buttons, test_pending_and_status | met |
| 13.4 | Alarm config: all `Alm:*`, `Sts:Heartbeat` (delay 10 s), `Sts:TickAge` | `ioc/screens/sampleGas_alarms.xml:16-96` | test_gen_screens (alarm XML: test_production, test_bench) | met |
| 13.4 | The alarm server actually announces a dead or stalled IOC | – | none | **untested** (U2) |
| 13.4 | Red overlay on both panels while TickAge > 10: `MAJOR: controller not ticking (IOC up, program stalled): call the beamline staff` (equal) | `sampleGas_simple.bob:137-161`; `sampleGas_main.bob:153` | test_gen_screens (TickAge on both panels); test_tickage (the record) | met |
| 13.4 | Archive PV list | `ioc/screens/archive_pvs.txt` | test_gen_screens TestArchiveList | met |
| 14.1 | Plant simulator: Alicat_BC semantics, physics, world controls, seed/noise, ≤ 0.5 % validation | `ioc/test/plant_sim.py`, `ioc/test/plantsim/` | test_plant_model (6 cases against the reference), test_plant_server | met |
| 14.2 | 17 real-time scenarios pass on the current build | `ioc/test/test_scenarios.py` | sc*.json all `pass`, but 15 of 17 ran before `b2b846e` | **untested** (U1) |
| 14.2 | Scenarios 17 and 19 by golden vectors | `sgReplay.c:41-62` | replay | met |
| 14.2 | Quantitative comparison (sc 1, 2, 3, 8: ±3 s, ±0.02 SLPM, ±0.05) | – | none (Task 3 in progress) | **untested** (U4) |
| 14.3 | Restart test: REGULATE resumed, first-step bump ≤ 0.02, parameters and helium restored, client stale alarm | sc14 + `extra_checks` (`ioc/test/test_scenarios.py:83-94`) | sc14-20260929-095218: REGULATE and the held flow asserted; bump read by hand; restore and client alarm not asserted | **untested** (U2) |
| 14.3a | PV-name test | `ioc/test/test_pvnames.py` | pvnames-20260929-132924 | met |
| 14.3b | Write-enable and Resume Flow test | `ioc/test/test_write_enable.py` | writeen-20260929-134021 | met |
| 14.4 | Algorithm unit tests against the reference | `sgReplay.c` + `ioc/test/golden/*.trace`; `sgUnitTest.c`; `sgIocTest.c` | replay 19/19; sgUnitTest 35/35; sgIocTest 25/25 (HANDOFF) | met |
| 14.5 | Definition of done (bench build, unit tests, 17/17, restart/PV-name/write-enable tests, shadow across restart, screens render, simulator 19/19, README) | – | simulator unchanged since sim-v1.0 (`git diff sim-v1.0 -- simulator/` empty); `ioc/README.md` exists; the 17/17 and restart items are open (U1, U2) | **untested** (U1, U2, U4) |

## 2. Deviations

### A. The code differs from the spec (ranked by production risk)

**D1. A failed Alicat write raises no alarm: silent inaction. High.**
- **Spec:** §12 asks for "`pvPut(..., SYNC)` for the Alicat writes (so a failure is seen)". The production rule (spec §8.15 step 1, HANDOFF §5) says: "in production the failure to avoid is the controller silently not acting … When the controller cannot act, raise a banner alarm."
- **Code:**
  - `sampleGas.st:79`: `sgIocPutDone(stn, kind, pvPut(dOut[kind], SYNC, 2.0));`
  - `sgIoc.c:1480-1484` logs MINOR `put to <PV> failed (pvStat <n>)` once per failure streak. Nothing else happens.
  - The flow-mismatch check (`sgChecks.c:32-45`) compares `Flow_RBV` with the Alicat's own `Setpoint_RBV`. A setpoint that never arrived therefore looks healthy.
  - FLOW_ZERO and OPEN_STOP call `command(0)` once, on entry (`sgCore.c:296-297`). Their per-tick handler does nothing (`sgCore.c:609`).
- **Consequence:** one lost put leaves helium flowing while `Sts:StateDesc` reads "Flow stopped by the operator." or "Enclosure confirmed open: flow stopped to save helium".
  - A CA put can also succeed while the device write fails, for example on a stream timeout in the Alicat IOC. The put status alone would not catch that case either.
  - The same blind spot hides a second writer, such as the old timer script (INSTALL step 6: "Two writers fight over the setpoint").
- **Proposed resolution (fix the code, add spec text):**
  1. While a streak of failed puts lasts, raise `Alm:Mismatch` = 2, `MFC write failed: <PV> (pvStat <n>): controller cannot act`. Clear it on the next good put.
  2. Add a setpoint-follow check to the core, in §8.14:
     - **Proposed §8.14 wording:** "Setpoint follow (writes enabled, state ≠ IDLE, Running_RBV = 1, MFC connected): if |Setpoint_RBV − lastCmd| > max(`mismatchAbs`, `mismatchFrac`·lastCmd) for more than `holdDetect` + `mismatchMargin` s, raise `Alm:Mismatch` = 2 `Alicat setpoint <sp:.2f> SLPM does not follow the controller (<lastCmd:.2f> SLPM): write lost or another writer`, and re-send lastCmd each `holdRetryInterval` s while it lasts."
     - The replay is unaffected, because the reference Alicat always follows.
     - Test: a plant-sim switch that drops or rejects `Setpoint` puts. Press Flow Zero and expect the MAJOR alarm within ~10 s.

**D2. A Live switch during the start-up wait lets the controller write with no operator press. Medium (writes).**
- **Spec:** §8.20 says 0 → 1 "enters IDLE … The Alicat changes only when the operator next presses Purge, Flow Zero, or Resume Flow". The §13.2 dialog promises: "The controller goes to IDLE and changes nothing until you press Purge, Flow Zero or Resume Flow."
- **Code:**
  - `syncWriteEnable` also runs while the start-up still waits for the Alicat (`sgIoc.c:1443`). `sg_gate_set_enable` enters IDLE (`sgGate.c:99-102`).
  - When the Alicat connects, the start decision runs `sg_restart` (`sgIoc.c:1414-1417`).
  - `sg_restart` may enter REGULATE, after which epid writes from the next PID step. It may also enter OPEN_LOOP, which writes `expectedFlow` at once (`sgCore.c:292-293`). Neither needs an operator press.
  - The comment at `sgIoc.c:1440-1442` covers only a switch made in the same tick as the decision.
- **Reach:** only a start in shadow can hit this: `st.cmd.pc` (FORCE_SHADOW=1) and `startLSSSampleGasTest`.
- **Proposed resolution (fix the code):** remember that writes were enabled during the wait. Then have the start decision enter IDLE (`restart: writes enabled during the start-up wait`) instead of REGULATE or OPEN_LOOP.
  - Alternative: refuse 0 → 1 until started, logging `writes cannot be enabled yet: waiting for the Alicat PVs`.
  - Test: start the IOC in FORCE_SHADOW before the plant, switch Live, then start the plant preset regulating. Expect IDLE and no put.

**D3. Wrong Alicat flow units give a log line, not an alarm. Medium (helium accounting, alarms).**
- **Spec:** §8.8 (optional): "if `FlowUnits_RBV` ≠ SLPM, raise MAJOR `MFC flow units are <u>, not SLPM` … implement it as an alarm only."
- **Code:** `sgIoc.c:1208`: `sg_log(&s->c, 2, "MFC flow units are %s, not SLPM", u->lastS);`. It runs once per channel assignment and raises no `Alm:*`, so nothing reaches the banner or the alarm server.
- **Consequence:** wrong units make every flow reading, the mismatch check, the ledger and the forecast wrong.
- **Proposed resolution (fix the code, small spec change):** raise it as an alarm.
  - `Alm:Gas` cannot simply carry it at level 2. PRECHECK clears `Gas` whenever the gas table is He (`sgCore.c:437`), which would also clear the units alarm.
  - Either add `Alm:Units` (MAJOR) to §7.5, or have PRECHECK check both conditions and set `Alm:Gas` to the worse one. In that case §7.5 reads `Alm:Gas | 1, 2`.

**D4. A PID or command that is not a number holds the flow silently. Medium-low (silent inaction).**
- **Spec:** the production rule. The rulings R12 and "epid output is not a number" have no alarm text in the spec.
- **Code:**
  - An epid configure/process failure goes only to `errlogPrintf` (`sgIoc.c:1173-1174`).
  - A non-finite OVAL logs MAJOR `epid output is not a number` once, and nothing is commanded (`sgIoc.c:1183-1186`).
  - `command ignored: flow value is not a number` is logged once (`sgCore.c:190-193`).
  - The state stays REGULATE, HANDOFF or OPEN_LOOP, the valve stays at the last good setpoint, and the banner does not change.
- **Proposed resolution (fix the code):** while it lasts, raise `Alm:Mismatch` = 2, `controller cannot compute a flow (PID output not a number): flow held at <lastCmd:.2f> SLPM`. Clear it on the next finite output.

**D5. KP = 0 is accepted, and it switches feedback off without an alarm. Low-medium.**
- **Spec:** §9.2 says "KP must be negative (more flow lowers O2)", but its table gives KP max 0.
- **Code:** `ioc/tools/sg_pvs.py:177` sets max 0.0, so `Mode:X:KP` DRVH is 0. With KP = 0, both P and ΔI = KP·KI·e·dt are zero, so REGULATE holds a fixed flow.
- **Proposed resolution (spec wording, then regenerate):** §9.2 row `| KP | SLPM/% | −100 | −0.1 |`. Or keep 0 and add "KP = 0 disables feedback". This needs a user decision.

**D6. The reconnect re-send does not restart the mismatch timer. Low (alarms).**
- **Spec:** §8.18: "on reconnect: re-send `lastCmd` as in §8.7". §8.7's resume includes `spChangeT = now, flowAtSpChange = the current Flow_RBV`; that was added after sc08.
- **Code:** `sgCore.c:576-585` re-sends and logs, but leaves `spChangeT` alone. After an outage in which the Alicat's flow moved but its setpoint did not, the next evaluation can raise the same spurious MAJOR `flow mismatch` that sc08 showed.
- **Proposed resolution (fix the code):** set `spChangeT` and `flowAtSpChange` in `mfcLink`'s re-send, and add a unit test next to t_hold_resume_mismatch.

**D7. `Sts:Banner` truncates at 256 characters. Low (alarms).**
- **Spec:** §7.1: "Active alarm texts, most severe first, `"; "`-separated". No size is given.
- **Code:** `ioc/tools/sg_pvs.py:304` sets `SIZV 256`. `sgIoc.c:1378-1379` builds the banner in 4096 bytes, then `pubTxt` cuts it at 255.
  - The start-up wait text alone is about 110 characters.
  - Two or three alarms fill the field, and the lower-severity ones are cut mid-text. The banner is the one summary on both panels.
- **Proposed resolution (fix the code):** `SIZV 2048`, then regenerate the db and screens. No spec change is needed.

**D8. Release control during the start-up wait is dropped without a log line. Low.**
- **Spec:** §8.5: ReleaseIdle "always"; "Rejected commands … reset the command to 0 and log `<command> ignored in <STATE>`".
- **Code:** `sgIoc.c:1126`: `if (takeCmd(s, IN_CMD_IDLE) && (s->started || !s->configured)) sg_op_idle(c);`. During the wait, the press is consumed silently, and the later restart decision may still enter REGULATE.
- **Proposed resolution (fix the code):** remember the press and have the restart decision enter IDLE `admin released control`. At minimum, log `Release control ignored: waiting for the Alicat PVs, controller not acting yet`.

**D9. Commands see the previous tick's O2 validity. Low.**
- **Spec:** §8.1: commands "are handled at the start of the next tick, after step 1".
- **Code:** `commands()` (`sgIoc.c:1444`) runs before `sg_tick` (`sgIoc.c:1446`), whose `readInputs` is step 1. `sgCore.h:24-25` documents this: operator calls "see the new c->in but the previous tick's `now`, o2, o2ok and state".
  - That matches the reference, which acts on a press at once.
  - The effect is a one-second-old o2ok / o2 < lidLevel for Resume Flow.
- **Proposed resolution (spec wording):** "Operator commands are handled at the start of the next tick, before the controller's own step 1: they see this tick's channel values and the O2 value and validity of the previous tick (as the reference, which acts on a press between ticks)."

**D10. The start-up log still says the wait ends after 30 s. Low (text).**
- **Code:** `sgIoc.c:877` logs `waiting up to 30 s for the O2 and Alicat PVs to connect`, as the PC-trial log of 2026-09-28 shows.
- **Spec:** since the user direction of 2026-09-28, the wait for the Alicat has no time limit (§8.15 step 1).
- **Proposed resolution (fix the code):** `waiting for the O2 (up to 30 s) and Alicat PVs (no time limit) to connect`.

**D11. The shadow log line names the record field, not the PV. Low (text).**
- **Spec:** §8.20: log `shadow mode: would write <PV> = <v>`.
- **Code:** `sgGate.c:69-70` logs `shadow mode: would write Setpoint = 0.25` (the suffix only) and `shadow mode: would write Run` (no value). test_write_enable and the operator guide use this form.
- **Proposed resolution (spec wording):** "log `shadow mode: would write <Setpoint|RampRate> = <v:.2f>`, or `shadow mode: would write Run`".

**D12. `Sts:CylPressure` shows no "n/a" text. Low (text).**
- **Spec:** §7.1 says "otherwise INVALID with text n/a".
- **Code:** `sgIoc.c:1324` publishes NaN. The ai record goes UDF/INVALID, and the main panel shows the INVALID border.
- **Proposed resolution (spec wording):** "otherwise NaN (INVALID severity); the screens show it as invalid".

**D13. The SNL gets more macros than P. Low (the spec is outdated).**
- **Spec:** §5.5: "The SNL gets only `P`."
- **Code:**
  - `st.cmd.production`: `seq sampleGas, "P=$(P),LOGDIR=logs,READONLY=0,FORCE_SHADOW=$(SG_FORCE_SHADOW=0)"`.
  - `st.cmd.pc` adds `WRITE_MFC=15IDC:Alicat1:`, which §8.20 itself prescribes.
  - A missing READONLY means writable (`sgIoc.c:783`).
- **Proposed resolution (spec wording):** "The SNL gets `P` plus the start-up switches `LOGDIR`, `HESET`, `READONLY`, `FORCE_SHADOW` and (PC trial only) `WRITE_MFC`; the linked PV names come only from `Cfg:*`."

### B. Spec gaps against the production rule (the code does what the spec says)

**G1. An IOC restart during a purge leaves the Alicat at full flow, silently. High (silent inaction, helium, surface).**
- **Spec:** §8.15 step 4, last branch: "else: enter IDLE (`restart: O2 above lid threshold (§4.7)`)". `sgCore.c:639` does exactly this.
- **When it happens:** valid O2 ≥ `lidLevel` (10 %) with `Setpoint_RBV` > 0. Examples: a restart in the first minutes of a purge, with the Alicat at `purgeFlow` = 20 SLPM, or a restart with the lid off while flowing.
- **Why it is silent:**
  - In IDLE the hold monitor (`sgCore.c:599`), the lid detector (§8.6) and the flow-mismatch check (`sgChecks.c:37`) are all off.
  - The banner reads `No alarms`.
  - Only `CylLow` would eventually speak. At 20 SLPM an 8000 L cylinder lasts about 6.7 h, and the flow is 10× the 2 SLPM surface limit.
- **Proposed resolution (user decision, since it changes the reference):**
  - **Preferred:** that branch enters PRECHECK (and so PURGE) with the reason `restart: O2 above lid threshold with the Alicat flowing: purge resumed`. The purge timeout bounds it. The lid check stops the flow if the enclosure is open (O2 ≥ `dropSkipLevel`). §8.5 already says "A purge is the right action there."
    - **Proposed §8.15 wording:** "else if `Setpoint_RBV` > 0: enter PRECHECK (`restart: O2 above lid threshold with the Alicat flowing: purge resumed`)".
  - **Minimum:** stay in IDLE, but raise `Alm:Mismatch` = 2, `restart: O2 above the lid threshold, Alicat left at <sp:.2f> SLPM: controller not acting; press Purge or Flow Zero`. Clear it on any operator command.

**G2. Shadow mode is not on the banner or the alarm server. Medium (silent inaction).**
- **Spec:** §13.0 and §13.1 ask only for a SHADOW MODE badge.
- **The problem:**
  - `Par:writeEnable` is autosaved (`Db/sampleGas_settings.req`). Once someone presses Shadow on the production IOC, every later start, including `start_ioc -b` at boot, comes up regulating in shadow and never writes.
  - The banner reads `No alarms` and the alarm server is silent. Only the SHADOW MODE badge on the simple and full panels (and the Admin switch) shows it. (`startLSSSampleGasTest` rightly keeps its own autosave directory, so a commissioning start does not cause this.)
- **Proposed resolution (user decision):** while writeEnable = 0 on a configured station, raise a MINOR alarm, e.g. a new `Alm:Shadow` `shadow mode: the controller is not writing to the Alicat`. Add it to §7.5, §8.20, the alarm XML and the archive list.

**G3. A frozen Alicat readback goes unnoticed. Low (not verified).**
- **Spec:** §6.1 says "Every read must carry connection state and severity". Only O2 has a frozen rule (§8.2).
- **Code:** only `Flow_RBV`'s severity is used (`sgIoc.c:1006`). If the Alicat IOC's poll stops without driving the soft `*_RBV` records INVALID, `Flow_RBV` and `Setpoint_RBV` freeze in agreement. The mismatch and hold checks then see nothing wrong. I have not verified whether `Alicat_BC.db` propagates a stream timeout into the RBV severities.
- **Proposed resolution:** check that on the Alicat IOC. If it does not propagate, add a stale-readback rule for `Flow_RBV` (timestamp unchanged for > `frozenTime` → treated as disconnected, §8.18). This needs a user decision.

**Outside §5-§14 (spec text, for the user):**
- §16's row "until then the production IOC runs with `writeEnable` = 0" contradicts §8.20 and §9.1 (default 1, 2026-09-28) and `st.cmd.production`.
- §2.1 rule 1 ("No writes to `15IDC:*` … until the user explicitly lifts this rule") predates the supervised writes of 2026-09-28.
- Both need updating.

## 3. Untested (ranked the same way), with the cheapest test

1. **U1. The §14.2 real-time scenarios on the current build.** Every sc*.json passes, but only sc08 and sc14 ran on the `b2b846e` code (fully bumpless start, hold-resume mismatch fix, totalizer fix). The other 15 ran on 2026-09-28. The bumpless start changes every HANDOFF → REGULATE, and the replay cannot see it, because it models epid's plain start.
   - **Cheapest:** rerun sc01 (1 h), with an added assertion that the first PID step after HANDOFF moves `Sts:LastCmd` by ≤ 0.02 SLPM, plus the short set 2, 3, 9, 12, 13 (~2 h). Then the long set unattended (~12 h).
2. **U2. The rest of §14.3, and the §13.4 alarm server.**
   - sc14 asserts REGULATE and the held flow. The ≤ 0.02 SLPM first step was only read by hand.
   - Parameter and helium restore are not asserted in sc14 (test_pvnames covers names and helium across a restart).
   - No alarm server has ever run against the bench.
   - **Cheapest:**
     - Add to `extra_checks` for sc14: the |ΔLastCmd| at the first PID step after the restart (from the trace), a few `Par:`/`Mode:` values and `He:CumL` continuity before and after.
     - Run a Phoebus alarm server with `sampleGas_alarms_bench.xml` during one IOC kill: expect the `Sts:Heartbeat` alarm after 10 s. test_tickage already covers `Sts:TickAge`.
3. **U3. §8.18 at runtime.** The disconnect alarm, the dropped puts and the reconnect re-send are unit-tested only (t_mfc_disconnect, t_mfc_blip). The glue's `mfcConnected`, which also requires the three put channels, has no runtime test.
   - **Cheapest:** in REGULATE, stop the plant simulator for 10 s and restart it (~5 min bench test). Expect MAJOR `MFC not responding (CA disconnected)`, no put while it is down, then `MFC reconnected: setpoint … re-sent`. This would also show D6.
4. **U4. §14.2 quantitative comparison and the §8.11 epid bench check.** Plan 4 Task 3 (`test_compare.py`, noise off) is in progress. It is the cheapest test there is: it already exists as a brief.
5. **U5. The production build and autosave R5-11** (§11.2, §10, §2.3).
   - Run `ioc/tools/linux_build_check.sh all` (WSL, gcc 11, production modules), then test_pvnames against that build. It covers `manual_save`, NELM 6000 arrays and the `Cfg:*` strings in pass 0.
6. **U6. The Linux start scripts** (§8.20 `startLSSSampleGasTest`, §11.1, §11.3 `startLSSSampleGas`).
   - In the same WSL tree, run both scripts from another directory. Expect a start. For the test script, expect `Par:writeEnable` 0 and `.sav` files in `autosave-test/` only.
7. **U7. The Phoebus confirmation dialogs** (§13.2). The rule-built `confirm_message` has never been seen in Phoebus. If rules do not apply to that property, the static fallback text shows instead, without the Alicat name and the flow.
   - **Cheapest:** open both dialogs on the bench panel, compare their text with §13.2, and cancel (5 min, HANDOFF §3 step 5).
8. **U8. Clock catch-up and skip, and a wall clock that steps back** (§8.1). Both are untested.
   - **Cheapest:** move the arithmetic of `sgIocSecondsToNextTick` into a pure function and unit-test it. Or suspend the bench IOC process for 20 s and for 70 s, and check the heartbeat catch-up and the MINOR `tick clock … skipped ahead`.

Tested only in part (for completeness):
- NewCylinder's immediate helium save (only MarkNewRun is bench-checked; same code path).
- The `not configured: …` MAJOR banner alarm (unit test only).
- The PC-trial Cfg:MFC "writes disabled" path (PC-mode check, script not in the repo).
- `Sts:Flow` INVALID on a disconnect (no assertion).
