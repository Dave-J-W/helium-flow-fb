# Installing and testing 15LSS_sample_gas on the IOC host

The IOC serves `15IDC:SampleGas:` and drives the Alicat `15IDC:Alicat1:` from the O2 reading
`15IDC:D1Dmm_calc`. It installs into the ChemMat area of the synApps tree,
`/home/chem_epics/chemmatCARS/synApps/support/ChemMat`.

**Read this first.** A normal start (`start_ioc`) is *live*: if helium is already flowing (O2
below 10 % and the Alicat setpoint above 0) it takes over the Alicat within seconds. So the first
start below is a shadow start, which writes nothing. Stop the old Phoebus timer script before
you switch to live: two writers fight over the setpoint.

## 1. Get the code

Clone only the IOC and its screens from GitHub:

```bash
cd /home/chem_epics/chemmatCARS/synApps/support/ChemMat
git clone --sparse --filter=blob:none \
    https://github.com/Dave-J-W/helium-flow-fb.git lssSampleGas
cd lssSampleGas
git sparse-checkout set ioc/lssSampleGas ioc/screens
git log -1 --oneline                        # the version you install
```

The IOC top is then `/home/chem_epics/chemmatCARS/synApps/support/ChemMat/lssSampleGas/ioc/lssSampleGas`, and the screens are in
`/home/chem_epics/chemmatCARS/synApps/support/ChemMat/lssSampleGas/ioc/screens`. If git says `unknown option --sparse`, it is older than 2.27;
leave out `--sparse --filter=blob:none` and the `sparse-checkout` line. That clones the whole
repo (about 15 MB), and everything else stays the same.

## 2. Build

```bash
cd /home/chem_epics/chemmatCARS/synApps/support/ChemMat/lssSampleGas/ioc/lssSampleGas
make 2>&1 | tee build.log
lssSampleGasApp/src/O.linux-x86_64/sgUnitTest | tail -1     # PASS: 46/46
lssSampleGasApp/src/O.linux-x86_64/sgIocTest  | tail -1     # PASS: 30/30
```

No configuration is needed: `configure/RELEASE` already has this host's paths (base
`/usr/local/epics/base`, seq R2-2-9, asyn R4-44-2, calc R3-7-5, sscan R2-11-6, std R3-6-4,
autosave R5-11). The same versions build without warnings on gcc 11 (checked on Ubuntu
22.04). If `make` fails, send the last 40 lines of `build.log`.

## 3. First start: shadow, in a terminal

Check that no `15LSS_sample_gas` is already running under procServ, then:

```bash
cd /home/chem_epics/chemmatCARS/synApps/support/ChemMat/lssSampleGas/ioc/lssSampleGas
cd iocBoot/iocLSS_sample_gas
./startLSSSampleGasTest
```

This is the production IOC in **shadow mode**: it reads everything and writes nothing. The
console is this terminal; type `exit` to stop it. Its settings go to `autosave-test/`, so
nothing from the test carries over to the production start. Within a few seconds you should
see:

- `PV names: MFC 15IDC:Alicat1:, O2 15IDC:D1Dmm_calc, CYL none`
- `start in shadow mode (FORCE_SHADOW): Par:writeEnable set to 0`
- a state line, e.g. `— → IDLE (restart: setpoint is 0 (§4.7))`

If you see `waiting for the Alicat PVs ...` instead, the Alicat IOC isn't up or its PV names
differ; the controller keeps waiting and decides once they connect.

From another terminal, compare with the O2 and Alicat panels you already have:

```bash
caget 15IDC:SampleGas:Sts:O2 15IDC:SampleGas:Sts:Flow 15IDC:SampleGas:Sts:SetpointRBV
caget 15IDC:SampleGas:Sts:State 15IDC:SampleGas:Sts:TickAge
```

`Sts:TickAge` should be 0 or 1 (seconds since the controller last ticked).

## 4. Screens

The displays are in `/home/chem_epics/chemmatCARS/synApps/support/ChemMat/lssSampleGas/ioc/screens`. Open `sampleGas_simple.bob` (everyday) or
`sampleGas_main.bob` (full panel) with the macro `P=15IDC:SampleGas:`, e.g. from an action button
in one of your menu displays ("Open display", macros `P = 15IDC:SampleGas:`). The displays open
each other and pass `P` on. `docs/ioc/OPERATOR_GUIDE.md` in the repo explains every field.

In shadow mode both panels show a SHADOW MODE badge and the banner shows the MAJOR
`shadow mode: the controller is not writing to the Alicat`.

## 5. Test sequence

Have helium on and the enclosure closed. **Flow Zero** stops the helium at any point.

1. **Watch in shadow** for a few minutes: readings match, `Sts:TickAge` stays 0–1.
2. **Stop the old timer script.**
3. **Go live:** Admin → **Live (on)** → confirm. It goes to IDLE; the valve doesn't move.
4. **Take over:** **Purge** (from air: purge at 20 SLPM, lid check, handoff, regulation in about
   6–7 minutes), or **Resume Flow** if the box is already purged (O2 below 10 %).
5. **Regulate**: O2 settles at the target (0.99 %), in-range LED green.
6. **Flow Zero**: the Alicat goes to 0.
7. **Finish:** `exit` in the IOC terminal. The Alicat keeps its last setpoint.

## 6. Production start

1. Add the `start_ioc` line (check port 20125 is free in `IOCLIST`). It is one line; in the PDF
   it only wraps because of the page width:

   ```
   15LSS_sample_gas  20125  1  /home/chem_epics/chemmatCARS/synApps/support/ChemMat/lssSampleGas/ioc/lssSampleGas/iocBoot/iocLSS_sample_gas/startLSSSampleGas
   ```
2. Keep the old timer script off, and make sure the test IOC has exited.
3. `start_ioc 15LSS_sample_gas`. It is live. With the Alicat at 0 it waits in IDLE for Purge or
   Resume Flow; with helium flowing and the box purged it resumes regulating at once.
4. Alarm server: load `ioc/screens/sampleGas_alarms.xml`. The archiver PV list is
   `ioc/screens/archive_pvs.txt`.

After a crash (procServ does not restart it), start it again with `start_ioc`; the Alicat holds
its setpoint meanwhile and the restart resumes without a bump.

## 7. Updating

```bash
cd /home/chem_epics/chemmatCARS/synApps/support/ChemMat/lssSampleGas
git pull
cd ioc/lssSampleGas && make 2>&1 | tee build.log
```

Then restart the IOC (`start_ioc 15LSS_sample_gas`, or `exit` in its console). `git pull` keeps
the IOC's settings (`autosave/`) and logs; they are not in the repo.

## Troubleshooting

| You see | What to do |
|---|---|
| `waiting for the Alicat PVs (...): controller not acting yet` | The Alicat IOC is down, or its PV names differ: `caget 15IDC:Alicat1:Setpoint_RBV`. The names can be changed on Deep admin. |
| `not configured: Cfg:... is empty` | A PV name on Deep admin is empty. |
| `MFC gas table is ..., not He` | Set the Alicat's gas table to helium. |
| `MFC flow units are ..., not SLPM` | Set the Alicat's flow units to SLPM. |
| `flow mismatch: cylinder empty or MFC fault?` | The flow doesn't follow the setpoint: cylinder valve, pressure, regulator. |
| `Alicat setpoint ... does not follow the controller` | A write was lost or something else writes the setpoint (the old timer script?). |
| `controller not ticking` (red over the banner) | The IOC answers but the controller stopped: restart the IOC. |
| Screens all magenta | The IOC isn't running, or Phoebus can't reach it (CA address list). |
