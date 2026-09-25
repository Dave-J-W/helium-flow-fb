"""Regenerate the screen captures for docs/simulator/USER_GUIDE_ILLUSTRATED.md.

    python tools/make_screenshots.py

Uses headless Microsoft Edge and the simulator's screenshot mode: the URL hash
#shot=<scenario>&t=<seconds>&win=<s>&panel=admin|deep|help|agent|selftest&scroll=<id>&details=1
runs that scenario to time t on station 15IDC, pauses, and opens the requested panel.
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIM = os.path.join(ROOT, "simulator", "sample_gas_simulator.html").replace("\\", "/")
OUT = os.path.join(ROOT, "docs", "simulator", "img")
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
SIZE = "1500,950"
WINDOWS = {"600", "1800", "3600", "14400", "43200", "0"}   # the simulator's Window options (s); others show a blank selector

SHOTS = [  # (file, hash, what it shows)
    ("01_overview", "shot=1&t=1500&win=1800", "regulating after a purge from air"),
    ("02_purge", "shot=1&t=200&win=600", "purge in progress, lid check passed"),
    ("03_lid_open_stop", "shot=3&t=720&win=600", "lid lifted while regulating: OPEN_STOP"),
    ("04_admin", "shot=1&t=1500&panel=admin", "Admin screen"),
    ("05_deep_admin", "shot=1&t=1500&panel=deep", "Deep admin screen"),
    ("06_mfc_hold", "shot=8&t=420&win=600&sc=8", "Alicat on hold, resumed automatically; scenario panel"),
    ("07_mode_mismatch", "shot=6&t=14400&win=14400&sc=6", "collimator lid with mode A: flow alarm after settling"),
    ("08_settling", "shot=16&t=1500&win=3600&sc=16", "target change: SETTLING without false alarms"),
    ("09_ioc_down", "shot=14&t=600&win=600&sc=14", "IOC crashed: heartbeat alarm, Alicat holds its flow"),
    ("10_cylinder_forecast", "shot=17&t=172800&win=0&sc=17", "cylinder run-out forecast after 2 days"),
    ("11_usage_report", "shot=19&t=777600&panel=admin&scroll=usage", "helium usage report with user runs"),
    ("12_plant_params", "shot=1&t=60&details=1&scroll=plantGrid", "plant model parameters (simulator only)"),
    ("13_help", "shot=1&t=60&panel=help", "Help page"),
    ("14_selftest", "shot=1&t=60&panel=selftest", "Self-test tab"),
]

for name, h, _ in SHOTS:
    w = dict(p.split("=") for p in h.split("&")).get("win")
    if w is not None and w not in WINDOWS:
        sys.exit(f"{name}: win={w} is not a Window option {sorted(WINDOWS, key=int)}")

os.makedirs(OUT, exist_ok=True)
for name, h, what in SHOTS:
    path = os.path.join(OUT, name + ".png")
    if os.path.exists(path):
        os.remove(path)
    url = f"file:///{SIM}#{h}"
    subprocess.run([EDGE, "--headless", "--disable-gpu", "--hide-scrollbars", f"--window-size={SIZE}",
                    "--virtual-time-budget=20000", f"--screenshot={path}", url],
                   check=True, timeout=180, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ok = os.path.exists(path)
    print(f"{'ok  ' if ok else 'FAIL'} {name}.png  ({what})")
    if not ok:
        sys.exit(1)
