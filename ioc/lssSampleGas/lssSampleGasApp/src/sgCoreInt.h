/* sgCoreInt.h: internals shared between the core's source files (sgCore.c, sgAlarms.c,
   sgChecks.c, sgPid.c, sgLedger.c). Not part of the sgCore.h contract; do not include it from
   the IOC glue or from tests. */
#ifndef SGCOREINT_H
#define SGCOREINT_H
#include <math.h>
#include "sgCore.h"

/* JavaScript Math.max / Math.min / the reference's clamp: NaN in, NaN out (C's fmax/fmin
   would drop the NaN). Math.max(a, b) with a NaN operand is NaN. */
static inline double sg_jmax(double a, double b) { return (isnan(a) || isnan(b)) ? NAN : (a > b ? a : b); }
static inline double sg_jmin(double a, double b) { return (isnan(a) || isnan(b)) ? NAN : (a < b ? a : b); }
static inline double sg_jclamp(double x, double a, double b) { return sg_jmin(b, sg_jmax(a, x)); }

/* JS arr.push(v); while (arr.length > cap) arr.shift(); on a fixed array of `size` elements
   holding *n. A full array drops its oldest element first; a NaN cap never trims; a fractional
   cap acts as its floor (sgCore.c). */
void sg_push_capped(double *arr, int *n, int size, double v, double cap);

/* command(v) (reference line 1101): lastCmd bookkeeping and io.put_setpoint, with the ruling R12
   guard. Shared because sg_pid_done (sgPid.c) writes the PID output through it. */
void sg_command(sg_ctl *c, double v);
/* configEpid (reference line 1106), sgPid.c. */
void sg_config_epid(sg_ctl *c);
/* Per-tick parts of tick() that live in other files. */
void sg_alarm_checks(sg_ctl *c);     /* sgChecks.c, reference alarmChecks (1403) */
void sg_ledger_update(sg_ctl *c);    /* sgLedger.c, reference ledgerUpdate (1219) */
void sg_cyl_forecast(sg_ctl *c);     /* sgLedger.c, reference cylForecast (1279) */

/* Alm:Mismatch sources (spec §8.14 "Mismatch sources", enum sg_mm), sgCore.c: the alarm shows the
   highest active source; with none left it clears (silently if asked). sg_mm_set may be called
   every tick (sg_set_alarm logs only a change). */
void sg_mm_set(sg_ctl *c, int src, const char *msg);
void sg_mm_clear(sg_ctl *c, int src, int silent);
void sg_mm_reset(sg_ctl *c);         /* all sources off, without touching the alarm slot */

/* Spec §8.18: true while the Alicat readings are stale (disconnected in a state that owns the
   flow): the flow mismatch check is skipped and sgCore.c's mfcLink owns the Mismatch alarm. */
static inline int sg_mfc_lost(const sg_ctl *c) { return !c->in.mfcConnected && c->state != SG_IDLE; }
#endif
