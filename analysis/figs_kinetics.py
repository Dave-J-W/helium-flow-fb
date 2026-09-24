"""Figures 3-8 from results.json (analysis.py) and classify.py. Run analysis.py first."""
import json
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from o2load import load, GAS, F_at, F_history
from events import segment, minutes
from classify import classify

R = json.load(open("results.json"))
d = load()
to, o2, ts, sp, sp_at = d["to"], d["o2"], d["ts"], d["sp"], d["sp_at"]
tf, fl, rec, f_at = d["tf"], d["fl"], d["rec"], d["f_at"]
is_open, runs = segment(d)
V = R["purge"]["V_L"]; sV = R["purge"]["V_se"]
Ca = R["leakin"]["Ca"]
LIDC = {"normal": "#2a9d4b", "collimator": "#7b3fb5", "atypical": "#e08a00", "air": "0.6",
        "unresolved": "#e08a00"}


def title(fig, a, b):
    fig.text(0.06, 0.965, a, fontsize=12, weight="bold")
    fig.text(0.06, 0.950, b, fontsize=8.5, va="top")      # top-anchored: multi-line subtitles grow down


# =============================================================== fig 3: timing link + volume
P = R["purge"]["events"]
F = np.array([p["F_on"] for p in P]); x = np.array([p["vol"] for p in P])
y = np.log(np.array([p["Ca"] for p in P]) / np.array([p["C1"] for p in P]))
fig, ax = plt.subplots(1, 3, figsize=(16, 5.4), gridspec_kw=dict(width_ratios=[1.1, 0.8, 1.1]))
fig.subplots_adjust(left=0.06, right=0.98, top=0.82, bottom=0.12, wspace=0.3)
for f, mk in [(3, "s"), (5.2, "^"), (20, "o")]:
    m = F == f
    ax[0].plot(x[m], y[m], mk, ms=8, label=f"{f:g} SLPM (n={m.sum()})")
xx = np.linspace(0, 120, 50)
ax[0].plot(xx, xx / V, "k-", lw=1, label=f"fit: V = {V:.1f} ± {sV:.1f} L")
ax[0].set_xlabel(f"{GAS} delivered since flow-on, ∫F dt (L)")
ax[0].set_ylabel("ln(C_ambient / C_first-closed-sample)")
ax[0].legend(fontsize=8, loc="upper left")
ax[0].set_title("(a) purge dilution, exactly-timed purges only", fontsize=9, loc="left")
res = y - (x + F * R["purge"]["delta_min"]) / V
ax[1].axhline(0, color="k", lw=0.6)
ax[1].plot(x, 100 * res, "o", color="C0")
ax[1].set_xlabel("∫F dt (L)"); ax[1].set_ylabel("residual in C (%, relative)")
ax[1].set_title("(b) residuals", fontsize=9, loc="left")
ax[1].text(0.03, 0.04, f"rms {100*R['purge']['rms_resid_lnC']:.1f} % of C\n"
           f"clock offset O2 vs {GAS}-flow PVs:\n  {60*R['purge']['delta_min']:+.0f} ± {60*R['purge']['delta_se']:.0f} s",
           transform=ax[1].transAxes, fontsize=8.5, va="bottom")
C = R["closures"]
nd = np.array([c["needed_L"] for c in C]); real = nd > 5
spL = np.array([max(c["setpoint_L"], 0.01) for c in C]); flL = np.array([max(c["flow_L"], 0.01) for c in C])
ex_ = np.array([c["flow_exact"] for c in C])
ax[2].loglog(nd[real], spL[real], "x", color="C3", ms=7, label="per Setpoint_RBV")
ax[2].loglog(nd[real & ex_], flL[real & ex_], "o", color="C0", label="per reconstructed flow, edges exact")
ax[2].loglog(nd[real & ~ex_], flL[real & ~ex_], "o", mfc="none", color="C0",
             label="per reconstructed flow, purge start ±90 s")
for a_, b_, c_ in zip(nd[real], spL[real], flL[real]):
    if b_ < 0.5 * c_:
        ax[2].plot([a_, a_], [b_, c_], color="0.8", lw=0.6, zorder=0)
ax[2].plot([5, 300], [5, 300], "k-", lw=0.7)
ax[2].text(8, 11, "delivered = needed", rotation=33, fontsize=8)
ax[2].set_xlabel(f"{GAS} needed to dilute air to the observed O2, V·ln(C0/C1) (L)")
ax[2].set_ylabel(f"{GAS} delivered between the two O2 samples (L)")
ax[2].set_xlim(8, 300); ax[2].set_ylim(0.008, 300)
ax[2].legend(fontsize=8, loc="center left")
nu = sum(1 for c in C if c.get("unlogged"))
ax[2].set_title("(c) closures: does the flow explain the O2 drop?", fontsize=9, loc="left")
ax[2].text(9, 0.012, f"{nu} closures: Setpoint_RBV shows 0.01-2 L, but Flow_RBV shows a 20 SLPM purge\n"
           "that the setpoint readback never recorded. With it, delivered = needed.", fontsize=8, color="C3")
title(fig, f"Time link and enclosure volume: one well-mixed {V:.0f} L box, clocks agree, flow closes the budget",
      "Assumes lid closed when the flow switched on (a later closure would bias V upward). Fit uses closures whose purge edges are all "
      "exactly timed by setpoint events. No interpolation: O2 at its own timestamps, flow as a zero-order hold.")
fig.savefig("figures/fig3_timing_volume.png", dpi=130); plt.close(fig)

# =============================================================== fig 4: lid classification
out, fits, sig, (epJ, FJ, JJ, labJ) = classify()
fig = plt.figure(figsize=(16, 10))
gs = fig.add_gridspec(2, 2, height_ratios=[1, 1.1], width_ratios=[1.5, 1])
fig.subplots_adjust(left=0.06, right=0.98, top=0.85, bottom=0.07, hspace=0.3, wspace=0.18)
ax0 = fig.add_subplot(gs[0, :])
ax0.semilogy(to, o2, "-", color="0.3", lw=0.6)
for k, a, b in runs:
    if k == "open":
        ax0.axvspan(to[a] - np.timedelta64(5, "m"), to[b] + np.timedelta64(5, "m"), color="0.85", lw=0)
for r in out:
    c = LIDC[r["lid"]]
    ax0.axvspan(r["t0"] - np.timedelta64(5, "m"), r["t1"] + np.timedelta64(5, "m"), color=c, alpha=0.35, lw=0)
    if r["disagree"]:
        ax0.plot(r["t0"] + (r["t1"] - r["t0"]) / 2, 25, "v", color="r", ms=8)
    if r["lid"] == "atypical":
        ax0.plot(r["t0"] + (r["t1"] - r["t0"]) / 2, 25, "d", color=LIDC["atypical"], ms=8, mec="k")
ax0.set_ylim(0.08, 35); ax0.set_ylabel("O2 (%)")
ax0.xaxis.set_major_locator(mdates.DayLocator()); ax0.xaxis.set_major_formatter(mdates.DateFormatter("%a %d %b"))
from matplotlib.patches import Patch
from matplotlib.lines import Line2D
nL = {L: sum(r["lid"] == L for r in out) for L in LIDC}
ax0.legend(handles=[Patch(color=LIDC["normal"], alpha=0.5, label=f"normal lid ({nL['normal']} episodes)"),
                    Patch(color=LIDC["collimator"], alpha=0.5, label=f"collimator lid ({nL['collimator']})"),
                    Patch(color=LIDC["atypical"], alpha=0.5, label=f"atypical ingress ({nL['atypical']})"),
                    Patch(color="0.85", label="open (O2 >= 19 %)"),
                    Line2D([], [], marker="v", color="r", ls="", label="hold-flow band disagrees with physics")],
           fontsize=8, ncol=5, loc="lower center", bbox_to_anchor=(0.5, 1.0))
ax0.set_title("(a) lid per closed episode", fontsize=9, loc="left", pad=22)

ax1 = fig.add_subplot(gs[1, 0])
fin = {r["n"]: r["lid"] for r in out}
for L in ("normal", "collimator", "atypical"):
    m = np.array([fin[e] == L for e in epJ])
    ax1.loglog(FJ[m] * np.exp(np.random.default_rng(1).normal(0, 0.03, m.sum())), JJ[m], "o", ms=4,
               color=LIDC[L], alpha=0.6, label=f"{L} ({m.sum()} intervals)")
ff = np.logspace(np.log10(0.2), np.log10(2), 50)
for L in ("normal", "collimator"):
    p = fits[L]
    ax1.plot(ff, 10 ** np.polyval(p, np.log10(ff)), "-", color=LIDC[L], lw=2)
    ax1.fill_between(ff, 10 ** (np.polyval(p, np.log10(ff)) - sig[L]), 10 ** (np.polyval(p, np.log10(ff)) + sig[L]),
                     color=LIDC[L], alpha=0.12, lw=0)
    ax1.text(ff[-1] * 1.02, 10 ** np.polyval(p, np.log10(ff[-1])),
             f"J = {10**p[1]:.1f}·F^{p[0]:+.2f}\n(×/÷{10**sig[L]:.2f})", color=LIDC[L], fontsize=8, va="center")
ax1.axvspan(0.15, 0.35, color=LIDC["normal"], alpha=0.07, lw=0)
ax1.axvspan(0.7, 1.2, color=LIDC["collimator"], alpha=0.07, lw=0)
ax1.set_xlim(0.2, 3.2)
ax1.set_xlabel(f"delivered {GAS} flow F (SLPM)"); ax1.set_ylabel("O2 ingress J = F·C + V·dC/dt  (mL O2 / min)")
ax1.legend(fontsize=8, loc="upper right")
ax1.set_title("(b) the physics that identifies the lid: ingress vs flow, one point per 10-min interval", fontsize=9, loc="left")

ax2 = fig.add_subplot(gs[1, 1]); ax2.axis("off")
lines = ["EPISODES WHERE PHYSICS OVERRIDES / QUALIFIES THE HOLD-FLOW BAND", ""]
for r in out:
    if r["disagree"] or r["lid"] == "atypical":
        s = f"{str(r['t0'])[5:16].replace('T',' ')}-{str(r['t1'])[11:16]}  band: {r['sp_band'].split()[0]:10s} -> {r['lid']}"
        lines.append(s)
        lines.append(f"     J ~ {r['Jmed']:.1f} mL O2/min at F = {', '.join(f'{f:g}' for f in r['Fs'])} SLPM  ({r['basis']})")
css_c = 10 ** np.polyval(fits["collimator"], np.log10(0.25)) / 2.5
lines += ["", "Collimator lid run at 0.25 SLPM: J ~ 23-30 mL/min, so O2 climbs",
          f"toward ~{css_c:.0f} % (tau = V/F = {V/0.25/60:.1f} h). The lid cannot change without",
          "O2 going to air, so the 0.8-SLPM part of the same episode fixes it.",
          "", "NOT CLASSIFIED: 3 single samples at 18.9 % with flow off",
          "(20 Sep 13:59, 14:19, 15:49) = ambient drifting under 19 %."]
ax2.text(0, 1, "\n".join(lines), va="top", family="monospace", fontsize=8)
title(fig, "Which lid was on: identified from O2 ingress, not from the setpoint",
      f"Ingress from a one-volume model, V = {V:.1f} L. Intervals with F > 2 SLPM, flow off, a setpoint change, or the first sample after closure are excluded. "
      f"\nLid families fitted self-consistently, starting from the band of each interval's delivered {GAS} flow (Flow_RBV-corrected).")
fig.savefig("figures/fig4_lid_classification.png", dpi=130); plt.close(fig)

# =============================================================== fig 5: rises and thresholds
Rr = R["rises"]
rs = np.array(Rr["removal_slopes_pct_per_min"])
top = Rr["top10"]
dtm = np.diff(to).astype("timedelta64[ms]").astype(float) / 6e4
sl = np.diff(o2) / dtm
cl = (~is_open[:-1]) & (~is_open[1:]) & (o2[1:] < 15) & (o2[:-1] < 15) & (sl > 0)
fig = plt.figure(figsize=(16, 9.5))
gs = fig.add_gridspec(2, 2, height_ratios=[1, 1], width_ratios=[1.35, 1])
fig.subplots_adjust(left=0.06, right=0.98, top=0.88, bottom=0.07, hspace=0.38, wspace=0.2)
a = fig.add_subplot(gs[0, 0])
rng = np.random.default_rng(0)
a.semilogx(sl[cl], 0 + rng.uniform(-0.15, 0.15, cl.sum()), "o", ms=3, color="0.5", alpha=0.6,
           label=f"lid on, O2 rising ({cl.sum()} intervals)")
topv = sorted([t["slope_pct_per_min"] for t in top])
a.semilogx(rs, 1 + rng.uniform(-0.15, 0.15, len(rs)), "o", ms=5, color="C1",
           label=f"lid removals ({len(rs)}), as sampled")
tm = rs >= topv[0] - 1e-9
a.semilogx(rs[tm], 1 + rng.uniform(-0.15, 0.15, tm.sum()), "o", ms=9, mfc="none", mec="k",
           label="the 10 steepest rises in the record")
lb = 15.5 / (Rr["rise_time_upper_s"] / 60)
a.annotate("", xy=(lb, 1.55), xytext=(1.9, 1.55), arrowprops=dict(arrowstyle="->", color="C3"))
a.text(2.1, 1.65, f"true lift slope >= {lb:.0f} %/min (95 %): 0 of {Rr['n_removals']} lifts caught mid-rise\n"
       f"=> air reaches the sensor within {Rr['rise_time_upper_s']:.0f} s of lifting", color="C3", fontsize=8)
a.axvspan(1.9, 2.1, color="C1", alpha=0.15, lw=0)
a.text(1.95, 0.45, "sampling\nceiling\n18.5 %/600 s", fontsize=7, ha="center", color="C1")
mx = sl[cl].max()
a.axvline(1.0, color="k", ls="--", lw=1)
a.text(1.03, -0.45, "suggested\nrate trigger\n1 %/min", fontsize=8)
a.text(mx * 1.05, 0.25, f"max with lid on\n{mx:.2f} %/min", fontsize=8, color="0.3")
a.set_yticks([0, 1]); a.set_yticklabels(["lid on", "lid lifted"]); a.set_ylim(-0.6, 2.1)
a.set_xlim(1e-3, 50)
a.set_xlabel("O2 rise rate between consecutive 600 s samples (%/min)")
a.legend(fontsize=8, loc="upper left")
a.set_title("(a) lid-removal vs sealed-lid rise rates", fontsize=9, loc="left")

b = fig.add_subplot(gs[0, 1]); b.axis("off")
L = ["THE 10 STEEPEST O2 RISES (all are lid removals)", "",
     " sample time        from %   to %   slope %/min   %/s"]
for t in top:
    L.append(f" {t['t'][5:16].replace('T',' '):16s} {t['from_']:6.2f}  {t['to']:6.2f}   {t['slope_pct_per_min']:7.3f}    {t['slope_pct_per_s']:.4f}")
L += ["", f" all {len(rs)} removals: median {np.median(rs):.2f} %/min, range {rs.min():.2f}-{rs.max():.2f}",
      " (low values = O2 already raised before the lift, e.g. flow",
      "  off with lid on, or collimator lid starved at 0.25 SLPM)", "",
      " THESE SLOPES ARE SAMPLING-LIMITED LOWER BOUNDS: every lift",
      " completes inside one 600 s interval, so the spread only",
      " reflects the starting O2. The true kinetics need the live PV",
      " (or an archiver rate << 1 min) to resolve."]
b.text(0, 1, "\n".join(L), va="top", family="monospace", fontsize=8.3)

c = fig.add_subplot(gs[1, 0])
lk = R["leakin"]
i0 = int(np.where(to == np.datetime64(lk["start"]))[0][0]) if np.any(to == np.datetime64(lk["start"])) else None
m = (to >= np.datetime64(lk["start"])) & (to <= np.datetime64(lk["end"]))
tt = minutes(to[m][0], to[m]); cc = o2[m]
k = lk["k_per_min"]
from scipy.optimize import least_squares
C0 = least_squares(lambda p: Ca - (Ca - p[0]) * np.exp(-k * tt) - cc, [cc[0]]).x[0]
mod = Ca - (Ca - C0) * np.exp(-k * tt)
c.plot(tt / 60, cc, "o", ms=4, label="O2, flow off, lid on (normal lid)")
c.plot(tt / 60, mod, "-", color="C3", label=f"C = Ca - (Ca-C0)·exp(-t/tau), tau = {lk['tau_h']:.1f} h")
lin = np.polyfit(tt, cc, 1)
c.plot(tt / 60, np.polyval(lin, tt), ":", color="0.4", label="straight line (for comparison)")
c.set_xlabel(f"hours since {lk['start'][5:16].replace('T', ' ')}"); c.set_ylabel("O2 (%)")
c.legend(fontsize=8, loc="upper left")
c.set_title("(c) sealed-lid leak-in with the flow OFF (21 Sep)", fontsize=9, loc="left")
c.text(0.98, 0.05, f"effective air exchange Q = V/tau = {lk['Q_Lmin']*1000:.0f} mL/min\n"
       f"O2 ingress at start = {lk['J_initial_mLmin']:.1f} mL/min (vs ~{10**np.polyval(fits['normal'], np.log10(0.25)):.1f} with 0.25 SLPM {GAS} on)\n"
       f"rms: exponential {lk['rms']:.3f} %, straight line {lk['rms_line']:.3f} %",
       transform=c.transAxes, ha="right", fontsize=8)
cr = fig.add_subplot(gs[1, 1])
cr.axhline(0, color="k", lw=0.6)
cr.plot(tt / 60, cc - mod, "o-", ms=3, lw=0.5, color="C3", label="exponential")
cr.plot(tt / 60, cc - np.polyval(lin, tt), "o-", ms=3, lw=0.5, color="0.5", label="straight line")
cr.set_xlabel("hours"); cr.set_ylabel("residual (% O2)"); cr.legend(fontsize=8)
cr.set_title("(d) residuals of (c)", fontsize=9, loc="left")
title(fig, "Lid removal is instantaneous at this sampling; sealed-lid rises never exceed 0.17 %/min",
      "O2 archived every 600 s. A rise between consecutive samples is plotted as ΔC/Δt, which is capped at ~1.9 %/min by the sampling itself.")
fig.savefig("figures/fig5_rises_thresholds.png", dpi=130); plt.close(fig)

# =============================================================== fig 6: programming chart
fig, ax = plt.subplots(1, 2, figsize=(15, 5.6))
fig.subplots_adjust(left=0.06, right=0.98, top=0.83, bottom=0.12, wspace=0.22)
ff = np.logspace(np.log10(0.2), np.log10(2), 100)     # the range the families were fitted on
for L in ("normal", "collimator"):
    p = fits[L]
    J = 10 ** np.polyval(p, np.log10(ff))
    Css = J / (10 * ff)
    lo_, hi_ = Css / 10 ** sig[L], Css * 10 ** sig[L]
    ax[0].loglog(ff, Css, color=LIDC[L], lw=2, label=f"{L} lid: C_ss = J(F)/(10·F)")
    ax[0].fill_between(ff, lo_, hi_, color=LIDC[L], alpha=0.15, lw=0)
# observed settled points: last 3 samples of constant-flow stretches >= 60 min in classified episodes
for r in out:
    if r["lid"] not in ("normal", "collimator"):
        continue
    a_, b_ = r["i0"], r["i1"]
    j = a_
    while j <= b_:
        jj = j
        while jj + 1 <= b_ and f_at[jj + 1] == f_at[j]:
            jj += 1
        if jj - j >= 6 and 0 < f_at[j] <= 3:
            ax[0].plot(f_at[j], np.median(o2[jj - 2:jj + 1]), "o", color=LIDC[r["lid"]], mec="k", ms=5)
        j = jj + 1
ax[0].axvspan(0.15, 0.35, color=LIDC["normal"], alpha=0.07, lw=0)
ax[0].axvspan(0.7, 1.2, color=LIDC["collimator"], alpha=0.07, lw=0)
ax[0].axhline(1.0, color="k", ls=":", lw=0.8)
ax[0].set_xlabel("hold flow F (SLPM)"); ax[0].set_ylabel("steady-state O2 (%)")
ax[0].set_ylim(0.05, 30); ax[0].set_xlim(0.1, 4); ax[0].legend(fontsize=8, loc="upper right")
ax[0].text(0.11, 0.07, "curves drawn only over the fitted 0.2-2 SLPM range", fontsize=7.5, color="0.4")
ax[0].set_title("(a) what a hold flow buys, per lid (dots: end of >= 60 min constant-flow stretches;\n"
                "not all are settled, since tau = V/F is 2.8 h at 0.25 SLPM)", fontsize=9, loc="left")
FF = np.array([0.25, 0.8, 1, 2, 5, 10, 20, 30])
tp = V / FF * np.log(19.4 / 1.0)
ax[1].loglog(FF, tp, "o-", color="k")
for f_, t_ in zip(FF, tp):
    ax[1].text(f_ * 1.08, t_ * 1.05, f"{t_:.1f} min" if t_ < 100 else f"{t_/60:.1f} h", fontsize=8)
ax[1].set_xlabel("purge flow F (SLPM)"); ax[1].set_ylabel("time from air to 1 % O2 (min)")
ax[1].set_title(f"(b) purge time from air to 1 %: V·ln(19.4/1)/F; {GAS} used always {V*np.log(19.4):.0f} L", fontsize=9, loc="left")
ax[1].axvline(20, color="C1", ls="--", lw=0.8)
c6 = 19.4 * np.exp(-120 / V)
ax[1].text(21, 30, f"current program:\n20 SLPM x 6 min\n-> {c6:.1f} % predicted", fontsize=8, color="C1")
title(fig, "Mass-flow-controller programming: hold flow per lid, and purge duration",
      f"Well-mixed model with V = {V:.1f} ± {sV:.1f} L and the fitted ingress families. Ingress falls with flow for the collimator lid (over-pressure), "
      "so its hold flow matters far more than the normal lid's.")
fig.savefig("figures/fig6_mfc_programming.png", dpi=130); plt.close(fig)

# =============================================================== fig 7: setpoint vs flow audit
A = R["audit"]
fig = plt.figure(figsize=(16, 10))
gs = fig.add_gridspec(2, 2, height_ratios=[1, 1.1], width_ratios=[1.4, 1])
fig.subplots_adjust(left=0.06, right=0.98, top=0.87, bottom=0.07, hspace=0.35, wspace=0.18)
a = fig.add_subplot(gs[0, 0])
un = [c for c in R["closures"] if c.get("unlogged")]
for p in A["purges"]:
    t0 = np.datetime64(p["t0"])
    if p["logged"]:
        a.plot(t0, p["dur_min"], "s", color="C0", ms=6)
tu = np.array([np.datetime64(c["t"]) for c in un])
du = np.array([c["dur_from_O2"] for c in un])
lo_ = np.array([c["dur_bracket"][0] for c in un]); hi_ = np.array([c["dur_bracket"][1] for c in un])
# bracket edges drawn as-is; a point a few seconds outside its bracket (tolerance 0.1 min) gets a zero-length bar
a.errorbar(tu, du, yerr=[np.clip(du - lo_, 0, None), np.clip(hi_ - du, 0, None)], fmt="o", color="C3", ms=5, capsize=3, lw=0.8)
a.axhline(6, color="k", ls=":", lw=0.8)
a.set_ylim(0, 10); a.set_ylabel("20 SLPM purge duration (min)")
a.xaxis.set_major_locator(mdates.DayLocator()); a.xaxis.set_major_formatter(mdates.DateFormatter("%a %d"))
a.legend(handles=[Line2D([], [], marker="s", color="C0", ls="", label=f"in Setpoint_RBV ({A['n_logged']}): edges from setpoint events"),
                  Line2D([], [], marker="o", color="C3", ls="", label=f"NOT in Setpoint_RBV ({len(un)} closures): duration from the O2 drop,\n"
                         "bar = what the ~180 s Flow_RBV sampling allows")],
         fontsize=8, loc="lower left")
a.set_title("(a) every closure purge is the same 6-min program, whether or not the setpoint readback logged it",
            fontsize=9, loc="left")
b = fig.add_subplot(gs[0, 1])
dl = [p["dur_min"] for p in A["purges"] if p["logged"] and p["start_exact"] and p["end_exact"]]
b.hist(dl, bins=np.arange(3, 9.01, 0.25), color="C0", alpha=0.7, label=f"logged, exact edges (n={len(dl)})")
b.hist(du, bins=np.arange(3, 9.01, 0.25), color="C3", alpha=0.6, label=f"unlogged, from O2 (n={len(du)})")
b.set_xlabel("purge duration (min)"); b.set_ylabel("count"); b.legend(fontsize=8)
b.set_title(f"(b) unlogged: median {np.median(du):.2f} min, range {du.min():.2f}-{du.max():.2f}; "
            f"{sum(c['in_bracket'] for c in un)}/{len(un)} inside flow bracket", fontsize=9, loc="left")
c = fig.add_subplot(gs[1, 0])
w0, w1 = np.datetime64("2026-09-24T04:20"), np.datetime64("2026-09-24T05:10")
m = (to >= w0) & (to <= w1)
c.semilogy(to[m], o2[m], "o", color="C0", ms=6, label="O2, 600 s archive", zorder=5)
cex = [x for x in un if x["t"] == "2026-09-24T04:49"][0]
t_end = np.datetime64("2026-09-24T04:49:15.691")
t_st = t_end - np.timedelta64(int(cex["dur_from_O2"] * 60e3), "ms")
grid = np.arange(w0, w1, np.timedelta64(10, "s"))
Cm_ = np.empty(len(grid)); Cc = o2[np.searchsorted(to, w0)]
for i_, g in enumerate(grid):
    Fg_ = 20.0 if t_st <= g < t_end else (0.25 if g >= t_end else 0.0)
    if Fg_ > 0:
        Css = 10 ** np.polyval(fits["normal"], np.log10(min(max(Fg_, 0.2), 2))) / 10 / Fg_
        Cc = Css + (Cc - Css) * np.exp(-Fg_ * (10 / 60) / V)
    Cm_[i_] = Cc
c.semilogy(grid, Cm_, "-", color="C3", lw=1, label=f"one-volume model, V = {V:.1f} L, purge {cex['dur_from_O2']:.2f} min ending at setpoint 0.25")
c.set_ylim(0.5, 30); c.set_ylabel("O2 (%)")
c2 = c.twinx()
ms_ = (ts >= w0 - np.timedelta64(3, "h")) & (ts <= w1)
c2.step(np.r_[ts[ms_], w1], np.r_[sp[ms_], sp[ms_][-1]], where="post", color="C1", lw=2, alpha=0.6, label="Setpoint_RBV")
mf_ = (tf >= w0) & (tf <= w1)
c2.plot(tf[mf_], fl[mf_], "k^", ms=6, label="Flow_RBV samples")
c2.set_ylabel(f"SLPM {GAS}"); c2.set_ylim(-1, 25)
c.set_xlim(w0, w1)
c.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
h1, l1 = c.get_legend_handles_labels(); h2, l2 = c2.get_legend_handles_labels()
c.legend(h1 + h2, l1 + l2, fontsize=7.5, loc="center left")
c.set_title("(c) example, 24 Sep: the setpoint readback goes 0 -> 0.25, the flow readback shows 20 SLPM in between",
            fontsize=9, loc="left")
dd_ = fig.add_subplot(gs[1, 1]); dd_.axis("off")
oth = [p for p in A["purges"] if not p["logged"]]
closure_t = set(np.datetime64(x["t"]) for x in un)
mid_ep = []
for p in oth:
    t1_ = np.datetime64(p["t1"])
    k_ = np.searchsorted(to, t1_)
    if k_ < len(to) and np.datetime64(str(to[k_])[:16]) in closure_t:
        continue
    mid_ep.append(p)
L = ["SETPOINT_RBV vs FLOW_RBV", "",
     f"Flow samples {A['n_flow']}, disagreeing with the setpoint readback {A['n_disagree']}",
     f"20 SLPM purges in the flow record: {A['n_purges']}; in Setpoint_RBV: {A['n_logged']}", "",
     "UNLOGGED PURGES / FLOW CHANGES NOT AT A CLOSURE", ""]
for p in mid_ep:
    L.append(f" {p['t0'][5:16].replace('T',' ')}  ~{p['F']:.1f} SLPM  ~{p['dur_min']:.1f} min (edges ±90 s)")
L += ["", " 18 Sep 03:22: one 20 SLPM flow sample but O2 flat",
      "   (1.15 -> 1.14 %): the burst lasted well under a minute.",
      "", f" Delivered {GAS} this week (reconstructed): {R['gas_use']['total_L']:.0f} L",
      f"   per Setpoint_RBV alone:                   {R['gas_use']['setpoint_total_L']:.0f} L",
      f"   in 20-SLPM purges:                        {R['gas_use']['purge_L']:.0f} L",
      f"   with the lid definitely open:             {R['gas_use']['while_open_L']:.0f} L",
      f"   in lift intervals (upper bound):          {R['gas_use']['lift_interval_upper_L']:.0f} L"]
dd_.text(0, 1, "\n".join(L), va="top", family="monospace", fontsize=8.3)
title(fig, "Setpoint_RBV misses most purges; Flow_RBV and the O2 agree on what was delivered",
      f"Delivered {GAS} flow = setpoint readback overridden by Flow_RBV where they disagree by > max(0.05 SLPM, 10 %). "
      "Unlogged purge durations are solved from the O2 drop with V fixed by the logged purges, then checked against the flow sampling.")
fig.savefig("figures/fig7_setpoint_vs_flow.png", dpi=130); plt.close(fig)

# =============================================================== fig 8: model replay of the week
kL = R["leakin"]["k_per_min"]
dt_s = 10.0
pred = np.full(len(o2), np.nan); lidof = np.array([""] * len(o2), dtype=object)
curves = []
lifts = []
for r in out:
    if r["lid"] == "air":
        continue
    lid = r["lid"] if r["lid"] in fits else r.get("closest", "normal")
    a_, b_ = r["i0"], r["i1"]
    g = np.arange(to[a_], to[b_] + np.timedelta64(1, "s"), np.timedelta64(int(dt_s * 1000), "ms"))
    Fg_ = F_at(rec, g)
    Cc = o2[a_]; Cs = np.empty(len(g))
    def step(C, Fv):
        if Fv > 0:
            Jv = 10 ** np.polyval(fits[lid], np.log10(min(max(Fv, 0.2), 2))) / 10     # % L/min
            Css = Jv / Fv
            return Css + (C - Css) * np.exp(-Fv * (dt_s / 60) / V)
        return Ca - (Ca - C) * np.exp(-kL * dt_s / 60)

    for i_, Fv in enumerate(Fg_):
        # A purge inside a "closed" episode: was the lid lifted between two 600 s O2 samples
        # just before it? Run both starting points (current C, or air) through the full model to
        # the first O2 sample after the purge starts. Call it a lift only if air fits that sample
        # within x/1.6 and fits it better than no-lift.
        if Fv >= 15 and i_ and Fg_[i_ - 1] < 15 and Cc < 15:     # Cc >= 15: box is already air
            k_ = np.searchsorted(to, g[i_])
            if k_ <= b_:
                jend = np.searchsorted(g, to[k_])
                c_no, c_air = Cc, Ca
                for Fw in Fg_[i_:jend]:
                    c_no, c_air = step(c_no, Fw), step(c_air, Fw)
                d_no, d_air = abs(np.log10(c_no / o2[k_])), abs(np.log10(c_air / o2[k_]))
                if d_air < d_no and d_air < np.log10(1.6):
                    Cc = Ca
                    lifts.append(g[i_])
        Cc = step(Cc, Fv)
        Cs[i_] = Cc
    idx = np.searchsorted(g, to[a_:b_ + 1]); idx = np.clip(idx, 0, len(g) - 1)
    pred[a_:b_ + 1] = Cs[idx]; lidof[a_:b_ + 1] = r["lid"]
    curves.append((g, Cs, r["lid"]))
fig, ax = plt.subplots(2, 1, figsize=(16, 9), sharex=True, gridspec_kw=dict(height_ratios=[1.3, 1]))
fig.subplots_adjust(left=0.06, right=0.98, top=0.85, bottom=0.07, hspace=0.08)
ax[0].semilogy(to, o2, "o", ms=2.5, color="0.35", label="O2 data (600 s)")
for g, Cs, lid in curves:
    ax[0].semilogy(g, Cs, "-", lw=1.1, color=LIDC[lid])
ax[0].set_ylim(0.08, 30); ax[0].set_ylabel("O2 (%)")
for tl in lifts:
    ax[0].axvline(tl, color="r", ls="--", lw=0.8)
    ax[0].text(tl, 22, "unsampled lid lift \n(only an air start fits \nthe O2 after this purge) ", color="r",
               fontsize=7.5, va="top", ha="right")
ax[0].legend(handles=[Line2D([], [], marker="o", color="0.35", ls="", label="O2 data"),
                      Line2D([], [], color=LIDC["normal"], label="replay, normal lid"),
                      Line2D([], [], color=LIDC["collimator"], label="replay, collimator lid"),
                      Line2D([], [], color=LIDC["atypical"], label="replay, atypical (nearest family)")],
             fontsize=8, ncol=4, loc="lower center", bbox_to_anchor=(0.5, 1.0))
rr = np.log10(pred / o2)
fst = np.zeros(len(o2), bool)
for r in out:
    fst[r["i0"]] = True
ok_ = np.isfinite(rr) & ~fst
for lid in ("normal", "collimator", "atypical"):
    m = ok_ & (lidof == lid)
    ax[1].plot(to[m], 10 ** rr[m], "o", ms=3, color=LIDC[lid])
ax[1].set_yscale("log"); ax[1].set_ylim(0.2, 5)
ax[1].axhline(1, color="k", lw=0.6)
for f_ in (1 / 1.5, 1.5):
    ax[1].axhline(f_, color="k", ls=":", lw=0.6)
ax[1].set_ylabel("model / data")
ax[1].xaxis.set_major_locator(mdates.DayLocator()); ax[1].xaxis.set_major_formatter(mdates.DateFormatter("%a %d %b"))
stats = []
for lid in ("normal", "collimator", "atypical"):
    m = ok_ & (lidof == lid)
    if m.any():
        stats.append(f"{lid}: {100*np.mean(np.abs(rr[m]) < np.log10(1.5)):.0f} % of {m.sum()} samples within ×/÷1.5, "
                     f"median |error| {100*(10**np.median(np.abs(rr[m]))-1):.0f} %")
ax[1].text(0.01, 0.04, "\n".join(stats), transform=ax[1].transAxes, fontsize=8.5,
           bbox=dict(fc="w", ec="0.8"))
title(fig, "Replay: the whole week from 5 global numbers (V, two ingress families, flow-off leak rate)",
      f"Each closed episode starts from its first O2 sample, then evolves with the reconstructed {GAS} flow only: V dC/dt = J_lid(F) - F·C, "
      f"or dC/dt = k(Ca - C) with the flow off\n(k from 21 Sep, normal lid; used for every lid for want of a collimator value). "
      "Nothing is refitted per episode. Unlogged purge edges are known to ±90 s, i.e. ±30 L of He, which sets the depth of the modelled\n"
      "collimator dips to within a factor ~2. Before each purge inside an episode, 'lid stayed on' and 'lid was lifted' are both run to the next "
      "O2 sample; a lift is called only if air fits within ×/÷1.6.")
fig.savefig("figures/fig8_model_replay.png", dpi=130); plt.close(fig)
R["replay"] = dict(stats=stats, unsampled_lifts=[str(t)[:19] for t in lifts])
json.dump(R, open("results.json", "w"), indent=1, default=float)
print("wrote fig3-fig8")
