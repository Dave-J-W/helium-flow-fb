/* sgStart.c: the glue's start-up decision and its "cannot act" alarms (user direction
   2026-09-28: "in the production code the failure state is NOT DOING ANYTHING when it should be
   doing its job"). Pure C, no EPICS: unit-tested in sgIocTest.c.

   The restart decision (sg_restart, spec §8.15 steps 2-4) is not taken while the Alicat is not
   connected: sg_restart would then go to IDLE "restart: MFC not connected" and stay idle for
   good (e.g. when the Alicat IOC boots after this one). Instead the glue waits, without a time
   limit, with the wait on the banner (Alm:Mismatch MAJOR), and runs the normal decision once the
   Alicat is there. An invalid O2 does not hold the restart (it gives OPEN_LOOP, which acts). */
#include <math.h>
#include <stdio.h>
#include <string.h>
#include "sgIoc.h"

int sg_start_decision(int configured, const sg_inputs *in)
{
    if (!configured) return SG_START_NOTCONF;
    if (in->mfcConnected && isfinite(in->sp)) return SG_START_RESTART;
    return SG_START_WAIT;
}

void sg_start_wait_alarm(sg_ctl *c, int *raised, const char *names)
{
    char msg[SG_MSG];
    /* once: the text stays as first raised (logged MAJOR once); raised again only if something
       cleared it meanwhile (e.g. an §8.21 Apply of a new MFC name clears Mismatch silently) */
    if (*raised && c->alarms[SG_A_MISMATCH].active) return;
    snprintf(msg, sizeof msg, "waiting for the Alicat PVs (%s): controller not acting yet",
             names && *names ? names : "not connected");
    sg_set_alarm(c, SG_A_MISMATCH, 2, msg);
    *raised = 1;
}

/* D2: 0 -> 1 promises "IDLE, and the Alicat changes only when the operator next presses Purge,
   Flow Zero or Resume Flow" (spec §8.20, the §13.2 dialog); a switch made while the start-up
   still waited for the Alicat was followed by the restart decision, which could enter REGULATE
   (writes from the next PID step) or OPEN_LOOP (writes at once). D8: a Release control press in
   the wait was dropped without a word, and the decision could still take over the flow. */
const char *sg_start_idle_reason(int released, int live)
{
    if (released) return "admin released control";
    if (live) return "restart: writes enabled during the start-up wait";
    return NULL;
}

void sg_start_idle(sg_ctl *c, double now, const char *reason)
{
    sg_reinit(c, now);
    sg_log(c, 0, "IOC started (autosaved settings restored)");
    sg_enter(c, SG_IDLE, reason);
}

/* G3: if the Alicat IOC's poll stops without driving its soft *_RBV records INVALID, Flow_RBV and
   Setpoint_RBV freeze in agreement and the mismatch and hold checks see nothing wrong. A CA
   monitor carries a new time stamp only with a new value, so this judges only while the Alicat
   should be flowing, where the reading keeps moving (the archive's longest unchanged run is
   ~66 min at most; the limit is 3 x that, spec §8.18). */
int sg_flow_stale(sg_stale *st, double now, int ok, unsigned tsSec, unsigned tsNsec,
                  int flowing, double limit)
{
    if (!ok || !flowing) { st->have = 0; return 0; }
    if (!st->have || tsSec != st->sec || tsNsec != st->nsec) {
        st->have = 1; st->sec = tsSec; st->nsec = tsNsec; st->since = now;
        return 0;
    }
    return now - st->since > limit;
}

/* G2: Par:writeEnable is autosaved, so a station left in shadow would otherwise come up
   regulating in shadow at every later start with "No alarms" on the banner. */
void sg_shadow_alarm(sg_ctl *c, int running, int writeEnable)
{
    if (running && !writeEnable)
        sg_set_alarm(c, SG_A_SHADOW, 2, "shadow mode: the controller is not writing to the Alicat");
    else if (c->alarms[SG_A_SHADOW].active)
        sg_clear_alarm(c, SG_A_SHADOW, !running);
}

/* D3: wrong units make every flow reading, the mismatch check, the ledger and the forecast
   wrong; an alarm only, never a refusal (spec §8.8). */
void sg_units_alarm(sg_ctl *c, const char *units)
{
    char msg[SG_MSG];
    if (!units) return;
    if (strcmp(units, "SLPM") == 0) { sg_clear_alarm(c, SG_A_UNITS, 0); return; }
    snprintf(msg, sizeof msg, "MFC flow units are %s, not SLPM", units);
    sg_set_alarm(c, SG_A_UNITS, 2, msg);
}

/* D1a: a failed pvPut logged one MINOR line and nothing else, so a lost Flow Zero left the
   panel reading "Flow stopped" while the helium kept flowing. */
void sg_put_done(sg_ctl *c, int stat[3], const char *const pv[3], int kind, int pvStat)
{
    char msg[SG_MSG];
    int k;
    if (kind < 0 || kind > 2) return;
    if (pvStat != 0 && stat[kind] == 0)
        sg_log(c, 1, "put to %s failed (pvStat %d)", pv[kind] ? pv[kind] : "?", pvStat);
    stat[kind] = pvStat;
    for (k = 0; k < 3 && stat[k] == 0; k++) {}
    if (k == 3) { sg_mfc_write_status(c, NULL); return; }
    snprintf(msg, sizeof msg, "MFC write failed: %s (pvStat %d): controller cannot act",
             pv[k] ? pv[k] : "?", stat[k]);
    sg_mfc_write_status(c, msg);
}

void sg_not_configured_alarm(sg_ctl *c, int *raised, int on, const char *text)
{
    char msg[SG_MSG];
    if (on) {
        snprintf(msg, sizeof msg, "not configured: %s", text);
        sg_set_alarm(c, SG_A_MISMATCH, 2, msg);   /* logs only when the text changes */
        *raised = 1;
    } else if (*raised) {
        sg_clear_alarm(c, SG_A_MISMATCH, 1);
        *raised = 0;
    }
}
