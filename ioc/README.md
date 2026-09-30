# 15LSS_sample_gas IOC

The helium-purge and O2-feedback controller for the 15-ID-C sample enclosure: an EPICS IOC
(epid + an SNL program + a C controller core) that drives the Alicat MFC `15IDC:Alicat1:` from
the O2 reading `15IDC:D1Dmm_calc`. It replaces the timer script in Phoebus.

- **Operators:** [docs/ioc/OPERATOR_GUIDE.md](../docs/ioc/OPERATOR_GUIDE.md), with screenshots.
- **Specification** (the authority, with the user's decisions): [docs/ioc/15LSS_sample_gas_IOC_spec.md](../docs/ioc/15LSS_sample_gas_IOC_spec.md).
- **Release notes:** [RELEASE.md](RELEASE.md).

## Layout

| Path | What |
|---|---|
| `lssSampleGas/` | The EPICS top (app name `lssSampleGas`; the IOC is `15LSS_sample_gas`) |
| `lssSampleGas/lssSampleGasApp/src/sgCore.c`, `sgChecks.c`, `sgPid.c`, `sgLedger.c` | The controller core: pure C, no EPICS, a port of the reference simulator's `Controller` |
| `…/src/sgIoc.c`, `sgGate.c`, `sgCfg.c`, `sgStart.c`, `sgLogFile.c`, `sgText.c` | The IOC glue: dbAccess, the single write gate, PV names, start-up wait, log files |
| `…/src/sampleGas.st` | The SNL program: Channel Access, the 1 Hz tick, the gated puts |
| `…/src/sgUnitTest.c`, `sgIocTest.c`, `sgReplay.c` | Core unit tests, glue tests, and the trace replay against the reference |
| `…/Db/sampleGas.db`, `*.req`, `…/src/sgPvTable.*` | **Generated** from `tools/sg_pvs.py`: do not edit |
| `lssSampleGas/iocBoot/iocLSS_sample_gas/` | `st.cmd` (bench), `st.cmd.pc` (PC trial), `st.cmd.production` + `startLSSSampleGas` (beamline) |
| `screens/` | The four Phoebus displays and the alarm-server configs (**generated** by `tools/gen_screens.py`), `archive_pvs.txt` |
| `tools/` | Build, run, replay and check scripts; the PV table and the generators; `doc_screens.py` |
| `test/` | The caproto plant simulator, the bench harness, the scenario acceptance tests, `doc_session.py` |

## Build on the bench (this Windows PC, MinGW)

EPICS base 7.0.8.1 and synApps modules are built under MSYS2 (`~/epics-sim-env.sh`). The repo
path has a space, which GNU make cannot handle, so `build.sh` builds through a per-worktree
junction under `~/bench/`. From PowerShell:

```powershell
powershell -File ioc/tools/msys.ps1 ioc/tools/build.sh
```

Don't rebuild while an IOC from this tree is running: the install overwrites the running exe
and DLL.

## Tests

| Test | Command (PowerShell, repo root) | Expect |
|---|---|---|
| Core unit tests | `ioc\lssSampleGas\lssSampleGasApp\src\O.windows-x64-mingw\sgUnitTest.exe` | 44/44 |
| Glue tests | `…\O.windows-x64-mingw\sgIocTest.exe` | 30/30 |
| Replay against the reference, 19 scenarios, twice (also at epoch-time offsets) | `powershell -File ioc/tools/msys.ps1 ioc/tools/replay.sh` | 19/19, 0 differences |
| Generators (db, screens, alarm XML) against the spec tables | `python -m unittest test_gen_db test_gen_screens` in `ioc/tools` | OK |
| Generated files up to date | `python gen_db.py --check`, `python gen_screens.py --check` | up to date |
| Plant simulator | `python -m unittest test_plant_model test_plant_server` in `ioc/test` | OK |
| Heartbeat staleness, bench IOC + simulator (stops the controller with `seqStop`) | `python -m unittest -v test_tickage` in `ioc/test` | OK, about 2 min |
| PV names (spec §14.3a), bench IOC + two-plant simulator | `python -m unittest -v test_pvnames` in `ioc/test` | OK, about 11 min |
| Write enable, shadow mode and Resume Flow (spec §14.3b, §14.5 shadow check), bench IOC + simulator | `python -m unittest -v test_write_enable` in `ioc/test` | OK, about 4 min |
| Scenario acceptance, real time, bench IOC + simulator | `$env:SG_SCENARIOS='2,3,8'; python -m unittest test_scenarios` in `ioc/test` | PASS; results in `ioc/test/results/` |
| Comparison with the reference, noise off (spec §14.2: transition times ±3 s, flows ±0.02 SLPM, lid ratio ±0.05), scenarios 1, 2, 3, 8, bench IOC + simulator | `$env:SG_COMPARE='2,3,8'; python -m unittest -v test_compare` in `ioc/test` (default all four; needs `golden/nonoise/`, made by `node test/ref/make_traces.js --no-noise`, or by the test itself) | OK; tables in `ioc/test/results/compare-sc*.txt`; about 1 h 55 min for all four |

Python is the bench venv (`%USERPROFILE%\.venvs\bluesky\Scripts\python.exe`) with
`PYTHONIOENCODING=utf-8`. The full scenario suite takes about 14 h; the short set (2, 3, 8, 9,
12, 13, 14) about 2.5 h. See `test/README.md` for the simulator, ports and confinement.

## Run on the bench

`ioc/tools/run_ioc.sh` starts the bench IOC (`SIM:SampleGas:`, CA server port 5076) against the
simulator (`ioc/test/plant_sim.py`, port 5066). `bench.py` does both for the tests. Check a
running IOC from outside, read-only:

```powershell
python ioc/tools/check_ioc.py SIM:SampleGas: "127.0.0.1:5076"
```

## PC trial against the real enclosure

`ioc/tools/run_ioc_pc.sh` runs the IOC on this PC against the live PVs, serving
`LSSPC:SampleGas:` on localhost only. It is read-only unless started with `--allow-writes`; even
then every start is in shadow mode, and writes can only go to `15IDC:Alicat1:`. The user starts
it (never an agent), and writes need the user present. The CA address list comes from the
uncommitted `tools/beamline_env.local.sh`.

## Screens

Open with macro `P` (`15IDC:SampleGas:` at the beamline). To run a second Phoebus next to your
own, see `screens/pc_phoebus.ini` and the `phoebus` Claude skill.
The screenshots in `docs/ioc/img/` come from `python ioc/test/doc_session.py`, which runs the
bench, its own Phoebus instances (`tools/doc_screens.py`) and a purge-to-alarm sequence (about
35 min).

## Change a PV, parameter or screen

Edit `tools/sg_pvs.py` (and, for layout, `tools/gen_screens.py`), then:

```powershell
cd ioc/tools; python gen_db.py; python gen_screens.py; python -m unittest test_gen_db test_gen_screens
```

The generator tests read the parameter table and the Deep admin groups from the spec, so a
parameter change goes into spec §9.1 / §13 too. Rebuild to install the new database.

## Install at the beamline (Linux soft-IOC host)

Follow [docs/ioc/INSTALL.md](../docs/ioc/INSTALL.md). In short:
1. A sparse clone of this repo (only `ioc/lssSampleGas` and `ioc/screens`) goes into
   `/home/chem_epics/chemmatCARS/synApps/support/lssSampleGas`.
2. Run `make` in its `ioc/lssSampleGas`. `configure/RELEASE` already has the host's paths.
3. Make a shadow-mode first start in a terminal (`startLSSSampleGasTest`), then the test
   sequence.
4. Add the `start_ioc` line (port 20125), then `start_ioc 15LSS_sample_gas`.
5. To update: `git pull`, `make`, restart.
