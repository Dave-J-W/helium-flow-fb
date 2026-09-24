"""Figure 1: raw O2, setpoint readback and flow readback on one time axis (import + time-link check)."""
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from o2load import load, GAS, _disagree

d = load()
to, o2, ts, sp, tf, fl = d["to"], d["o2"], d["ts"], d["sp"], d["tf"], d["fl"]
et, ev, ex = d["rec"][:3]
tend = to[-1]
LOW = 0.05                                    # where zero is drawn on the log flow axes


def logz(v):
    return np.where(np.abs(v) < 0.02, LOW, np.abs(v))


fig, ax = plt.subplots(4, 1, figsize=(15, 12), sharex=True,
                       gridspec_kw=dict(height_ratios=[1, 1, 0.8, 0.8]))
fig.subplots_adjust(left=0.07, right=0.98, top=0.93, bottom=0.05, hspace=0.08)

ax[0].plot(to, o2, ".-", ms=3, lw=0.6, color="C0")
ax[0].axhline(19, color="r", ls=":", lw=0.8)
ax[0].text(to[0], 19.3, "19 % = open-lid threshold", color="r", fontsize=8)
ax[0].set_ylabel("O2 (%), linear"); ax[0].set_ylim(-0.5, 21)

ax[1].semilogy(to, o2, ".-", ms=3, lw=0.6, color="C0")
ax[1].set_ylabel("O2 (%), log"); ax[1].set_ylim(0.08, 30)

ax[2].step(np.r_[ts, tend], logz(np.r_[sp, sp[-1]]), where="post", color="C1", lw=1)
ax[2].set_ylabel(f"Setpoint_RBV\n(SLPM {GAS})")
k = np.searchsorted(ts, tf, side="right") - 1
bad = _disagree(fl, np.where(k >= 0, sp[np.clip(k, 0, None)], np.nan))
ax[3].plot(tf, logz(fl), ".", ms=2, color="0.45", label=f"Flow_RBV samples ({len(tf)}, every ~180 s)")
ax[3].plot(tf[bad], logz(fl[bad]), "o", ms=5, mfc="none", mec="r",
           label=f"flow disagrees with setpoint readback ({bad.sum()} samples)")
ax[3].step(np.r_[et, tend], logz(np.r_[ev, ev[-1]]), where="post", color="C2", lw=1,
           label="reconstructed delivered flow")
ax[3].set_ylabel(f"Flow_RBV\n(SLPM {GAS})")
ax[3].legend(fontsize=8, loc="upper right", ncol=3)
for a in ax[2:]:
    a.set_yscale("log"); a.set_ylim(0.03, 40)
    a.axhspan(0.15, 0.35, color="C2", alpha=0.15, lw=0)
    a.axhspan(0.7, 1.2, color="C4", alpha=0.15, lw=0)
    a.set_yticks([LOW, 0.25, 0.8, 2, 5, 20]); a.set_yticklabels(["0 (off)", "0.25", "0.8", "2", "5", "20"])
ax[2].text(ts[0], 0.2, " normal lid band 0.15-0.35", color="C2", fontsize=8, va="center")
ax[2].text(ts[0], 0.92, " collimator lid band 0.7-1.2", color="C4", fontsize=8, va="center")
ax[3].xaxis.set_major_locator(mdates.DayLocator())
ax[3].xaxis.set_minor_locator(mdates.HourLocator(byhour=[6, 12, 18]))
ax[3].xaxis.set_major_formatter(mdates.DateFormatter("%a %d %b"))
for a in ax:
    a.grid(alpha=0.3, which="major"); a.grid(alpha=0.1, which="minor", axis="x")
fig.text(0.07, 0.972, f"Enclosure O2 vs Alicat {GAS} setpoint and flow readbacks, 17-24 Sep 2026", fontsize=12, weight="bold")
fig.text(0.07, 0.950, f"O2 (15IDC:D1Dmm_calc): {len(o2)} samples on a 600 s grid.  Setpoint_RBV: {len(sp)} change-only events.  "
         f"Flow_RBV: {len(tf)} samples from {str(tf[0])[5:16]}.\nNo interpolation; steps are zero-order holds.  "
         "Red circles: 20 SLPM purges and flow changes the setpoint readback never recorded.", fontsize=8.5, va="top")
fig.savefig("figures/fig1_overview.png", dpi=130)
print("wrote fig1_overview.png")
