/* sgEpidSim.c: devEpidSoft emulation, the reference's processEpid (lines 1463-1480) without
   configEpid/CVAL (the caller passes cfg, from sg_pid_prepare) and without command() (the caller
   calls sg_pid_done). KD is always 0 in the reference, so the D term is 0. */
#include <math.h>
#include "sgEpidSim.h"

static double jclamp(double x, double a, double b)   /* reference clamp; NaN in, NaN out */
{
    if (isnan(x) || isnan(a) || isnan(b)) return NAN;
    if (x < a) x = a;
    if (x > b) x = b;
    return x;
}

void sg_epid_sim_reset(sg_epid_sim *e)
{
    e->I = e->P = e->D = e->ePrev = e->OVAL = 0;
    e->lastT = NAN;
    e->fbonPrev = 0;
}

double sg_epid_sim_process(sg_epid_sim *e, const sg_epid_cfg *cfg, int fbon, double now,
                           double pidScan, double outl)
{
    const double cval = cfg->CVAL;
    const double dt = isnan(e->lastT) ? pidScan : now - e->lastT;
    const double err = cfg->VAL - cval;
    double p, di, i, d, o;
    e->lastT = now;
    if (fbon && !e->fbonPrev) {                /* integral starts from OUTL */
        e->I = jclamp(outl, cfg->DRVL, cfg->DRVH);
        e->OVAL = e->I;
    }
    e->fbonPrev = fbon;
    if (!fbon) { e->ePrev = err; return e->OVAL; }
    p = cfg->KP * err;
    di = cfg->KP * cfg->KI * err * dt;
    i = e->I;
    if (cfg->KI != 0) {
        if ((e->OVAL > cfg->DRVL && e->OVAL < cfg->DRVH) || (e->OVAL >= cfg->DRVH && di < 0) ||
            (e->OVAL <= cfg->DRVL && di > 0))
            i += di;
        i = jclamp(i, cfg->DRVL, cfg->DRVH);
    } else i = 0;
    d = 0;
    o = jclamp(p + i + d, cfg->DRVL, cfg->DRVH);
    e->I = i; e->P = p; e->D = d; e->ePrev = err;
    if (cfg->ODEL == 0 || fabs(o - e->OVAL) > cfg->ODEL) e->OVAL = o;
    return e->OVAL;
}
