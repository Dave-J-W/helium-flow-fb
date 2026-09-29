"""Longest run of an unchanged Alicat Flow_RBV while Setpoint_RBV > 0 (IOC spec 8.18, gap G3).

The IOC treats a Flow_RBV that delivers no new value for too long, while the Alicat should be
flowing, as "MFC not responding". A Channel Access monitor carries a new value only when the value
changes, so the limit must sit well above the longest genuine run of one value. This prints that
run for both Flow_RBV exports in data/:

* the ~10 s file (24 Sep 09:48-13:48): every stored sample is a monitor update;
* the ~180 s file (17-24 Sep): a decimation of those updates, so a run of equal samples is only an
  upper bound (the value may have changed and changed back in between). The per-hour fraction of
  equal consecutive samples, and the same statistic in the overlap with the ~10 s file, show how
  far to trust it.

A run = the time from the first sample of a value to the first sample of a different value
(consecutive equal samples merged), counted only if the setpoint (zero-order hold of its own
on-change export) was > 0 for the whole run. Run: python flow_rbv_runs.py
"""
import csv
import os
from collections import defaultdict
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")


def load(name):
    rows = []
    with open(os.path.join(DATA, name), newline="") as f:
        r = csv.reader(f)
        next(r)
        for ts, v in r:
            rows.append((datetime.strptime(ts, "%Y/%m/%d %H:%M:%S.%f").timestamp(), float(v)))
    rows.sort()
    return rows


def sp_at(sp, t):
    cur = None
    for ts, v in sp:
        if ts > t:
            break
        cur = v
    return cur


def sp_positive_throughout(sp, t0, t1):
    cur = sp_at(sp, t0)
    if cur is None or cur <= 0:
        return False
    return all(v > 0 for ts, v in sp if t0 < ts < t1)


def runs(flow, sp):
    out, i, n = [], 0, len(flow)
    while i < n:
        j = i
        while j + 1 < n and flow[j + 1][1] == flow[i][1]:
            j += 1
        if j + 1 < n:
            t0, t1 = flow[i][0], flow[j + 1][0]
            if sp_positive_throughout(sp, t0, t1):
                out.append((t1 - t0, flow[i][1], t0))
        i = j + 1
    return sorted(out, reverse=True)


def stamp(t):
    return datetime.fromtimestamp(t).strftime("%d %b %H:%M:%S")


for label, fl, spf in [("~10 s file", "alicat flow rbv Sep high res 4 hours.csv",
                        "alicat setpoint rbv Sep high res 4 hours.csv"),
                       ("~180 s file", "alicat flow rbv Sep.csv", "alicat setpoint rbv Sep.csv")]:
    flow, sp = load(fl), load(spf)
    rr = runs(flow, sp)
    gaps = [b[0] - a[0] for a, b in zip(flow, flow[1:])]
    print(f"{label}: {len(flow)} samples {stamp(flow[0][0])} - {stamp(flow[-1][0])}, "
          f"longest gap between samples {max(gaps):.0f} s")
    print(f"  {len(rr)} runs with the setpoint > 0; longest:")
    for d, v, t0 in rr[:3]:
        print(f"    {d:6.0f} s  at {v:.2f} SLPM from {stamp(t0)}")

flow, sp = load("alicat flow rbv Sep.csv"), load("alicat setpoint rbv Sep.csv")
hours = defaultdict(lambda: [0, 0])
for (t0, v0), (t1, v1) in zip(flow, flow[1:]):
    s0, s1 = sp_at(sp, t0), sp_at(sp, t1)
    if s0 and s1 and s0 > 0 and s1 > 0:
        h = hours[datetime.fromtimestamp(t0).strftime("%m-%d %H")]
        h[0] += 1
        h[1] += v0 == v1
full = [b / a for a, b in hours.values() if a >= 15]
print(f"~180 s file: {len(full)} clock hours (>= 15 pairs, setpoint > 0); fraction of equal "
      f"consecutive pairs: median {sorted(full)[len(full) // 2]:.2f}, max {max(full):.2f}; "
      f"hours without a change: {sum(1 for f in full if f == 1.0)}")
hi = load("alicat flow rbv Sep high res 4 hours.csv")
ov = [(t, v) for t, v in flow if hi[0][0] <= t <= hi[-1][0]]
eq = sum(1 for (_, x), (_, y) in zip(ov, ov[1:]) if x == y)
print(f"overlap with the ~10 s file: {eq} of {len(ov) - 1} consecutive ~180 s pairs equal")
