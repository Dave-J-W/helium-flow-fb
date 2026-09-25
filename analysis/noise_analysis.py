"""O2 reading noise at 1 Hz: white sensor noise plus a slow real wander (normal lid, fixed flow).

    python noise_analysis.py      ->  noise_results.json, figures/fig14_noise.png

Record: 15IDC:D1Dmm_calc at 1 Hz, 25 Sep 2026 14:31-15:01 (30 min), normal lid, Alicat setpoint
0.25 SLPM with Flow_RBV reading 0.24-0.26. O2 rose 0.905 -> 0.932 % over the record, so a cubic
trend is removed before the noise statistics. The user judges the slow wander to be real O2 (leak /
temperature), not analyzer drift; this record alone cannot separate the two.

Model fitted to the Allan deviation of the detrended record (tau 1-300 s):
    reading = trend + white(sigma_w) + OU(sigma_o, tau_c)
The same model with the fitted numbers drives the controller simulator (wanderRel, wanderTau, noise).
"""
import json
import os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from o2load import _load, O2_1HZ, DATA_DIR

HERE = os.path.dirname(os.path.abspath(__file__))
TAUS = np.array([1, 2, 3, 5, 7, 10, 15, 20, 30, 45, 60, 90, 120, 180, 240, 300])
FIT_MAX_TAU = 300                  # s; longer taus are shaped by the cubic detrend of a 30-min record
ACF_LAGS = np.arange(0, 181)


def adev(y, m):
    """Overlapping Allan deviation for averaging length m samples."""
    c = np.cumsum(np.insert(y, 0, 0.0))
    a = (c[m:] - c[:-m]) / m
    return float(np.sqrt(0.5 * np.mean((a[m:] - a[:-m]) ** 2)))


def adev_model(tau, sw, so, tc):
    """Allan deviation of white (sw per sample, 1 s) + Ornstein-Uhlenbeck (variance so^2, time constant tc)."""
    x = tau / tc
    ou = so ** 2 * (1 / x) ** 2 * (4 * np.exp(-x) - np.exp(-2 * x) + 2 * x - 3)
    return np.sqrt(sw ** 2 / tau + ou)


def acf(y, lags):
    y = y - y.mean(); v = np.dot(y, y)
    return np.array([np.dot(y[:len(y) - k], y[k:]) / v for k in lags])


# ---------------------------------------------------------------- load and detrend
t, v = _load(O2_1HZ)
ts = (t - t[0]).astype("timedelta64[ms]").astype(float) / 1000.0
keep = np.concatenate([[True], np.diff(ts) > 0.5])      # the export repeats its last sample
ts, v = ts[keep], v[keep]
trend = np.polyval(np.polyfit(ts, v, 3), ts)
res = v - trend
slope = np.polyfit(ts, v, 1)[0] * 60 * 1000             # m%/min

white = float(np.std(np.diff(v)) / np.sqrt(2))          # %, first-difference estimate (trend-free)
ad_det = np.array([adev(res, m) for m in TAUS])
ad_raw = np.array([adev(v, m) for m in TAUS])
ac_data = acf(res, ACF_LAGS)

# ---------------------------------------------------------------- fit OU wander (white fixed from first differences)
fit = TAUS <= FIT_MAX_TAU
best = None
for so in np.linspace(0.1e-3, 1.5e-3, 141):
    for tc in np.geomspace(10, 1000, 121):
        m = adev_model(TAUS[fit], white, so, tc)
        err = float(np.mean(np.log(m / ad_det[fit]) ** 2))
        if best is None or err < best[0]:
            best = (err, so, tc)
_, sigma_o, tau_c = best
ad_fit = adev_model(TAUS, white, sigma_o, tau_c)
rms_log = float(np.sqrt(best[0]))
# white-only reference, and how much the OU term improves the description
err_white = float(np.sqrt(np.mean(np.log(adev_model(TAUS[fit], white, 1e-9, 100) / ad_det[fit]) ** 2)))
ac_model = (sigma_o ** 2 * np.exp(-ACF_LAGS / tau_c)) / (sigma_o ** 2 + white ** 2)
ac_model[0] = 1.0

# ---------------------------------------------------------------- 10 s archive check (steady 30-min holds near 1 %)
arch = []
for name in ("oxygen level rbv sep high res 8 hours.csv", "oxygen level rbv Sep high res 4 hours.csv"):
    ta, va = _load(os.path.join(DATA_DIR, name))
    sa = (ta - ta[0]).astype("timedelta64[ms]").astype(float) / 1000.0
    W = 180                                              # 30 min of 10 s samples
    for i in range(0, len(va) - W, W // 2):
        seg, tt = va[i:i + W], sa[i:i + W]
        if seg.max() > 3 or seg.min() < 0.3:
            continue
        r = seg - np.polyval(np.polyfit(tt - tt[0], seg, 2), tt - tt[0])
        if np.abs(np.diff(seg)).max() > 0.05:            # skip windows containing a step (purge edge)
            continue
        arch.append(float(np.std(np.diff(seg)) / np.sqrt(2)))
arch_med = float(np.median(arch)) if arch else None
pred_10s = float(np.sqrt(white ** 2 + sigma_o ** 2 * (1 - np.exp(-10 / tau_c))))   # per-sample diff std/sqrt2 at 10 s spacing

out = {
    "record": {"file": os.path.basename(O2_1HZ), "samples": int(len(v)), "span_min": round(float(ts[-1]) / 60, 1),
               "level_start_pct": round(float(v[0]), 4), "level_end_pct": round(float(v[-1]), 4),
               "trend_m%_per_min": round(float(slope), 3), "flow": "setpoint 0.25 SLPM, Flow_RBV 0.24-0.26", "lid": "normal"},
    "white_noise_m%_per_1Hz_sample": round(white * 1000, 3),
    "residual_std_m%_after_cubic_detrend": round(float(res.std()) * 1000, 3),
    "allan_dev_m%": {int(k): round(float(a) * 1000, 3) for k, a in zip(TAUS, ad_det)},
    "allan_dev_raw_m%": {int(k): round(float(a) * 1000, 3) for k, a in zip(TAUS, ad_raw)},
    "acf": {int(k): round(float(ac_data[k]), 3) for k in (1, 2, 5, 10, 20, 30, 60, 120)},
    "model": {"white_m%": round(white * 1000, 3), "wander_sigma_m%": round(float(sigma_o) * 1000, 3),
              "wander_tau_s": round(float(tau_c), 1), "wander_rel_at_record_level": round(float(sigma_o / v.mean()), 6),
              "fit_rms_log_error": round(rms_log, 3), "white_only_rms_log_error": round(err_white, 3), "fit_taus_s": f"1-{FIT_MAX_TAU}"},
    "archive_10s_check": {"windows": len(arch), "median_diff_std_m%": round(arch_med * 1000, 3) if arch_med else None,
                          "model_prediction_m%": round(pred_10s * 1000, 3)},
}
with open(os.path.join(HERE, "noise_results.json"), "w") as f:
    json.dump(out, f, indent=1)

# ---------------------------------------------------------------- figure
fig = plt.figure(figsize=(13, 9.2))
gs = fig.add_gridspec(3, 2, height_ratios=[1, 1, 0.66], hspace=0.42, wspace=0.22, left=0.07, right=0.98, top=0.895, bottom=0.03)
fig.text(0.07, 0.975, "O2 reading noise at 1 Hz: white sensor noise + slow real wander", fontsize=13, weight="bold", va="top")
fig.text(0.07, 0.952, "15IDC:D1Dmm_calc, 25 Sep 2026 14:31-15:01 (30 min), normal lid, Alicat setpoint 0.25 SLPM (Flow_RBV 0.24-0.26)",
         fontsize=9.5, va="top")

ax = fig.add_subplot(gs[0, 0])
ax.plot(ts / 60, v, lw=0.6, color="0.2", label="reading, 1 Hz")
ax.plot(ts / 60, trend, lw=1.4, color="tab:red", label=f"cubic trend (mean slope {slope:+.2f} m%/min)")
ax.set_xlabel("time in record (min)"); ax.set_ylabel("O2 (%)"); ax.set_title("(a) raw record and removed trend", loc="left", fontsize=10)
ax.legend(fontsize=8, loc="upper left")

ax = fig.add_subplot(gs[0, 1])
ax.plot(ts / 60, res * 1000, lw=0.5, color="0.35", label="residual, 1 Hz")
rm = np.convolve(res, np.ones(20) / 20, mode="same") * 1000
ax.plot(ts / 60, rm, lw=1.3, color="tab:blue", label="20-sample running mean")
ax.axhline(0, color="k", lw=0.5)
ax.set_xlabel("time in record (min)"); ax.set_ylabel("residual (m% O2 = 0.001 % O2)")
ax.set_title("(b) residual after trend removal: fast scatter + slow wander", loc="left", fontsize=10)
ax.legend(fontsize=8, loc="upper right")

ax = fig.add_subplot(gs[1, 0])
ax.loglog(TAUS, ad_det * 1000, "o", color="0.15", label="data, detrended")
ax.loglog(TAUS, ad_raw * 1000, "x", color="0.6", label="data, raw (trend dominates beyond 60 s)")
ax.loglog(TAUS, white / np.sqrt(TAUS) * 1000, "--", color="tab:gray", label=f"white only ({white*1000:.2f} m%/sqrt(tau)): rms log err {err_white:.2f}")
ax.loglog(TAUS, ad_fit * 1000, "-", color="tab:red",
          label=f"white + wander: sigma {sigma_o*1000:.2f} m%, tau {tau_c:.0f} s: rms log err {rms_log:.2f}")
ax.axvspan(FIT_MAX_TAU, TAUS[-1] * 1.3, color="0.92")
ax.set_xlim(0.8, TAUS[-1] * 1.3)
ax.set_xlabel("averaging time tau (s)"); ax.set_ylabel("Allan deviation (m% O2)")
ax.set_title("(c) averaging stops helping beyond ~10 s: the wander floor", loc="left", fontsize=10)
ax.legend(fontsize=7.5, loc="lower left")

ax = fig.add_subplot(gs[1, 1])
ax.plot(ACF_LAGS[1:], ac_data[1:], color="0.2", lw=1, label="data, detrended residual")
ax.plot(ACF_LAGS[1:], ac_model[1:], color="tab:red", lw=1.4, label="model (white + wander)")
ax.axhline(0, color="k", lw=0.5)
ax.set_xlabel("lag (s)"); ax.set_ylabel("autocorrelation"); ax.set_ylim(-0.2, 0.6)
ax.set_title("(d) correlation (check only; model fitted in c)", loc="left", fontsize=10)
ax.text(0.98, 0.04, "detrending a 30-min record removes slow power,\nso the data ACF is biased low at long lags", transform=ax.transAxes,
        ha="right", va="bottom", fontsize=7.5, color="0.35")
ax.legend(fontsize=8, loc="upper right")

ax = fig.add_subplot(gs[2, :]); ax.axis("off")
lines = [
    "NOISE      white (sensor), per 1 Hz sample:  %.2f m%% O2 = %.5f %% O2   (first-difference estimate)" % (white * 1000, white),
    "WANDER     slow component (believed real O2):  sigma %.2f m%% O2 (%.3f %% of level), correlation time %.0f s   (fit to Allan dev., tau 1-%d s)" % (sigma_o * 1000, 100 * sigma_o / v.mean(), tau_c, FIT_MAX_TAU),
    "AVERAGING  20-sample mean of the residual: %.2f m%% O2;   Allan deviation floor 10-300 s: %.2f-%.2f m%% O2" % (float(rm.std()), ad_det[(TAUS >= 10) & (TAUS <= 300)].min() * 1000, ad_det[(TAUS >= 10) & (TAUS <= 300)].max() * 1000),
    "ARCHIVE    10 s archive, steady holds near 1 %%: per-sample scatter %s m%% O2 (%d windows);  model predicts %.2f m%% O2" % (f"{arch_med*1000:.2f}" if arch_med else "n/a", len(arch), pred_10s * 1000),
    "LIMITS     one 30-min record, one lid (normal), one level (~0.92 %), fixed flow. The wander is assumed real (user); this record",
    "           cannot separate it from analyzer drift. A +2 m% step at 16.5 min (cause unknown) is included in the fit.",
    "           Taus > %d s (shaded) are shaped by the cubic detrend and are not fitted." % FIT_MAX_TAU,
    "NOT SHOWN  collimator lid and other O2 levels: no 1 Hz data yet.",
]
for i, s in enumerate(lines):
    ax.text(0.0, 1 - i / len(lines), s, family="monospace", fontsize=8.6, va="top", transform=ax.transAxes)

os.makedirs(os.path.join(HERE, "figures"), exist_ok=True)
fig.savefig(os.path.join(HERE, "figures", "fig14_noise.png"), dpi=130)

ASCII = {"σ": "sigma", "→": "->", "−": "-"}
msg = (f"white {white*1000:.3f} m%, wander sigma {sigma_o*1000:.3f} m% tau {tau_c:.0f} s "
       f"(rms log err {rms_log:.3f} vs white-only {err_white:.3f}); archive {arch_med*1000 if arch_med else float('nan'):.3f} vs model {pred_10s*1000:.3f} m%")
print("".join(ASCII.get(c, c) for c in msg))
