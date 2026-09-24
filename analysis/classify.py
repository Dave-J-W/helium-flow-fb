"""Lid identification per closed-lid episode.

A lid cannot change without the enclosure going to air, so the unit of classification is the
closed episode between two open intervals. Two independent pieces of evidence:

  A. hold-flow band the operator chose: 0.15-0.35 SLPM normal lid, 0.7-1.2 collimator lid
  B. physics: O2 ingress J = F*C + V*dC/dt (mL O2/min) at the flows actually used.
     Each lid has its own J(F) family. Fitted as a power law in log-log space, with labels
     iterated to self-consistency starting from the setpoint band of each INTERVAL.

B decides when the episode has usable intervals; A is the fallback. Disagreements are listed.
The band in A is taken from the reconstructed DELIVERED helium flow (o2load), which equals the
setpoint readback except where Flow_RBV shows the readback missed a change.
"""
import json
import numpy as np
from o2load import load, F_at
from events import segment

NORMAL_BAND = (0.15, 0.35)
COLLIM_BAND = (0.7, 1.2)
V_L = json.load(open("results.json"))["purge"]["V_L"]   # J_rows was built with this value
FIT_OK = 1.5                # |mean residual| (in family sigma) above which neither family fits
SEPARATION = 2.0            # required gap between |z_other| and |z_best| for a "strong" call
F_MAX_DISCRIM = 2.0         # SLPM; above this the two families converge (and purge transients)


def band(F):
    if NORMAL_BAND[0] <= F <= NORMAL_BAND[1]:
        return "normal"
    if COLLIM_BAND[0] <= F <= COLLIM_BAND[1]:
        return "collimator"
    return None


def classify():
    d = load()
    is_open, runs = segment(d)
    to, o2, sp_at = d["to"], d["o2"], d["sp_at"]
    eps = [(a, b) for k, a, b in runs if k == "closed"]
    J = np.load("J_rows.npy")                      # ep, F, C, dCdt, J(%*L/min)
    # flow-off rows are a different (unpressurised) regime; high-flow rows cannot discriminate
    ok = (J[:, 4] > 0) & (J[:, 1] > 0) & (J[:, 1] <= F_MAX_DISCRIM)
    ep, F, Jm = J[ok, 0].astype(int), J[ok, 1], 10 * J[ok, 4]
    x, y = np.log10(F), np.log10(Jm)

    # iterate: interval labels -> two log-log lines -> episode labels -> ...
    lab = np.array([band(f) for f in F], dtype=object)
    for _ in range(20):
        fits = {}
        for L in ("normal", "collimator"):
            m = lab == L
            fits[L] = np.polyfit(x[m], y[m], 1)
        r = {L: y - np.polyval(fits[L], x) for L in fits}
        sig = {L: np.std(r[L][lab == L], ddof=2) for L in fits}
        new = lab.copy()
        for e in np.unique(ep):
            m = ep == e
            cost = {L: np.mean((r[L][m] / sig[L]) ** 2) + 2 * np.log(sig[L]) for L in fits}
            new[m] = min(cost, key=cost.get)
        if np.all(new == lab):
            break
        lab = new

    out = []
    for n, (a, b) in enumerate(eps):
        # time-weighted hold-flow band over the episode (purges excluded by band())
        grid = np.arange(to[a], to[b] + np.timedelta64(1, "s"), np.timedelta64(30, "s"))
        gb = [band(f) for f in F_at(d["rec"], grid)]
        nN, nC = gb.count("normal"), gb.count("collimator")
        if nN + nC == 0:
            spb = "none"
        else:
            spb = "normal" if nN >= nC else "collimator"
            frac = max(nN, nC) / (nN + nC)
            if frac < 0.999:
                spb += f" ({100*frac:.0f}% of in-band time)"
        rec = dict(n=n, i0=a, i1=b, t0=to[a], t1=to[b], nsamp=b - a + 1, sp_band=spb)
        m = ep == n
        if b - a == 0 and o2[a] > 15:
            rec.update(lid="air", basis="single sample at %.2f %% with flow off: ambient reading "
                       "drifted under the 19 %% threshold, not a sealed lid" % o2[a])
        elif m.any():
            zn = np.mean(r["normal"][m]) / sig["normal"]
            zc = np.mean(r["collimator"][m]) / sig["collimator"]
            best, zb, zo = ("normal", zn, zc) if abs(zn) < abs(zc) else ("collimator", zc, zn)
            if abs(zb) > FIT_OK:
                lid, basis = "atypical", f"ingress fits neither family; closer to {best}"
            elif abs(zo) - abs(zb) >= SEPARATION:
                lid, basis = best, "ingress, strong"
            else:
                lid, basis = best, "ingress, weak"
            rec.update(lid=lid, closest=best, zn=zn, zc=zc, nJ=int(m.sum()),
                       Jmed=float(np.median(Jm[m])), Fs=[float(f) for f in sorted(set(np.round(F[m], 2)))],
                       basis=basis)
        else:
            rec.update(lid=(spb.split()[0] if spb != "none" else "unresolved"),
                       basis="setpoint band only (no usable ingress interval)")
        rec["disagree"] = (rec["lid"] in ("normal", "collimator") and
                           rec["sp_band"].split()[0] not in (rec["lid"], "none"))
        out.append(rec)
    return out, fits, sig, (ep, F, Jm, lab)


if __name__ == "__main__":
    out, fits, sig, _ = classify()
    for L in fits:
        print(f"{L:10s} J = {10**fits[L][1]:.2f} * F^{fits[L][0]:+.2f} mL O2/min   scatter x/{10**sig[L]:.2f}")
    for r in out:
        extra = f"J~{r['Jmed']:5.1f} mL/min at F={r['Fs']} zN={r['zn']:+5.1f} zC={r['zc']:+5.1f}" if "Jmed" in r else ""
        flag = "  <-- SETPOINT BAND DISAGREES" if r["disagree"] else ""
        print(f"{str(r['t0'])[:16]}-{str(r['t1'])[11:16]} n={r['nsamp']:3d} hold-flow band {r['sp_band']:18s} -> "
              f"{r['lid']:10s} [{r['basis']}] {extra}{flag}")
