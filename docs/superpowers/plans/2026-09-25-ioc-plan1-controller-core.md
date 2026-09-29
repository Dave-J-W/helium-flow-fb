# 15LSS_sample_gas IOC, Plan 1: controller core in C, verified against the reference

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A pure-C port of the simulator's `Controller` (the whole control algorithm), built inside the
IOC's EPICS top on the MinGW bench, that reproduces the reference tick for tick in all 19 scenarios.

**Architecture:** The controller logic lives in a C "core" with no EPICS or Channel Access
dependency: inputs go in as a snapshot struct, and every effect (Alicat puts, log lines) comes out
through callbacks. The SNL program of Plan 2 becomes a thin shell around it. A Node.js script runs
the reference JavaScript with a seeded random generator and records, per scenario, every input the
controller saw and every output it produced. A C replay program feeds the same inputs to the core
and compares the outputs line by line. This checks the whole algorithm, including the multi-day
scenarios 17 and 19, in seconds instead of the 14 h real-time suite.

**Tech Stack:** C17 (gcc 16 on the bench forced to gnu17, gcc 11 on production), EPICS base
7.0.8.1 build system (MinGW `windows-x64-mingw`), Node.js (MSYS2 package) for the reference traces.

**Spec:** `docs/ioc/15LSS_sample_gas_IOC_spec.md` (revision 2026-09-25b). The reference is
`Controller` in `simulator/sample_gas_simulator.html` at tag `sim-v1.0`; line numbers below refer to
that file.

## Roadmap (this plan is 1 of 5)

| Plan | Deliverable | Depends on |
|---|---|---|
| **1 (this)** | C controller core + reference replay tests (all 19 scenarios) | – |
| 2 | IOC: database (all PVs of spec §7), SNL shell, autosave, write gate/shadow mode, `Cfg:` PV names, persistent log; smoke-run on the bench | 1 |
| 3 | `ioc/test/plant_sim.py`: caproto plant simulator (spec §14.1) + open-loop validation | – |
| 4 | Acceptance: real-time scenarios, restart, PV-name and write-enable tests (spec §14.2–§14.3b) | 2, 3 |
| 5 | Phoebus screens, alarm-server config, archiver PV list, `ioc/README.md` (spec §13, §14.5) | 2 |

## Global Constraints

- No writes to `15IDC:*` by any tool. Nothing in Plan 1 uses Channel Access at all.
- Bench env: every MSYS2 script sources `~/epics-sim-env.sh` (CA/PVA confined to 127.0.0.1).
- Code must build with gcc 16 (`-std=gnu17`, set in base `CONFIG_SITE.local`) **and** gcc 11. Plain
  C17: no `nullptr`, `constexpr`, `typeof`, `[[attributes]]`, no bare `bool` without `<stdbool.h>`.
- The core is pure C (`<math.h>`, `<stdio.h>`, `<string.h>`, `<stdlib.h>` only); no heap allocation
  after init; fixed array sizes from spec §7.
- Message texts are the reference's, verbatim (spec §8.19). Known, spec-mandated differences are
  listed in the replay program's `KNOWN_DIFFS` table and nowhere else.
- UTF-8 source files; messages contain `→ ≥ ≤ Δ ± – —`.
- Never write code through shell heredocs (use the file-writing tool).
- Python, when needed: `%USERPROFILE%\.venvs\bluesky\Scripts\python.exe` (not needed in Plan 1).
- Commits: repo-local identity `Dave-J-W <248028152+Dave-J-W@users.noreply.github.com>`; end
  messages with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Do not push without the
  user's say-so.
- No site paths (account names, host names, IPs) in committed files; site paths live in
  `configure/RELEASE.local` (gitignored).

## File map

```
ioc/
  lssSampleGas/                        EPICS top (spec §5.1)
    Makefile                           standard top Makefile (DIRS configure, *App)
    configure/                         CONFIG, CONFIG_SITE, Makefile, RELEASE, RULES, RULES.ioc,
                                       RULES_DIRS, RULES_TOP  (makeBaseApp templates)
    configure/RELEASE.local.example    placeholders; the real RELEASE.local is gitignored
    lssSampleGasApp/
      Makefile
      src/
        Makefile                       library sampleGasSupport (core) + test programs
        sgFmt.h / sgFmt.c              JS-compatible number/time formatting
        sgCore.h                       core API and data types (the contract for Plans 2 and 4)
        sgCore.c                       tick, inputs, states, lid/hold/precheck/purge/handoff, restart, ops
        sgAlarms.c                     alarm table, set/clear/latch/debounce/override, banner
        sgChecks.c                     alarmChecks: mismatch, settling, flow/pinned/O2 alarms
        sgPid.c                        configEpid, PID prepare/done
        sgLedger.c                     ledger, litresAt, usage report, cylinder forecast
        sgEpidSim.h / sgEpidSim.c      devEpidSoft emulation (tests only, mirrors processEpid)
        sgFmtTest.c                    unit tests for sgFmt
        sgUnitTest.c                   unit tests for behaviour not in the reference (Task 8)
        sgReplay.c                     reference replay harness
  test/
    ref/make_traces.js                 runs the reference, writes traces + node self-test
    golden/.gitignore                  traces are generated, not committed
  tools/
    msys.ps1                           run an MSYS2 script from PowerShell
    build.sh                           build the top on the bench
    replay.sh                          generate traces (if missing) and run all replays
```

The bench build runs through a junction without spaces (Task 1), because the repo path contains
`Claude Locals` and GNU make cannot handle spaces in paths.

---

### Task 1: Tooling and an EPICS top that builds on the bench

**Files:**
- Create: `ioc/tools/msys.ps1`, `ioc/tools/build.sh`, `ioc/lssSampleGas/**` (top skeleton),
  `ioc/lssSampleGas/configure/RELEASE.local.example`, `ioc/lssSampleGas/.gitignore`,
  `ioc/lssSampleGas/lssSampleGasApp/src/sgFmt.c` (stub), `.../src/sgFmtTest.c` (trivial)

**Interfaces:**
- Produces: `powershell -File ioc/tools/msys.ps1 ioc/tools/build.sh` builds everything and exits 0;
  test programs land in `ioc/lssSampleGas/lssSampleGasApp/src/O.windows-x64-mingw/`.

- [ ] **Step 1: Install Node.js into MSYS2** (a local dev tool; reversible with `pacman -R`)

Script file `ioc/tools/install_node.sh`:
```bash
#!/bin/bash
set -e
pacman -S --needed --noconfirm mingw-w64-x86_64-nodejs
node --version
```
Run from PowerShell:
```powershell
$env:MSYSTEM='MINGW64'; $env:CHERE_INVOKING='1'; C:\msys64\usr\bin\bash.exe -l ioc/tools/install_node.sh
```
Expected: a version line such as `v22.x`.

- [ ] **Step 2: Write `ioc/tools/msys.ps1`**

```powershell
# Run a bash script under MSYS2 MINGW64 from the repo root: powershell -File ioc/tools/msys.ps1 <script> [args]
param([Parameter(Mandatory=$true)][string]$Script, [Parameter(ValueFromRemainingArguments=$true)]$Rest)
$env:MSYSTEM = 'MINGW64'; $env:CHERE_INVOKING = '1'
& C:\msys64\usr\bin\bash.exe -l $Script @Rest
exit $LASTEXITCODE
```

- [ ] **Step 3: Create the junction and the top skeleton**

`ioc/tools/build.sh`:
```bash
#!/bin/bash
# Build the lssSampleGas top on the MinGW bench. The repo path has a space, which GNU make cannot
# handle, so the build runs through a junction without spaces: ~/bench/lssSampleGas -> repo top.
set -e
source ~/epics-sim-env.sh
REPO_TOP="$(cd "$(dirname "$0")/../lssSampleGas" && pwd -W)"      # C:/Users/.../ioc/lssSampleGas
LINK="$HOME/bench/lssSampleGas"
if [ ! -e "$LINK" ]; then
  cmd //c mklink //J "$(cygpath -w "$LINK")" "$(cygpath -w "$REPO_TOP")"
fi
cd "$LINK"
make "$@"
```
Generate the skeleton with base's template, then trim (run once, in MSYS2, in a scratch directory,
then copy `Makefile`, `configure/` and `lssSampleGasApp/Makefile`, `lssSampleGasApp/src/Makefile`
into the repo):
```bash
mkdir -p /tmp/sgtop && cd /tmp/sgtop && /home/base-7.0.8.1/bin/windows-x64-mingw/makeBaseApp.pl -t ioc lssSampleGas
```
Edit `configure/RELEASE`: remove the `EPICS_BASE = ...` line (it comes from `RELEASE.local`), keep the
three `-include` lines at the end. Write `configure/RELEASE.local.example`:
```make
# Copy to RELEASE.local and fill in. RELEASE.local is gitignored: site paths stay out of the repo.
# Bench (MinGW):   SUPPORT=/home/support   EPICS_BASE=/home/base-7.0.8.1
# Production:      SUPPORT=<synApps support dir>   EPICS_BASE=/usr/local/epics/base
SUPPORT=<support>
EPICS_BASE=<base>
SNCSEQ=$(SUPPORT)/seq
STD=$(SUPPORT)/std
CALC=$(SUPPORT)/calc
ASYN=$(SUPPORT)/asyn
AUTOSAVE=$(SUPPORT)/autosave
SSCAN=$(SUPPORT)/sscan
```
(On production the module directories carry versions, e.g. `$(SUPPORT)/std-R3-6-4`, spec §11.2.)
Create the bench `configure/RELEASE.local` by copying the example with the bench values.
`.gitignore` in the top: copy `~/bench/smoke/.gitignore`.

- [ ] **Step 4: Minimal `src/Makefile` with a test program**

```make
TOP=../..
include $(TOP)/configure/CONFIG
# C99-conforming printf on MinGW (exact decimal expansion; sgFmt relies on it)
USR_CPPFLAGS_WIN32 += -D__USE_MINGW_ANSI_STDIO=1

# Pure-C controller core, testable without an IOC
CORE_SRCS = sgFmt.c

TESTPROD_HOST += sgFmtTest
sgFmtTest_SRCS += sgFmtTest.c $(CORE_SRCS)
sgFmtTest_SYS_LIBS_Linux += m

include $(TOP)/configure/RULES
```
`sgFmt.c`: `#include "sgFmt.h"` and nothing else yet; `sgFmt.h`: include guard only.
`sgFmtTest.c`: `int main(void) { return 0; }`.

- [ ] **Step 5: Build**

Run: `powershell -File ioc/tools/msys.ps1 ioc/tools/build.sh`
Expected: exit 0; `ioc/lssSampleGas/lssSampleGasApp/src/O.windows-x64-mingw/sgFmtTest.exe` exists.
If make fails on a path with spaces, the junction is being resolved: fall back to building a copy
(`rsync -a --delete` the top to `~/bench/lssSampleGas` in `build.sh`) and note it in `build.sh`.

- [ ] **Step 6: Commit**

```bash
git add ioc/tools ioc/lssSampleGas
git commit -m "ioc: EPICS top skeleton and bench build tooling"
```

---

### Task 2: Reference trace generator

**Files:**
- Create: `ioc/test/ref/make_traces.js`, `ioc/test/golden/.gitignore` (contents: `*` and `!.gitignore`),
  `ioc/tools/replay.sh` (trace part only)

**Interfaces:**
- Produces: `ioc/test/golden/sc<NN>.trace` for NN = 01..19 (scenario numbers as in the names; the
  array order in `SCENARIOS` puts 19 before 18), and `ioc/test/golden/summary.json`
  (`{"<n>": {"state": ..., "selftest": "pass"|<problems>}}`).

**Trace format** (one record per line, space-separated, strings last; numbers as JS `String(v)`,
`null` for null, `NaN` for NaN). Lines appear in the order the reference executed them:

| Line | Meaning | C replay action |
|---|---|---|
| `R` | `resetAll()`: reinit, enter IDLE `station reset`, cylinder and ledger state cleared | reinit + clear + `sg_enter(IDLE, "station reset")`, reset epid emulation |
| `S <mode> <target>` | parameter snapshot (`p.mode` letter, `p.target`); emitted before every `C`, and before a `T` when either changed | set `p.mode`, `p.target` directly (no log) |
| `I <t> <o2> <sevr> <flow> <sp> <running 0/1> <ramp> <total> <gas>` | analyzer value/severity and polled Alicat readbacks at this moment; emitted before every `C` and `T` | `sg_set_inputs` |
| `C <t> <name> [arg]` | an outside call into the controller: `opPurge`, `opFlowZero`, `opResume`, `opIdle`, `setTarget <v>`, `newCylinder <who>`, `ledgerEvent <type>`, `crash`, `restart` | call the core function (crash/restart: see Task 5) |
| `T <t>` | `tick(t)` | `sg_tick` (skipped while crashed) |
| `P <t> <spVal>` | `processEpid()`; `spVal` = the Setpoint record VAL at that moment (bumpless source) | `sg_pid_prepare` → emulated epid → `sg_pid_done` |
| `A <t> sp <v>` / `A <t> ramp <v>` / `A <t> run` | a put made by the controller | must match the core's next queued put |
| `L <t> <sev> <msg>` | a log line made by the controller | must match the core's next queued log line |
| `X <t> <state> <lastCmd> <OVAL> <FBON 0/1> <worstSev> <overrideCount> <cylLeftL> <cumL>` | controller state after the tick; emitted when any field changed, and every 600 s | compare (numbers to 1e-9 absolute) |

- [ ] **Step 1: Write `make_traces.js`**

It must:
1. Read `simulator/sample_gas_simulator.html`, take the text from the line starting
   `const clamp = ` up to (not including) the line containing `//  Charts`, plus the `SELFTEST`
   array (from `const SELFTEST = [` to the closing `];`). Fail loudly if a marker is missing.
2. Replace `Math.random` with a seeded mulberry32 (`seed = 12345` by default, `--seed` option) before
   evaluating, and evaluate the text with `vm.runInThisContext` inside a function wrapper that
   returns `{ Station, Controller, Plant, SCENARIOS, SELFTEST, fmtT }`.
3. Instrument (before creating stations):
   - wrap every method on `Controller.prototype` to maintain `ctlDepth` (increment on entry,
     decrement in `finally`);
   - when a method in `{opPurge, opFlowZero, opResume, opIdle, setTarget, newCylinder, ledgerEvent,
     crash, restart}` is entered with `ctlDepth === 0`, emit `S`, `I`, then `C`;
   - `tick` entered at depth 0: emit `S` (if changed), `I`, `T`; after it returns, emit `X` if
     changed or `t % 600 === 0`;
   - `processEpid` at depth 0: emit `P <t> <plant.a.spVal>`;
   - `reinit` at depth 0 → the start of `resetAll`: emit `R` (the following `enter('IDLE','station
     reset')` and ledger clearing are part of `R`, so suppress the `enter` call's own `C`; it is not
     in the recorded set anyway, but its `L` line is recorded normally);
   - wrap `Plant.prototype.putSetpoint/putRamp/putRun`: emit `A` only when `ctlDepth > 0`;
   - replace `Station.prototype.log`: emit `L <t> <sev> <msg>` only when `ctlDepth > 0`, and keep
     calling the original so `privateLog` works for the self-test checks.
4. For each scenario in `SCENARIOS`: create a fresh `Station('SELFTEST', 'TEST:SampleGas:',
   'TEST:Alicat:', 'TEST:O2', '')` with `silent = true`, run `startScenario`, step for the
   `SELFTEST` duration of that scenario (`dur * 4` steps), write the trace to
   `ioc/test/golden/sc<NN>.trace` through a buffered stream, and evaluate the `SELFTEST` row
   exactly as `runSelfTest` does (end state, regexes over the private log, `check`). Record the
   result in `summary.json`.
5. Print one line per scenario (`sc01  REGULATE  pass  3600 s  12345 lines`) and exit 1 if any
   self-test row fails.

- [ ] **Step 2: Run it**

`ioc/tools/replay.sh` (first part):
```bash
#!/bin/bash
set -e
cd "$(dirname "$0")/.."
if [ ! -f test/golden/summary.json ] || [ "$1" = "--regen" ]; then
  node test/ref/make_traces.js --html ../simulator/sample_gas_simulator.html --out test/golden
fi
```
Run: `powershell -File ioc/tools/msys.ps1 ioc/tools/replay.sh --regen`
Expected: 19 lines, all `pass` (the extracted reference reproduces its own self-test 19/19).

- [ ] **Step 3: Sanity-check a trace by eye**

Open `ioc/test/golden/sc01.trace`: it starts with `R`, then `L 0 0 — → IDLE (station reset)`, then
`S A 0.99`, then `I ...`/`T ...` pairs, with `C 5 opPurge` at t = 5 followed by
`L 5 0 IDLE → PRECHECK (operator pressed Purge)`.

- [ ] **Step 4: Commit**

```bash
git add ioc/test/ref ioc/test/golden/.gitignore ioc/tools/replay.sh
git commit -m "ioc(test): reference trace generator (19 scenarios, seeded)"
```

---

### Task 3: JS-compatible formatting (`sgFmt`)

**Files:**
- Create/replace: `src/sgFmt.h`, `src/sgFmt.c`, `src/sgFmtTest.c`

**Interfaces:**
- Produces:
```c
/* sgFmt.h: formatting that reproduces the reference's JavaScript output byte for byte. */
#ifndef SGFMT_H
#define SGFMT_H
#include <stddef.h>
/* Number.prototype.toFixed(d): exact decimal expansion, ties away from zero.
   Non-finite x gives "–" (U+2013), as the reference's fmtN. Returns buf. */
char *sg_fmtN(char *buf, size_t n, double x, int d);
/* String(x): shortest round-trip digits; decimal notation for 1e-7 <= |x| < 1e21, else
   "1.5e-7" / "1e+21" style. NaN -> "NaN". Returns buf. */
char *sg_fmtJs(char *buf, size_t n, double x);
/* fmtT: h:mm:ss of round(max(0, s)). */
char *sg_fmtT(char *buf, size_t n, double s);
/* fmtDur (hours): "–" if non-finite; < 48 h: "5.5 h" (1 decimal below 10 h, else 0); else "5.5 d". */
char *sg_fmtDur(char *buf, size_t n, double h);
#endif
```

- [ ] **Step 1: Write the failing tests** (`sgFmtTest.c`)

```c
#include <stdio.h>
#include <string.h>
#include <math.h>
#include "sgFmt.h"

static int fails = 0;
static void eq(const char *got, const char *want, const char *what) {
    if (strcmp(got, want) != 0) { printf("FAIL %s: got '%s' want '%s'\n", what, got, want); fails++; }
}
int main(void) {
    char b[64];
    eq(sg_fmtN(b, sizeof b, 0.125, 2), "0.13", "toFixed tie rounds up");        /* printf gives 0.12 */
    eq(sg_fmtN(b, sizeof b, 2.5, 0), "3", "toFixed tie 2.5");
    eq(sg_fmtN(b, sizeof b, 1.005, 2), "1.00", "toFixed 1.005 is below the tie in binary");
    eq(sg_fmtN(b, sizeof b, -0.001, 2), "-0.00", "toFixed keeps the sign");
    eq(sg_fmtN(b, sizeof b, 0.0, 1), "0.0", "toFixed zero");
    eq(sg_fmtN(b, sizeof b, 19.4449, 1), "19.4", "toFixed plain");
    eq(sg_fmtN(b, sizeof b, NAN, 2), "\xE2\x80\x93", "fmtN NaN is an en dash");
    eq(sg_fmtJs(b, sizeof b, 2.0), "2", "String(2)");
    eq(sg_fmtJs(b, sizeof b, 0.8), "0.8", "String(0.8)");
    eq(sg_fmtJs(b, sizeof b, 0.1 + 0.2), "0.30000000000000004", "String(0.1+0.2)");
    eq(sg_fmtJs(b, sizeof b, 8.8e-4), "0.00088", "String(8.8e-4)");
    eq(sg_fmtJs(b, sizeof b, 1e-5), "0.00001", "String(1e-5)");
    eq(sg_fmtJs(b, sizeof b, 1e-7), "1e-7", "String(1e-7)");
    eq(sg_fmtJs(b, sizeof b, 216000), "216000", "String(216000)");
    eq(sg_fmtJs(b, sizeof b, -9.3), "-9.3", "String(-9.3)");
    eq(sg_fmtJs(b, sizeof b, 1e21), "1e+21", "String(1e21)");
    eq(sg_fmtT(b, sizeof b, 3725.4), "1:02:05", "fmtT");
    eq(sg_fmtT(b, sizeof b, -5), "0:00:00", "fmtT negative");
    eq(sg_fmtDur(b, sizeof b, 5.5), "5.5 h", "fmtDur < 10 h");
    eq(sg_fmtDur(b, sizeof b, 30.4), "30 h", "fmtDur 10-48 h");
    eq(sg_fmtDur(b, sizeof b, 132), "5.5 d", "fmtDur days");
    printf("%s: %d failure(s)\n", fails ? "FAIL" : "PASS", fails);
    return fails ? 1 : 0;
}
```

- [ ] **Step 2: Build and run; expect link errors (functions missing)**

Run: `powershell -File ioc/tools/msys.ps1 ioc/tools/build.sh`
Expected: FAIL, undefined references to `sg_fmtN` etc.

- [ ] **Step 3: Implement `sgFmt.c`**

- `sg_fmtN`: if `!isfinite(x)` copy `"\xE2\x80\x93"`. Otherwise `snprintf(tmp, sizeof tmp,
  "%.60f", fabs(x))` (exact digits with the MinGW ANSI printf), then round the digit string at `d`
  decimals: round up iff the first dropped digit is `'5'`..`'9'` (propagate carries, possibly
  adding a leading `1`). Prefix `-` if `x < 0` (JS keeps the sign: `(-0.001).toFixed(2)` is
  `"-0.00"`; `-0.0` has `x < 0` false and prints `"0.00"`). No decimal point when `d == 0`.
- `sg_fmtJs`: NaN → `"NaN"`, ±Infinity → `"Infinity"`/`"-Infinity"`, zero → `"0"`. Otherwise for
  `p = 1..17`: `snprintf(tmp, "%.*e", p - 1, x)`, `strtod` back, stop at the first exact match.
  Split into sign, digit string `D` (no point, trailing zeros stripped) and exponent `e`. If
  `-7 <= e < 21`: decimal notation (`e >= len(D)-1`: digits then zeros; `e >= 0`: point inside;
  `e < 0`: `"0."` + `-e-1` zeros + digits). Else `D[0]` + (`"." + rest` if any) + `"e"` + sign +
  `abs(e)`, with `+` for positive exponents.
- `sg_fmtT`: `s = round(max(0, s))`; `"%d:%02d:%02d"`.
- `sg_fmtDur`: as the reference `fmtDur` (line 812), using `sg_fmtN`.

- [ ] **Step 4: Build and run the test**

Run: `powershell -File ioc/tools/msys.ps1 ioc/tools/build.sh` then
`ioc/lssSampleGas/lssSampleGasApp/src/O.windows-x64-mingw/sgFmtTest.exe`
Expected: `PASS: 0 failure(s)`.

- [ ] **Step 5: Commit**

```bash
git add ioc/lssSampleGas/lssSampleGasApp/src/sgFmt.* ioc/lssSampleGas/lssSampleGasApp/src/sgFmtTest.c
git commit -m "ioc(core): JavaScript-compatible number and time formatting"
```

---

### Task 4: Core data model, alarm machinery and the replay harness

**Files:**
- Create: `src/sgCore.h`, `src/sgCore.c` (init, reinit, enter, command, expectedFlow, purgeTimeout),
  `src/sgAlarms.c`, `src/sgEpidSim.h`, `src/sgEpidSim.c`, `src/sgReplay.c`
- Modify: `src/Makefile` (add sources and the `sgReplay` test program), `ioc/tools/replay.sh`

**Interfaces:**
- Produces `sgCore.h` (the contract every later task and Plan 2 uses):

```c
/* sgCore.h: 15LSS_sample_gas controller core. Pure C17, no EPICS.
   A port of Controller in simulator/sample_gas_simulator.html (tag sim-v1.0); names follow it. */
#ifndef SGCORE_H
#define SGCORE_H
#include <stddef.h>

enum sg_state { SG_NONE = -1, SG_IDLE, SG_PRECHECK, SG_PURGE, SG_HANDOFF, SG_REGULATE,
                SG_OPEN_LOOP, SG_FLOW_ZERO, SG_OPEN_STOP, SG_NSTATES };
/* Order = PV list of spec §7.5 (Alm:*). PINNEDLOW exists only because enter() clears it. */
enum sg_alarm { SG_A_OPENSTOP, SG_A_PURGEINC, SG_A_O2BAD, SG_A_OPENLOOP, SG_A_OVERRIDE,
                SG_A_HOLDSTUCK, SG_A_MISMATCH, SG_A_FLOWHIGH, SG_A_FLOWLOW, SG_A_PINNED,
                SG_A_O2HIGH, SG_A_NOTREACHED, SG_A_CYLLOW, SG_A_GAS, SG_A_PINNEDLOW, SG_NALARMS };
enum sg_event { SG_EV_PURGE = 1, SG_EV_ZERO = 2, SG_EV_CYLINDER = 3, SG_EV_NEWRUN = 4 };
enum sg_lidres { SG_LID_PENDING, SG_LID_PASSED, SG_LID_SKIPPED, SG_LID_OPEN };

#define SG_MSG      256
#define SG_O2HIST   1024    /* >= max(90, stallWindow + slopeAvgN + 5) = 665 */
#define SG_RATEHIST 512     /* >= lidSlopeWindow max 300 */
#define SG_AVGBUF   128     /* >= avgN max 120 */
#define SG_OVALHIST 1024    /* >= stallWindow + 1 = 601 */
#define SG_CYLHIST  6000    /* spec §7.7 He:HistT */
#define SG_LEDGER   4000    /* He:EvT */
#define SG_SNAPS    2000    /* He:SnapT */
#define SG_RUNS     100     /* He:Rep:Run* */
#define SG_NWIN     5       /* forecast windows 3, 2, 1, 0.5, 0.25 days */

typedef struct { char name[40]; double baseFlow, n, KP, KI, drvh, drvl; } sg_mode;

typedef struct {   /* spec §9.1, names = the Par:<key> suffixes; defaults in sg_default_params */
    double target, tol, delta, purgeFlow, purgeTimeoutMargin, cylWarnH, cylAlarmH, fbDelay,
           flowMinorX, flowMajorX, pinnedTime, o2AbnormalOffset, settleTimeout, V, lidOnsetFrac,
           lidOnsetMax, lidWindow, lidCurvMin, openSlopeFrac, dropSkipLevel, cylCapacityL,
           reportDays, runGap, handoffHold, purgeLag, handoffFlowTol, lidLevel, lidFilter,
           lidSlope, lidSlopeWindow, lidArmLevel, rampMaxPurge, rampMinHandoff, hardCeiling,
           purgeTimeoutMin, purgeTimeoutMax, ambientRef, flowLowX, mismatchAbs, mismatchFrac,
           mismatchMargin, holdDetect, holdRetries, holdRetryInterval, holdSlowRetry, frozenTime,
           o2Min, o2Max, avgN, pidScan, odel, gainSchedule, fineBand, fineKPx, fineKIx,
           alarmDelay, flowAlarmDelay, progressMin, stallGrace, flowSteadyBand, o2SteadyRate,
           stallWindow, slopeAvgN, stallTime;
    int mode;              /* 0..3 = A..D */
    sg_mode modes[4];
} sg_params;

typedef struct {           /* one consistent snapshot per tick (spec §8.1) */
    double o2; int o2Sevr; /* 3 = INVALID; a disconnected O2 PV is reported as 3 */
    double flow, sp, ramp, total;
    int running;           /* Running_RBV */
    char gas[40];          /* Gas_RBV state string; "He" is correct */
    int mfcConnected;      /* spec §8.18; always 1 in the reference */
} sg_inputs;

typedef struct {           /* effects; any pointer may be NULL */
    void *ctx;
    void (*log)(void *ctx, double t, int sev, const char *msg);
    void (*put_setpoint)(void *ctx, double v);
    void (*put_ramp)(void *ctx, double v);
    void (*put_run)(void *ctx);
} sg_io;

typedef struct { int sev; char msg[SG_MSG]; double since, until; int active; } sg_alarm_slot;
typedef struct { int cur, cand; double since; int used; } sg_deb;
typedef struct { double VAL, CVAL, KP, KI, DRVL, DRVH, ODEL, OVAL; int FBON; } sg_epid;
typedef struct { double t; int type; double L; } sg_ledger_ev;
typedef struct { double t, L; } sg_snap;
typedef struct { double t, used; } sg_cylpt;
typedef struct { double days, h, rate; const char *why; } sg_cylest;   /* h NaN = none */

typedef struct {           /* per-state data (JS this.sd); NaN = unset */
    double t0, o2Start, timerT0, below, elapsed, onsetT, cOn, kin, curv, kObs, checkAt, cCheck,
           est, timeout, t1;
    int dropChecked, lidResult, phaseDelay;   /* phaseDelay: HANDOFF 0 = settle, 1 = delay */
} sg_sd;

typedef struct {
    double start, end, L, startL, endL;       /* end NaN = open */
    int purges, cylinders, finished;
} sg_run;
typedef struct {
    sg_run runs[SG_RUNS]; int nRuns;           /* runs in the window */
    double wStart, wEnd, dispensed, cylEquiv, inRuns; int cyls;
} sg_report;

typedef struct sg_ctl {
    sg_params p; sg_io io; sg_inputs in;
    double now; int state; sg_sd sd; char lastAction[SG_MSG]; long heartbeat;
    sg_alarm_slot alarms[SG_NALARMS]; sg_deb deb[SG_NALARMS];
    double o2, lastO2; int o2ok, sameCount, frozen;
    double o2hist[SG_O2HIST]; int nO2;
    double rateHist[SG_RATEHIST]; int nRate; double maxRate; int aboveCount, lidArmed;
    double avgBuf[SG_AVGBUF]; int nAvg; int blind;
    double lastCmd, lastCmdTime, flowAtCmd;          /* lastCmd NaN = none yet */
    double spSeen, spChangeT, flowAtSpChange;
    int holdSec, holdAttempts, holdRecovering; double nextHoldAttempt;
    int pinnedSec, pinnedLowSec;
    int settling, settleDir, stallSec, stallLatched; double settleT0, towardRate, o2Slope;
    double ovalHist[SG_OVALHIST]; int nOval; int inFine;
    sg_epid epid;
    long overrideCount; char overrideLog[20][SG_MSG]; double overrideT[20]; int nOverride;
    /* helium state (autosaved in the IOC) */
    double cylBase, cylLeftL, cylMedianH;             /* NaN = unset */
    sg_cylpt cylHist[SG_CYLHIST]; int nCyl;
    sg_cylest cylEst[SG_NWIN]; int nCylEst;           /* 0 until the first 60 s sample */
    double cumL, lastTotal;                           /* lastTotal NaN = unset */
    sg_ledger_ev ledger[SG_LEDGER]; int nLedger;
    sg_snap snaps[SG_SNAPS]; int nSnaps;
} sg_ctl;

typedef struct { double VAL, KP, KI, DRVL, DRVH, ODEL, CVAL; } sg_epid_cfg;

extern const char *const sg_state_names[SG_NSTATES];   /* "IDLE", ... */
extern const char *const sg_state_desc[SG_NSTATES];    /* reference STATES[].desc */

void   sg_default_params(sg_params *p);                /* defaultControllerParams + slots C, D */
void   sg_init(sg_ctl *c, const sg_io *io, double now);/* defaults, empty helium state, reinit,
                                                          enter IDLE "IOC started" */
void   sg_reinit(sg_ctl *c, double now);               /* JS reinit(): keeps params, helium
                                                          state and overrideCount */
void   sg_set_inputs(sg_ctl *c, const sg_inputs *in);
void   sg_tick(sg_ctl *c, double now);                 /* JS tick() */
int    sg_pid_due(const sg_ctl *c, double now);        /* now % max(1, round(pidScan)) == 0 */
int    sg_pid_prepare(sg_ctl *c, sg_epid_cfg *cfg);    /* 0 if avgBuf empty; else configEpid,
                                                          CVAL = mean(avgBuf), fill cfg */
void   sg_pid_done(sg_ctl *c, double oval);            /* epid.OVAL = oval; command(oval) if FBON */
void   sg_restart(sg_ctl *c, double now);              /* JS restart(), spec §8.15 steps 2-4 */
void   sg_enter(sg_ctl *c, int state, const char *reason);
int    sg_op_purge(sg_ctl *c);                         /* return 1 if accepted */
int    sg_op_flow_zero(sg_ctl *c);
/* Resume Flow (spec §8.5): OPEN_LOOP with o2ok → HANDOFF (the reference's opResume, Task 5);
   IDLE with o2ok and o2 < lidLevel → REGULATE from the current setpoint (Task 8). On rejection
   returns 0 and writes the rejection text to why (may be NULL). */
int    sg_op_resume_flow(sg_ctl *c, char *why, size_t n);
int    sg_op_idle(sg_ctl *c);
void   sg_set_target(sg_ctl *c, double v, const char *who);
void   sg_set_mode(sg_ctl *c, int mode, const char *who);     /* Task 8 */
void   sg_new_cylinder(sg_ctl *c, const char *who);
void   sg_ledger_event(sg_ctl *c, int type);
void   sg_mark_new_run(sg_ctl *c);                            /* Task 8: event + log */
void   sg_reset_override_count(sg_ctl *c);                    /* Task 8 */
double sg_expected_flow(const sg_ctl *c);
double sg_purge_timeout(const sg_ctl *c);
double sg_litres_at(const sg_ctl *c, double t);
void   sg_usage_report(const sg_ctl *c, sg_report *r);
int    sg_worst_sev(const sg_ctl *c);
/* alarm machinery (sgAlarms.c) */
void   sg_set_alarm(sg_ctl *c, int k, int sev, const char *msg);
void   sg_clear_alarm(sg_ctl *c, int k, int silent);
void   sg_latch(sg_ctl *c, int k, int sev, const char *msg, double dur);
void   sg_expire_alarms(sg_ctl *c);
void   sg_debounce(sg_ctl *c, int k, int level, const char *msg1, const char *msg2, double delay);
void   sg_override(sg_ctl *c, const char *msg);
void   sg_banner(const sg_ctl *c, char *buf, size_t n);       /* most severe first, "; ", or
                                                                 "No alarms" */
/* logging helper used throughout the core: formats with vsnprintf and calls io.log */
void   sg_log(sg_ctl *c, int sev, const char *fmt, ...);
#endif
```

- Produces `sgEpidSim.h` (tests only):
```c
/* devEpidSoft emulation, identical to the reference's processEpid (line 1459). Test use only:
   in the IOC the real epid record does this. */
#ifndef SGEPIDSIM_H
#define SGEPIDSIM_H
#include "sgCore.h"
typedef struct { double I, P, D, ePrev, lastT, OVAL; int fbonPrev; } sg_epid_sim;
void   sg_epid_sim_reset(sg_epid_sim *e);   /* I = P = D = ePrev = OVAL = 0, lastT NaN, fbonPrev 0 */
/* One processing. outl = the Setpoint record VAL (bumpless source). Returns the new OVAL. */
double sg_epid_sim_process(sg_epid_sim *e, const sg_epid_cfg *cfg, int fbon, double now,
                           double pidScan, double outl);
#endif
```

- [ ] **Step 1: Write `sgReplay.c`** (the test that drives all later tasks)

Behaviour:
- `sgReplay [--max-diffs N] trace...`; for each trace file, print `sc01: PASS (n lines)` or the
  first N differences as `sc01:<line>: <what> got '<core>' want '<ref>'`, then exit 1 if any failed.
- An `sg_io` whose callbacks append to a FIFO of produced records: logs as `L <sev> <msg>`, puts
  as `A sp <v>` / `A ramp <v>` / `A run` (numbers formatted with `sg_fmtJs`).
- Process lines in order. Before executing an `R`/`S`/`I`/`C`/`T`/`P` line, the FIFO must be
  empty; leftover records are reported as `extra output`. An `L`/`A` line pops the FIFO head and
  compares exactly (time is informational; `A` values compare numerically within 1e-9). A missing
  record is reported as `missing output`.
- `KNOWN_DIFFS`: a table of `{ reference text, core text, spec section }`, applied to `L` lines
  before comparing:
  `{"IOC started via start_ioc (autosaved settings restored)", "IOC started (autosaved settings restored)", "§8.15"}`,
  `{"operator pressed Resume PID", "operator pressed Resume Flow", "§8.5"}` (substring
  replacement: it appears inside `OPEN_LOOP → HANDOFF (operator pressed Resume PID)`).
- `C <t> opResume` calls `sg_op_resume_flow(c, NULL, 0)`; `C <t> setTarget <v>` calls
  `sg_set_target(c, v, "operator")` (the trace does not carry `who`; ledger ruling R3).
- Line actions: see the trace-format table in Task 2. Crash: set `down = 1`, push
  `L 2 IOC crashed: controller stopped, Alicat holds its last setpoint` itself (the reference logs
  it from inside `crash()`), and skip `T`/`P` lines while down. Restart: `down = 0`,
  `sg_restart(now)`, reset the epid emulation. `R`: `sg_reinit`, clear helium state (cylBase,
  cylHist, cylEst, cylMedianH, cylLeftL = NaN/empty; cumL 0; lastTotal NaN; ledger and snapshots
  empty), `sg_enter(c, SG_IDLE, "station reset")`, reset the epid emulation.
- `P`: `if (sg_pid_prepare(c, &cfg)) { oval = sg_epid_sim_process(&e, &cfg, c->epid.FBON, t,
  c->p.pidScan, spVal); sg_pid_done(c, oval); }`.
- `X`: compare `state` name, `lastCmd`, `OVAL`, `FBON`, `worstSev`, `overrideCount`, `cylLeftL`,
  `cumL`; `null`/`NaN` in the trace means the core value must be NaN.
- At the end of each trace, also apply the scenario's `SELFTEST` row to the core's own output
  (end state; the `has` regexes as substring alternatives split on `|` with `\?` unescaped; checks
  17 and 19 through `nCylEst`/`cylEst[].h` and `sg_usage_report`). The rows are a static table in
  `sgReplay.c` copied from lines 2225-2245, with `operator pressed Resume PID` changed to
  `operator pressed Resume Flow` in rows 10 and 11 (spec §14.2).

Makefile additions:
```make
CORE_SRCS = sgFmt.c sgCore.c sgAlarms.c sgChecks.c sgPid.c sgLedger.c
TESTPROD_HOST += sgReplay
sgReplay_SRCS += sgReplay.c sgEpidSim.c $(CORE_SRCS)
sgReplay_SYS_LIBS_Linux += m
```
(Create `sgChecks.c`, `sgPid.c`, `sgLedger.c` as files with the functions stubbed, so it links;
stubs: `sg_pid_prepare` returns 0, `sg_pid_done` does nothing, ledger functions do nothing,
`sg_usage_report` zeroes the report.)

`ioc/tools/replay.sh` second part:
```bash
BIN=lssSampleGas/lssSampleGasApp/src/O.windows-x64-mingw
"$BIN/sgReplay" --max-diffs 5 test/golden/sc*.trace
```

- [ ] **Step 2: Implement the data model and machinery** (port; names follow the reference)
  - `sg_default_params`: every value of `defaultControllerParams` (lines 831-879) and the mode
    table of spec §7.4 (slots C, D = copies of A named `Spare C`, `Spare D`).
  - `sg_init`, `sg_reinit` (line 1042; `lastCmd`, `cylBase`, `lastTotal`, etc. = NaN for null),
    `sg_enter` (line 1135), `command` (static, line 1101: `io.put_setpoint`), `sg_expected_flow`
    (1100), `sg_purge_timeout` (1120), `sg_set_target` (1082), `startSettling` (static, 1086).
  - `sgAlarms.c`: `setAlarm`, `clearAlarm`, `latch`, `expireAlarms`, `debounce`, `override`,
    `worstSev` (1058-1098). Insertion order matters for the banner: keep a per-alarm `since` and
    sort the banner by severity (desc), then `since` (asc).
  - The en dash and arrow texts in `enter()`: `"%s → %s (%s)"` with `—` when `prev` is `SG_NONE`.
  - `sg_epid_sim_process`: exactly lines 1463-1480 minus the `configEpid`/`CVAL` part (the caller
    passed `cfg`) and minus `command` (the caller calls `sg_pid_done`).

- [ ] **Step 3: Build and run the replay**

Run: `powershell -File ioc/tools/msys.ps1 ioc/tools/build.sh` then
`powershell -File ioc/tools/msys.ps1 ioc/tools/replay.sh`
Expected: it runs to completion and reports FAIL for most scenarios; the first line of every trace
(`— → IDLE (station reset)`) already matches, which proves the harness and `sg_enter`.

- [ ] **Step 4: Commit**

```bash
git add ioc/lssSampleGas/lssSampleGasApp/src ioc/tools/replay.sh
git commit -m "ioc(core): data model, alarm machinery, reference replay harness"
```

---

### Task 5: Tick, states and commands

**Files:**
- Modify: `src/sgCore.c`

**Interfaces:**
- Consumes: Task 4 header. Produces the behaviour of `sg_tick`, `sg_restart`, `sg_op_*`.

- [ ] **Step 1: Port** (each a static function unless in the header)
  - `sg_tick` (line 1166): `now`, `heartbeat++`, `readInputs`, `sg_expire_alarms`, `holdMonitor`
    if state ≠ IDLE, `lidDetector`, the state switch, then ledgerUpdate, cylForecast, alarmChecks
    (declared in sgLedger.c / sgChecks.c; stubs until Tasks 6-7).
  - `readInputs` (1189): o2hist/rateHist/avgBuf as arrays with shift-left on overflow (JS
    `push` + `shift`); `maxRate = max(0, max(rateHist))`.
  - `lidDetector` (1207), `stopOpen` (1217), `holdMonitor` (1307), `doPrecheck` (1328),
    `doPurge` (1340, including the lid check; `sd.kin` string `'skipped'` → `lidResult =
    SG_LID_SKIPPED`, numeric → store in `kin`), `doHandoff` (1391), `sg_restart` (1492; log text
    `IOC started (autosaved settings restored)` per spec §8.15), `sg_op_purge`,
    `sg_op_flow_zero`, `sg_op_idle` (1483-1486), and the OPEN_LOOP branch of
    `sg_op_resume_flow` (the reference's `opResume`, 1485, with the log reason
    `operator pressed Resume Flow`; any other state returns 0 for now, with `why` =
    `Resume Flow ignored in <STATE>`; the IDLE branch comes in Task 8).
  - Reference quirks to keep: `sd.o2Start` is the O2 of the current tick's `readInputs` (the
    value current when `enter('PURGE')` runs inside `doPrecheck`; corrected 2026-09-25 after
    Task 5 review); `!(sd.o2Start >= dropSkipLevel)` is true for NaN;
    the handoff `phase` starts as settle; `flowAtCmd` uses the current snapshot's flow.
  - `ledgerEvent` is called by `enter` (purge, zero); implement `sg_ledger_event` for real in
    Task 7, but its append is trivial, so do it now.

- [ ] **Step 2: Build and replay**

Expected: PASS for scenarios 1, 2, 3, 4, 5, 8, 9, 10, 11, 12, 13, 14, 18 **up to the first
difference caused by the still-stubbed alarmChecks/epid/ledger**. Use `--max-diffs 1` and read the
first difference of each: it must be in an alarm/PID/ledger line, not in states or commands. Fix
anything else before moving on.

- [ ] **Step 3: Commit**

```bash
git commit -am "ioc(core): tick, state machine, lid detector, hold monitor, purge and restart"
```

---

### Task 6: Alarm checks and the PID step

**Files:**
- Modify: `src/sgChecks.c`, `src/sgPid.c`

- [ ] **Step 1: Port**
  - `alarmChecks` (1403-1457) into `sgChecks.c`: mismatch tracking, settling slope and
    `stallSec`, `ovalHist`, `flowSteady`, settled/timeout/stalled logic, `flowHigh`/`flowLow`
    debounce with `flowAlarmDelay`, `pinnedSec`, `pinnedLowSec` and its one-shot note, `o2High`.
    Messages use `sg_fmtJs` for parameter values (`${p.flowMinorX}` etc.) and `sg_fmtN` where the
    reference uses `fmtN`.
  - `configEpid` (1106) and `sg_pid_prepare`/`sg_pid_done` into `sgPid.c`. `sg_pid_prepare`:
    return 0 if `nAvg == 0`; configEpid; `epid.CVAL = mean(avgBuf)` (sum in index order, as JS
    `reduce`); copy to `cfg`. `sg_pid_done`: `epid.OVAL = oval`; if `epid.FBON` then
    `command(oval)`.

- [ ] **Step 2: Build and replay**

Expected: scenarios 1-16 and 18 PASS, apart from differences in ledger/forecast lines (stubbed until
Task 7): with `--max-diffs 1`, the first difference in 6, 7, 15, 16 is gone.

- [ ] **Step 3: Commit**

```bash
git commit -am "ioc(core): alarm checks, settling logic and PID step"
```

---

### Task 7: Helium ledger, forecast and usage report

**Files:**
- Modify: `src/sgLedger.c`

- [ ] **Step 1: Port**
  - `ledgerUpdate` (1219), `sg_litres_at` (1230), `sg_usage_report` (1238-1272), `sg_new_cylinder`
    (1273), `cylForecast` (1279-1306). Fixed arrays with shift-left trimming, capacities from
    `sgCore.h`; if an array is full, drop the oldest entry (log nothing; the capacities exceed what
    the trim rules keep).
  - `usageReport` grouping, in C: build the purge list, then walk it exactly as the JS closure does
    (`close()` computes `nextPurge`, `zero`, `finished`, `end`, `endL`, `cylinders`). Runs beyond
    `SG_RUNS` are dropped from the front (oldest), keeping the window logic intact.

- [ ] **Step 2: Build and replay**

Expected: **all 19 scenarios PASS**, including the self-test rows (17: ≥ 3 forecast windows; 19: 2
runs, the first with 23 purges).

- [ ] **Step 3: Commit**

```bash
git commit -am "ioc(core): helium ledger, run-out forecast and usage report; replay 19/19"
```

---

### Task 8: Behaviour beyond the reference (spec additions)

**Files:**
- Modify: `src/sgCore.c`, `src/sgLedger.c`, `src/Makefile`
- Create: `src/sgUnitTest.c`

**Interfaces:**
- Produces: the IDLE branch of `sg_op_resume_flow`, `sg_set_mode`, `sg_mark_new_run`, `sg_reset_override_count`,
  the `mfcConnected` handling, and text helpers used by Plan 2:
```c
/* in sgCore.h */
void sg_forecast_text(const sg_ctl *c, char *buf, size_t n);   /* He:ForecastText */
void sg_progress_text(const sg_ctl *c, char *buf, size_t n);   /* Sts:Progress */
```

- [ ] **Step 1: Write the failing tests** (`sgUnitTest.c`), each building a controller with
  `sg_init`, feeding hand-made `sg_inputs` and ticks, and checking results:

```c
/* 1. Resume Flow from IDLE with valid O2 below lidLevel: REGULATE, FBON, lastCmd = sp,
      no put at the switch, log "IDLE → REGULATE (operator pressed Resume Flow)". */
/* 2. Resume Flow from IDLE with O2 = 12 %: rejected, why = "Resume Flow ignored: O2 12.00 %
      above the lid threshold 10 %: purge first", state stays IDLE. */
/* 3. Resume Flow with O2 INVALID (IDLE, and OPEN_LOOP): why = "Resume Flow ignored: O2 invalid". */
/* 4. Resume Flow in PURGE: why = "Resume Flow ignored in PURGE". */
/* 5. sg_set_mode(c, 1, "operator"): log "operator: enclosure mode → Collimator lid". */
/* 6. sg_mark_new_run: ledger gains an SG_EV_NEWRUN event and logs
      "admin: start of a new user run marked". */
/* 7. mfcConnected = 0 for holdDetect + 1 ticks in REGULATE: Mismatch MAJOR with
      "MFC not responding (CA disconnected)"; in IDLE: no alarm. On reconnect: lastCmd re-sent
      (put_setpoint called once) and the alarm clears. */
/* 8. sg_forecast_text with no estimates and < 6 h of data:
      "run-out forecast: collecting data (needs ≥ 6 h)"; with 5 estimates 132..134.4 h:
      "empty in 5.5 d–5.6 d (median 5.5 d, 5 windows)". */
```
Write each as a function returning 0/1 with a printed `FAIL <n>: ...` line, and `main` summing
them, as in `sgFmtTest.c`. Add to the Makefile:
```make
TESTPROD_HOST += sgUnitTest
sgUnitTest_SRCS += sgUnitTest.c $(CORE_SRCS)
sgUnitTest_SYS_LIBS_Linux += m
```

- [ ] **Step 2: Build and run; expect failures**

- [ ] **Step 3: Implement**
  - IDLE branch of `sg_op_resume_flow` (spec §8.5): allowed in IDLE with `o2ok` and
    `o2 < lidLevel`; then `lastCmd = in.sp`, `lastCmdTime = now`, `flowAtCmd = in.flow`,
    `sg_enter(REGULATE, "operator pressed Resume Flow")`, configEpid, `epid.FBON = 1`. The
    "station configured" condition is the IOC layer's (Plan 2). Rejection texts per spec §8.5.
  - `sg_set_mode`, `sg_mark_new_run`, `sg_reset_override_count` (log `settings: override count
    reset`, as the reference UI).
  - §8.18 in `sg_tick`: count ticks with `!mfcConnected` while state ∉ {IDLE}; at > `holdDetect`
    raise Mismatch MAJOR `MFC not responding (CA disconnected)` and skip the flow-mismatch
    evaluation; puts are still requested (the IOC layer drops them while disconnected). On the
    first connected tick after such an alarm, re-send `lastCmd` if known, clear the alarm, log
    `MFC reconnected: setpoint <lastCmd:.2f> re-sent`.
  - `sg_forecast_text` (spec §7.1) and `sg_progress_text` (the simulator's progress line; find it
    by searching the HTML for `progress` in the render code and port its text).

- [ ] **Step 4: Build, run unit tests and the replay**

Expected: `sgUnitTest` PASS 8/8, and the replay still 19/19 (the additions must not change
reference behaviour).

- [ ] **Step 5: Commit**

```bash
git add -A ioc/lssSampleGas/lssSampleGasApp/src
git commit -m "ioc(core): Resume Flow from IDLE, mode change, new-run mark, MFC disconnect handling"
```

---

## Self-review notes

- Spec coverage for Plan 1: §8.1-§8.17 behaviour (core), §8.5 ResumeFlow, §8.18 (core part),
  §14.4 (golden vectors, via full replay instead of per-function vectors: stronger, same intent).
  Not in Plan 1: §6/§7 PVs, §8.19 persistent log, §8.20 write gate, §8.21 PV names, §10 autosave,
  §8.15 steps 0-1: all IOC-layer, Plan 2.
- Divergence from spec §8.1 command ordering: the core applies operator commands before
  `readInputs`, exactly as the reference does; the spec accepts acting at the next tick, and this is
  the reference's order. Plan 2's SNL calls `sg_op_*` before `sg_tick` in the same tick.
