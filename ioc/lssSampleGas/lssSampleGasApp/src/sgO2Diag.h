/* sgO2Diag.h: read-only diagnostic of how reliably the O2 channel's CA time stamp delivers a NEW
   reading to the controller's 1 Hz tick (user's request, 2026-10-01, after raising the O2 IOC's
   own update rate to 2 Hz so the 1 Hz control loop always has a fresh reading). Pure C, no EPICS
   dependency, like sgStart.c: unit-tested in sgIocTest.c.

   Pure observation: never read by sgCore.c / sgChecks.c / sgPid.c, raises no alarm, and has no
   effect on any decision the controller makes. sgIoc.c calls sg_o2diag_tick once per tick (the
   same tick order as every other use of the O2 channel, in buildInputs) and publishes
   sg_o2diag_get's report to the Diag:O2* PVs (spec 7.6).

   Call once per tick with the O2 channel's state THIS tick:
     tsValid: the channel is connected and has delivered at least one value (sgIoc.c's sg_chan.ok
       for the O2 channel: false while disconnected, not yet assigned, or before the very first
       value after start or a channel re-assignment -- sg_chan's own "nothing received yet" test,
       tsSec == 0 && tsNsec == 0).
     tsSec, tsNsec: that channel's CA time stamp this tick (sg_chan.tsSec/tsNsec for the O2
       channel). Meaningful only to detect that a NEW value arrived since the last tick (the pair
       differs from the one stored at the previous tick) and, once two new time stamps have been
       seen, to measure the interval between them. The time stamp is NEVER compared against
       `now`: `now` is this host's own tick clock, tsSec/tsNsec is the O2 IOC's clock, and the two
       can be offset from each other without anything being wrong (unlike sg_flow_stale's §8.18
       check, which only ever compares a time stamp with itself across ticks, this module follows
       the same rule).
     now: this tick's controller clock (sg_ctl's `now`), used ONLY to decide when a one-minute
       bin has elapsed (24 h ring); never compared with tsSec/tsNsec.

   A tick is FRESH when tsValid is true and (tsSec, tsNsec) differs from the previous tick's
   stored pair -- the O2 IOC delivered a value this host had not seen before, regardless of this
   host's own 1 Hz cadence. While O2 is disconnected or has not delivered a time stamp yet
   (tsValid false), the tick counts as not fresh AND as disconnected (Diag:O2Disconn10m); a valid
   but UNCHANGED time stamp (the same reading seen again: a sensor slower than this tick, or the
   2 Hz source momentarily behind) counts as not fresh but NOT disconnected, so the two causes of
   a low Fresh percentage stay distinguishable on the screen.

   A fresh tick's interval is the new time stamp minus the previous NEW one, in seconds; the very
   first fresh tick ever (no previous new time stamp to diff against) has no interval. If the new
   time stamp is EARLIER than the previous one (the O2 IOC's clock stepped back, or it restarted
   with a fresh low time stamp), the raw difference would be negative: it is still counted as a
   new update (fresh = true), but the interval is clamped to 0 rather than reported negative. */
#ifndef SGO2DIAG_H
#define SGO2DIAG_H

#define SG_O2DIAG_RING 600      /* 10 min of ticks at 1 Hz */
#define SG_O2DIAG_BINS 1440     /* 24 h of one-minute bins */

typedef struct {
    /* the previous tick's O2 time stamp (for new-value and interval detection) */
    int haveTs; unsigned tsSec, tsNsec;
    double lastInterval;                 /* s: the most recent fresh-to-fresh interval seen,
                                             ever; NAN until a second new time stamp has been
                                             seen (the first fresh tick has nothing to diff) */
    /* 600-tick ring, 10 min: one slot written every tick */
    unsigned char fresh[SG_O2DIAG_RING];    /* 1 if that tick counted as fresh */
    unsigned char disc[SG_O2DIAG_RING];     /* 1 if that tick's tsValid was false */
    double gap[SG_O2DIAG_RING];             /* that tick's own interval, or NAN (not fresh, or
                                                the first fresh tick ever) */
    int head, n;                            /* ring write position; ticks seen, capped at RING */

    /* the current, not-yet-closed one-minute bin */
    double binStartNow; int haveBin;
    unsigned binTicks, binFresh;
    double binMaxGap;                       /* NAN until a fresh tick with an interval falls in
                                                this bin */

    /* 1440 one-minute bins, 24 h: oldest overwritten as the ring fills past 24 h */
    unsigned short binsTicks[SG_O2DIAG_BINS], binsFresh[SG_O2DIAG_BINS];
    double binsMaxGap[SG_O2DIAG_BINS];
    int binsHead, binsN;
} sg_o2diag;

typedef struct {
    double fresh10m, fresh24h;    /* %, 0-100; NAN if no ticks counted yet (fresh10m only, right
                                      at start -- fresh24h is NAN only in that same instant) */
    double maxGap10m, maxGap24h;  /* s: the longest fresh-to-fresh interval in the window; NAN if
                                      fewer than two new time stamps have been seen in it */
    double lastInterval;          /* s: the single most recent fresh-to-fresh interval, any
                                      window; NAN until one has been seen at all */
    long ticks10m;                /* ticks counted in the 10-min ring (<= SG_O2DIAG_RING): how
                                      much data the 10-min/24-h percentages rest on so far */
    long disc10m;                 /* of those, how many had tsValid false (a disconnection, not
                                      merely a slow sensor) */
} sg_o2diag_report;

void sg_o2diag_init(sg_o2diag *d);
void sg_o2diag_tick(sg_o2diag *d, double now, int tsValid, unsigned tsSec, unsigned tsNsec);
void sg_o2diag_get(const sg_o2diag *d, sg_o2diag_report *r);

#endif
