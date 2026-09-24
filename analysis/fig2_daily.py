"""Figure 2: one panel per day, O2 (log, left) with setpoint readback and delivered flow (right)."""
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from o2load import load, GAS

d = load()
to, o2, ts, sp, tf, fl = d["to"], d["o2"], d["ts"], d["sp"], d["tf"], d["fl"]
et, ev = d["rec"][:2]
days = np.arange(np.datetime64("2026-09-17"), np.datetime64("2026-09-25"))
fig, axs = plt.subplots(len(days), 1, figsize=(15, 3 * len(days)))
fig.subplots_adjust(left=0.06, right=0.93, top=0.965, bottom=0.03, hspace=0.38)


def lz(v):
    return np.where(np.abs(v) < 0.02, 0.05, np.abs(v))


for ax, d0 in zip(axs, days):
    t0, t1 = d0.astype("datetime64[ms]"), (d0 + np.timedelta64(1, "D")).astype("datetime64[ms]")
    m = (to >= t0 - np.timedelta64(1, "h")) & (to <= t1 + np.timedelta64(1, "h"))
    ax.semilogy(to[m], o2[m], ".-", ms=4, lw=0.7, color="C0")
    ax.set_ylim(0.08, 30); ax.axhline(19, color="r", ls=":", lw=0.7)
    ax.set_ylabel("O2 (%)", color="C0")
    ax2 = ax.twinx()
    ax2.step(np.r_[ts, to[-1]], lz(np.r_[sp, sp[-1]]), where="post", color="C1", lw=1.6, alpha=0.6,
             label="Setpoint_RBV")
    ax2.step(np.r_[et, to[-1]], lz(np.r_[ev, ev[-1]]), where="post", color="C2", lw=0.9,
             label="delivered flow (reconstructed)")
    mf = (tf >= t0) & (tf <= t1)
    ax2.plot(tf[mf], lz(fl[mf]), ".", ms=2, color="k", label="Flow_RBV samples")
    ax2.set_yscale("log"); ax2.set_ylim(0.03, 40)
    ax2.set_yticks([0.05, 0.25, 0.8, 2, 5, 20]); ax2.set_yticklabels(["off", "0.25", "0.8", "2", "5", "20"])
    ax2.set_ylabel(f"SLPM {GAS}")
    ax2.axhspan(0.15, 0.35, color="C2", alpha=0.10, lw=0); ax2.axhspan(0.7, 1.2, color="C4", alpha=0.10, lw=0)
    ax.set_xlim(t0, t1)
    ax.xaxis.set_major_locator(mdates.HourLocator(interval=2))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.set_title(str(d0), fontsize=9, loc="left"); ax.grid(alpha=0.3)
axs[0].get_shared_x_axes()
h, l = ax2.get_legend_handles_labels()
fig.legend(h, l, loc="upper right", ncol=3, fontsize=8, bbox_to_anchor=(0.93, 0.995))
fig.savefig("figures/fig2_daily.png", dpi=110)
print("wrote fig2_daily.png")
