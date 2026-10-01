/* sgCore.h: 15LSS_sample_gas controller core. Pure C17, no EPICS, no heap.
   A port of Controller in simulator/sample_gas_simulator.html (tag sim-v1.0); names follow it.

   ======================================================================= contract for the glue
   Time
   - `now` is integer epoch seconds, +1 per tick, never repeated: the core tests
     fmod(now, 60 | 3600 | pidScan) == 0 and counts ticks as seconds. It is never skipped
     either, with one exception in the IOC (sgIoc.c, sgIocSecondsToNextTick): when the tick
     clock has fallen more than 60 s behind the wall clock (a stalled IOC), late ticks are no
     longer caught up and `now` jumps once to the wall clock (logged MINOR); up to 60 s behind,
     the missed ticks are run back to back.

   Call order, every tick
     1. sg_set_inputs(c, &in)            one consistent snapshot (spec §8.1), see sg_inputs
     2. operator calls                   sg_op_*, sg_set_target, sg_set_mode, sg_new_cylinder,
                                         sg_mark_new_run, sg_reset_override_count,
                                         sg_channels_changed (spec §8.1 order)
     3. sg_tick(c, now)
     4. if sg_pid_due(c, now): sg_pid_prepare(c, &cfg) and, if it returns 1, the epid record
        (see sg_epid_cfg), then sg_pid_done(c, PID.OVAL)
   First tick after iocInit
     sg_init (at startup, see below), autosave's values into c->p / overrideCount /
     sg_helium_import, then: sg_set_inputs -> sg_restart(c, now) -> sg_tick(c, now) -> step 4.
   Operator calls run between steps 1 and 3, so they see the new c->in but the previous tick's
   `now`, o2, o2ok and state.

   Ownership
   - The glue may read every field of sg_ctl.
   - It writes only: c->p (parameters; see below), c->io, the helium state through
     sg_helium_import, and c->overrideCount (the autosaved value, once at start).
   - At start (before sg_restart) the autosaved parameters, target and mode included, are
     written into c->p directly. After that, p.target and p.mode change only through
     sg_set_target / sg_set_mode, and only on a real change (both log; a target change restarts
     settling). Every other p field may be written directly; it takes effect at its next use.
   - Not thread-safe: every call on one sg_ctl from one thread (the SNL state set).

   Puts (io.put_setpoint / put_ramp / put_run) are requests: the glue's single gated put
   function (spec §8.3, §8.20) drops them in shadow mode and while the MFC is disconnected (not
   while it is only frozen, in.mfcStale). It must treat the MFC as connected in the same tick as
   in.mfcConnected says so, or the reconnect re-send of spec §8.18 (made inside sg_tick) is
   dropped.
   ============================================================================================ */
#ifndef SGCORE_H
#define SGCORE_H
#include <stddef.h>
#include <stdio.h>      /* MinGW: __MINGW_PRINTF_FORMAT for the sg_log format attribute */

#ifdef __GNUC__
#  ifdef __MINGW_PRINTF_FORMAT
#    define SG_PRINTF(f, a) __attribute__((format(__MINGW_PRINTF_FORMAT, f, a)))
#  else
#    define SG_PRINTF(f, a) __attribute__((format(printf, f, a)))
#  endif
#else
#  define SG_PRINTF(f, a)
#endif

enum sg_state { SG_NONE = -1, SG_IDLE, SG_PRECHECK, SG_PURGE, SG_HANDOFF, SG_REGULATE,
                SG_OPEN_LOOP, SG_FLOW_ZERO, SG_OPEN_STOP, SG_NSTATES };
/* Order = PV list of spec §7.5 (Alm:*). PINNEDLOW exists only because enter() clears it.
   UNITS and SHADOW are not in the reference; the glue raises them (sgStart.c; spec §8.8, §8.20). */
enum sg_alarm { SG_A_OPENSTOP, SG_A_PURGEINC, SG_A_O2BAD, SG_A_OPENLOOP, SG_A_OVERRIDE,
                SG_A_HOLDSTUCK, SG_A_MISMATCH, SG_A_FLOWHIGH, SG_A_FLOWLOW, SG_A_PINNED,
                SG_A_O2HIGH, SG_A_NOTREACHED, SG_A_CYLLOW, SG_A_GAS, SG_A_UNITS, SG_A_SHADOW,
                SG_A_PINNEDLOW, SG_NALARMS };
enum sg_event { SG_EV_PURGE = 1, SG_EV_ZERO = 2, SG_EV_CYLINDER = 3, SG_EV_NEWRUN = 4 };
enum sg_lidres { SG_LID_PENDING, SG_LID_PASSED, SG_LID_SKIPPED, SG_LID_OPEN };

#define SG_MSG      256
#define SG_O2HIST   1024    /* >= max(90, stallWindow + slopeAvgN + 5, 2 lidWindow + 5) = 665 */
#define SG_RATEHIST 512     /* >= lidSlopeWindow max 300 */
#define SG_AVGBUF   128     /* >= avgN max 120 */
#define SG_OVALHIST 1024    /* >= stallWindow + 1 = 601 */
#define SG_CYLHIST  6000    /* spec §7.7 He:HistT */
#define SG_LEDGER   4000    /* He:EvT */
#define SG_SNAPS    2000    /* He:SnapT */
#define SG_RUNS     100     /* He:Rep:Run* */
#define SG_NWIN     5       /* forecast windows 3, 2, 1, 0.5, 0.25 days */

typedef struct { char name[40]; double baseFlow, n, KP, KI, drvh, drvl; } sg_mode;

typedef struct {   /* spec §9.1, names = the Par:<key> suffixes; defaults in sg_default_params */
    double target, tol, delta, purgeFlow, purgeTimeoutMargin, cylWarnH, cylAlarmH, fbDelay,
           flowMinorX, flowMajorX, pinnedTime, o2AbnormalOffset, settleTimeout, V, lidOnsetFrac,
           lidOnsetMax, lidWindow, lidCurvMin, lidMinSamples, openSlopeFrac, dropSkipLevel,
           cylCapacityL,
           reportDays, runGap, handoffHold, purgeLag, handoffFlowTol, lidLevel, lidFilter,
           lidSlope, lidSlopeWindow, lidArmLevel, rampMaxPurge, rampMinHandoff, hardCeiling,
           purgeTimeoutMin, purgeTimeoutMax, ambientRef, flowLowX, mismatchAbs, mismatchFrac,
           mismatchMargin, holdDetect, holdRetries, holdRetryInterval, holdSlowRetry, frozenTime,
           o2Min, o2Max, avgN, pidScan, odel, gainSchedule, fineBand, fineKPx, fineKIx,
           alarmDelay, flowAlarmDelay, progressMin, stallGrace, flowSteadyBand, o2SteadyRate,
           stallWindow, slopeAvgN, stallTime;
    int mode;              /* 0..3 = A..D */
    sg_mode modes[4];
} sg_params;

/* One consistent snapshot per tick (spec §8.1), filled by the glue:
   - o2            the O2 channel's last received value; never NaN or a default for a
                   disconnection (o2Sevr says that). Ruling R13: a non-finite o2 anyway is
                   treated as o2Sevr 3 with the previous value held.
   - o2Sevr        the O2 channel's alarm severity; 3 when disconnected or INVALID.
   - flow, sp, ramp  Flow_RBV, Setpoint_RBV, RampRate_RBV: last received values, never NaN or a
                   default for a disconnection (mfcConnected says that). Before the first value
                   ever arrives NaN is acceptable (mfcConnected is 0 then).
   - total         Total_RBV; NaN when disconnected (the ledger and forecast skip it; they also
                   treat it as NaN whenever mfcConnected is 0).
   - running       Running_RBV (1 = running, 0 = on hold), last received value.
   - gas           Gas_RBV state string ("He" is correct), last received value.
   - mfcConnected  0 when any required Alicat channel of spec §6.1 is disconnected or Flow_RBV is
                   INVALID, or Flow_RBV is frozen (mfcStale), else 1 (spec §8.18).
   - mfcStale      1 when mfcConnected is 0 because Flow_RBV is frozen while the Alicat should be
                   flowing (G3, spec §8.18): only the alarm and log texts differ. The glue keeps
                   the gate open meanwhile (puts are still made; see "Puts" above).
   - writeEnabled  1 while writes are enabled (the gate's Par:writeEnable, spec §8.20), 0 in shadow
                   mode. Only the setpoint-follow check (spec §8.14) reads it: it never judges in
                   shadow, where no write is made. The glue may correct it after the operator
                   calls of the same tick (a Par:writeEnable switch). The replay sets 1 (the
                   reference always writes); a zeroed struct means shadow. */
typedef struct {
    double o2; int o2Sevr;
    double flow, sp, ramp, total;
    int running;
    char gas[40];
    int mfcConnected, mfcStale;
    int writeEnabled;
} sg_inputs;

/* Sources of Alm:Mismatch (spec §8.14 "Mismatch sources"), highest first; the flow-mismatch check
   is the lowest and runs only while none of these is on. */
enum sg_mm { SG_MM_DISCONN, SG_MM_WRITE, SG_MM_NOCOMPUTE, SG_MM_FOLLOW, SG_MM_N };

typedef struct {           /* effects; any pointer may be NULL */
    void *ctx;
    void (*log)(void *ctx, double t, int sev, const char *msg);  /* sev 0 info, 1 MINOR, 2 MAJOR */
    void (*put_setpoint)(void *ctx, double v);
    void (*put_ramp)(void *ctx, double v);
    void (*put_run)(void *ctx);
} sg_io;

typedef struct { int sev; char msg[SG_MSG]; double since, until; int active; } sg_alarm_slot;
typedef struct { int cur, cand; double since; int used; } sg_deb;
/* The core's copy of the epid record fields (the reference's this.epid): VAL..ODEL as last
   configured, CVAL as last computed, OVAL as last returned by sg_pid_done (0 after reinit). */
typedef struct { double VAL, CVAL, KP, KI, DRVL, DRVH, ODEL, OVAL; int FBON; } sg_epid;
typedef struct { double t; int type; double L; } sg_ledger_ev;
typedef struct { double t, L; } sg_snap;
typedef struct { double t, used; } sg_cylpt;
typedef struct { double days, h, rate; const char *why; } sg_cylest;   /* h NaN = none */

typedef struct {           /* per-state data (JS this.sd); NaN = unset */
    double t0, o2Start, timerT0, below, elapsed, onsetT, cOn, kin, curv, kObs, checkAt, cCheck,
           est, timeout, t1;
    int dropChecked, lidResult, phaseDelay;   /* phaseDelay: HANDOFF 0 = settle, 1 = delay */
} sg_sd;

typedef struct {
    double start, end, L, startL, endL;       /* end NaN = open */
    int purges, cylinders, finished;
} sg_run;
typedef struct {
    sg_run runs[SG_RUNS]; int nRuns;           /* runs in the window */
    double wStart, wEnd, dispensed, cylEquiv, inRuns; int cyls;
} sg_report;

typedef struct sg_ctl {
    sg_params p; sg_io io; sg_inputs in;
    double now; int state; sg_sd sd; char lastAction[SG_MSG]; long heartbeat;
    sg_alarm_slot alarms[SG_NALARMS]; sg_deb deb[SG_NALARMS];
    double o2, lastO2; int o2ok, sameCount, frozen;
    double o2hist[SG_O2HIST]; int nO2;
    double rateHist[SG_RATEHIST]; int nRate; double maxRate; int aboveCount, lidArmed;
    double avgBuf[SG_AVGBUF]; int nAvg; int blind;
    double lastCmd, lastCmdTime, flowAtCmd;          /* lastCmd NaN = none yet */
    double spSeen, spChangeT, flowAtSpChange;
    int holdSec, holdAttempts, holdRecovering; double nextHoldAttempt;
    int pinnedSec, pinnedLowSec;
    int settling, settleDir, stallSec, stallLatched; double settleT0, towardRate, o2Slope;
    double ovalHist[SG_OVALHIST]; int nOval; int inFine;
    int flowSteady;                                   /* Diag:FlowSteady; 0 outside REGULATE */
    sg_epid epid;
    long overrideCount; char overrideLog[20][SG_MSG]; double overrideT[20]; int nOverride;
    /* helium state (autosaved in the IOC: sg_helium_export / sg_helium_import) */
    double cylBase, cylLeftL;                         /* NaN = unset */
    sg_cylpt cylHist[SG_CYLHIST]; int nCyl;
    double cumL, lastTotal;                           /* lastTotal NaN = unset */
    sg_ledger_ev ledger[SG_LEDGER]; int nLedger;
    sg_snap snaps[SG_SNAPS]; int nSnaps;
    long ledgerSeq;        /* +1 on every ledger event (recompute the report, manual_save) */
    /* forecast (derived; recomputed every 60 s) */
    sg_cylest cylEst[SG_NWIN]; int nCylEst;           /* 0 until the first 60 s sample */
    double cylMedianH, cylMinH, cylMaxH;              /* of the window estimates; NaN = none */
    /* spec §8.18, MFC disconnected (not in the reference); reset by sg_reinit and in IDLE */
    int mfcDisconnSec;     /* consecutive ticks with mfcConnected = 0 outside IDLE */
    int mfcDisconnAlarm;   /* 1 while the "MFC not responding" Mismatch alarm is raised */
    int mfcDisconnStale;   /* ... raised for a frozen Flow_RBV (the recovery log text) */
    int badCmd;            /* ruling R12: 1 after an ignored non-finite command (logged once) */
    /* Alm:Mismatch sources (spec §8.14; not in the reference); reset by sg_reinit and by an MFC
       change (sg_channels_changed) */
    int mmOn[SG_MM_N]; char mmMsg[SG_MM_N][SG_MSG];
    int mmShown;           /* the source whose text Alm:Mismatch shows, -1 = none of them */
    int followSec;         /* setpoint follow: consecutive ticks with Setpoint_RBV off lastCmd */
    double followStep;     /* |lastCmd - the command before it| (SLPM): the Alicat ramps its
                              Setpoint_RBV over followStep / RampRate s after lastCmdTime */
    double nextFollowResend;
    /* §8.11: 1 when REGULATE was entered by the restart decision or Resume Flow from IDLE (the
       flow is the settled one: seed the start fully bumpless); 0 when entered through HANDOFF,
       where the purge has driven O2 below target and the seed would clamp at DRVL and start the
       integral low (Plan 4 Task 3, sc01: O2 overshoot to 1.03 %, a MINOR alarm) */
    int seedStart;
} sg_ctl;

/* One PID step for the epid record, from sg_pid_prepare. The glue:
     1. before processing, whenever the epid record's FBON goes 0 -> 1 (or at the first PID step
        after IOC start), writes PID:Out = sg_pid_bumpless_i(cfg), so the record's first output
        is OUTL (= lastCmd, the flow actually commanded): a fully bumpless start (spec §7.8,
        §8.3, §8.11). OUTL is NaN only if nothing has been commanded since the restart; leave
        PID:Out alone then.
     2. writes VAL, KP, KI, DRVL, DRVH, ODEL, FBON to the record and CVAL to PID:CVAL; ODEL is
        written as 0 for the start processing of step 1, so the output deadband cannot hold the
        record's stale OVAL (computed while FBON was 0) instead of OUTL,
     3. processes the record and passes its OVAL to sg_pid_done. */
typedef struct { double VAL, KP, KI, DRVL, DRVH, ODEL, CVAL, OUTL; int FBON;
                 int SEED;   /* 1: seed the start fully bumpless (step 1); 0: PID:Out = OUTL,
                                epid's own start (after HANDOFF, as the reference) */
               } sg_epid_cfg;

/* Helium autosave image in the He: waveform layout (spec §7.7, §10): HistT/HistUsed/HistN,
   EvT/EvType/EvL/EvN, SnapT/SnapL/SnapN, plus CumL, LastTotal, CylBase (NaN = unset). All
   arrays are DOUBLE; EvType holds the sg_event numbers 1..4. ~224 KB: make it static. */
typedef struct {
    double cumL, lastTotal, cylBase;
    double histT[SG_CYLHIST], histUsed[SG_CYLHIST]; int histN;
    double evT[SG_LEDGER], evType[SG_LEDGER], evL[SG_LEDGER]; int evN;
    double snapT[SG_SNAPS], snapL[SG_SNAPS]; int snapN;
} sg_helium;

extern const char *const sg_state_names[SG_NSTATES];   /* "IDLE", ... */
extern const char *const sg_state_desc[SG_NSTATES];    /* reference STATES[].desc */

/* ---------------------------------------------------------------- lifecycle */
void   sg_default_params(sg_params *p);                /* defaultControllerParams + slots C, D */
/* Defaults, empty helium state, reinit, enter IDLE "IOC started" (which is logged through
   io.log). The IOC suppresses that line by passing io = NULL (or io.log = NULL) and setting
   c->io before sg_restart. */
void   sg_init(sg_ctl *c, const sg_io *io, double now);
/* Spec §8.15 steps 2-4 (JS restart()). Logs "IOC started (autosaved settings restored)", then
   the first branch that applies:
     1. MFC not connected or Setpoint_RBV not finite -> IDLE "restart: MFC not connected (§4.7)"
        (ruling R11: the controller must not take over a flow it cannot see)
     2. O2 valid, o2 < lidLevel and Setpoint_RBV > 0 -> REGULATE, lastCmd = Setpoint_RBV, FBON 1
     3. Setpoint_RBV <= 0                          -> IDLE "restart: setpoint is 0 (§4.7)"
     4. O2 not valid                               -> OPEN_LOOP "restart: O2 invalid (§4.7)"
     5. otherwise (O2 valid, o2 >= lidLevel, Setpoint_RBV > 0) -> PRECHECK "restart: O2 above lid
        threshold with the Alicat flowing: purge resumed" (G1, user decision 2026-09-29; the
        reference enters IDLE here)
   "Not configured" (§8.15 step 0) and the connection wait (step 1) are the glue's. */
void   sg_restart(sg_ctl *c, double now);

/* ---------------------------------------------------------------- per tick */
void   sg_set_inputs(sg_ctl *c, const sg_inputs *in);
void   sg_tick(sg_ctl *c, double now);                 /* JS tick() */
int    sg_pid_due(const sg_ctl *c, double now);        /* now % max(1, round(pidScan)) == 0 */
int    sg_pid_prepare(sg_ctl *c, sg_epid_cfg *cfg);    /* 0 if avgBuf is empty (skip the step);
                                                          else configEpid, CVAL = mean(avgBuf),
                                                          fill cfg */
double sg_pid_bumpless_i(const sg_epid_cfg *cfg);     /* clamp(OUTL - KP*(VAL - CVAL), DRVL, DRVH):
                                                          PID:Out for the start step (NaN if OUTL
                                                          is NaN; OUTL if the P term is not finite) */
void   sg_pid_done(sg_ctl *c, double oval);            /* epid.OVAL = oval; command(oval) if FBON.
                                                          A non-finite oval (pass NaN for a failed
                                                          epid process too) is never commanded
                                                          and does not replace epid.OVAL; with
                                                          FBON it raises the "cannot compute"
                                                          Mismatch source (ruling R12, spec §8.3) */
/* Spec §8.3, a failed Alicat put: failMsg = the alarm text ("MFC write failed: <PV> (pvStat <n>):
   controller cannot act") while a put channel's last put failed, NULL once none is failing. The
   glue calls it after each put (outside sg_tick); see sg_put_done in sgIoc.h. */
void   sg_mfc_write_status(sg_ctl *c, const char *failMsg);

/* ---------------------------------------------------------------- operator and admin */
int    sg_op_purge(sg_ctl *c);                         /* return 1 if accepted */
int    sg_op_flow_zero(sg_ctl *c);
/* Resume Flow (spec §8.5): OPEN_LOOP with o2ok -> HANDOFF (the reference's opResume);
   IDLE with o2ok, o2 < lidLevel, the MFC connected and a finite Setpoint_RBV -> REGULATE from
   the current setpoint. On rejection returns 0 and writes the rejection text to why (may be
   NULL). "Station configured" is the glue's condition. */
int    sg_op_resume_flow(sg_ctl *c, char *why, size_t n);
int    sg_op_idle(sg_ctl *c);
void   sg_set_target(sg_ctl *c, double v, const char *who);
void   sg_set_mode(sg_ctl *c, int mode, const char *who);        /* 0..3; others ignored */
void   sg_new_cylinder(sg_ctl *c, const char *who);
void   sg_mark_new_run(sg_ctl *c);                                /* ledger event + log */
void   sg_reset_override_count(sg_ctl *c);
/* Spec §8.21 steps 4-5 after Cfg:Apply (the glue checks IDLE, handles writeEnable and logs):
   O2 changed -> clear o2hist, rateHist, avgBuf, sameCount, aboveCount, maxRate, frozen, lastO2;
   clear O2Bad silently. MFC changed -> reset the mismatch tracking (spSeen, spChangeT,
   flowAtSpChange), the hold-monitor counters, the §8.18 disconnect counters; clear Mismatch,
   HoldStuck, Gas and Units silently; lastTotal = cylBase = unset and the usage log cleared, so the next
   tick takes the new totalizer as its reference. */
void   sg_channels_changed(sg_ctl *c, int o2Changed, int mfcChanged);

/* ---------------------------------------------------------------- helium autosave */
void   sg_helium_export(const sg_ctl *c, sg_helium *h);
/* Replace the helium state with an autosaved image. Counts are clamped to the capacities; an
   entry is dropped if its t (or its value) is not finite, if its t is earlier than the previous
   kept entry's, or (events) if its type is not 1..4. Non-finite lastTotal / cylBase restore as
   NaN (unset), a non-finite cumL as 0. Returns the number of entries dropped (0 = clean). */
int    sg_helium_import(sg_ctl *c, const sg_helium *h);

/* ---------------------------------------------------------------- derived values and texts */
double sg_expected_flow(const sg_ctl *c);
double sg_purge_timeout(const sg_ctl *c);
double sg_litres_at(const sg_ctl *c, double t);
void   sg_usage_report(const sg_ctl *c, sg_report *r);             /* He:Rep:* (spec §8.17) */
int    sg_worst_sev(const sg_ctl *c);
void   sg_forecast_text(const sg_ctl *c, char *buf, size_t n);   /* He:ForecastText */
void   sg_progress_text(const sg_ctl *c, char *buf, size_t n);   /* Sts:Progress */
void   sg_banner(const sg_ctl *c, char *buf, size_t n);          /* most severe first, "; ",
                                                                    or "No alarms" */

/* ---------------------------------------------------------------- alarms */
void   sg_set_alarm(sg_ctl *c, int k, int sev, const char *msg);
void   sg_clear_alarm(sg_ctl *c, int k, int silent);
void   sg_override(sg_ctl *c, const char *msg);

/* ---------------------------------------------------------------- core-internal
   Used by the core itself and by the replay and unit tests; the glue does not call these. */
void   sg_reinit(sg_ctl *c, double now);               /* JS reinit(): keeps params, helium
                                                          state and overrideCount */
void   sg_enter(sg_ctl *c, int state, const char *reason);   /* the glue uses sg_op_* instead */
void   sg_ledger_event(sg_ctl *c, int type);           /* the glue uses sg_new_cylinder and
                                                          sg_mark_new_run instead */
void   sg_latch(sg_ctl *c, int k, int sev, const char *msg, double dur);
void   sg_expire_alarms(sg_ctl *c);
void   sg_debounce(sg_ctl *c, int k, int level, const char *msg1, const char *msg2, double delay);
/* Logging helper used throughout the core: formats with vsnprintf and calls io.log. */
void   sg_log(sg_ctl *c, int sev, const char *fmt, ...) SG_PRINTF(3, 4);
#endif
