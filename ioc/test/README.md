# Plant simulator (bench)

`plant_sim.py` is a caproto Channel Access server that plays a helium-purged sample enclosure,
its O2 analyzer and its Alicat MFC, on `127.0.0.1` only. It lets a real IOC (or a test harness
standing in for one) be pointed at a bench plant instead of the real beamline hardware.

It is a server, not a client: it never talks to any PV other than the ones it serves itself, and
it refuses to serve anything that looks like a real beamline name (see "15ID rule" below).

## Starting it

Source the bench CA environment first, in whichever shell you're using, then run the server with
the absolute venv Python (`python` on `PATH` is a broken stub on this box).

Before starting, check nothing is already listening on the port you want (see "Known Windows
limitation" below):

```powershell
netstat -ano | findstr 5066
```

If that prints nothing, the port is free.

PowerShell:

```powershell
. .\ioc\tools\bench_env.ps1
```

```powershell
& "$env:USERPROFILE\.venvs\bluesky\Scripts\python.exe" ioc\test\plant_sim.py
```

MSYS2 bash:

```bash
source ioc/tools/bench_env.sh
```

```bash
"$USERPROFILE/.venvs/bluesky/Scripts/python.exe" ioc/test/plant_sim.py
```

Default config serves one plant at `SIM:Alicat1:` / `SIM:O2` / `SIM:World:`. Add `--second` for a
second plant at `SIM:Alicat2:` / `SIM:O2b` / `SIM:World2:`, or `--plant
ALICAT_PREFIX,O2_PV,WORLD_PREFIX` (repeatable) for custom names. `--seed N` seeds the RNG,
`--no-noise` zeroes the noise and wander terms, `--port` overrides the default port (5066).

`--phase <s>` (default `0.75`) shifts when the 1 Hz publish (`sample_analyzer`, `poll_alicat`,
and the PV writes) happens: at whole wall-clock second + `phase`, not on the second itself. An
IOC's own 1 Hz tick samples its inputs at (or near) the whole second (spec Sec 8.1); publishing
at exactly the same instant means the IOC randomly sees this second's or the previous second's
readback (jitter ~15 ms + CA latency). The default gives a fixed ~250 ms margin after the whole
second before the IOC's own sample point, and a fixed (not racing) latency from the reader's
point of view. Valid range is informal -- pick something in `(0, 1)` s; the server does not
validate it.

On success it prints `plant_sim.py: serving N PVs on 127.0.0.1:<port>` to stderr and keeps running
until killed (Ctrl-C, or `proc.terminate()`/`taskkill` from a test harness).

## Why port 5066

Two CA servers on one Windows host cannot share a port for unicast name searches. The bench IOC
serves on 5076 (`EPICS_CAS_SERVER_PORT`; 5064 is left to the PC IOC connected to the real
beamline, Plan 4 ruling P4-R1); this simulator uses 5066, so a client searches both
(`bench_env.ps1` / `bench_env.sh` set `EPICS_CA_ADDR_LIST="127.0.0.1:5076 127.0.0.1:5066"`).

## Bench harness and scenario acceptance (Plan 4)

`bench.py` (`with Bench(noise=True, seed=1) as b:`) starts this simulator and the bench IOC
(through `ioc/tools/run_ioc.sh`), sets `Par:writeEnable` 1, and offers PV, command, state and
log helpers; it kills only the processes it started. `test_scenarios.py` runs the spec §14.2
scenarios (`scenarios.py`) in real time; `SG_SCENARIOS=2,3,8` selects some. Results go to
`results/sc<NN>-<timestamp>.json` (gitignored), IOC and plant console output next to them.
The PC IOC (`run_ioc_pc.sh`, port 5064) may keep running meanwhile.

## Confinement gates (why it might refuse to start, exit code 2)

The server binds to `127.0.0.1` only and never broadcasts its CA beacon off-box. Before opening
any socket it checks all of the following and prints `plant_sim.py: refusing to start: ...` to
stderr and exits 2 if any fails:

| Check | Refuses when | If unset |
|---|---|---|
| Beamline-looking PV names | any Alicat/O2/World prefix starts with `15ID` (case-insensitive) | n/a |
| `EPICS_CAS_INTF_ADDR_LIST` | set to anything other than `127.0.0.1` | sets it to `127.0.0.1` itself |
| `EPICS_CAS_BEACON_ADDR_LIST` | set to anything other than `127.0.0.1` | sets it to `127.0.0.1` itself |
| `EPICS_CAS_AUTO_BEACON_ADDR_LIST` | set to anything other than `NO` (case-insensitive) | sets it to `NO` itself |
| Beacon list caproto actually resolved | `caproto.get_beacon_address_list()` returns any address other than `127.0.0.1` (belt-and-suspenders re-check right before serving) | -- |
| Duplicate PV names | two plants (e.g. two `--plant` configs, or `--second` colliding with a custom `--plant`) would serve the same PV name | -- |
| Existing listener on the port | a plain TCP connect to `127.0.0.1:<port>` succeeds *before* this server tries to bind -- catches the stale-second-instance case below, which the next check cannot | -- |
| Bound TCP port | the port caproto actually bound is not the one requested -- caproto silently falls back to a random port if the requested one is busy; this is treated as "something else is already listening", not silently accepted | -- |

The last check happens after sockets are already open, so that one is a hard `os._exit(2)`
(no cleanup to preserve) instead of a clean `sys.exit(2)`.

`EPICS_CAS_INTF_ADDR_LIST` alone does **not** confine caproto's periodic CA beacon (a UDP
broadcast, separate from the TCP/UDP listener) -- that needs the two
`EPICS_CAS_*BEACON_ADDR_LIST` variables above too. `bench_env.ps1`/`bench_env.sh` set all of
these; the checks above exist so the server is still safe if someone runs it without sourcing
either file.

Note: a libca *client* (pyepics, `caget`/`caput`, this repo's tests) spawns its own `caRepeater`
process bound to `0.0.0.0:5065` to receive beacons -- that is the client's socket, not this
server's, and it only receives; it never sends anything off-box. Seeing it in `netstat` next to
this server is expected and is not a confinement violation.

Before opening any socket the server also probes `127.0.0.1:<port>` itself with a plain TCP
connect: if something answers, it refuses to start (exit 2, "another CA server already listens
on ..."). See "Known Windows limitation" below for why this probe exists in addition to the
bound-port check above.

## The 15ID rule

This simulator is bench-only. It must never be started with a prefix beginning `15ID` -- that
namespace belongs to the real beamline IOC and PVs there are not to be touched by this tool.
Every `--plant` prefix (Alicat, O2, World) is checked; any one starting `15ID` (any case) is a
refusal, not a warning.

## PV table

One plant serves the PVs below under its own `<A>` (Alicat prefix), `<O2>` (single O2 PV name)
and `<W>` (World prefix). Defaults for plant 0: `<A>` = `SIM:Alicat1:`, `<O2>` = `SIM:O2`, `<W>` =
`SIM:World:`. With `--second`, plant 1 defaults to `SIM:Alicat2:` / `SIM:O2b` / `SIM:World2:`.
Readbacks update at 1 Hz, at whole wall-clock second + `--phase` seconds (default 0.75 s into
the second, not on the second itself -- see `--phase` above).

| PV | Type | Behaviour |
|---|---|---|
| `<A>Setpoint` | float, PREC 3, EGU SLPM | put quantises to 0.01 SLPM and always changes the PV's own value (VAL), even while held; only reaches the device (and therefore `Flow_RBV`) when the Alicat is running |
| `<A>Setpoint_RBV` | float, read-only | device's actual setpoint, 1 Hz |
| `<A>Flow_RBV` | float, PREC 2, read-only | actual flow, 1 Hz |
| `<A>Total_RBV` | float, read-only | cumulative standard litres, never resets, 1 Hz |
| `<A>Running_RBV` | enum `Paused`/`Running`, read-only | 1 Hz |
| `<A>Status` | string, read-only | `HLD` when not running, else empty |
| `<A>RampRate` | float | put sets the ramp rate (SLPM/s); 0 means unlimited (instant) |
| `<A>RampRate_RBV` | float, read-only | current ramp rate |
| `<A>Run` | int | any put resumes the device (the "C"/cancel-hold command); a `stuck` hold blocks it until `ClearHold` |
| `<A>Gas_RBV` | enum, 16 states, read-only | see "Gas_RBV placeholder" below |
| `<A>FlowUnits_RBV` | string, read-only | always `SLPM` |
| `<O2>` | float, PREC 4, EGU % | O2 reading, 1 Hz; severity goes INVALID (status UDF) in `invalid` analyzer mode; in `frozen` mode the same value is re-posted unchanged every second |
| `<W>LiftLid` | int | put `1` opens the lid; any other value is ignored |
| `<W>CloseLid` | int | put `1` closes the lid; any other value is ignored |
| `<W>CrackLid` | int | put `1` closes the lid but leaves it cracked; any other value is ignored |
| `<W>Reseat` | int | put `1` re-randomizes the lid seal factor; any other value is ignored |
| `<W>ClearHold` | int | put `1` clears a `stuck` hold and resumes; any other value is ignored |
| `<W>BreathDip` | int | put `1` dips ambient O2 by 0.6 % for 60 s; any other value is ignored |
| `<W>LidType` | enum `A`/`B` | selects the ingress model (`A` = normal lid, `B` = collimator lid) |
| `<W>Hold` | enum `current`/`open`/`stuck` | `current` holds at present flow, `open` holds at `holdOpenFlow` (0.7 SLPM), `stuck` also blocks `Run` until `ClearHold` |
| `<W>SetRamp` | float | same underlying ramp as `<A>RampRate` -- a world-level way to change the device's ramp rate |
| `<W>Gas` | enum, 16 states | sets the device's gas directly (`<A>Gas_RBV` reflects it at the next 1 Hz poll) |
| `<W>CylPressure` | float | put sets the cylinder pressure (psi); reads back at 1 Hz as the cylinder drains |
| `<W>AnalyzerMode` | enum `normal`/`invalid`/`frozen` | see `<O2>` above |
| `<W>Preset` | float | jumps straight to steady state at the given flow (Alicat already running at that flow) -- for fast test setup, not a physical action |
| `<W>Seed` | int | put reseeds the RNG (test determinism, not physical) |
| `<W>Noise` | enum `Off`/`On` | zeroes or restores the white-noise and slow-wander terms |
| `<W>Bulk` | float, read-only | true bulk enclosure O2 concentration -- diagnostic only, not what the analyzer PV reports (test use only) |
| `<W>Time` | float, read-only | plant time in seconds since the server started |

### Gas_RBV / World:Gas placeholder

The real Alicat gas table (`ipApp/Db/Alicat_BC.db`) is not vendored into this repo. The spec only
constrains index 7 = `He`; the simulator's 16-entry list is a reasonable guess at the standard
Alicat table for the other 15 entries, not sourced from the deployed db. Nothing in this
simulator or its tests depends on any entry other than index 7.

### World: examples

```powershell
caput SIM:World:LiftLid 1
```

```powershell
caput SIM:World:Hold current
```

```powershell
caput SIM:World:Preset 0.29
```

```powershell
caput SIM:World:AnalyzerMode invalid
```

## Regenerating the reference and running the tests

`test_plant_model.py` checks the Python plant against a JSON reference generated from the
original browser simulator (`simulator/sample_gas_simulator.html`) by a Node script. Regenerate
it (needs Node inside MSYS2; run from the repo root):

```powershell
powershell -File ioc/tools/msys.ps1 ioc/tools/plant_ref.sh
```

That writes `ioc/test/golden/plant_openloop.json`. If it's missing, `test_plant_model.py` skips
the reference-comparison tests (the rest of it still runs) with a message telling you to run
`plant_ref.sh` first.

Run both test files with the absolute venv Python, from `ioc/test`:

```powershell
& "$env:USERPROFILE\.venvs\bluesky\Scripts\python.exe" -m unittest -v test_plant_model
```

```powershell
& "$env:USERPROFILE\.venvs\bluesky\Scripts\python.exe" -m unittest -v test_plant_server
```

`test_plant_server.py` sets its own bench CA environment on `os.environ` before importing
`epics`, so it does not need `bench_env.ps1`/`bench_env.sh` sourced first. It starts its own
`plant_sim.py --no-noise --port 5066` subprocess -- make sure nothing else is already listening
on 5066 (see below) before running it, or you will get two servers each unaware of the other.

## Known Windows limitation: two instances can share a port

On this platform, `SO_REUSEADDR` lets a second process bind a `LISTENING` socket to a port a
first process is already listening on -- both `netstat -ano` lines show `LISTENING`, and no
error is raised on either side. caproto's own busy-port fallback (which this server checks for,
see the confinement-gates table above) is never triggered, because Windows never reports the
port as busy in the first place. Both instances are still loopback-only, so this is not a
127.0.0.1-confinement problem, but which instance actually answers a given client connection is
undefined.

The server now protects itself against this (see "Existing listener on the port" in the
confinement-gates table): before doing anything else it tries a plain TCP connect to
`127.0.0.1:<port>`, and refuses to start (exit 2) if anything answers. This catches a stale
second instance even though Windows' own bind-time check cannot.

Still check for an existing listener before starting one (`netstat -ano | findstr 5066`) as a
first line of defence -- it is cheaper than starting a process only to have it refuse.
