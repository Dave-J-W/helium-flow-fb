"""bench.py: start/stop the plant simulator and the bench IOC, and talk to them (Plan 4 task 1).

    with Bench(noise=False, seed=1) as b:
        b.cmd('Purge'); b.wait_state('PURGE', 10); print(b.log_lines())

Safety (Plan 4 rulings P4-R1..R3):
  * The PC IOC (st.cmd.pc, LSSPC:, CA port 5064, connected to the real beamline) is never touched:
    this module only ever kills processes it started itself, by PID, and the CA environment it
    sets (below, before `import epics`) searches 127.0.0.1:5076 (bench IOC) and :5066 (plant)
    only. The bench IOC is started through ioc/tools/run_ioc.sh, which refuses to start unless CA
    is confined and its server port is not 5064.
  * Exactly one plant_sim per port: start_plant refuses if any plant_sim.py process exists or
    anything answers on the plant port.
"""
import glob
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

IOC_PORT = 5076
PLANT_PORT = 5066
BENCH_ENV = {
    'EPICS_HOST_ARCH': 'windows-x64-mingw',
    'EPICS_CA_ADDR_LIST': f'127.0.0.1:{IOC_PORT} 127.0.0.1:{PLANT_PORT}',
    'EPICS_CA_AUTO_ADDR_LIST': 'NO',
    'EPICS_CAS_INTF_ADDR_LIST': '127.0.0.1',
    'EPICS_CAS_BEACON_ADDR_LIST': '127.0.0.1',
    'EPICS_CAS_AUTO_BEACON_ADDR_LIST': 'NO',
    'EPICS_CAS_SERVER_PORT': str(IOC_PORT),
    'EPICS_PVA_ADDR_LIST': '127.0.0.1',
    'EPICS_PVA_AUTO_ADDR_LIST': 'NO',
    'EPICS_PVAS_INTF_ADDR_LIST': '127.0.0.1',
    'EPICS_PVAS_BEACON_ADDR_LIST': '127.0.0.1',
    'EPICS_PVAS_AUTO_BEACON_ADDR_LIST': 'NO',
    'PYTHONIOENCODING': 'utf-8',
}
os.environ.update(BENCH_ENV)          # pyepics reads the CA environment at import
import warnings  # noqa: E402

import epics  # noqa: E402
import psutil  # noqa: E402

warnings.filterwarnings('ignore', message=r'ca\.get\(')   # expected while the IOC is down

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
IOC_BOOT = REPO / 'ioc' / 'lssSampleGas' / 'iocBoot' / 'iocLSS_sample_gas'
RESULTS = HERE / 'results'
MSYS_BASH = r'C:\msys64\usr\bin\bash.exe'
STATIONS = {'SIM': 'SIM:SampleGas:'}
WORLD = 'SIM:World:'


class BenchError(RuntimeError):
    pass


def _port_answers(port):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=0.5):
            return True
    except OSError:
        return False


def _kill_tree(pid):
    """Kill pid and all its descendants (children first). Only ever called with our own PIDs."""
    try:
        root = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    procs = root.children(recursive=True) + [root]
    for p in procs:
        try:
            p.kill()
        except psutil.NoSuchProcess:
            pass
    psutil.wait_procs(procs, timeout=10)


def _cmdline(p):
    try:
        return ' '.join(p.cmdline())
    except (psutil.Error, OSError):
        return ''


def plant_sim_processes():
    return [p for p in psutil.process_iter(['name']) if 'plant_sim.py' in _cmdline(p)]


def bench_ioc_processes():
    """lssSampleGas.exe processes running the bench st.cmd (never the PC IOC's st.cmd.pc)."""
    out = []
    for p in psutil.process_iter(['name']):
        if (p.info['name'] or '').lower() == 'lsssamplegas.exe' and _cmdline(p).endswith(' st.cmd'):
            out.append(p)
    return out


class Bench:
    """plant_first=False starts only the IOC (no plant: the IOC waits for the Alicat, spec §8.15
    step 1); start the plant later with start_plant(preset=...). ioc_env: extra environment for
    the IOC (e.g. {'FORCE_SHADOW': '1'}, which st.cmd passes to the program)."""

    def __init__(self, noise=True, seed=None, second_plant=False, write_enable=1, tag='bench',
                 plant_first=True, ioc_env=None):
        self.noise, self.seed, self.second_plant = noise, seed, second_plant
        self.write_enable = write_enable
        self.tag = tag
        self.plant_first = plant_first
        self.ioc_env = dict(ioc_env or {})
        self.plant_proc = self.ioc_proc = None
        self._pvs = {}
        self._watches = []
        self._log_offsets = {}
        self._files = []
        RESULTS.mkdir(exist_ok=True)
        self.stamp = time.strftime('%Y%m%d-%H%M%S')

    # ------------------------------------------------------------------ context
    def __enter__(self):
        for f in glob.glob(str(IOC_BOOT / 'autosave' / '*.sav*')):
            os.remove(f)              # each Bench starts from database defaults
        self.mark_log()
        try:
            if self.plant_first:
                self.start_plant()
            self.start_ioc()
            if self.write_enable:
                self.put(self.p('SIM') + 'Par:writeEnable', 1)
                time.sleep(1.5)
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        for pv in self._watches:
            pv.clear_callbacks()
        self.stop_ioc(kill=True)
        self.stop_plant()
        for f in self._files:
            f.close()
        self._files = []

    def p(self, station='SIM'):
        return STATIONS[station]

    def _outfile(self, what):
        f = open(RESULTS / f'{self.tag}-{self.stamp}-{what}.txt', 'ab')
        self._files.append(f)
        return f

    # ------------------------------------------------------------------ plant
    def start_plant(self, preset=None):
        """preset: start the plant already at steady state at this flow (plant_sim --preset)."""
        if plant_sim_processes():
            raise BenchError('a plant_sim.py process already exists (P4-R3): '
                             + ', '.join(str(p.pid) for p in plant_sim_processes()))
        if _port_answers(PLANT_PORT):
            raise BenchError(f'something already listens on 127.0.0.1:{PLANT_PORT}')
        args = [sys.executable, str(HERE / 'plant_sim.py'), '--port', str(PLANT_PORT)]
        if self.seed is not None:
            args += ['--seed', str(self.seed)]
        if not self.noise:
            args.append('--no-noise')
        if self.second_plant:
            args.append('--second')
        if preset is not None:
            args += ['--preset', str(preset)]
        out = self._outfile('plant')
        self.plant_proc = subprocess.Popen(args, cwd=str(HERE), env=dict(os.environ), stdin=subprocess.DEVNULL,
                                           stdout=out, stderr=subprocess.STDOUT)
        t_end = time.time() + 30
        while time.time() < t_end:
            if self.plant_proc.poll() is not None:
                raise BenchError(f'plant_sim exited with {self.plant_proc.returncode}')
            if epics.caget(WORLD + 'Time', timeout=1.0) is not None:
                return
            time.sleep(0.5)
        raise BenchError('plant_sim did not come up within 30 s')

    def stop_plant(self):
        if self.plant_proc is not None:
            _kill_tree(self.plant_proc.pid)
            self.plant_proc.wait(10)
            self.plant_proc = None

    # ------------------------------------------------------------------ IOC
    def start_ioc(self, timeout=90):
        if self.ioc_proc is not None and self.ioc_proc.poll() is None:
            raise BenchError('bench IOC already started by this Bench')
        if bench_ioc_processes():
            raise BenchError('a bench IOC (st.cmd) is already running: '
                             + ', '.join(str(p.pid) for p in bench_ioc_processes()))
        if _port_answers(IOC_PORT):
            raise BenchError(f'something already listens on 127.0.0.1:{IOC_PORT}')
        env = dict(os.environ, MSYSTEM='MINGW64', CHERE_INVOKING='1', **self.ioc_env)
        out = self._outfile('ioc')
        # stdin stays an open pipe: the IOC shell exits at EOF on stdin
        self.ioc_proc = subprocess.Popen([MSYS_BASH, '-l', 'ioc/tools/run_ioc.sh'], cwd=str(REPO), env=env,
                                         stdin=subprocess.PIPE, stdout=out, stderr=subprocess.STDOUT)
        hb = self.p('SIM') + 'Sts:Heartbeat'
        t_end = time.time() + timeout
        first = None
        while time.time() < t_end:
            if self.ioc_proc.poll() is not None:
                raise BenchError(f'bench IOC exited with {self.ioc_proc.returncode} (see results/*-ioc.txt)')
            v = epics.caget(hb, timeout=1.0, use_monitor=False)
            if v is not None:
                if first is None:
                    first = v
                elif v != first:
                    return
            time.sleep(0.5)
        raise BenchError(f'bench IOC heartbeat did not advance within {timeout} s')

    def stop_ioc(self, kill=False):
        if self.ioc_proc is None:
            return
        if not kill and self.ioc_proc.poll() is None:
            try:
                self.ioc_proc.stdin.write(b'exit\n')
                self.ioc_proc.stdin.flush()
                self.ioc_proc.wait(15)
            except (OSError, subprocess.TimeoutExpired):
                pass
        _kill_tree(self.ioc_proc.pid)
        try:
            self.ioc_proc.stdin.close()
            self.ioc_proc.wait(10)
        except (OSError, subprocess.TimeoutExpired):
            pass
        self.ioc_proc = None

    def restart_ioc(self, down_s=0.0):
        self.stop_ioc(kill=True)
        time.sleep(down_s)
        self.start_ioc()

    def ioc_pids(self):
        if self.ioc_proc is None:
            return []
        try:
            root = psutil.Process(self.ioc_proc.pid)
            return [root.pid] + [c.pid for c in root.children(recursive=True)]
        except psutil.NoSuchProcess:
            return []

    # ------------------------------------------------------------------ PVs
    def _pv(self, name):
        pv = self._pvs.get(name)
        if pv is None:
            pv = self._pvs[name] = epics.PV(name, auto_monitor=False)
        return pv

    def get(self, pv, timeout=3.0, **kw):
        p = self._pv(pv)
        p.wait_for_connection(timeout)
        return p.get(timeout=timeout, use_monitor=False, **kw)

    def put(self, pv, value, wait=True, timeout=5.0):
        p = self._pv(pv)
        if not p.wait_for_connection(timeout):
            raise BenchError(f'{pv} not connected')
        r = p.put(value, wait=wait, timeout=timeout)
        if wait and r is None:
            raise BenchError(f'put {pv} = {value!r} timed out')
        return r

    def world(self, suffix, value):
        self.put(WORLD + suffix, value)

    def cmd(self, name, station='SIM'):
        self.put(self.p(station) + 'Cmd:' + name, 1)

    def state(self, station='SIM'):
        return self.get(self.p(station) + 'Sts:State', as_string=True)

    def heartbeat(self, station='SIM'):
        return self.get(self.p(station) + 'Sts:Heartbeat')

    def wait_state(self, want, timeout, station='SIM'):
        t0 = time.time()
        while True:
            if self.state(station) == want:
                return time.time() - t0
            if time.time() - t0 > timeout:
                raise BenchError(f'state {self.state(station)!r}, not {want!r}, after {timeout} s')
            time.sleep(0.5)

    def next_tick(self, timeout=10.0, station='SIM'):
        """Block until Sts:Heartbeat changes; return the wall time it was seen."""
        h0 = self.heartbeat(station)
        t_end = time.time() + timeout
        while time.time() < t_end:
            h = self.heartbeat(station)
            if h is not None and h != h0:
                return time.time()
            time.sleep(0.05)
        raise BenchError('no IOC tick within %s s' % timeout)

    def watch(self, pv):
        """Monitor recorder: a live list of (wall time, value)."""
        rec = []
        # the callback goes in with the PV: on a channel pyepics already has connected (the same
        # name used earlier in this process) the subscription, and its first event, happen inside
        # PV(); a callback added afterwards can miss that event
        p = epics.PV(pv, auto_monitor=True, callback=lambda value=None, **kw: rec.append((time.time(), value)))
        self._watches.append(p)
        return rec

    # ------------------------------------------------------------------ autosave and log
    def wait_autosaved(self, suffixes, station='SIM', timeout=90):
        """Wait until the settings .sav holds the IOC's current values of these PVs (the monitor
        set writes at most every 30 s), so a restart restores them."""
        pre = self.p(station)
        want = {pre + s: str(self.get(pre + s, as_string=False)) for s in suffixes}
        path = IOC_BOOT / 'autosave' / 'sampleGas_settings.sav'
        t_end = time.time() + timeout
        while time.time() < t_end:
            try:
                txt = path.read_text(encoding='utf-8', errors='replace')
            except OSError:
                txt = ''
            saved = dict(ln.split(' ', 1) for ln in txt.splitlines() if ' ' in ln and not ln.startswith('#'))
            if txt.rstrip().endswith('<END>') and all(_same(saved.get(k), v) for k, v in want.items()):
                return
            time.sleep(1.0)
        raise BenchError(f'autosave did not record {want} within {timeout} s')

    def _log_files(self, station):
        return sorted(glob.glob(str(IOC_BOOT / 'logs' / f'sampleGas_{station}_*.log')))

    def mark_log(self, station='SIM'):
        """Remember where the log files end now; log_lines(since=mark) reads from there."""
        mark = {f: os.path.getsize(f) for f in self._log_files(station)}
        if not self._log_offsets:
            self._log_offsets = dict(mark)
        return mark

    def log_lines(self, station='SIM', since=None):
        start = self._log_offsets if since is None else since
        out = []
        for f in self._log_files(station):
            with open(f, 'rb') as fh:
                fh.seek(start.get(f, 0))
                out += fh.read().decode('utf-8', errors='replace').splitlines()
        return out


def _same(saved, want):
    if saved is None:
        return False
    try:
        return abs(float(saved) - float(want)) <= 1e-9 * max(1.0, abs(float(want)))
    except ValueError:
        return saved.strip() == want.strip()


def log_has(lines, pattern):
    rx = re.compile(pattern)
    return any(rx.search(ln) for ln in lines)
