/* sgO2Diag.c: see sgO2Diag.h. Pure C, no EPICS; unit-tested in sgIocTest.c. */
#include <math.h>
#include <string.h>
#include "sgO2Diag.h"

void sg_o2diag_init(sg_o2diag *d)
{
    memset(d, 0, sizeof *d);
    d->lastInterval = NAN;
    d->binMaxGap = NAN;
}

/* NaN-propagating max: a NAN argument loses to a finite one, so folding a ring of mostly-NAN
   "no interval this tick" slots into a running max never clobbers a real value with NAN. */
static double maxFinite(double a, double b)
{
    if (isnan(a)) return b;
    if (isnan(b)) return a;
    return a > b ? a : b;
}

void sg_o2diag_tick(sg_o2diag *d, double now, int tsValid, unsigned tsSec, unsigned tsNsec)
{
    int fresh = 0;
    double interval = NAN;
    int i, guard;

    if (tsValid && (tsSec != 0 || tsNsec != 0)) {
        if (!d->haveTs || tsSec != d->tsSec || tsNsec != d->tsNsec) {
            fresh = 1;
            if (d->haveTs) {
                double raw = ((double)tsSec - (double)d->tsSec) +
                             ((double)tsNsec - (double)d->tsNsec) * 1e-9;
                /* a time stamp going backwards (the O2 IOC's clock stepped back, or it restarted
                   with a fresh low time stamp) is still counted as a new update -- just clamped
                   to a 0 s interval rather than reported negative */
                interval = raw < 0.0 ? 0.0 : raw;
                d->lastInterval = interval;
            }
            d->haveTs = 1; d->tsSec = tsSec; d->tsNsec = tsNsec;
        }
        /* else: a valid time stamp, unchanged since the last tick -- the same reading seen
           again (a sensor slower than this tick): not fresh, but not disconnected either */
    }
    /* else: disconnected, or connected with no time stamp yet (nothing received since start or
       since a channel re-assignment): not fresh; counted in disc[] below. d->haveTs/tsSec/tsNsec
       are left untouched, so the first new value after a reconnection is still diffed against
       the last real one and yields a correct interval across the whole gap. */

    /* ---- 600-tick ring, 10 min: one slot every tick ---- */
    i = d->head;
    d->fresh[i] = (unsigned char)fresh;
    d->disc[i] = (unsigned char)!tsValid;
    d->gap[i] = interval;
    d->head = (i + 1) % SG_O2DIAG_RING;
    if (d->n < SG_O2DIAG_RING) d->n++;

    /* ---- current one-minute bin, 24 h ---- */
    if (!d->haveBin) { d->haveBin = 1; d->binStartNow = now; }
    d->binTicks++;
    if (fresh) {
        d->binFresh++;
        if (!isnan(interval)) d->binMaxGap = maxFinite(d->binMaxGap, interval);
    }
    /* roll the bin forward on `now` alone (never tsSec/tsNsec): a loop, not a single step,
       because a tick-clock catch-up (sgIoc.c's SG_MAXBEHIND) can advance `now` by much more
       than 60 s in one call. Each rolled bin is pushed as-is (possibly empty, ticks = 0) so the
       24 h ring still covers the right span of wall time. */
    guard = 0;
    while (now - d->binStartNow >= 60.0 && guard++ < SG_O2DIAG_BINS + 10) {
        int j = d->binsHead;
        d->binsTicks[j] = (unsigned short)d->binTicks;
        d->binsFresh[j] = (unsigned short)d->binFresh;
        d->binsMaxGap[j] = d->binMaxGap;
        d->binsHead = (j + 1) % SG_O2DIAG_BINS;
        if (d->binsN < SG_O2DIAG_BINS) d->binsN++;
        d->binStartNow += 60.0;
        d->binTicks = 0; d->binFresh = 0; d->binMaxGap = NAN;
    }
    if (guard > SG_O2DIAG_BINS) {
        /* a pathological jump (far more than 24 h in one tick): the loop above already pushed
           enough bins to overwrite the whole 24 h ring with empty ones; resync the partial bin's
           clock to now rather than keep counting iterations */
        d->binStartNow = now;
    }
    (void)guard;
}

void sg_o2diag_get(const sg_o2diag *d, sg_o2diag_report *r)
{
    long i, n = d->n < SG_O2DIAG_RING ? d->n : SG_O2DIAG_RING;
    long freshN = 0, discN = 0;
    double maxGap10 = NAN, maxGap24 = d->binMaxGap;
    long ticks24 = (long)d->binTicks, fresh24 = (long)d->binFresh;
    long m = d->binsN < SG_O2DIAG_BINS ? d->binsN : SG_O2DIAG_BINS;

    for (i = 0; i < n; i++) {
        if (d->fresh[i]) freshN++;
        if (d->disc[i]) discN++;
        if (!isnan(d->gap[i])) maxGap10 = maxFinite(maxGap10, d->gap[i]);
    }
    for (i = 0; i < m; i++) {
        ticks24 += d->binsTicks[i];
        fresh24 += d->binsFresh[i];
        maxGap24 = maxFinite(maxGap24, d->binsMaxGap[i]);
    }

    r->ticks10m = n;
    r->disc10m = discN;
    r->fresh10m = n > 0 ? 100.0 * (double)freshN / (double)n : NAN;
    r->fresh24h = ticks24 > 0 ? 100.0 * (double)fresh24 / (double)ticks24 : NAN;
    r->maxGap10m = maxGap10;
    r->maxGap24h = maxGap24;
    r->lastInterval = d->lastInterval;
}
