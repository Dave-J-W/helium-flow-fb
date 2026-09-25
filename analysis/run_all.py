"""Regenerate every number and figure from the raw CSVs in data/, in dependency order.

    python run_all.py

Each step runs with the repo as its working directory, so this works from anywhere.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
STEPS = ["analysis.py",          # results.json, J_rows.npy
         "fig1_overview.py",
         "fig2_daily.py",
         "figs_kinetics.py",     # figs 3-8 (needs results.json, J_rows.npy)
         "hr_analysis.py",       # hr_results.json, figs 9-13 (needs results.json)
         "noise_analysis.py"]    # noise_results.json, fig 14 (1 Hz O2 record, independent)

os.makedirs(os.path.join(HERE, "figures"), exist_ok=True)
for s in STEPS:
    print(f"== {s}", flush=True)
    r = subprocess.run([sys.executable, s], cwd=HERE, stdout=subprocess.DEVNULL)
    if r.returncode:
        sys.exit(f"{s} failed with exit code {r.returncode}")
print("all figures in figures/, numbers in results.json, hr_results.json and noise_results.json")
