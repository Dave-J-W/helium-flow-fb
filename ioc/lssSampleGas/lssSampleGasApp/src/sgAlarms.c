/* sgAlarms.c: the reference's alarm machinery (sample_gas_simulator.html lines 1058-1098):
   setAlarm, clearAlarm, latch, expireAlarms, debounce, override, worstSev, plus the banner text.
   The JS alarms Map becomes one slot per sg_alarm key; an inactive slot = key absent. */
#include <math.h>
#include <stdio.h>
#include <string.h>
#include "sgCoreInt.h"

static const char *const sevName[4] = { "", "MINOR", "MAJOR", "INVALID" };

static int validKey(int k) { return k >= 0 && k < SG_NALARMS; }

/* setAlarm (1058): (re)store and log only when the severity or the message changed. */
void sg_set_alarm(sg_ctl *c, int k, int sev, const char *msg)
{
    sg_alarm_slot *a;
    if (!validKey(k)) return;
    a = &c->alarms[k];
    if (!a->active || a->sev != sev || strncmp(a->msg, msg, SG_MSG - 1) != 0) {
        a->active = 1; a->sev = sev;
        snprintf(a->msg, sizeof a->msg, "%s", msg);
        a->since = c->now; a->until = NAN;
        sg_log(c, sev, "%s", msg);
    }
}

/* clearAlarm (1065) */
void sg_clear_alarm(sg_ctl *c, int k, int silent)
{
    sg_alarm_slot *a;
    if (!validKey(k)) return;
    a = &c->alarms[k];
    if (a->active) {
        a->active = 0;
        if (!silent) sg_log(c, 0, "cleared: %s", a->msg);
    }
}

/* latch (1069): always stores and logs; expires at now + dur (the reference default is 300). */
void sg_latch(sg_ctl *c, int k, int sev, const char *msg, double dur)
{
    sg_alarm_slot *a;
    if (!validKey(k)) return;
    a = &c->alarms[k];
    a->active = 1; a->sev = sev;
    snprintf(a->msg, sizeof a->msg, "%s", msg);
    a->since = c->now; a->until = c->now + dur;
    sg_log(c, sev, "%s", msg);
}

/* expireAlarms (1073) */
void sg_expire_alarms(sg_ctl *c)
{
    int k;
    for (k = 0; k < SG_NALARMS; k++) {
        sg_alarm_slot *a = &c->alarms[k];
        if (a->active && !isnan(a->until) && c->now >= a->until) a->active = 0;
    }
}

/* debounce (1074): level 0/1/2 must persist `delay` s before it takes effect. The reference's
   `delay ?? alarmDelay` default is spelled here as delay = NaN. */
void sg_debounce(sg_ctl *c, int k, int level, const char *msg1, const char *msg2, double delay)
{
    sg_deb *d;
    if (!validKey(k)) return;
    d = &c->deb[k];
    if (!d->used) { d->used = 1; d->cur = 0; d->cand = 0; d->since = c->now; }
    if (level != d->cand) { d->cand = level; d->since = c->now; }
    if (isnan(delay)) delay = c->p.alarmDelay;
    if (d->cand != d->cur && c->now - d->since >= delay) {
        d->cur = d->cand;
        if (d->cur == 0) sg_clear_alarm(c, k, 0);
        else sg_set_alarm(c, k, d->cur, d->cur == 2 ? msg2 : msg1);
    }
}

/* worstSev (1094) */
int sg_worst_sev(const sg_ctl *c)
{
    int k, s = 0;
    for (k = 0; k < SG_NALARMS; k++)
        if (c->alarms[k].active && c->alarms[k].sev > s) s = c->alarms[k].sev;
    return s;
}

/* override (1095): count, remember (the last 20), latch the MINOR override alarm for 300 s. */
void sg_override(sg_ctl *c, const char *msg)
{
    char buf[SG_MSG];      /* the alarm slot holds SG_MSG anyway */
    const int cap = (int)(sizeof c->overrideT / sizeof c->overrideT[0]);
    c->overrideCount++;
    if (c->nOverride == cap) {
        memmove(c->overrideLog[0], c->overrideLog[1], (size_t)(cap - 1) * sizeof c->overrideLog[0]);
        memmove(&c->overrideT[0], &c->overrideT[1], (size_t)(cap - 1) * sizeof c->overrideT[0]);
        c->nOverride--;
    }
    snprintf(c->overrideLog[c->nOverride], SG_MSG, "%s", msg);
    c->overrideT[c->nOverride] = c->now;
    c->nOverride++;
    snprintf(c->lastAction, sizeof c->lastAction, "%s", msg);
    snprintf(buf, sizeof buf, "override: %s", msg);
    sg_latch(c, SG_A_OVERRIDE, 1, buf, 300);
}

/* Banner (reference UI, line 2102): active alarms, most severe first, then oldest first
   (since), ties by key order; "SEV: msg" joined with "; ", or "No alarms". */
void sg_banner(const sg_ctl *c, char *buf, size_t n)
{
    int idx[SG_NALARMS], m = 0, i, j;
    size_t len = 0;
    if (n == 0) return;
    buf[0] = '\0';
    for (i = 0; i < SG_NALARMS; i++) if (c->alarms[i].active) idx[m++] = i;
    for (i = 1; i < m; i++) {                  /* insertion sort: stable, m <= 15 */
        int x = idx[i];
        const sg_alarm_slot *ax = &c->alarms[x];
        for (j = i - 1; j >= 0; j--) {
            const sg_alarm_slot *aj = &c->alarms[idx[j]];
            if (aj->sev > ax->sev || (aj->sev == ax->sev && !(ax->since < aj->since))) break;
            idx[j + 1] = idx[j];
        }
        idx[j + 1] = x;
    }
    if (m == 0) { snprintf(buf, n, "No alarms"); return; }
    for (i = 0; i < m && len < n - 1; i++) {
        const sg_alarm_slot *a = &c->alarms[idx[i]];
        int sev = (a->sev >= 0 && a->sev <= 3) ? a->sev : 0;
        int w = snprintf(buf + len, n - len, "%s%s: %s", i ? "; " : "", sevName[sev], a->msg);
        if (w < 0) break;
        len += (size_t)w;
    }
}
