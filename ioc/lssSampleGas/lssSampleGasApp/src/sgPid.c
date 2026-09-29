/* sgPid.c: configEpid and the PID prepare/done steps around the epid record (reference lines
   1106 and 1459). The record's own algorithm (lines 1463-1480) is the real epid record in the IOC
   and sgEpidSim.c in the tests. */
#include <math.h>
#include "sgCoreInt.h"

/* Station.step (line 1544): sec % max(1, round(pidScan)) === 0. JS Math.round = floor(x + 0.5). */
int sg_pid_due(const sg_ctl *c, double now)
{
    double m = sg_jmax(1, floor(c->p.pidScan + 0.5));
    return !isnan(m) && fmod(now, m) == 0;
}

/* JS truthiness of a number: nonzero and not NaN. */
static int truthy(double x) { return x != 0 && !isnan(x); }

/* configEpid (line 1106). Gain scheduling: the mode's KP (tuned at 0.99 %) is scaled by
   0.99/target; KI is unchanged. The optional fine band (softer gains near target, with
   hysteresis) judges the CVAL of the previous processing, as in the reference. */
void sg_config_epid(sg_ctl *c)
{
    sg_epid *e = &c->epid;
    const sg_params *p = &c->p;
    const sg_mode *m = &p->modes[(p->mode >= 0 && p->mode < 4) ? p->mode : 0];
    e->VAL = p->target;
    e->KP = truthy(p->gainSchedule) ? m->KP * (0.99 / p->target) : m->KP;
    e->KI = m->KI;
    e->ODEL = p->odel;
    if (p->fineBand > 0 && isfinite(e->CVAL)) {
        double err = fabs(e->VAL - e->CVAL);
        if (c->inFine) { if (err > 2 * p->fineBand) c->inFine = 0; }
        else if (err < p->fineBand) c->inFine = 1;
        if (c->inFine) { e->KP *= p->fineKPx; e->KI *= p->fineKIx; }
    } else c->inFine = 0;
    e->DRVH = sg_jmin(m->drvh, p->hardCeiling);
    e->DRVL = sg_jmin(e->DRVH, m->drvl);      /* per-lid minimum flow (no global floor) */
}

/* processEpid (line 1459) up to the record: nothing if avgBuf is empty; else configEpid, then
   CVAL = the N-to-1 mean of avgBuf (summed in index order, as the reference's reduce). cfg also
   carries FBON and OUTL = lastCmd, the bumpless-start source (spec §7.8, §8.3). */
int sg_pid_prepare(sg_ctl *c, sg_epid_cfg *cfg)
{
    sg_epid *e = &c->epid;
    double sum = 0;
    int i;
    if (c->nAvg == 0) return 0;
    sg_config_epid(c);
    for (i = 0; i < c->nAvg; i++) sum += c->avgBuf[i];
    e->CVAL = sum / c->nAvg;
    cfg->VAL = e->VAL; cfg->KP = e->KP; cfg->KI = e->KI; cfg->DRVL = e->DRVL;
    cfg->DRVH = e->DRVH; cfg->ODEL = e->ODEL; cfg->CVAL = e->CVAL;
    cfg->OUTL = c->lastCmd; cfg->FBON = e->FBON;
    return 1;
}

/* Fully bumpless start (user decision 2026-09-29, spec §8.11): the value for PID:Out before the
   FBON 0 -> 1 processing. devEpidSoft takes I from OUTL and outputs P + I at that step, so
   I = OUTL - KP*(VAL - CVAL) makes the first output OUTL (= lastCmd) exactly. Clamped to the
   drive limits, the range epid keeps I in: near a limit the first step moves by the excess, in
   the direction P asks for. NaN OUTL -> NaN (leave PID:Out alone); a non-finite P term (NaN
   CVAL) falls back to OUTL, the plain epid start. */
double sg_pid_bumpless_i(const sg_epid_cfg *cfg)
{
    double p = cfg->KP * (cfg->VAL - cfg->CVAL);
    if (isnan(cfg->OUTL)) return NAN;
    if (!isfinite(p)) return cfg->OUTL;
    return sg_jclamp(cfg->OUTL - p, cfg->DRVL, cfg->DRVH);
}

/* processEpid after the record: OVAL back into the core; OUTL is written every cycle while FBON
   (line 1480). */
void sg_pid_done(sg_ctl *c, double oval)
{
    c->epid.OVAL = oval;
    if (c->epid.FBON) sg_command(c, oval);
}
