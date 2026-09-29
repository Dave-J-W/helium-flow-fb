/* sgLedger.c: helium ledger, cylinder forecast and usage report (reference lines 1218-1306).
   Reference null is NaN here too (cylBase, lastTotal, cylEst[].h, run end). Arrays are fixed
   (capacities in sgCore.h); a full array drops its oldest entry, and trimming shifts left.
   Also the admin new-run mark, the He:ForecastText helper and the helium autosave image. */
#include <math.h>
#include <stdio.h>
#include <string.h>
#include "sgCoreInt.h"
#include "sgFmt.h"

/* Remove the first k entries of an array of n elements of size sz (JS shift() k times). */
static void shiftLeft(void *arr, int *n, int k, size_t sz)
{
    if (k <= 0) return;
    if (k > *n) k = *n;
    memmove(arr, (char *)arr + (size_t)k * sz, (size_t)(*n - k) * sz);
    *n -= k;
}

/* ledgerEvent (line 1218): append (now, type, cumL); if the array is full, drop the oldest.
   ledgerSeq counts the events (the glue recomputes the report and saves on a change). */
void sg_ledger_event(sg_ctl *c, int type)
{
    if (c->nLedger >= SG_LEDGER) shiftLeft(c->ledger, &c->nLedger, c->nLedger - (SG_LEDGER - 1),
                                           sizeof c->ledger[0]);
    c->ledger[c->nLedger].t = c->now;
    c->ledger[c->nLedger].type = type;
    c->ledger[c->nLedger].L = c->cumL;
    c->nLedger++;
    c->ledgerSeq++;
}

/* Total_RBV as the ledger and the forecast see it: NaN while the MFC is disconnected (in every
   state, IDLE included), so the ruling R7 guards below also cover a channel that has never
   connected (whose Total_RBV would read 0 and wreck the autosaved lastTotal and cylBase). */
static double totalRbv(const sg_ctl *c) { return c->in.mfcConnected ? c->in.total : NAN; }

/* ledgerUpdate (line 1219), called every tick: accumulate litres, hourly snapshot, trim.
   Ruling R7 (beyond the reference): a non-finite Total_RBV is skipped for the accumulation, so it
   cannot poison cumL (autosaved); lastTotal keeps the last finite reading, and the next finite
   one accumulates across the gap. Snapshots and trimming go on as usual. */
void sg_ledger_update(sg_ctl *c)
{
    double tot = totalRbv(c);
    int hour = fmod(c->now, 3600) == 0;
    if (isfinite(tot)) {
        if (!isnan(c->lastTotal)) c->cumL += sg_jmax(0, tot - c->lastTotal);
        c->lastTotal = tot;
    }
    if (hour || !c->nSnaps) {
        if (c->nSnaps >= SG_SNAPS) shiftLeft(c->snaps, &c->nSnaps, c->nSnaps - (SG_SNAPS - 1),
                                             sizeof c->snaps[0]);
        c->snaps[c->nSnaps].t = c->now;
        c->snaps[c->nSnaps].L = c->cumL;
        c->nSnaps++;
    }
    if (hour) {
        double keep = c->now - (c->p.reportDays + 10) * 86400;
        int k = 0;
        while (k < c->nLedger && c->ledger[k].t < keep) k++;
        shiftLeft(c->ledger, &c->nLedger, k, sizeof c->ledger[0]);
        k = 0;
        while (k < c->nSnaps && c->snaps[k].t < keep) k++;
        shiftLeft(c->snaps, &c->nSnaps, k, sizeof c->snaps[0]);
    }
}

/* litresAt (line 1230): cumulative litres at time t, interpolated between hourly snapshots. */
double sg_litres_at(const sg_ctl *c, double t)
{
    const sg_snap *s = c->snaps;
    int i;
    if (!c->nSnaps || t >= c->now) return c->cumL;
    if (t <= s[0].t) return s[0].L;
    for (i = 0; i < c->nSnaps && !(s[i].t >= t); i++) {}
    if (i >= c->nSnaps) return c->cumL;
    return s[i - 1].L + (s[i].L - s[i - 1].L) * (t - s[i - 1].t) / (s[i].t - s[i - 1].t);
}

/* ---------------------------------------------------------------- usageReport (1238-1272) */
typedef struct {
    const sg_ctl *c;
    int pu[SG_LEDGER]; int nPu;    /* ledger indices of the purge events, in array order */
    sg_report *r;                  /* r->runs collects every run; the oldest drop when full */
} usage_ctx;

/* marks.some(m => m.t > lo && m.t <= hi); hi NaN = no upper bound (the close() form). */
static int markAfter(const sg_ctl *c, double lo, double hi, int bounded)
{
    int i;
    for (i = 0; i < c->nLedger; i++) {
        const sg_ledger_ev *m = &c->ledger[i];
        if (m->type == SG_EV_NEWRUN && m->t > lo && (!bounded || m->t <= hi)) return 1;
    }
    return 0;
}

/* segEnd(pu, before): time of the first flow-zero after the purge and before `before`. */
static double segEnd(const sg_ctl *c, const sg_ledger_ev *pu, double before)
{
    int i;
    for (i = 0; i < c->nLedger; i++) {
        const sg_ledger_ev *q = &c->ledger[i];
        if (q->type == SG_EV_ZERO && q->t > pu->t && q->t < before) return q->t;
    }
    return pu->t;
}

/* close(): the group is the purge-list range [g0, g1]. */
static void closeGroup(usage_ctx *u, int g0, int g1)
{
    const sg_ctl *c = u->c;
    const sg_ledger_ev *ev = c->ledger;
    const sg_ledger_ev *first = &ev[u->pu[g0]], *last = &ev[u->pu[g1]];
    const sg_ledger_ev *nextPurge = NULL, *zero = NULL, *end;
    sg_run *run;
    double endL, endT;
    int i, finished, cyl = 0;
    for (i = 0; i < u->nPu; i++) if (ev[u->pu[i]].t > last->t) { nextPurge = &ev[u->pu[i]]; break; }
    for (i = 0; i < c->nLedger; i++)
        if (ev[i].type == SG_EV_ZERO && ev[i].t > last->t && (!nextPurge || ev[i].t < nextPurge->t)) {
            zero = &ev[i];
            break;
        }
    finished = nextPurge || c->now - (zero ? zero->t : last->t) > c->p.runGap
               || markAfter(c, last->t, NAN, 0);
    end = zero ? zero : (finished ? last : NULL);
    endL = end ? end->L : c->cumL;
    endT = end ? end->t : c->now;
    for (i = 0; i < c->nLedger; i++)
        if (ev[i].type == SG_EV_CYLINDER && ev[i].t >= first->t && ev[i].t <= endT) cyl++;
    if (u->r->nRuns >= SG_RUNS)
        shiftLeft(u->r->runs, &u->r->nRuns, u->r->nRuns - (SG_RUNS - 1), sizeof u->r->runs[0]);
    run = &u->r->runs[u->r->nRuns++];
    run->start = first->t;
    run->end = end ? end->t : NAN;
    run->purges = g1 - g0 + 1;
    run->L = endL - first->L;
    run->startL = first->L;
    run->endL = endL;
    run->cylinders = cyl;
    run->finished = finished;
}

/* usageReport (line 1238), spec §5.6. Runs beyond SG_RUNS are dropped from the front (oldest)
   before the window filter; the last run, which sets the window, is always kept. */
void sg_usage_report(const sg_ctl *c, sg_report *r)
{
    usage_ctx u;
    const sg_ledger_ev *ev = c->ledger;
    const sg_params *p = &c->p;
    double wEnd, wStart, lwEnd, lwStart, inRuns = 0;
    int i, g0 = -1, n, cyls = 0;
    memset(r, 0, sizeof *r);
    u.c = c; u.r = r; u.nPu = 0;
    for (i = 0; i < c->nLedger; i++) if (ev[i].type == SG_EV_PURGE) u.pu[u.nPu++] = i;
    for (i = 0; i < u.nPu; i++) {
        const sg_ledger_ev *e = &ev[u.pu[i]];
        if (g0 >= 0) {
            const sg_ledger_ev *prev = &ev[u.pu[i - 1]];
            if (e->t - segEnd(c, prev, e->t) > p->runGap || markAfter(c, prev->t, e->t, 1)) {
                closeGroup(&u, g0, i - 1);
                g0 = -1;
            }
        }
        if (g0 < 0) g0 = i;
    }
    if (g0 >= 0) closeGroup(&u, g0, u.nPu - 1);
    wEnd = r->nRuns ? (isnan(r->runs[r->nRuns - 1].end) ? c->now : r->runs[r->nRuns - 1].end)
                    : c->now;
    wStart = wEnd - p->reportDays * 86400;
    r->dispensed = sg_litres_at(c, wEnd) - sg_litres_at(c, wStart);
    for (i = 0; i < c->nLedger; i++)
        if (ev[i].type == SG_EV_CYLINDER && ev[i].t > wStart && ev[i].t <= wEnd) cyls++;
    n = 0;                                          /* inWin: filter in place */
    for (i = 0; i < r->nRuns; i++) {
        double e = isnan(r->runs[i].end) ? c->now : r->runs[i].end;
        if (e > wStart) r->runs[n++] = r->runs[i];
    }
    r->nRuns = n;
    lwEnd = sg_litres_at(c, wEnd);
    lwStart = sg_litres_at(c, wStart);
    for (i = 0; i < n; i++) {
        const sg_run *q = &r->runs[i];
        double e = isnan(q->end) ? c->now : q->end;
        inRuns = inRuns + (e <= wEnd ? q->endL : lwEnd) - (q->start >= wStart ? q->startL : lwStart);
    }
    r->wStart = wStart;
    r->wEnd = wEnd;
    r->cyls = cyls;
    r->cylEquiv = r->dispensed / p->cylCapacityL;
    r->inRuns = inRuns;
}

/* newCylinder (line 1273): operator "New He cylinder", re-baseline the totalizer reading. With
   the MFC disconnected cylBase becomes unset, and the first connected tick takes Total_RBV as the
   base (the first-start rule of cylForecast). */
void sg_new_cylinder(sg_ctl *c, const char *who)
{
    char b1[40], b2[64];
    sg_ledger_event(c, SG_EV_CYLINDER);
    c->cylBase = totalRbv(c);
    c->nCyl = 0;
    sg_clear_alarm(c, SG_A_CYLLOW, 1);
    sg_log(c, 0, "%s: new He cylinder fitted (%s L usable); usage counted from Total_RBV = %s L",
           who, sg_fmtJs(b1, sizeof b1, c->p.cylCapacityL), sg_fmtN(b2, sizeof b2, c->cylBase, 1));
}

/* ---------------------------------------------------------------- helium autosave (spec §10) */
/* Export the autosaved helium state into the He: waveform layout (spec §7.7). Unused array
   elements are zeroed so the saved image is deterministic. */
void sg_helium_export(const sg_ctl *c, sg_helium *h)
{
    int i;
    memset(h, 0, sizeof *h);
    h->cumL = c->cumL; h->lastTotal = c->lastTotal; h->cylBase = c->cylBase;
    h->histN = c->nCyl;
    for (i = 0; i < c->nCyl; i++) { h->histT[i] = c->cylHist[i].t; h->histUsed[i] = c->cylHist[i].used; }
    h->evN = c->nLedger;
    for (i = 0; i < c->nLedger; i++) {
        h->evT[i] = c->ledger[i].t; h->evType[i] = c->ledger[i].type; h->evL[i] = c->ledger[i].L;
    }
    h->snapN = c->nSnaps;
    for (i = 0; i < c->nSnaps; i++) { h->snapT[i] = c->snaps[i].t; h->snapL[i] = c->snaps[i].L; }
}

/* Count n clamped to [0, cap]; the entries claimed beyond cap are added to *dropped. */
static int clampCount(int n, int cap, int *dropped)
{
    if (n < 0) return 0;
    if (n > cap) { *dropped += n - cap; return cap; }
    return n;
}

/* Import an autosaved image (possibly corrupt: a partial restore, a hand-edited .sav). Counts are
   clamped to the capacities; an entry is dropped if its time or value is not finite, if its time
   is earlier than the previous kept entry's (the arrays are in time order), or, for events, if
   its type is not 1..4. Non-finite lastTotal / cylBase become NaN (unset); a non-finite cumL
   becomes 0. Replaces the whole helium state; returns the number of entries dropped. The derived
   state (cylLeftL, the forecast) is recomputed by the next ticks. */
int sg_helium_import(sg_ctl *c, const sg_helium *h)
{
    int i, n, dropped = 0;
    double lastT;
    c->cumL = isfinite(h->cumL) ? h->cumL : 0;
    c->lastTotal = isfinite(h->lastTotal) ? h->lastTotal : NAN;
    c->cylBase = isfinite(h->cylBase) ? h->cylBase : NAN;
    n = clampCount(h->histN, SG_CYLHIST, &dropped);
    c->nCyl = 0; lastT = -INFINITY;
    for (i = 0; i < n; i++) {
        double t = h->histT[i], u = h->histUsed[i];
        if (!isfinite(t) || !isfinite(u) || t < lastT) { dropped++; continue; }
        c->cylHist[c->nCyl].t = t; c->cylHist[c->nCyl].used = u; c->nCyl++;
        lastT = t;
    }
    n = clampCount(h->evN, SG_LEDGER, &dropped);
    c->nLedger = 0; lastT = -INFINITY;
    for (i = 0; i < n; i++) {
        double t = h->evT[i], ty = h->evType[i], L = h->evL[i];
        int known = ty == SG_EV_PURGE || ty == SG_EV_ZERO || ty == SG_EV_CYLINDER || ty == SG_EV_NEWRUN;
        if (!isfinite(t) || !isfinite(L) || !known || t < lastT) { dropped++; continue; }
        c->ledger[c->nLedger].t = t; c->ledger[c->nLedger].type = (int)ty; c->ledger[c->nLedger].L = L;
        c->nLedger++;
        lastT = t;
    }
    n = clampCount(h->snapN, SG_SNAPS, &dropped);
    c->nSnaps = 0; lastT = -INFINITY;
    for (i = 0; i < n; i++) {
        double t = h->snapT[i], L = h->snapL[i];
        if (!isfinite(t) || !isfinite(L) || t < lastT) { dropped++; continue; }
        c->snaps[c->nSnaps].t = t; c->snaps[c->nSnaps].L = L; c->nSnaps++;
        lastT = t;
    }
    return dropped;
}

/* Admin "mark start of a new user run" (spec §8.5; the reference UI, line 1964). */
void sg_mark_new_run(sg_ctl *c)
{
    sg_ledger_event(c, SG_EV_NEWRUN);
    sg_log(c, 0, "admin: start of a new user run marked");
}

/* He:ForecastText (spec §7.1): the reference UI's forecast line (render code, lines 2124-2126):
   the range (cylMinH–cylMaxH) and median of the windows that have an estimate, or the
   collecting-data text. */
void sg_forecast_text(const sg_ctl *c, char *buf, size_t n)
{
    char b1[40], b2[40], b3[40];
    int k, nh = 0;
    if (n == 0) return;
    for (k = 0; k < c->nCylEst && k < SG_NWIN; k++) if (!isnan(c->cylEst[k].h)) nh++;
    if (!nh) { snprintf(buf, n, "run-out forecast: collecting data (needs ≥ 6 h)"); return; }
    snprintf(buf, n, "empty in %s–%s (median %s, %d windows)", sg_fmtDur(b1, sizeof b1, c->cylMinH),
             sg_fmtDur(b2, sizeof b2, c->cylMaxH), sg_fmtDur(b3, sizeof b3, c->cylMedianH), nh);
}

/* cylForecast (line 1279), spec §5.5: run-out estimate from helium usage, several windows. */
static const double WIN_DAYS[SG_NWIN] = { 3, 2, 1, 0.5, 0.25 };

void sg_cyl_forecast(sg_ctl *c)
{
    const sg_params *p = &c->p;
    double tot = totalRbv(c), used, span, hs[SG_NWIN], m;
    char b[40], msg[SG_MSG];
    int k, i, nh = 0;
    if (!isfinite(tot)) return;         /* ruling R7: skip the whole step (cylLeftL keeps its value) */
    if (isnan(c->cylBase)) c->cylBase = tot;             /* first start: assume a full cylinder */
    if (tot < c->cylBase - 1) {
        sg_log(c, 1, "Alicat totalizer went backwards (reset?): usage re-baselined");
        c->cylBase = tot - (c->nCyl ? c->cylHist[c->nCyl - 1].used : 0);
    }
    used = tot - c->cylBase;
    c->cylLeftL = sg_jmax(0, p->cylCapacityL - used);
    if (fmod(c->now, 60) != 0) return;
    if (c->nCyl >= SG_CYLHIST) shiftLeft(c->cylHist, &c->nCyl, c->nCyl - (SG_CYLHIST - 1),
                                         sizeof c->cylHist[0]);
    c->cylHist[c->nCyl].t = c->now;
    c->cylHist[c->nCyl].used = used;
    c->nCyl++;
    k = 0;
    while (k < c->nCyl && c->cylHist[k].t < c->now - 4 * 86400) k++;
    shiftLeft(c->cylHist, &c->nCyl, k, sizeof c->cylHist[0]);
    span = c->now - c->cylHist[0].t;
    for (k = 0; k < SG_NWIN; k++) {
        sg_cylest *e = &c->cylEst[k];
        double w = WIN_DAYS[k] * 86400, st = 0, su = 0, mt, mu, sxy = 0, sxx = 0, rate;
        int n = 0;
        e->days = WIN_DAYS[k];
        e->h = NAN; e->rate = NAN; e->why = NULL;
        if (span < 0.9 * w) { e->why = "not enough data yet"; continue; }
        for (i = 0; i < c->nCyl; i++)
            if (c->cylHist[i].t >= c->now - w) { st += c->cylHist[i].t; n++; }
        mt = st / n;
        for (i = 0; i < c->nCyl; i++)
            if (c->cylHist[i].t >= c->now - w) su += c->cylHist[i].used;
        mu = su / n;
        for (i = 0; i < c->nCyl; i++) {
            const sg_cylpt *q = &c->cylHist[i];
            if (!(q->t >= c->now - w)) continue;
            sxy += (q->t - mt) * (q->used - mu);
            sxx += (q->t - mt) * (q->t - mt);
        }
        rate = sxx > 0 ? sxy / sxx : 0;                   /* L/s (least-squares usage rate) */
        e->rate = rate * 86400;                           /* L/day */
        if (c->cylLeftL <= 0) { e->h = 0; continue; }
        if (rate <= 1e-6) { e->why = "no usage"; continue; }
        e->h = c->cylLeftL / rate / 3600;                 /* hours */
    }
    c->nCylEst = SG_NWIN;
    for (k = 0; k < SG_NWIN; k++) {                       /* filter h != null, sort ascending */
        double h = c->cylEst[k].h;
        if (isnan(h)) continue;
        for (i = nh; i > 0 && hs[i - 1] > h; i--) hs[i] = hs[i - 1];
        hs[i] = h;
        nh++;
    }
    c->cylMedianH = nh ? hs[(nh - 1) / 2] : NAN;
    c->cylMinH = nh ? hs[0] : NAN;                        /* the range shown in ForecastText */
    c->cylMaxH = nh ? hs[nh - 1] : NAN;
    m = c->cylMedianH;
    if (!isnan(m) && m < p->cylAlarmH) {
        snprintf(msg, sizeof msg, "helium cylinder empty in < %s h (forecast)",
                 sg_fmtJs(b, sizeof b, p->cylAlarmH));
        sg_set_alarm(c, SG_A_CYLLOW, 2, msg);
    } else if (!isnan(m) && m < p->cylWarnH) {
        snprintf(msg, sizeof msg, "helium cylinder empty in < %s h (forecast)",
                 sg_fmtJs(b, sizeof b, p->cylWarnH));
        sg_set_alarm(c, SG_A_CYLLOW, 1, msg);
    } else sg_clear_alarm(c, SG_A_CYLLOW, 0);
}
