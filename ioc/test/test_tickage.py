"""Bench test of the heartbeat-staleness record Sts:TickAge (spec 7.1, 13.4).

Starts the simulator and the bench IOC, checks that TickAge stays near 0 while the controller
ticks, then stops the controller program (seqStop) with the IOC left running and checks that
TickAge climbs past 10 s with a MAJOR alarm. About 2 min.

    python -m unittest -v test_tickage      (in ioc/test, bench Python, PYTHONIOENCODING=utf-8)
"""
import time
import unittest

from bench import Bench


class TickAgeTest(unittest.TestCase):
    def test_stall_raises_major(self):
        with Bench(noise=True, seed=1, tag='tickage') as b:
            P = b.p()
            ages = []
            for _ in range(8):
                time.sleep(1)
                ages.append(b.get(P + 'Sts:TickAge'))
            self.assertTrue(all(a is not None and a <= 1 for a in ages), f"while ticking: {ages}")
            self.assertEqual(b._pv(P + 'Sts:TickAge').severity, 0)

            hb0 = b.heartbeat()
            b.ioc_proc.stdin.write(b'seqStop sampleGas\n')
            b.ioc_proc.stdin.flush()
            time.sleep(14)
            pv = b._pv(P + 'Sts:TickAge')
            age = pv.get(use_monitor=False)
            self.assertIsNotNone(age)
            self.assertLessEqual(abs(b.heartbeat() - hb0), 1, "heartbeat still advancing")
            self.assertGreater(age, 10, f"TickAge {age} after the stall")
            pv.get_ctrlvars()
            self.assertEqual(pv.severity, 2, f"severity {pv.severity}, want MAJOR")


if __name__ == '__main__':
    unittest.main()
