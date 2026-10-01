"""Bench test for plant_sim.py: a real CA client (pyepics) against the caproto plant server.

Starts plant_sim.py as a subprocess with the bench CA environment (ioc/tools/bench_env.ps1,
mirrored in BENCH_ENV below) applied to os.environ, and drives it exactly as a CA client (or a
real IOC under test) would: caput/caget/monitor on the SIM:* PVs only. Never touches anything
outside that set, and confirms with netstat that the server's own listener -- and its CA beacon,
a separate confinement setting caproto reads (see plant_sim.py CAPROTO_NOTES and
task-3-report.md fix round 1) -- are bound/targeted to 127.0.0.1 only, not the beamline network.

pyepics loads its bundled libca on import and libca reads the EPICS_CA_* environment variables
at that time, so BENCH_ENV must land in os.environ *before* `import epics` -- see the
beamline-python-env skill notes and CAPROTO_NOTES in plant_sim.py.

Run from this directory: python -m unittest -v test_plant_server
"""

import os

# ---------------------------------------------------------------------------------------------
# Bench CA environment -- mirrors ioc/tools/bench_env.ps1 (which mirrors ~/epics-sim-env.sh plus
# the two overrides that let a client see both bench servers on 127.0.0.1:5064 and :5066).
# Must be applied to os.environ before `import epics` below.
BENCH_ENV = {
    'EPICS_HOST_ARCH': 'windows-x64-mingw',
    # 5064/5066 are the bench_env.ps1/.sh pair (IOC / this simulator's default port); 5073 and
    # 5076 are extra plant_sim.py instances this test file starts on non-default ports and then
    # talks to as a real CA client (TestPutterValidationOverCA, TestSecondPlantAndLiftLidAction
    # Rule) -- libca reads this list once, at first PV access, so every port a client ever
    # needs to name-search must be here before that happens. Ports used only for subprocess
    # exit-code / log-readiness checks (no actual get/put from this process, e.g.
    # TestBeaconConfinement's 5070 or TestStaleInstanceDetection's 5075) do not need to be
    # listed.
    # 5074: TestPresetAndPutCounter (own PV names, so it can run next to a bench plant)
    'EPICS_CA_ADDR_LIST': '127.0.0.1:5064 127.0.0.1:5066 127.0.0.1:5073 127.0.0.1:5074 127.0.0.1:5076',
    'EPICS_CA_AUTO_ADDR_LIST': 'NO',
    'EPICS_CAS_INTF_ADDR_LIST': '127.0.0.1',
    'EPICS_CAS_BEACON_ADDR_LIST': '127.0.0.1',
    'EPICS_CAS_AUTO_BEACON_ADDR_LIST': 'NO',
    'EPICS_PVA_ADDR_LIST': '127.0.0.1',
    'EPICS_PVA_AUTO_ADDR_LIST': 'NO',
    'EPICS_PVAS_INTF_ADDR_LIST': '127.0.0.1',
    'EPICS_PVAS_BEACON_ADDR_LIST': '127.0.0.1',
    'EPICS_PVAS_AUTO_BEACON_ADDR_LIST': 'NO',
}
os.environ.update(BENCH_ENV)

import asyncio  # noqa: E402
import math  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
import time  # noqa: E402
import unittest  # noqa: E402

import epics  # noqa: E402  (must be imported after BENCH_ENV is applied)

HERE = os.path.dirname(os.path.abspath(__file__))
PLANT_SIM = os.path.join(HERE, 'plant_sim.py')
PORT = 5066

if HERE not in sys.path:
    sys.path.insert(0, HERE)
import plant_sim  # noqa: E402  (module under test, for the pure-function unit tests below)


# ---------------------------------------------------------------------------------------------
# Module-level helpers shared by every TestCase below (process lifecycle and PID resolution).

def _open_log_files():
    """Two on-disk temp files for a child's stdout/stderr -- never unread PIPEs.

    An unread subprocess.PIPE can deadlock (the child blocks once its OS pipe buffer fills), and
    even short of that, plain PIPE handles give no way to inspect output after a hard kill.
    Files are readable at any time by re-opening the path, independent of the write handle.
    """
    stdout_f = tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix='.plant_sim.stdout.log')
    stderr_f = tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix='.plant_sim.stderr.log')
    return stdout_f, stderr_f


def _read_log(f):
    try:
        with open(f.name) as fh:
            return fh.read()
    except Exception as exc:
        return f'<could not read {f.name}: {exc}>'


def _terminate_and_close(proc, stdout_f, stderr_f):
    """Cleanup for one plant_sim.py subprocess: terminate, kill after a timeout, close+delete
    the log files. Registered with addClassCleanup/addCleanup immediately after Popen so it
    still runs if anything between Popen and the end of the test raises."""
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    for f in (stdout_f, stderr_f):
        try:
            f.close()
        except Exception:
            pass
        try:
            os.unlink(f.name)
        except OSError:
            pass


def _descendant_pids(root_pid, max_depth=4):
    """PIDs of root_pid and everything it (recursively) spawned.

    On this machine `sys.executable` (.venvs/bluesky/Scripts/python.exe) is a venv relaunch
    stub whose pyvenv.cfg points at the anaconda3 base interpreter: launching it via
    subprocess.Popen spawns the stub, which itself launches the base interpreter as a real
    *child* process, and it is that child (not the stub) which actually imports caproto and
    binds the listening socket -- confirmed with `Get-CimInstance Win32_Process | select
    ProcessId,ParentProcessId,CommandLine` (task-3-report.md). So `Popen.pid` alone never
    appears in netstat for this server; walking the process tree with Win32_Process
    (psutil-free, per the brief) is required. Process termination is unaffected by this
    (Windows Job Object semantics already bring the child down with the stub -- verified
    separately), only this identity check is.
    """
    pids = {root_pid}
    frontier = [root_pid]
    for _ in range(max_depth):
        if not frontier:
            break
        filt = ' or '.join(f'ParentProcessId={p}' for p in frontier)
        cmd = ['powershell', '-NoProfile', '-Command',
               f'(Get-CimInstance Win32_Process -Filter "{filt}").ProcessId']
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        found = [int(tok) for tok in result.stdout.split() if tok.strip().isdigit()]
        frontier = [p for p in found if p not in pids]
        pids.update(frontier)
    return pids


class TestPlantServer(unittest.TestCase):
    """5 tests against a single shared plant_sim.py subprocess (server startup is not free)."""

    @classmethod
    def setUpClass(cls):
        env = os.environ.copy()
        cls.stdout_f, cls.stderr_f = _open_log_files()
        cls.proc = subprocess.Popen(
            [sys.executable, PLANT_SIM, '--no-noise', '--port', str(PORT)],
            cwd=HERE, env=env,
            stdout=cls.stdout_f, stderr=cls.stderr_f, text=True,
        )
        # Registered immediately after Popen: any exception below (including the
        # wait_for_connection failure path) still leaves this cleanup scheduled, so the child
        # is never leaked even if setUpClass itself raises.
        cls.addClassCleanup(_terminate_and_close, cls.proc, cls.stdout_f, cls.stderr_f)

        cls.o2_connect_pv = epics.PV('SIM:O2')
        connected = cls.o2_connect_pv.wait_for_connection(timeout=10)
        if not connected:
            raise RuntimeError(
                'SIM:O2 did not connect within 10 s; server failed to start\n'
                f'stdout={_read_log(cls.stdout_f)!r}\nstderr={_read_log(cls.stderr_f)!r}')

    # ---- Step 2.1: Flow_RBV updates about once a second -------------------------------------
    def test_1_flow_rbv_updates_about_once_a_second(self):
        counts = {'n': 0}

        def _cb(**kwargs):
            counts['n'] += 1

        pv = epics.PV('SIM:Alicat1:Flow_RBV')
        self.assertTrue(pv.wait_for_connection(timeout=5))
        pv.add_callback(_cb)
        time.sleep(0.3)     # let any connection-time callback settle
        counts['n'] = 0
        time.sleep(5.0)
        pv.clear_callbacks()
        self.assertGreaterEqual(counts['n'], 4, f'too few Flow_RBV updates in 5 s: {counts["n"]}')
        self.assertLessEqual(counts['n'], 6, f'too many Flow_RBV updates in 5 s: {counts["n"]}')

    # ---- Step 2.2: Preset reaches target flow and O2 -----------------------------------------
    def test_2_preset_reaches_target_flow_and_o2(self):
        from plantsim.plant import Plant   # local import: only needed for this test's reference

        ret = epics.caput('SIM:World:Preset', 0.29, wait=True, timeout=5)
        self.assertEqual(ret, 1)
        time.sleep(3.0)

        flow = epics.caget('SIM:Alicat1:Flow_RBV')
        o2 = epics.caget('SIM:O2')

        ref_plant = Plant(noise=False)
        expected_o2 = ref_plant.steady_at(0.29)

        self.assertAlmostEqual(flow, 0.29, delta=max(0.05 * 0.29, 0.01),
                                msg=f'Flow_RBV={flow} did not settle near 0.29')
        rel = abs(o2 - expected_o2) / max(abs(expected_o2), 1e-3)
        self.assertLessEqual(rel, 0.05, f'SIM:O2={o2} vs steady_at(0.29)={expected_o2}')

    # ---- Step 2.3: Hold / Setpoint-while-held / Run semantics --------------------------------
    def test_3_hold_setpoint_ignored_then_run_resumes_old_value(self):
        ret = epics.caput('SIM:World:Hold', 'current', wait=True, timeout=5)
        self.assertEqual(ret, 1)
        time.sleep(2.0)

        running = epics.caget('SIM:Alicat1:Running_RBV')
        status = epics.caget('SIM:Alicat1:Status', as_string=True)
        self.assertEqual(running, 0, 'Running_RBV should be 0 (Paused) while held')
        self.assertEqual(status, 'HLD')

        sp_before = epics.caget('SIM:Alicat1:Setpoint_RBV')

        ret = epics.caput('SIM:Alicat1:Setpoint', 0.8, wait=True, timeout=5)
        self.assertEqual(ret, 1)
        time.sleep(1.5)
        sp_after_put = epics.caget('SIM:Alicat1:Setpoint_RBV')
        self.assertAlmostEqual(sp_after_put, sp_before, places=3,
                                msg='Setpoint_RBV must not move while the Alicat is on hold')

        ret = epics.caput('SIM:Alicat1:Run', 1, wait=True, timeout=5)
        self.assertEqual(ret, 1)
        time.sleep(2.0)
        running_after = epics.caget('SIM:Alicat1:Running_RBV')
        self.assertEqual(running_after, 1, 'Running_RBV should be 1 (Running) after Run')
        sp_after_run = epics.caget('SIM:Alicat1:Setpoint_RBV')
        self.assertAlmostEqual(sp_after_run, sp_before, places=3,
                                msg='Run resumes the device\'s OLD setpoint, not the held put')

    # ---- Step 2.4: AnalyzerMode invalid -> INVALID severity -----------------------------------
    def test_4_analyzer_mode_invalid_gives_severity_3(self):
        ret = epics.caput('SIM:World:AnalyzerMode', 'invalid', wait=True, timeout=5)
        self.assertEqual(ret, 1)
        time.sleep(2.0)

        pv = epics.PV('SIM:O2')
        self.assertTrue(pv.wait_for_connection(timeout=5))
        pv.get(use_monitor=False)
        self.assertEqual(pv.severity, 3, f'SIM:O2 severity={pv.severity}, expected INVALID (3)')

        # leave the analyzer sane for anything run after this file
        epics.caput('SIM:World:AnalyzerMode', 'normal', wait=True, timeout=5)

    # ---- Step 2.5: TCP listener bound to 127.0.0.1 only ---------------------------------------
    def test_5_server_bound_to_localhost_only(self):
        pids = _descendant_pids(self.proc.pid)
        result = subprocess.run(['netstat', '-ano'], capture_output=True, text=True)
        pid_strs = {str(p) for p in pids}
        pid_lines = [line for line in result.stdout.splitlines()
                     if line.split() and line.split()[-1] in pid_strs]
        self.assertTrue(pid_lines,
                         f'no netstat lines found for pids {pids} (root {self.proc.pid}):\n'
                         f'{result.stdout}')
        checked = 0
        for line in pid_lines:
            parts = line.split()
            proto, local_addr, foreign_addr = parts[0], parts[1], parts[2]
            if proto == 'UDP' and foreign_addr != '*:*':
                # A UDP socket with a specific foreign address (not '*:*') is a targeted
                # send socket -- in this server's case, caproto's periodic beacon socket,
                # which after fix round 1 is connect()ed to 127.0.0.1:5065 (see
                # TestBeaconConfinement below, which checks this exact address rather than
                # assuming it). Windows reports its *local* side as 0.0.0.0:<ephemeral>
                # regardless of which interface it is really usable from; since it only ever
                # talks to 127.0.0.1 (verified below), it is not something the network can
                # reach, so it is not part of the "never bind off localhost" property this test
                # checks. Only sockets open to receive from anywhere ('*:*') are.
                self.assertTrue(foreign_addr.startswith('127.0.0.1:'),
                                 f'plant_sim.py (pids {pids}) sent UDP to a non-localhost peer: '
                                 f'{line}')
                continue
            self.assertTrue(local_addr.startswith('127.0.0.1:'),
                             f'plant_sim.py (pids {pids}) bound to a non-localhost address: '
                             f'{line}')
            checked += 1
        self.assertGreater(checked, 0,
                            f'no listening/bound sockets found to check for pids {pids}:\n'
                            + '\n'.join(pid_lines))

    # ---- I-1: publish lands at whole second + --phase, not on the whole second itself --------
    def test_6_publish_lands_near_phase_offset_not_on_the_second(self):
        """This server runs with the default --phase (0.75, see plant_sim.py build_arg_parser).
        Before fix round 2, the publish tick landed on the whole wall-clock second (phase 0);
        after, it lands ~0.75 s into the second. Collect each Flow_RBV update's own CA
        timestamp (not local receipt time, to keep OS/network scheduling jitter out of it) and
        check its fractional-second part clusters near 0.75, not near 0.0.
        """
        fracs = []

        def _cb(timestamp=None, **kwargs):
            if timestamp is not None:
                fracs.append(timestamp % 1.0)

        pv = epics.PV('SIM:Alicat1:Flow_RBV')   # default form='time' -- callbacks carry timestamps
        self.assertTrue(pv.wait_for_connection(timeout=5))
        pv.add_callback(_cb)
        time.sleep(0.3)
        fracs.clear()
        time.sleep(5.0)
        pv.clear_callbacks()

        self.assertGreaterEqual(len(fracs), 3, f'too few timestamped updates: {fracs}')
        avg_frac = sum(fracs) / len(fracs)
        self.assertLess(abs(avg_frac - 0.75), 0.3,
                         f'Flow_RBV update timestamps landed at fractional second {fracs} '
                         f'(mean {avg_frac:.3f}), expected clustered near phase=0.75, not near '
                         'the whole second (0.0)')


class TestBeaconConfinement(unittest.TestCase):
    """Fix round 1, finding 1: EPICS_CAS_INTF_ADDR_LIST alone does not confine caproto's CA
    beacon (a periodic UDP "I'm alive" broadcast, separate from the TCP/UDP listener) -- it also
    reads EPICS_CAS_BEACON_ADDR_LIST / EPICS_CAS_AUTO_BEACON_ADDR_LIST. This starts its own
    plant_sim.py with *both* of those stripped from the child's environment (as if the bench env
    script had never been run, or someone forgot those two exports) and checks that plant_sim.py
    still confines the beacon to 127.0.0.1 by setting them itself -- i.e. it must never beacon to
    255.255.255.255 or any other non-loopback address."""

    PORT = 5070

    def test_beacon_confined_when_env_omits_beacon_vars(self):
        env = os.environ.copy()
        for key in ('EPICS_CAS_BEACON_ADDR_LIST', 'EPICS_CAS_AUTO_BEACON_ADDR_LIST'):
            env.pop(key, None)

        stdout_f, stderr_f = _open_log_files()
        proc = subprocess.Popen(
            [sys.executable, PLANT_SIM, '--no-noise', '--port', str(self.PORT)],
            cwd=HERE, env=env, stdout=stdout_f, stderr=stderr_f, text=True,
        )
        self.addCleanup(_terminate_and_close, proc, stdout_f, stderr_f)

        # Not a CA-client connection check here: this test's own libca context was already
        # initialized (at the first `import epics`/PV lookup in this process, using
        # BENCH_ENV's EPICS_CA_ADDR_LIST="127.0.0.1:5064 127.0.0.1:5066") and libca reads that
        # env var only at context creation, so a client PV search can never reach a server on
        # PORT=5070 from within this same test process. Instead, wait for the server's own
        # readiness line in its log -- everything this test checks (netstat, by PID) needs the
        # process to exist and have opened its sockets, not a working CA round-trip.
        deadline = time.monotonic() + 10
        ready = False
        while time.monotonic() < deadline:
            if 'serving' in _read_log(stderr_f):
                ready = True
                break
            if proc.poll() is not None:
                break
            time.sleep(0.2)
        if not ready:
            self.fail('server with beacon env vars stripped did not start: '
                      f'stdout={_read_log(stdout_f)!r}\nstderr={_read_log(stderr_f)!r}')

        pids = _descendant_pids(proc.pid)
        result = subprocess.run(['netstat', '-ano'], capture_output=True, text=True)
        pid_strs = {str(p) for p in pids}
        udp_lines = [line for line in result.stdout.splitlines()
                     if line.split() and line.split()[0] == 'UDP'
                     and line.split()[-1] in pid_strs]
        self.assertTrue(udp_lines, f'no UDP netstat lines for pids {pids}:\n{result.stdout}')

        saw_targeted_send = False
        for line in udp_lines:
            parts = line.split()
            local_addr, foreign_addr = parts[1], parts[2]
            if foreign_addr == '*:*':
                self.assertTrue(local_addr.startswith('127.0.0.1:'),
                                 f'UDP socket open to receive from anywhere: {line}')
            else:
                self.assertTrue(foreign_addr.startswith('127.0.0.1:'),
                                 f'beacon (or other targeted UDP send) went to a non-localhost '
                                 f'peer: {line}')
                saw_targeted_send = True
        self.assertTrue(saw_targeted_send,
                         f'expected at least one targeted UDP send (the beacon) among:\n'
                         + '\n'.join(udp_lines))


class TestRefusals(unittest.TestCase):
    """The exit-2 refusal paths: cheap to test because every one of these fails before any
    socket opens, so each subprocess should exit almost immediately."""

    def _run(self, extra_env=None, extra_args=()):
        env = os.environ.copy()
        if extra_env:
            env.update(extra_env)
        cmd = [sys.executable, PLANT_SIM, '--port', '5099', *extra_args]
        return subprocess.run(cmd, cwd=HERE, env=env, capture_output=True, text=True, timeout=10)

    def test_refuses_15id_prefix(self):
        result = self._run(extra_args=['--plant', '15IDC:Alicat1:,SIM:O2,SIM:World:'])
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('15ID', result.stderr)

    def test_refuses_non_loopback_intf_addr_list(self):
        result = self._run(extra_env={'EPICS_CAS_INTF_ADDR_LIST': '0.0.0.0'})
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('EPICS_CAS_INTF_ADDR_LIST', result.stderr)

    def test_refuses_non_loopback_beacon_addr_list(self):
        result = self._run(extra_env={'EPICS_CAS_BEACON_ADDR_LIST': '1.2.3.4'})
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('EPICS_CAS_BEACON_ADDR_LIST', result.stderr)

    def test_refuses_auto_beacon_addr_list_yes(self):
        result = self._run(extra_env={'EPICS_CAS_AUTO_BEACON_ADDR_LIST': 'YES'})
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('EPICS_CAS_AUTO_BEACON_ADDR_LIST', result.stderr)

    def test_refuses_duplicate_pv_names_across_plants(self):
        result = self._run(extra_args=[
            '--plant', 'SIM:Alicat1:,SIM:O2,SIM:World:',
            '--plant', 'SIM:Alicat9:,SIM:O2,SIM:World9:',
        ])
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('SIM:O2', result.stderr)

    def test_refuses_preset_not_positive(self):
        for bad in ('0', '-1', 'nan'):
            result = self._run(extra_args=['--preset', bad])
            self.assertEqual(result.returncode, 2, (bad, result.stderr))
            self.assertIn('--preset', result.stderr)


# ---------------------------------------------------------------------------------------------
# --preset (a plant already regulating from its first served value) and <W>AlicatPuts (every put
# to the Alicat's writable PVs counted), for the bench tests that start the IOC before the plant
# (test_fixes.py). Own PV names: can run while a bench plant serves the SIM:Alicat1: set.

class TestPresetAndPutCounter(unittest.TestCase):

    PORT = 5074
    A, O2, W = 'SIM:PsA:', 'SIM:PsO2', 'SIM:PsW:'
    FLOW = 0.29

    @classmethod
    def setUpClass(cls):
        env = os.environ.copy()
        cls.stdout_f, cls.stderr_f = _open_log_files()
        cls.proc = subprocess.Popen(
            [sys.executable, PLANT_SIM, '--no-noise', '--port', str(cls.PORT), '--preset', str(cls.FLOW),
             '--plant', f'{cls.A},{cls.O2},{cls.W}'],
            cwd=HERE, env=env, stdout=cls.stdout_f, stderr=cls.stderr_f, text=True,
        )
        cls.addClassCleanup(_terminate_and_close, cls.proc, cls.stdout_f, cls.stderr_f)
        # the first values a client gets, taken as early as possible after the server is up
        t_end = time.time() + 15
        cls.first = None
        while time.time() < t_end and cls.first is None:
            sp = epics.caget(cls.A + 'Setpoint_RBV', timeout=0.5, use_monitor=False)
            if sp is not None:
                cls.first = {'sp': sp,
                             'val': epics.caget(cls.A + 'Setpoint', use_monitor=False),
                             'flow': epics.caget(cls.A + 'Flow_RBV', use_monitor=False),
                             'o2': epics.caget(cls.O2, use_monitor=False),
                             'puts': epics.caget(cls.W + 'AlicatPuts', use_monitor=False)}
        if cls.first is None:
            raise RuntimeError('preset server did not come up: '
                               f'stderr={_read_log(cls.stderr_f)!r}')

    def test_1_first_values_are_the_preset(self):
        from plantsim.plant import Plant
        f = self.first
        self.assertAlmostEqual(f['sp'], self.FLOW, places=6)
        self.assertAlmostEqual(f['val'], self.FLOW, places=6)
        self.assertAlmostEqual(f['flow'], self.FLOW, places=2)
        o2 = Plant(noise=False).steady_at(self.FLOW)
        self.assertLess(abs(f['o2'] - o2) / o2, 0.05, f'O2 {f["o2"]} vs steady_at {o2}')
        self.assertEqual(f['puts'], 0)
        self.assertIn('preset at 0.29 SLPM', _read_log(self.stderr_f))

    def test_2_alicat_puts_counted_world_ramp_not(self):
        n0 = epics.caget(self.W + 'AlicatPuts', use_monitor=False)
        for pv, v in (('Setpoint', 0.31), ('RampRate', 3), ('Run', 1)):
            self.assertEqual(epics.caput(self.A + pv, v, wait=True, timeout=5), 1)
        self.assertEqual(epics.caget(self.W + 'AlicatPuts', use_monitor=False), n0 + 3)
        self.assertEqual(epics.caput(self.W + 'SetRamp', 3, wait=True, timeout=5), 1)
        self.assertEqual(epics.caput(self.W + 'Preset', 0.29, wait=True, timeout=5), 1)
        self.assertEqual(epics.caget(self.W + 'AlicatPuts', use_monitor=False), n0 + 3,
                         'a World: put counted as a put to the Alicat')
        log = _read_log(self.stderr_f)
        self.assertIn('put Setpoint = 0.31', log)
        self.assertIn('put Run = 1', log)


# ---------------------------------------------------------------------------------------------
# Pure-function unit tests (final-fix-brief I-1/M-2 timing helpers, I-2/M-3 validation helpers):
# no subprocess, no CA -- these check plant_sim.py's own module-level functions directly.

class TestTimingHelpers(unittest.TestCase):
    """I-1 (publish phase), M-2 (sample after step 4/8/12, not 1/5/9), and the catch-up-tick
    minor item -- all pulled into plain functions in plant_sim.py so they can be checked without
    an asyncio loop or a live server."""

    def test_is_publish_tick_samples_after_step_4_8_12_not_1_5_9(self):
        # k is 0-indexed; step count taken so far (after this tick's plant.step()) is k + 1.
        # Reference cadence (Station.step, html 1534-1541): steps % 4 == 0 -> steps 4, 8, 12 ...
        publish_ticks = [k for k in range(16) if plant_sim._is_publish_tick(k)]
        self.assertEqual(publish_ticks, [3, 7, 11, 15],
                          'publish should fire on k=3,7,11,15 (step count 4,8,12,16), matching '
                          'the reference -- fix round 1 fired on k=0,4,8,12 (step count '
                          '1,5,9,13) instead')

    def test_compute_start_mono_aligns_publish_tick_to_whole_second_plus_phase(self):
        now_wall = 1_000_000.2   # arbitrary; only the fractional part matters here
        now_mono = 500.0         # arbitrary monotonic origin, independent of now_wall's epoch
        phase = 0.75
        start_mono = plant_sim._compute_start_mono(now_wall, now_mono, phase)

        # The first publish tick is k=3 (see _is_publish_tick). Its scheduled monotonic time,
        # translated back into wall-clock terms via the fixed (now_wall, now_mono) mapping,
        # must land at the next whole second plus `phase` -- not at the whole second itself.
        publish_tick_mono = start_mono + 3 * plant_sim.DT
        publish_tick_wall = now_wall + (publish_tick_mono - now_mono)
        expected_wall = math.ceil(now_wall) + phase
        self.assertAlmostEqual(publish_tick_wall, expected_wall, places=9)

    def test_catchup_lag_warning_thresholds_at_1_second(self):
        self.assertIsNone(plant_sim._catchup_lag_warning(0.0))
        self.assertIsNone(plant_sim._catchup_lag_warning(1.0))
        msg = plant_sim._catchup_lag_warning(1.5)
        self.assertIsNotNone(msg)
        self.assertIn('1.500', msg)


class TestValidationHelpers(unittest.TestCase):
    """I-2 (_finite) and M-3 (_validate_enum)."""

    def test_finite_accepts_ordinary_floats(self):
        self.assertEqual(plant_sim._finite(0.29), 0.29)
        self.assertEqual(plant_sim._finite(0), 0.0)
        self.assertEqual(plant_sim._finite(-3), -3.0)

    def test_finite_rejects_nan_and_inf(self):
        for bad in (float('nan'), float('inf'), float('-inf')):
            with self.assertRaises(ValueError):
                plant_sim._finite(bad)

    def test_validate_enum_accepts_a_label_already_in_states(self):
        self.assertEqual(plant_sim._validate_enum('B', plant_sim.LIDTYPE_STATES, 'LidType'), 'B')

    def test_validate_enum_rejects_anything_not_in_states(self):
        # An out-of-range index that caproto's own ChannelEnum.verify_value failed to resolve
        # (see _validate_enum's docstring) reaches the putter as a raw int, not a label.
        with self.assertRaises(ValueError):
            plant_sim._validate_enum(5, plant_sim.LIDTYPE_STATES, 'LidType')
        with self.assertRaises(ValueError):
            plant_sim._validate_enum('nonsense', plant_sim.LIDTYPE_STATES, 'LidType')


class _FakePV:
    """Records writes like a caproto ChannelData written with verify_value=False."""

    def __init__(self):
        self.writes = []

    async def write(self, value, **kw):
        self.writes.append((time.monotonic(), value, kw.get('severity')))


class TestO2Feed(unittest.TestCase):
    """--o2-drop / --o2-jitter (2026-10-01, the user: handle missed O2 updates on a ~1 Hz
    schedule): O2Feed decides per update; _publish posts nothing for a skipped update."""

    def test_no_drop_no_jitter_posts_every_update_at_once(self):
        f = plant_sim.O2Feed()
        self.assertFalse(f.active)
        self.assertEqual([f.decide() for _ in range(100)], [0.0] * 100)

    def test_drop_fraction_and_seed(self):
        f = plant_sim.O2Feed(drop=0.15, seed=7)
        d = [f.decide() for _ in range(20000)]
        frac = sum(x is None for x in d) / len(d)
        self.assertAlmostEqual(frac, 0.15, delta=0.01)
        self.assertEqual(f.skipped, sum(x is None for x in d))
        self.assertEqual(f.posted + f.skipped, 20000)
        g = plant_sim.O2Feed(drop=0.15, seed=7)
        self.assertEqual([g.decide() for _ in range(20000)], d, 'same seed, same drops')
        h = plant_sim.O2Feed(drop=0.15, seed=8)
        self.assertNotEqual([h.decide() for _ in range(200)], d[:200], 'other seed, other drops')

    def test_drops_do_not_depend_on_jitter(self):
        a = plant_sim.O2Feed(drop=0.2, seed=3)
        b = plant_sim.O2Feed(drop=0.2, jitter=0.5, seed=3)
        da = [a.decide() is None for _ in range(5000)]
        db = [b.decide() for _ in range(5000)]
        self.assertEqual(da, [x is None for x in db])
        delays = [x for x in db if x is not None]
        self.assertTrue(all(0 <= x < 0.5 for x in delays))
        self.assertGreater(max(delays), 0.4)

    def test_rejects_bad_values(self):
        for kw in ({'drop': -0.1}, {'drop': 1.0}, {'jitter': -1}, {'jitter': 1.0}):
            with self.assertRaises(ValueError, msg=kw):
                plant_sim.O2Feed(**kw)

    def test_refuses_to_start_on_bad_values(self):
        for args in (['--o2-drop', '1.5'], ['--o2-jitter', '2']):
            r = subprocess.run([sys.executable, PLANT_SIM, '--port', '5099', *args], cwd=HERE,
                               env=os.environ.copy(), capture_output=True, text=True, timeout=10)
            self.assertEqual(r.returncode, 2, (args, r.stderr))
            self.assertIn('--o2-', r.stderr)

    def test_seed_derivation(self):
        p = plant_sim.build_arg_parser()
        self.assertEqual(plant_sim.o2_feed_seed(p.parse_args([]), 0), 0)
        self.assertEqual(plant_sim.o2_feed_seed(p.parse_args(['--seed', '3']), 1), 1004)
        self.assertEqual(plant_sim.o2_feed_seed(p.parse_args(['--seed', '3', '--o2-seed', '9']), 1), 10)

    def _publish_many(self, feed, n, spacing=0.0):
        from plantsim.plant import Plant
        plant = Plant(seed=1)
        pv = {k: _FakePV() for k in ('setpoint_rbv', 'flow_rbv', 'total_rbv', 'running_rbv', 'status',
                                      'ramprate_rbv', 'gas_rbv', 'flowunits_rbv', 'o2', 'o2skipped',
                                      'cylpressure', 'bulk', 'time')}
        ctx = {'plant': plant, 'pv': pv, 'o2feed': feed}
        sampled = []

        async def run():
            for _ in range(n):
                for _ in range(4):
                    plant.step()
                await plant_sim._publish(ctx)
                sampled.append(plant.an['value'])
                await asyncio.sleep(spacing)
            await asyncio.sleep(1.0)            # let the late writes land
        asyncio.run(run())
        return pv, sampled

    def test_publish_skips_dropped_updates(self):
        feed = plant_sim.O2Feed(drop=0.3, seed=11)
        ref = plant_sim.O2Feed(drop=0.3, seed=11)
        pv, sampled = self._publish_many(feed, 200)
        kept = [v for v in sampled if ref.decide() is not None]
        self.assertEqual([w[1] for w in pv['o2'].writes], kept, 'the posted values = the kept samples')
        self.assertEqual(len(kept) + feed.skipped, 200)
        self.assertGreater(feed.skipped, 30)
        self.assertEqual(len(pv['flow_rbv'].writes), 200, 'the Alicat readbacks are not dropped')
        self.assertEqual(pv['o2skipped'].writes[-1][1], feed.skipped)

    def test_publish_jitter_posts_late_and_in_order(self):
        feed = plant_sim.O2Feed(jitter=0.3, seed=5)
        t0 = time.monotonic()
        pv, sampled = self._publish_many(feed, 8, spacing=0.35)
        self.assertEqual([w[1] for w in pv['o2'].writes], sampled, 'every update posted, in order')
        self.assertGreater(pv['o2'].writes[-1][0] - t0, 7 * 0.35, 'posted late')


# ---------------------------------------------------------------------------------------------
# I-2 (non-finite puts) at the CA level: the actual behaviour a real client sees on a live
# server -- a NaN put must not corrupt plant state or take the server down.

class TestPutterValidationOverCA(unittest.TestCase):

    PORT = 5073

    @classmethod
    def setUpClass(cls):
        env = os.environ.copy()
        cls.stdout_f, cls.stderr_f = _open_log_files()
        cls.proc = subprocess.Popen(
            [sys.executable, PLANT_SIM, '--no-noise', '--port', str(cls.PORT)],
            cwd=HERE, env=env, stdout=cls.stdout_f, stderr=cls.stderr_f, text=True,
        )
        cls.addClassCleanup(_terminate_and_close, cls.proc, cls.stdout_f, cls.stderr_f)
        pv = epics.PV('SIM:O2')
        if not pv.wait_for_connection(timeout=10):
            raise RuntimeError('SIM:O2 did not connect on the validation-test server: '
                                f'stdout={_read_log(cls.stdout_f)!r}\n'
                                f'stderr={_read_log(cls.stderr_f)!r}')

    def test_nan_puts_rejected_and_server_keeps_publishing(self):
        nan = float('nan')
        # (put PV, a readback that should be unaffected by a rejected put to it)
        checks = [
            ('SIM:Alicat1:RampRate', 'SIM:Alicat1:RampRate_RBV'),
            ('SIM:World:SetRamp', 'SIM:Alicat1:RampRate_RBV'),
            ('SIM:World:Preset', 'SIM:Alicat1:Flow_RBV'),
            ('SIM:World:CylPressure', 'SIM:World:CylPressure'),
            ('SIM:Alicat1:Setpoint', 'SIM:Alicat1:Setpoint'),
        ]
        for put_pv, readback_pv in checks:
            before = epics.caget(readback_pv, timeout=5)
            self.assertIsNotNone(before, f'baseline caget({readback_pv}) failed')
            self.assertFalse(math.isnan(before), f'baseline {readback_pv}={before} already NaN')

            try:
                epics.caput(put_pv, nan, wait=True, timeout=5)
            except Exception:
                pass  # a rejected put may surface as a CA exception or a failure status --
                      # what matters below is whether the plant and the server survived it.

            self.assertIsNone(self.proc.poll(),
                               f'plant_sim.py died after a NaN put to {put_pv} '
                               f'(exit {self.proc.returncode}); '
                               f'stderr={_read_log(self.stderr_f)!r}')
            time.sleep(1.5)
            after = epics.caget(readback_pv, timeout=5)
            self.assertIsNotNone(after, f'caget({readback_pv}) failed after a NaN put to {put_pv}')
            self.assertFalse(math.isnan(after),
                              f'{readback_pv}={after} went NaN after a NaN put to {put_pv} '
                              '-- the put should have been rejected before touching plant state')

        self.assertIsNone(self.proc.poll(), 'plant_sim.py died during the NaN-put test')
        flow_pv = epics.PV('SIM:Alicat1:Flow_RBV')
        self.assertTrue(flow_pv.wait_for_connection(timeout=5))
        counts = {'n': 0}
        flow_pv.add_callback(lambda **kw: counts.__setitem__('n', counts['n'] + 1))
        time.sleep(3.0)
        flow_pv.clear_callbacks()
        self.assertGreater(counts['n'], 0,
                            'Flow_RBV stopped updating after the NaN-put test -- server is no '
                            'longer publishing even though the process is still alive')
        # P3-R3: a rejected put alarms only the PV that was put to. Readbacks must not inherit
        # caproto's WRITE/MAJOR alarm through a shared (default) alarm group.
        for readback in ('SIM:Alicat1:Flow_RBV', 'SIM:Alicat1:Setpoint_RBV',
                         'SIM:Alicat1:Running_RBV', 'SIM:World:Bulk', 'SIM:World:Time'):
            pv = epics.PV(readback)
            self.assertTrue(pv.wait_for_connection(timeout=5), readback)
            pv.get(with_ctrlvars=True, use_monitor=False)
            self.assertEqual(pv.severity, 0,
                             f'{readback} severity {pv.severity} after rejected puts '
                             '(alarm leaked from a put to another PV)')


# ---------------------------------------------------------------------------------------------
# M-3: invalid enum puts must be rejected by the *putter itself*, before it ever mutates plant
# state. This calls plant_sim's put() factories directly against a real Plant, the same way
# caproto's ChannelData.write() calls them -- not over CA -- because a CA-level check on the
# PV's own *displayed* value is not sensitive to this bug: empirically, caproto/pyepics already
# stop an out-of-range LidType put from ever being *shown* back on the PV (a different, earlier
# validation layer), but that does not stop the old putter body (`plant.lid_type = str(value)`)
# from running first and setting `plant.lid_type` to the nonsense string '5' regardless -- a
# silent corruption of the model driving the physics that a client would never see on the PV it
# just put to.

class TestEnumPutterRejection(unittest.TestCase):

    def test_lidtype_rejects_out_of_range_index(self):
        plant = plant_sim.Plant(noise=False)
        put = plant_sim.mk_put_lidtype(plant)
        with self.assertRaises(ValueError):
            asyncio.run(put(None, None, 5))
        self.assertEqual(plant.lid_type, 'A', 'LidType must stay unchanged on a rejected put')

    def test_hold_rejects_unknown_label(self):
        plant = plant_sim.Plant(noise=False)
        put = plant_sim.mk_put_hold(plant)
        running_before = plant.a['running']
        with self.assertRaises(ValueError):
            asyncio.run(put(None, None, 'bogus'))
        self.assertEqual(plant.a['running'], running_before)

    def test_gas_rejects_out_of_range_index(self):
        plant = plant_sim.Plant(noise=False)
        put = plant_sim.mk_put_gas(plant)
        gas_before = plant.a['gas']
        with self.assertRaises(ValueError):
            asyncio.run(put(None, None, 99))
        self.assertEqual(plant.a['gas'], gas_before)

    def test_analyzer_mode_rejects_unknown_label(self):
        plant = plant_sim.Plant(noise=False)
        put = plant_sim.mk_put_mode(plant)
        mode_before = plant.an['mode']
        with self.assertRaises(ValueError):
            asyncio.run(put(None, None, 'bogus'))
        self.assertEqual(plant.an['mode'], mode_before)

    def test_noise_rejects_out_of_range_index(self):
        plant = plant_sim.Plant(noise=False)
        put = plant_sim.mk_put_noise(plant)
        noise_before = dict(plant.pp)
        with self.assertRaises(ValueError):
            asyncio.run(put(None, None, 7))
        self.assertEqual(plant.pp['noise'], noise_before['noise'])
        self.assertEqual(plant.pp['wanderRel'], noise_before['wanderRel'])


# ---------------------------------------------------------------------------------------------
# I-3: a stale second instance must be detected even though Windows' SO_REUSEADDR lets it bind.

class TestStaleInstanceDetection(unittest.TestCase):

    PORT = 5075

    def test_second_instance_on_same_port_exits_2(self):
        env = os.environ.copy()
        first_out, first_err = _open_log_files()
        first = subprocess.Popen(
            [sys.executable, PLANT_SIM, '--no-noise', '--port', str(self.PORT)],
            cwd=HERE, env=env, stdout=first_out, stderr=first_err, text=True,
        )
        self.addCleanup(_terminate_and_close, first, first_out, first_err)

        deadline = time.monotonic() + 10
        ready = False
        while time.monotonic() < deadline:
            if 'serving' in _read_log(first_err):
                ready = True
                break
            if first.poll() is not None:
                break
            time.sleep(0.2)
        if not ready:
            self.fail('first instance did not start: '
                      f'stdout={_read_log(first_out)!r}\nstderr={_read_log(first_err)!r}')

        second_out, second_err = _open_log_files()
        second = subprocess.Popen(
            [sys.executable, PLANT_SIM, '--no-noise', '--port', str(self.PORT)],
            cwd=HERE, env=env, stdout=second_out, stderr=second_err, text=True,
        )
        try:
            ret = second.wait(timeout=10)
            second_stderr = _read_log(second_err)   # read before cleanup deletes the log file
        finally:
            _terminate_and_close(second, second_out, second_err)

        self.assertEqual(ret, 2, second_stderr)
        self.assertIn('already listens', second_stderr)


# ---------------------------------------------------------------------------------------------
# M-1: --second serves an independent second plant, and the put==1 action rule (a put of 0, or
# a resend of the settled-back value, must never re-fire a World: action).

class TestSecondPlantAndLiftLidActionRule(unittest.TestCase):

    PORT = 5076

    @classmethod
    def setUpClass(cls):
        env = os.environ.copy()
        cls.stdout_f, cls.stderr_f = _open_log_files()
        cls.proc = subprocess.Popen(
            [sys.executable, PLANT_SIM, '--no-noise', '--second', '--port', str(cls.PORT)],
            cwd=HERE, env=env, stdout=cls.stdout_f, stderr=cls.stderr_f, text=True,
        )
        cls.addClassCleanup(_terminate_and_close, cls.proc, cls.stdout_f, cls.stderr_f)
        pv = epics.PV('SIM:O2b')
        if not pv.wait_for_connection(timeout=10):
            raise RuntimeError('SIM:O2b did not connect (--second): '
                                f'stdout={_read_log(cls.stdout_f)!r}\n'
                                f'stderr={_read_log(cls.stderr_f)!r}')

    def _updates_in(self, pvname, seconds):
        counts = {'n': 0}
        pv = epics.PV(pvname)
        self.assertTrue(pv.wait_for_connection(timeout=5), f'{pvname} did not connect')
        pv.add_callback(lambda **kw: counts.__setitem__('n', counts['n'] + 1))
        time.sleep(0.3)
        counts['n'] = 0
        time.sleep(seconds)
        pv.clear_callbacks()
        return counts['n']

    def test_1_second_plant_pvs_update_independently(self):
        for pvname in ('SIM:Alicat2:Flow_RBV', 'SIM:O2b', 'SIM:World2:Time'):
            n = self._updates_in(pvname, 3.0)
            self.assertGreater(n, 0, f'{pvname} did not update in 3 s under --second')

    def test_2_lifting_second_plant_lid_does_not_touch_first_plant(self):
        ret = epics.caput('SIM:World:Preset', 0.4, wait=True, timeout=5)
        self.assertEqual(ret, 1)
        time.sleep(1.5)   # Preset takes effect on plant state immediately, but Bulk/O2 only
                           # reflect it at the next 1 Hz publish tick
        o2_before = epics.caget('SIM:O2')
        bulk_before = epics.caget('SIM:World:Bulk')
        self.assertLess(bulk_before, 15.0, f'Preset did not purge plant 0: Bulk={bulk_before}')

        ret = epics.caput('SIM:World2:LiftLid', 1, wait=True, timeout=5)
        self.assertEqual(ret, 1)
        time.sleep(2.0)

        self.assertAlmostEqual(epics.caget('SIM:World:Bulk'), bulk_before, delta=0.3,
                                msg='lifting plant 1 (World2:) lid moved plant 0 Bulk')
        self.assertAlmostEqual(epics.caget('SIM:O2'), o2_before, delta=0.3,
                                msg='lifting plant 1 (World2:) lid moved plant 0 O2')

        epics.caput('SIM:World2:CloseLid', 1, wait=True, timeout=5)

    def test_3_liftlid_put_1_lifts_put_0_is_a_noop(self):
        # Bulk starts at ambient (~19-20%) with no flow; purge plant 1 down first via Preset so
        # lifting the lid afterward has something visible to raise.
        ret = epics.caput('SIM:World2:Preset', 0.5, wait=True, timeout=5)
        self.assertEqual(ret, 1)
        time.sleep(1.5)   # Bulk only reflects the new state at the next 1 Hz publish tick
        bulk_purged = epics.caget('SIM:World2:Bulk')
        self.assertLess(bulk_purged, 15.0, f'Preset did not purge plant 1: Bulk={bulk_purged}')

        ret = epics.caput('SIM:World2:LiftLid', 0, wait=True, timeout=5)
        self.assertEqual(ret, 1)
        time.sleep(1.0)
        bulk_after_noop = epics.caget('SIM:World2:Bulk')
        self.assertAlmostEqual(bulk_after_noop, bulk_purged, delta=0.3,
                                msg='LiftLid put of 0 must be a no-op')

        ret = epics.caput('SIM:World2:LiftLid', 1, wait=True, timeout=5)
        self.assertEqual(ret, 1)
        time.sleep(6.0)   # openTau ~5.75 s -- give the lid time to visibly move Bulk
        bulk_after_lift = epics.caget('SIM:World2:Bulk')
        self.assertGreater(bulk_after_lift, bulk_after_noop + 0.5,
                            'LiftLid put of 1 should raise Bulk toward ambient '
                            f'({bulk_after_noop} -> {bulk_after_lift})')


if __name__ == '__main__':
    unittest.main()
