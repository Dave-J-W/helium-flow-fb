/* sgIocTest.c: unit tests for the pure-C IOC glue pieces (sgIoc.h): the write gate and shadow
   logging (spec §8.3, §8.20), the PV-name apply rules (spec §8.21), the persistent monthly log
   (spec §8.19, §7.6), and the usage-report text (spec §7.7 He:Rep:Text). No EPICS dependency.
   Each test returns 0 (pass) or 1 (fail, printing "FAIL <name>: ..."), the convention of
   sgUnitTest.c. Plan 2 task 2, fix round 1 (write-gate safety findings). */
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#if defined(_WIN32)
#include <direct.h>
#define SG_MKDIR(p) _mkdir(p)
#else
#include <sys/stat.h>
#define SG_MKDIR(p) mkdir(p, 0755)
#endif
#include "sgCore.h"
#include "sgIoc.h"
#include "sgFmt.h"

/* ---------------------------------------------------------------- effect capture (as sgUnitTest.c) */
#define MAXLOG 512
static char logMsg[MAXLOG][SG_MSG + 64];
static int logSev[MAXLOG];
static int nLog;

static void cbLog(void *ctx, double t, int sev, const char *msg)
{
    (void)ctx; (void)t;
    if (nLog < MAXLOG) { snprintf(logMsg[nLog], sizeof logMsg[nLog], "%s", msg); logSev[nLog] = sev; }
    nLog++;
}
static void cbSp(void *ctx, double v) { (void)ctx; (void)v; }
static void cbRamp(void *ctx, double v) { (void)ctx; (void)v; }
static void cbRun(void *ctx) { (void)ctx; }

static sg_ctl C;      /* ~250 KB: static, not on the stack (as sgUnitTest.c) */

static void newCtl(void)
{
    sg_io io;
    memset(&io, 0, sizeof io);
    io.log = cbLog; io.put_setpoint = cbSp; io.put_ramp = cbRamp; io.put_run = cbRun;
    sg_init(&C, &io, 0);
    nLog = 0;      /* discard sg_init's own "IOC started" log line (sg_enter, sgCore.c:121) */
}

static int countLog(int sev, const char *msg)
{
    int i, n = 0, lim = nLog < MAXLOG ? nLog : MAXLOG;
    for (i = 0; i < lim; i++) if (logSev[i] == sev && strcmp(logMsg[i], msg) == 0) n++;
    return n;
}

#define CHECK(name, cond, ...)                                                     \
    do {                                                                           \
        if (!(cond)) {                                                             \
            printf("FAIL %s: ", name); printf(__VA_ARGS__); printf("\n");          \
            return 1;                                                              \
        }                                                                          \
    } while (0)

/* ================================================================ 1. gate, enabled + connected */
static int t_gate_enabled(void)
{
    const char *T = "G1 gate enabled+connected";
    sg_gate g; sg_action a;
    memset(&g, 0, sizeof g);
    g.writeEnable = 1; g.mfcConnected = 1;
    newCtl();
    sg_gate_request(&g, &C, SG_ACT_SP, 0.29);
    CHECK(T, g.n == 1, "n %d, want 1", g.n);
    CHECK(T, sg_gate_next(&g, &a) == 1, "sg_gate_next returned 0 on a non-empty queue");
    CHECK(T, a.kind == SG_ACT_SP && a.v == 0.29, "popped kind %d v %g, want SP 0.29", a.kind, a.v);
    CHECK(T, sg_gate_next(&g, &a) == 0, "sg_gate_next returned 1 on an empty queue");
    CHECK(T, nLog == 0, "unexpected log line(s): %d", nLog);
    return 0;
}

/* ================================================================ 2. gate, shadow mode dedup */
static int t_gate_shadow(void)
{
    const char *T = "G2 gate shadow dedup";
    sg_gate g; sg_action a;
    memset(&g, 0, sizeof g);
    g.writeEnable = 0; g.mfcConnected = 1;
    newCtl();
    sg_gate_request(&g, &C, SG_ACT_SP, 0.29);
    CHECK(T, g.n == 0, "an action was queued in shadow mode");
    CHECK(T, sg_gate_next(&g, &a) == 0, "an action was queued in shadow mode");
    CHECK(T, countLog(0, "shadow mode: would write Setpoint = 0.29") == 1, "log line missing");
    sg_gate_request(&g, &C, SG_ACT_SP, 0.29);
    CHECK(T, nLog == 1, "identical repeat logged again (nLog %d)", nLog);
    sg_gate_request(&g, &C, SG_ACT_SP, 0.30);
    CHECK(T, countLog(0, "shadow mode: would write Setpoint = 0.30") == 1,
          "changed value was not logged");
    CHECK(T, nLog == 2, "nLog %d, want 2", nLog);
    return 0;
}

/* ================================================================ 3. write-enable transitions */
static int t_gate_enable_transitions(void)
{
    const char *T = "G3 write-enable transitions";
    sg_gate g; sg_action a;
    memset(&g, 0, sizeof g);
    newCtl();
    sg_gate_set_enable(&g, &C, 1, 0.42);
    CHECK(T, g.writeEnable == 1, "writeEnable not set");
    CHECK(T, C.state == SG_IDLE, "state %s, want IDLE", sg_state_names[C.state]);
    CHECK(T, countLog(0, "writes enabled: Alicat left at 0.42 SLPM") == 1, "enable text missing");
    CHECK(T, g.n == 0, "an action was queued at the enable switch");

    newCtl();
    sg_enter(&C, SG_REGULATE, "test");
    g.writeEnable = 1; g.mfcConnected = 1;
    sg_gate_request(&g, &C, SG_ACT_SP, 0.29);
    CHECK(T, g.n == 1, "setup: action not queued before disabling");
    sg_gate_set_enable(&g, &C, 0, 0.55);
    CHECK(T, g.writeEnable == 0, "writeEnable not cleared");
    CHECK(T, C.state == SG_REGULATE, "state %s, want REGULATE (unchanged)",
          sg_state_names[C.state]);
    CHECK(T, countLog(2, "writes disabled: shadow mode, Alicat holds 0.55 SLPM") == 1,
          "disable MAJOR text missing");
    CHECK(T, g.n == 0, "queue not emptied by the 1->0 switch");
    CHECK(T, sg_gate_next(&g, &a) == 0, "an action queued before 1->0 was handed out after it");
    return 0;
}

/* ================================================================ 4. drop while disconnected
   (finding 5): writeEnable=1 but mfcConnected=0 is not shadow mode -- it must be silent (the
   core's own §8.18 Mismatch alarm already reports the disconnect; a "shadow mode: would write"
   line here would be actively misleading). */
static int t_gate_drop_disconnected(void)
{
    const char *T = "G4 enabled but disconnected: drop silently";
    sg_gate g; sg_action a;
    memset(&g, 0, sizeof g);
    g.writeEnable = 1; g.mfcConnected = 0;
    newCtl();
    sg_gate_request(&g, &C, SG_ACT_SP, 0.5);
    CHECK(T, g.n == 0, "an action was queued while disconnected");
    CHECK(T, sg_gate_next(&g, &a) == 0, "sg_gate_next returned an action");
    CHECK(T, nLog == 0, "unexpected log line(s) for a disconnected drop: %d", nLog);
    return 0;
}

/* ================================================================ 5. a queued action must not
   survive a later 1->0 (finding 1, test 1) -- via the real sg_gate_set_enable transition. */
static int t_gate_requeue_on_disable(void)
{
    const char *T = "G5 queued action does not survive 1->0";
    sg_gate g; sg_action a;
    memset(&g, 0, sizeof g);
    g.writeEnable = 1; g.mfcConnected = 1;
    newCtl();
    sg_gate_request(&g, &C, SG_ACT_SP, 0.29);
    CHECK(T, g.n == 1, "setup: action not queued");
    sg_gate_set_enable(&g, &C, 0, 0.29);
    CHECK(T, g.n == 0, "queue not emptied by sg_gate_set_enable(1->0)");
    CHECK(T, sg_gate_next(&g, &a) == 0, "sg_gate_next returned an action queued before 1->0");
    return 0;
}

/* ================================================================ 6. a queued action must not
   survive an MFC disconnect either (finding 1, test 2) -- via a direct mfcConnected mutation, as
   the glue's per-tick copy of in.mfcConnected would do; this exercises sg_gate_next's own
   defensive re-check, independently of sg_gate_set_enable's belt-and-suspenders clear. */
static int t_gate_requeue_on_disconnect(void)
{
    const char *T = "G6 queued action does not survive an MFC disconnect";
    sg_gate g; sg_action a;
    memset(&g, 0, sizeof g);
    g.writeEnable = 1; g.mfcConnected = 1;
    newCtl();
    sg_gate_request(&g, &C, SG_ACT_SP, 0.29);
    CHECK(T, g.n == 1, "setup: action not queued");
    g.mfcConnected = 0;
    CHECK(T, sg_gate_next(&g, &a) == 0, "sg_gate_next handed out an action after a disconnect");
    CHECK(T, g.n == 0, "sg_gate_next did not empty the queue on a disconnect");
    return 0;
}

/* ================================================================ 7. collapse by kind, so
   overflow cannot occur with only 3 kinds (finding 2). A replaced entry keeps its queue
   position; the final value per kind is always the newest requested. */
static int t_gate_collapse(void)
{
    const char *T = "G7 gate collapses same-kind requests (no overflow)";
    sg_gate g; sg_action a; int i;
    memset(&g, 0, sizeof g);
    g.writeEnable = 1; g.mfcConnected = 1;
    newCtl();
    sg_gate_request(&g, &C, SG_ACT_SP, 0.1);
    sg_gate_request(&g, &C, SG_ACT_SP, 0.2);
    sg_gate_request(&g, &C, SG_ACT_RAMP, 1.0);
    sg_gate_request(&g, &C, SG_ACT_SP, 0.3);
    CHECK(T, g.n == 2, "n %d, want 2 (one SP, one RAMP)", g.n);
    CHECK(T, g.q[0].kind == SG_ACT_SP && g.q[0].v == 0.3,
          "SP not collapsed to the newest value in place: kind %d v %g", g.q[0].kind, g.q[0].v);
    CHECK(T, g.q[1].kind == SG_ACT_RAMP && g.q[1].v == 1.0,
          "RAMP entry disturbed by the SP collapse: kind %d v %g", g.q[1].kind, g.q[1].v);

    for (i = 0; i < 50; i++) sg_gate_request(&g, &C, SG_ACT_SP, 10.0 + i);
    CHECK(T, g.n == 2, "n %d after 50 same-kind requests, want 2 (still no overflow)", g.n);
    CHECK(T, g.q[0].v == 59.0, "SP is not the newest of the 50 (v %g)", g.q[0].v);

    CHECK(T, sg_gate_next(&g, &a) == 1 && a.kind == SG_ACT_SP && a.v == 59.0, "drain order 1");
    CHECK(T, sg_gate_next(&g, &a) == 1 && a.kind == SG_ACT_RAMP && a.v == 1.0, "drain order 2");
    CHECK(T, sg_gate_next(&g, &a) == 0, "queue not empty after draining both entries");
    return 0;
}

/* ================================================================ 8. Run logged without a
   value; a NaN streak logs once (finding 6). */
static int t_gate_run_and_nan(void)
{
    const char *T = "G8 shadow Run without a value; NaN streak logs once";
    sg_gate g;
    memset(&g, 0, sizeof g);
    g.writeEnable = 0; g.mfcConnected = 1;
    newCtl();

    sg_gate_request(&g, &C, SG_ACT_RUN, 0.0);      /* v is irrelevant for Run */
    CHECK(T, countLog(0, "shadow mode: would write Run") == 1, "Run text missing/wrong");
    sg_gate_request(&g, &C, SG_ACT_RUN, 0.0);
    CHECK(T, nLog == 1, "Run repeat logged again (nLog %d)", nLog);

    sg_gate_request(&g, &C, SG_ACT_RAMP, NAN);
    sg_gate_request(&g, &C, SG_ACT_RAMP, NAN);
    sg_gate_request(&g, &C, SG_ACT_RAMP, NAN);
    CHECK(T, countLog(0, "shadow mode: would write RampRate = \xe2\x80\x93") == 1,
          "NaN streak did not log exactly once");
    CHECK(T, nLog == 2, "nLog %d, want 2 (Run once + the NaN streak once)", nLog);
    return 0;
}

/* ================================================================ 9. sg_gate_init: the startup
   value is not a transition (finding 3). */
static int t_gate_init(void)
{
    const char *T = "G9 sg_gate_init sets the startup value without a transition";
    sg_gate g;
    int base;

    /* documented sequence: init(0), then a later set_enable(1) does the 0->1 transition */
    memset(&g, 0, sizeof g);
    newCtl();
    sg_enter(&C, SG_REGULATE, "test");        /* so IDLE below can only have come from set_enable */
    sg_gate_init(&g, 0);
    CHECK(T, g.writeEnable == 0 && g.n == 0, "init(0) did not set a clean, disabled gate");
    sg_gate_set_enable(&g, &C, 1, 0.42);
    CHECK(T, g.writeEnable == 1, "set_enable(1) did not take effect");
    CHECK(T, C.state == SG_IDLE, "set_enable(1) did not enter IDLE (state %s)",
          sg_state_names[C.state]);

    /* init(1) at start (autosave already had writes enabled) must not log or enter IDLE: it is
       not a transition, just the initial value. base is taken after sg_enter, which logs its own
       transition line (plus, entering REGULATE, a "settling" line) -- init(1) must add nothing
       beyond that baseline. */
    newCtl();
    sg_enter(&C, SG_REGULATE, "test");
    base = nLog;
    sg_gate_init(&g, 1);
    CHECK(T, g.writeEnable == 1, "init(1) did not set the value");
    CHECK(T, C.state == SG_REGULATE, "init(1) changed state (it must never touch sg_ctl): %s",
          sg_state_names[C.state]);
    CHECK(T, nLog == base, "init(1) logged something (nLog %d, base %d)", nLog, base);
    return 0;
}

/* ================================================================ 9b. P2-R6: a queued action
   does not survive a disconnect/reconnect even when the queue was NOT drained while
   disconnected (sg_gate_next's own re-check never ran). sg_gate_set_connected's 1 -> 0 is what
   empties it; a later 0 -> 1 must not bring anything back. */
static int t_gate_set_connected(void)
{
    const char *T = "G10 sg_gate_set_connected: queued SP, disconnect, reconnect -> nothing drained";
    sg_gate g; sg_action a;
    newCtl();
    sg_gate_init(&g, 1);
    sg_gate_set_connected(&g, 1);
    CHECK(T, g.mfcConnected == 1, "set_connected(1) did not set the flag");
    sg_gate_request(&g, &C, SG_ACT_SP, 0.29);
    CHECK(T, g.n == 1, "setup: action not queued");
    sg_gate_set_connected(&g, 0);
    CHECK(T, g.mfcConnected == 0 && g.n == 0, "1 -> 0 did not empty the queue (n %d)", g.n);
    sg_gate_set_connected(&g, 1);
    CHECK(T, sg_gate_next(&g, &a) == 0, "an action queued before the disconnect was handed out");
    /* the same flag value again is not a transition: a queued action survives it */
    sg_gate_request(&g, &C, SG_ACT_RAMP, 5.0);
    sg_gate_set_connected(&g, 1);
    CHECK(T, g.n == 1, "set_connected(1) while connected emptied the queue");
    CHECK(T, sg_gate_next(&g, &a) == 1 && a.kind == SG_ACT_RAMP && a.v == 5.0,
          "queued RAMP not handed out while connected");
    CHECK(T, nLog == 0, "unexpected log line(s): %d", nLog);
    return 0;
}

/* ================================================================ 9c. start-up without the
   Alicat (user direction 2026-09-28): no restart decision until the MFC is connected; the wait
   is an alarm; once connected the normal decision runs (an invalid O2 gives OPEN_LOOP). */
static sg_inputs mkIn(int mfc, double sp, double o2, int o2Sevr)
{
    sg_inputs in;
    memset(&in, 0, sizeof in);
    in.mfcConnected = mfc; in.sp = sp; in.flow = sp; in.ramp = 3; in.total = 100;
    in.o2 = o2; in.o2Sevr = o2Sevr; in.running = 1;
    snprintf(in.gas, sizeof in.gas, "He");
    return in;
}

static int t_start_decision(void)
{
    const char *T = "S1 sg_start_decision";
    sg_inputs in;
    in = mkIn(0, NAN, NAN, 3);
    CHECK(T, sg_start_decision(0, &in) == SG_START_NOTCONF, "unconfigured not NOTCONF");
    CHECK(T, sg_start_decision(1, &in) == SG_START_WAIT, "MFC disconnected not WAIT");
    in = mkIn(1, NAN, NAN, 3);
    CHECK(T, sg_start_decision(1, &in) == SG_START_WAIT, "Setpoint_RBV NaN not WAIT");
    in = mkIn(1, 0.3, NAN, 3);
    CHECK(T, sg_start_decision(1, &in) == SG_START_RESTART,
          "MFC connected with O2 invalid not RESTART (O2 must not hold the restart)");
    return 0;
}

static int t_start_wait_alarm(void)
{
    const char *T = "S2 start-up wait: alarm once, cleared by the restart, OPEN_LOOP with O2 invalid";
    const char *msg = "waiting for the Alicat PVs (SIM:Alicat1:* (all Alicat channels)): "
                      "controller not acting yet";
    sg_inputs in;
    int raised = 0, i;
    newCtl();
    in = mkIn(0, NAN, NAN, 3);
    sg_set_inputs(&C, &in);
    for (i = 0; i < 5; i++) sg_start_wait_alarm(&C, &raised, "SIM:Alicat1:* (all Alicat channels)");
    CHECK(T, C.alarms[SG_A_MISMATCH].active && C.alarms[SG_A_MISMATCH].sev == 2,
          "Mismatch MAJOR not raised while waiting");
    CHECK(T, strcmp(C.alarms[SG_A_MISMATCH].msg, msg) == 0, "text: %s", C.alarms[SG_A_MISMATCH].msg);
    CHECK(T, countLog(2, msg) == 1 && nLog == 1, "wait not logged MAJOR exactly once (nLog %d)", nLog);
    sg_channels_changed(&C, 0, 1);            /* an §8.21 Apply clears Mismatch silently ... */
    sg_start_wait_alarm(&C, &raised, "SIM:Alicat2:* (all Alicat channels)");
    CHECK(T, C.alarms[SG_A_MISMATCH].active, "wait alarm not raised again after it was cleared");
    /* the Alicat connects: the normal decision; O2 invalid, setpoint > 0 -> OPEN_LOOP (acts) */
    in = mkIn(1, 0.3, NAN, 3);
    sg_set_inputs(&C, &in);
    CHECK(T, sg_start_decision(1, &in) == SG_START_RESTART, "decision not RESTART once connected");
    sg_restart(&C, 1000);
    CHECK(T, C.state == SG_OPEN_LOOP, "state %s, want OPEN_LOOP", sg_state_names[C.state]);
    CHECK(T, !(C.alarms[SG_A_MISMATCH].active && strcmp(C.alarms[SG_A_MISMATCH].msg, msg) == 0),
          "the wait alarm survived the restart");
    return 0;
}

static int t_not_configured_alarm(void)
{
    const char *T = "S3 not-configured alarm while writes are enabled";
    int raised = 0, i;
    newCtl();
    for (i = 0; i < 3; i++) sg_not_configured_alarm(&C, &raised, 1, "Cfg:MFC is empty");
    CHECK(T, C.alarms[SG_A_MISMATCH].active && C.alarms[SG_A_MISMATCH].sev == 2,
          "Mismatch MAJOR not raised");
    CHECK(T, countLog(2, "not configured: Cfg:MFC is empty") == 1 && nLog == 1,
          "not logged MAJOR exactly once (nLog %d)", nLog);
    sg_not_configured_alarm(&C, &raised, 0, "");
    CHECK(T, !C.alarms[SG_A_MISMATCH].active && !raised, "not cleared when off");
    CHECK(T, nLog == 1, "the clear was not silent (nLog %d)", nLog);
    sg_set_alarm(&C, SG_A_MISMATCH, 2, "flow mismatch: cylinder empty or MFC fault?");
    sg_not_configured_alarm(&C, &raised, 0, "");
    CHECK(T, C.alarms[SG_A_MISMATCH].active, "cleared a Mismatch it had not raised");
    return 0;
}

/* ================================================================ 10. PV-name apply rules */
static sg_names mkNames(const char *mfc, const char *o2, const char *cyl, const char *stn)
{
    sg_names n;
    memset(&n, 0, sizeof n);
    snprintf(n.mfc, sizeof n.mfc, "%s", mfc);
    snprintf(n.o2, sizeof n.o2, "%s", o2);
    snprintf(n.cyl, sizeof n.cyl, "%s", cyl);
    snprintf(n.stn, sizeof n.stn, "%s", stn);
    return n;
}

static int t_cfg_reject_state(void)
{
    const char *T = "C1 cfg reject state";
    sg_names active = mkNames("15IDC:Alicat1:", "15IDC:D1Dmm_calc", "", "15IDC");
    sg_names active0 = active;
    sg_names edit = mkNames("SIM:Alicat2:", "15IDC:D1Dmm_calc", "", "15IDC");
    char status[128]; int mfcChanged = 777;               /* sentinel: must stay untouched */
    int rc = sg_cfg_apply(&edit, &active, SG_REGULATE, &mfcChanged, status, sizeof status);
    CHECK(T, rc == SG_CFG_REJECT_STATE, "rc %d, want SG_CFG_REJECT_STATE", rc);
    CHECK(T, strcmp(status, "rejected: release control first (state REGULATE)") == 0,
          "status '%s'", status);
    CHECK(T, memcmp(&active, &active0, sizeof active) == 0, "active names changed");
    CHECK(T, mfcChanged == 777, "mfcChanged %d, want left untouched (777)", mfcChanged);
    return 0;
}

static int t_cfg_changed_o2(void)
{
    const char *T = "C2 cfg changed O2 in IDLE";
    sg_names active = mkNames("15IDC:Alicat1:", "15IDC:D1Dmm_calc", "", "15IDC");
    sg_names edit = mkNames("15IDC:Alicat1:", "SIM:O2b", "", "15IDC");
    char status[128]; int mfcChanged = -1;
    int rc = sg_cfg_apply(&edit, &active, SG_IDLE, &mfcChanged, status, sizeof status);
    CHECK(T, rc == SG_CFG_OK, "rc %d, want SG_CFG_OK", rc);
    CHECK(T, mfcChanged == 0, "mfcChanged %d, want 0", mfcChanged);
    CHECK(T, strcmp(active.o2, "SIM:O2b") == 0, "active.o2 '%s'", active.o2);
    return 0;
}

static int t_cfg_changed_mfc(void)
{
    const char *T = "C3 cfg changed MFC in IDLE";
    sg_names active = mkNames("15IDC:Alicat1:", "15IDC:D1Dmm_calc", "", "15IDC");
    sg_names edit = mkNames("SIM:Alicat2:", "15IDC:D1Dmm_calc", "", "15IDC");
    char status[128]; int mfcChanged = -1;
    int rc = sg_cfg_apply(&edit, &active, SG_IDLE, &mfcChanged, status, sizeof status);
    CHECK(T, rc == SG_CFG_OK, "rc %d, want SG_CFG_OK", rc);
    CHECK(T, mfcChanged == 1, "mfcChanged %d, want 1", mfcChanged);
    CHECK(T, strcmp(active.mfc, "SIM:Alicat2:") == 0, "active.mfc '%s'", active.mfc);
    return 0;
}

static int t_cfg_unchanged(void)
{
    const char *T = "C4 cfg unchanged";
    sg_names active = mkNames("15IDC:Alicat1:", "15IDC:D1Dmm_calc", "", "15IDC");
    sg_names edit = active;
    char status[128]; int mfcChanged = -1;
    int rc = sg_cfg_apply(&edit, &active, SG_IDLE, &mfcChanged, status, sizeof status);
    CHECK(T, rc == SG_CFG_UNCHANGED, "rc %d, want SG_CFG_UNCHANGED", rc);
    CHECK(T, mfcChanged == 0, "mfcChanged %d, want 0", mfcChanged);
    return 0;
}

static int t_cfg_not_configured(void)
{
    const char *T = "C5 cfg empty O2 not configured";
    sg_names active = mkNames("15IDC:Alicat1:", "15IDC:D1Dmm_calc", "", "15IDC");
    sg_names edit = mkNames("15IDC:Alicat1:", "", "", "15IDC");
    char status[128]; int mfcChanged = -1;
    int rc = sg_cfg_apply(&edit, &active, SG_IDLE, &mfcChanged, status, sizeof status);
    CHECK(T, rc == SG_CFG_NOT_CONFIGURED, "rc %d, want SG_CFG_NOT_CONFIGURED", rc);
    CHECK(T, strcmp(status, "not configured: Cfg:O2 is empty") == 0, "status '%s'", status);
    CHECK(T, mfcChanged == 0, "mfcChanged %d, want 0 (MFC unchanged)", mfcChanged);
    CHECK(T, strcmp(active.mfc, "15IDC:Alicat1:") == 0 && strcmp(active.o2, "") == 0,
          "*active not updated: mfc '%s' o2 '%s'", active.mfc, active.o2);
    return 0;
}

static int t_cfg_empty_mfc(void)
{
    const char *T = "C7 cfg empty MFC not configured";
    sg_names active = mkNames("15IDC:Alicat1:", "15IDC:D1Dmm_calc", "", "15IDC");
    sg_names edit = mkNames("", "15IDC:D1Dmm_calc", "", "15IDC");
    char status[128]; int mfcChanged = -1;
    int rc = sg_cfg_apply(&edit, &active, SG_IDLE, &mfcChanged, status, sizeof status);
    CHECK(T, rc == SG_CFG_NOT_CONFIGURED, "rc %d, want SG_CFG_NOT_CONFIGURED", rc);
    CHECK(T, strcmp(status, "not configured: Cfg:MFC is empty") == 0, "status '%s'", status);
    CHECK(T, mfcChanged == 1, "mfcChanged %d, want 1 (MFC went from set to empty)", mfcChanged);
    CHECK(T, strcmp(active.mfc, "") == 0 && strcmp(active.o2, "15IDC:D1Dmm_calc") == 0,
          "*active not updated: mfc '%s' o2 '%s'", active.mfc, active.o2);
    return 0;
}

static int t_cfg_trim(void)
{
    const char *T = "C6 cfg trims surrounding spaces";
    sg_names active = mkNames("15IDC:Alicat1:", "15IDC:D1Dmm_calc", "", "15IDC");
    sg_names edit = mkNames("  15IDC:Alicat1:  ", " 15IDC:D1Dmm_calc ", "  ", " 15IDC ");
    char status[128]; int mfcChanged = -1;
    int rc = sg_cfg_apply(&edit, &active, SG_IDLE, &mfcChanged, status, sizeof status);
    CHECK(T, rc == SG_CFG_UNCHANGED, "rc %d, want SG_CFG_UNCHANGED (trimmed edit == active)", rc);
    return 0;
}

/* Spec §8.21 step 5 with §8.15: after an Alicat PV change the helium state is saved at once (the
   re-baselined, unset reference) and again once the new totalizer's reference is set, so a
   restart never restores the old Alicat's reference (bench test_pvnames, 2026-09-29). */
static int t_cfg_he_save(void)
{
    const char *T = "C8 helium autosave on an Alicat PV change";
    int pending = 0;
    newCtl();
    C.lastTotal = 1234.5; C.cylBase = 1000.0; C.in.total = 1234.5;
    CHECK(T, sg_cfg_he_save(&pending, 0, C.lastTotal) == 0 && pending == 0,
          "saved with no change (pending %d)", pending);
    sg_channels_changed(&C, 0, 1);                            /* the apply tick */
    CHECK(T, !isfinite(C.lastTotal), "lastTotal %g after the change, want unset", C.lastTotal);
    CHECK(T, sg_cfg_he_save(&pending, 1, C.lastTotal) == 1, "no save on the change itself");
    CHECK(T, pending == 1, "pending %d after the change, want 1", pending);
    CHECK(T, sg_cfg_he_save(&pending, 0, C.lastTotal) == 0 && pending == 1,
          "saved (or dropped pending) before the new reference (pending %d)", pending);
    C.lastTotal = 3.25;                                       /* the new Alicat's first reading */
    CHECK(T, sg_cfg_he_save(&pending, 0, C.lastTotal) == 1, "no save once the new reference is set");
    CHECK(T, pending == 0, "pending %d after the second save, want 0", pending);
    CHECK(T, sg_cfg_he_save(&pending, 0, C.lastTotal) == 0, "saved again with nothing changed");
    return 0;
}

/* ================================================================ 11. persistent log */

/* Mirrors sgLogFile.c's line format exactly, for exact-line comparison in the tests below (per
   fix-round-1 finding 9: compare complete lines built with localtime, not substrings). */
static void formatExpectedLine(char *buf, size_t n, double epochT, int sev, const char *stn,
                                const char *msg)
{
    time_t tt = (time_t)epochT;
    struct tm *tm = localtime(&tt);
    char ts[40];
    const char *sevStr = (sev == 1) ? "MINOR" : (sev == 2) ? "MAJOR" : "";
    if (tm) {
        snprintf(ts, sizeof ts, "%04d-%02d-%02d %02d:%02d:%02d", tm->tm_year + 1900,
                 tm->tm_mon + 1, tm->tm_mday, tm->tm_hour, tm->tm_min, tm->tm_sec);
    } else {
        snprintf(ts, sizeof ts, "\?\?\?\?-\?\?-\?\? \?\?:\?\?:\?\?");
    }
    snprintf(buf, n, "%s  %s  %-5s  %s", ts, stn, sevStr, msg);
}

/* A local-time epoch for a given calendar date/time, built with mktime so it is exactly what
   localtime() will report back for that epoch (mktime and localtime are inverses), regardless of
   which timezone the test happens to run in -- unlike a hand-picked "magic" epoch number, which
   would land on a different local date depending on TZ. */
static double localEpoch(int year, int mon1to12, int day, int hh, int mm, int ss)
{
    struct tm t;
    memset(&t, 0, sizeof t);
    t.tm_year = year - 1900; t.tm_mon = mon1to12 - 1; t.tm_mday = day;
    t.tm_hour = hh; t.tm_min = mm; t.tm_sec = ss; t.tm_isdst = -1;
    return (double)mktime(&t);
}

static int t_logfile(void)
{
    const char *T = "L1 log file";
    sg_logfile L;
    FILE *f;
    char line[512], text[4096], expect1[400], expect2[400], expectText[820];
    double t0 = 1789900000.0;      /* September 2026 */
    int i;
    size_t used;
    const char *path = "sgIocTest_logs/sampleGas_SIM_2026-09.log";

    SG_MKDIR("sgIocTest_logs");
    remove(path);           /* determinism across repeated runs, not just a fresh directory */

    sg_logfile_init(&L, "sgIocTest_logs", "SIM");
    sg_logfile_line(&L, t0, 1, "first line");
    sg_logfile_line(&L, t0 + 1, 2, "second line");

    formatExpectedLine(expect1, sizeof expect1, t0, 1, "SIM", "first line");
    formatExpectedLine(expect2, sizeof expect2, t0 + 1, 2, "SIM", "second line");

    f = fopen(path, "r");
    CHECK(T, f != NULL, "log file '%s' not created", path);
    CHECK(T, fgets(line, sizeof line, f) != NULL, "line 1 missing");
    line[strcspn(line, "\r\n")] = '\0';
    CHECK(T, strcmp(line, expect1) == 0, "line 1 '%s', want '%s'", line, expect1);
    CHECK(T, fgets(line, sizeof line, f) != NULL, "line 2 missing");
    line[strcspn(line, "\r\n")] = '\0';
    CHECK(T, strcmp(line, expect2) == 0, "line 2 '%s', want '%s'", line, expect2);
    CHECK(T, fgets(line, sizeof line, f) == NULL, "file has more than the 2 lines written");
    fclose(f);

    used = sg_logfile_text(&L, text, sizeof text);
    CHECK(T, used == strlen(text), "sg_logfile_text returned %zu, strlen %zu", used, strlen(text));
    snprintf(expectText, sizeof expectText, "%s\n%s", expect2, expect1);
    CHECK(T, strcmp(text, expectText) == 0, "text mismatch:\n--- got ---\n%s\n--- want ---\n%s",
          text, expectText);

    sg_logfile_init(&L, "sgIocTest_logs", "SIM");
    for (i = 0; i < 250; i++) {
        char msg[32];
        snprintf(msg, sizeof msg, "line %d", i);
        sg_logfile_line(&L, t0 + i, 0, msg);
    }
    CHECK(T, L.count == SG_LOGRING, "ring count %d, want %d", L.count, SG_LOGRING);
    used = sg_logfile_text(&L, text, sizeof text);
    (void)used;
    CHECK(T, strstr(text, "line 249") != NULL, "ring missing the newest line");
    CHECK(T, strstr(text, "line 49") == NULL,
          "ring kept more than the newest 200 lines (found 'line 49')");
    return 0;
}

/* Month rollover: two lines with epochs in different months must land in two different files
   (fix-round-1 finding 9). Uses its own station label ("MB") so its files can never collide with
   t_logfile's "SIM" files. */
static int t_logfile_month_boundary(void)
{
    const char *T = "L2 log file month boundary (two files)";
    sg_logfile L;
    FILE *f;
    char line[512], expect[400];
    double augT = localEpoch(2026, 8, 15, 12, 0, 0);
    double sepT = localEpoch(2026, 9, 15, 12, 0, 0);
    const char *augPath = "sgIocTest_logs/sampleGas_MB_2026-08.log";
    const char *sepPath = "sgIocTest_logs/sampleGas_MB_2026-09.log";

    SG_MKDIR("sgIocTest_logs");
    remove(augPath);
    remove(sepPath);

    sg_logfile_init(&L, "sgIocTest_logs", "MB");
    sg_logfile_line(&L, augT, 0, "aug line");
    sg_logfile_line(&L, sepT, 0, "sep line");

    f = fopen(augPath, "r");
    CHECK(T, f != NULL, "august file '%s' not created", augPath);
    CHECK(T, fgets(line, sizeof line, f) != NULL, "august line missing");
    line[strcspn(line, "\r\n")] = '\0';
    formatExpectedLine(expect, sizeof expect, augT, 0, "MB", "aug line");
    CHECK(T, strcmp(line, expect) == 0, "august line '%s', want '%s'", line, expect);
    CHECK(T, fgets(line, sizeof line, f) == NULL, "august file has the september line too");
    fclose(f);

    f = fopen(sepPath, "r");
    CHECK(T, f != NULL, "september file '%s' not created", sepPath);
    CHECK(T, fgets(line, sizeof line, f) != NULL, "september line missing");
    line[strcspn(line, "\r\n")] = '\0';
    formatExpectedLine(expect, sizeof expect, sepT, 0, "MB", "sep line");
    CHECK(T, strcmp(line, expect) == 0, "september line '%s', want '%s'", line, expect);
    CHECK(T, fgets(line, sizeof line, f) == NULL, "september file has the august line too");
    fclose(f);
    return 0;
}

/* A file-write failure (nonexistent directory) must never abort the process, and the line must
   still land in the ring even though it could not be written to disk. The stderr latch itself
   (finding 7: "reported once per failure streak") is not asserted here -- that would need to
   capture this process's own stderr, which is out of scope for this quick robustness check; the
   report accompanying this fix round records that as a known gap. */
static int t_logfile_write_error(void)
{
    const char *T = "L3 log file write error never aborts";
    sg_logfile L;
    sg_logfile_init(&L, "sgIocTest_logs/no_such_subdir_xyz", "SIM");
    sg_logfile_line(&L, 1789900000.0, 0, "one");
    sg_logfile_line(&L, 1789900001.0, 0, "two");
    CHECK(T, L.count == 2, "ring not updated despite the fopen failure (count %d)", L.count);
    return 0;
}

/* ================================================================ 12. usage-report text */
static void localDate(char *buf, size_t n, double t)
{
    time_t tt = (time_t)t;
    struct tm *tm = localtime(&tt);
    snprintf(buf, n, "%04d-%02d-%02d %02d:%02d", tm->tm_year + 1900, tm->tm_mon + 1, tm->tm_mday,
             tm->tm_hour, tm->tm_min);
}

static int t_report_text(void)
{
    const char *T = "R1 report text";
    sg_report r;
    char buf[2048], expect[2048];
    char d0[64], d1[64], d2[64], d3[64], d4[64];
    char line0[256], line1[256];

    newCtl();
    C.p.reportDays = 60; C.p.cylCapacityL = 8000; C.p.runGap = 36000;      /* 10 h */

    memset(&r, 0, sizeof r);
    r.wStart = 1788480000.0;
    r.wEnd   = 1789948800.0;
    r.dispensed = 1234; r.inRuns = 900; r.cyls = 2; r.cylEquiv = 0.15;
    r.nRuns = 2;
    r.runs[0].start = 1788500000.0; r.runs[0].end = 1788600000.0;
    r.runs[0].L = 400; r.runs[0].purges = 3; r.runs[0].cylinders = 0; r.runs[0].finished = 1;
    r.runs[1].start = 1789800000.0; r.runs[1].end = NAN;
    r.runs[1].L = 500; r.runs[1].purges = 12; r.runs[1].cylinders = 1; r.runs[1].finished = 0;

    sg_report_text(&C, &r, buf, sizeof buf);

    localDate(d0, sizeof d0, r.wStart);
    localDate(d1, sizeof d1, r.wEnd);
    localDate(d2, sizeof d2, r.runs[0].start);
    localDate(d3, sizeof d3, r.runs[0].end);
    localDate(d4, sizeof d4, r.runs[1].start);

    snprintf(line0, sizeof line0, "  #1  %s \xe2\x86\x92 %s   %3d purges   %6s L   %s%s",
             d2, d3, 3, "400", "", "");
    snprintf(line1, sizeof line1, "  #2  %s \xe2\x86\x92 %s   %3d purges   %6s L   %s%s",
             d4, "in progress", 12, "500", "1 cylinder change(s)", "  (open)");

    snprintf(expect, sizeof expect,
        "last 60 days (%s \xe2\x86\x92 %s):\n"
        "  helium dispensed   1234 L  (900 L during user runs)\n"
        "  cylinders fitted   2    cylinder-equivalents used  0.15  (at 8000 L each)\n"
        "user runs (end = first flow-zero after the last purge; a run closes after 10 h with no "
        "purge, or at a \"new user run\" mark):\n"
        "%s\n%s", d0, d1, line0, line1);

    CHECK(T, strcmp(buf, expect) == 0, "text mismatch:\n--- got ---\n%s\n--- want ---\n%s", buf,
          expect);
    return 0;
}

int main(void)
{
    static int (*const tests[])(void) = {
        t_gate_enabled, t_gate_shadow, t_gate_enable_transitions,
        t_gate_drop_disconnected, t_gate_requeue_on_disable, t_gate_requeue_on_disconnect,
        t_gate_collapse, t_gate_run_and_nan, t_gate_init, t_gate_set_connected,
        t_start_decision, t_start_wait_alarm, t_not_configured_alarm,
        t_cfg_reject_state, t_cfg_changed_o2, t_cfg_changed_mfc, t_cfg_unchanged,
        t_cfg_not_configured, t_cfg_empty_mfc, t_cfg_trim, t_cfg_he_save,
        t_logfile, t_logfile_month_boundary, t_logfile_write_error,
        t_report_text,
    };
    const int n = (int)(sizeof tests / sizeof tests[0]);
    int i, fails = 0;
    for (i = 0; i < n; i++) fails += tests[i]();
    printf("%s: %d/%d tests passed\n", fails ? "FAIL" : "PASS", n - fails, n);
    return fails ? 1 : 0;
}
