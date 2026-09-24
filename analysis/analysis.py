"""Enclosure O2 kinetics, lid classification and lid-removal detection (helium purge).

Model: one well-mixed volume V (L), purged with pure helium at the delivered flow F (SLPM,
reconstructed from Flow_RBV + Setpoint_RBV, see o2load), with an O2 ingress J through the
lid seals (in %*L/min; x10 = mL O2/min):
        V dC/dt = J - F*C             (C in % O2, valid while C << ambient)
so     J = F*C + V*dC/dt   on every sample interval once V is known.
V comes from the high-flow purges with exactly-timed edges, where ingress is negligible.

Run:  python analysis.py      (writes results.json and J_rows.npy, prints the readout)
"""
import json
import numpy as np
from scipy.optimize import least_squares
from scipy.stats import beta
import ruptures as rpt
from o2load import load, F_history, F_at, GAS
from events import segment, lid_events, minutes

d = load()
to, o2, ts, sp, sp_at = d["to"], d["o2"], d["ts"], d["sp"], d["sp_at"]
tf, fl, rec, f_at = d["tf"], d["fl"], d["rec"], d["f_at"]
et, evv, exx = rec[:3]
is_open, runs = segment(d)
ev = lid_events(d, runs)
R = {"gas": GAS}

# ------------------------------------------------------------------ 0. flow vs setpoint audit
purges = []
for i in range(len(et) - 1):
    if evv[i] >= 15 and (i == 0 or evv[i - 1] < 15):
        j = i
        while j + 1 < len(et) and evv[j + 1] >= 15:
            j += 1
        # logged = the setpoint readback itself shows >= 15 SLPM inside the segment
        ksp = (ts >= et[i] - np.timedelta64(1, "s")) & (ts < et[j + 1]) & (sp >= 15)
        purges.append(dict(t0=str(et[i])[:19], t1=str(et[j + 1])[:19], dur_min=minutes(et[i], et[j + 1]),
                           start_exact=bool(exx[i]), end_exact=bool(exx[j + 1]),
                           logged=bool(ksp.any()), F=float(np.median(evv[i:j + 1]))))
k = np.searchsorted(ts, tf, side="right") - 1
s_at_f = np.where(k >= 0, sp[np.clip(k, 0, None)], np.nan)
R["audit"] = dict(n_flow=len(tf), n_disagree=rec[3]["n_disagree"], n_edges=len(et),
                  n_inexact_edges=int((~exx).sum()), purges=purges,
                  n_purges=len(purges), n_logged=sum(p["logged"] for p in purges))

# ------------------------------------------------------------------ 1. purge volume + clock offset
# Closures whose flow history after the last open sample is: 0, then a flow-on edge to
# >= 3 SLPM, with every edge from flow-on to the first closed sample exactly timed.
# Assume the lid closed when the flow came on: ln(Ca/C1) = sum(F_k dt_k)/V (ingress negligible).
pur = []
for e in ev:
    if e["kind"] != "closure":
        continue
    h = e["f_hist"]
    on = [n for n, s in enumerate(h) if s[2] >= 3]
    if not on or h[0][2] != 0:
        continue
    n0 = on[0]
    if not all(s[3] for s in h[n0:]) or any(s[2] == 0 for s in h[n0:]):
        continue
    segs = [(minutes(a, b), F) for a, b, F, x in h[n0:]]
    pur.append(dict(t=str(e["t_post"])[:16], F_on=h[n0][2], segs=segs, Ca=e["c_pre"], C1=e["c_post"],
                    vol=sum(dt * F for dt, F in segs),
                    logged=any(s[2] >= 3 for s in e["sp_hist"])))
F_on = np.array([p["F_on"] for p in pur]); vol_ = np.array([p["vol"] for p in pur])
y_ = np.log(np.array([p["Ca"] for p in pur]) / np.array([p["C1"] for p in pur]))


def res_pur(p):
    V, delta = p                    # delta (min): O2 clock minus flow clock
    return y_ - (vol_ + F_on * delta) / V


fit = least_squares(res_pur, [40, 0])
Jm = fit.jac; cov = np.linalg.inv(Jm.T @ Jm) * np.sum(fit.fun ** 2) / (len(y_) - 2)
V, delta = fit.x; sV, sdelta = np.sqrt(np.diag(cov))
for p in pur:
    p["V_each"] = p["vol"] / np.log(p["Ca"] / p["C1"])
R["purge"] = dict(events=pur, V_L=V, V_se=sV, delta_min=delta, delta_se=sdelta,
                  rms_resid_lnC=float(np.sqrt(np.mean(fit.fun ** 2))))

# ------------------------------------------------------------------ 2. closure gas budget
clo = []
for e in ev:
    if e["kind"] != "closure":
        continue
    need = V * np.log(e["c_pre"] / e["c_post"]) if e["c_post"] < e["c_pre"] else 0.0
    sp_L = sum(minutes(a, b) * F for a, b, F in e["sp_hist"])
    fl_L = sum(minutes(a, b) * F for a, b, F, x in e["f_hist"])
    exact = all(x for *_, x in e["f_hist"][1:])
    row = dict(t=str(e["t_post"])[:16], C_pre=e["c_pre"], C_post=e["c_post"], needed_L=need,
               setpoint_L=sp_L, flow_L=fl_L, flow_exact=exact,
               maxF=max(F for *_, F, _x in e["f_hist"]))
    # unlogged purge: end exact (setpoint change), start inexact -> solve the start from O2
    h = e["f_hist"]
    on = [n for n, s in enumerate(h) if s[2] >= 15]
    if on and not h[on[0]][3] and need > 0:
        n0 = on[0]
        after = sum(minutes(a, b) * F for a, b, F, x in h[n0 + 1:])
        Fp = h[n0][2]
        dur = (need - after) / Fp
        t_end = h[n0][1]
        # bracket of the start from the flow samples
        i_hi = np.searchsorted(tf, h[n0][0])            # first flow sample after reconstructed edge
        t_lo, t_hi = tf[i_hi - 1], tf[i_hi]
        dur_lo, dur_hi = minutes(t_hi, t_end), minutes(t_lo, t_end)
        row.update(unlogged=True, dur_from_O2=dur, dur_bracket=(dur_lo, dur_hi),
                   in_bracket=bool(dur_lo - 0.1 <= dur <= dur_hi + 0.1), end_exact=bool(h[n0 + 1][3])
                   if n0 + 1 < len(h) else False)
    clo.append(row)
R["closures"] = clo

# ------------------------------------------------------------------ 3. rises: slopes, top 10, catch statistics
dtm = np.diff(to).astype("timedelta64[ms]").astype(float) / 6e4
slope = np.diff(o2) / dtm                                           # % per min
order = np.argsort(slope)[::-1]
top = [dict(t=str(to[i + 1])[:16], from_=o2[i], to=o2[i + 1], slope_pct_per_min=slope[i],
            slope_pct_per_s=slope[i] / 60, is_removal=bool(is_open[i + 1] and not is_open[i]))
       for i in order[:10]]
rem = [e for e in ev if e["kind"] == "removal"]
clean = [e for e in rem if e["c_pre"] < 15]
rs = np.array([e["slope"] * 60 for e in clean])
# caught mid-rise: jumped > 2 %-points from the sample before (not the slow sealed-lid drift
# that often precedes a lift), short of the ambient plateau, and the next sample is open
mid = [i for i in range(1, len(o2) - 1)
       if 3 < o2[i] < 18.5 and is_open[i + 1] and o2[i] - o2[i - 1] > 2]
n_rem = len(clean); k_mid = len(mid)
p_up = beta.ppf(0.95, k_mid + 1, n_rem - k_mid)                    # one-sided 95 % Clopper-Pearson
off = [minutes(e["t_off"], e["t_post"]) for e in clean if e["t_off"] is not None]
R["rises"] = dict(top10=top, n_removals=n_rem, removal_slopes_pct_per_min=rs.tolist(),
                  caught_mid=[str(to[i])[:16] + f" {o2[i]:.2f}" for i in mid],
                  rise_time_upper_s=p_up * 600, p_up=p_up,
                  n_flow_off_before=len(off), median_off_to_first_open_min=float(np.median(off)))
closed_pair = (~is_open[:-1]) & (~is_open[1:]) & (o2[1:] < 15) & (o2[:-1] < 15)
cs = np.where(closed_pair, slope, -np.inf)
oc = np.argsort(cs)[::-1][:8]
R["closed_rises"] = [dict(t=str(to[i + 1])[:16], from_=o2[i], to=o2[i + 1], slope=slope[i],
                          F=f_at[i + 1]) for i in oc]

# ------------------------------------------------------------------ 4. ingress J per interval
closed_eps = [(a, b) for k_, a, b in runs if k_ == "closed"]
rows = []
for n, (a, b) in enumerate(closed_eps):
    for i in range(a + 1, b):              # skip first closed sample (closure dynamics)
        h = F_history(rec, to[i - 1], to[i + 1])
        if len(h) != 1:
            continue                        # flow changed within the previous or this interval
        F = h[0][2]
        # no purge within the 30 min before (post-purge mixing transients)
        hp = F_history(rec, to[i] - np.timedelta64(30, "m"), to[i])
        if any(s[2] >= 3 for s in hp) and F < 3:
            continue
        Cm = 0.5 * (o2[i] + o2[i + 1]); dC = (o2[i + 1] - o2[i]) / dtm[i]
        rows.append(dict(ep=n, i=i, F=F, C=Cm, dCdt=dC, J=F * Cm + V * dC))
Jr = np.array([[r["ep"], r["F"], r["C"], r["dCdt"], r["J"]] for r in rows])

# ------------------------------------------------------------------ 5. flow-off leak-in (lid on, F = 0)
m0 = (~is_open) & (f_at == 0) & (o2 < 15)
best, cur = (0, 0), None
for i in range(len(o2)):
    if m0[i]:
        cur = i if cur is None else cur
        if i - cur > best[1] - best[0]:
            best = (cur, i)
    else:
        cur = None
a0, b0 = best
a0 += 1
tt = minutes(to[a0], to[a0:b0 + 1]); cc = o2[a0:b0 + 1]
Ca_amb = float(np.median(o2[is_open]))


def leak_model(p, t):
    k_, C0 = p
    return Ca_amb - (Ca_amb - C0) * np.exp(-k_ * t)


lf = least_squares(lambda p: leak_model(p, tt) - cc, [1e-3, cc[0]])
k_leak, C0_leak = lf.x
lin = np.polyfit(tt, cc, 1)
R["leakin"] = dict(start=str(to[a0])[:16], end=str(to[b0])[:16], n=int(b0 - a0 + 1),
                   k_per_min=k_leak, tau_h=1 / k_leak / 60, Q_Lmin=k_leak * V, C0=C0_leak,
                   J_initial_mLmin=10 * V * k_leak * (Ca_amb - C0_leak), Ca=Ca_amb,
                   flow_max_during=float(np.max(np.abs(fl[(tf >= to[a0]) & (tf <= to[b0])]))),
                   rms=float(np.sqrt(np.mean((leak_model(lf.x, tt) - cc) ** 2))),
                   rms_line=float(np.sqrt(np.mean((np.polyval(lin, tt) - cc) ** 2))))

# ------------------------------------------------------------------ 6. helium used, and used with the lid open
t_end = min(to[-1], tf[-1])
grid = np.arange(tf[0], t_end, np.timedelta64(10, "s"))
Fg = np.clip(np.nan_to_num(F_at(rec, grid)), 0, None)
kg = np.searchsorted(to, grid, side="right") - 1
# "definitely open": the O2 samples on BOTH sides of the instant read open. The closure purge
# runs between the last open and first closed sample and must not be charged to the open lid.
kg = np.clip(kg, 0, len(to) - 2)
og = is_open[kg] & is_open[kg + 1]
kk = np.searchsorted(ts, grid, side="right") - 1
Sg = sp[np.clip(kk, 0, None)]
days = minutes(grid[0], grid[-1]) / 1440
lift = (~is_open[kg]) & is_open[kg + 1]            # inside a closed -> open (lift) interval
R["gas_use"] = dict(window=(str(grid[0])[:16], str(grid[-1])[:16]), days=days,
                    total_L=float(Fg.sum() / 6), while_open_L=float(Fg[og].sum() / 6),
                    lift_interval_upper_L=float(Fg[lift].sum() / 6),
                    purge_L=float(Fg[Fg >= 3].sum() / 6), setpoint_total_L=float(Sg.sum() / 6))

# ------------------------------------------------------------------ 7. PELT cross-check on log10(O2)
y = np.log10(o2)
dd = np.diff(y)
sig = 1.4826 * np.median(np.abs(dd - np.median(dd))) / np.sqrt(2)
quiet = [(np.datetime64("2026-09-22T10:15"), np.datetime64("2026-09-22T15:15")),
         (np.datetime64("2026-09-21T08:35"), np.datetime64("2026-09-22T07:15")),
         (np.datetime64("2026-09-23T16:05"), np.datetime64("2026-09-23T19:25"))]
JUMP_FLOOR = 0.3                             # decades: a factor of 2 in O2


def pelt(yv, pen_factor):
    cps = rpt.Pelt(model="l2", min_size=1, jump=1).fit(yv.reshape(-1, 1)).predict(
        pen=pen_factor * sig ** 2 * np.log(len(yv)))
    bnd = [0] + cps
    lv = [yv[bnd[q]:bnd[q + 1]].mean() for q in range(len(bnd) - 1)]
    return [(cps[q], lv[q + 1] - lv[q]) for q in range(len(cps) - 1)]


pen_scan = {}
for pf in [2, 10, 50, 200, 1000]:
    pen_scan[pf] = sum(len(pelt(y[(to >= qa) & (to <= qb)], pf)) for qa, qb in quiet)
pf_use = min([pf for pf, fp in pen_scan.items() if fp == 0] or [1000])
steps = [(i, j) for i, j in pelt(y, pf_use) if abs(j) >= JUMP_FLOOR]
thr_idx = [e["i_post"] for e in ev]
R["pelt"] = dict(sigma_decades=sig, pen_scan=pen_scan, pen_factor=pf_use, jump_floor=JUMP_FLOOR,
                 n_steps=len(steps), threshold_events=len(thr_idx),
                 matched=sum(any(abs(i - p) <= 1 for p, _ in steps) for i in thr_idx),
                 missed=[(str(to[i])[:16], float(o2[i - 1]), float(o2[i])) for i in thr_idx
                         if not any(abs(i - p) <= 1 for p, _ in steps)])

np.save("J_rows.npy", Jr)
with open("results.json", "w") as f:
    json.dump(R, f, indent=1, default=float)
if __name__ == "__main__":
    short = {k: v for k, v in R.items() if k not in ("closures",)}
    short["audit"] = {k: v for k, v in R["audit"].items() if k != "purges"}
    print(json.dumps(short, indent=1, default=lambda x: round(float(x), 4)))
    print("\nCLOSURES (needed vs setpoint-logged vs flow-reconstructed He, L)")
    for c in R["closures"]:
        u = ""
        if c.get("unlogged"):
            u = (f"  unlogged purge: {c['dur_from_O2']:.2f} min from O2, flow bracket "
                 f"{c['dur_bracket'][0]:.2f}-{c['dur_bracket'][1]:.2f}  {'OK' if c['in_bracket'] else 'OUTSIDE'}")
        print(f"  {c['t']}  {c['C_pre']:5.2f}->{c['C_post']:5.2f}  need {c['needed_L']:6.1f}  "
              f"SP {c['setpoint_L']:6.1f}  flow {c['flow_L']:6.1f}{'' if c['flow_exact'] else '~'}{u}")
