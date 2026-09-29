# lssSampleGas (15LSS_sample_gas) Release Notes

## R1-0 (in preparation; first hardware tests 2026-09-28)
Initial release: the helium purge and O2 feedback controller for the 15-ID sample enclosure,
replacing the timer script. Specification: `docs/ioc/15LSS_sample_gas_IOC_spec.md`.
- Controller logic in a pure-C core (`sgCore.c`, `sgChecks.c`, `sgPid.c`, `sgLedger.c`), a
  port of the reference simulator's `Controller`.
  - Verified by replaying 19 recorded reference scenarios with zero differences, including
    epoch-time replay.
  - 35 unit tests cover the paths no scenario reaches.
- States IDLE, PRECHECK, PURGE, HANDOFF, REGULATE, OPEN_LOOP, FLOW_ZERO and OPEN_STOP.
  - The purge has a lid check (decay rate and curvature) and a lag-corrected handoff.
  - REGULATE is epid PID control with gain scheduling and a fine band.
  - A lid detector, an MFC hold monitor, and settling and flow alarms.
- Helium ledger, cylinder run-out forecast and a usage report, all from the Alicat totalizer.
- One IOC, one or more stations. The Alicat, O2 and cylinder PV names are editable, autosaved
  fields (`Cfg:*`), so 15IDE can be added later without code changes.
- Write switch `Par:writeEnable` (Live / Shadow), autosaved, default Live.
  - Shadow mode runs every rule and logs what it would write.
  - All Alicat writes go through one gate in the SNL program; no database link points at the
    Alicat.
- Production start-up (`iocBoot/iocLSS_sample_gas/st.cmd.production`, `startLSSSampleGas` for
  `start_ioc`) acts on start and after restarts.
  - Restarts resume regulation bumplessly.
  - If the Alicat is late, the controller waits for it under a MAJOR alarm; it does not settle
    in IDLE.
- PC trial start-up (`st.cmd.pc`, `ioc/tools/run_ioc_pc.sh`):
  - read-only unless `--allow-writes`;
  - starts in shadow;
  - writes are pinned to `15IDC:Alicat1:`;
  - the server is confined to 127.0.0.1.
- Phoebus displays (`ioc/screens/`): simple panel, full panel, Admin and Deep admin, generated
  from the PV table. Also an alarm-server configuration and an archiver PV list.
- Bench: a caproto plant simulator (`ioc/test/plant_sim.py`) with the reference physics, and an
  acceptance harness running the reference scenarios in real time.
- Modules: base 7.0.8.1, seq R2-2-9, std R3-6-4 (epid), calc R3-7-5, asyn R4-44-2,
  autosave R5-11, sscan R2-11-6 (production loadout:
  `configure/RELEASE.local.production.example`).
