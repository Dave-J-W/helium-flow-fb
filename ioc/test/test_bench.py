"""test_bench.py: the bench harness starts the plant and the IOC, the IOC ticks, and stopping
leaves no process behind (Plan 4 task 1). Needs the built IOC; takes about a minute.

    cd ioc/test && python -m unittest -v test_bench
"""
import time
import unittest

import psutil

import bench
from bench import Bench


class BenchTest(unittest.TestCase):
    def test_start_tick_stop(self):
        with Bench(noise=False, seed=1) as b:
            pids = b.ioc_pids() + [b.plant_proc.pid]
            self.assertTrue(any(psutil.Process(p).name().lower() == 'lsssamplegas.exe' for p in pids))
            h0 = b.heartbeat()
            time.sleep(3)
            self.assertGreaterEqual(b.heartbeat() - h0, 2)
            self.assertEqual(b.state(), 'IDLE')
            self.assertEqual(b.get('SIM:SampleGas:Sts:WriteEnable'), 1)
            self.assertTrue(any('IOC started' in ln for ln in b.log_lines()))
            flow = b.watch('SIM:Alicat1:Flow_RBV')
            b.restart_ioc()
            self.assertEqual(b.state(), 'IDLE')
            time.sleep(2)
            self.assertTrue(flow, 'monitor recorded nothing')
            pids += b.ioc_pids()
        for p in pids:
            self.assertFalse(psutil.pid_exists(p) and psutil.Process(p).is_running()
                             and psutil.Process(p).status() != psutil.STATUS_ZOMBIE, f'pid {p} left running')
        self.assertEqual(bench.bench_ioc_processes(), [])
        self.assertEqual(bench.plant_sim_processes(), [])


if __name__ == '__main__':
    unittest.main()
