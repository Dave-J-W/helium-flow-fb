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

Step by step, with the shadow-mode commissioning start (`startLSSSampleGasTest`) and a test
sequence: [docs/ioc/INSTALL.md](../docs/ioc/INSTALL.md). In short:

1. Put this top at `<support>/ChemMat/lssSampleGas/` (its own top), from GitHub or a tarball of
   `ioc/lssSampleGas`.
2. No configuration: `configure/RELEASE` has the production host's paths
   (`SUPPORT=/home/chem_epics/chemmatCARS/synApps/support`, base `/usr/local/epics/base`, seq
   R2-2-9, std R3-6-4, calc R3-7-5, asyn R4-44-2, autosave R5-11, sscan R2-11-6). Only for a
   moved tree, a `configure/RELEASE.local` (see `RELEASE.local.production.example`).
3. `make` in the top (gcc, `linux-x86_64`).
4. Add the `start_ioc` line (check that the port is free in `IOCLIST`):
   `15LSS_sample_gas   20125   1  <support>/ChemMat/lssSampleGas/iocBoot/iocLSS_sample_gas/startLSSSampleGas`
5. **Stop the old timer script first:** two writers would fight over the setpoint.
6. `start_ioc 15LSS_sample_gas`. It starts live (writes enabled). With the Alicat at 0 it enters
   IDLE; if the Alicat IOC isn't up yet, it waits under a MAJOR alarm and acts once it connects.
7. Load `screens/sampleGas_alarms.xml` into the alarm server; give the archiver
   `screens/archive_pvs.txt`.
8. Place the `.bob` files and open them with `P=15IDC:SampleGas:`.
