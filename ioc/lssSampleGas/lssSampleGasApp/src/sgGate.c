/* sgGate.c: the write gate and shadow logging (spec §8.3, §8.20). Safety-critical: this is the
   only path an Alicat write can take, so both directions of Par:writeEnable, an MFC disconnect,
   and an §8.21 MFC-name change must never let a stale queued action reach the Alicat. See
   sgIoc.h for the field-ownership contract (g->mfcConnected set every tick through
   sg_gate_set_connected, g->writeEnable set once by sg_gate_init and thereafter only through
   sg_gate_set_enable). */
#include <stdio.h>
#include <string.h>
#include "sgIoc.h"
#include "sgFmt.h"

static const char *const pvName[3] = { "Setpoint", "RampRate", "Run" };

static void resetShadowDedup(sg_gate *g)
{
    int i;
    for (i = 0; i < 3; i++) { g->haveShadow[i] = 0; g->lastShadowText[i][0] = '\0'; }
}

void sg_gate_init(sg_gate *g, int writeEnable)
{
    memset(g, 0, sizeof *g);
    g->writeEnable = writeEnable ? 1 : 0;
}

/* P2-R6: the per-tick copy of in.mfcConnected. A 1 -> 0 transition empties the queue here, at
   the moment the disconnect is seen, so an action queued before the disconnect can never be
   handed out after the reconnect -- whether or not the glue drained the queue in between (it
   does, every tick; sg_gate_next's own re-check would also empty it, but only if called while
   still disconnected). */
void sg_gate_set_connected(sg_gate *g, int connected)
{
    connected = connected ? 1 : 0;
    if (g->mfcConnected && !connected) g->n = 0;
    g->mfcConnected = connected;
}

/* §8.20, §8.18, §8.21 step 3: three distinct outcomes, not two. */
void sg_gate_request(sg_gate *g, sg_ctl *c, int kind, double v)
{
    char text[32];

    if (kind < 0 || kind > SG_ACT_RUN) return;

    if (g->writeEnable) {
        int i;
        if (!g->mfcConnected) return;   /* disconnected, not shadow: §8.18's alarm covers it */
        for (i = 0; i < g->n; i++) {
            if (g->q[i].kind == kind) { g->q[i].v = v; return; }   /* collapse: same position, newest v */
        }
        if (g->n < (int)(sizeof g->q / sizeof g->q[0])) {
            g->q[g->n].kind = kind;
            g->q[g->n].v = v;
            g->n++;
        }
        return;
    }

    /* Shadow mode. Dedup on the text actually logged, not the raw double: a NaN streak (sg_fmtN
       always renders non-finite as the same "–") or a value that rounds to the same 2 decimals
       logs once, not every tick. Run has no natural value: log it bare. */
    if (kind == SG_ACT_RUN) text[0] = '\0';
    else sg_fmtN(text, sizeof text, v, 2);

    if (g->haveShadow[kind] && strcmp(g->lastShadowText[kind], text) == 0) return;
    g->haveShadow[kind] = 1;
    snprintf(g->lastShadowText[kind], sizeof g->lastShadowText[kind], "%s", text);

    if (kind == SG_ACT_RUN) sg_log(c, 0, "shadow mode: would write Run");
    else sg_log(c, 0, "shadow mode: would write %s = %s", pvName[kind], text);
}

/* Re-checks at pop time, not just at the enqueue time sg_gate_request saw: an action queued while
   enabled must never be handed out after a later 1 -> 0 or MFC disconnect, however that
   condition changed since it was queued (sg_gate_set_enable already empties the queue on 1 -> 0;
   this check is the second line of defense, and the only one that catches g->mfcConnected being
   updated directly by the glue's per-tick copy). */
int sg_gate_next(sg_gate *g, sg_action *out)
{
    if (!(g->writeEnable && g->mfcConnected)) { g->n = 0; return 0; }
    if (g->n <= 0) return 0;
    *out = g->q[0];
    g->n--;
    if (g->n > 0) memmove(&g->q[0], &g->q[1], (size_t)g->n * sizeof g->q[0]);
    return 1;
}

/* §8.20: 0 -> 1 leaves the valve where it is (no action queued) and enters IDLE; 1 -> 0 leaves
   the state unchanged but must not let anything queued while enabled survive the switch, and
   must not let a shadow log dedup against a value logged long before this switch. A call with en
   equal to the current writeEnable is a no-op (no log, no state change, no queue/dedup change):
   the caller only calls this on an actual Par:writeEnable transition; the initial value at IOC
   start comes from sg_gate_init, never from here. */
void sg_gate_set_enable(sg_gate *g, sg_ctl *c, int en, double sp)
{
    char vb[32];
    en = en ? 1 : 0;
    if (en == g->writeEnable) return;
    if (en) {
        g->writeEnable = 1;
        sg_log(c, 0, "writes enabled: Alicat left at %s SLPM", sg_fmtN(vb, sizeof vb, sp, 2));
        sg_enter(c, SG_IDLE, "writes enabled");
    } else {
        g->n = 0;                  /* nothing queued while enabled may reach the Alicat now */
        g->writeEnable = 0;
        resetShadowDedup(g);       /* fresh visibility into this shadow-mode period */
        sg_log(c, 2, "writes disabled: shadow mode, Alicat holds %s SLPM",
               sg_fmtN(vb, sizeof vb, sp, 2));
    }
}
