"""Bench session that produces the documentation screenshots (docs/ioc/img/).

Starts the plant simulator and the bench IOC (bench.Bench), a separate Phoebus (ioc/tools/
doc_screens.py) and walks the controller through the states the operator guide shows:
IDLE, PURGE from air, REGULATE, Admin and Deep admin, a stuck-hold MAJOR alarm, shadow mode and
a dead IOC. Takes about 30 min, in real time. Everything it starts, it stops.

    python doc_session.py [--out docs/ioc/img]
"""
import argparse
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'tools'))
import doc_screens  # noqa: E402
from bench import Bench, REPO  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--out', default=str(REPO / 'docs' / 'ioc' / 'img'))
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    def shot(display, name):
        doc_screens.shot(display, str(out / f'{name}.png'))

    def log(msg):
        print(time.strftime('%H:%M:%S'), msg, flush=True)

    with Bench(noise=True, seed=1, tag='docs') as b:
        P = b.p()
        doc_screens.start('SIM:SampleGas:', '127.0.0.1:5076 127.0.0.1:5066')
        try:
            b.wait_state('IDLE', 60)
            log('IDLE')
            time.sleep(20)
            shot('simple', 'simple_idle')
            shot('main', 'main_idle')

            b.cmd('Purge')
            b.wait_state('PURGE', 60)
            log('PURGE')
            time.sleep(90)
            shot('main', 'main_purge')
            shot('simple', 'simple_purge')

            b.wait_state('REGULATE', 1800)
            log('REGULATE')
            time.sleep(300)
            shot('main', 'main_regulate')
            shot('simple', 'simple_regulate')
            shot('admin', 'admin')
            shot('deep', 'deep')

            b.world('Hold', 'stuck')
            t0 = time.time()
            while time.time() - t0 < 180 and b.get(P + 'Sts:WorstSevr') != 2:
                time.sleep(2)
            log(f'alarm: {b.get(P + "Sts:Banner.VAL$", as_string=True)}')
            time.sleep(5)
            shot('main', 'main_alarm')
            shot('simple', 'simple_alarm')
            b.world('ClearHold', 1)
            time.sleep(30)

            b.put(P + 'Par:writeEnable', 0)
            log('shadow')
            time.sleep(10)
            shot('simple', 'simple_shadow')
            shot('main', 'main_shadow')

            b.stop_ioc(kill=True)
            log('IOC stopped')
            time.sleep(15)
            shot('main', 'main_ioc_down')
        finally:
            doc_screens.stop()
    log('done')


if __name__ == '__main__':
    sys.exit(main())
