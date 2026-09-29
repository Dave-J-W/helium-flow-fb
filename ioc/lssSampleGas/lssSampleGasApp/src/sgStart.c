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
