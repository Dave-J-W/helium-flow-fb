# 15LSS_sample_gas IOC, Plan 2: the IOC application

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The runnable IOC `lssSampleGas`: every PV of spec §7, the SNL shell around the Plan 1
core, autosave, the write gate and shadow mode, editable linked PV names, the persistent log, and a
bench start-up that runs against the Plan 3 plant simulator.

**Architecture:** Three layers.
1. The **core** (Plan 1, pure C) holds all controller logic.
2. A **glue** layer (`sgIoc.c`, C inside the IOC) moves data between the core and the station's
   own records through dbAccess (`dbNameToAddr`/`dbGetField`/`dbPutField`): parameters and
   commands in, status/diagnostic/alarm/helium PVs out, the epid record configured and processed.
   It also implements everything the reference lacks: the write gate and shadow logging (§8.20),
   PV-name apply rules (§8.21), the persistent log (§8.19), helium state import/export for
   autosave, and report/forecast texts.
3. A thin **SNL program** (`sampleGas.st`, reentrant) owns the Channel Access side: channels to the
   remote Alicat, O2 and cylinder PVs (anonymous `assign`, then `pvAssign` to the names in
   `Cfg:*`), monitors, connection and severity, the 1 Hz wall-clock-aligned tick, and the gated
   puts (`pvPut(..., SYNC)`), which the glue queues as actions.

All PV definitions come from one Python table (`ioc/tools/sg_pvs.py`) that generates the
database, the autosave request files and a C table the glue uses; Plan 5 reuses it for the
screens. Generated files are committed, so the production build needs no Python.

**Tech Stack:** EPICS base 7.0.8.1, seq 2.2.9, std (epid), calc, asyn, autosave, sscan; MinGW bench;
Python 3.12 venv for the generator and tests; caproto plant simulator from Plan 3.

**Spec:** `docs/ioc/15LSS_sample_gas_IOC_spec.md` (§5, §6, §7, §8.1-§8.3, §8.15, §8.19-§8.21, §9,
§10, §11.2-§11.3). **Depends on:** Plan 1 (core, `sgCore.h`), Plan 3 (plant simulator, bench CA env:
merge branch `ioc-plan3` into this branch first).

## Global Constraints

- Everything in Plan 1's Global Constraints (no `15IDC:*` writes; localhost bench; C17 for gcc 16
  and gcc 11; no heredocs; commit identity and trailer; no site paths in committed files).
- Bench PV prefixes: controller `SIM:SampleGas:` (station 1), `SIM2:SampleGas:` (station 2, loaded
  with empty `MFC`/`O2` to exercise "not configured"); plant `SIM:Alicat1:`, `SIM:O2`.
- The bench IOC is started with `ioc/tools/bench_env.sh` sourced (CA addr list
  `127.0.0.1:5064 127.0.0.1:5066`, server bound to 127.0.0.1).
- Write gate: no put to any Alicat PV unless `Par:writeEnable` = 1; nothing in the database links
  to an Alicat PV (spec §7.8, §8.3, §8.20).
- Code compatible with production module versions: autosave R5-11, calc R3-7-5, asyn R4-44-2,
  sscan R2-11-6, std R3-6-4, seq R2-2-9 (spec §2.3, §11.2).

## File map

```
ioc/tools/
  sg_pvs.py                    THE table: every PV of spec §7 and §9 (name, record type, fields,
                               default, limits, level, group, label, autosave set)
  gen_db.py                    table → Db files, .req files, sgPvTable.c; --check mode
  run_ioc.sh                   bench: env, envPaths rewrite (MSYS → C:/msys64), DLL PATH, start
ioc/lssSampleGas/
  configure/CONFIG_SITE.local.example   INSTALL_LOCATION without spaces (bench)
  lssSampleGasApp/Db/
    Makefile
    sampleGas.db               generated: one station (params, modes, Cfg, Cmd, Sts, Diag, Alm, He,
                               epid, PID:CVAL, PID:Out, Log)
    sampleGas_settings.req     generated (spec §10)
    sampleGas_helium.req       generated (spec §10)
  lssSampleGasApp/src/
    sgPvTable.c / sgPvTable.h  generated: PV suffix ↔ sg_params field offset / status source
    sgIoc.h / sgIoc.c          glue (station context, tick, publish, epid step)
    sgGate.c                   write gate, shadow logging, action queue (pure C, unit-tested)
    sgCfg.c                    §8.21 apply rules (pure C, unit-tested)
    sgLogFile.c                persistent monthly log + Log:Text ring (pure C except errlog)
    sgText.c                   report text, forecast text, dates (pure C, unit-tested)
    sgIocTest.c                unit tests for sgGate, sgCfg, sgLogFile, sgText
    sampleGas.st               SNL shell
    sampleGasSeq.dbd           registrar(sampleGasRegistrar)
    lssSampleGasMain.cpp       standard IOC main
  iocBoot/iocLSS_sample_gas/
    Makefile, st.cmd (bench), st.cmd.production (15IDC; 15IDE commented), save_restore.cmd,
    startLSSSampleGas (production start script, cd to own dir), autosave/ (gitignored), logs/
ioc/test/
  test_ioc_smoke.py            bench smoke test (plant sim + IOC): starts, ticks, shadow, purge
```

---

### Task 1: The PV table and the generator

**Files:** Create `ioc/tools/sg_pvs.py`, `ioc/tools/gen_db.py`, the three generated Db files,
`src/sgPvTable.c/.h`; a test `ioc/tools/test_gen_db.py` (unittest).

**Interfaces (produced):**
```python
# sg_pvs.py
PARAMS: list[dict]   # one per §9.1 row: key, default, unit, min, max, level ('U'|'A'|'D'),
                     #   group (one of the nine Deep admin groups of §13.3, or 'Main'/'Admin'),
                     #   label (short, for screens), integer (bool), desc
MODE_FIELDS: list[dict]  # baseFlow, n, KP, KI, drvh, drvl with §9.2 limits; MODE_DEFAULTS per slot
PVS: list[dict]      # every other PV of §7.1, §7.3, §7.5-§7.9: suffix, rtyp, fields (dict),
                     #   source/sink tag used by the glue, autosave set ('settings'|'helium'|None)
```
`gen_db.py` writes `sampleGas.db` with macros `P`, `MFC`, `O2`, `CYL`, `STN` (the latter four only as
the `Cfg:*` and `Cfg:Default:*` initial values, spec §7.9), the `.req` files, and
`sgPvTable.c` containing:
```c
typedef struct { const char *suffix; size_t offset; int isInt; } sgParPv;   /* Par:<key> → sg_params */
extern const sgParPv sgParPvs[]; extern const int sgNParPvs;
typedef struct { const char *suffix; size_t offset; } sgModePv;             /* Mode:<X>:<field> → sg_mode */
extern const sgModePv sgModePvs[]; extern const int sgNModePvs;
```
`gen_db.py --check` regenerates into memory and exits 1 if any committed output differs.

- [ ] **Step 1: Write the failing test** (`test_gen_db.py`):
  - `len([p for p in PARAMS])` equals the number of `§9.1` rows (parse the spec table rows between
    `### 9.1` and `### 9.2`: every line starting `| ` whose first cell is a key); every key, default,
    min, max, level matches the spec row (numbers compared as floats; `−` minus signs in the spec
    normalised).
  - Every PV named in spec §7.1, §7.3, §7.5, §7.6, §7.7, §7.8, §7.9 (parse backticked
    `$(P)...` names in those sections' tables and bullets) exists in the generated `sampleGas.db`.
  - The 51 D-level keys are each in exactly one of the nine Deep admin groups, matching the lists
    in spec §13.3.
  - `gen_db.py --check` exits 0.
- [ ] **Step 2: Run: fails (no files).** `cd ioc/tools; python -m unittest -v test_gen_db`
- [ ] **Step 3: Write `sg_pvs.py`** by transcribing spec §7 and §9 exactly (record types from §7:
  parameters `ao`/`longout` with DRVL/DRVH/VAL, `Par:writeEnable` `bo`; `Mode` `mbbo`; mode fields
  `stringout`/`ao`; `Cfg:*` `stringout`/`stringin`/`bi`/`bo`/`lsi`; `Cmd:*` `bo`; `Sts:*` per the
  §7.1 table; `Alm:<Name>` `mbbi` with ZRSV/ONSV/TWSV and `Alm:<Name>:Msg` `lsi` SIZV 256;
  `Diag:*` per §7.6; `Log:Text` waveform CHAR NELM 16384; `He:*` per §7.7 with the NELMs given;
  `PID` epid with the §7.8 fields and `OUTL=$(P)PID:Out NPP`; `PID:CVAL` ai; `PID:Out` ao).
  Add a `Cmd:SaveHelium`-free design: helium saves go through `manual_save` (spec §10).
- [ ] **Step 4: Write `gen_db.py`**, generate, commit the outputs.
- [ ] **Step 5: Run the test: PASS.**
- [ ] **Step 6: Commit** (`"ioc(db): PV table and generated database, autosave requests"`).

---

### Task 2: Pure-C glue pieces with unit tests (gate, PV-name rules, log file, texts)

**Files:** Create `src/sgGate.c`, `src/sgCfg.c`, `src/sgLogFile.c`, `src/sgText.c`, their shared
header `src/sgIoc.h` (declarations), `src/sgIocTest.c`; modify `src/Makefile` (TESTPROD_HOST
`sgIocTest`).

**Interfaces (produced, in `sgIoc.h`):**
```c
/* Write gate and action queue (spec §8.3, §8.20) */
enum sg_act { SG_ACT_SP, SG_ACT_RAMP, SG_ACT_RUN };
typedef struct { int kind; double v; } sg_action;
typedef struct {
    sg_action q[16]; int n;
    int writeEnable, mfcConnected;
    double lastShadow[3]; int haveShadow[3];   /* dedup of shadow log lines per PV */
} sg_gate;
void sg_gate_request(sg_gate *g, sg_ctl *c, int kind, double v);  /* queue if writes enabled and
     connected; else log "shadow mode: would write <PV> = <v>" once per changed value */
int  sg_gate_next(sg_gate *g, sg_action *out);                    /* pop, 0 when empty */
void sg_gate_set_enable(sg_gate *g, sg_ctl *c, int en, double sp); /* 0→1: log
     "writes enabled: Alicat left at <sp:.2f> SLPM", sg_enter(IDLE, "writes enabled"); 1→0: log
     MAJOR "writes disabled: shadow mode, Alicat holds <sp:.2f> SLPM", state unchanged */

/* PV names (spec §8.21) */
typedef struct { char mfc[40], o2[40], cyl[40], stn[40]; } sg_names;
enum { SG_CFG_OK, SG_CFG_UNCHANGED, SG_CFG_REJECT_STATE, SG_CFG_NOT_CONFIGURED };
int  sg_cfg_apply(const sg_names *edit, sg_names *active, int state, int *mfcChanged,
                  char *status, size_t n);          /* trims, validates, decides; fills status */
int  sg_cfg_configured(const sg_names *active);     /* MFC and O2 non-empty */

/* Persistent log (spec §8.19, §7.6) */
typedef struct { char dir[256]; char stn[40]; char ring[200][320]; int head, count; } sg_logfile;
void sg_logfile_init(sg_logfile *L, const char *dir, const char *stn);
void sg_logfile_line(sg_logfile *L, double epochT, int sev, const char *msg);  /* formats
     "YYYY-MM-DD hh:mm:ss  <STN>  [MINOR|MAJOR]  <msg>", appends to <dir>/sampleGas_<STN>_YYYY-MM.log
     (fflush), adds to the ring, and prints it with errlogPrintf when built into the IOC */
size_t sg_logfile_text(const sg_logfile *L, char *buf, size_t n);  /* newest first, '\n'-joined */

/* Texts */
void sg_report_text(const sg_ctl *c, const sg_report *r, char *buf, size_t n); /* spec §7.7
     He:Rep:Text: the reference's Admin usage text (lines 1979-1985) with dates
     "YYYY-MM-DD hh:mm" (local time) in place of "day N.NN" */
```
(`sg_forecast_text` and `sg_progress_text` come from Plan 1 Task 8.)

- [ ] **Step 1: Write failing tests** in `sgIocTest.c`:
  1. Gate, writes enabled + connected: `sg_gate_request(SP, 0.29)` queues one action; `sg_gate_next`
     returns it, then 0.
  2. Gate, writes disabled: no action queued; one log line `shadow mode: would write Setpoint =
     0.29`; a second identical request logs nothing; a request with 0.30 logs again.
  3. `sg_gate_set_enable` 0→1 enters IDLE, logs the text, queues nothing; 1→0 from REGULATE keeps
     REGULATE and logs MAJOR text.
  4. Cfg: apply in REGULATE → `SG_CFG_REJECT_STATE`, status `rejected: release control first (state
     REGULATE)`, active unchanged; in IDLE with a changed O2 → OK, `mfcChanged` 0; with a changed MFC
     → OK, `mfcChanged` 1; unchanged → `SG_CFG_UNCHANGED`; empty O2 → `SG_CFG_NOT_CONFIGURED` with
     status `not configured: Cfg:O2 is empty`; names with surrounding spaces are trimmed.
  5. Log file: two lines written to a temp dir produce a file `sampleGas_SIM_2026-09.log`
     (use a fixed epoch in September 2026) whose lines match the format; `sg_logfile_text` returns
     the newest first; 250 lines keep only the newest 200 in the ring.
  6. Report text: for a hand-built `sg_report` with two runs, the text matches an expected string.
- [ ] **Step 2: Build; expect link failures.**
- [ ] **Step 3: Implement** (`sgLogFile.c` guards `errlogPrintf` behind `#ifdef SG_IN_IOC`, which
  the IOC library build defines and the test build does not).
- [ ] **Step 4: Build and run `sgIocTest`: PASS.**
- [ ] **Step 5: Commit** (`"ioc(glue): write gate, PV-name rules, persistent log, report text"`).

---

### Task 3: Glue module inside the IOC and the SNL shell

**Files:** Create `src/sgIoc.c`, `src/sampleGas.st`, `src/sampleGasSeq.dbd`,
`src/lssSampleGasMain.cpp`; modify `src/Makefile`, `lssSampleGasApp/Makefile`, `ioc/tools/build.sh`,
add `configure/CONFIG_SITE.local.example`.

**Interfaces (produced, `sgIoc.h`):**
```c
typedef struct sg_station sg_station;          /* opaque: core, gate, logfile, names, dbAddrs */
sg_station *sgIocCreate(const char *prefix);   /* resolves every local PV by name (dbNameToAddr),
                                                  reads Cfg:* and helium records into the core */
void sgIocNames(sg_station *s, sg_names *active);   /* names to pvAssign */
double sgIocSecondsToNextTick(sg_station *s);  /* to the next whole wall-clock second */
void sgIocTick(sg_station *s, const sg_inputs *in, double now);  /* one tick, spec §8.1 order:
     read params and commands, apply commands (before sg_tick, as the reference), sg_tick,
     PID step if due (write epid fields, PID:CVAL, process PID, read OVAL, sg_pid_done), keep
     PID:Out = lastCmd while FBON = 0, publish Sts/Diag/Alm/He, helium manual_save after
     NewCylinder/MarkNewRun, Log:Text */
int  sgIocNextAction(sg_station *s, int *kind, double *v);  /* drained by the SNL after each tick */
int  sgIocNamesChanged(sg_station *s);         /* 1 once after a successful Cfg:Apply */
```

SNL outline (`sampleGas.st`, `option +r;`):
```
program sampleGas
option +r;
%%#include "sgIoc.h"
double o2;      assign o2;      monitor o2;
double flow;    assign flow;    monitor flow;
double spRbv;   assign spRbv;   monitor spRbv;
double ramp;    assign ramp;    monitor ramp;
double total;   assign total;   monitor total;
short  running; assign running; monitor running;
string gas;     assign gas;     monitor gas;
string status;  assign status;  monitor status;
double spPut;   assign spPut;
double rampPut; assign rampPut;
short  runPut;  assign runPut;
double cyl;     assign cyl;     monitor cyl;
%%sg_station *stn;  (as a variable held per instance: declare with `foreign`/`%{ }%` per seq 2.2 rules)
ss ctl {
  state init {
    entry { /* stn = sgIocCreate(macValueGet("P")); pvAssign each channel to names (MFC + suffix) */ }
    when (pvConnectCount() >= 1 || delay(30.0)) { } state tick   /* spec §8.15: wait ≤ 30 s */
  }
  state tick {
    entry { /* dt = sgIocSecondsToNextTick(stn) */ }
    when (delay(dt)) {
      /* build sg_inputs from the monitored vars, pvConnected() and pvSeverity(o2) (INVALID if
         disconnected); sgIocTick(); while (sgIocNextAction(...)) { set spPut/rampPut/runPut;
         pvPut(var, SYNC); } if (sgIocNamesChanged(stn)) pvAssign again */
    } state tick
  }
}
```
Channel names: `<MFC>Flow_RBV`, `Setpoint_RBV`, `RampRate_RBV`, `Total_RBV`, `Running_RBV`,
`Gas_RBV`, `Status`, and put channels `Setpoint`, `RampRate`, `Run`; `<O2>`; `<CYL>` (unassigned
when empty). The restart decision (§8.15 steps 2-4) runs on the first tick through `sg_restart`.
`FlowUnits_RBV` is read once at start for the optional MAJOR alarm (§8.8).

- [ ] **Step 1: Build configuration.** Try `INSTALL_LOCATION` first: `configure/CONFIG_SITE.local`
  (gitignored; commit the `.example`) sets `INSTALL_LOCATION=/home/<user>/bench/lssSampleGas-install`
  so every `-L` path is space-free while sources stay in the repo. If linking still word-splits,
  switch `build.sh` to its documented rsync-copy fallback. Record which one worked in `build.sh`.
- [ ] **Step 2: Makefile.** `LIBRARY_IOC += sampleGasSupport` with the core sources, the glue
  sources and `sampleGas.st` (trap: SNL must be in the library on MinGW); `sampleGasSupport_LIBS +=
  std calc seq pv autosave $(EPICS_BASE_IOC_LIBS)`; `USR_CFLAGS += -DSG_IN_IOC` for the library;
  `PROD_IOC = lssSampleGas`, dbd from base, asyn, stdSupport, calcSupport, asSupport,
  sampleGasSeq.dbd; `lssSampleGas_LIBS += sampleGasSupport std calc sscan autosave asyn seq pv`.
  Keep the Plan 1 TESTPROD_HOST programs building from sources.
- [ ] **Step 3: Implement `sgIoc.c` and `sampleGas.st`.** Resolve every local PV once at create
  time (`dbNameToAddr`); a missing record is a fatal start-up error naming the PV. Publishing: write
  only values that changed since the last tick (keep the last published copy per PV). Alarm PVs:
  value 0/1/2 plus `:Msg`; `Sts:Banner` from `sg_banner`; `Sts:WorstSevr`. `Sts:O2` NaN with
  INVALID when O2 is invalid. Mode state strings (`Mode.ZRST..THST`) copied from `Mode:X:name` at
  start and on change. Commands: a `Cmd:*` record at 1 is handled once and reset to 0 (spec §8.1
  order); rejected commands log `<command> ignored in <STATE>` (or the Resume Flow texts from
  `sg_op_resume_flow`'s `why`). The station is "not configured" when `sg_cfg_configured` is false:
  no state machine, commands rejected with the status text (spec §8.21).
- [ ] **Step 4: Build.** Expected: the IOC executable and library link on the bench.
- [ ] **Step 5: Commit** (`"ioc: glue module and SNL shell; IOC builds"`).

---

### Task 4: iocBoot, autosave and the bench run

**Files:** Create `iocBoot/iocLSS_sample_gas/{Makefile, st.cmd, st.cmd.production,
save_restore.cmd, startLSSSampleGas, autosave/.gitignore, logs/.gitignore}`, `ioc/tools/run_ioc.sh`;
modify the top `Makefile` (iocBoot dir) and `.gitignore`.

- [ ] **Step 1: st.cmd (bench)** per spec §5.5 with `P=SIM:SampleGas:,MFC=SIM:Alicat1:,O2=SIM:O2,
  CYL=,STN=SIM` and a second station `P=SIM2:SampleGas:,MFC=,O2=,CYL=,STN=SIM2`; `< save_restore.cmd`
  before `iocInit` (pass 0 and pass 1 for both request files, per station prefix via the
  `P` macro in `set_pass0_restoreFile("sampleGas_settings_$(P).sav")`-style names; one save file per
  station); `create_monitor_set` for both sets and both stations after `iocInit`; one `seq
  sampleGas, "P=..."` per station. `st.cmd.production`: the 15IDC station, 15IDE commented.
- [ ] **Step 2: `startLSSSampleGas`** (production): `#!/bin/bash`, `cd "$(dirname "$0")"`, `exec
  ../../bin/linux-x86_64/lssSampleGas st.cmd.production` (spec §11.3). Executable bit set in git
  (`git update-index --chmod=+x`).
- [ ] **Step 3: `run_ioc.sh`** (bench): source `ioc/tools/bench_env.sh`, then **refuse to start**
  (exit 2, message) unless `EPICS_CA_AUTO_ADDR_LIST=NO`, `EPICS_CAS_INTF_ADDR_LIST=127.0.0.1`,
  `EPICS_CAS_BEACON_ADDR_LIST=127.0.0.1`, `EPICS_CAS_AUTO_BEACON_ADDR_LIST=NO`, every host in
  `EPICS_CA_ADDR_LIST` is 127.0.0.1, and the PVA equivalents are confined (the Plan 3 review found
  that a server started without these beacons to the whole network); rewrite `envPaths` MSYS
  paths to `C:/msys64/...` (trap 4); prepend the support `bin/windows-x64-mingw` dirs and base's to
  PATH (trap 5); `cd` to the iocBoot dir and run the IOC with `st.cmd`. Optional `--background`
  writes the console to `logs/console.log`.
- [ ] **Step 4: Manual run.** Start the plant simulator (Plan 3; `plant_sim.py --no-noise`), then
  the IOC. Check with `caget` (pyepics or `caget.exe` from base) that `SIM:SampleGas:Sts:Heartbeat`
  increments, `Sts:State` is IDLE (restart: setpoint 0), `SIM2:SampleGas:Cfg:Status` says not
  configured, and the autosave `.sav` files appear within 30 s.
- [ ] **Step 5: Commit** (`"ioc: iocBoot, autosave, bench start script"`).

---

### Task 5: Bench smoke test

**Files:** Create `ioc/test/test_ioc_smoke.py` (unittest; starts the plant simulator and the IOC as
subprocesses with the bench env, stops them in `tearDownClass`).

- [ ] **Step 1: Write the tests** (real time; the whole file should take under 5 minutes):
  1. Heartbeat increments by 4-6 in 5 s.
  2. Shadow mode: with `Par:writeEnable` 0 (the database default), press `Cmd:Purge`: state goes
     PRECHECK → PURGE; the log (`Log:Text`) contains `shadow mode: would write Setpoint = 20`;
     `SIM:Alicat1:Setpoint` never changes (monitor it for the whole test) and `Flow_RBV` stays 0.
  3. Writes enabled: put `Par:writeEnable` 1: log `writes enabled`, state IDLE, the Alicat setpoint
     unchanged. Then `SIM:World:Preset 0.29` and wait 90 s for O2 to settle; press
     `Cmd:ResumeFlow`: REGULATE; within 30 s `SIM:Alicat1:Setpoint` changes by at most 0.02 at
     the first PID step.
  4. Linked names: `SIM:SampleGas:Cfg:Active:O2` is `SIM:O2`; `Cfg:Conn:O2` is 1.
  5. Station 2: `SIM2:SampleGas:Cfg:Status` starts with `not configured`; `Cmd:Purge` there is
     rejected (state stays IDLE).
  6. Autosave: after changing `Par:tol` to 0.03 and waiting 35 s, the settings `.sav` file contains
     `SIM:SampleGas:Par:tol 0.03`.
- [ ] **Step 2: Run; fix what fails (in the IOC code, not the test, unless the test contradicts the
  spec).**
- [ ] **Step 3: Commit** (`"ioc(test): bench smoke test"`).

### Task 6: Run from this PC against the real enclosure (added 2026-09-28, user request)

The user approved continuous reads of the live PVs and is ready for write-enabled testing, run
first from this Windows PC rather than the Linux host. Writes still only through the write gate,
only to `15IDC:Alicat1:Setpoint`, `RampRate`, `Run`, with the user present, and with an explicit
go from the user at the moment writes are first enabled.

**Files:** Create `iocBoot/iocLSS_sample_gas/st.cmd.pc`, `ioc/tools/run_ioc_pc.sh`,
`ioc/tools/beamline_env.local.sh.example` (the real `beamline_env.local.sh` is gitignored: it holds
the beamline CA address list), `ioc/HARDWARE_TEST.md`; modify `sgIoc.c`/`sampleGas.st` (read-only
macro), `.gitignore`.

- [ ] **Step 1: Read-only macro.** `seq sampleGas, "P=...,READONLY=1"`: the SNL never `pvAssign`s
  the three writable Alicat channels (Setpoint, RampRate, Run), and the glue refuses
  `Par:writeEnable` 0→1 (puts it back to 0, logs MAJOR `read-only IOC: writes cannot be
  enabled`). With `READONLY=0` behaviour is unchanged. Unit/bench test: with READONLY=1 against the
  plant simulator, writeEnable 1 is refused and no put reaches `SIM:Alicat1:*`.
- [ ] **Step 2: PC start-up.** `st.cmd.pc`: one station, `P=LSSPC:SampleGas:` (served on localhost
  only, not a beamline prefix), `MFC=15IDC:Alicat1:`, `O2=15IDC:D1Dmm_calc`, `CYL=`, `STN=15IDC`,
  `READONLY=$(READONLY=1)`, own autosave directory `autosave-pc/`, own log directory. `run_ioc_pc.sh`
  sources `beamline_env.local.sh` for the CA *client* address list (the beamline), then forces the
  *server* side to localhost (`EPICS_CAS_INTF_ADDR_LIST=127.0.0.1`,
  `EPICS_CAS_BEACON_ADDR_LIST=127.0.0.1`, `EPICS_CAS_AUTO_BEACON_ADDR_LIST=NO`), refuses to start if
  the served prefix starts with `15ID` or if the server side is not confined, and accepts
  `READONLY=0` only from an explicit command-line flag `--allow-writes`.
- [ ] **Step 3: Phoebus on this PC.** A bench Phoebus settings file for `LSSPC:` (CA client to
  127.0.0.1:5064 only) and its own server port (never the user's running Phoebus); Plan 5 screens
  opened with `P=LSSPC:SampleGas:`.
- [ ] **Step 4: `HARDWARE_TEST.md`**: the procedure for today, with the user: pre-checks (the old
  timer script and anything else writing the Alicat stopped; enclosure closed, normal lid, mode A;
  cylinder pressure; Alicat not on hold), phase A read-only (IOC readings match the operator's
  Phoebus for 10 min; state IDLE; no put possible), phase B writes (user go → restart with
  `--allow-writes`, `writeEnable` 1 with the confirmation → IDLE, nothing written; Purge from air;
  watch the lid check, handoff, regulation; Flow Zero; restart test in REGULATE), then the spec
  §15.3 items as the user decides. **First write tests with the helium supply OFF** (user
  decision 2026-09-28, to save gas): expected sequence for Purge = PRECHECK → PURGE with
  Setpoint_RBV 20 and Flow_RBV 0 → MAJOR `flow mismatch: cylinder empty or MFC fault?` within
  ~5-10 s → purge timer starts at 30 s → `no O2 decay within 30 s of full flow: enclosure open?` →
  OPEN_STOP, setpoint back to 0; also Flow Zero, writes off/on (valve untouched), hold/Run
  behaviour if the Alicat goes on hold. Then with the gas ON (open-lid purge to confirm the lid-check thresholds;
  surface-vibration ceiling; bump tests), each with its stop condition and what to record. How to
  stop everything at once (Flow Zero, then writes off, then stop the IOC).

## Self-review notes

- Spec coverage: §7 PVs (Task 1), §8.1 tick and command order (Task 3), §8.3/§8.20 gate and shadow
  (Tasks 2-3, 5), §8.15 start (Task 3), §8.18 connection (SNL inputs), §8.19 log (Task 2),
  §8.21 names (Tasks 2-3), §10 autosave (Tasks 1, 4), §11.3 start script (Task 4). Acceptance
  scenarios, restart and PV-name tests are Plan 4; screens Plan 5.
