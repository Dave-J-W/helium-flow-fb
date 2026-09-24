"""Shared loader for the enclosure O2 / Alicat helium-flow archiver exports.

O2        15IDC:D1Dmm_calc          % O2, archived on a fixed 600 s grid
Setpoint  15IDC:Alicat1:Setpoint_RBV SLPM He, archived on change only
Flow      15IDC:Alicat1:Flow_RBV     SLPM He, archived every ~180 s

The setpoint readback MISSES many of the 20 SLPM purges and some flow changes (the flow
readback and the O2 both show them), so the flow actually delivered is reconstructed:

  F_rec(t) = setpoint (zero-order hold), overridden wherever the flow readback disagrees
             with it by more than max(0.05 SLPM, 10 %).
  Edges of an override sit at the midpoint between the bracketing flow samples (+-~90 s),
  except that an edge snaps to a setpoint event inside the same gap when the value after the
  edge matches that setpoint. Each edge carries an 'exact' flag.

Nothing is interpolated: every quantity is either sampled at its own timestamp or held.
"""
import os
from datetime import datetime
import numpy as np

# raw archiver exports live in data/ next to this file (copied from Downloads on 2026-09-24)
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
O2_FILE = DATA_DIR + r"\oxygen level rbv Sep.csv"
SP_FILE = DATA_DIR + r"\alicat setpoint rbv Sep.csv"
FLOW_FILE = DATA_DIR + r"\alicat flow rbv Sep.csv"
HR_O2 = DATA_DIR + r"\oxygen level rbv Sep high res 4 hours.csv"
HR_SP = DATA_DIR + r"\alicat setpoint rbv Sep high res 4 hours.csv"
HR_FLOW = DATA_DIR + r"\alicat flow rbv Sep high res 4 hours.csv"
GAS = "He"
TOL_ABS, TOL_REL = 0.05, 0.10


def _load(path):
    t, v = [], []
    with open(path) as f:
        next(f)
        for line in f:
            a, b = line.strip().split(",")
            t.append(datetime.strptime(a, "%Y/%m/%d %H:%M:%S.%f"))
            v.append(float(b))
    return np.array(t, dtype="datetime64[ms]"), np.array(v)


def _disagree(fl, s):
    return np.abs(fl - s) > np.maximum(TOL_ABS, TOL_REL * np.abs(s))


def reconstruct_flow(ts, sp, tf, fl):
    """Return step function (edge_t, value, exact) with value holding from edge_t onward."""
    k = np.searchsorted(ts, tf, side="right") - 1
    s_at = np.where(k >= 0, sp[np.clip(k, 0, None)], np.nan)
    bad = _disagree(fl, s_at)
    # the value the reconstruction should have AT each flow sample
    val = np.where(bad, fl, s_at)
    val = np.where(np.abs(val) < TOL_ABS, 0.0, val)            # -0.01 / 0.01 readback noise is zero
    edges = [(ts[0], sp[0], True)]
    # walk setpoint events and flow samples in time order
    j = 1                                                       # next setpoint event
    cur = sp[0]
    for i in range(len(tf)):
        # setpoint events strictly before this flow sample
        while j < len(ts) and ts[j] < tf[i]:
            # a setpoint event changes the held value only if the flow sample after it agrees
            # with it; otherwise the flow readback overrides and the edge is placed below
            edges.append((ts[j], sp[j], True))
            cur = sp[j]
            j += 1
        if not np.isclose(val[i], cur, atol=TOL_ABS, rtol=TOL_REL):
            # the delivered flow at tf[i] differs from what we are holding: find the gap
            t_prev = tf[i - 1] if i else tf[i] - np.timedelta64(180, "s")
            # snap to a setpoint event in the gap whose value matches the new flow
            cands = [(t, v) for t, v in zip(ts, sp) if t_prev < t <= tf[i]
                     and np.isclose(v, val[i], atol=TOL_ABS, rtol=TOL_REL)]
            if cands:
                te, ex = cands[-1][0], True
            else:
                te, ex = t_prev + (tf[i] - t_prev) / 2, False
                # an edge cannot precede a setpoint edge already placed in this gap
                if edges and edges[-1][0] > te:
                    te = edges[-1][0] + np.timedelta64(1, "s")
            edges.append((te, val[i], ex))
            cur = val[i]
    # drop setpoint edges immediately overridden (same position ordering preserved)
    edges.sort(key=lambda e: e[0])
    et = np.array([e[0] for e in edges]); ev = np.array([e[1] for e in edges])
    ex = np.array([e[2] for e in edges])
    return et, ev, ex, dict(n_disagree=int(bad.sum()), n_flow=len(tf))


def F_at(rec, t):
    et, ev, _ = rec[:3]
    k = np.searchsorted(et, t, side="right") - 1
    return np.where(k >= 0, ev[np.clip(k, 0, None)], np.nan)


def F_history(rec, ta, tb):
    """[(t_start, t_end, F, exact_start)] covering [ta, tb]."""
    et, ev, ex = rec[:3]
    k = np.searchsorted(et, ta, side="right") - 1
    out, t, F, e = [], ta, ev[k], ex[k]
    for j in range(k + 1, len(et)):
        if et[j] >= tb:
            break
        out.append((t, et[j], F, e)); t, F, e = et[j], ev[j], ex[j]
    out.append((t, tb, F, e))
    return out


def load():
    to, o2 = _load(O2_FILE)
    ts, sp = _load(SP_FILE)
    tf, fl = _load(FLOW_FILE)
    # drop trailing export-flush samples (seconds apart) so they cannot fake slopes
    dt = np.diff(to).astype("timedelta64[ms]").astype(float) / 1e3
    keep = np.r_[True, dt > 300]
    to, o2 = to[keep], o2[keep]
    idx = np.searchsorted(ts, to, side="right") - 1
    sp_at = np.where(idx >= 0, sp[np.clip(idx, 0, None)], np.nan)
    rec = reconstruct_flow(ts, sp, tf, fl)
    f_at = F_at(rec, to)
    # before the first flow sample only the setpoint is known
    f_at = np.where(to < tf[0], sp_at, f_at)
    return dict(to=to, o2=o2, ts=ts, sp=sp, sp_at=sp_at, tf=tf, fl=fl, rec=rec, f_at=f_at)


def hours(t, t0):
    return (t - t0).astype("timedelta64[ms]").astype(float) / 3.6e6
