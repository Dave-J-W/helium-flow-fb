/* sgCore.c: controller data model, state entry and commands. A port of class Controller in
   simulator/sample_gas_simulator.html (line 1029 on); function names follow the reference.
   Reference null is represented as NaN throughout (lastCmd, cylBase, lastTotal, sd fields). */
#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include "sgCoreInt.h"
#include "sgFmt.h"

const char *const sg_state_names[SG_NSTATES] = {
    "IDLE", "PRECHECK", "PURGE", "HANDOFF", "REGULATE", "OPEN_LOOP", "FLOW_ZERO", "OPEN_STOP"
};
const char *const sg_state_desc[SG_NSTATES] = {   /* reference STATES (line 815) */
    "Controller is not managing the flow.",
    "Checking and correcting MFC settings before the purge (§4.1).",
    "Purging at full flow until O2 < target − Δ (§4.2).",
    "Stepping down to the expected flow, then enabling feedback (§4.4).",
    "epid is holding O2 at the target (§4.5).",
    "O2 unavailable: fixed expected flow, no feedback (§4.3, §5).",
    "Flow stopped by the operator.",
    "Enclosure confirmed open: flow stopped to save helium (§4.6).",
};

static const char *stateName(int s)
{
    return (s >= 0 && s < SG_NSTATES) ? sg_state_names[s] : "—";
}

/* ---------------------------------------------------------------- logging */
void sg_log(sg_ctl *c, int sev, const char *fmt, ...)
{
    char buf[1024];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof buf, fmt, ap);
    va_end(ap);
    if (c->io.log) c->io.log(c->io.ctx, c->now, sev, buf);
}

/* ---------------------------------------------------------------- parameters */
static void setMode(sg_mode *m, const char *name, double baseFlow, double n, double KP,
                    double KI, double drvh, double drvl)
{
    snprintf(m->name, sizeof m->name, "%s", name);
    m->baseFlow = baseFlow; m->n = n; m->KP = KP; m->KI = KI; m->drvh = drvh; m->drvl = drvl;
}

/* defaultControllerParams (line 831) and the mode table of spec §7.4 */
void sg_default_params(sg_params *p)
{
    memset(p, 0, sizeof *p);
    p->target = 0.99; p->tol = 0.02; p->delta = -0.04; p->mode = 0;
    p->purgeLag = 12;
    setMode(&p->modes[0], "Normal lid", 0.25, 1.0, -9.3, 8.8e-4, 1.0, 0.05);
    setMode(&p->modes[1], "Collimator lid", 0.84, 0.53, -10, 1.4e-3, 2.0, 0.3);
    setMode(&p->modes[2], "Spare C", 0.25, 1.0, -9.3, 8.8e-4, 1.0, 0.05);
    setMode(&p->modes[3], "Spare D", 0.25, 1.0, -9.3, 8.8e-4, 1.0, 0.05);
    p->purgeFlow = 20; p->dropSkipLevel = 18;
    p->purgeTimeoutMargin = 1.3;
    p->purgeTimeoutMin = 120; p->purgeTimeoutMax = 1800;
    p->ambientRef = 19.4;
    p->V = 41;
    p->lidOnsetFrac = 0.02;
    p->lidOnsetMax = 30;
    p->lidWindow = 15;
    p->openSlopeFrac = 0.5;
    p->lidCurvMin = 0.8;
    p->cylCapacityL = 8000;
    p->cylWarnH = 24; p->cylAlarmH = 6;
    p->reportDays = 60;
    p->runGap = 216000;
    p->handoffHold = 5; p->handoffFlowTol = 0.05; p->fbDelay = 0;
    p->lidLevel = 10; p->lidFilter = 5; p->lidSlope = 0.2; p->lidSlopeWindow = 60; p->lidArmLevel = 9;
    p->rampMaxPurge = 5; p->rampMinHandoff = 2;
    p->hardCeiling = 2.0;
    p->mismatchAbs = 0.05; p->mismatchFrac = 0.05; p->mismatchMargin = 5;
    p->holdDetect = 3; p->holdRetries = 3; p->holdRetryInterval = 10; p->holdSlowRetry = 60;
    p->frozenTime = 30; p->o2Min = -0.5; p->o2Max = 25;
    p->avgN = 10; p->pidScan = 10; p->odel = 0.01;
    p->gainSchedule = 1;
    p->fineBand = 0.01;
    p->fineKPx = 0.5; p->fineKIx = 1.0;
    p->flowMinorX = 1.5; p->flowMajorX = 2.0; p->flowLowX = 0.5; p->pinnedTime = 600;
    p->o2AbnormalOffset = 0.3;
    p->alarmDelay = 10;
    p->progressMin = 0.0005;
    p->stallGrace = 300;
    p->flowSteadyBand = 0.02;
    p->o2SteadyRate = 0.002;
    p->stallWindow = 120;
    p->slopeAvgN = 20;
    p->stallTime = 120;
    p->settleTimeout = 7200;
    p->flowAlarmDelay = 300;
}

/* ---------------------------------------------------------------- init / reinit */
static void sdClear(sg_sd *sd)
{
    sd->t0 = sd->o2Start = sd->timerT0 = sd->below = sd->elapsed = sd->onsetT = sd->cOn =
        sd->kin = sd->curv = sd->kObs = sd->checkAt = sd->cCheck = sd->est = sd->timeout =
        sd->t1 = NAN;
    sd->dropChecked = 0; sd->lidResult = SG_LID_PENDING; sd->phaseDelay = 0;
}

void sg_init(sg_ctl *c, const sg_io *io, double now)
{
    memset(c, 0, sizeof *c);
    sg_default_params(&c->p);
    if (io) c->io = *io;
    c->in.o2 = NAN; c->in.o2Sevr = 3; c->in.running = 1; c->in.mfcConnected = 1;
    snprintf(c->in.gas, sizeof c->in.gas, "He");
    c->overrideCount = 0; c->nOverride = 0;
    c->settleT0 = 0; c->inFine = 0;
    /* empty helium state (constructor, line 1035) */
    c->cylBase = NAN; c->nCyl = 0; c->nCylEst = 0; c->cylMedianH = NAN; c->cylLeftL = NAN;
    c->cylMinH = NAN; c->cylMaxH = NAN;
    c->cumL = 0; c->lastTotal = NAN; c->nLedger = 0; c->nSnaps = 0; c->ledgerSeq = 0;
    sg_reinit(c, now);
    sg_enter(c, SG_IDLE, "IOC started");
}

/* reinit (line 1042). Keeps params, io, inputs, helium state, overrideCount and the override
   list; like the reference it also leaves settleT0 and inFine alone. */
void sg_reinit(sg_ctl *c, double now)
{
    int k;
    c->now = now; c->state = SG_NONE; sdClear(&c->sd);
    for (k = 0; k < SG_NALARMS; k++) { c->alarms[k].active = 0; c->deb[k].used = 0; }
    c->lastAction[0] = '\0';
    c->o2 = NAN; c->o2ok = 0; c->lastO2 = NAN; c->sameCount = 0; c->frozen = 0;
    c->nO2 = 0; c->nRate = 0; c->maxRate = 0; c->aboveCount = 0; c->lidArmed = 0;
    c->nAvg = 0; c->blind = 0;
    c->lastCmd = NAN; c->lastCmdTime = 0; c->flowAtCmd = 0;
    c->spSeen = NAN; c->spChangeT = 0; c->flowAtSpChange = 0;
    c->holdSec = 0; c->holdAttempts = 0; c->nextHoldAttempt = 0; c->holdRecovering = 0;
    c->pinnedSec = 0; c->pinnedLowSec = 0; c->heartbeat = 0;
    c->settling = 0; c->settleDir = 0; c->stallSec = 0; c->towardRate = 0; c->stallLatched = 0;
    c->nOval = 0; c->o2Slope = 0;
    c->mfcDisconnSec = 0; c->mfcDisconnAlarm = 0; c->badCmd = 0; c->flowSteady = 0;
    c->epid.VAL = c->p.target; c->epid.CVAL = NAN; c->epid.KP = 0; c->epid.KI = 0;
    c->epid.DRVL = 0; c->epid.DRVH = 0; c->epid.ODEL = c->p.odel; c->epid.FBON = 0;
    c->epid.OVAL = 0;
}

void sg_set_inputs(sg_ctl *c, const sg_inputs *in) { c->in = *in; }

/* Spec §8.21 steps 4-5 (not in the reference): forget what the old channels produced. The log
   lines, writeEnable and the IDLE-only rule of §8.21 are the glue's. lastO2 also goes (it is the
   frozen detector's and ruling R13's previous value, taken from the old channel). */
void sg_channels_changed(sg_ctl *c, int o2Changed, int mfcChanged)
{
    if (o2Changed) {
        c->nO2 = 0; c->nRate = 0; c->nAvg = 0;
        c->sameCount = 0; c->aboveCount = 0; c->maxRate = 0; c->frozen = 0; c->lastO2 = NAN;
        sg_clear_alarm(c, SG_A_O2BAD, 1);
    }
    if (mfcChanged) {
        c->spSeen = NAN; c->spChangeT = 0; c->flowAtSpChange = 0;
        c->holdSec = 0; c->holdAttempts = 0; c->nextHoldAttempt = 0; c->holdRecovering = 0;
        c->mfcDisconnSec = 0; c->mfcDisconnAlarm = 0;
        sg_clear_alarm(c, SG_A_MISMATCH, 1);
        sg_clear_alarm(c, SG_A_HOLDSTUCK, 1);
        sg_clear_alarm(c, SG_A_GAS, 1);
        c->lastTotal = NAN; c->cylBase = NAN; c->nCyl = 0;   /* re-baseline on the new totalizer */
        /* The glue calls this mid-tick, after sg_set_inputs: this tick's Total_RBV is still the
           old Alicat's. Drop it, or the ledger and the forecast baseline on it, and the new
           totalizer's first reading then logs "went backwards" (or, if higher, counts the
           difference as usage). Acceptance run, 2026-09-29. */
        c->in.total = NAN;
    }
}

/* ---------------------------------------------------------------- helpers */
/* expectedFlow (line 1100) */
double sg_expected_flow(const sg_ctl *c)
{
    const sg_mode *m = &c->p.modes[(c->p.mode >= 0 && c->p.mode < 4) ? c->p.mode : 0];
    return m->baseFlow * pow(0.99 / c->p.target, m->n);
}

/* command (line 1101): v = Math.max(0, v), lastCmd bookkeeping, put.
   Ruling R12 (beyond the reference): a non-finite v (NaN, ±Inf, e.g. from a bad parameter or a
   bad PID output) is never commanded: lastCmd is kept, nothing is put, and MAJOR
   "command ignored: flow value is not a number" is logged once per run of such calls (badCmd;
   HANDOFF and OPEN_LOOP command every tick). -0 is normalised to +0. */
void sg_command(sg_ctl *c, double v)
{
    if (!isfinite(v)) {
        if (!c->badCmd) sg_log(c, 2, "command ignored: flow value is not a number");
        c->badCmd = 1;
        return;
    }
    c->badCmd = 0;
    if (!(v > 0)) v = 0;                      /* negative and -0 -> +0 */
    if (isnan(c->lastCmd) || fabs(v - c->lastCmd) > 1e-9) {
        c->lastCmd = v; c->lastCmdTime = c->now; c->flowAtCmd = c->in.flow;
    }
    if (c->io.put_setpoint) c->io.put_setpoint(c->io.ctx, v);
}

/* purgeTimeout (line 1120): kinetic purge time to target − Δ, × margin, clamped */
double sg_purge_timeout(const sg_ctl *c)
{
    const sg_params *p = &c->p;
    const sg_sd *sd = &c->sd;
    double goal = sg_jmax(0.05, p->target - p->delta);
    double extra = p->purgeLag + p->handoffHold;
    double t;
    if (sd->kObs > 0 && !isnan(sd->checkAt)) {   /* after the lid check: measured decay */
        t = sd->checkAt + p->purgeTimeoutMargin *
            (log(sg_jmax(sd->cCheck, goal) / goal) / sd->kObs + extra);
    } else {
        double c0 = (c->blind || !(sd->o2Start > 0)) ? p->ambientRef : sd->o2Start;
        double k = p->purgeFlow / p->V / 60;
        t = p->purgeTimeoutMargin * (log(sg_jmax(c0, goal) / goal) / k + extra);
    }
    return sg_jclamp(t, p->purgeTimeoutMin, p->purgeTimeoutMax);
}

/* startSettling (line 1086) */
static void startSettling(sg_ctl *c, const char *why)
{
    const sg_params *p = &c->p;
    char tb[64];
    c->stallSec = 0; c->stallLatched = 0;
    if (isfinite(c->o2) && fabs(c->o2 - p->target) <= p->tol) { c->settling = 0; return; }
    c->settling = 1; c->settleT0 = c->now; sg_clear_alarm(c, SG_A_NOTREACHED, 1);
    if (isfinite(c->o2)) {
        double d = p->target - c->o2;
        c->settleDir = d > 0 ? 1 : d < 0 ? -1 : 0;
    } else c->settleDir = 0;
    sg_log(c, 0, "settling (%s): O2 %s toward %s %%", why, c->settleDir < 0 ? "falling" : "rising",
           sg_fmtN(tb, sizeof tb, p->target, 3));
}

/* setTarget (line 1082) */
void sg_set_target(sg_ctl *c, double v, const char *who)
{
    char vb[64];
    c->p.target = v;
    sg_log(c, 0, "%s: O2 target → %s %%", who, sg_fmtJs(vb, sizeof vb, v));
    if (c->state == SG_REGULATE) startSettling(c, "target change");
}

/* ---------------------------------------------------------------- state entry */
/* enter (line 1135) */
void sg_enter(sg_ctl *c, int s, const char *reason)
{
    static const int cleared[] = { SG_A_FLOWHIGH, SG_A_FLOWLOW, SG_A_PINNED, SG_A_PINNEDLOW,
                                   SG_A_O2HIGH, SG_A_OPENLOOP, SG_A_NOTREACHED };
    const sg_params *p = &c->p;
    int prev = c->state;
    size_t i;
    c->state = s; sdClear(&c->sd); c->sd.t0 = c->now;
    if (s != SG_REGULATE) c->epid.FBON = 0;
    for (i = 0; i < sizeof cleared / sizeof cleared[0]; i++) {
        sg_clear_alarm(c, cleared[i], 1);
        c->deb[cleared[i]].used = 0;
    }
    c->pinnedSec = 0; c->pinnedLowSec = 0; c->settling = 0;
    if (s == SG_REGULATE) startSettling(c, "feedback on");
    /* lastAction is display text: truncating a long reason is acceptable */
    if (snprintf(c->lastAction, sizeof c->lastAction, "%s: %s", stateName(s), reason) < 0)
        c->lastAction[0] = '\0';
    sg_log(c, 0, "%s → %s (%s)", stateName(prev), stateName(s), reason);
    switch (s) {
    case SG_PRECHECK:
        sg_clear_alarm(c, SG_A_OPENSTOP, 1); sg_clear_alarm(c, SG_A_PURGEINC, 1);
        break;
    case SG_PURGE:
        sg_ledger_event(c, SG_EV_PURGE);
        c->sd.o2Start = c->o2; c->sd.timerT0 = NAN; c->sd.dropChecked = 0; c->sd.below = 0;
        c->sd.elapsed = 0; c->sd.onsetT = NAN; c->sd.cOn = NAN; c->sd.kin = NAN;
        c->sd.lidResult = SG_LID_PENDING;
        c->lidArmed = 0; sg_command(c, p->purgeFlow);
        break;
    case SG_HANDOFF: {
        double ramp = c->in.ramp;
        if (ramp > 0 && ramp < p->rampMinHandoff) {
            char rb[64], mb[64], msg[SG_MSG];
            if (c->io.put_ramp) c->io.put_ramp(c->io.ctx, p->rampMinHandoff);
            snprintf(msg, sizeof msg, "ramp rate %s → %s SLPM/s (handoff minimum)",
                     sg_fmtN(rb, sizeof rb, ramp, 1), sg_fmtJs(mb, sizeof mb, p->rampMinHandoff));
            sg_override(c, msg);
        }
        c->sd.phaseDelay = 0;                 /* phase = 'settle' */
        sg_command(c, sg_expected_flow(c));
        break;
    }
    case SG_OPEN_LOOP:
        sg_command(c, sg_expected_flow(c));
        sg_set_alarm(c, SG_A_OPENLOOP, 2, "O2 unavailable: running blind at fixed flow");
        break;
    case SG_FLOW_ZERO: case SG_OPEN_STOP:
        sg_ledger_event(c, SG_EV_ZERO); sg_command(c, 0);
        break;
    default:
        break;
    }
}

/* ---------------------------------------------------------------- per-tick parts */
/* JS arr.push(v); while (arr.length > cap) arr.shift(); with a fixed C array of size `size`.
   cap is a parameter (it may shrink between calls), clamped to the array size. Declared in
   sgCoreInt.h (sgChecks.c uses it for ovalHist). */
void sg_push_capped(double *arr, int *n, int size, double v, double cap)
{
    int lim;
    if (*n >= size) {                         /* array full: drop the oldest to make room */
        memmove(&arr[0], &arr[1], (size_t)(size - 1) * sizeof arr[0]);
        *n = size - 1;
    }
    arr[(*n)++] = v;
    /* integer length > cap <=> length > floor(cap); a NaN cap never trims (length > NaN is false) */
    lim = (isnan(cap) || cap >= size) ? size : (cap > 0 ? (int)floor(cap) : 0);
    if (*n > lim) {
        int drop = *n - lim;
        memmove(&arr[0], &arr[drop], (size_t)(*n - drop) * sizeof arr[0]);
        *n -= drop;
    }
}

/* readInputs (line 1189).
   Ruling R13 (beyond the reference, whose analyzer holds its last value while INVALID): a
   non-finite O2 value counts as severity INVALID and the previous tick's value (lastO2) is used
   in its place, so no NaN reaches o2hist, where it would make the 10 s rate NaN and blind the lid
   detector for about 70 s after the reading recovers. Before any finite reading (lastO2 NaN)
   nothing is appended to o2hist. */
static void readInputs(sg_ctl *c)
{
    const sg_params *p = &c->p;
    double v = c->in.o2, rate;
    int sevr = c->in.o2Sevr, n, i;
    if (!isfinite(v)) { sevr = 3; v = c->lastO2; }
    c->o2 = v;
    if (sevr < 3 && v == c->lastO2) c->sameCount++; else c->sameCount = 0;
    c->lastO2 = v;
    c->frozen = c->sameCount >= p->frozenTime;
    c->o2ok = sevr < 3 && v >= p->o2Min && v <= p->o2Max && !c->frozen;
    if (!isnan(v))
        sg_push_capped(c->o2hist, &c->nO2, SG_O2HIST, v,
                       sg_jmax(90, p->stallWindow + p->slopeAvgN + 5));
    n = c->nO2;
    rate = n > 10 ? (v - c->o2hist[n - 11]) / 10 : 0;   /* 10 s difference, %/s */
    sg_push_capped(c->rateHist, &c->nRate, SG_RATEHIST, c->o2ok ? rate : 0, p->lidSlopeWindow);
    c->maxRate = 0;                                   /* Math.max(0, ...rateHist) */
    for (i = 0; i < c->nRate; i++) c->maxRate = sg_jmax(c->maxRate, c->rateHist[i]);
    c->aboveCount = (c->o2ok && v > p->lidLevel) ? c->aboveCount + 1 : 0;
    if (c->o2ok) sg_push_capped(c->avgBuf, &c->nAvg, SG_AVGBUF, v, p->avgN);
    if (!c->o2ok) sg_set_alarm(c, SG_A_O2BAD, 2, c->frozen ? "O2 reading frozen" : "O2 reading invalid");
    else sg_clear_alarm(c, SG_A_O2BAD, 0);
}

/* stopOpen (line 1217) */
static void stopOpen(sg_ctl *c, const char *msg)
{
    char m[SG_MSG];
    snprintf(m, sizeof m, "%s", msg);         /* msg may point into state that enter() resets */
    sg_enter(c, SG_OPEN_STOP, m);
    sg_set_alarm(c, SG_A_OPENSTOP, 2, m);
}

/* lidDetector (line 1207), spec §4.6. Returns 1 if it stopped the flow. */
static int lidDetector(sg_ctl *c)
{
    const sg_params *p = &c->p;
    int s = c->state, armed;
    if (s == SG_PURGE && c->o2ok && c->o2 < p->lidArmLevel) c->lidArmed = 1;
    armed = s == SG_HANDOFF || s == SG_REGULATE || (s == SG_PURGE && c->lidArmed);
    if (armed && c->o2ok && c->aboveCount >= p->lidFilter && c->maxRate >= p->lidSlope) {
        char ob[64], rb[64], msg[SG_MSG];
        snprintf(msg, sizeof msg, "enclosure opened, flow stopped (O2 %s %%, rise %s %%/s)",
                 sg_fmtN(ob, sizeof ob, c->o2, 1), sg_fmtN(rb, sizeof rb, c->maxRate, 2));
        stopOpen(c, msg);
        return 1;
    }
    return 0;
}

/* holdMonitor (line 1307), spec §4.6.2 */
static void holdMonitor(sg_ctl *c)
{
    const sg_params *p = &c->p;
    if (!c->in.running) {
        c->holdSec++;
        if (c->holdAttempts >= p->holdRetries) sg_set_alarm(c, SG_A_HOLDSTUCK, 2, "MFC on hold, cannot resume");
        if (c->holdSec >= p->holdDetect && c->now >= c->nextHoldAttempt) {
            if (c->io.put_run) c->io.put_run(c->io.ctx);
            c->holdAttempts++; c->holdRecovering = 1;
            sg_log(c, 1, "MFC on hold: wrote Run (attempt %d)", c->holdAttempts);
            c->nextHoldAttempt = c->now + (c->holdAttempts < p->holdRetries ? p->holdRetryInterval
                                                                           : p->holdSlowRetry);
        }
    } else {
        if (c->holdRecovering) {
            char lb[64], msg[SG_MSG];
            c->holdRecovering = 0;
            /* re-send: writes during hold never reached the device */
            if (!isnan(c->lastCmd) && c->io.put_setpoint) c->io.put_setpoint(c->io.ctx, c->lastCmd);
            /* Beyond the reference (acceptance sc08): the flow restarts from where the hold left
               it, but the unchanged Setpoint_RBV would not restart the flow-mismatch timer, whose
               ramp allowance has long expired; restart it here, as a setpoint change would. */
            c->spChangeT = c->now; c->flowAtSpChange = c->in.flow;
            sg_clear_alarm(c, SG_A_HOLDSTUCK, 0);
            snprintf(msg, sizeof msg, "MFC was on hold, resumed; setpoint %s re-sent",
                     sg_fmtN(lb, sizeof lb, c->lastCmd, 2));
            sg_override(c, msg);
        }
        c->holdSec = 0; c->holdAttempts = 0; c->nextHoldAttempt = 0;
    }
}

/* doPrecheck (line 1328), spec §4.1: correct and warn, never refuse */
static void doPrecheck(sg_ctl *c)
{
    const sg_params *p = &c->p;
    const sg_inputs *r = &c->in;
    if (!r->running) {
        if (c->io.put_run) c->io.put_run(c->io.ctx);
        c->holdRecovering = 1;
        sg_log(c, 1, "PRECHECK: MFC on hold, wrote Run");
    }
    if (r->ramp > p->rampMaxPurge || r->ramp == 0) {
        char rb[64], mb[64], msg[SG_MSG];
        if (c->io.put_ramp) c->io.put_ramp(c->io.ctx, p->rampMaxPurge);
        snprintf(msg, sizeof msg, "ramp rate %s → %s SLPM/s (purge maximum)",
                 r->ramp == 0 ? "0 (= no ramp, instant)" : sg_fmtN(rb, sizeof rb, r->ramp, 1),
                 sg_fmtJs(mb, sizeof mb, p->rampMaxPurge));
        sg_override(c, msg);
    }
    if (strcmp(r->gas, "He") != 0) {
        char msg[SG_MSG];
        snprintf(msg, sizeof msg, "MFC gas table is %s, not He (flow reading wrong)", r->gas);
        sg_set_alarm(c, SG_A_GAS, 1, msg);
    } else sg_clear_alarm(c, SG_A_GAS, 0);
    c->blind = !c->o2ok;
    sg_enter(c, SG_PURGE, c->blind ? "blind purge: O2 unavailable, timer only" : "purge started");
}

/* o2hist[i] as JS reads it: out of range is undefined, which becomes NaN in arithmetic. */
static double o2histAt(const sg_ctl *c, double i)
{
    if (!(i >= 0) || i >= c->nO2 || i != floor(i)) return NAN;
    return c->o2hist[(int)i];
}

/* doPurge (line 1340), spec §4.2 / §4.3, including the lid inference from purge kinetics */
static void doPurge(sg_ctl *c)
{
    const sg_params *p = &c->p;
    const sg_inputs *r = &c->in;
    sg_sd *sd = &c->sd;
    double el, k;
    char b1[64], b2[64], b3[64], b4[64], b5[64], msg[SG_MSG];
    if (isnan(sd->timerT0) && (r->flow >= 0.95 * p->purgeFlow || c->now - sd->t0 >= 30)) sd->timerT0 = c->now;
    if (!c->o2ok && !c->blind) {
        c->blind = 1;
        sg_log(c, 2, "O2 lost during purge: continuing on the timer (blind)");
    }
    el = isnan(sd->timerT0) ? 0 : c->now - sd->timerT0;
    sd->elapsed = el;
    sd->timeout = sg_purge_timeout(c);
    if (c->blind) {
        if (el >= sd->timeout) sg_enter(c, SG_OPEN_LOOP, "blind purge complete");
        return;
    }
    /* Lid inference (§4.2): onset-based decay rate over lidWindow s against the lid-on F/V. */
    if (!sd->dropChecked && !isnan(sd->timerT0)) {
        if (!(sd->o2Start >= p->dropSkipLevel)) {    /* true for NaN, as in the reference */
            sd->dropChecked = 1; sd->lidResult = SG_LID_SKIPPED;
            sg_log(c, 0, "lid check skipped: purge started at %s %% (< %s %%)",
                   sg_fmtN(b1, sizeof b1, sd->o2Start, 2), sg_fmtJs(b2, sizeof b2, p->dropSkipLevel));
        } else {
            if (isnan(sd->onsetT) && c->o2 <= sd->o2Start * (1 - p->lidOnsetFrac)) {
                sd->onsetT = el; sd->cOn = c->o2;
            }
            if (isnan(sd->onsetT) && el >= p->lidOnsetMax) {
                sd->dropChecked = 1; sd->kin = 0; sd->lidResult = SG_LID_OPEN;
                snprintf(msg, sizeof msg, "no O2 decay within %s s of full flow: enclosure open?",
                         sg_fmtJs(b1, sizeof b1, p->lidOnsetMax));
                stopOpen(c, msg);
                return;
            }
            if (!isnan(sd->onsetT) && el >= sd->onsetT + p->lidWindow) {
                double kExp = p->purgeFlow / p->V / 60;          /* 1/s, lid on */
                double span = el - sd->onsetT, half = floor(span / 2);
                int n = c->nO2;
                double cMid = o2histAt(c, n - 1 - (span - half));
                double kObs = log(sd->cOn / c->o2) / span;
                double k1 = log(sd->cOn / cMid) / half, k2 = log(cMid / c->o2) / (span - half);
                double ratio = kObs / kExp, curv = k1 > 0 ? k2 / k1 : 0;
                sd->dropChecked = 1;
                sd->kin = ratio; sd->curv = curv; sd->kObs = kObs; sd->checkAt = el; sd->cCheck = c->o2;
                if (ratio < p->openSlopeFrac || curv < p->lidCurvMin) {
                    sd->lidResult = SG_LID_OPEN;
                    snprintf(msg, sizeof msg,
                             "purge decay %s %% of the lid-on rate, curvature %s: enclosure open?",
                             sg_fmtN(b1, sizeof b1, 100 * ratio, 0), sg_fmtN(b2, sizeof b2, curv, 2));
                    stopOpen(c, msg);
                    return;
                }
                sd->lidResult = SG_LID_PASSED;
                sg_log(c, 0, "lid check passed at %s s: decay %s %% of the lid-on rate (open < %s %%), "
                       "curvature %s (open < %s)",
                       sg_fmtJs(b1, sizeof b1, el), sg_fmtN(b2, sizeof b2, 100 * ratio, 0),
                       sg_fmtN(b3, sizeof b3, 100 * p->openSlopeFrac, 0), sg_fmtN(b4, sizeof b4, curv, 2),
                       sg_fmtJs(b5, sizeof b5, p->lidCurvMin));
            }
        }
    }
    /* Lag-compensated handoff: bulk ≈ reading × exp(−k·lag); k from the lid check, else F/V. */
    k = sd->kObs > 0 ? sd->kObs : p->purgeFlow / p->V / 60;
    sd->est = c->o2 * exp(-k * p->purgeLag);
    if (sd->est < p->target - p->delta) sd->below++; else sd->below = 0;
    if (sd->below >= p->handoffHold) {
        snprintf(msg, sizeof msg, "lag-corrected O2 %s %% < target − Δ for %s s (reading %s %%)",
                 sg_fmtN(b1, sizeof b1, sd->est, 3), sg_fmtJs(b2, sizeof b2, p->handoffHold),
                 sg_fmtN(b3, sizeof b3, c->o2, 3));
        sg_enter(c, SG_HANDOFF, msg);
        return;
    }
    if (el >= sd->timeout) {
        snprintf(msg, sizeof msg, "purge incomplete: timeout (%s) reached before target − Δ",
                 sg_fmtT(b1, sizeof b1, sd->timeout));
        sg_latch(c, SG_A_PURGEINC, 1, msg, 300);
        sg_enter(c, SG_HANDOFF, "purge timeout");
    }
}

/* doHandoff (line 1391), spec §4.4 */
static void doHandoff(sg_ctl *c)
{
    const sg_params *p = &c->p;
    sg_sd *sd = &c->sd;
    double expf;
    if (!c->o2ok) { sg_enter(c, SG_OPEN_LOOP, "O2 unavailable during handoff"); return; }
    expf = sg_expected_flow(c);
    sg_command(c, expf);
    if (!sd->phaseDelay) {                    /* phase 'settle' */
        if (fabs(c->in.flow - expf) <= p->handoffFlowTol || c->now - sd->t0 > 30) {
            sd->phaseDelay = 1; sd->t1 = c->now;
        }
    } else if (c->now - sd->t1 >= p->fbDelay) {
        sg_enter(c, SG_REGULATE, "feedback on");
        sg_config_epid(c); c->epid.FBON = 1;
    }
}

/* Spec §8.18, loss of the Alicat connection (not in the reference, whose Alicat never
   disconnects; mfcConnected is always 1 there, so this does nothing in the replay). Outside IDLE,
   more than holdDetect consecutive disconnected ticks raise Mismatch MAJOR; the state machine goes
   on and still requests puts, which the IOC layer drops while disconnected. On the first connected
   tick after ANY disconnected tick, lastCmd (if finite) is re-sent, as the hold monitor does
   (§8.7): a one-shot command (Flow Zero's command(0), the purge entry's command(purgeFlow)) made
   during even a short blip would otherwise be lost (ruling R8). A short blip (<= holdDetect ticks)
   re-sends silently; after the alarm the re-send clears it and is logged. In IDLE the controller
   does not own the flow: no alarm and no re-send. State: mfcDisconnSec, mfcDisconnAlarm. */
static void mfcLink(sg_ctl *c)
{
    static const char MSG[] = "MFC not responding (CA disconnected)";
    if (c->state == SG_IDLE) {
        if (c->mfcDisconnAlarm) sg_clear_alarm(c, SG_A_MISMATCH, 1);
        c->mfcDisconnSec = 0; c->mfcDisconnAlarm = 0;
        return;
    }
    if (!c->in.mfcConnected) {
        c->mfcDisconnSec++;
        if ((double)c->mfcDisconnSec > c->p.holdDetect) {
            sg_set_alarm(c, SG_A_MISMATCH, 2, MSG);   /* logs only when it changes */
            c->mfcDisconnAlarm = 1;
        }
        return;
    }
    if (c->mfcDisconnSec > 0) {
        if (isfinite(c->lastCmd) && c->io.put_setpoint) c->io.put_setpoint(c->io.ctx, c->lastCmd);
        if (c->mfcDisconnAlarm) {
            char lb[64];
            sg_clear_alarm(c, SG_A_MISMATCH, 0);
            if (isfinite(c->lastCmd))
                sg_log(c, 0, "MFC reconnected: setpoint %s re-sent", sg_fmtN(lb, sizeof lb, c->lastCmd, 2));
            else sg_log(c, 0, "MFC reconnected");
        }
    }
    c->mfcDisconnSec = 0; c->mfcDisconnAlarm = 0;
}

/* ---------------------------------------------------------------- tick */
/* tick (line 1166). The IOC never calls tick while "down": a crashed IOC runs no code at all.
   Additions beyond the reference: mfcLink (spec §8.18). */
void sg_tick(sg_ctl *c, double now)
{
    c->now = now;
    c->heartbeat++;
    readInputs(c);
    sg_expire_alarms(c);
    mfcLink(c);
    if (c->state != SG_IDLE) holdMonitor(c);
    if (!lidDetector(c)) {
        switch (c->state) {
        case SG_PRECHECK: doPrecheck(c); break;
        case SG_PURGE: doPurge(c); break;
        case SG_HANDOFF: doHandoff(c); break;
        case SG_REGULATE:
            if (!c->o2ok) sg_enter(c, SG_OPEN_LOOP, c->frozen ? "O2 reading frozen" : "O2 reading invalid");
            break;
        case SG_OPEN_LOOP: sg_command(c, sg_expected_flow(c)); break;
        default: break;
        }
    }
    sg_ledger_update(c);                      /* both skip a non-finite Total_RBV (ruling R7) */
    sg_cyl_forecast(c);
    sg_alarm_checks(c);
}

/* restart (line 1492), spec §8.15 steps 2-4; the log text is the spec's (§8.15 step 3).
   Ruling R11 (beyond the reference, whose Alicat is always connected): with the MFC disconnected
   or a non-finite Setpoint_RBV the controller cannot see the flow it would take over, so this is
   checked first and enters IDLE. epid.OVAL is left at reinit's 0, as in the reference (the
   replay compares it, and alarmChecks reads it until the first PID step); the record's bumpless
   start comes from sg_epid_cfg.OUTL = lastCmd (sgCore.h, sg_pid_prepare). */
void sg_restart(sg_ctl *c, double now)
{
    const sg_params *p = &c->p;
    const sg_inputs *in = &c->in;
    int valid;
    sg_reinit(c, now);
    c->now = now;
    valid = in->o2Sevr < 3 && in->o2 >= p->o2Min && in->o2 <= p->o2Max;
    sg_log(c, 0, "IOC started (autosaved settings restored)");
    if (!in->mfcConnected || !isfinite(in->sp)) sg_enter(c, SG_IDLE, "restart: MFC not connected (§4.7)");
    else if (valid && in->o2 < p->lidLevel && in->sp > 0) {
        c->lastCmd = in->sp; c->lastCmdTime = now; c->flowAtCmd = in->flow;
        sg_enter(c, SG_REGULATE, "restart: O2 valid and setpoint > 0, resume regulation (§4.7)");
        sg_config_epid(c); c->epid.FBON = 1;
    } else if (in->sp <= 0) sg_enter(c, SG_IDLE, "restart: setpoint is 0 (§4.7)");
    else if (!valid) sg_enter(c, SG_OPEN_LOOP, "restart: O2 invalid (§4.7)");
    else sg_enter(c, SG_IDLE, "restart: O2 above lid threshold (§4.7)");
}

/* ---------------------------------------------------------------- operator actions */
/* opPurge, opFlowZero, opIdle (lines 1483-1486); "down" is the IOC not running, so it has no
   counterpart here. */
int sg_op_purge(sg_ctl *c)
{
    if (c->state == SG_PRECHECK || c->state == SG_PURGE) return 0;
    sg_enter(c, SG_PRECHECK, "operator pressed Purge");
    return 1;
}

int sg_op_flow_zero(sg_ctl *c)
{
    sg_enter(c, SG_FLOW_ZERO, "operator pressed Flow Zero");
    return 1;
}

int sg_op_idle(sg_ctl *c)
{
    sg_enter(c, SG_IDLE, "admin released control");
    return 1;
}

/* Resume Flow (spec §8.5). From OPEN_LOOP: the reference's opResume (line 1485), with the log
   reason "operator pressed Resume Flow". From IDLE (new; the spec reuses the resume branch of
   restart, lines 1498-1501): with o2ok and o2 < lidLevel (checked first), and the MFC connected
   with a finite Setpoint_RBV (ruling R9), regulate from the current Setpoint_RBV
   without a put at the switch; PID:Out = lastCmd so epid starts bumplessly. "Station configured"
   is the IOC layer's condition. Rejections return 0 with the spec's text in why. */
int sg_op_resume_flow(sg_ctl *c, char *why, size_t n)
{
    char ob[64], lb[64];
    if (why && n) why[0] = '\0';
    if (c->state == SG_OPEN_LOOP || c->state == SG_IDLE) {
        if (!c->o2ok) {
            if (why && n) snprintf(why, n, "Resume Flow ignored: O2 invalid");
            return 0;
        }
        if (c->state == SG_OPEN_LOOP) {
            sg_enter(c, SG_HANDOFF, "operator pressed Resume Flow");
            return 1;
        }
        if (!(c->o2 < c->p.lidLevel)) {
            if (why && n)
                snprintf(why, n, "Resume Flow ignored: O2 %s %% above the lid threshold %s %%: purge first",
                         sg_fmtN(ob, sizeof ob, c->o2, 2), sg_fmtJs(lb, sizeof lb, c->p.lidLevel));
            return 0;
        }
        if (!c->in.mfcConnected || !isfinite(c->in.sp)) {    /* ruling R9 */
            if (why && n) snprintf(why, n, "Resume Flow ignored: MFC not connected");
            return 0;
        }
        c->lastCmd = c->in.sp; c->lastCmdTime = c->now; c->flowAtCmd = c->in.flow;
        c->epid.OVAL = c->lastCmd;
        sg_enter(c, SG_REGULATE, "operator pressed Resume Flow");
        sg_config_epid(c); c->epid.FBON = 1;
        return 1;
    }
    if (why && n) snprintf(why, n, "Resume Flow ignored in %s", stateName(c->state));
    return 0;
}

/* Mode change (spec §8.5; the reference UI's selMode handler, lines 2031-2033). No other immediate
   action: OPEN_LOOP and HANDOFF pick the new expected flow up at the next tick, epid at its next
   configEpid. An out-of-range mode is ignored. */
void sg_set_mode(sg_ctl *c, int mode, const char *who)
{
    if (mode < 0 || mode > 3) return;
    c->p.mode = mode;
    sg_log(c, 0, "%s: enclosure mode → %s", who, c->p.modes[mode].name);
}

/* Admin "reset override count" (the reference UI, line 1963). The override list is kept. */
void sg_reset_override_count(sg_ctl *c)
{
    c->overrideCount = 0;
    sg_log(c, 0, "settings: override count reset");
}

/* Sts:Progress: the reference UI's progress line (render code, lines 2134-2145):
     PURGE     purge <elapsed> / <timeout> [(waiting for full flow)]  then either
               "  BLIND (timer only)" or "  start <o2Start> %  lid check: <..>  lag-corrected <est> %,
               handoff at < <target − Δ> %"
     HANDOFF   "waiting for flow to settle at expected" or "feedback on in <h:mm:ss>"
     REGULATE  [SETTLING ↑|↓ to <target> %  (<rate> %/min toward[, STALLED])\n]
               PID: CVAL <cval> %  out <oval> [<drvl>…<drvh>]
     others    "" (the reference shows a blank line)
   Two differences, both because the text is for the IOC: " (scaled)" after the purge times is
   dropped (it refers to the simulator's time scaling), and so are the reference's "P <p>  I <i>"
   terms, which are the epid record's internal fields; the core has no copy of them (the IOC shows
   them from the record). */
void sg_progress_text(const sg_ctl *c, char *buf, size_t n)
{
    const sg_params *p = &c->p;
    const sg_sd *sd = &c->sd;
    char b1[64], b2[64], b3[64], b4[64], b5[64], lid[96], set[SG_MSG];
    if (n == 0) return;
    buf[0] = '\0';
    if (c->state == SG_PURGE) {
        double to = (isnan(sd->timeout) || sd->timeout == 0) ? sg_purge_timeout(c) : sd->timeout;
        int len = snprintf(buf, n, "purge %s / %s%s", sg_fmtT(b1, sizeof b1, sd->elapsed),
                           sg_fmtT(b2, sizeof b2, to),
                           isnan(sd->timerT0) ? " (waiting for full flow)" : "");
        if (len < 0 || (size_t)len >= n) return;
        if (c->blind) { snprintf(buf + len, n - (size_t)len, "  BLIND (timer only)"); return; }
        if (sd->lidResult == SG_LID_SKIPPED) snprintf(lid, sizeof lid, "skipped");
        else if (isnan(sd->kin)) snprintf(lid, sizeof lid, "pending");
        else snprintf(lid, sizeof lid, "decay %s %% of lid-on", sg_fmtN(b3, sizeof b3, 100 * sd->kin, 0));
        snprintf(buf + len, n - (size_t)len,
                 "  start %s %%  lid check: %s  lag-corrected %s %%, handoff at < %s %%",
                 sg_fmtN(b3, sizeof b3, sd->o2Start, 2), lid, sg_fmtN(b4, sizeof b4, sd->est, 3),
                 sg_fmtN(b5, sizeof b5, p->target - p->delta, 3));
    } else if (c->state == SG_HANDOFF) {
        if (!sd->phaseDelay) snprintf(buf, n, "waiting for flow to settle at expected");
        else snprintf(buf, n, "feedback on in %s", sg_fmtT(b1, sizeof b1, p->fbDelay - (c->now - sd->t1)));
    } else if (c->state == SG_REGULATE) {
        set[0] = '\0';
        if (c->settling)
            snprintf(set, sizeof set, "SETTLING %s to %s %%  (%s %%/min toward%s)\n",
                     c->settleDir < 0 ? "↓" : "↑", sg_fmtN(b1, sizeof b1, p->target, 2),
                     sg_fmtN(b2, sizeof b2, c->towardRate, 3),
                     c->stallSec >= p->stallTime ? ", STALLED" : "");
        snprintf(buf, n, "%sPID: CVAL %s %%  out %s [%s…%s]", set,
                 sg_fmtN(b1, sizeof b1, c->epid.CVAL, 3), sg_fmtN(b2, sizeof b2, c->epid.OVAL, 3),
                 sg_fmtN(b3, sizeof b3, c->epid.DRVL, 2), sg_fmtN(b4, sizeof b4, c->epid.DRVH, 2));
    }
}
