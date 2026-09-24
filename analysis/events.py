"""Segment the record into lid-open intervals and closed-lid episodes, and list the
lid-removal / lid-closure events. Pure bookkeeping; no fitting here.
Flow means the reconstructed delivered helium flow (o2load.reconstruct_flow)."""
import numpy as np
from o2load import load, F_history

OPEN_THRESH = 19.0          # % O2, user-specified: above this the lid is off
T_S = 600.0                 # archiver grid, s


def minutes(a, b):
    return (b - a).astype("timedelta64[ms]").astype(float) / 6e4


def segment(d):
    o2 = d["o2"]
    is_open = o2 >= OPEN_THRESH
    runs = []                               # (kind, i0, i1) inclusive sample indices
    i0 = 0
    for i in range(1, len(o2) + 1):
        if i == len(o2) or is_open[i] != is_open[i0]:
            runs.append(("open" if is_open[i0] else "closed", i0, i - 1))
            i0 = i
    return is_open, runs


def sp_history(d, ta, tb):
    """Setpoint-readback segments [(t_start, t_end, F)] covering [ta, tb] (zero-order hold)."""
    ts, sp = d["ts"], d["sp"]
    k = np.searchsorted(ts, ta, side="right") - 1
    out, t = [], ta
    F = sp[k] if k >= 0 else np.nan
    for j in range(k + 1, len(ts)):
        if ts[j] >= tb:
            break
        out.append((t, ts[j], F)); t, F = ts[j], sp[j]
    out.append((t, tb, F))
    return out


def lid_events(d, runs):
    to, o2 = d["to"], d["o2"]
    et, ev, ex = d["rec"][:3]
    out = []
    for (k0, a0, b0), (k1, a1, b1) in zip(runs[:-1], runs[1:]):
        if k0 == "closed" and k1 == "open":
            i_pre, i_post = b0, a1
            j = np.searchsorted(et, to[i_post], side="right") - 1
            F_post = ev[j]
            t_off = None
            if F_post == 0:
                jj = j
                while jj > 0 and ev[jj - 1] == 0:
                    jj -= 1
                t_off = et[jj]
            out.append(dict(kind="removal", i_pre=i_pre, i_post=i_post, t_pre=to[i_pre], t_post=to[i_post],
                            c_pre=o2[i_pre], c_post=o2[i_post],
                            slope=(o2[i_post] - o2[i_pre]) / (minutes(to[i_pre], to[i_post]) * 60),
                            F_post=F_post, t_off=t_off, closed_len=b0 - a0 + 1))
        if k0 == "open" and k1 == "closed":
            out.append(dict(kind="closure", i_pre=b0, i_post=a1, t_pre=to[b0], t_post=to[a1],
                            c_pre=o2[b0], c_post=o2[a1], open_len=b0 - a0 + 1,
                            f_hist=F_history(d["rec"], to[b0], to[a1]),
                            sp_hist=sp_history(d, to[b0], to[a1])))
    return out


if __name__ == "__main__":
    d = load()
    is_open, runs = segment(d)
    to, o2 = d["to"], d["o2"]
    print(f"{sum(k=='open' for k,_,_ in runs)} open runs, {sum(k=='closed' for k,_,_ in runs)} closed runs")
    for k, a, b in runs:
        print(f"{k:6s} {str(to[a])[:16]} -> {str(to[b])[11:16]}  n={b-a+1:4d}  O2 {o2[a]:6.2f}..{o2[b]:6.2f} "
              f"SP {np.unique(d['sp_at'][a:b+1])}  flow {np.unique(np.round(d['f_at'][a:b+1], 2))}")
    for e in lid_events(d, runs):
        if e["kind"] == "removal":
            off = (f"flow off {minutes(e['t_off'], e['t_post']):5.1f} min before first open sample"
                   if e["t_off"] is not None else f"FLOW STILL ON {e['F_post']}")
            print(f"REMOVAL {str(e['t_post'])[:16]}  {e['c_pre']:6.2f} -> {e['c_post']:6.2f}  "
                  f"{e['slope']*60:6.3f} %/min  {off}")
        else:
            h = ", ".join(f"{str(a)[11:19]}{'' if x else '~'} F={F:g}" for a, b, F, x in e["f_hist"])
            print(f"CLOSURE {str(e['t_pre'])[:16]} {e['c_pre']:6.2f} -> {str(e['t_post'])[11:16]} "
                  f"{e['c_post']:6.2f}  flow: {h}")
