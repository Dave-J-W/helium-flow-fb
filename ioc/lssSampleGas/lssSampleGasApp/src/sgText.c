/* sgText.c: He:Rep:Text (spec §7.7): a port of the reference's Admin usage text
   (simulator/sample_gas_simulator.html lines 1979-1985), with one change from the reference: the
   reference's "day N.NN" (days since the simulator's own start) becomes "YYYY-MM-DD hh:mm" in
   local time, since sg_ctl's clock (`now`, and everything derived from it: sg_report's wStart /
   wEnd / run start / end) is an absolute epoch second, not a simulator-relative day count. */
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include "sgIoc.h"
#include "sgFmt.h"
#ifdef SG_IN_IOC
#include <epicsTime.h>
#endif

/* In the IOC (built as sgTextIoc.c, SG_IN_IOC: two stations = two threads) the thread-safe
   epicsTime_localtime; in the tests plain localtime. */
static void localDate(char *buf, size_t n, double t)
{
    time_t tt = (time_t)t;
    struct tm tmv;
#ifdef SG_IN_IOC
    if (epicsTime_localtime(&tt, &tmv) != epicsTimeOK) { snprintf(buf, n, "?"); return; }
#else
    struct tm *p = localtime(&tt);
    if (!p) { snprintf(buf, n, "?"); return; }
    tmv = *p;
#endif
    snprintf(buf, n, "%04d-%02d-%02d %02d:%02d", tmv.tm_year + 1900, tmv.tm_mon + 1, tmv.tm_mday,
             tmv.tm_hour, tmv.tm_min);
}

void sg_report_text(const sg_ctl *c, const sg_report *r, char *buf, size_t n)
{
    const sg_params *p = &c->p;
    char d0[64], d1[64];
    char nb1[32], nb2[32], nb3[32], nb4[32], nb5[32], durb[32];
    size_t len;
    int w, i;

    if (n == 0) return;
    buf[0] = '\0';

    localDate(d0, sizeof d0, r->wStart > 0 ? r->wStart : 0);
    localDate(d1, sizeof d1, r->wEnd);

    w = snprintf(buf, n,
        "last %s days (%s \xe2\x86\x92 %s):\n"
        "  helium dispensed   %s L  (%s L during user runs)\n"
        "  cylinders fitted   %d    cylinder-equivalents used  %s  (at %s L each)\n"
        "user runs (end = first flow-zero after the last purge; a run closes after %s with no "
        "purge, or at a \"new user run\" mark):\n",
        sg_fmtJs(nb1, sizeof nb1, p->reportDays), d0, d1,
        sg_fmtN(nb2, sizeof nb2, r->dispensed, 0), sg_fmtN(nb3, sizeof nb3, r->inRuns, 0),
        r->cyls, sg_fmtN(nb4, sizeof nb4, r->cylEquiv, 2),
        sg_fmtJs(nb5, sizeof nb5, p->cylCapacityL), sg_fmtDur(durb, sizeof durb, p->runGap / 3600.0));
    if (w < 0 || (size_t)w >= n) return;
    len = (size_t)w;

    if (r->nRuns == 0) {
        snprintf(buf + len, n - len, "  (no purges yet)");
        return;
    }
    for (i = 0; i < r->nRuns && len < n - 1; i++) {
        const sg_run *run = &r->runs[i];
        char ds[64], de[64], lb[32], cylText[40];
        const char *openText;
        int ww;
        localDate(ds, sizeof ds, run->start);
        if (isnan(run->end)) snprintf(de, sizeof de, "in progress");
        else localDate(de, sizeof de, run->end);
        sg_fmtN(lb, sizeof lb, run->L, 0);
        if (run->cylinders) snprintf(cylText, sizeof cylText, "%d cylinder change(s)", run->cylinders);
        else cylText[0] = '\0';
        openText = run->finished ? "" : "  (open)";
        ww = snprintf(buf + len, n - len, "%s  #%d  %s \xe2\x86\x92 %s   %3d purges   %6s L   %s%s",
                      i ? "\n" : "", i + 1, ds, de, run->purges, lb, cylText, openText);
        if (ww < 0) break;
        len += (size_t)ww;
        if (len >= n - 1) { len = strlen(buf); break; }
    }
}
