"""sg_pvs.py: the single source of truth for every PV of the 15LSS_sample_gas IOC.

Transcribed by hand from docs/ioc/15LSS_sample_gas_IOC_spec.md sections 7 (PV interface) and
9 (parameter reference). Consumed by gen_db.py (writes sampleGas.db, the autosave .req files,
and sgPvTable.c/.h) and, later, by Plan 5's screen generator. Do not hand-edit the generated
outputs; edit this file (and gen_db.py) and regenerate.

Record-type and screen-grouping choices not dictated by the spec (§7's "the implementer's
discretion" items) are noted inline; see task-1-report.md for the full rationale.
"""
import re

# ---------------------------------------------------------------------------
# 9.1 Station parameters (Par:<key>), transcribed verbatim from the spec table.
# Columns: key, default, unit, min, max, level ('U' main panel / 'A' admin / 'D' deep admin).
# Order matches sg_params in sgCore.h exactly (target..stallTime), plus writeEnable (glue-only;
# not a field of sg_params) appended last, as in the spec table.
# ---------------------------------------------------------------------------
_P91 = [
    ("target", 0.99, "%", 0.2, 5, "U", "O2 target"),
    ("tol", 0.02, "%", 0.005, 0.5, "A", "In-range tolerance (display, alarms; not a deadband)"),
    ("delta", -0.04, "%", -1, 1, "A", "Handoff margin below target (negative = above)"),
    ("purgeFlow", 20, "SLPM", 1, 20, "A", "Purge flow"),
    ("purgeTimeoutMargin", 1.3, "×", 1, 3, "A", "Timeout = margin × kinetic time"),
    ("cylWarnH", 24, "h", 1, 240, "A", "Forecast MINOR below"),
    ("cylAlarmH", 6, "h", 0.5, 120, "A", "Forecast MAJOR below"),
    ("fbDelay", 0, "s", 0, 600, "A", "Delay after handoff flow settles, before FBON"),
    ("flowMinorX", 1.5, "×exp", 1.05, 5, "A", "Flow-high MINOR"),
    ("flowMajorX", 2.0, "×exp", 1.1, 10, "A", "Flow-high MAJOR"),
    ("pinnedTime", 600, "s", 30, 7200, "A", "Pinned at max → MAJOR after"),
    ("o2AbnormalOffset", 0.3, "%", 0.02, 5, "A", "O2 abnormally high above target by"),
    ("settleTimeout", 7200, "s", 600, 86400, "A", "Target not reached within"),
    ("V", 41, "L", 5, 200, "D", "Enclosure volume (lid-on decay rate F/V)"),
    ("lidOnsetFrac", 0.02, "–", 0.005, 0.2, "D", "Decay onset = relative drop"),
    ("lidOnsetMax", 30, "s", 10, 180, "D", "No onset within → open"),
    ("lidWindow", 15, "s", 4, 60, "D", "Decay measured over"),
    ("lidCurvMin", 0.8, "–", 0.1, 1, "D", "Open if 2nd/1st-half rate below"),
    ("openSlopeFrac", 0.5, "–", 0.05, 0.95, "D", "Open if decay < fraction of F/V"),
    ("dropSkipLevel", 18, "%", 1, 21, "D", "Lid check skipped if purge starts below"),
    ("cylCapacityL", 8000, "L", 100, 50000, "D", "Usable He per full cylinder"),
    ("reportDays", 60, "days", 1, 73, "D", "Usage report window"),
    ("runGap", 216000, "s", 3600, 2592000, "D", "User run closes after no purge for (60 h)"),
    ("handoffHold", 5, "s", 1, 120, "D", "Lag-corrected O2 below target−Δ for"),
    ("purgeLag", 12, "s", 0, 60, "D", "Analyzer lag at purge flow (0 = none)"),
    ("handoffFlowTol", 0.05, "SLPM", 0.01, 1, "D", "Handoff flow-settled tolerance"),
    ("lidLevel", 10, "%", 2, 19, "D", "Lid detector O2 level"),
    ("lidFilter", 5, "s", 1, 60, "D", "...above level for"),
    ("lidSlope", 0.2, "%/s", 0.005, 2, "D", "...rise rate (10 s difference)"),
    ("lidSlopeWindow", 60, "s", 10, 300, "D", "...rate look-back window"),
    ("lidArmLevel", 9, "%", 1, 18, "D", "Detector arms in PURGE below"),
    # level A (Admin), not the spec's original D: user decision 2026-09-29
    ("rampMaxPurge", 5, "SLPM/s", 0.5, 20, "A", "Ramp clamp at purge start (0 counts as instant)"),
    ("rampMinHandoff", 2, "SLPM/s", 0.1, 10, "A", "Ramp clamp at handoff (min)"),
    ("hardCeiling", 2.0, "SLPM", 0.1, 20, "D", "Hard PID flow ceiling (surface vibration)"),
    ("purgeTimeoutMin", 120, "s", 30, 1800, "D", "Purge timeout clamp"),
    ("purgeTimeoutMax", 1800, "s", 60, 7200, "D", "Purge timeout clamp"),
    ("ambientRef", 19.4, "%", 5, 21, "D", "O2 assumed at blind-purge start"),
    ("flowLowX", 0.5, "×exp", 0.05, 0.95, "D", "Flow-low (wrong mode?) threshold"),
    ("mismatchAbs", 0.05, "SLPM", 0.01, 2, "D", "Mismatch tolerance (absolute)"),
    ("mismatchFrac", 0.05, "–", 0, 0.5, "D", "Mismatch tolerance (fraction)"),
    ("mismatchMargin", 5, "s", 1, 120, "D", "Mismatch time margin after ramp"),
    ("holdDetect", 3, "s", 1, 60, "D", "Hold monitor: on hold for"),
    ("holdRetries", 3, "–", 1, 20, "D", "Fast Run attempts"),
    ("holdRetryInterval", 10, "s", 2, 120, "D", "Fast retry interval"),
    ("holdSlowRetry", 60, "s", 10, 3600, "D", "Slow retry interval"),
    ("frozenTime", 30, "s", 5, 600, "D", "O2 frozen after unchanged for"),
    ("o2Min", -0.5, "%", -5, 1, "D", "O2 plausible minimum"),
    ("o2Max", 25, "%", 19, 100, "D", "O2 plausible maximum"),
    ("avgN", 10, "samples", 1, 120, "D", "O2 average for epid"),
    ("pidScan", 10, "s", 1, 120, "D", "PID step period"),
    ("odel", 0.01, "SLPM", 0, 0.5, "D", "epid ODEL"),
    ("gainSchedule", 1, "–", 0, 1, "D", "KP × 0.99/target"),
    ("fineBand", 0.01, "%", 0, 0.5, "D", "Fine band (0 = off); ON by user decision"),
    ("fineKPx", 0.5, "×", 0, 2, "D", "Fine band KP multiplier"),
    ("fineKIx", 1.0, "×", 0, 2, "D", "Fine band KI multiplier"),
    ("alarmDelay", 10, "s", 0, 300, "D", "Default alarm persistence"),
    ("flowAlarmDelay", 300, "s", 0, 3600, "D", "Flow-high/low and O2-range persistence"),
    ("progressMin", 0.0005, "%/min", 0.0001, 0.1, "D", "Settling progress threshold"),
    ("stallGrace", 300, "s", 0, 3600, "D", "Progress judged after"),
    ("flowSteadyBand", 0.02, "SLPM", 0.001, 1, "D", "PID output steady within ±"),
    ("o2SteadyRate", 0.002, "%/min", 0.0001, 0.1, "D", "O2 steady below"),
    ("stallWindow", 120, "s", 20, 600, "D", "Slope window"),
    ("slopeAvgN", 20, "samples", 1, 60, "D", "Samples averaged at each end of the window"),
    ("stallTime", 120, "s", 10, 1800, "D", "No progress this long → stalled"),
    # default 1, not the spec table's 0: user decision 2026-09-28 -- production must start able
    # to act ("the failure state is not doing anything"); the PC trial forces shadow at start
    # (FORCE_SHADOW=1 in st.cmd.pc). test_gen_db.DEFAULT_OVERRIDES records the difference.
    ("writeEnable", 1, "–", 0, 1, "A", "NEW: 0 = shadow mode (§8.20)"),
]

# 13.3 Deep admin groups (nine, in the order the spec lists them). Every D-level key must
# appear in exactly one of these (checked by test_gen_db).
DEEP_GROUPS = [
    "Enclosure and purge",
    "Lid check (during purge)",
    "Lid detector (lid lifted)",
    "PID",
    "Settling",
    "Alarms",
    "O2 reading checks",
    "MFC hold monitor",
    "Helium",
]

_D_GROUP_KEYS = {
    "Enclosure and purge": ["V", "purgeLag", "handoffHold", "handoffFlowTol", "purgeTimeoutMin",
                             "purgeTimeoutMax", "ambientRef"],
    "Lid check (during purge)": ["dropSkipLevel", "lidOnsetFrac", "lidOnsetMax", "lidWindow",
                                  "openSlopeFrac", "lidCurvMin"],
    "Lid detector (lid lifted)": ["lidLevel", "lidFilter", "lidSlope", "lidSlopeWindow",
                                   "lidArmLevel"],
    "PID": ["avgN", "pidScan", "odel", "gainSchedule", "fineBand", "fineKPx", "fineKIx",
            "hardCeiling"],
    "Settling": ["progressMin", "stallGrace", "stallWindow", "slopeAvgN", "stallTime",
                 "flowSteadyBand", "o2SteadyRate"],
    "Alarms": ["alarmDelay", "flowAlarmDelay", "flowLowX", "mismatchAbs", "mismatchFrac",
               "mismatchMargin"],
    "O2 reading checks": ["frozenTime", "o2Min", "o2Max"],
    "MFC hold monitor": ["holdDetect", "holdRetries", "holdRetryInterval", "holdSlowRetry"],
    "Helium": ["cylCapacityL", "reportDays", "runGap"],
}

# Integer parameters (§7.2: "Integer parameters are longout"). Everything else in _P91 is a
# continuous quantity (ao), even where the default happens to be a whole number. These four are
# genuinely discrete: two sample/attempt counts and one 0/1 flag. (Record-type decision left to
# the implementer by the spec; writeEnable is 0/1 too but is generated as `bo`, not `longout` —
# see PARAMS_WRITE_ENABLE below.)
_INTEGER_KEYS = {"avgN", "slopeAvgN", "holdRetries", "gainSchedule"}


def _key_to_group(key, level):
    if level == "U":
        return "Main"
    if level == "A":
        return "Admin"
    for group, keys in _D_GROUP_KEYS.items():
        if key in keys:
            return group
    raise ValueError(f"D-level key {key!r} not placed in any §13.3 group")


def _label(key):
    """Cheap CamelCase -> "Camel Case" splitter for a short screen label."""
    out = []
    for i, ch in enumerate(key):
        if ch.isupper() and i > 0 and not key[i - 1].isupper():
            out.append(" ")
        out.append(ch)
    s = "".join(out)
    return s[0].upper() + s[1:] if s else s


PARAMS = []
for _key, _default, _unit, _min, _max, _level, _meaning in _P91:
    PARAMS.append({
        "key": _key,
        "default": float(_default),
        "unit": _unit,
        "min": float(_min),
        "max": float(_max),
        "level": _level,
        "group": _key_to_group(_key, _level),
        "label": _label(_key),
        "integer": _key in _INTEGER_KEYS or _key == "writeEnable",
        "desc": _meaning,
    })

assert len(PARAMS) == 65, len(PARAMS)
assert sum(1 for p in PARAMS if p["level"] == "D") == 49

# ---------------------------------------------------------------------------
# 9.2 Mode-slot limits and 7.4 mode-slot defaults (Mode:<X>:<field>, X in A..D).
# ---------------------------------------------------------------------------
MODE_FIELDS = [
    {"field": "baseFlow", "unit": "SLPM", "min": 0.01, "max": 20.0},
    {"field": "n", "unit": "–", "min": 0.1, "max": 3.0},
    {"field": "KP", "unit": "SLPM/%", "min": -100.0, "max": 0.0},
    {"field": "KI", "unit": "1/s", "min": 0.0, "max": 0.05},
    {"field": "drvh", "unit": "SLPM", "min": 0.1, "max": 20.0},
    {"field": "drvl", "unit": "SLPM", "min": 0.0, "max": 5.0},
]

MODE_SLOTS = ["A", "B", "C", "D"]

MODE_DEFAULTS = {
    "A": {"name": "Normal lid", "baseFlow": 0.25, "n": 1.0, "KP": -9.3, "KI": 8.8e-4,
          "drvh": 1.0, "drvl": 0.05},
    "B": {"name": "Collimator lid", "baseFlow": 0.84, "n": 0.53, "KP": -10.0, "KI": 1.4e-3,
          "drvh": 2.0, "drvl": 0.3},
    "C": {"name": "Spare C", "baseFlow": 0.25, "n": 1.0, "KP": -9.3, "KI": 8.8e-4,
          "drvh": 1.0, "drvl": 0.05},
    "D": {"name": "Spare D", "baseFlow": 0.25, "n": 1.0, "KP": -9.3, "KI": 8.8e-4,
          "drvh": 1.0, "drvl": 0.05},
}

# ---------------------------------------------------------------------------
# 7.5 Alarms: (PV name suffix after "Alm:", key used in the reference / sgAlarms.c, matching the
# order of enum sg_alarm in sgCore.h). Each gets Alm:<Name> (mbbi) and Alm:<Name>:Msg (lsi).
# ---------------------------------------------------------------------------
ALARM_TABLE = [
    ("OpenStop", "openStop"),
    ("PurgeIncomplete", "purgeInc"),
    ("O2Bad", "o2bad"),
    ("OpenLoop", "openLoop"),
    ("Override", "override"),
    ("HoldStuck", "holdStuck"),
    ("Mismatch", "mismatch"),
    ("FlowHigh", "flowHigh"),
    ("FlowLow", "flowLow"),
    ("Pinned", "pinned"),
    ("O2High", "o2High"),
    ("NotReached", "notReached"),
    ("CylLow", "cylLow"),
    ("Gas", "gas"),
]
assert len(ALARM_TABLE) == 14

# ---------------------------------------------------------------------------
# Everything else in spec §7 (operator screen minus Par:target and Mode, admin commands, alarms,
# diagnostics, helium ledger, epid/averaging, linked PV names). suffix is appended to "$(P)".
#
# rtyp: base-7 record type. fields: dict of extra DB fields gen_db.py emits verbatim (beyond the
# DTYP-omitted / PINI defaults it applies from 'pini' and rtyp-specific defaults such as lsi
# SIZV 256). tag: which part of the glue owns/writes this PV (used later by the SNL/glue code and
# by Plan 5's screens, not by gen_db.py itself). autosave: 'settings' | 'helium' | None.
# pini: True to force PINI YES (parameter-like records that must be valid at iocInit with no
# autosave file present).
# ---------------------------------------------------------------------------

_ALM_SEVR_FIELDS = {
    "ZRST": "none", "ONST": "MINOR", "TWST": "MAJOR",
    "ZRVL": 0, "ONVL": 1, "TWVL": 2,
    "ZRSV": "NO_ALARM", "ONSV": "MINOR", "TWSV": "MAJOR",
    # VAL 0 ("none") + PINI YES (set by each caller below): without this, Alm:* and
    # Sts:WorstSevr come up UDF/INVALID at iocInit until the SNL's first write, i.e. ~15 spurious
    # INVALID alarms at every IOC restart (controller ruling, fix round 1, finding 5).
    "VAL": 0,
}

PVS = []


def _add(suffix, rtyp, fields=None, tag="misc", autosave=None, pini=False, desc=None):
    PVS.append({
        "suffix": suffix, "rtyp": rtyp, "fields": dict(fields or {}), "tag": tag,
        "autosave": autosave, "pini": pini, "desc": desc,
    })


# --- Mode (top-level enclosure mode select), spec §7.1 ---
_mode_state_fields = {"ZRST": MODE_DEFAULTS["A"]["name"], "ONST": MODE_DEFAULTS["B"]["name"],
                      "TWST": MODE_DEFAULTS["C"]["name"], "THST": MODE_DEFAULTS["D"]["name"],
                      "ZRVL": 0, "ONVL": 1, "TWVL": 2, "THVL": 3,
                      # defined at start even with no autosave file yet (it came up INVALID/UDF
                      # on the first hardware run, 2026-09-28): default mode A, no UDF alarm
                      "VAL": 0, "UDFS": "NO_ALARM"}
_add("Mode", "mbbo", _mode_state_fields, tag="mode", autosave="settings", pini=True,
     desc="Enclosure mode A/B/C/D")

# --- Operator + admin commands, spec §7.1 and §7.3 ---
for _suf, _desc in [
    ("Cmd:Purge", "Start a purge"),
    ("Cmd:FlowZero", "Go to FLOW_ZERO"),
    ("Cmd:ResumeFlow", "Resume feedback without a purge"),
    ("Cmd:NewCylinder", "New helium cylinder fitted"),
    ("Cmd:ReleaseIdle", "Go to IDLE"),
    ("Cmd:MarkNewRun", "Ledger event newrun"),
    ("Cmd:ResetOverrideCount", "Set Diag:OverrideCount = 0"),
]:
    _add(_suf, "bo", tag="cmd", desc=_desc)

# --- Status (operator screen), spec §7.1 ---
_STATE_NAMES = ["IDLE", "PRECHECK", "PURGE", "HANDOFF", "REGULATE", "OPEN_LOOP", "FLOW_ZERO",
                "OPEN_STOP"]
_state_fields = {}
for _i, _n in enumerate(_STATE_NAMES):
    _state_fields[["ZRST", "ONST", "TWST", "THST", "FRST", "FVST", "SXST", "SVST"][_i]] = _n
    _state_fields[["ZRVL", "ONVL", "TWVL", "THVL", "FRVL", "FVVL", "SXVL", "SVVL"][_i]] = _i
_add("Sts:State", "mbbi", _state_fields, tag="sts", desc="Controller state")
_add("Sts:StateDesc", "lsi", {"SIZV": 256}, tag="sts", desc="One-line description of the state")
_add("Sts:LastAction", "lsi", {"SIZV": 256}, tag="sts", desc="Most recent transition reason")
_add("Sts:Progress", "lsi", {"SIZV": 512}, tag="sts", desc="State detail / progress line")
_add("Sts:InRange", "bi", {"ZNAM": "Out of range", "ONAM": "In range"}, tag="sts")
_add("Sts:O2", "ai", {"EGU": "%", "PREC": "3"}, tag="sts", desc="O2 used this tick")
_add("Sts:O2Valid", "bi", {"ZNAM": "Invalid", "ONAM": "Valid"}, tag="sts")
_add("Sts:ExpectedFlow", "ai", {"EGU": "SLPM", "PREC": "3"}, tag="sts")
_add("Sts:LastCmd", "ai", {"EGU": "SLPM", "PREC": "3"}, tag="sts", desc="Last flow commanded")
_add("Sts:Flow", "ai", {"EGU": "SLPM", "PREC": "3"}, tag="sts", desc="Copy of MFC Flow_RBV")
_add("Sts:SetpointRBV", "ai", {"EGU": "SLPM", "PREC": "3"}, tag="sts")
_add("Sts:MfcRunning", "bi", {"ZNAM": "Hold", "ONAM": "Running"}, tag="sts")
_add("Sts:MfcStatus", "stringin", {}, tag="sts", desc="Copy of MFC Status")
_add("Sts:Heartbeat", "longin", {}, tag="sts", desc="+1 every tick")
# Heartbeat staleness (spec 13.4, ruling P5-R5; added 2026-09-29 at the user's request): the IOC
# alive but the controller no longer ticking (sequencer stopped or stuck) raises a MAJOR on
# TickAge, which the database computes on its own scan thread. HbLast holds the heartbeat seen
# at the previous scan: TickAge reads it, then its FLNK updates it.
_add("Sts:TickAge", "calc", {"SCAN": "1 second", "INPA": "$(P)Sts:Heartbeat NPP",
                             "INPB": "$(P)Sts:HbLast NPP", "INPC": "$(P)Sts:TickAge NPP",
                             "CALC": "A#B?0:C+1", "EGU": "s", "PREC": 0, "HIHI": 10,
                             "HHSV": "MAJOR", "HOPR": 60, "FLNK": "$(P)Sts:HbLast"},
     tag="sts", desc="Seconds since the last tick")
_add("Sts:HbLast", "calc", {"INPA": "$(P)Sts:Heartbeat NPP", "CALC": "A"}, tag="sts",
     desc="Heartbeat at the last TickAge scan")
_add("Sts:Banner", "lsi", {"SIZV": 256}, tag="sts", desc="Active alarm texts")
_add("Sts:WorstSevr", "mbbi", dict(_ALM_SEVR_FIELDS), tag="sts", desc="Worst active alarm",
     pini=True)
_add("Sts:WriteEnable", "bi", {"ZNAM": "Shadow", "ONAM": "Live"}, tag="sts",
     desc="Mirror of Par:writeEnable")
_add("Sts:CylPressure", "ai", {"EGU": "psi", "PREC": "1"}, tag="sts",
     desc="Copy of the Cfg:CYL PV if configured")

# --- Helium forecast on the main panel, spec §7.1 ---
_add("He:LeftL", "ai", {"EGU": "L", "PREC": "1"}, tag="he", desc="Litres left in the cylinder")
_add("He:EmptyMinH", "ai", {"EGU": "h", "PREC": "2"}, tag="he")
_add("He:EmptyMaxH", "ai", {"EGU": "h", "PREC": "2"}, tag="he")
_add("He:EmptyMedianH", "ai", {"EGU": "h", "PREC": "2"}, tag="he")
_add("He:ForecastText", "lsi", {"SIZV": 256}, tag="he", desc="Run-out forecast text")

# --- Alarms, spec §7.5 ---
for _name, _key in ALARM_TABLE:
    _add(f"Alm:{_name}", "mbbi", dict(_ALM_SEVR_FIELDS), tag="alm", desc=f"key: {_key}",
         pini=True)
    _add(f"Alm:{_name}:Msg", "lsi", {"SIZV": 256}, tag="alm")

# --- Diagnostics, spec §7.6 (read-only, written by the SNL) ---
_add("Diag:AboveCount", "ai", {"EGU": "s", "PREC": "1"}, tag="diag")
_add("Diag:MaxRate", "ai", {"EGU": "%/s", "PREC": "4"}, tag="diag")
_add("Diag:LidArmed", "bi", {"ZNAM": "Disarmed", "ONAM": "Armed"}, tag="diag")
_add("Diag:HoldSec", "longin", {"EGU": "s"}, tag="diag")
_add("Diag:HoldAttempts", "longin", {}, tag="diag")
_add("Diag:Settling", "bi", {"ZNAM": "Not settling", "ONAM": "Settling"}, tag="diag")
_add("Diag:SettleDir", "longin", {}, tag="diag", desc="-1/0/+1")
_add("Diag:SettleT0", "ai", {"EGU": "s", "PREC": "0"}, tag="diag")
_add("Diag:TowardRate", "ai", {"EGU": "%/min", "PREC": "4"}, tag="diag")
_add("Diag:O2Slope", "ai", {"EGU": "%/min", "PREC": "4"}, tag="diag")
_add("Diag:StallSec", "longin", {"EGU": "s"}, tag="diag")
_add("Diag:StallLatched", "bi", {"ZNAM": "Clear", "ONAM": "Latched"}, tag="diag")
_add("Diag:FlowSteady", "bi", {"ZNAM": "Not steady", "ONAM": "Steady"}, tag="diag")
_add("Diag:InFine", "bi", {"ZNAM": "Coarse", "ONAM": "Fine"}, tag="diag")
_add("Diag:PinnedSec", "longin", {"EGU": "s"}, tag="diag")
_add("Diag:PurgeElapsed", "ai", {"EGU": "s", "PREC": "1"}, tag="diag")
_add("Diag:PurgeTimeout", "ai", {"EGU": "s", "PREC": "1"}, tag="diag")
_add("Diag:O2Start", "ai", {"EGU": "%", "PREC": "3"}, tag="diag")
_add("Diag:LidOnsetT", "ai", {"EGU": "s", "PREC": "1"}, tag="diag")
_add("Diag:LidRatio", "ai", {"PREC": "4"}, tag="diag")
_add("Diag:LidCurv", "ai", {"PREC": "4"}, tag="diag")
_add("Diag:LidResult", "mbbi", {"ZRST": "pending", "ONST": "passed", "TWST": "skipped",
                                 "THST": "open", "ZRVL": 0, "ONVL": 1, "TWVL": 2, "THVL": 3},
     tag="diag")
_add("Diag:EstO2", "ai", {"EGU": "%", "PREC": "3"}, tag="diag", desc="Lag-corrected O2")
_add("Diag:Blind", "bi", {"ZNAM": "Not blind", "ONAM": "Blind"}, tag="diag")
_add("Diag:HandoffPhase", "bi", {"ZNAM": "settle", "ONAM": "delay"}, tag="diag")
_add("Diag:OverrideCount", "longin", {}, tag="diag", autosave="settings")
_add("Diag:OverrideLog", "lsi", {"SIZV": 4096}, tag="diag",
     desc="Last 20 overrides, newest first")
_add("Log:Text", "waveform", {"FTVL": "CHAR", "NELM": 16384}, tag="diag",
     desc="Event log, newest first")

# --- Helium ledger and usage, spec §7.7 ---
_add("He:CumL", "ao", {"EGU": "L", "PREC": "2"}, tag="he", autosave="helium",
     desc="Monotonic litres dispensed")
# LastTotal and CylBase start as NaN = "unset" (sgCore.h, sg_helium): on a fresh IOC with no
# autosave file the glue imports these records as the helium state, and a 0 default would be read
# as a real totalizer reference -- the first tick would add the whole Total_RBV to CumL and count
# the cylinder as used up (Plan 2 task 3).
_add("He:LastTotal", "ao", {"EGU": "L", "PREC": "2", "VAL": "NaN"}, tag="he", autosave="helium")
_add("He:CylBase", "ao", {"EGU": "L", "PREC": "2", "VAL": "NaN"}, tag="he", autosave="helium")
_add("He:HistT", "waveform", {"FTVL": "DOUBLE", "NELM": 6000}, tag="he", autosave="helium")
_add("He:HistUsed", "waveform", {"FTVL": "DOUBLE", "NELM": 6000}, tag="he", autosave="helium")
_add("He:HistN", "longin", {}, tag="he", autosave="helium")
_add("He:EvT", "waveform", {"FTVL": "DOUBLE", "NELM": 4000}, tag="he", autosave="helium")
_add("He:EvType", "waveform", {"FTVL": "LONG", "NELM": 4000}, tag="he", autosave="helium")
_add("He:EvL", "waveform", {"FTVL": "DOUBLE", "NELM": 4000}, tag="he", autosave="helium")
_add("He:EvN", "longin", {}, tag="he", autosave="helium")
_add("He:SnapT", "waveform", {"FTVL": "DOUBLE", "NELM": 2000}, tag="he", autosave="helium")
_add("He:SnapL", "waveform", {"FTVL": "DOUBLE", "NELM": 2000}, tag="he", autosave="helium")
_add("He:SnapN", "longin", {}, tag="he", autosave="helium")

for _w in ["3d", "2d", "1d", "12h", "6h"]:
    _add(f"He:Est{_w}H", "ai", {"EGU": "h", "PREC": "2"}, tag="he",
         desc=f"Run-out, {_w} window")
    _add(f"He:Rate{_w}", "ai", {"EGU": "L/day", "PREC": "2"}, tag="he",
         desc=f"Usage rate, {_w} window")

for _suf in ["Dispensed", "InRuns", "Cylinders", "CylEquiv", "WinStart", "WinEnd"]:
    _add(f"He:Rep:{_suf}", "ai", {"PREC": "2"}, tag="he")

for _suf, _ftvl in [("RunStart", "DOUBLE"), ("RunEnd", "DOUBLE"), ("RunPurges", "LONG"),
                     ("RunL", "DOUBLE"), ("RunCyl", "LONG"), ("RunFinished", "LONG")]:
    _add(f"He:Rep:{_suf}", "waveform", {"FTVL": _ftvl, "NELM": 100}, tag="he")
_add("He:Rep:RunN", "longin", {}, tag="he")
_add("He:Rep:Text", "waveform", {"FTVL": "CHAR", "NELM": 8192}, tag="he",
     desc="Human-readable usage report")

# --- epid and averaging, spec §7.8 ---
_add("PID", "epid", {
    "SCAN": "Passive", "INP": "$(P)PID:CVAL NPP", "OUTL": "$(P)PID:Out NPP", "KD": 0,
    "FMOD": "PID", "PREC": "4", "EGU": "SLPM",
}, tag="pid", desc="PID step; VAL/KP/KI/DRVL/DRVH/ODEL/FBON written by the SNL")
_add("PID:CVAL", "ai", {"EGU": "%", "PREC": "3"}, tag="pid",
     desc="Mean of the last avgN valid O2 samples")
_add("PID:Out", "ao", {"EGU": "SLPM", "PREC": "3"}, tag="pid", desc="epid's output (soft)")

# --- Linked PV names, spec §7.9 ---
for _f in ["MFC", "O2", "CYL", "STN"]:
    _add(f"Cfg:{_f}", "stringout", {"VAL": f"$({_f})"}, tag="cfg", autosave="settings",
         pini=True)
for _f in ["MFC", "O2", "CYL", "STN"]:
    _add(f"Cfg:Default:{_f}", "stringin", {"VAL": f"$({_f})"}, tag="cfg", pini=True)
for _f in ["MFC", "O2", "CYL", "STN"]:
    _add(f"Cfg:Active:{_f}", "stringin", {}, tag="cfg")
_add("Cfg:Apply", "bo", {}, tag="cfg", desc="Apply the edited names")
_add("Cfg:RestoreDefaults", "bo", {}, tag="cfg", desc="Copy the macro defaults into the fields")
_add("Cfg:Pending", "bi", {"ZNAM": "applied", "ONAM": "pending"}, tag="cfg")
for _f in ["MFC", "O2", "CYL"]:
    _add(f"Cfg:Conn:{_f}", "bi", {"ZNAM": "Disconnected", "ONAM": "Connected"}, tag="cfg")
_add("Cfg:Status", "lsi", {"SIZV": 256}, tag="cfg", desc="Last apply result")

del _add
