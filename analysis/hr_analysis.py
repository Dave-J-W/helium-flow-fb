"""High-resolution tranche: 10 s O2 in two windows on 24 Sep, normal lid throughout.

  window A: 'oxygen level rbv Sep high res 4 hours.csv'  00:59-04:59
            paired with the weekly Flow_RBV (~180 s) and the exact setpoint events
  window B: 'oxygen level rbv sep high res 8 hours.csv'  05:46-13:46
            paired with the high-res Flow_RBV / Setpoint_RBV (~10 s, 09:48-13:48) where they exist

Events analysed (all located from the data, anchored to exact setpoint events where available):
  A    flow off 02:55:42 -> lid lift ~03:00 -> open -> unlogged purge -> hold
  C    flow off 08:47:35 -> NO lift -> flow back on 09:09:43       (sealed leak-in + delays)
  B    flow off 11:51:03 -> lid lift ~11:52:14 -> open ~2 min -> purge 11:54:19-12:00:19 -> hold
       (this is the lift no 600 s O2 sample caught)

Run after analysis.py (needs results.json). Writes hr_results.json and fig9-fig13.
"""
import json
import numpy as np
from scipy.optimize import least_squares
from scipy.stats import beta
import ruptures as rpt
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.lines import Line2D
from o2load import load, _load, HR_O2, HR_FLOW, HR_SP, F_at, GAS, DATA_DIR

HR_O2_B = DATA_DIR + r"\oxygen level rbv sep high res 8 hours.csv"
R = json.load(open("results.json"))
V = R["purge"]["V_L"]; kL = R["leakin"]["k_per_min"]; Ca_w = R["leakin"]["Ca"]
d = load()
rec, ts, sp, tf, fl = d["rec"], d["ts"], d["sp"], d["tf"], d["fl"]
tfh, fh = _load(HR_FLOW); tsh, sph = _load(HR_SP)


def load_o2(path):
    t, c = _load(path)
    dtv = np.diff(t).astype("timedelta64[ms]").astype(float) / 1e3
    keep = np.r_[True, dtv > 5]                       # drop the export-flush sample
    return t[keep], c[keep]


def sec(a, b):
    return (b - a).astype("timedelta64[ms]").astype(float) / 1e3


def T(s):
    return np.datetime64("2026-09-24T" + s)


def sp_event(v_test, after, before, tt=None, vv=None):
    """First setpoint-readback event in (after, before) whose value passes v_test. Uses the weekly
    setpoint export unless another (e.g. the high-res one) is given: the weekly export is missing
    some events, such as the 11:51:02 flow-off."""
    tt = ts if tt is None else tt
    vv = sp if vv is None else vv
    j = np.where((tt > after) & (tt < before) & v_test(vv))[0]
    return tt[j[0]]


def robust_sigma(x):
    dx = np.diff(x)
    return 1.4826 * np.median(np.abs(dx - np.median(dx))) / np.sqrt(2)


tA, cA = load_o2(HR_O2)
tB, cB = load_o2(HR_O2_B)
H = {"A": {"window": (str(tA[0])[:19], str(tA[-1])[:19]), "n": len(tA)},
     "B": {"window": (str(tB[0])[:19], str(tB[-1])[:19]), "n": len(tB)},
     "hr_flow_window": (str(tfh[0])[:19], str(tfh[-1])[:19])}
for nm, (t_, c_) in (("A", (tA, cA)), ("B", (tB, cB))):
    tw_, cw_ = d["to"], d["o2"]
    com, i1, i2 = np.intersect1d(t_, tw_, return_indices=True)
    H[nm]["vs_weekly"] = dict(shared=len(com), max_abs_diff=float(np.max(np.abs(c_[i1] - cw_[i2]))))
    H[nm]["repeat_fraction"] = float(np.mean(np.diff(c_) == 0))


# ------------------------------------------------------------------ building blocks
def hinge(p, x):                                       # flat, then linear from x0
    c0, x0, s = p
    return c0 + s * np.clip(x - x0, 0, None)


def delay_and_slope(t, c, t_event, t_stop, sig):
    """Flat-then-linear fit from t_event to t_stop: transport delay and slope (%/min)."""
    m = (t >= t_event - np.timedelta64(120, "s")) & (t <= t_stop)
    x, y = sec(t_event, t[m]), c[m]
    f = least_squares(lambda p: hinge(p, x) - y, [y[0], 60, 5e-4])
    return dict(delay_s=float(f.x[1]), slope_pct_per_min=float(f.x[2] * 60), c0=float(f.x[0]),
                rms=float(np.sqrt(np.mean(f.fun ** 2))), x=x, y=y, p=f.x)


def lift_analysis(t, c, t_off):
    """Largest single-sample rise after t_off. If the sample before it already jumped well
    above the sealed trend (caught mid-rise), fit onset and a single fast exponential."""
    m = t > t_off
    idx = np.where(m)[0]
    i = idx[np.argmax(np.diff(c[idx[0] - 1:idx[-1] + 1])[1:])]     # c[i] -> c[i+1] biggest
    i = int(np.argmax(np.where(m[:-1], np.diff(c), -np.inf)))
    Ca = float(np.max(c[i + 1:i + 25]))                             # first ~4 min open peak
    pre_trend = c[i - 1] - c[i - 2]
    partial = (c[i] - c[i - 1]) > 5 * max(abs(pre_trend), 0.01)     # c[i] already on the rise
    out = dict(i=i, Ca=Ca, bracket=(str(t[i])[11:19], str(t[i + 1])[11:19]), C_last=float(c[i]),
               C_first_big=float(c[i + 1]), dt=sec(t[i], t[i + 1]))
    x0 = t[i - 1] if partial else t[i]
    C0 = float(c[i - 1]) if partial else float(c[i])
    out["C0"] = C0
    xs = sec(x0, t[i - 1 if partial else i:i + 8])
    ys = c[i - 1 if partial else i:i + 8]
    if partial:
        # C = C0 + (Ca - C0)(1 - exp(-(x - xL)/tau)) for x > xL, fitted to the rise samples
        def res(p):
            xL, tau = p
            return C0 + (Ca - C0) * (1 - np.exp(-np.clip(xs[:4] - xL, 0, None) / tau)) - ys[:4]
        f = least_squares(res, [8, 6], bounds=([0, 0.5], [10, 60]))
        xL, tau = f.x
        out["t_onset"] = x0 + np.timedelta64(int(xL * 1000), "ms")
        out.update(partial=True, t_lift=str(out["t_onset"])[11:21], onset_before_partial_s=float(xs[1] - xL),
                   tau_fast_s=float(tau), tau_is="fitted (a sample caught mid-rise)",
                   pred_3rd=float(C0 + (Ca - C0) * (1 - np.exp(-(xs[3] - xL) / tau))), obs_3rd=float(ys[3]),
                   delay_after_flow_off_s=float(sec(t_off, x0) + xL))
    else:
        f1 = (c[i + 1] - c[i]) / (Ca - c[i])
        tau = -out["dt"] / np.log(1 - f1)
        out["t_onset"] = t[i]                    # latest possible onset consistent with the bound
        out.update(partial=False, t_lift=f"{str(t[i])[11:19]}-{str(t[i + 1])[11:19]}", tau_fast_s=float(tau),
                   tau_is="upper bound (lift assumed at the last sealed sample)", fraction_first=float(f1),
                   delay_after_flow_off_s=float(sec(t_off, t[i]) + out["dt"] / 2))
    out["slope_lower_pct_per_min"] = float(np.max(np.diff(c[i - 1:i + 3])) / 10 * 60)
    xo = sec(x0, t[i:i + 40]); co = c[i:i + 40]
    out["t_to_18p5_s"] = float(xo[np.argmax(co > 18.5)])
    out["t_to_19p0_s"] = float(xo[np.argmax(co > 19.0)])
    out["x0"] = x0
    return out


def purge_analysis(t, c, t_end, t_on_known=None, t_start=None, F=20.0):
    t_start = t_end - np.timedelta64(9, "m") if t_start is None else t_start
    m = (t >= t_start) & (t <= t_end)
    x, y = sec(t_end, t[m]), c[m]
    # pre-purge level: the 40 s before a known flow-on (the open reading may still be rising),
    # otherwise the first two minutes of the window
    if t_on_known is not None and t_start > t_end - np.timedelta64(9, "m"):
        Cpl = float(np.median(c[(t >= t_on_known - np.timedelta64(40, "s")) & (t <= t_on_known)]))
    else:
        Cpl = float(np.median(y[:12]))

    def model(p, xx):
        t0, V_ = p
        return np.where(xx < t0, Cpl, Cpl * np.exp(-F * np.clip(xx - t0, 0, None) / 60 / V_))

    f = least_squares(lambda p: np.log(model(p, x)) - np.log(y), [-340, 42])
    t0, V_ = f.x
    Jf = f.jac; cov = np.linalg.inv(Jf.T @ Jf) * np.sum(f.fun ** 2) / (len(y) - 2)
    se_t0, se_V = np.sqrt(np.diag(cov))
    md = (x > t0 + 20) & (x < -5)
    xs, ls = x[md], np.log(y[md])
    Vloc = np.array([(xs[k + 3], F / (-np.polyfit(xs[k:k + 7] / 60, ls[k:k + 7], 1)[0])) for k in range(len(xs) - 6)])
    out = dict(C_plateau=Cpl, onset=str(t_end + np.timedelta64(int(t0 * 1000), "ms"))[11:21], onset_se_s=float(se_t0),
               onset_before_end_s=float(-t0), V_L=float(V_), rms_log=float(np.sqrt(np.mean(f.fun ** 2))),
               V_local_first=float(np.median(Vloc[:6, 1])), V_local_last=float(np.median(Vloc[-6:, 1])),
               x=x, y=y, model=lambda xx: model(f.x, xx), Vloc=Vloc)
    if t_on_known is not None:
        out["delay_after_flow_on_s"] = float(sec(t_on_known, t_end) + t0)
    # post-purge settling at the hold flow
    mp = (t > t_end) & (t <= t_end + np.timedelta64(10, "m"))
    xr, cr = sec(t_end, t[mp]), c[mp]
    fr = least_squares(lambda p: p[0] + p[1] * np.exp(-xr / p[2]) - cr, [0.95, 0.15, 90],
                       bounds=([0, 0, 5], [5, 5, 3000]))
    out.update(post_C_first=float(cr[0]), post_C_inf=float(fr.x[0]), post_A=float(fr.x[1]), post_tau_s=float(fr.x[2]),
               post_x=xr, post_y=cr, post_fit=fr.x)
    return out


def window_slopes(t, c, w):
    n = int(round(w / 10)) + 1
    tt = sec(t[0], t) / 60
    return np.array([np.polyfit(tt[k:k + n], c[k:k + n], 1)[0] for k in range(len(c) - n + 1)])


# ------------------------------------------------------------------ window A
offA = sp_event(lambda v: v == 0, tA[0], tA[-1])
LA = lift_analysis(tA, cA, offA)
endA = sp_event(lambda v: v > 0, tA[LA["i"]], tA[-1])
PA = purge_analysis(tA, cA, endA, t_on_known=endA - np.timedelta64(360, "s"))   # 6.00-min program assumed
sigA = robust_sigma(cA[(tA > tA[0] + np.timedelta64(5, "m")) & (tA < offA)])
DA = delay_and_slope(tA, cA, offA, tA[LA["i"] - 3], sigA)

# ------------------------------------------------------------------ window B
offC = sp_event(lambda v: v == 0, tB[0], T("10:00"))
onC = sp_event(lambda v: v > 0, offC, T("10:00"))
sigB = robust_sigma(cB[(tB > tB[0] + np.timedelta64(5, "m")) & (tB < offC)])
DC = delay_and_slope(tB, cB, offC, onC, sigB)
# flow back on at 0.25 SLPM: O2 keeps rising, then turns over -> delay = time of the maximum
mC = (tB >= onC) & (tB <= onC + np.timedelta64(8, "m"))
# smooth over 3 samples before locating the turn-over
cs = np.convolve(cB[mC], np.ones(3) / 3, mode="same")
turn = tB[mC][1:-1][np.argmax(cs[1:-1])]
DC["delay_flow_on_s"] = sec(onC, turn)
offB = sp_event(lambda v: v == 0, T("11:00"), tB[-1], tsh, sph)
LB = lift_analysis(tB, cB, offB)
# purge edges from the high-res setpoint readback, which caught both ramps mid-way
on_B = tsh[np.where((tsh > offB) & (sph > 0.3) & (sph < 19.9))[0][0]]           # 11:54:19 (0.43)
end_B = tsh[np.where((tsh > on_B + np.timedelta64(60, "s")) & (sph > 0.3) & (sph < 19.9))[0][0]]  # 12:00:19 (19.55)
# window starts after the lid is fully open (the 9-min default would reach back before the lift)
PB = purge_analysis(tB, cB, end_B, t_on_known=on_B, t_start=tB[LB["i"] + 6])
H["B"]["purge_edges_from"] = f"Setpoint_RBV mid-ramp readings {str(on_B)[11:19]} and {str(end_B)[11:19]}"
H["B"]["purge_program_min"] = sec(on_B, end_B) / 60


def strip(dct):
    return {k: v for k, v in dct.items() if k not in ("x", "y", "p", "model", "Vloc", "post_x", "post_y", "post_fit",
                                                      "x0", "i", "t_onset")}


H["A"].update(flow_off=str(offA)[11:21], lift=strip(LA), purge=strip(PA), sealed=strip(DA), sigma_hold=sigA)
H["C"] = dict(flow_off=str(offC)[11:21], flow_on=str(onC)[11:21], sealed=strip(DC),
              predicted_from_21Sep=float(kL * (Ca_w - DC["c0"])))
H["B"].update(flow_off=str(offB)[11:21], lift=strip(LB), purge=strip(PB), sigma_hold=sigB,
              lift_after_flow_off_s=LB["delay_after_flow_off_s"])

# ------------------------------------------------------------------ open-lid readings
iAo = LA["i"] + 1
m_pl = (tA > tA[iAo] + np.timedelta64(10, "m")) & (tA < endA - np.timedelta64(8, "m"))
m_dip = (tA > tA[iAo] + np.timedelta64(200, "s")) & (tA < tA[iAo] + np.timedelta64(420, "s"))
H["A"]["open"] = dict(peak=LA["Ca"], end=float(cA[m_pl][-1]), dip_min=float(cA[m_dip].min()),
                      dip_t=str(tA[m_dip][np.argmin(cA[m_dip])])[11:19])
iBo = LB["i"] + 1
H["B"]["open"] = dict(peak=LB["Ca"], duration_s=sec(tB[LB["i"]], on_B))

# ------------------------------------------------------------------ rate thresholds (both windows)
regimes = {
    "hold 0.25 SLPM, lid on": [(tA, cA, tA[0] + np.timedelta64(5, "m"), offA),
                               (tB, cB, tB[0] + np.timedelta64(5, "m"), offC),
                               (tB, cB, onC + np.timedelta64(10, "m"), offB),
                               (tB, cB, end_B + np.timedelta64(10, "m"), tB[-1])],
    "flow off, lid on": [(tA, cA, offA, tA[LA["i"]]), (tB, cB, offC, onC + np.timedelta64(3, "m")),
                         (tB, cB, offB, tB[LB["i"] - 1])],
    "post-purge settling": [(tA, cA, endA, endA + np.timedelta64(10, "m")), (tB, cB, end_B, end_B + np.timedelta64(10, "m"))],
    "open, plateau": [(tA, cA, tA[iAo] + np.timedelta64(10, "m"), endA - np.timedelta64(8, "m"))],
}
rates = {}
for k, segs in regimes.items():
    rates[k] = {}
    for w in (10, 30, 60):
        rates[k][w] = np.concatenate([window_slopes(t_[(t_ >= a) & (t_ <= b)], c_[(t_ >= a) & (t_ <= b)], w)
                                      for t_, c_, a, b in segs])


def lift_rate(t, c, L, w):
    n = int(round(w / 10)) + 1
    i1 = L["i"] + 1
    tt = sec(t[0], t) / 60
    return float(np.polyfit(tt[i1 - n + 1:i1 + 1], c[i1 - n + 1:i1 + 1], 1)[0])


lift_rates = {nm: {w: lift_rate(t_, c_, L_, w) for w in (10, 30, 60)}
              for nm, t_, c_, L_ in (("A", tA, cA, LA), ("B", tB, cB, LB))}
max_sealed = {w: float(max(np.max(rates[k][w]) for k in rates if "open" not in k)) for w in (10, 30, 60)}
H["rates"] = dict(max_sealed=max_sealed, lift=lift_rates,
                  min_ratio={w: min(lift_rates["A"][w], lift_rates["B"][w]) / max_sealed[w] for w in (10, 30, 60)})

# ------------------------------------------------------------------ PELT on log10(O2), window B
y = np.log10(cB)
sg = robust_sigma(y)
quietB = [(tB > tB[0] + np.timedelta64(5, "m")) & (tB < offC), (tB > end_B + np.timedelta64(15, "m"))]


def pelt(yv, pf):
    cps = rpt.Pelt(model="l2", min_size=3, jump=1).fit(yv.reshape(-1, 1)).predict(pen=pf * sg ** 2 * np.log(len(yv)))
    b = [0] + cps
    lv = [yv[b[q]:b[q + 1]].mean() for q in range(len(b) - 1)]
    return [(cps[q], lv[q + 1] - lv[q]) for q in range(len(cps) - 1)]


scan = {pf: sum(len(pelt(y[m], pf)) for m in quietB) for pf in (10, 100, 1000, 10000, 100000, 1000000)}
pf_use = min([pf for pf, n in scan.items() if n == 0] or [1000000])
raw = [(i, j) for i, j in pelt(y, pf_use) if abs(j) >= 0.05]
merged = []
for i, j in raw:
    if merged and np.sign(j) == np.sign(merged[-1][2]) and sec(tB[merged[-1][1]], tB[i]) <= 120:
        merged[-1] = (merged[-1][0], i, merged[-1][2] + j)
    else:
        merged.append((i, i, j))
H["pelt_B"] = dict(sigma_decades=sg, pen_scan=scan, pen_factor=pf_use, floor_decades=0.05,
                   events=[(str(tB[a])[11:19], str(tB[b])[11:19], float(j)) for a, b, j in merged])

# ------------------------------------------------------------------ transport delays
H["delays_s"] = {
    "flow off -> O2 starts rising (0.25 SLPM hold, lid on), A": DA["delay_s"],
    "flow off -> O2 starts rising (0.25 SLPM hold, lid on), C": DC["delay_s"],
    "flow back on at 0.25 SLPM -> O2 turns over, C": DC["delay_flow_on_s"],
    "20 SLPM purge on -> O2 starts falling, B (edge ±5 s)": PB["delay_after_flow_on_s"],
    "20 SLPM purge on -> O2 starts falling, A (6.00-min program assumed)": PA["delay_after_flow_on_s"],
}
json.dump(H, open("hr_results.json", "w"), indent=1, default=float)


# =============================================================== figures
def title(fig, a, b):
    fig.text(0.06, 0.965, a, fontsize=12, weight="bold")
    fig.text(0.06, 0.950, b, fontsize=8.5, va="top")


def lz(v):
    return np.where(np.abs(v) < 0.02, 0.05, np.abs(v))


CA_, CB_, CC_ = "C0", "C3", "C2"

# ---- fig 9: both windows
fig, ax = plt.subplots(4, 1, figsize=(16, 13), gridspec_kw=dict(height_ratios=[1.4, 0.8, 1.4, 0.8]))
fig.subplots_adjust(left=0.06, right=0.98, top=0.9, bottom=0.05, hspace=0.35)
for k, (t_, c_, lab) in enumerate(((tA, cA, "A"), (tB, cB, "B"))):
    a, b = ax[2 * k], ax[2 * k + 1]
    a.semilogy(t_, c_, "-", lw=0.8, color=CA_ if lab == "A" else CB_, label=f"O2, 10 s, window {lab} ({len(t_)} samples)")
    mw = (d["to"] >= t_[0]) & (d["to"] <= t_[-1])
    a.semilogy(d["to"][mw], d["o2"][mw], "o", ms=6, mfc="none", color="k", label="O2, 600 s weekly archive (same values)")
    a.set_ylim(0.7, 30); a.set_ylabel("O2 (%)"); a.legend(fontsize=8, loc="center left")
    g = np.arange(t_[0], t_[-1], np.timedelta64(10, "s"))
    b.step(g, lz(F_at(rec, g)), where="post", color=CC_, lw=1, label="delivered flow (weekly reconstruction)")
    mf = (tf >= t_[0]) & (tf <= t_[-1])
    b.plot(tf[mf], lz(fl[mf]), "k.", ms=4, label="Flow_RBV weekly (~180 s)")
    if lab == "B":
        mh = (tfh >= t_[0]) & (tfh <= t_[-1])
        b.plot(tfh[mh], lz(fh[mh]), "-", color="C1", lw=0.8, label="Flow_RBV high res (~10 s)")
    b.set_yscale("log"); b.set_ylim(0.03, 40)
    b.set_yticks([0.05, 0.25, 1, 5, 20]); b.set_yticklabels(["off", "0.25", "1", "5", "20"])
    b.set_ylabel(f"SLPM {GAS}"); b.legend(fontsize=8, loc="center left")
    for x_ in (a, b):
        x_.set_xlim(t_[0], t_[-1]); x_.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
onA_t = endA - np.timedelta64(int(PA["onset_before_end_s"] * 1000), "ms")
evs = [(ax[0], offA, "flow off"), (ax[0], tA[LA["i"] + 1], "lift"), (ax[0], onA_t, "purge on"),
       (ax[0], endA, "purge off"), (ax[2], offC, "flow off (no lift)"), (ax[2], onC, "flow on")]
for a, tt_, lab in evs:
    a.axvline(tt_, color="r", ls=":", lw=0.8)
    a.text(tt_, 25, " " + lab, color="r", fontsize=7.5, rotation=90, va="top")
ax[2].axvspan(offB, end_B, color="r", alpha=0.12, lw=0)
ax[2].text(offB, 25, "flow off -> lift -> purge\n11:51-12:00 (fig 13) ", color="r", fontsize=7.5, va="top", ha="right")
ax[0].set_title("window A: 00:59-04:59 (flow only from the ~180 s weekly readback)", fontsize=9, loc="left")
ax[2].set_title("window B: 05:46-13:46 (high-res flow from 09:48); contains the 11:52 lift no 600 s sample saw", fontsize=9, loc="left")
title(fig, "High-res O2, 24 Sep: two 10 s windows, normal lid, two lid cycles and one sealed flow-off",
      "Both 10 s O2 exports agree exactly with the 600 s archive at shared timestamps. Window B was the re-export; "
      "it overlaps the high-res flow and setpoint.")
fig.savefig("figures/fig9_hr_overview.png", dpi=130); plt.close(fig)

# ---- fig 10: sealed flow-off and lifts
fig = plt.figure(figsize=(16, 10))
gs = fig.add_gridspec(2, 3, width_ratios=[1.2, 1.2, 0.95])
fig.subplots_adjust(left=0.06, right=0.98, top=0.87, bottom=0.07, hspace=0.35, wspace=0.28)
a = fig.add_subplot(gs[0, 0])
for D_, col, lab in ((DA, CA_, "A (lift at +%.0f s)" % LA["delay_after_flow_off_s"]),
                     (DC, CC_, "C (no lift, 22 min)")):
    a.plot(D_["x"], D_["y"] - D_["c0"], "o", ms=3, color=col, label=f"{lab}: delay {D_['delay_s']:.0f} s, "
           f"then {D_['slope_pct_per_min']:.3f} %/min")
    a.plot(D_["x"], hinge(D_["p"], D_["x"]) - D_["c0"], "-", color=col, lw=1)
mBs = (tB >= offB - np.timedelta64(120, "s")) & (tB <= tB[LB["i"] - 1])
a.plot(sec(offB, tB[mBs]), cB[mBs] - np.median(cB[mBs][:10]), "s", ms=4, color=CB_,
       label=f"B: lid lifted at +{LB['delay_after_flow_off_s']:.0f} s, before any leak-in")
a.axvline(0, color="k", ls=":"); a.set_xlim(-120, 400); a.set_ylim(-0.02, 0.2)
a.set_xlabel("s since flow off (setpoint event)"); a.set_ylabel("O2 rise since flow off (%-points)")
a.legend(fontsize=7.5, loc="upper left")
a.set_title(f"(a) sealed, flow off: ~{np.mean([DA['delay_s'], DC['delay_s']]):.0f} s delay, then leak-in "
            f"(21 Sep predicts {H['C']['predicted_from_21Sep']:.3f} %/min)", fontsize=9, loc="left")
b = fig.add_subplot(gs[0, 1])
stop = {"A": endA - np.timedelta64(7, "m"), "B": on_B}          # stop before each purge
for L_, t_, c_, col, lab in ((LA, tA, cA, CA_, "A"), (LB, tB, cB, CB_, "B")):
    x0 = L_["t_onset"]
    m = (t_ >= x0 - np.timedelta64(40, "s")) & (t_ <= min(x0 + np.timedelta64(180, "s"), stop[lab]))
    b.plot(sec(x0, t_[m]), c_[m], "o-", ms=4, lw=0.7, color=col,
           label=f"{lab}: tau_fast {'=' if L_['partial'] else '<='} {L_['tau_fast_s']:.1f} s")
b.set_xlabel("s since lift onset (B: fitted; A: its last sealed sample)"); b.set_ylabel("O2 (%)"); b.legend(fontsize=8)
b.set_title(f"(b) the two lifts, aligned at onset. B's 1.85 % sample is {LB['onset_before_partial_s']:.1f} s after its fitted onset",
            fontsize=9, loc="left")
bb = fig.add_subplot(gs[1, 0])
for L_, t_, c_, col, lab in ((LA, tA, cA, CA_, "A"), (LB, tB, cB, CB_, "B")):
    x0 = L_["t_onset"]
    m = (t_ >= x0) & (t_ <= min(x0 + np.timedelta64(150, "s"), stop[lab]))
    bb.semilogy(sec(x0, t_[m]), np.clip(L_["Ca"] - c_[m], 1e-3, None), "o-", ms=4, lw=0.7, color=col, label=f"{lab}")
xx = np.linspace(0, 40, 50)
bb.semilogy(xx, (LB["Ca"] - LB["C0"]) * np.exp(-xx / LB["tau_fast_s"]), "--", color="k", lw=0.8,
            label=f"exp(-t/{LB['tau_fast_s']:.1f} s)")
bb.set_ylim(0.005, 30); bb.set_xlabel("s since lift onset")
bb.set_ylabel("distance from the open-lid peak (%-points)"); bb.legend(fontsize=8)
bb.set_title("(c) a ~6 s exponential carries 97 % of the step; then a slower tail", fontsize=9, loc="left")
cc_ = fig.add_subplot(gs[1, 1])
mA2 = (tA >= tA[iAo]) & (tA <= endA - np.timedelta64(6, "m"))
cc_.plot(tA[mA2], cA[mA2], "-", color=CA_, lw=0.8)
cc_.axhline(19, color="r", ls=":", lw=0.8); cc_.text(tA[mA2][0], 19.01, " 19 % threshold", color="r", fontsize=8)
cc_.set_ylabel("O2 (%)"); cc_.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
cc_.set_title(f"(d) A, lid off 1.7 h: peak {LA['Ca']:.2f} %, relaxes to {H['A']['open']['end']:.2f} %; "
              f"dip at {H['A']['open']['dip_t'][:5]} to {H['A']['open']['dip_min']:.2f} % (below 19 %)", fontsize=9, loc="left")
tx = fig.add_subplot(gs[:, 2]); tx.axis("off")
lines = ["LID KINETICS AT 10 s (normal lid)", "",
         "SEALED, FLOW OFF", f" delay before leak-in   A {DA['delay_s']:.0f} s   C {DC['delay_s']:.0f} s",
         f" leak-in slope          A {DA['slope_pct_per_min']:.3f}  C {DC['slope_pct_per_min']:.3f} %/min",
         f" 21 Sep model predicts    {H['C']['predicted_from_21Sep']:.3f} %/min",
         f" flow back on (C): O2 keeps rising {DC['delay_flow_on_s']:.0f} s", "",
         "LIFT                     A            B"]
lines.append(f" last sealed  %     {LA['C_last']:9.2f}    {LB['C_last']:9.2f}")
lines.append(f" first big    %     {LA['C_first_big']:9.2f}    {LB['C_first_big']:9.2f}")
lines.append(f" tau_fast     s     {'<=' + format(LA['tau_fast_s'], '.1f'):>9}    {LB['tau_fast_s']:9.1f}")
lines.append(f" > 18.5 % after s   {LA['t_to_18p5_s']:9.0f}    {LB['t_to_18p5_s']:9.0f}")
lines.append(f" > 19.0 % after s   {LA['t_to_19p0_s']:9.0f}    {LB['t_to_19p0_s']:9.0f}")
lines.append(f" max 10-s slope     {LA['slope_lower_pct_per_min']:7.0f}      {LB['slope_lower_pct_per_min']:7.0f}  %/min")
lines.append(f" lift after flow-off {LA['delay_after_flow_off_s']:6.0f} s     {LB['delay_after_flow_off_s']:6.0f} s")
lines += ["", f" B's fitted exponential predicts the next", f"   sample at {LB['pred_3rd']:.2f} %; observed {LB['obs_3rd']:.2f} %.",
          f" A's bound (<= {LA['tau_fast_s']:.1f} s) is consistent: A's lift", "   began at, or just before, its last sealed sample.",
          "", "vs the 600 s record: slope >= 1.9 %/min and", " transit <= 54 s (95 %). Measured: tau ~6 s,",
          " peak slope > 90 %/min."]
tx.text(0, 1, "\n".join(lines), va="top", family="monospace", fontsize=8.3)
title(fig, "Lid removal at 10 s: a ~6 s exponential takes the enclosure to air",
      "A = 02:55-03:00 (window A); B = 11:51-11:52 (window B, the lift no 600 s sample saw); C = 08:47-09:10 flow off with the lid kept on. "
      "Two lifts and one sealed flow-off, all normal lid:\nsingle-event numbers, not distributions.")
fig.savefig("figures/fig10_hr_lift.png", dpi=130); plt.close(fig)

# ---- fig 11: purges
fig, ax = plt.subplots(2, 2, figsize=(16, 9))
fig.subplots_adjust(left=0.06, right=0.98, top=0.86, bottom=0.07, hspace=0.35, wspace=0.2)
for P_, col, lab in ((PA, CA_, "A"), (PB, CB_, "B")):
    ax[0, 0].semilogy(P_["x"], P_["y"], "o", ms=3, color=col, label=f"{lab}: onset {P_['onset']}, V = {P_['V_L']:.1f} L")
    xx = np.linspace(P_["x"][0], 0, 300)
    ax[0, 0].semilogy(xx, P_["model"](xx), "-", color=col, lw=0.8)
    ax[0, 1].plot(P_["x"], 100 * (np.log(P_["y"]) - np.log(P_["model"](P_["x"]))), "o", ms=3, color=col, label=lab)
    ax[1, 0].plot(P_["Vloc"][:, 0], P_["Vloc"][:, 1], "o-", ms=3, color=col, label=lab)
    ax[1, 1].plot(P_["post_x"], P_["post_y"] - P_["post_fit"][0], "o", ms=3, color=col,
                  label=f"{lab}: {P_['post_A']:.3f} %·exp(-t/{P_['post_tau_s']:.0f} s)")
    ax[1, 1].plot(P_["post_x"], P_["post_fit"][1] * np.exp(-P_["post_x"] / P_["post_fit"][2]), "-", color=col, lw=0.8)
ax[0, 0].set_xlabel("s relative to purge end"); ax[0, 0].set_ylabel("O2 (%)"); ax[0, 0].legend(fontsize=8, loc="lower left")
ax[0, 0].set_title(f"(a) 20 SLPM purges; weekly V = {V:.1f} L", fontsize=9, loc="left")
ax[0, 1].axhline(0, color="k", lw=0.6); ax[0, 1].set_xlabel("s relative to purge end")
ax[0, 1].set_ylabel("residual (% of C)"); ax[0, 1].legend(fontsize=8)
ax[0, 1].set_title("(b) residuals of the well-mixed fit: same shape both times", fontsize=9, loc="left")
ax[1, 0].axhline(V, color="k", ls=":", lw=0.8)
ax[1, 0].set_xlabel("s relative to purge end"); ax[1, 0].set_ylabel("20 SLPM / (-dlnC/dt) (L)"); ax[1, 0].legend(fontsize=8)
ax[1, 0].set_title("(c) apparent volume rises through the purge: mixing good, not perfect", fontsize=9, loc="left")
ax[1, 1].set_xlabel("s after purge end"); ax[1, 1].set_ylabel("O2 above final level (%-points)"); ax[1, 1].legend(fontsize=8)
ax[1, 1].set_title(f"(d) settling at 0.25 SLPM after the purge (one well-mixed volume: tau {V/0.25:.0f} min)",
                   fontsize=9, loc="left")
title(fig, "Purges at 10 s: reproducible, well mixed to a few %, with a short transport delay",
      f"B's purge edges come from Setpoint_RBV readings caught mid-ramp ({str(on_B)[11:19]}, {str(end_B)[11:19]}): a "
      f"{H['B']['purge_program_min']:.2f} min program; the O2 starts falling {PB['delay_after_flow_on_s']:.0f} s after flow-on.\n"
      "A's start is known only from the O2 (Flow_RBV brackets it to ±90 s).")
fig.savefig("figures/fig11_hr_purge.png", dpi=130); plt.close(fig)

# ---- fig 12: thresholds
fig = plt.figure(figsize=(16, 7.5))
gs = fig.add_gridspec(1, 2, width_ratios=[1.5, 1])
fig.subplots_adjust(left=0.07, right=0.98, top=0.84, bottom=0.1, wspace=0.15)
a = fig.add_subplot(gs[0])
cols = {"hold 0.25 SLPM, lid on": "C2", "flow off, lid on": "C1", "post-purge settling": "C4", "open, plateau": "0.5"}
rng = np.random.default_rng(0)
for yi, w in enumerate((10, 30, 60)):
    for q, (k, col) in enumerate(cols.items()):
        v = np.clip(np.abs(rates[k][w]), 1e-4, None)
        a.semilogx(v, yi - 0.3 + 0.15 * q + rng.uniform(-0.04, 0.04, len(v)), "o", ms=2, color=col, alpha=0.45,
                   label=k if yi == 0 else None)
    for nm, mk in (("A", "*"), ("B", "P")):
        a.semilogx(lift_rates[nm][w], yi + 0.3, mk, ms=14, color="C3", mec="k", label=f"lift {nm}" if yi == 0 else None)
a.axvline(5, color="k", ls="--"); a.text(5.3, 2.55, "5 %/min", fontsize=8)
a.set_yticks([0, 1, 2]); a.set_yticklabels(["10 s window", "30 s window", "60 s window"])
a.set_xlim(1e-4, 300); a.set_ylim(-1.2, 2.7)
a.set_xlabel("|dO2/dt| from a least-squares slope over the window (%/min)")
a.legend(fontsize=8, loc="lower right", ncol=3)
a.set_title("(a) rate-trigger separation, both 10 s windows (12 h of sealed and open data, 2 lifts)", fontsize=9, loc="left")
b = fig.add_subplot(gs[1]); b.axis("off")
lines = ["TRIGGER NUMBERS, 10 s O2, NORMAL LID", "",
         f"hold noise: sigma {sigA*1000:.1f} (A) / {sigB*1000:.1f} (B) m% per sample", "",
         " window  max sealed    lift A    lift B   ratio"]
for w in (10, 30, 60):
    lines.append(f"  {w:3d} s  {max_sealed[w]:7.3f}   {lift_rates['A'][w]:7.1f}   {lift_rates['B'][w]:7.1f}  "
                 f"{H['rates']['min_ratio'][w]:5.0f}x")
lines += ["            (%/min)", "",
          "LEVEL", " O2 > 10 %: first sample after each lift.",
          f" Open reading: peak {LA['Ca']:.2f}/{LB['Ca']:.2f} %, then relaxes",
          f" to {H['A']['open']['end']:.2f} %; dipped to {H['A']['open']['dip_min']:.2f} %. 19 % is too tight.", "",
          "DELAYS (use for controller tuning)"]
short = ["flow off -> leak-in reaches O2 (A)", "flow off -> leak-in reaches O2 (C)",
          "0.25 SLPM back on -> O2 turns over (C)", "20 SLPM on -> O2 falls (B, edge ±5 s)",
          "20 SLPM on -> O2 falls (A, 6-min prog.)"]
for s_, v in zip(short, H["delays_s"].values()):
    lines.append(f" {v:5.0f} s  {s_}")
lines += ["", "SUGGESTED (normal lid):", " lift: O2 > 10 %  OR  30-s slope > 5 %/min",
          " feedback at hold flow: expect ~80 s dead time", "", "CAVEAT: 2 lifts, 2 purges, 1 lid (normal)."]
b.text(0, 1, "\n".join(lines), va="top", family="monospace", fontsize=8.2)
title(fig, "Thresholds and delays from 12 h of 10 s O2: lifts sit >30x above anything sealed",
      "Sealed maxima include flow-off leak-in and pre-lift acceleration. Lift rates are lower bounds set by the 10 s sampling.")
fig.savefig("figures/fig12_hr_thresholds.png", dpi=130); plt.close(fig)

# ---- fig 13: the 11:51 cycle with high-res flow AND high-res O2
fig, ax = plt.subplots(2, 1, figsize=(16, 9), sharex=True, gridspec_kw=dict(height_ratios=[1.4, 1]))
fig.subplots_adjust(left=0.06, right=0.82, top=0.87, bottom=0.07, hspace=0.07)
w0, w1 = T("11:48"), T("12:06")
m = (tB >= w0) & (tB <= w1)
ax[0].semilogy(tB[m], cB[m], "o-", ms=3, lw=0.7, color=CB_, label="O2, 10 s (re-export)")
mw = (d["to"] >= w0) & (d["to"] <= w1)
ax[0].semilogy(d["to"][mw], d["o2"][mw], "ks", ms=9, mfc="none", label="O2, 600 s archive: the only samples before")
ax[0].set_ylabel("O2 (%)"); ax[0].set_ylim(0.7, 30); ax[0].legend(fontsize=8, loc="center left")
mh = (tfh >= w0) & (tfh <= w1)
ax[1].plot(tfh[mh], fh[mh], "o-", ms=3, lw=0.7, color="C1", label="Flow_RBV, high res")
ms_ = (tsh >= w0 - np.timedelta64(3, "h")) & (tsh <= w1)
ax[1].step(np.r_[tsh[ms_], w1], np.r_[sph[ms_], sph[ms_][-1]], where="post", color="k", lw=1.5, alpha=0.5,
           label="Setpoint_RBV, high res")
ax[1].set_ylabel(f"SLPM {GAS}"); ax[1].legend(fontsize=8, loc="center left")
ax[1].set_xlim(w0, w1); ax[1].xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
for tt_, lab in ((offB, "flow off"), (tB[LB["i"] + 1], "lift"), (on_B, "purge on"), (end_B, "purge off")):
    for x_ in ax:
        x_.axvline(tt_, color="r", ls=":", lw=0.8)
    ax[0].text(tt_, 25, " " + lab, color="r", fontsize=8, rotation=90, va="top")
lines = ["THE 11:52 LIFT, NOW MEASURED", "",
         f"flow off      {H['B']['flow_off'][:8]}", f"lift          {LB['t_lift'][:8]}",
         f"  ({LB['delay_after_flow_off_s']:.0f} s after flow-off)",
         f"open for      {H['B']['open']['duration_s']/60:.1f} min", f"peak O2       {LB['Ca']:.2f} %",
         f"purge on      {str(on_B)[11:19]}", f"O2 falls from {PB['onset'][:8]}",
         f"  ({PB['delay_after_flow_on_s']:.0f} s delay)", f"purge off     {str(end_B)[11:19]}",
         f"program       {H['B']['purge_program_min']:.2f} min", f"V from decay  {PB['V_L']:.1f} L",
         f"after purge   {PB['post_C_first']:.3f} -> {PB['post_C_inf']:.3f} %", f"  tau {PB['post_tau_s']:.0f} s", "",
         "The 10-min replay inferred this lift", "from the O2 after the purge (1.34 %",
         "seen; 1.31 % predicted for an air", "start, 0.07 % for a sealed box).",
         "The 10 s data confirm it directly."]
fig.text(0.835, 0.85, "\n".join(lines), va="top", family="monospace", fontsize=8.3)
title(fig, "24 Sep 11:48-12:06: the lid lift no 600 s sample saw, at 10 s in O2, flow and setpoint",
      "All three PVs at ~10 s. The setpoint readback caught both purge ramps mid-way (0.43 and 19.55 SLPM), which fixes the purge edges to a few seconds.")
fig.savefig("figures/fig13_hr_flow.png", dpi=130); plt.close(fig)

print(json.dumps({k: v for k, v in H.items()}, indent=1, default=lambda x: round(float(x), 4) if np.isscalar(x) else str(x)))
