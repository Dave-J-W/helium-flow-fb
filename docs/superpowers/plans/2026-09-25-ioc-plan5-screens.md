# 15LSS_sample_gas IOC, Plan 5: Phoebus screens, alarms, archiving, README

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The four Phoebus displays approved as mockups on 2026-09-25 (simple, full, Admin, Deep
admin), the alarm-server configuration, the archiver PV list, and `ioc/README.md`, all verified
against the running bench IOC in the locally installed Phoebus.

**Architecture:** A generator (`ioc/tools/gen_screens.py`) writes the `.bob` XML from the same PV
table as the database (`ioc/tools/sg_pvs.py`, Plan 2), so every parameter field, limit, unit and
group on the Admin and Deep admin screens is guaranteed to match a real record. The simple and full
panels are laid out by hand-written layout code in the same generator (fixed geometry per widget).
Only macro `P` is used; the Alicat is read through the `Sts:` mirrors (spec §13.1).

**Tech Stack:** Phoebus 4.7.4 (installed at `C:\Program Files\phoebus-win`, bundled JDK), Display
Builder `.bob` format (XML, `<display version="2.0.0">`), Python 3.12 venv.

**Spec:** §13.0-§13.4, §14.5 ("screens render against the bench IOC"). Mockups: the four panels shown
to the user on 2026-09-25 (simple panel; full panel with one Resume Flow button and no cylinder
button; Admin with the write switch, New He cylinder fitted, parameters, per-mode gains, overrides,
usage report, internals; Deep admin with PV names, modes and nine groups). **Depends on:** Plan 2
(PV table, running IOC).

## Global Constraints

- Never modify the user's Phoebus installation, settings or existing screens (spec §1, §11.4).
  Launch Phoebus for verification with a separate settings file (`-settings ioc/screens/bench.ini`
  setting `org.phoebus.pv.ca/addr_list=127.0.0.1:5064 127.0.0.1:5066` and
  `auto_addr_list=false`) and a separate workspace directory under the scratch area.
- Verification opens windows on the user's desktop: close every Phoebus process started by the test
  (by PID, never by image name).
- **The user's own Phoebus is running** (a `java.exe` with the beamline `settings.ini`, connected to
  beamline IOCs). Phoebus forwards `-resource` to an already-running instance through its server
  port (default 4918), which would open bench screens inside the beamline Phoebus. Always launch the
  bench instance with a different server port (`-server 4999`) and verify, before opening any
  display, that the new process is a separate PID using the bench settings. Never interact with,
  signal or close the user's Phoebus.
- Only macro `P` in the displays. Plan 1-3 constraints apply.

## File map

```
ioc/tools/gen_screens.py        table + layout → .bob files; --check
ioc/screens/
  sampleGas_simple.bob          §13.0
  sampleGas_main.bob            §13.1
  sampleGas_admin.bob           §13.2 (write switch with confirmation both ways)
  sampleGas_deep.bob            §13.3 (PV names, modes, nine groups)
  sampleGas_alarms.xml          §13.4 alarm-server config: Alm:* PVs + heartbeat staleness
  archive_pvs.txt               §13.4 archiver list with rates
  bench.ini                     Phoebus settings for the bench (localhost CA only)
ioc/tools/screenshot_phoebus.ps1  launch Phoebus on a display, wait, capture the window, close
ioc/README.md                   build, bench, tests, screens, production install (§14.5)
```

### Task 1: Screen generator and the two admin screens

- [ ] Step 1: failing test `ioc/tools/test_gen_screens.py`: every `Par:*`, `Mode:*` and `Cfg:*`
  PV of the table appears on exactly the screen its level says (U: main/simple; A: Admin; D: Deep
  admin); the nine Deep admin group titles appear in order with their keys; the `.bob` files parse as
  XML; `gen_screens.py --check` exits 0.
- [ ] Step 2: implement the generator for Admin and Deep admin (text entry widgets with units and
  labels from the table; group boxes; the linked-PV table with edit field, active name, connection
  LED and greyed default; Apply PV names and Restore defaults buttons; `Par:writeEnable` as two
  action buttons each with a confirmation (`confirm_dialog` true, the §13.2 texts); New He cylinder
  fitted, Mark new user run, Release control, Reset override count; text-update widgets for
  `Diag:OverrideLog`, `He:Rep:Text`, forecast windows, epid fields).
- [ ] Step 3: pass; commit.

### Task 2: Simple and full panels

- [ ] Step 1: extend the test: the simple panel contains exactly the PVs of §13.0 (and no `PID`,
  `Sts:SetpointRBV`, `Sts:LastCmd`); the full panel contains the §13.1 PVs, one Resume Flow button
  with the enabling rule, no New He cylinder button; both show the SHADOW MODE badge rule.
- [ ] Step 2: implement the layouts (readout boxes, banner coloured by `Sts:WorstSevr` via a rule,
  state label coloured per state via rules matching the simulator's `STATES` colours, in-range LED,
  buttons writing 1 to `Cmd:*`, Resume Flow enabled by a rule on `Sts:State` and `Sts:O2Valid`, a
  strip-chart of `Sts:O2` and `Sts:Flow` with log axes and a target line on the full panel).
- [ ] Step 3: pass; commit.

### Task 3: Render against the bench IOC

- [ ] Step 1: `screenshot_phoebus.ps1 -Display <bob> -Macros "P=SIM:SampleGas:" -Out <png>`: starts
  Phoebus from the install with `-settings ioc/screens/bench.ini -resource "<file>?P=..."`, waits for
  the window, captures it with System.Drawing, closes the process by PID.
- [ ] Step 2: with the plant simulator and the IOC running (Plan 4's `Bench`), capture all four
  displays in IDLE and in REGULATE, and one with an alarm active; check each image by eye (no
  disconnected-PV borders, texts legible, layout as the mockups). Fix and repeat.
- [ ] Step 3: commit the screenshots under `docs/ioc/img/` (small PNGs) and the script.

### Task 4: Alarm config, archiver list, README

- [ ] Step 1: `sampleGas_alarms.xml`: one component per station with every `Alm:*` PV (with its
  description), and a heartbeat staleness alarm on `Sts:Heartbeat` (stale > 10 s), plus the IOC
  disconnect. Validate it parses as XML and lists every `Alm:*` PV from the table (test).
- [ ] Step 2: `archive_pvs.txt`: `Sts:O2`, `Sts:State`, `Sts:Flow`, `Sts:SetpointRBV`, `Sts:LastCmd`
  at 1 s, every `Alm:*` on change, `Cfg:Active:*` on change (spec §13.4), per station prefix.
- [ ] Step 3: `ioc/README.md` (spec §14.5): what the IOC is, the three layers, how to build on the
  bench, how to run the bench (plant simulator + IOC), how to run each test suite and the replay,
  the screens and how to open them, the production install steps (spec §15.1, the `start_ioc` line
  of §11.3), and the rules (shadow mode default, no writes until the user lifts the rule).
- [ ] Step 4: commit.
