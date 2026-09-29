/* sgChecks.c: the per-tick alarm checks (reference alarmChecks, lines 1403-1457): flow mismatch,
   settling and stall, flow high/low, pinned, O2 high. */
#include <math.h>
#include <stdio.h>
#include <string.h>
#include "sgCoreInt.h"
#include "sgFmt.h"

/* JS Math.round: floor(x + 0.5); NaN stays NaN. */
static double jround(double x) { return floor(x + 0.5); }

/* mean(end) of alarmChecks: the A samples ending at index end, summed in index order. An index
   out of range (only reachable with a negative stallWindow) reads NaN, like JS undefined. */
static double histMean(const sg_ctl *c, int end, double A)
{
    double s = 0;
    int i;
    for (i = end - (int)A + 1; i <= end; i++)
        s += (i >= 0 && i < c->nO2) ? c->o2hist[i] : NAN;
    return s / A;
}

/* Setpoint follow (spec §8.14, D1b of the conformance audit; not in the reference, whose Alicat
   always follows: the replay runs it with writeEnabled = 1 and it never comes on there). The
   flow-mismatch check below compares Flow_RBV with the Alicat's OWN Setpoint_RBV, so a lost
   put (a one-shot Flow Zero, the purge entry), a device write that failed behind a good CA put,
   or a second writer looked healthy. Judged only with writes enabled, outside IDLE, the Alicat
   running and connected; more than holdDetect + mismatchMargin consecutive ticks with
   Setpoint_RBV off lastCmd, and more than the Alicat's ramp time for the last step
   (followStep / RampRate_RBV) plus that margin since the command, raise the MAJOR source and
   re-send lastCmd at once and every holdRetryInterval s. The ramp allowance matters: the
   Alicat's Setpoint_RBV reports the ramped setpoint (archive of 24 Sep: 0.43 then 20 over 7 s
   on a purge), so a 20 SLPM step at a slow ramp rate takes longer than the fixed margin. */
static void followCheck(sg_ctl *c)
{
    const sg_params *p = &c->p;
    const sg_inputs *r = &c->in;
    if (!(r->writeEnabled && c->state != SG_IDLE && r->running && r->mfcConnected &&
          isfinite(c->lastCmd) && isfinite(r->sp))) {
        c->followSec = 0; c->nextFollowResend = 0;
        sg_mm_clear(c, SG_MM_FOLLOW, 1);
        return;
    }
    if (!(fabs(r->sp - c->lastCmd) > sg_jmax(p->mismatchAbs, p->mismatchFrac * c->lastCmd))) {
        c->followSec = 0; c->nextFollowResend = 0;
        sg_mm_clear(c, SG_MM_FOLLOW, 0);
        return;
    }
    c->followSec++;
    if (!((double)c->followSec > p->holdDetect + p->mismatchMargin)) return;
    if (!(c->now - c->lastCmdTime > (r->ramp > 0 ? c->followStep / r->ramp : 0)
                                    + p->holdDetect + p->mismatchMargin)) return;
    if (!c->mmOn[SG_MM_FOLLOW]) {             /* the text as first raised (not re-logged) */
        char sb[64], lb[64], msg[SG_MSG];
        snprintf(msg, sizeof msg, "Alicat setpoint %s SLPM does not follow the controller "
                 "(%s SLPM): write lost or another writer", sg_fmtN(sb, sizeof sb, r->sp, 2),
                 sg_fmtN(lb, sizeof lb, c->lastCmd, 2));
        sg_mm_set(c, SG_MM_FOLLOW, msg);
    }
    if (c->now >= c->nextFollowResend) {
        if (c->io.put_setpoint) c->io.put_setpoint(c->io.ctx, c->lastCmd);
        c->nextFollowResend = c->now + p->holdRetryInterval;
    }
}

void sg_alarm_checks(sg_ctl *c)
{
    const sg_params *p = &c->p;
    const sg_inputs *r = &c->in;
    const int s = c->state;
    char b1[SG_MSG], b2[SG_MSG], n1[64], n2[64];

    followCheck(c);
    /* flow mismatch (§5.1): Flow_RBV against the Alicat's own Setpoint_RBV.
       spSeen NaN = null (never seen). */
    if (isnan(c->spSeen) || fabs(r->sp - c->spSeen) > 1e-9) {
        c->spSeen = r->sp; c->spChangeT = c->now; c->flowAtSpChange = r->flow;
    }
    if (sg_mfc_lost(c)) {
        /* spec §8.18: readings are stale; sgCore.c's mfcLink owns the Mismatch alarm meanwhile */
    } else if (c->mmShown >= 0) {
        /* a higher Mismatch source owns the alarm (spec §8.14 "Mismatch sources") */
    } else if (s != SG_IDLE && r->running) {
        double allowed = (r->ramp > 0 ? fabs(r->sp - c->flowAtSpChange) / r->ramp : 0)
                         + p->mismatchMargin;
        double tol = sg_jmax(p->mismatchAbs, p->mismatchFrac * r->sp);
        int off = fabs(r->sp - r->flow) > tol;
        if (off && c->now - c->spChangeT >= allowed)
            sg_set_alarm(c, SG_A_MISMATCH, 2, "flow mismatch: cylinder empty or MFC fault?");
        else if (!off) sg_clear_alarm(c, SG_A_MISMATCH, 0);
    } else sg_clear_alarm(c, SG_A_MISMATCH, 1);

    c->flowSteady = 0;                        /* Diag:FlowSteady; set below in REGULATE */
    if (s == SG_REGULATE) {
        const int n = c->nO2;
        const double W = jround(p->stallWindow), A = jround(p->slopeAvgN);
        int flowSteady, stalled, settled, level;
        double ratio;
        /* --- settling flag and progress (slope) check */
        if (c->o2ok && n > W + A) {
            /* W and A are whole numbers here, and n > W + A bounds them */
            double slope = (histMean(c, n - 1, A) - histMean(c, n - 1 - (int)W, A)) / W * 60;
            int judging;
            c->o2Slope = slope;                                     /* %/min */
            c->towardRate = c->o2 < p->target ? slope : -slope;   /* positive = toward target */
            judging = c->settling && c->now - c->settleT0 >= p->stallGrace;
            if (judging && c->towardRate < p->progressMin) c->stallSec++;
            else if (!c->stallLatched) c->stallSec = 0;
        }
        sg_push_capped(c->ovalHist, &c->nOval, SG_OVALHIST, c->epid.OVAL, W + 1);   /* ovalHist */
        flowSteady = c->nOval > W && fabs(c->epid.OVAL - c->ovalHist[0]) <= p->flowSteadyBand
                     && fabs(c->o2Slope) < p->o2SteadyRate;
        c->flowSteady = flowSteady;
        /* settled once O2 reaches the target itself (crosses it in the settling direction) */
        if (c->settling && c->o2ok &&
            (c->settleDir < 0 ? c->o2 <= p->target
             : c->settleDir > 0 ? c->o2 >= p->target
             : fabs(c->o2 - p->target) <= p->tol)) {
            c->settling = 0; c->stallLatched = 0; c->stallSec = 0;
            sg_clear_alarm(c, SG_A_NOTREACHED, 0);
            sg_log(c, 0, "settled: O2 reached %s ± %s %% after %s",
                   sg_fmtN(n1, sizeof n1, p->target, 3), sg_fmtJs(n2, sizeof n2, p->tol),
                   sg_fmtT(b1, sizeof b1, c->now - c->settleT0));
        }
        if (c->settling && c->now - c->settleT0 >= p->settleTimeout) {
            snprintf(b1, sizeof b1, "target not reached within %s",
                     sg_fmtT(n1, sizeof n1, p->settleTimeout));
            sg_set_alarm(c, SG_A_NOTREACHED, 1, b1);
        }
        stalled = c->stallSec >= p->stallTime
                  || (c->settling && c->now - c->settleT0 >= p->settleTimeout);
        if (c->settling && stalled && !c->stallLatched) {
            c->stallLatched = 1;
            sg_log(c, 0, "settling stalled: O2 no longer approaching the target, "
                         "process alarms now active");
        }
        settled = !c->settling || c->stallLatched;          /* alarms apply unless making progress */
        ratio = r->flow / sg_expected_flow(c);
        level = !(settled && flowSteady) ? 0
                : ratio >= p->flowMajorX ? 2 : ratio >= p->flowMinorX ? 1 : 0;
        snprintf(b1, sizeof b1, "flow ≥ %s× expected: check enclosure",
                 sg_fmtJs(n1, sizeof n1, p->flowMinorX));
        snprintf(b2, sizeof b2, "flow ≥ %s× expected: check enclosure seal",
                 sg_fmtJs(n2, sizeof n2, p->flowMajorX));
        sg_debounce(c, SG_A_FLOWHIGH, level, b1, b2, p->flowAlarmDelay);
        /* flow well below what the mode expects, judged on the PID's demand (OVAL), so an empty
           cylinder does not read as "wrong mode" */
        level = settled && flowSteady && c->epid.OVAL / sg_expected_flow(c) <= p->flowLowX ? 1 : 0;
        snprintf(b1, sizeof b1, "flow ≤ %s× expected: wrong enclosure mode selected?",
                 sg_fmtJs(n1, sizeof n1, p->flowLowX));
        sg_debounce(c, SG_A_FLOWLOW, level, b1, "", p->flowAlarmDelay);
        if (c->epid.OVAL >= c->epid.DRVH - 1e-6) c->pinnedSec++; else c->pinnedSec = 0;
        if (settled && c->pinnedSec >= p->pinnedTime)
            sg_set_alarm(c, SG_A_PINNED, 2, "PID pinned at max flow: check enclosure seal");
        else sg_clear_alarm(c, SG_A_PINNED, 0);
        /* pinned at minimum flow while O2 stays below target: logged once, no alarm */
        if (c->epid.OVAL <= c->epid.DRVL + 1e-6 && c->o2ok && c->o2 < p->target - p->tol)
            c->pinnedLowSec++;
        else c->pinnedLowSec = 0;
        if (settled && (double)c->pinnedLowSec == p->pinnedTime)
            sg_log(c, 0, "note: PID at minimum flow and O2 still below target");
        if (c->o2ok) {
            level = !settled ? 0 : c->o2 > p->target + p->o2AbnormalOffset ? 2
                    : c->o2 > p->target + p->tol ? 1 : 0;
            sg_debounce(c, SG_A_O2HIGH, level, "O2 above target range", "O2 abnormally high",
                        p->flowAlarmDelay);
        }
    }
}
