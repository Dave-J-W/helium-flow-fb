"""Offline stress test of the controller core against missed O2 updates.

The user, 2026-10-01: "building our system to handle missed updates on a ~1Hz schedule is good for
robustness". Each golden trace (ioc/test/golden/sc*.trace, the inputs the reference fed, tick by
tick) is replayed through the C core (sgReplay in its stress mode) once clean and many times with
the O2 input perturbed: a dropped tick repeats the previous tick's O2 value and severity, as a
missed CA monitor update does in the IOC glue. Every decision of a perturbed run is compared with
the CLEAN run of the same code (not with the trace's recorded output) and classified:

  TIMING       the same decisions, some a few seconds earlier or later (harmless)
  FALSE_STOP   an OPEN_STOP the clean run does not make (violates "no false stops of the helium")
  FALSE_MAJOR  a MAJOR alarm (or an OPEN_LOOP entry) the clean run does not raise
  FALSE_MINOR  a MINOR alarm the clean run does not raise
  FAIL_TO_ACT  an OPEN_STOP, OPEN_LOOP or MAJOR alarm of the clean run that does not happen
  MISSED_MINOR a MINOR alarm of the clean run that does not happen
  OTHER        any other change in the decisions (a transition, a settling or lid-check log line)

The replay is open loop: the plant inputs after a changed decision are the reference's, which
reacted to the clean decisions. A run's comparison is exact up to its first changed decision; the
script reports that first change, and later changes only as counts.

usage: python stress_o2.py [--replay PATH] [--golden DIR] [--seeds N] [--quick] [--json OUT]
                           [--only FAMILY,...]
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures as cf
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEF_REPLAY = os.path.join(HERE, "..", "lssSampleGas", "lssSampleGasApp", "src",
                          "O.windows-x64-mingw", "sgReplay.exe")
DEF_GOLDEN = os.path.join(HERE, "golden")

NUM = re.compile(r"-?\d+(\.\d+)?")


def norm(text: str) -> str:
    """A decision's identity without its numbers (an O2 value in a reason text is not a decision)."""
    return NUM.sub("#", text)


def run_replay(replay: str, traces: list[str], args: list[str]) -> dict:
    """Run sgReplay in stress mode; return {trace: {"events": [...], "final", "heL", "drops", "ticks", "selftest"}}."""
    out = subprocess.run([replay, "--events", *args, *traces], capture_output=True, text=True,
                         encoding="utf-8", check=True).stdout
    res: dict = collections.defaultdict(lambda: {"events": []})
    for line in out.splitlines():
        f = line.split("\t")
        if f[0] == "E" and len(f) >= 7:
            res[f[1]]["events"].append({"t": float(f[2]), "kind": f[3], "name": f[4],
                                        "sev": int(f[5]), "text": f[6]})
        elif f[0] == "S" and len(f) >= 7:
            res[f[1]].update(final=f[2], heL=float(f[3]), drops=int(f[4]), ticks=int(f[5]),
                             selftest=f[6])
    for r in res.values():           # a raised alarm also logs its text: keep the ALARM only
        alarms = {(e["t"], e["text"]) for e in r["events"] if e["kind"] == "ALARM"}
        r["events"] = [e for e in r["events"]
                       if not (e["kind"] == "LOG" and (e["t"], e["text"]) in alarms)]
    return dict(res)


# The OpenStop and OpenLoop alarms are raised with the transitions into OPEN_STOP and OPEN_LOOP,
# which are compared themselves; Override latches follow the PRECHECK/HANDOFF ramp corrections.
REDUNDANT = {"OpenStop", "OpenLoop"}


def key_of(ev: dict) -> str | None:
    k = ev["kind"]
    if k == "STATE":
        return f"STATE {ev['name']}"
    if k == "ALARM":
        return None if ev["name"] in REDUNDANT else f"ALARM {ev['name']} sev{ev['sev']}"
    if k == "LOG":
        return f"LOG sev{ev['sev']} {norm(ev['text'])}"
    return None          # CLEAR: follows from the raises and the transitions


def rule_of(key: str) -> str:
    """The rule (spec section) a decision belongs to."""
    rules = [
        ("no O2 decay within", "lid check 5.2 no onset (§8.9)"),
        ("purge decay", "lid check 5.3 decay/curvature (§8.9)"),
        ("lid check", "lid check (§8.9)"),
        ("enclosure opened", "lid-lift detector (§8.6)"),
        ("lag-corrected", "handoff count / lag-corrected estimate (§8.10)"),
        ("purge timeout", "purge timeout (§8.10)"),
        ("purge incomplete", "purge timeout (§8.10)"),
        ("PurgeIncomplete", "purge timeout (§8.10)"),
        ("blind purge complete", "blind purge (§8.9)"),
        ("O2 lost during purge", "invalid/frozen O2 (§8.2)"),
        ("O2 reading frozen", "frozen O2 (§8.2)"),
        ("O2 reading invalid", "invalid O2 (§8.2)"),
        ("O2 unavailable", "invalid/frozen O2 (§8.2)"),
        ("O2Bad", "invalid/frozen O2 (§8.2)"),
        ("OpenLoop", "invalid/frozen O2 (§8.2)"),
        ("OpenStop", "OpenStop alarm (§8.6/§8.9)"),
        ("restart", "restart rule (§8.15)"),
        ("HANDOFF>REGULATE", "handoff settle (§8.4)"),
        ("settled", "settling (§8.12)"),
        ("settling stalled", "stall (§8.12)"),
        ("NotReached", "settleTimeout (§8.12)"),
        ("O2High", "O2-high alarm (§8.14)"),
        ("FlowHigh", "flow alarms via flowSteady/o2Slope (§8.14)"),
        ("FlowLow", "flow alarms via flowSteady/o2Slope (§8.14)"),
        ("Pinned", "pinned (§8.14)"),
        ("note: PID at minimum", "minimum-flow note (§8.14)"),
        ("Override", "override latch (§8.14)"),
    ]
    for pat, rule in rules:
        if pat in key:
            return rule
    return "other"


def classify(key: str, n_clean: int, n_pert: int) -> str:
    extra = n_pert > n_clean
    if "STATE" in key and ">OPEN_STOP" in key:
        return "FALSE_STOP" if extra else "FAIL_TO_ACT"
    if "STATE" in key and ">OPEN_LOOP" in key:
        return "FALSE_MAJOR" if extra else "FAIL_TO_ACT"
    if key.startswith("ALARM"):
        if "Override" in key:
            return "OTHER"
        if "sev2" in key:
            return "FALSE_MAJOR" if extra else "FAIL_TO_ACT"
        return "FALSE_MINOR" if extra else "MISSED_MINOR"
    return "OTHER"


def compare(clean: dict, pert: dict) -> dict:
    """Per trace: the changed decisions of pert against clean."""
    groups_c: dict = collections.defaultdict(list)
    groups_p: dict = collections.defaultdict(list)
    why_c: dict = collections.defaultdict(list)
    why_p: dict = collections.defaultdict(list)
    seq_c, seq_p = [], []
    for ev in clean["events"]:
        k = key_of(ev)
        if k:
            groups_c[k].append(ev["t"]); seq_c.append((k, ev["t"]))
            if ev["kind"] == "STATE":
                why_c[k].append(norm(ev["text"]))
    for ev in pert["events"]:
        k = key_of(ev)
        if k:
            groups_p[k].append(ev["t"]); seq_p.append((k, ev["t"]))
            if ev["kind"] == "STATE":
                why_p[k].append(norm(ev["text"]))
    changes = []
    for k in sorted(set(groups_c) | set(groups_p)):
        tc, tp = groups_c.get(k, []), groups_p.get(k, [])
        reasons = " | ".join(sorted(set(why_c.get(k, []) + why_p.get(k, []))))
        rule = rule_of(k + " " + reasons)
        if len(tc) == len(tp):
            d = [b - a for a, b in zip(tc, tp)]
            if why_c.get(k, []) != why_p.get(k, []):
                changes.append({"key": k + " [reason changed]", "rule": rule, "cls": "OTHER",
                                "clean": why_c[k][:3], "pert": why_p[k][:3],
                                "tClean": tc[:6], "tPert": tp[:6]})
            elif any(x != 0 for x in d):
                changes.append({"key": k, "rule": rule, "cls": "TIMING",
                                "maxShift": max(d, key=abs), "n": len(d)})
        else:
            changes.append({"key": k + (f" ({reasons})" if reasons else ""), "rule": rule,
                            "cls": classify(k, len(tc), len(tp)),
                            "clean": len(tc), "pert": len(tp),
                            "tClean": tc[:6], "tPert": tp[:6]})
    first = None                      # the first changed decision in time order
    for i in range(max(len(seq_c), len(seq_p))):
        a = seq_c[i] if i < len(seq_c) else None
        b = seq_p[i] if i < len(seq_p) else None
        if a is None or b is None or a[0] != b[0]:
            first = {"i": i, "clean": a, "pert": b}
            break
    return {"changes": changes, "first": first,
            "finalClean": clean.get("final"), "finalPert": pert.get("final"),
            "heClean": clean.get("heL"), "hePert": pert.get("heL"),
            "drops": pert.get("drops"), "ticks": pert.get("ticks"),
            "selftest": pert.get("selftest")}


def families(seeds: int, quick: bool) -> list[tuple[str, list[str]]]:
    """(name, sgReplay args) of every perturbed configuration."""
    out = []
    sd = range(1, (2 if quick else seeds) + 1)
    for frac in (0.05, 0.15, 0.30):
        for burst in (1, 3, 5):
            for s in sd:
                out.append((f"drop{int(frac * 100)}_b{burst}_s{s}",
                            ["--o2-drop", str(frac), "--burst", str(burst), "--seed", str(s)]))
    # a held stretch shorter than frozenTime (30 s), placed at the purge onset and in regulation
    for k in (5, 10, 20, 29):
        for off in (0, 5, 10, 15, 20, 25):
            out.append((f"holdPURGE_{off}+{k}", ["--hold-on", f"PURGE:{off}:{k}"]))
        for off in (0, 60, 300):
            out.append((f"holdREGULATE_{off}+{k}", ["--hold-on", f"REGULATE:{off}:{k}"]))
    # a slow O2 PV (a new value every N s), alone and with missed updates on top
    for period in (2, 5, 10):
        for frac in (0.0, 0.14):
            for s in sd:
                out.append((f"period{period}_drop{int(frac * 100)}_s{s}",
                            ["--o2-period", str(period), "--o2-drop", str(frac), "--seed", str(s)]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--replay", default=DEF_REPLAY)
    ap.add_argument("--golden", default=DEF_GOLDEN)
    ap.add_argument("--seeds", type=int, default=8)
    ap.add_argument("--quick", action="store_true", help="2 seeds per configuration")
    ap.add_argument("--json", help="write every comparison here")
    ap.add_argument("--only", help="comma-separated name prefixes of the families to run")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    a = ap.parse_args()
    traces = sorted(os.path.join(a.golden, f) for f in os.listdir(a.golden)
                    if re.fullmatch(r"sc\d\d\.trace", f))
    if not traces:
        print(f"no traces in {a.golden}", file=sys.stderr)
        return 2
    clean = run_replay(a.replay, traces, [])
    bad = [t for t, r in clean.items() if r.get("selftest") != "pass"]
    print(f"clean: {len(clean)} traces, selftest failures: {bad or 'none'}")
    fams = families(a.seeds, a.quick)
    if a.only:
        pre = a.only.split(",")
        fams = [f for f in fams if any(f[0].startswith(p) for p in pre)]
    results = {}
    with cf.ThreadPoolExecutor(a.jobs) as ex:
        futs = {ex.submit(run_replay, a.replay, traces, args): name for name, args in fams}
        for fu in cf.as_completed(futs):
            name = futs[fu]
            pert = fu.result()
            results[name] = {t: compare(clean[t], pert[t]) for t in clean}
    # ---- per family group (name without the seed): counts of changed decisions per class
    group = lambda n: re.sub(r"_s\d+$", "", n)
    classes = ["TIMING", "FALSE_STOP", "FALSE_MAJOR", "FALSE_MINOR", "FAIL_TO_ACT",
               "MISSED_MINOR", "OTHER"]
    by_group: dict = collections.OrderedDict()
    for name, _ in fams:
        g = group(name)
        e = by_group.setdefault(g, {"runs": 0, "cls": collections.Counter(), "rules": collections.Counter(),
                                    "traces": collections.Counter(),
                                    "maxShift": 0, "heDev": 0.0, "heDL": 0.0, "finalDiff": 0,
                                    "selftestFail": 0, "examples": []})
        e["runs"] += 1
        for t, c in results[name].items():
            if c["finalClean"] != c["finalPert"]:
                e["finalDiff"] += 1
            if c["selftest"] != "pass":
                e["selftestFail"] += 1
            if c["heClean"]:
                e["heDev"] = max(e["heDev"], abs(c["hePert"] / c["heClean"] - 1))
                e["heDL"] = max(e["heDL"], abs(c["hePert"] - c["heClean"]))
            for ch in c["changes"]:
                e["cls"][ch["cls"]] += 1
                e["rules"][(ch["rule"], ch["cls"])] += 1
                e["traces"][(t, ch["cls"])] += 1
                if ch["cls"] == "TIMING":
                    e["maxShift"] = max(e["maxShift"], abs(ch["maxShift"]))
                elif len(e["examples"]) < 8:
                    e["examples"].append((name, t, ch["cls"], ch["key"], ch.get("tClean"), ch.get("tPert")))
    print("\nchanged decisions per configuration (summed over seeds and the 19 traces)")
    hdr = f"{'configuration':24s} {'runs':>4s} " + " ".join(f"{c[:11]:>11s}" for c in classes) + \
        f" {'maxShift_s':>10s} {'He_dev_%':>8s} {'He_dev_L':>8s} {'final!=':>7s} {'selftestX':>9s}"
    print(hdr)
    for g, e in by_group.items():
        print(f"{g:24s} {e['runs']:4d} " + " ".join(f"{e['cls'][c]:11d}" for c in classes) +
              f" {e['maxShift']:10.0f} {100 * e['heDev']:8.3f} {e['heDL']:8.2f} {e['finalDiff']:7d}"
              f" {e['selftestFail']:9d}")
    fam_of = lambda g: "1 Hz drops and bursts" if g.startswith("drop") else \
        "held stretches < frozenTime" if g.startswith("hold") else "slow O2 PV (+ drops)"
    for fam in ("1 Hz drops and bursts", "held stretches < frozenTime", "slow O2 PV (+ drops)"):
        gs = [e for g, e in by_group.items() if fam_of(g) == fam]
        if not gs:
            continue
        print(f"\n[{fam}] per rule: class -> count of changed decisions")
        tot: dict = collections.defaultdict(collections.Counter)
        for e in gs:
            for (rule, cls), n in e["rules"].items():
                tot[rule][cls] += n
        for rule in sorted(tot):
            print(f"  {rule:48s} " + ", ".join(f"{c} {n}" for c, n in tot[rule].most_common()))
        print(f"[{fam}] per trace: class -> count of changed decisions")
        tr: dict = collections.defaultdict(collections.Counter)
        for e in gs:
            for (t, cls), n in e["traces"].items():
                tr[t][cls] += n
        for t in sorted(tr):
            print(f"  {t:6s} " + ", ".join(f"{c} {n}" for c, n in tr[t].most_common()))
    print("\nexamples of non-timing changes (configuration, trace, class, decision, t clean, t perturbed)")
    for g, e in by_group.items():
        for ex in e["examples"]:
            print("  ", *ex)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({"clean": clean, "results": results}, f, indent=1, default=str)
    return 0


if __name__ == "__main__":
    sys.exit(main())
