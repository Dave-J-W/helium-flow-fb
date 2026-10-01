/* sgUnitTest.c: unit tests for the controller core paths the reference replay cannot reach:
   the spec additions (Resume Flow from IDLE, mode change, new-run mark, override count reset, MFC
   disconnect handling, forecast and progress texts, the NaN-total guard, the rulings R11-R13, the
   glue entry points of sgCore.h) and the reference branches no scenario trace exercises (ruling
   R5). Every expected log text is the
   reference's (simulator/sample_gas_simulator.html) or, for new paths, the spec's.
   Each test builds a controller with sg_init, feeds hand-made sg_inputs tick by tick, and returns
   0 (pass) or 1 (fail, with a printed "FAIL <name>: ..." line). */
#include <math.h>
#include <stdio.h>
#include <string.h>
#include "sgCore.h"
#include "sgFmt.h"

/* ---------------------------------------------------------------- effect capture */
#define MAXLOG 4096
static char logMsg[MAXLOG][SG_MSG + 64];
static int logSev[MAXLOG];
static double logT[MAXLOG];
static int nLog, nPutSp, nPutRamp, nPutRun;
static double lastPutSp;

static void cbLog(void *ctx, double t, int sev, const char *msg)
{
    (void)ctx;
    if (nLog < MAXLOG) {
        snprintf(logMsg[nLog], sizeof logMsg[nLog], "%s", msg);
        logSev[nLog] = sev; logT[nLog] = t;
    }
    nLog++;
}
static void cbSp(void *ctx, double v) { (void)ctx; nPutSp++; lastPutSp = v; }
static void cbRamp(void *ctx, double v) { (void)ctx; (void)v; nPutRamp++; }
static void cbRun(void *ctx) { (void)ctx; nPutRun++; }

static sg_ctl C;           /* ~250 KB: static, not on the stack */

static void newCtl(void)
{
    sg_io io;
    memset(&io, 0, sizeof io);
    io.log = cbLog; io.put_setpoint = cbSp; io.put_ramp = cbRamp; io.put_run = cbRun;
    nLog = 0; nPutSp = 0; nPutRamp = 0; nPutRun = 0; lastPutSp = NAN;
    sg_init(&C, &io, 0);
}

/* Number of log lines with this severity and exactly this text. */
static int countLog(int sev, const char *msg)
{
    int i, n = 0, lim = nLog < MAXLOG ? nLog : MAXLOG;
    for (i = 0; i < lim; i++) if (logSev[i] == sev && strcmp(logMsg[i], msg) == 0) n++;
    return n;
}
/* Time of the first log line with this text (any severity), NaN if none. */
static double logTime(const char *msg)
{
    int i, lim = nLog < MAXLOG ? nLog : MAXLOG;
    for (i = 0; i < lim; i++) if (strcmp(logMsg[i], msg) == 0) return logT[i];
    return NAN;
}
/* Number of log lines containing this text. */
static int countLogSub(const char *sub)
{
    int i, n = 0, lim = nLog < MAXLOG ? nLog : MAXLOG;
    for (i = 0; i < lim; i++) if (strstr(logMsg[i], sub)) n++;
    return n;
}

/* O2 alternates by 0.001 every tick so the frozen-reading detector (30 identical readings) stays
   quiet; with an even slopeAvgN the windowed means are identical, so the O2 slope stays 0. */
static double wob(double t) { return 0.001 * (double)((long)t % 2); }

static sg_inputs base(double o2)
{
    sg_inputs in;
    memset(&in, 0, sizeof in);
    in.o2 = o2; in.o2Sevr = 0; in.flow = 0.3; in.sp = 0.3; in.ramp = 1; in.total = 0;
    in.running = 1; in.mfcConnected = 1;
    snprintf(in.gas, sizeof in.gas, "He");
    return in;
}

static void tickAt(sg_inputs *in, double t)
{
    sg_set_inputs(&C, in);
    sg_tick(&C, t);
}

/* Tick with O2 = o2 + wob(t). */
static void tickO2(sg_inputs *in, double o2, double t)
{
    in->o2 = o2 + wob(t);
    tickAt(in, t);
}

static int alarmIs(int k, int sev, const char *msg)
{
    return C.alarms[k].active && C.alarms[k].sev == sev && strcmp(C.alarms[k].msg, msg) == 0;
}

#define CHECK(name, cond, ...)                                                     \
    do {                                                                           \
        if (!(cond)) {                                                             \
            printf("FAIL %s: ", name); printf(__VA_ARGS__); printf("\n");          \
            return 1;                                                              \
        }                                                                          \
    } while (0)

/* Controller in REGULATE via Resume Flow from IDLE: one tick at t = 1 with O2 o2 (valid,
   < lidLevel) and Setpoint_RBV = Flow_RBV = sp, then the button. */
static int resumeFromIdle(sg_inputs *in, double o2, double sp)
{
    char why[SG_MSG];
    in->sp = sp; in->flow = sp;
    tickO2(in, o2, 1);
    return sg_op_resume_flow(&C, why, sizeof why);
}

/* ================================================================ brief tests 1-8 */

/* 1. Resume Flow from IDLE: REGULATE, FBON, lastCmd = Setpoint_RBV, no put at the switch. */
static int t_resume_idle(void)
{
    const char *T = "1 resume from IDLE";
    sg_inputs in = base(0.5);
    char why[SG_MSG];
    int ok;
    newCtl();
    in.sp = 0.3; in.flow = 0.29;
    tickAt(&in, 1);
    nPutSp = 0;
    snprintf(why, sizeof why, "junk");
    ok = sg_op_resume_flow(&C, why, sizeof why);
    CHECK(T, ok == 1, "returned %d, why '%s'", ok, why);
    CHECK(T, why[0] == '\0', "why not emptied: '%s'", why);
    CHECK(T, C.state == SG_REGULATE, "state %d", C.state);
    CHECK(T, C.epid.FBON == 1, "FBON %d", C.epid.FBON);
    CHECK(T, C.lastCmd == 0.3 && C.lastCmdTime == 1 && C.flowAtCmd == 0.29,
          "lastCmd %g lastCmdTime %g flowAtCmd %g", C.lastCmd, C.lastCmdTime, C.flowAtCmd);
    CHECK(T, C.epid.OVAL == 0.3, "PID:Out %g, want lastCmd", C.epid.OVAL);
    CHECK(T, C.epid.DRVH == 1.0 && C.epid.DRVL == 0.05, "configEpid not run: DRVL %g DRVH %g",
          C.epid.DRVL, C.epid.DRVH);
    CHECK(T, nPutSp == 0, "%d setpoint put(s) at the switch", nPutSp);
    CHECK(T, countLog(0, "IDLE → REGULATE (operator pressed Resume Flow)") == 1, "log missing");
    tickAt(&in, 2);
    CHECK(T, nPutSp == 0 && C.state == SG_REGULATE, "put or state change on the next tick");
    return 0;
}

/* 2. Resume Flow from IDLE with O2 above lidLevel is rejected. */
static int t_resume_idle_high(void)
{
    const char *T = "2 resume from IDLE, O2 high";
    sg_inputs in = base(12);
    char why[SG_MSG];
    newCtl();
    tickAt(&in, 1);
    CHECK(T, sg_op_resume_flow(&C, why, sizeof why) == 0, "accepted");
    CHECK(T, strcmp(why, "Resume Flow ignored: O2 12.00 % above the lid threshold 10 %: purge first") == 0,
          "why '%s'", why);
    CHECK(T, C.state == SG_IDLE, "state %d", C.state);
    CHECK(T, nPutSp == 0, "put");
    return 0;
}

/* 3. Resume Flow with O2 invalid, in IDLE and in OPEN_LOOP (R5 g). */
static int t_resume_invalid(void)
{
    const char *T = "3 resume with O2 invalid";
    sg_inputs in = base(0.5);
    char why[SG_MSG];
    newCtl();
    in.o2Sevr = 3;
    tickAt(&in, 1);
    CHECK(T, sg_op_resume_flow(&C, why, sizeof why) == 0, "accepted in IDLE");
    CHECK(T, strcmp(why, "Resume Flow ignored: O2 invalid") == 0, "IDLE why '%s'", why);
    CHECK(T, C.state == SG_IDLE, "IDLE state %d", C.state);
    sg_enter(&C, SG_OPEN_LOOP, "test");
    tickAt(&in, 2);
    CHECK(T, sg_op_resume_flow(&C, why, sizeof why) == 0, "accepted in OPEN_LOOP");
    CHECK(T, strcmp(why, "Resume Flow ignored: O2 invalid") == 0, "OPEN_LOOP why '%s'", why);
    CHECK(T, C.state == SG_OPEN_LOOP, "OPEN_LOOP state %d", C.state);
    /* and the accepted OPEN_LOOP path still goes to HANDOFF */
    in.o2Sevr = 0;
    tickAt(&in, 3);
    CHECK(T, sg_op_resume_flow(&C, why, sizeof why) == 1 && C.state == SG_HANDOFF,
          "OPEN_LOOP with valid O2: state %d why '%s'", C.state, why);
    CHECK(T, countLog(0, "OPEN_LOOP → HANDOFF (operator pressed Resume Flow)") == 1, "log");
    return 0;
}

/* 4. Resume Flow in PURGE is rejected with the generic text (R5 g). */
static int t_resume_purge(void)
{
    const char *T = "4 resume in PURGE";
    sg_inputs in = base(20.9);
    char why[SG_MSG];
    newCtl();
    in.flow = 20; in.sp = 20;
    tickAt(&in, 1);
    sg_op_purge(&C);
    tickAt(&in, 2);
    CHECK(T, C.state == SG_PURGE, "state %d", C.state);
    CHECK(T, sg_op_resume_flow(&C, why, sizeof why) == 0, "accepted");
    CHECK(T, strcmp(why, "Resume Flow ignored in PURGE") == 0, "why '%s'", why);
    CHECK(T, C.state == SG_PURGE, "state changed to %d", C.state);
    return 0;
}

/* 5. Mode change. */
static int t_set_mode(void)
{
    const char *T = "5 set mode";
    newCtl();
    sg_set_mode(&C, 1, "operator");
    CHECK(T, C.p.mode == 1, "mode %d", C.p.mode);
    CHECK(T, countLog(0, "operator: enclosure mode → Collimator lid") == 1, "log missing");
    CHECK(T, fabs(sg_expected_flow(&C) - 0.84) < 1e-12, "expected flow %g", sg_expected_flow(&C));
    sg_set_mode(&C, 7, "operator");                  /* out of range: ignored */
    CHECK(T, C.p.mode == 1, "out-of-range mode accepted: %d", C.p.mode);
    return 0;
}

/* 6. New-run mark, and the override count reset. */
static int t_mark_new_run(void)
{
    const char *T = "6 mark new run";
    sg_inputs in = base(0.5);
    int n0;
    newCtl();
    in.total = 100; tickAt(&in, 1);
    in.total = 112.5; tickAt(&in, 2);
    n0 = C.nLedger;
    sg_mark_new_run(&C);
    CHECK(T, C.nLedger == n0 + 1, "nLedger %d, was %d", C.nLedger, n0);
    CHECK(T, C.ledger[n0].type == SG_EV_NEWRUN && C.ledger[n0].t == 2 && C.ledger[n0].L == 12.5,
          "event type %d t %g L %g", C.ledger[n0].type, C.ledger[n0].t, C.ledger[n0].L);
    CHECK(T, countLog(0, "admin: start of a new user run marked") == 1, "log missing");
    C.overrideCount = 5;
    sg_reset_override_count(&C);
    CHECK(T, C.overrideCount == 0, "overrideCount %ld", C.overrideCount);
    CHECK(T, countLog(0, "settings: override count reset") == 1, "reset log missing");
    return 0;
}

/* 7. MFC disconnected (spec §8.18). */
static int t_mfc_disconnect(void)
{
    const char *T = "7 MFC disconnect";
    const char *MSG = "MFC not responding (CA disconnected)";
    sg_inputs in = base(0.5);
    double t;
    newCtl();
    CHECK(T, resumeFromIdle(&in, 0.5, 0.3) == 1, "resume refused");
    nPutSp = 0;
    in.mfcConnected = 0;
    in.flow = 0;                          /* stale reading: must not raise "flow mismatch" */
    for (t = 2; t <= 4; t++) tickO2(&in, 0.5, t);            /* holdDetect (3) ticks */
    CHECK(T, !C.alarms[SG_A_MISMATCH].active, "alarm after holdDetect ticks: '%s'",
          C.alarms[SG_A_MISMATCH].msg);
    tickO2(&in, 0.5, 5);                                       /* holdDetect + 1 */
    CHECK(T, alarmIs(SG_A_MISMATCH, 2, MSG), "no MAJOR disconnect alarm after holdDetect + 1");
    CHECK(T, countLog(2, MSG) == 1, "alarm log count %d", countLog(2, MSG));
    for (t = 6; t <= 12; t++) tickO2(&in, 0.5, t);
    CHECK(T, alarmIs(SG_A_MISMATCH, 2, MSG), "alarm replaced: '%s'", C.alarms[SG_A_MISMATCH].msg);
    CHECK(T, countLog(2, MSG) == 1, "alarm logged again (%d)", countLog(2, MSG));
    CHECK(T, countLogSub("flow mismatch") == 0, "flow mismatch evaluated while disconnected");
    CHECK(T, C.state == SG_REGULATE, "state %d", C.state);
    CHECK(T, nPutSp == 0, "put while disconnected in REGULATE (no PID cycle ran)");
    in.mfcConnected = 1; in.flow = 0.3;
    tickO2(&in, 0.5, 13);
    CHECK(T, nPutSp == 1 && lastPutSp == 0.3, "re-send: %d put(s), last %g", nPutSp, lastPutSp);
    CHECK(T, !C.alarms[SG_A_MISMATCH].active, "alarm not cleared");
    CHECK(T, countLog(0, "MFC reconnected: setpoint 0.30 re-sent") == 1, "reconnect log missing");
    tickO2(&in, 0.5, 14);
    CHECK(T, nPutSp == 1, "re-sent twice");

    /* IDLE: the controller does not own the flow, no alarm and no re-send */
    newCtl();
    in = base(0.5);
    in.mfcConnected = 0;
    for (t = 1; t <= 10; t++) tickO2(&in, 0.5, t);
    CHECK(T, !C.alarms[SG_A_MISMATCH].active, "alarm in IDLE");
    in.mfcConnected = 1;
    tickO2(&in, 0.5, 11);
    CHECK(T, nPutSp == 0 && countLogSub("MFC reconnected") == 0, "re-send in IDLE");
    return 0;
}

/* 7b. Ruling R8: a short CA blip (<= holdDetect ticks) outside IDLE re-sends lastCmd on reconnect,
   silently and without an alarm, so a one-shot command made during the blip is not lost. */
static int t_mfc_blip(void)
{
    const char *T = "7b MFC blip re-send";
    sg_inputs in = base(0.5);
    int n0;
    /* Flow Zero pressed during the blip: its command(0) is dropped by the IOC layer */
    newCtl();
    tickO2(&in, 0.5, 1);
    in.mfcConnected = 0;
    tickO2(&in, 0.5, 2);                              /* IDLE: not counted */
    sg_op_flow_zero(&C);
    CHECK(T, C.state == SG_FLOW_ZERO && C.lastCmd == 0, "state %d lastCmd %g", C.state, C.lastCmd);
    nPutSp = 0;
    tickO2(&in, 0.5, 3);
    tickO2(&in, 0.5, 4);                              /* 2 disconnected ticks < holdDetect 3 */
    CHECK(T, nPutSp == 0 && !C.alarms[SG_A_MISMATCH].active, "put %d or alarm during blip", nPutSp);
    in.mfcConnected = 1;
    n0 = nLog;
    tickO2(&in, 0.5, 5);
    CHECK(T, nPutSp == 1 && lastPutSp == 0, "FLOW_ZERO re-send: %d put(s), last %g", nPutSp, lastPutSp);
    CHECK(T, !C.alarms[SG_A_MISMATCH].active, "alarm after blip");
    CHECK(T, nLog == n0, "blip logged %d line(s): '%s'", nLog - n0, n0 < MAXLOG ? logMsg[n0] : "");
    tickO2(&in, 0.5, 6);
    CHECK(T, nPutSp == 1, "re-sent twice");
    /* REGULATE */
    newCtl();
    in = base(0.5);
    CHECK(T, resumeFromIdle(&in, 0.5, 0.3) == 1, "resume refused");
    nPutSp = 0;
    in.mfcConnected = 0;
    tickO2(&in, 0.5, 2);
    tickO2(&in, 0.5, 3);
    in.mfcConnected = 1;
    n0 = nLog;
    tickO2(&in, 0.5, 4);
    CHECK(T, nPutSp == 1 && lastPutSp == 0.3, "REGULATE re-send: %d put(s), last %g", nPutSp, lastPutSp);
    CHECK(T, !C.alarms[SG_A_MISMATCH].active && nLog == n0, "alarm or log after blip");
    return 0;
}

/* 7c. Ruling R9: Resume Flow from IDLE needs the MFC connected and a finite Setpoint_RBV; the O2
   checks come first. */
static int t_resume_mfc(void)
{
    const char *T = "7c resume needs the MFC";
    const char *WHY = "Resume Flow ignored: MFC not connected";
    sg_inputs in = base(0.5);
    char why[SG_MSG];
    newCtl();
    in.mfcConnected = 0;
    tickO2(&in, 0.5, 1);
    CHECK(T, sg_op_resume_flow(&C, why, sizeof why) == 0, "accepted while disconnected");
    CHECK(T, strcmp(why, WHY) == 0, "disconnected why '%s'", why);
    in.mfcConnected = 1; in.sp = NAN;
    tickO2(&in, 0.5, 2);
    CHECK(T, sg_op_resume_flow(&C, why, sizeof why) == 0 && strcmp(why, WHY) == 0, "NaN sp: '%s'", why);
    in.sp = INFINITY;
    tickO2(&in, 0.5, 3);
    CHECK(T, sg_op_resume_flow(&C, why, sizeof why) == 0 && strcmp(why, WHY) == 0, "Inf sp: '%s'", why);
    CHECK(T, C.state == SG_IDLE && nPutSp == 0, "state %d puts %d", C.state, nPutSp);
    /* the O2 checks come first */
    in.sp = 0.3; in.mfcConnected = 0; in.o2Sevr = 3;
    tickAt(&in, 4);
    CHECK(T, sg_op_resume_flow(&C, why, sizeof why) == 0 &&
          strcmp(why, "Resume Flow ignored: O2 invalid") == 0, "invalid O2 first: '%s'", why);
    in.o2Sevr = 0;
    tickO2(&in, 12, 5);
    CHECK(T, sg_op_resume_flow(&C, why, sizeof why) == 0 && strncmp(why, "Resume Flow ignored: O2 12.0", 28) == 0,
          "lid threshold first: '%s'", why);
    /* connected with a finite setpoint: accepted */
    in.mfcConnected = 1;
    tickO2(&in, 0.5, 6);
    CHECK(T, sg_op_resume_flow(&C, why, sizeof why) == 1 && C.state == SG_REGULATE, "refused: '%s'", why);
    return 0;
}

/* 8. He:ForecastText. */
static int t_forecast_text(void)
{
    const char *T = "8 forecast text";
    static const double hs[5] = { 133.8, 132, 134.4, 132.6, 133.2 };
    char buf[SG_MSG];
    int k;
    newCtl();
    sg_forecast_text(&C, buf, sizeof buf);
    CHECK(T, strcmp(buf, "run-out forecast: collecting data (needs ≥ 6 h)") == 0, "empty: '%s'", buf);
    C.nCylEst = SG_NWIN;
    for (k = 0; k < SG_NWIN; k++) { C.cylEst[k].h = NAN; C.cylEst[k].why = "not enough data yet"; }
    sg_forecast_text(&C, buf, sizeof buf);
    CHECK(T, strcmp(buf, "run-out forecast: collecting data (needs ≥ 6 h)") == 0, "no h: '%s'", buf);
    for (k = 0; k < SG_NWIN; k++) C.cylEst[k].h = hs[k];
    C.cylMedianH = 133.2; C.cylMinH = 132; C.cylMaxH = 134.4;       /* as cylForecast sets them */
    sg_forecast_text(&C, buf, sizeof buf);
    CHECK(T, strcmp(buf, "empty in 5.5 d–5.6 d (median 5.5 d, 5 windows)") == 0, "5: '%s'", buf);
    C.cylEst[0].h = NAN; C.cylEst[2].h = NAN; C.cylEst[4].h = NAN;   /* 132 and 132.6 left */
    C.cylMedianH = 132; C.cylMinH = 132; C.cylMaxH = 132.6;
    sg_forecast_text(&C, buf, sizeof buf);
    CHECK(T, strcmp(buf, "empty in 5.5 d–5.5 d (median 5.5 d, 2 windows)") == 0, "2: '%s'", buf);
    C.cylEst[1].h = 5.25; C.cylEst[3].h = 30.4; C.cylMedianH = 5.25; C.cylMinH = 5.25; C.cylMaxH = 30.4;
    sg_forecast_text(&C, buf, sizeof buf);
    CHECK(T, strcmp(buf, "empty in 5.3 h–30 h (median 5.3 h, 2 windows)") == 0, "hours: '%s'", buf);
    C.cylMinH = 2; C.cylMaxH = 3;                   /* the range comes from cylMinH / cylMaxH */
    sg_forecast_text(&C, buf, sizeof buf);
    CHECK(T, strcmp(buf, "empty in 2.0 h–3.0 h (median 5.3 h, 2 windows)") == 0, "fields: '%s'", buf);
    return 0;
}

/* ================================================================ progress text */
static int t_progress_text(void)
{
    const char *T = "9 progress text";
    sg_inputs in = base(20.9);
    char buf[SG_MSG], want[SG_MSG], tb[32], eb[32];
    newCtl();
    sg_progress_text(&C, buf, sizeof buf);
    CHECK(T, strcmp(buf, "") == 0, "IDLE: '%s'", buf);
    /* PURGE, waiting for full flow */
    in.flow = 0; in.sp = 20;
    tickAt(&in, 1);
    sg_op_purge(&C);
    tickAt(&in, 2);
    CHECK(T, C.state == SG_PURGE, "state %d", C.state);
    sg_progress_text(&C, buf, sizeof buf);
    snprintf(want, sizeof want, "purge 0:00:00 / %s (waiting for full flow)  start 20.90 %%  "
             "lid check: pending  lag-corrected – %%, handoff at < 1.030 %%",
             sg_fmtT(tb, sizeof tb, sg_purge_timeout(&C)));
    CHECK(T, strcmp(buf, want) == 0, "PURGE entry:\n got '%s'\nwant '%s'", buf, want);
    /* full flow: timer running, lag-corrected estimate */
    in.flow = 20;
    tickAt(&in, 3);
    tickAt(&in, 13);                    /* one tick 10 s later: elapsed 0:00:10 */
    sg_progress_text(&C, buf, sizeof buf);
    snprintf(want, sizeof want, "purge 0:00:10 / %s  start 20.90 %%  lid check: pending  "
             "lag-corrected %s %%, handoff at < 1.030 %%",
             sg_fmtT(tb, sizeof tb, C.sd.timeout), sg_fmtN(eb, sizeof eb, C.sd.est, 3));
    CHECK(T, strcmp(buf, want) == 0, "PURGE timing:\n got '%s'\nwant '%s'", buf, want);
    /* lid check with a measured decay */
    C.sd.kin = 0.873;
    sg_progress_text(&C, buf, sizeof buf);
    CHECK(T, strstr(buf, "  lid check: decay 87 % of lid-on  ") != NULL, "decay: '%s'", buf);
    C.sd.lidResult = SG_LID_SKIPPED; C.sd.kin = NAN;
    sg_progress_text(&C, buf, sizeof buf);
    CHECK(T, strstr(buf, "  lid check: skipped  ") != NULL, "skipped: '%s'", buf);
    /* blind */
    in.o2Sevr = 3;
    tickAt(&in, 14);
    sg_progress_text(&C, buf, sizeof buf);
    snprintf(want, sizeof want, "purge 0:00:11 / %s  BLIND (timer only)",
             sg_fmtT(tb, sizeof tb, C.sd.timeout));
    CHECK(T, strcmp(buf, want) == 0, "blind:\n got '%s'\nwant '%s'", buf, want);
    /* HANDOFF */
    newCtl();
    in = base(0.5);
    C.p.fbDelay = 60;
    tickAt(&in, 1);
    sg_enter(&C, SG_HANDOFF, "test");
    sg_progress_text(&C, buf, sizeof buf);
    CHECK(T, strcmp(buf, "waiting for flow to settle at expected") == 0, "settle: '%s'", buf);
    in.flow = 0.25;                                   /* at the expected flow */
    tickAt(&in, 2);
    sg_progress_text(&C, buf, sizeof buf);
    CHECK(T, strcmp(buf, "feedback on in 0:01:00") == 0, "delay: '%s'", buf);
    tickAt(&in, 12);
    sg_progress_text(&C, buf, sizeof buf);
    CHECK(T, strcmp(buf, "feedback on in 0:00:50") == 0, "delay 10 s later: '%s'", buf);
    /* REGULATE, settling, before the first PID cycle */
    newCtl();
    in = base(0.5);
    CHECK(T, resumeFromIdle(&in, 0.5, 0.3) == 1, "resume refused");
    sg_progress_text(&C, buf, sizeof buf);
    CHECK(T, strcmp(buf, "SETTLING ↑ to 0.99 %  (0.000 %/min toward)\n"
                         "PID: CVAL – %  out 0.300 [0.05…1.00]") == 0, "REGULATE: '%s'", buf);
    C.stallSec = (int)C.p.stallTime; C.towardRate = -0.0123; C.epid.CVAL = 0.5; C.settleDir = -1;
    sg_progress_text(&C, buf, sizeof buf);
    CHECK(T, strcmp(buf, "SETTLING ↓ to 0.99 %  (-0.012 %/min toward, STALLED)\n"
                         "PID: CVAL 0.500 %  out 0.300 [0.05…1.00]") == 0, "stalled: '%s'", buf);
    C.settling = 0;
    sg_progress_text(&C, buf, sizeof buf);
    CHECK(T, strcmp(buf, "PID: CVAL 0.500 %  out 0.300 [0.05…1.00]") == 0, "settled: '%s'", buf);
    return 0;
}

/* ================================================================ R5: untraced reference branches */

/* Start a purge: tick at t = 1 in IDLE, press Purge, tick at t = 2 (PRECHECK → PURGE). */
static void startPurge(sg_inputs *in, double o2)
{
    tickO2(in, o2, 1);
    sg_op_purge(&C);
    tickO2(in, o2, 2);
}

/* R5 a: no O2 decay within lidOnsetMax of full flow → OPEN_STOP. */
static int t_no_decay(void)
{
    const char *T = "R5a no O2 decay";
    const char *MSG = "no O2 decay within 5 s of full flow: enclosure open?";
    sg_inputs in = base(20.9);
    double t;
    newCtl();
    C.p.lidOnsetMax = 5;
    in.flow = 20; in.sp = 20;
    startPurge(&in, 20.9);
    CHECK(T, C.state == SG_PURGE, "state %d", C.state);
    for (t = 3; t <= 7; t++) tickO2(&in, 20.9, t);    /* timer starts at 3 (full flow) */
    CHECK(T, C.state == SG_PURGE, "left PURGE early: state %d", C.state);
    tickO2(&in, 20.9, 8);
    CHECK(T, C.state == SG_OPEN_STOP, "state %d at el = 5", C.state);
    CHECK(T, countLog(0, "PURGE → OPEN_STOP (no O2 decay within 5 s of full flow: enclosure open?)") == 1,
          "transition log");
    CHECK(T, countLog(2, MSG) == 1 && alarmIs(SG_A_OPENSTOP, 2, MSG), "OpenStop alarm");
    CHECK(T, lastPutSp == 0, "flow not stopped: last put %g", lastPutSp);
    return 0;
}

/* R5 a2 (user's decision 2026-09-30): dropSkipLevel lowered from 18 to 17 % because lid-open
   handling dips reach ~18.4 % in the extreme (docs/ioc/15LSS_sample_gas_IOC_spec.md value
   table; 2026-09-24-o2-purge-feedback-design.md §2.1.8). The condition is
   `!(o2Start >= dropSkipLevel)` (true for NaN, as in the reference), so o2Start == dropSkipLevel
   runs the check, not skips it.
   - 17.50 %: above the new default -> the lid check runs (here, via the no-decay branch).
     The same start under the OLD default of 18 % would have been skipped: confirmed below.
   - 17.00 %: the boundary -> still runs (17.00 >= 17 is true, so NOT skipped).
   - 16.90 %: below the new default -> still skipped, as before. */
static int t_drop_skip_level(void)
{
    const char *T = "R5a2 dropSkipLevel 17 %";
    const char *MSG = "no O2 decay within 5 s of full flow: enclosure open?";
    sg_inputs in = base(17.5);
    double t;

    /* 17.50 %: the new default (17) runs the lid check. */
    newCtl();
    C.p.lidOnsetMax = 5;
    in.flow = 20; in.sp = 20;
    startPurge(&in, 17.5);
    CHECK(T, C.state == SG_PURGE, "17.5%%: state %d", C.state);
    for (t = 3; t <= 7; t++) tickO2(&in, 17.5, t);
    CHECK(T, countLogSub("lid check skipped") == 0, "17.5%%: wrongly skipped");
    tickO2(&in, 17.5, 8);
    CHECK(T, C.state == SG_OPEN_STOP, "17.5%%: state %d", C.state);
    CHECK(T, countLog(2, MSG) == 1 && alarmIs(SG_A_OPENSTOP, 2, MSG), "17.5%%: check did not run");
    CHECK(T, lastPutSp == 0, "17.5%%: flow not stopped: last put %g", lastPutSp);

    /* Same 17.50 % start under the OLD default (18 %): this is the case the user reported —
       confirms it would have been skipped, letting helium flow into an open enclosure. */
    newCtl();
    C.p.lidOnsetMax = 5; C.p.dropSkipLevel = 18;
    in = base(17.5); in.flow = 20; in.sp = 20;
    startPurge(&in, 17.5);
    tickO2(&in, 17.5, 3);
    CHECK(T, countLog(0, "lid check skipped: purge started at 17.50 % (< 18 %)") == 1,
          "17.5%% under the old default of 18 %%: should have been skipped");

    /* 17.00 %: the boundary itself still runs the check (>=, not >). */
    newCtl();
    C.p.lidOnsetMax = 5;
    in = base(17.0); in.flow = 20; in.sp = 20;
    startPurge(&in, 17.0);
    for (t = 3; t <= 7; t++) tickO2(&in, 17.0, t);
    CHECK(T, countLogSub("lid check skipped") == 0, "17.00%%: wrongly skipped at the boundary");
    tickO2(&in, 17.0, 8);
    CHECK(T, C.state == SG_OPEN_STOP, "17.00%%: state %d", C.state);
    CHECK(T, countLog(2, MSG) == 1 && alarmIs(SG_A_OPENSTOP, 2, MSG), "17.00%%: check did not run");

    /* 16.90 %: still below the new default -> still skipped. */
    newCtl();
    in = base(16.9); in.flow = 20; in.sp = 20;
    startPurge(&in, 16.9);
    tickO2(&in, 16.9, 3);
    CHECK(T, countLog(0, "lid check skipped: purge started at 16.90 % (< 17 %)") == 1,
          "16.90%%: should still be skipped");
    return 0;
}

/* ================================================================ lid check: robust slopes
   User's decision 2026-09-30 (spec §8.9 step 5.3): the decay rates are least-squares slopes of
   ln(O2) over the window's kept samples (a repeated value = a missed O2 update, dropped), the
   window is 30 s, and a half with fewer than lidMinSamples updates waits up to 2 lidWindow, then
   the check judges on the ratio alone. The purges below start at 20.9 % with full flow from t = 3
   (the purge timer's start), and the fresh reading at tick t is
       20.9 × (a + (1 − a) exp(−r kExp (t − 3)))     (a = 0: a pure exponential at r × F/V)
   times (1 + noise × pnoise(t)). held(t) = 1 makes tick t repeat the previous reading. */
static const double KEXP = 20.0 / 41 / 60;           /* F/V at the defaults, 1/s */

static double pnoise(long t)                         /* deterministic, zero mean, in [-1, 1] */
{
    unsigned int x = (unsigned int)t * 2654435761u;
    x ^= x >> 13; x *= 0x5bd1e995u; x ^= x >> 15;
    return (double)(x & 0xffffu) / 32767.5 - 1.0;
}
typedef int (*heldFn)(long t);
static int heldNone(long t) { (void)t; return 0; }
/* 20 %: every 5th tick, plus the first tick after the onset (7), the window's midpoint (21) and
   both of its last ticks (35, 36): the onset is at t = 6 and the check at t = 36 */
static int held20(long t) { return t % 5 == 0 || t == 7 || t == 21 || t == 35 || t == 36; }
/* 50 %: every odd tick, plus the check tick 36 (so 35 and 36 both repeat 34) */
static int held50(long t) { return t % 2 == 1 || t == 36; }
/* a 10 s O2 PV: a fresh value at t = 6, 16, 26, ... only */
static int held10s(long t) { return t % 10 != 6; }
/* the analyzer misses every update for 20 s after the onset (t = 7..26), then 1 Hz again */
static int heldStall(long t) { return t >= 7 && t <= 26; }

/* Run the purge until the lid check decides (or the state leaves PURGE) or t = tMax. Returns the
   tick of the decision, -1 if none. */
static long lidRun(double a, double r, double noise, heldFn held, long tMax)
{
    sg_inputs in = base(20.9);
    double prev = 20.9;
    long t;
    newCtl();
    in.flow = 20; in.sp = 20;
    tickAt(&in, 1);
    sg_op_purge(&C);
    tickAt(&in, 2);
    for (t = 3; t <= tMax; t++) {
        double v = 20.9 * (a + (1 - a) * exp(-r * KEXP * (double)(t - 3))) * (1 + noise * pnoise(t));
        if (held(t)) v = prev;
        prev = v;
        in.o2 = v;
        tickAt(&in, (double)t);
        if (C.sd.dropChecked || C.state != SG_PURGE) return t;
    }
    return -1;
}

/* L1: an exact lid-on exponential gives ratio = curvature = 1 (to 1e-9), with or without
   repeated samples (20 % and 50 %, including the sample after the onset, the midpoint and the
   last samples of the window): the repeats are dropped, so nothing changes. The old three-point
   check read the repeated end sample as O2 that stopped falling. */
static int t_lid_exact_repeats(void)
{
    const char *T = "L1 lid check: exact decay, repeated samples";
    static const struct { heldFn held; const char *name; } cases[] = {
        { heldNone, "no repeats" }, { held20, "20 % repeats" }, { held50, "50 % repeats" },
    };
    size_t k;
    for (k = 0; k < sizeof cases / sizeof cases[0]; k++) {
        long td = lidRun(0, 1, 0, cases[k].held, 120);
        CHECK(T, C.state == SG_PURGE && C.sd.lidResult == SG_LID_PASSED,
              "%s: state %d lidResult %d", cases[k].name, C.state, C.sd.lidResult);
        CHECK(T, fabs(C.sd.kin - 1) < 1e-9 && fabs(C.sd.curv - 1) < 1e-9,
              "%s: ratio %.12f curvature %.12f (want 1, 1)", cases[k].name, C.sd.kin, C.sd.curv);
        CHECK(T, fabs(C.sd.kObs - KEXP) < 1e-12, "%s: kObs %.15g", cases[k].name, C.sd.kObs);
        CHECK(T, C.sd.onsetT == 3 && td == 36 && C.sd.checkAt == 33,
              "%s: onset el %g, decided at t %ld (el %g); want onset 3, t 36 (lidWindow 30)",
              cases[k].name, C.sd.onsetT, td, C.sd.checkAt);
        CHECK(T, countLogSub("ratio only") == 0, "%s: ratio-only fallback at 1 Hz", cases[k].name);
    }
    CHECK(T, countLog(0, "lid check passed at 33 s: decay 100 % of the lid-on rate (open < 50 %), "
                         "curvature 1.00 (open < 0.8), 15 O2 updates") == 1,
          "passed line (50 %%: 31 samples, 16 repeats dropped)");
    return 0;
}

/* L2: with reading noise (2e-4 relative, ~4 m% at 20 %) the 20 % repeats change the ratio and
   the curvature by less than 0.01 (noise level; the old check moved them by ~0.07-0.13). */
static int t_lid_noise_repeats(void)
{
    const char *T = "L2 lid check: noisy decay, repeated samples";
    double r0, c0;
    lidRun(0, 1, 2e-4, heldNone, 120);
    CHECK(T, C.sd.lidResult == SG_LID_PASSED, "no repeats: lidResult %d", C.sd.lidResult);
    r0 = C.sd.kin; c0 = C.sd.curv;
    CHECK(T, fabs(r0 - 1) < 0.02 && fabs(c0 - 1) < 0.05, "no repeats: ratio %.4f curvature %.4f", r0, c0);
    lidRun(0, 1, 2e-4, held20, 120);
    CHECK(T, C.sd.lidResult == SG_LID_PASSED, "20 %%: lidResult %d", C.sd.lidResult);
    CHECK(T, fabs(C.sd.kin - r0) < 0.01 && fabs(C.sd.curv - c0) < 0.01,
          "20 %% repeats: ratio %.4f (vs %.4f) curvature %.4f (vs %.4f)", C.sd.kin, r0, C.sd.curv, c0);
    return 0;
}

/* L3: a 10 s O2 PV on a closed lid. At onset + 30 s each half holds 2 updates (< 4): the check
   waits; at onset + 60 s the first half still holds 3, so it judges on the ratio alone, logs
   that (MINOR), and passes. The old check judged every such purge open (curvature 0). */
static int t_lid_10s_updates(void)
{
    const char *T = "L3 lid check: 10 s O2 updates";
    const char *RONLY = "lid check: too few O2 updates for the curvature test (3/4): ratio only";
    long td = lidRun(0, 1, 0, held10s, 200);
    CHECK(T, C.state == SG_PURGE && C.sd.lidResult == SG_LID_PASSED,
          "closed lid judged open: state %d lidResult %d", C.state, C.sd.lidResult);
    CHECK(T, fabs(C.sd.kin - 1) < 1e-9 && isnan(C.sd.curv), "ratio %.12f curvature %g", C.sd.kin, C.sd.curv);
    CHECK(T, td == 66 && C.sd.checkAt == 63, "decided at t %ld (el %g); want 66 (onset 3 + 2 x 30)",
          td, C.sd.checkAt);
    CHECK(T, countLog(1, RONLY) == 1 && logTime(RONLY) == 66, "ratio-only line missing");
    CHECK(T, countLog(0, "lid check passed at 63 s: decay 100 % of the lid-on rate (open < 50 %), "
                         "curvature – (open < 0.8), 7 O2 updates") == 1, "passed line");
    CHECK(T, countLogSub("enclosure open?") == 0 && C.alarms[SG_A_OPENSTOP].active == 0, "OpenStop");
    return 0;
}

/* L4: open lid, slow decay (0.2 × F/V): caught on the ratio, at 1 Hz (full test, at onset + 30 s)
   and with a 10 s O2 PV (ratio only, at onset + 60 s); the flow is stopped. */
static int t_lid_open_slow(void)
{
    const char *T = "L4 lid check: open lid, slow decay";
    const char *MSG = "purge decay 20 % of the lid-on rate, curvature 1.00: enclosure open?";
    const char *MSG10 = "purge decay 20 % of the lid-on rate, curvature –: enclosure open?";
    long td = lidRun(0, 0.2, 0, heldNone, 200);
    CHECK(T, C.state == SG_OPEN_STOP, "1 Hz: state %d", C.state);
    CHECK(T, countLog(2, MSG) == 1 && alarmIs(SG_A_OPENSTOP, 2, MSG) && lastPutSp == 0, "1 Hz: OpenStop / flow");
    CHECK(T, td == 46, "1 Hz: caught at t %ld; want 46 (onset el 13 + 30)", td);
    td = lidRun(0, 0.2, 0, held10s, 200);
    CHECK(T, C.state == SG_OPEN_STOP, "10 s: state %d", C.state);
    CHECK(T, countLog(2, MSG10) == 1 && alarmIs(SG_A_OPENSTOP, 2, MSG10) && lastPutSp == 0,
          "10 s: OpenStop / flow");
    CHECK(T, td == 76 && countLogSub("ratio only") == 1, "10 s: caught at t %ld; want 76 (onset 13 + 60)", td);
    return 0;
}

/* L5: open lid whose decay levels off (a = 0.7, r = 6: ratio ~0.98, curvature ~0.53): caught on
   the curvature, also with 20 % repeated samples. */
static int t_lid_open_plateau(void)
{
    const char *T = "L5 lid check: open lid, decay levels off";
    static const heldFn helds[] = { heldNone, held20 };
    size_t k;
    for (k = 0; k < 2; k++) {
        lidRun(0.7, 6, 0, helds[k], 200);
        CHECK(T, C.state == SG_OPEN_STOP, "%s: state %d", k ? "20 %" : "no repeats", C.state);
        CHECK(T, strstr(C.alarms[SG_A_OPENSTOP].msg, "of the lid-on rate, curvature 0.5") != NULL,
              "%s: not caught on the curvature: '%s'", k ? "20 %" : "no repeats", C.alarms[SG_A_OPENSTOP].msg);
    }
    return 0;
}

/* L6: too few updates in a half at onset + 30 s: the check waits, and decides as soon as both
   halves hold lidMinSamples (here the analyzer misses 20 s after the onset; the first half
   [0, span/2) reaches 4 updates at span 47: the onset sample and 21, 22, 23 s after it). */
static int t_lid_wait(void)
{
    const char *T = "L6 lid check: wait for updates";
    long td = lidRun(0, 1, 0, heldStall, 200);
    CHECK(T, C.sd.lidResult == SG_LID_PASSED && fabs(C.sd.kin - 1) < 1e-9 && fabs(C.sd.curv - 1) < 1e-9,
          "lidResult %d ratio %.12f curvature %.12f", C.sd.lidResult, C.sd.kin, C.sd.curv);
    CHECK(T, td == 53 && C.sd.checkAt == 50, "decided at t %ld (el %g); want 53 (span 47)", td, C.sd.checkAt);
    CHECK(T, countLogSub("ratio only") == 0, "fell back to ratio only");
    return 0;
}

/* L7: the defaults and the O2 history length (2 lidWindow + 5 at the largest lidWindow). */
static int t_lid_defaults(void)
{
    const char *T = "L7 lid check defaults";
    sg_params p;
    long t;
    sg_default_params(&p);
    CHECK(T, p.lidWindow == 30 && p.lidMinSamples == 4, "lidWindow %g lidMinSamples %g",
          p.lidWindow, p.lidMinSamples);
    /* lidWindow 60 with the shortest stall settings: the history must still hold 2 × 60 + 1 */
    newCtl();
    C.p.lidWindow = 60; C.p.stallWindow = 20; C.p.slopeAvgN = 1;
    {
        sg_inputs in = base(5);
        for (t = 1; t <= 200; t++) tickO2(&in, 5, (double)t);
    }
    CHECK(T, C.nO2 == 125, "o2hist length %d (want 125)", C.nO2);
    /* the same at lidWindow 60 through a purge with a 20 s O2 PV: ratio only at span 120, from
       all 7 updates (with the old 90-sample history the onset sample would have been lost) */
    {
        sg_inputs in = base(20.9);
        double prev = 20.9;
        newCtl();
        C.p.lidWindow = 60; C.p.stallWindow = 20; C.p.slopeAvgN = 1;
        in.flow = 20; in.sp = 20;
        tickAt(&in, 1); sg_op_purge(&C); tickAt(&in, 2);
        for (t = 3; t <= 300 && !C.sd.dropChecked; t++) {
            double v = 20.9 * exp(-KEXP * (double)(t - 3));
            if (t % 20 != 6) v = prev;
            prev = v; in.o2 = v; tickAt(&in, (double)t);
        }
        CHECK(T, C.sd.lidResult == SG_LID_PASSED && fabs(C.sd.kin - 1) < 1e-9 && C.sd.checkAt == 123,
              "lidWindow 60, 20 s: lidResult %d ratio %.12f checkAt %g", C.sd.lidResult, C.sd.kin,
              C.sd.checkAt);
        CHECK(T, countLog(1, "lid check: too few O2 updates for the curvature test (3/4): ratio only") == 1 &&
                 countLogSub("curvature – (open < 0.8), 7 O2 updates") == 1, "lidWindow 60, 20 s: log");
    }
    return 0;
}

/* R5 b: purge timeout → PurgeIncomplete latch, then HANDOFF. */
static int t_purge_timeout(void)
{
    const char *T = "R5b purge timeout";
    const char *MSG = "purge incomplete: timeout (0:00:10) reached before target − Δ";
    sg_inputs in = base(5);
    double t;
    newCtl();
    C.p.purgeTimeoutMin = 10; C.p.purgeTimeoutMax = 10;
    in.flow = 20; in.sp = 20;
    startPurge(&in, 5);
    for (t = 3; t <= 12; t++) tickO2(&in, 5, t);
    CHECK(T, countLog(0, "lid check skipped: purge started at 5.00 % (< 17 %)") == 1, "skip log");
    CHECK(T, C.state == SG_PURGE, "left PURGE early: state %d", C.state);
    tickO2(&in, 5, 13);
    CHECK(T, C.state == SG_HANDOFF, "state %d", C.state);
    CHECK(T, countLog(1, MSG) == 1, "latch log missing");
    CHECK(T, alarmIs(SG_A_PURGEINC, 1, MSG) && C.alarms[SG_A_PURGEINC].until == 13 + 300,
          "PurgeIncomplete latch");
    CHECK(T, countLog(0, "PURGE → HANDOFF (purge timeout)") == 1, "transition log");
    CHECK(T, logTime(MSG) == 13 && logTime("PURGE → HANDOFF (purge timeout)") == 13, "timing");
    return 0;
}

/* R5 c: O2 lost during purge → blind, then blind completion → OPEN_LOOP. */
static int t_blind_purge(void)
{
    const char *T = "R5c blind purge";
    const char *LOST = "O2 lost during purge: continuing on the timer (blind)";
    sg_inputs in = base(20.9);
    double t;
    newCtl();
    C.p.purgeTimeoutMin = 10; C.p.purgeTimeoutMax = 10;
    in.flow = 20; in.sp = 20;
    startPurge(&in, 20.9);
    CHECK(T, C.state == SG_PURGE && !C.blind, "state %d blind %d", C.state, C.blind);
    in.o2Sevr = 3;
    for (t = 3; t <= 12; t++) tickAt(&in, t);
    CHECK(T, countLog(2, LOST) == 1, "lost log count %d", countLog(2, LOST));
    CHECK(T, C.blind && C.state == SG_PURGE, "blind %d state %d", C.blind, C.state);
    tickAt(&in, 13);
    CHECK(T, C.state == SG_OPEN_LOOP, "state %d", C.state);
    CHECK(T, countLog(0, "PURGE → OPEN_LOOP (blind purge complete)") == 1, "transition log");
    CHECK(T, alarmIs(SG_A_OPENLOOP, 2, "O2 unavailable: running blind at fixed flow"), "OpenLoop alarm");
    CHECK(T, lastPutSp == 0.25, "expected flow not commanded: %g", lastPutSp);
    return 0;
}

/* R5 d: O2 invalid during HANDOFF → OPEN_LOOP. */
static int t_handoff_invalid(void)
{
    const char *T = "R5d handoff O2 invalid";
    sg_inputs in = base(0.5);
    newCtl();
    tickAt(&in, 1);
    sg_enter(&C, SG_HANDOFF, "test");
    in.o2Sevr = 3;
    tickAt(&in, 2);
    CHECK(T, C.state == SG_OPEN_LOOP, "state %d", C.state);
    CHECK(T, countLog(0, "HANDOFF → OPEN_LOOP (O2 unavailable during handoff)") == 1, "log");
    return 0;
}

/* R5 e: PRECHECK with the MFC on hold writes Run; the hold monitor then re-sends the setpoint. */
static int t_precheck_hold(void)
{
    const char *T = "R5e PRECHECK on hold";
    sg_inputs in = base(20.9);
    newCtl();
    in.running = 0; in.flow = 20; in.sp = 20;
    startPurge(&in, 20.9);
    CHECK(T, nPutRun == 1, "put_run count %d", nPutRun);
    CHECK(T, countLog(1, "PRECHECK: MFC on hold, wrote Run") == 1, "log missing");
    CHECK(T, C.state == SG_PURGE && C.holdRecovering, "state %d holdRecovering %d", C.state,
          C.holdRecovering);
    in.running = 1;
    nPutSp = 0;
    tickO2(&in, 20.9, 3);
    CHECK(T, nPutSp == 1 && lastPutSp == 20, "re-send: %d put(s), last %g", nPutSp, lastPutSp);
    CHECK(T, countLog(1, "override: MFC was on hold, resumed; setpoint 20.00 re-sent") == 1,
          "override log missing");
    return 0;
}

/* Acceptance sc08: after a hold resume the flow restarts from 0 at an unchanged setpoint; the
   flow-mismatch timer restarts with the re-send, so the ramp allowance (sp/ramp + margin =
   0.3 + 5 s) runs from the resume, not from the long-past setpoint change. */
static int t_hold_resume_mismatch(void)
{
    const char *T = "hold resume restarts the mismatch timer";
    sg_inputs in = base(0.5);
    double t;
    newCtl();
    sg_set_inputs(&C, &in);
    sg_restart(&C, 10);
    for (t = 10; t <= 30; t++) tickO2(&in, 0.5, t);
    CHECK(T, C.state == SG_REGULATE && !C.alarms[SG_A_MISMATCH].active, "state %d", C.state);
    in.running = 0; in.flow = 0;
    for (t = 31; t <= 60; t++) tickO2(&in, 0.5, t);
    CHECK(T, nPutRun >= 1 && C.holdRecovering, "no Run written (%d)", nPutRun);
    in.running = 1;
    for (t = 61; t <= 66; t++) {
        tickO2(&in, 0.5, t);
        CHECK(T, !C.alarms[SG_A_MISMATCH].active, "mismatch %g s after the resume", t - 61);
    }
    CHECK(T, C.spChangeT == 61 && C.flowAtSpChange == 0, "timer %g flow %g", C.spChangeT,
          C.flowAtSpChange);
    tickO2(&in, 0.5, 67);                      /* 6 s > 5.3 s allowed, flow still 0 */
    CHECK(T, alarmIs(SG_A_MISMATCH, 2, "flow mismatch: cylinder empty or MFC fault?"),
          "no mismatch once the allowance is spent");
    return 0;
}

/* G3 (spec §8.18): a frozen Flow_RBV takes the not-responding path with its own texts. */
static int t_mfc_frozen(void)
{
    const char *T = "G3 frozen Flow_RBV";
    const char *MSG = "MFC not responding (Flow_RBV reading frozen)";
    sg_inputs in = base(0.5);
    double t;
    newCtl();
    CHECK(T, resumeFromIdle(&in, 0.5, 0.3) == 1, "resume refused");
    in.mfcConnected = 0; in.mfcStale = 1;
    for (t = 2; t <= 5; t++) tickO2(&in, 0.5, t);
    CHECK(T, alarmIs(SG_A_MISMATCH, 2, MSG), "no MAJOR: '%s'", C.alarms[SG_A_MISMATCH].msg);
    CHECK(T, countLog(2, MSG) == 1, "alarm log count %d", countLog(2, MSG));
    nPutSp = 0;
    in.mfcConnected = 1; in.mfcStale = 0;
    tickO2(&in, 0.5, 6);
    CHECK(T, !C.alarms[SG_A_MISMATCH].active && nPutSp == 1 && lastPutSp == 0.3, "recovery: alarm "
          "'%s', %d put(s)", C.alarms[SG_A_MISMATCH].msg, nPutSp);
    CHECK(T, countLog(0, "MFC readings updating again: setpoint 0.30 re-sent") == 1,
          "recovery log missing");
    CHECK(T, countLogSub("MFC reconnected") == 0, "logged as a CA reconnect");
    return 0;
}

/* D6: the §8.18 reconnect re-send restarts the flow-mismatch timer, as the hold resume does
   (§8.7): after an outage in which the Alicat's flow moved but its setpoint did not, the long
   expired ramp allowance must not raise an instant MAJOR "flow mismatch". */
static int t_reconnect_mismatch(void)
{
    const char *T = "D6 reconnect restarts the mismatch timer";
    sg_inputs in = base(0.5);
    double t;
    newCtl();
    sg_set_inputs(&C, &in);
    sg_restart(&C, 10);
    for (t = 10; t <= 30; t++) tickO2(&in, 0.5, t);
    CHECK(T, C.state == SG_REGULATE && !C.alarms[SG_A_MISMATCH].active, "state %d", C.state);
    in.mfcConnected = 0;
    for (t = 31; t <= 40; t++) tickO2(&in, 0.5, t);
    CHECK(T, alarmIs(SG_A_MISMATCH, 2, "MFC not responding (CA disconnected)"), "no disconnect alarm");
    in.mfcConnected = 1; in.flow = 0;          /* back, with the flow down and the setpoint as it was */
    for (t = 41; t <= 46; t++) {
        tickO2(&in, 0.5, t);
        CHECK(T, !C.alarms[SG_A_MISMATCH].active, "mismatch %g s after the reconnect: '%s'", t - 41,
              C.alarms[SG_A_MISMATCH].msg);
    }
    CHECK(T, C.spChangeT == 41 && C.flowAtSpChange == 0, "timer %g flow %g", C.spChangeT,
          C.flowAtSpChange);
    tickO2(&in, 0.5, 47);                      /* 6 s > 5.3 s allowed, flow still 0 */
    CHECK(T, alarmIs(SG_A_MISMATCH, 2, "flow mismatch: cylinder empty or MFC fault?"),
          "no mismatch once the allowance is spent");
    return 0;
}

/* R5 f: gas table not He → Gas MINOR in PRECHECK; cleared by the next PRECHECK with He. */
static int t_gas(void)
{
    const char *T = "R5f gas not He";
    const char *MSG = "MFC gas table is N2, not He (flow reading wrong)";
    sg_inputs in = base(20.9);
    char cl[SG_MSG + 16];
    newCtl();
    in.flow = 20; in.sp = 20;
    snprintf(in.gas, sizeof in.gas, "N2");
    startPurge(&in, 20.9);
    CHECK(T, countLog(1, MSG) == 1 && alarmIs(SG_A_GAS, 1, MSG), "Gas alarm");
    CHECK(T, C.state == SG_PURGE, "purge refused: state %d", C.state);
    sg_op_flow_zero(&C);
    snprintf(in.gas, sizeof in.gas, "He");
    tickO2(&in, 20.9, 3);
    sg_op_purge(&C);
    tickO2(&in, 20.9, 4);
    snprintf(cl, sizeof cl, "cleared: %s", MSG);
    CHECK(T, !C.alarms[SG_A_GAS].active && countLog(0, cl) == 1, "Gas alarm not cleared");
    return 0;
}

/* Common set-up for the REGULATE alarm tests: short windows and delays, Resume Flow from IDLE at
   t = 1 with O2 near the target (not settling). */
static int regulateAtTarget(sg_inputs *in, double sp)
{
    C.p.stallWindow = 5; C.p.slopeAvgN = 2; C.p.flowAlarmDelay = 5;
    return resumeFromIdle(in, 0.99, sp);
}

/* R5 h1: flow high MINOR. */
static int t_flow_high(void)
{
    const char *T = "R5h flow high MINOR";
    const char *MSG = "flow ≥ 1.5× expected: check enclosure";
    sg_inputs in = base(0.99);
    double t;
    newCtl();
    CHECK(T, regulateAtTarget(&in, 0.4) == 1 && !C.settling, "resume at target failed");
    for (t = 2; t <= 40; t++) tickO2(&in, 0.99, t);
    CHECK(T, C.state == SG_REGULATE, "state %d", C.state);
    CHECK(T, alarmIs(SG_A_FLOWHIGH, 1, MSG) && countLog(1, MSG) == 1, "flowHigh MINOR missing");
    CHECK(T, !C.alarms[SG_A_FLOWLOW].active, "flowLow raised too");
    return 0;
}

/* R5 h2: flow low MINOR (judged on the PID demand OVAL). */
static int t_flow_low(void)
{
    const char *T = "R5h flow low MINOR";
    const char *MSG = "flow ≤ 0.5× expected: wrong enclosure mode selected?";
    sg_inputs in = base(0.99);
    double t;
    newCtl();
    CHECK(T, regulateAtTarget(&in, 0.1) == 1 && !C.settling, "resume at target failed");
    for (t = 2; t <= 40; t++) tickO2(&in, 0.99, t);
    CHECK(T, C.state == SG_REGULATE, "state %d", C.state);
    CHECK(T, alarmIs(SG_A_FLOWLOW, 1, MSG) && countLog(1, MSG) == 1, "flowLow MINOR missing");
    CHECK(T, !C.alarms[SG_A_FLOWHIGH].active, "flowHigh raised too");
    return 0;
}

/* R5 h3: target not reached within settleTimeout (default 7200 s → "2:00:00"). */
static int t_not_reached(void)
{
    const char *T = "R5h target not reached";
    const char *MSG = "target not reached within 2:00:00";
    sg_inputs in = base(2.0);
    double t;
    newCtl();
    CHECK(T, resumeFromIdle(&in, 2.0, 0.3) == 1 && C.settling && C.settleT0 == 1, "not settling");
    for (t = 2; t <= 7200; t++) tickO2(&in, 2.0, t);
    CHECK(T, !C.alarms[SG_A_NOTREACHED].active, "raised before settleTimeout");
    tickO2(&in, 2.0, 7201);
    CHECK(T, C.state == SG_REGULATE, "state %d", C.state);
    CHECK(T, alarmIs(SG_A_NOTREACHED, 1, MSG), "NotReached MINOR missing");
    CHECK(T, countLog(1, MSG) == 1 && logTime(MSG) == 7201, "log count %d at %g", countLog(1, MSG),
          logTime(MSG));
    return 0;
}

/* R5 h4: pinned-low note fires exactly once, when the count reaches pinnedTime. */
static int t_pinned_low(void)
{
    const char *T = "R5h pinned-low note";
    const char *MSG = "note: PID at minimum flow and O2 still below target";
    sg_inputs in = base(0.99);
    double t;
    newCtl();
    C.p.pinnedTime = 5;
    CHECK(T, regulateAtTarget(&in, 0.05) == 1 && !C.settling, "resume at target failed");
    CHECK(T, C.epid.OVAL <= C.epid.DRVL + 1e-6, "OVAL %g not at DRVL %g", C.epid.OVAL, C.epid.DRVL);
    for (t = 2; t <= 5; t++) tickO2(&in, 0.5, t);      /* O2 below target − tol: 4 counts */
    CHECK(T, countLog(0, MSG) == 0, "note before pinnedTime");
    tickO2(&in, 0.5, 6);                              /* 5th count */
    CHECK(T, countLog(0, MSG) == 1 && logTime(MSG) == 6, "note at pinnedTime: count %d at %g",
          countLog(0, MSG), logTime(MSG));
    for (t = 7; t <= 60; t++) tickO2(&in, 0.5, t);
    CHECK(T, countLog(0, MSG) == 1, "note repeated: %d", countLog(0, MSG));
    return 0;
}

/* R5 i1: cylinder-low MINOR then MAJOR from the run-out forecast. Totalizer 0.1 L/s from t = 60,
   capacity 5544 L: the 0.25-day window has data at t = 19500 (left 3600 L = 10 h), and the
   forecast falls below 6 h after t = 33900. */
static int t_cyl_low(void)
{
    const char *T = "R5i cylinder low";
    const char *MINOR = "helium cylinder empty in < 24 h (forecast)";
    const char *MAJOR = "helium cylinder empty in < 6 h (forecast)";
    sg_inputs in = base(0.5);
    double t;
    newCtl();
    C.p.cylCapacityL = 5544;
    for (t = 60; t <= 19499; t++) { in.total = 0.1 * (t - 60); tickO2(&in, 0.5, t); }
    CHECK(T, !C.alarms[SG_A_CYLLOW].active && isnan(C.cylMedianH), "early estimate: %g",
          C.cylMedianH);
    in.total = 0.1 * (19500 - 60); tickO2(&in, 0.5, 19500);
    CHECK(T, fabs(C.cylMedianH - 10) < 1e-6, "median %g h, want 10", C.cylMedianH);
    CHECK(T, alarmIs(SG_A_CYLLOW, 1, MINOR) && countLog(1, MINOR) == 1, "MINOR missing");
    for (t = 19501; t <= 33840; t++) { in.total = 0.1 * (t - 60); tickO2(&in, 0.5, t); }
    CHECK(T, alarmIs(SG_A_CYLLOW, 1, MINOR), "MINOR lost before 6 h (median %g)", C.cylMedianH);
    for (t = 33841; t <= 33960; t++) { in.total = 0.1 * (t - 60); tickO2(&in, 0.5, t); }
    CHECK(T, alarmIs(SG_A_CYLLOW, 2, MAJOR) && countLog(2, MAJOR) == 1, "MAJOR missing (median %g)",
          C.cylMedianH);
    return 0;
}

/* R5 i2: totalizer went backwards → re-baseline (without and with a logged usage sample). */
static int t_total_backwards(void)
{
    const char *T = "R5i totalizer backwards";
    const char *MSG = "Alicat totalizer went backwards (reset?): usage re-baselined";
    sg_inputs in = base(0.5);
    newCtl();
    in.total = 100; tickAt(&in, 1);
    CHECK(T, C.cylBase == 100, "cylBase %g", C.cylBase);
    in.total = 50; tickAt(&in, 2);
    CHECK(T, countLog(1, MSG) == 1, "log missing");
    CHECK(T, C.cylBase == 50 && C.cylLeftL == 8000, "no usage logged: cylBase %g left %g",
          C.cylBase, C.cylLeftL);
    in.total = 60; tickAt(&in, 60);                   /* 60 s sample: used 10 L */
    CHECK(T, C.nCyl == 1 && C.cylHist[0].used == 10, "sample: n %d used %g", C.nCyl,
          C.nCyl ? C.cylHist[0].used : NAN);
    in.total = 5; tickAt(&in, 61);
    CHECK(T, countLog(1, MSG) == 2, "second log missing");
    CHECK(T, C.cylBase == -5 && C.cylLeftL == 7990, "last used kept: cylBase %g left %g",
          C.cylBase, C.cylLeftL);
    return 0;
}

/* R7: a non-finite Total_RBV skips the ledger accumulation and the forecast for that tick. */
static int t_total_nan(void)
{
    const char *T = "R7 NaN total";
    sg_inputs in = base(0.5);
    newCtl();
    in.total = 100; tickAt(&in, 1);
    in.total = 105; tickAt(&in, 2);
    CHECK(T, C.cumL == 5 && C.cylLeftL == 7995, "cumL %g left %g", C.cumL, C.cylLeftL);
    in.total = NAN; tickAt(&in, 3);
    CHECK(T, C.cumL == 5 && C.lastTotal == 105, "cumL %g lastTotal %g", C.cumL, C.lastTotal);
    CHECK(T, C.cylBase == 100 && C.cylLeftL == 7995, "cylBase %g left %g", C.cylBase, C.cylLeftL);
    tickAt(&in, 60);                                  /* 60 s sample skipped */
    CHECK(T, C.nCyl == 0, "NaN usage sample logged");
    in.total = INFINITY; tickAt(&in, 61);
    CHECK(T, C.cumL == 5 && C.lastTotal == 105 && C.cylLeftL == 7995, "Inf: cumL %g left %g",
          C.cumL, C.cylLeftL);
    in.total = 110; tickAt(&in, 62);
    CHECK(T, C.cumL == 10 && C.lastTotal == 110 && C.cylLeftL == 7990, "after: cumL %g left %g",
          C.cumL, C.cylLeftL);
    tickAt(&in, 120);
    CHECK(T, C.nCyl == 1 && C.cylHist[0].used == 10, "sample after NaN: n %d", C.nCyl);
    CHECK(T, countLogSub("totalizer") == 0, "spurious totalizer log");
    return 0;
}

/* ================================================================ final-review guards */

static int allFinite(const double *a, int n)
{
    int i;
    for (i = 0; i < n; i++) if (!isfinite(a[i])) return 0;
    return 1;
}

/* Item 1: a never-connected MFC at start (Total_RBV = 0 from a disconnected channel) must not
   touch the restored ledger: no accumulation, no re-baseline, no usage sample, in IDLE too. */
static int t_ledger_disconnected(void)
{
    const char *T = "F1 ledger with the MFC disconnected";
    sg_inputs in = base(0.5);
    double t;
    newCtl();
    C.cumL = 100; C.lastTotal = 500; C.cylBase = 400;     /* as restored by autosave */
    in.mfcConnected = 0; in.total = 0;
    for (t = 58; t <= 60; t++) tickAt(&in, t);
    CHECK(T, C.state == SG_IDLE, "state %d", C.state);
    CHECK(T, C.cumL == 100 && C.lastTotal == 500 && C.cylBase == 400,
          "while disconnected: cumL %g lastTotal %g cylBase %g", C.cumL, C.lastTotal, C.cylBase);
    CHECK(T, C.nCyl == 0, "usage sample logged while disconnected");
    in.mfcConnected = 1; in.total = 510;
    tickAt(&in, 61);
    CHECK(T, C.cumL == 110 && C.lastTotal == 510, "after connect: cumL %g lastTotal %g", C.cumL,
          C.lastTotal);
    CHECK(T, C.cylBase == 400 && C.cylLeftL == 7890, "cylBase %g cylLeftL %g", C.cylBase, C.cylLeftL);
    CHECK(T, countLogSub("totalizer went backwards") == 0, "spurious 'went backwards' log");
    return 0;
}

/* Item 1, same rule: "New He cylinder" pressed while the MFC is disconnected leaves cylBase unset,
   and the first connected tick takes its Total_RBV as the base (full cylinder). */
static int t_new_cylinder_disconnected(void)
{
    const char *T = "F1b new cylinder with the MFC disconnected";
    sg_inputs in = base(0.5);
    newCtl();
    in.total = 500; tickAt(&in, 1);
    in.total = 700; tickAt(&in, 2);
    CHECK(T, C.cylBase == 500 && C.cylLeftL == 7800, "cylBase %g left %g", C.cylBase, C.cylLeftL);
    in.mfcConnected = 0; in.total = 0;
    tickAt(&in, 3);
    sg_new_cylinder(&C, "operator");
    CHECK(T, isnan(C.cylBase), "cylBase %g, want unset", C.cylBase);
    tickAt(&in, 4);
    in.mfcConnected = 1; in.total = 720;
    tickAt(&in, 5);
    CHECK(T, C.cylBase == 720 && C.cylLeftL == 8000, "after connect: cylBase %g left %g", C.cylBase,
          C.cylLeftL);
    CHECK(T, C.cumL == 220, "cumL %g", C.cumL);
    return 0;
}

/* Ruling R13: a non-finite O2 value counts as INVALID and the previous finite value is held for
   the histories, so the lid detector is not blinded after the reading recovers. */
static int t_o2_nan(void)
{
    const char *T = "R13 non-finite O2";
    sg_inputs in = base(0.5);
    double t;
    newCtl();
    for (t = 1; t <= 20; t++) tickO2(&in, 0.5, t);
    in.o2 = NAN; in.o2Sevr = 0;                          /* NaN with a good severity */
    for (t = 21; t <= 24; t++) tickAt(&in, t);
    in.o2 = INFINITY;
    tickAt(&in, 25);
    CHECK(T, !C.o2ok && alarmIs(SG_A_O2BAD, 2, "O2 reading invalid"), "o2ok %d, O2Bad not raised",
          C.o2ok);
    CHECK(T, allFinite(C.o2hist, C.nO2), "non-finite value in o2hist");
    CHECK(T, C.nO2 == 25 && C.o2hist[C.nO2 - 1] == 0.5 && C.o2 == 0.5, "held value: n %d last %g o2 %g",
          C.nO2, C.o2hist[C.nO2 - 1], C.o2);
    for (t = 26; t <= 40; t++) tickO2(&in, 0.5, t);
    CHECK(T, C.o2ok && isfinite(C.maxRate) && allFinite(C.rateHist, C.nRate),
          "after recovery: o2ok %d maxRate %g", C.o2ok, C.maxRate);
    return 0;
}

/* Ruling R11: restart with the MFC disconnected or a non-finite Setpoint_RBV enters IDLE, checked
   before the O2 branches. */
static int t_restart_mfc(void)
{
    const char *T = "R11 restart without the MFC";
    const char *LOG = "— → IDLE (restart: MFC not connected (§4.7))";
    sg_inputs in = base(0.5);
    newCtl();
    in.sp = 0.3; in.mfcConnected = 0;
    sg_set_inputs(&C, &in);
    sg_restart(&C, 100);
    CHECK(T, C.state == SG_IDLE && countLog(0, LOG) == 1, "disconnected: state %d", C.state);
    CHECK(T, countLog(0, "IOC started (autosaved settings restored)") == 1, "start log missing");
    CHECK(T, isnan(C.lastCmd) && C.epid.FBON == 0 && nPutSp == 0, "lastCmd %g FBON %d puts %d",
          C.lastCmd, C.epid.FBON, nPutSp);
    in.o2Sevr = 3;                                     /* O2 invalid too: the MFC branch is first */
    sg_set_inputs(&C, &in);
    sg_restart(&C, 101);
    CHECK(T, C.state == SG_IDLE && countLog(0, LOG) == 2, "O2 invalid: state %d", C.state);
    in.o2Sevr = 0; in.mfcConnected = 1; in.sp = NAN;
    sg_set_inputs(&C, &in);
    sg_restart(&C, 102);
    CHECK(T, C.state == SG_IDLE && countLog(0, LOG) == 3, "NaN sp: state %d", C.state);
    in.sp = INFINITY;
    sg_set_inputs(&C, &in);
    sg_restart(&C, 103);
    CHECK(T, C.state == SG_IDLE && countLog(0, LOG) == 4, "Inf sp: state %d", C.state);
    in.sp = 0.3;
    sg_set_inputs(&C, &in);
    sg_restart(&C, 104);
    CHECK(T, C.state == SG_REGULATE, "connected: state %d", C.state);
    return 0;
}

/* G1 (user decision 2026-09-29, spec §8.15 step 4): a restart with a valid O2 at or above
   lidLevel and the Alicat flowing resumes the purge (PRECHECK, then PURGE at purgeFlow) instead
   of a silent IDLE at up to 20 SLPM; with Setpoint_RBV 0 it stays IDLE. */
static int t_restart_lid(void)
{
    const char *T = "G1 restart above the lid threshold";
    const char *LOG = "— → PRECHECK (restart: O2 above lid threshold with the Alicat flowing: "
                      "purge resumed)";
    sg_inputs in = base(12);
    newCtl();
    in.sp = 20; in.flow = 20;
    sg_set_inputs(&C, &in);
    sg_restart(&C, 100);
    CHECK(T, C.state == SG_PRECHECK && countLog(0, LOG) == 1, "state %d", C.state);
    CHECK(T, nPutSp == 0, "put at the restart itself (%d)", nPutSp);
    tickO2(&in, 12, 100);                          /* the restart's own tick: PRECHECK -> PURGE */
    CHECK(T, C.state == SG_PURGE && C.sd.o2Start > 11.9, "state %d o2Start %g", C.state,
          C.sd.o2Start);
    CHECK(T, nPutSp == 1 && lastPutSp == 20 && C.lastCmd == 20, "purge flow: %d put(s), last %g",
          nPutSp, lastPutSp);
    CHECK(T, countLogSub("lid check") == 0, "lid check before the purge timer");
    tickO2(&in, 12, 101);
    CHECK(T, countLog(0, "lid check skipped: purge started at 12.00 % (< 17 %)") == 1,
          "12 %% start: lid check not skipped");
    /* setpoint 0: stays IDLE as before */
    newCtl();
    in = base(12); in.sp = 0; in.flow = 0;
    sg_set_inputs(&C, &in);
    sg_restart(&C, 200);
    CHECK(T, C.state == SG_IDLE && countLog(0, "— → IDLE (restart: setpoint is 0 (§4.7))") == 1,
          "setpoint 0: state %d", C.state);
    /* O2 invalid at the same level: OPEN_LOOP as before (the invalid branch comes first) */
    newCtl();
    in = base(12); in.sp = 20; in.o2Sevr = 3;
    sg_set_inputs(&C, &in);
    sg_restart(&C, 300);
    CHECK(T, C.state == SG_OPEN_LOOP, "O2 invalid: state %d", C.state);
    return 0;
}

/* Ruling R12: a non-finite flow is never commanded (logged MAJOR once per episode); -0 becomes +0. */
static int t_command_nan(void)
{
    const char *T = "R12 non-finite command";
    const char *MSG = "command ignored: flow value is not a number";
    sg_inputs in = base(0.5);
    newCtl();
    CHECK(T, resumeFromIdle(&in, 0.5, 0.3) == 1 && C.epid.FBON == 1, "resume refused");
    nPutSp = 0;
    sg_pid_done(&C, NAN);
    CHECK(T, C.lastCmd == 0.3 && nPutSp == 0, "NaN: lastCmd %g puts %d", C.lastCmd, nPutSp);
    CHECK(T, countLog(2, MSG) == 1, "NaN: log count %d", countLog(2, MSG));
    sg_pid_done(&C, INFINITY);
    CHECK(T, C.lastCmd == 0.3 && nPutSp == 0 && countLog(2, MSG) == 1, "Inf: lastCmd %g puts %d logs %d",
          C.lastCmd, nPutSp, countLog(2, MSG));
    sg_pid_done(&C, 0.4);
    CHECK(T, C.lastCmd == 0.4 && nPutSp == 1 && lastPutSp == 0.4, "finite: lastCmd %g puts %d",
          C.lastCmd, nPutSp);
    sg_pid_done(&C, -INFINITY);
    CHECK(T, C.lastCmd == 0.4 && nPutSp == 1 && countLog(2, MSG) == 2, "-Inf: lastCmd %g puts %d logs %d",
          C.lastCmd, nPutSp, countLog(2, MSG));
    sg_pid_done(&C, -0.0);
    CHECK(T, nPutSp == 2 && lastPutSp == 0 && !signbit(lastPutSp) && C.lastCmd == 0 && !signbit(C.lastCmd),
          "-0: puts %d put %g lastCmd %g", nPutSp, lastPutSp, C.lastCmd);
    return 0;
}

/* "cleared: <msg>" */
static int countCleared(const char *msg)
{
    char b[SG_MSG + 16];
    snprintf(b, sizeof b, "cleared: %s", msg);
    return countLog(0, b);
}

/* D1b (spec §8.14 "Setpoint follow"): a lost Flow Zero leaves the Alicat at its old setpoint;
   after holdDetect + mismatchMargin s the MAJOR follow alarm is raised and lastCmd re-sent every
   holdRetryInterval s; never judged in shadow mode, cleared silently in IDLE. */
/* D1b with the Alicat's ramp: Setpoint_RBV reports the RAMPED setpoint (archive, 24 Sep: 0.43
   then 20 over 7 s on a purge). A 20 SLPM step at 0.5 SLPM/s takes ~40 s: while the readback
   ramps toward the command, no alarm; stuck, the MAJOR comes only after step/ramp + 8 s. */
static int t_follow_ramp(void)
{
    const char *T = "D1b setpoint follow, slow ramp";
    sg_inputs in;
    double t, allowed;
    int k;
    for (k = 0; k < 2; k++) {                  /* k = 0 the readback ramps; k = 1 it is stuck */
        newCtl();
        in = base(0.5);
        in.writeEnabled = 1;
        in.ramp = 0.5;
        CHECK(T, resumeFromIdle(&in, 0.5, 0.3) == 1, "resume refused");
        sg_pid_done(&C, 20);                  /* the PID commands 20 SLPM at now = 1 */
        CHECK(T, fabs(C.followStep - 19.7) < 1e-9, "followStep %g", C.followStep);
        allowed = 19.7 / 0.5 + 3 + 5;          /* 47.4 s */
        for (t = 2; t <= 48; t++) {
            if (k == 0) in.sp = fmin(20, 0.3 + 0.5 * (t - 1));
            in.flow = in.sp;                   /* the flow follows the readback: no flow mismatch */
            tickO2(&in, 0.5, t);
            CHECK(T, !C.alarms[SG_A_MISMATCH].active, "%s: alarm at %g s (allowed %g): '%s'",
                  k ? "stuck" : "ramping", t - 1, allowed, C.alarms[SG_A_MISMATCH].msg);
        }
        tickO2(&in, 0.5, 49);                  /* 48 s after the command */
        if (k == 0) CHECK(T, !C.alarms[SG_A_MISMATCH].active, "ramping: alarm after reaching 20");
        else CHECK(T, C.alarms[SG_A_MISMATCH].active && C.alarms[SG_A_MISMATCH].sev == 2,
                   "stuck: no MAJOR 48 s after the command");
    }
    return 0;
}

static int t_follow(void)
{
    const char *T = "D1b setpoint follow";
    const char *MSG = "Alicat setpoint 0.30 SLPM does not follow the controller (0.00 SLPM): "
                      "write lost or another writer";
    sg_inputs in = base(0.5);
    double t;
    newCtl();
    in.writeEnabled = 1;
    CHECK(T, resumeFromIdle(&in, 0.5, 0.3) == 1, "resume refused");
    sg_op_flow_zero(&C);                       /* its command(0) is lost: the Alicat stays at 0.3 */
    CHECK(T, C.state == SG_FLOW_ZERO && C.lastCmd == 0, "state %d lastCmd %g", C.state, C.lastCmd);
    nPutSp = 0;
    for (t = 2; t <= 9; t++) tickO2(&in, 0.5, t);   /* 8 off ticks = holdDetect + mismatchMargin */
    CHECK(T, !C.alarms[SG_A_MISMATCH].active && nPutSp == 0, "alarm or re-send within 8 s: '%s'",
          C.alarms[SG_A_MISMATCH].msg);
    tickO2(&in, 0.5, 10);
    CHECK(T, alarmIs(SG_A_MISMATCH, 2, MSG), "no MAJOR after 9 s: '%s'", C.alarms[SG_A_MISMATCH].msg);
    CHECK(T, countLog(2, MSG) == 1, "alarm log count %d", countLog(2, MSG));
    CHECK(T, nPutSp == 1 && lastPutSp == 0, "re-send at the alarm: %d put(s), last %g", nPutSp,
          lastPutSp);
    for (t = 11; t <= 19; t++) tickO2(&in, 0.5, t);
    CHECK(T, nPutSp == 1, "re-sent before holdRetryInterval (%d)", nPutSp);
    tickO2(&in, 0.5, 20);
    CHECK(T, nPutSp == 2 && lastPutSp == 0, "no re-send after holdRetryInterval (%d)", nPutSp);
    CHECK(T, alarmIs(SG_A_MISMATCH, 2, MSG) && countLog(2, MSG) == 1 && countCleared(MSG) == 0,
          "the flow check (flow = Setpoint_RBV) cleared or re-logged it");
    in.sp = 0; in.flow = 0;                    /* the Alicat follows at last */
    tickO2(&in, 0.5, 21);
    CHECK(T, !C.alarms[SG_A_MISMATCH].active && countCleared(MSG) == 1, "not cleared (logged) "
          "when followed");
    /* shadow mode: never judged, no re-send */
    newCtl();
    in = base(0.5);
    CHECK(T, resumeFromIdle(&in, 0.5, 0.3) == 1, "shadow: resume refused");
    sg_op_flow_zero(&C);
    nPutSp = 0;
    for (t = 2; t <= 40; t++) tickO2(&in, 0.5, t);
    CHECK(T, !C.alarms[SG_A_MISMATCH].active && nPutSp == 0, "shadow: alarm '%s' or %d put(s)",
          C.alarms[SG_A_MISMATCH].msg, nPutSp);
    /* IDLE: the controller does not own the flow; the alarm clears silently */
    newCtl();
    in = base(0.5);
    in.writeEnabled = 1;
    CHECK(T, resumeFromIdle(&in, 0.5, 0.3) == 1, "IDLE: resume refused");
    sg_op_flow_zero(&C);
    for (t = 2; t <= 10; t++) tickO2(&in, 0.5, t);
    CHECK(T, alarmIs(SG_A_MISMATCH, 2, MSG), "IDLE: not raised first");
    sg_op_idle(&C);
    tickO2(&in, 0.5, 11);
    CHECK(T, !C.alarms[SG_A_MISMATCH].active && countCleared(MSG) == 0, "IDLE: not cleared silently");
    /* the hold-resume re-send restarts the count */
    newCtl();
    in = base(0.5);
    in.writeEnabled = 1;
    CHECK(T, resumeFromIdle(&in, 0.5, 0.3) == 1, "hold: resume refused");
    sg_op_flow_zero(&C);
    for (t = 2; t <= 6; t++) tickO2(&in, 0.5, t);        /* 5 off ticks */
    in.running = 0;
    for (t = 7; t <= 12; t++) tickO2(&in, 0.5, t);       /* on hold: not judged */
    in.running = 1;
    for (t = 13; t <= 20; t++) tickO2(&in, 0.5, t);      /* resume re-send, then 8 off ticks */
    CHECK(T, !C.alarms[SG_A_MISMATCH].active, "hold: count not restarted: '%s'",
          C.alarms[SG_A_MISMATCH].msg);
    tickO2(&in, 0.5, 21);
    CHECK(T, alarmIs(SG_A_MISMATCH, 2, MSG), "hold: no alarm 9 s after the resume");
    return 0;
}

/* D4 (spec §8.3): a PID output or command that is not a number is on the banner while it lasts;
   the next finite command clears it; a higher source (disconnect) shows over it; IDLE clears it
   silently. */
static int t_nocompute(void)
{
    const char *T = "D4 cannot compute a flow";
    const char *MSG = "controller cannot compute a flow (PID output not a number): flow held at "
                      "0.30 SLPM";
    const char *MSG2 = "controller cannot compute a flow (PID output not a number): flow held at "
                       "0.35 SLPM";
    sg_inputs in = base(0.5);
    double t;
    newCtl();
    CHECK(T, resumeFromIdle(&in, 0.5, 0.3) == 1 && C.epid.FBON == 1, "resume refused");
    sg_pid_done(&C, NAN);
    CHECK(T, alarmIs(SG_A_MISMATCH, 2, MSG), "no MAJOR: '%s'", C.alarms[SG_A_MISMATCH].msg);
    CHECK(T, countLog(2, MSG) == 1, "alarm log count %d", countLog(2, MSG));
    CHECK(T, C.epid.OVAL == 0.3, "OVAL %g, want the held 0.3", C.epid.OVAL);
    for (t = 2; t <= 5; t++) tickO2(&in, 0.5, t);
    sg_pid_done(&C, INFINITY);
    CHECK(T, alarmIs(SG_A_MISMATCH, 2, MSG) && countLog(2, MSG) == 1 && countCleared(MSG) == 0,
          "cleared or re-logged by the flow check");
    in.mfcConnected = 0;                       /* a higher source shows over it ... */
    for (t = 6; t <= 10; t++) tickO2(&in, 0.5, t);
    CHECK(T, alarmIs(SG_A_MISMATCH, 2, "MFC not responding (CA disconnected)"), "disconnect not shown");
    in.mfcConnected = 1;                       /* ... and hands back */
    tickO2(&in, 0.5, 11);
    CHECK(T, alarmIs(SG_A_MISMATCH, 2, MSG) && countLog(2, MSG) == 2, "not shown again after the "
          "reconnect: '%s'", C.alarms[SG_A_MISMATCH].msg);
    sg_pid_done(&C, 0.35);
    CHECK(T, !C.alarms[SG_A_MISMATCH].active && countCleared(MSG) == 1, "not cleared by a finite output");
    /* the command path: OPEN_LOOP commands expectedFlow every tick */
    C.p.modes[0].baseFlow = NAN;
    sg_enter(&C, SG_OPEN_LOOP, "test");
    tickO2(&in, 0.5, 12);
    CHECK(T, alarmIs(SG_A_MISMATCH, 2, MSG2), "OPEN_LOOP NaN: '%s'", C.alarms[SG_A_MISMATCH].msg);
    C.p.modes[0].baseFlow = 0.25;
    tickO2(&in, 0.5, 13);
    CHECK(T, !C.alarms[SG_A_MISMATCH].active && countCleared(MSG2) == 1, "OPEN_LOOP finite: not cleared");
    C.p.modes[0].baseFlow = NAN;
    tickO2(&in, 0.5, 14);                      /* lastCmd is 0.25 now */
    CHECK(T, alarmIs(SG_A_MISMATCH, 2, "controller cannot compute a flow (PID output not a number): "
                     "flow held at 0.25 SLPM"), "OPEN_LOOP NaN again: '%s'", C.alarms[SG_A_MISMATCH].msg);
    sg_op_idle(&C);
    tickO2(&in, 0.5, 15);
    CHECK(T, !C.alarms[SG_A_MISMATCH].active && countLogSub("cleared: controller cannot") == 2,
          "IDLE: not cleared silently");
    return 0;
}

/* D1a, core side (spec §8.3, §8.14): the write-failed source outranks the follow source and the
   flow check; an MFC change resets every source. */
static int t_write_fail(void)
{
    const char *T = "D1a write failed";
    const char *W = "MFC write failed: SIM:Alicat1:Setpoint (pvStat -1): controller cannot act";
    const char *F = "Alicat setpoint 0.30 SLPM does not follow the controller (0.00 SLPM): "
                    "write lost or another writer";
    sg_inputs in = base(0.5);
    double t;
    newCtl();
    in.writeEnabled = 1;
    CHECK(T, resumeFromIdle(&in, 0.5, 0.3) == 1, "resume refused");
    sg_mfc_write_status(&C, W);
    CHECK(T, alarmIs(SG_A_MISMATCH, 2, W) && countLog(2, W) == 1, "no MAJOR: '%s'",
          C.alarms[SG_A_MISMATCH].msg);
    for (t = 2; t <= 5; t++) tickO2(&in, 0.5, t);
    CHECK(T, alarmIs(SG_A_MISMATCH, 2, W) && countCleared(W) == 0, "cleared by the flow check");
    sg_op_flow_zero(&C);
    for (t = 6; t <= 16; t++) tickO2(&in, 0.5, t);        /* the follow source comes on below it */
    CHECK(T, alarmIs(SG_A_MISMATCH, 2, W) && countLog(2, F) == 0, "follow shown over the write "
          "failure: '%s'", C.alarms[SG_A_MISMATCH].msg);
    sg_mfc_write_status(&C, NULL);                        /* writes work again: follow shows */
    CHECK(T, alarmIs(SG_A_MISMATCH, 2, F) && countLog(2, F) == 1, "follow not shown: '%s'",
          C.alarms[SG_A_MISMATCH].msg);
    in.sp = 0; in.flow = 0;
    tickO2(&in, 0.5, 17);
    CHECK(T, !C.alarms[SG_A_MISMATCH].active && countCleared(F) == 1, "not cleared");
    sg_mfc_write_status(&C, W);
    sg_channels_changed(&C, 0, 1);                        /* §8.21: every source reset */
    tickO2(&in, 0.5, 18);
    CHECK(T, !C.alarms[SG_A_MISMATCH].active && C.mmShown == -1, "the MFC change kept '%s'",
          C.alarms[SG_A_MISMATCH].msg);
    sg_mfc_write_status(&C, W);
    sg_set_inputs(&C, &in);
    sg_restart(&C, 100);                                  /* the restart too */
    CHECK(T, !C.alarms[SG_A_MISMATCH].active && !C.mmOn[SG_MM_WRITE], "the restart kept it");
    return 0;
}

/* ================================================================ glue entry points */

/* Item 3: after sg_restart into REGULATE the first sg_pid_prepare hands the glue FBON = 1 and
   OUTL = lastCmd (the glue writes PID:Out = OUTL before processing on FBON 0 -> 1). */
static int t_pid_bumpless(void)
{
    const char *T = "F3 bumpless PID start";
    sg_inputs in = base(0.5);
    sg_epid_cfg cfg;
    newCtl();
    in.sp = 0.3; in.flow = 0.3;
    sg_set_inputs(&C, &in);
    sg_restart(&C, 10);
    tickAt(&in, 10);
    CHECK(T, C.state == SG_REGULATE && sg_pid_due(&C, 10), "state %d", C.state);
    memset(&cfg, 0xff, sizeof cfg);
    CHECK(T, sg_pid_prepare(&C, &cfg) == 1, "prepare refused");
    CHECK(T, cfg.FBON == 1 && cfg.OUTL == C.lastCmd && C.lastCmd == 0.3, "FBON %d OUTL %g lastCmd %g",
          cfg.FBON, cfg.OUTL, C.lastCmd);
    CHECK(T, cfg.VAL == C.epid.VAL && cfg.KP == C.epid.KP && cfg.DRVH == C.epid.DRVH &&
          cfg.CVAL == C.epid.CVAL, "cfg does not mirror epid");
    /* FBON = 0 outside REGULATE, OUTL still lastCmd */
    sg_enter(&C, SG_OPEN_LOOP, "test");
    tickAt(&in, 20);
    memset(&cfg, 0xff, sizeof cfg);
    CHECK(T, sg_pid_prepare(&C, &cfg) == 1 && cfg.FBON == 0 && cfg.OUTL == 0.25 && C.lastCmd == 0.25,
          "OPEN_LOOP: FBON %d OUTL %g lastCmd %g", cfg.FBON, cfg.OUTL, C.lastCmd);
    return 0;
}

/* §8.11 scope of the fully bumpless start: seeded after the restart decision and Resume Flow
   from IDLE; after HANDOFF epid's own start (cfg.SEED = 0), as the reference. Plan 4 Task 3
   (sc01): seeding at the handoff clamped I at DRVL, started the integral ~0.2 SLPM low and let O2
   overshoot to 1.03 % with a MINOR alarm the reference does not raise. */
static int t_seed_scope(void)
{
    const char *T = "F3c bumpless seed scope";
    sg_inputs in = base(0.5);
    sg_epid_cfg cfg;
    char why[SG_MSG];
    double t;
    /* restart into REGULATE: seeded */
    newCtl();
    in.sp = 0.3; in.flow = 0.3;
    sg_set_inputs(&C, &in);
    sg_restart(&C, 10);
    tickAt(&in, 10);
    memset(&cfg, 0xff, sizeof cfg);
    CHECK(T, C.state == SG_REGULATE && sg_pid_prepare(&C, &cfg) == 1 && cfg.SEED == 1,
          "restart: state %d SEED %d", C.state, cfg.SEED);
    /* OPEN_LOOP -> Resume Flow -> HANDOFF -> REGULATE: not seeded */
    sg_enter(&C, SG_OPEN_LOOP, "test");
    tickO2(&in, 0.5, 11);
    CHECK(T, sg_op_resume_flow(&C, why, sizeof why) == 1 && C.state == SG_HANDOFF,
          "resume from OPEN_LOOP: state %d ('%s')", C.state, why);
    in.flow = 0.25;
    for (t = 12; t <= 20 && C.state != SG_REGULATE; t++) tickO2(&in, 0.5, t);
    memset(&cfg, 0xff, sizeof cfg);
    CHECK(T, C.state == SG_REGULATE && sg_pid_prepare(&C, &cfg) == 1 && cfg.SEED == 0,
          "after HANDOFF: state %d SEED %d", C.state, cfg.SEED);
    /* Resume Flow from IDLE: seeded */
    newCtl();
    in = base(0.5);
    CHECK(T, resumeFromIdle(&in, 0.5, 0.3) == 1 && C.state == SG_REGULATE, "resume from IDLE");
    memset(&cfg, 0xff, sizeof cfg);
    CHECK(T, sg_pid_prepare(&C, &cfg) == 1 && cfg.SEED == 1, "resume from IDLE: SEED %d", cfg.SEED);
    return 0;
}

/* devEpidSoft's PID processing at FBON 0 -> 1 (std stdApp/src/devEpidSoft.c, epidFeedbackMode_PID
   with FBOP 0): I read from OUTL, no integration, output P + I clamped, then the ODEL deadband
   against the record's previous OVAL. KD = 0. Returns the record's new OVAL. */
static double epidSoftStart(const sg_epid_cfg *cfg, double outl, double odel, double ovalOld)
{
    double i = cfg->KI == 0 ? 0 : outl, oval = cfg->KP * (cfg->VAL - cfg->CVAL) + i;
    if (oval > cfg->DRVH) oval = cfg->DRVH;
    if (oval < cfg->DRVL) oval = cfg->DRVL;
    return (odel == 0 || fabs(ovalOld - oval) > odel) ? oval : ovalOld;
}

/* User decision 2026-09-29 (spec §8.11): the first output after FBON 0 -> 1 is lastCmd, with the
   glue writing PID:Out = sg_pid_bumpless_i(cfg) and ODEL 0 for that processing. */
static int t_pid_bumpless_seed(void)
{
    const char *T = "F3b fully bumpless PID start";
    sg_epid_cfg cfg = { .VAL = 0.99, .KP = -9.3, .KI = 8.8e-4, .DRVL = 0.05, .DRVH = 1.0,
                        .ODEL = 0.01, .CVAL = 1.0, .OUTL = 0.3, .FBON = 1 };
    double i0 = sg_pid_bumpless_i(&cfg), o;
    /* the plain epid start (PID:Out = lastCmd) bumps by P = +0.093 */
    o = epidSoftStart(&cfg, cfg.OUTL, 0, 0.305);
    CHECK(T, fabs(o - 0.393) < 1e-12, "plain start %.15g, want 0.393", o);
    CHECK(T, fabs(i0 - 0.207) < 1e-12, "seed %.15g, want 0.207", i0);
    o = epidSoftStart(&cfg, i0, 0, 0.305);
    CHECK(T, fabs(o - 0.3) < 1e-12, "first output %.15g, want lastCmd 0.3", o);
    /* why ODEL is 0 at the start: the deadband would keep the stale OVAL 0.305 */
    o = epidSoftStart(&cfg, i0, cfg.ODEL, 0.305);
    CHECK(T, o == 0.305, "ODEL %g kept %.15g", cfg.ODEL, o);
    /* near DRVL the seed is clamped: 0.1 - 0.093 -> 0.05, first output 0.143 (the direction P asks) */
    cfg.OUTL = 0.1;
    i0 = sg_pid_bumpless_i(&cfg);
    o = epidSoftStart(&cfg, i0, 0, 0.1);
    CHECK(T, i0 == 0.05 && fabs(o - 0.143) < 1e-12, "clamped: seed %.15g first %.15g", i0, o);
    /* O2 below target (P < 0) seeds above lastCmd */
    cfg.OUTL = 0.3; cfg.CVAL = 0.95;
    i0 = sg_pid_bumpless_i(&cfg);
    o = epidSoftStart(&cfg, i0, 0, 0.2);
    CHECK(T, i0 > 0.3 && fabs(o - 0.3) < 1e-12, "P<0: seed %.15g first %.15g", i0, o);
    /* NaN OUTL -> NaN (glue leaves PID:Out alone); NaN CVAL -> OUTL (the plain start) */
    cfg.OUTL = NAN;
    CHECK(T, isnan(sg_pid_bumpless_i(&cfg)), "NaN OUTL gave %g", sg_pid_bumpless_i(&cfg));
    cfg.OUTL = 0.3; cfg.CVAL = NAN;
    CHECK(T, sg_pid_bumpless_i(&cfg) == 0.3, "NaN CVAL gave %g", sg_pid_bumpless_i(&cfg));
    return 0;
}

/* Item 4: sg_channels_changed resets what the old channels produced (spec §8.21 steps 4-5). */
static int t_channels_changed(void)
{
    const char *T = "F4 channels changed";
    sg_inputs in = base(5);
    double t;
    int n0;
    newCtl();
    in.total = 100;
    for (t = 1; t <= 30; t++) tickO2(&in, 5 + 0.5 * t, t);        /* rising O2, above lidLevel */
    CHECK(T, C.nO2 > 0 && C.nRate > 0 && C.nAvg > 0 && C.aboveCount > 0 && C.maxRate > 0,
          "set-up: nO2 %d nRate %d nAvg %d above %d maxRate %g", C.nO2, C.nRate, C.nAvg,
          C.aboveCount, C.maxRate);
    C.sameCount = 31; C.frozen = 1;
    sg_set_alarm(&C, SG_A_O2BAD, 2, "O2 reading frozen");
    C.holdSec = 5; C.holdAttempts = 2; C.nextHoldAttempt = 99; C.holdRecovering = 1;
    C.mfcDisconnSec = 4; C.mfcDisconnAlarm = 1;
    sg_set_alarm(&C, SG_A_MISMATCH, 2, "flow mismatch: cylinder empty or MFC fault?");
    sg_set_alarm(&C, SG_A_HOLDSTUCK, 2, "MFC on hold, cannot resume");
    sg_set_alarm(&C, SG_A_GAS, 1, "MFC gas table is N2, not He (flow reading wrong)");
    C.nCyl = 3;
    CHECK(T, C.lastTotal == 100 && C.cylBase == 100 && isfinite(C.spSeen), "set-up ledger");
    n0 = nLog;
    sg_channels_changed(&C, 1, 0);                                  /* O2 only */
    CHECK(T, C.nO2 == 0 && C.nRate == 0 && C.nAvg == 0 && C.sameCount == 0 && C.aboveCount == 0 &&
          C.maxRate == 0 && C.frozen == 0, "O2: nO2 %d nRate %d nAvg %d same %d above %d max %g frozen %d",
          C.nO2, C.nRate, C.nAvg, C.sameCount, C.aboveCount, C.maxRate, C.frozen);
    CHECK(T, !C.alarms[SG_A_O2BAD].active, "O2Bad not cleared");
    CHECK(T, C.holdSec == 5 && C.alarms[SG_A_MISMATCH].active && C.lastTotal == 100 && C.nCyl == 3,
          "O2 change touched the MFC state");
    sg_channels_changed(&C, 0, 1);                                  /* MFC only */
    CHECK(T, isnan(C.spSeen) && C.spChangeT == 0 && C.flowAtSpChange == 0, "sp tracking not reset");
    CHECK(T, C.holdSec == 0 && C.holdAttempts == 0 && C.nextHoldAttempt == 0 && C.holdRecovering == 0,
          "hold counters: %d %d %g %d", C.holdSec, C.holdAttempts, C.nextHoldAttempt, C.holdRecovering);
    CHECK(T, C.mfcDisconnSec == 0 && C.mfcDisconnAlarm == 0, "disconnect counters");
    CHECK(T, !C.alarms[SG_A_MISMATCH].active && !C.alarms[SG_A_HOLDSTUCK].active &&
          !C.alarms[SG_A_GAS].active, "alarms not cleared");
    CHECK(T, isnan(C.lastTotal) && isnan(C.cylBase) && C.nCyl == 0, "ledger: lastTotal %g cylBase %g nCyl %d",
          C.lastTotal, C.cylBase, C.nCyl);
    CHECK(T, nLog == n0, "logged %d line(s): '%s'", nLog - n0, n0 < MAXLOG ? logMsg[n0] : "");
    /* the next tick takes the new totalizer as its reference: nothing counted as usage */
    in.total = 900;
    tickO2(&in, 5, 31);
    CHECK(T, C.cumL == 0 && C.lastTotal == 900 && C.cylBase == 900 && C.cylLeftL == 8000,
          "after: cumL %g lastTotal %g cylBase %g left %g", C.cumL, C.lastTotal, C.cylBase, C.cylLeftL);
    return 0;
}

/* The glue's order (sgIoc.c sgIocTick): inputs from the OLD Alicat are set, then Cfg:Apply calls
   sg_channels_changed, then sg_tick runs in the same tick; the new Alicat's totalizer arrives on
   a later tick. Neither a lower nor a higher new totalizer may log "went backwards" or count as
   usage (spec §8.21 step 5: CumL does not jump). */
static int t_channels_changed_midtick(void)
{
    const char *T = "F4b MFC change mid-tick";
    static const double newTotal[2] = { 500, 20000 };
    sg_inputs in;
    double t;
    int k;
    for (k = 0; k < 2; k++) {
        in = base(5);
        newCtl();
        in.total = 13227;
        for (t = 1; t <= 10; t++) tickO2(&in, 5, t);
        sg_set_inputs(&C, &in);                          /* tick 11: old Alicat's reading */
        sg_channels_changed(&C, 0, 1);
        sg_tick(&C, 11);
        in.mfcConnected = 0;                             /* new channel not connected yet */
        tickO2(&in, 5, 12);
        in.mfcConnected = 1; in.total = newTotal[k];
        for (t = 13; t <= 15; t++) tickO2(&in, 5, t);
        CHECK(T, countLogSub("went backwards") == 0, "new total %g: 'went backwards' logged",
              newTotal[k]);
        CHECK(T, C.cumL == 0 && C.cylBase == newTotal[k] && C.lastTotal == newTotal[k],
              "new total %g: cumL %g cylBase %g lastTotal %g", newTotal[k], C.cumL, C.cylBase,
              C.lastTotal);
    }
    return 0;
}

static sg_helium H1, H2;   /* ~224 KB each: static */

/* Item 5: helium autosave export/import: round trip, then a corrupt image. */
static int t_helium_io(void)
{
    const char *T = "F5 helium import/export";
    sg_inputs in = base(0.5);
    double t;
    int i, dropped;
    newCtl();
    in.flow = 20; in.sp = 20;
    for (t = 1; t <= 7300; t++) {
        in.total = 0.01 * t;
        if (t == 100) sg_op_purge(&C);
        if (t == 3000) sg_op_flow_zero(&C);
        if (t == 5000) sg_mark_new_run(&C);
        if (t == 6000) sg_new_cylinder(&C, "operator");
        tickO2(&in, 0.5, t);
    }
    CHECK(T, C.nLedger == 4 && C.nSnaps == 3 && C.nCyl > 10, "set-up: ev %d snaps %d hist %d",
          C.nLedger, C.nSnaps, C.nCyl);
    sg_helium_export(&C, &H1);
    CHECK(T, H1.cumL == C.cumL && H1.lastTotal == C.lastTotal && H1.cylBase == C.cylBase &&
          H1.histN == C.nCyl && H1.evN == C.nLedger && H1.snapN == C.nSnaps, "export scalars/counts");
    CHECK(T, H1.evT[3] == C.ledger[3].t && H1.evType[3] == SG_EV_CYLINDER && H1.evL[3] == C.ledger[3].L &&
          H1.snapT[2] == C.snaps[2].t && H1.snapL[2] == C.snaps[2].L &&
          H1.histT[5] == C.cylHist[5].t && H1.histUsed[5] == C.cylHist[5].used, "export arrays");
    newCtl();
    dropped = sg_helium_import(&C, &H1);
    sg_helium_export(&C, &H2);
    CHECK(T, dropped == 0, "clean image: %d dropped", dropped);
    CHECK(T, H2.cumL == H1.cumL && H2.lastTotal == H1.lastTotal && H2.cylBase == H1.cylBase &&
          H2.histN == H1.histN && H2.evN == H1.evN && H2.snapN == H1.snapN, "round trip scalars");
    for (i = 0; i < H1.histN; i++)
        CHECK(T, H2.histT[i] == H1.histT[i] && H2.histUsed[i] == H1.histUsed[i], "hist[%d]", i);
    for (i = 0; i < H1.evN; i++)
        CHECK(T, H2.evT[i] == H1.evT[i] && H2.evType[i] == H1.evType[i] && H2.evL[i] == H1.evL[i], "ev[%d]", i);
    for (i = 0; i < H1.snapN; i++)
        CHECK(T, H2.snapT[i] == H1.snapT[i] && H2.snapL[i] == H1.snapL[i], "snap[%d]", i);

    /* corrupt image */
    memset(&H1, 0, sizeof H1);
    H1.cumL = 50; H1.lastTotal = INFINITY; H1.cylBase = NAN;
    H1.histN = SG_CYLHIST + 5;                                  /* 5 beyond capacity */
    for (i = 0; i < SG_CYLHIST; i++) { H1.histT[i] = 60.0 * i; H1.histUsed[i] = 0.1 * i; }
    H1.histT[10] = NAN;                                         /* non-finite t */
    H1.histT[20] = 5;                                           /* out of time order */
    H1.histUsed[30] = NAN;                                      /* non-finite value */
    H1.evN = 7;
    H1.evT[0] = 100; H1.evType[0] = 1;   H1.evL[0] = 0;
    H1.evT[1] = 200; H1.evType[1] = 7;   H1.evL[1] = 1;         /* unknown type */
    H1.evT[2] = NAN; H1.evType[2] = 2;   H1.evL[2] = 1;         /* non-finite t */
    H1.evT[3] = 300; H1.evType[3] = 2;   H1.evL[3] = 2;
    H1.evT[4] = 250; H1.evType[4] = 3;   H1.evL[4] = 2;         /* out of time order */
    H1.evT[5] = 300; H1.evType[5] = 1.5; H1.evL[5] = 2;         /* not an event type */
    H1.evT[6] = 400; H1.evType[6] = 4;   H1.evL[6] = 3;
    H1.snapN = -3;                                              /* negative count: none */
    newCtl();
    dropped = sg_helium_import(&C, &H1);
    CHECK(T, dropped == 5 + 3 + 4, "dropped %d, want 12", dropped);
    CHECK(T, C.cumL == 50 && isnan(C.lastTotal) && isnan(C.cylBase), "scalars: cumL %g lastTotal %g cylBase %g",
          C.cumL, C.lastTotal, C.cylBase);
    CHECK(T, C.nCyl == SG_CYLHIST - 3 && C.cylHist[10].t == 660 && C.cylHist[19].t == 1260 &&
          C.cylHist[28].t == 1860 && C.cylHist[28].used == 0.1 * 31, "hist: n %d", C.nCyl);
    CHECK(T, C.nLedger == 3 && C.ledger[0].type == SG_EV_PURGE && C.ledger[1].t == 300 &&
          C.ledger[1].type == SG_EV_ZERO && C.ledger[2].type == SG_EV_NEWRUN && C.ledger[2].L == 3,
          "ledger: n %d", C.nLedger);
    CHECK(T, C.nSnaps == 0, "snaps %d", C.nSnaps);
    H1.cumL = NAN;
    newCtl();
    sg_helium_import(&C, &H1);
    CHECK(T, C.cumL == 0, "NaN cumL imported as %g", C.cumL);
    return 0;
}

/* Item 6: Diag:FlowSteady comes from sg_ctl.flowSteady, set in REGULATE, 0 elsewhere. */
static int t_flow_steady(void)
{
    const char *T = "F6 flowSteady";
    sg_inputs in = base(0.99);
    double t;
    newCtl();
    CHECK(T, regulateAtTarget(&in, 0.3) == 1, "resume refused");
    for (t = 2; t <= 20; t++) tickO2(&in, 0.99, t);
    CHECK(T, C.state == SG_REGULATE && C.flowSteady == 1, "REGULATE: state %d flowSteady %d", C.state,
          C.flowSteady);
    sg_op_flow_zero(&C);
    tickO2(&in, 0.99, 21);
    CHECK(T, C.flowSteady == 0, "FLOW_ZERO: flowSteady %d", C.flowSteady);
    return 0;
}

/* Minor: ledgerSeq counts every ledger event. */
static int t_ledger_seq(void)
{
    const char *T = "F7 ledgerSeq";
    sg_inputs in = base(20.9);
    long s0;
    newCtl();
    s0 = C.ledgerSeq;
    in.flow = 20; in.sp = 20;
    startPurge(&in, 20.9);                              /* purge event */
    CHECK(T, C.state == SG_PURGE && C.ledgerSeq == s0 + 1, "purge: seq %ld", C.ledgerSeq - s0);
    sg_op_flow_zero(&C);                                /* zero event */
    sg_mark_new_run(&C);
    sg_new_cylinder(&C, "operator");
    CHECK(T, C.ledgerSeq == s0 + 4, "seq +%ld, want +4", C.ledgerSeq - s0);
    return 0;
}

/* Minor: cylMinH / cylMaxH are the range of the window estimates, computed in cylForecast.
   Accelerating usage gives each window its own rate. */
static int t_cyl_minmax(void)
{
    const char *T = "F8 cylMinH/cylMaxH";
    sg_inputs in = base(0.5);
    double t, lo = INFINITY, hi = -INFINITY;
    int k, nh = 0;
    newCtl();
    CHECK(T, isnan(C.cylMinH) && isnan(C.cylMaxH), "not NaN at start");
    for (t = 60; t <= 40020; t++) {
        in.total = 0.1 * (t - 60) + 1e-6 * (t - 60) * (t - 60);
        tickO2(&in, 0.5, t);
    }
    for (k = 0; k < C.nCylEst; k++) {
        double h = C.cylEst[k].h;
        if (isnan(h)) continue;
        nh++;
        if (h < lo) lo = h;
        if (h > hi) hi = h;
    }
    CHECK(T, nh >= 2 && lo < hi, "windows %d lo %g hi %g", nh, lo, hi);
    CHECK(T, C.cylMinH == lo && C.cylMaxH == hi, "cylMinH %g cylMaxH %g, want %g %g", C.cylMinH,
          C.cylMaxH, lo, hi);
    return 0;
}

/* ================================================================ missed O2 updates (M1-M3)
   The user, 2026-10-01: "building our system to handle missed updates on a ~1Hz schedule is good
   for robustness". A held value is not new evidence. Found by the offline stress harness
   (ioc/test/stress_o2.py): holds of 10-29 s (< frozenTime) at the purge start stopped closed lids. */

/* the analyzer misses every update from t = 5 to t = 33 (29 repeats, < frozenTime 30) */
static int heldPastOnsetMax(long t) { return t >= 5 && t <= 33; }

/* M1 (spec §8.9 step 5.2): "no O2 decay within lidOnsetMax" is judged only on a fresh sample.
   At el 30 (t = 33) the reading is the t = 4 sample held for 29 s, so it says nothing about
   el 30: the check waits for the next update (t = 34), which shows the decay, and the lid check
   then passes. An open lid (no decay, fresh noisy readings) is still stopped, at that update. */
static int t_onset_fresh(void)
{
    const char *T = "M1 no-onset rule on fresh samples only";
    long td = lidRun(0, 1, 0, heldPastOnsetMax, 200);
    CHECK(T, countLogSub("no O2 decay within") == 0 && C.state == SG_PURGE,
          "closed lid stopped on a held reading: state %d", C.state);
    CHECK(T, C.sd.onsetT == 31 && C.sd.lidResult == SG_LID_PASSED && fabs(C.sd.kin - 1) < 1e-9,
          "onset el %g lidResult %d ratio %.12f (want onset 31, passed, 1)", C.sd.onsetT,
          C.sd.lidResult, C.sd.kin);
    CHECK(T, td == 64, "decided at t %ld (want 64: onset 34 + lidWindow 30)", td);
    td = lidRun(0, 0, 2e-4, heldPastOnsetMax, 200);      /* open lid: O2 stays at 20.9 % */
    CHECK(T, C.state == SG_OPEN_STOP && td == 34 &&
             alarmIs(SG_A_OPENSTOP, 2, "no O2 decay within 30 s of full flow: enclosure open?"),
          "open lid: state %d at t %ld (want OPEN_STOP at 34, the first update after el 30)", C.state, td);
    td = lidRun(0, 0, 2e-4, heldNone, 200);              /* and at 1 Hz, at el 30 as before */
    CHECK(T, C.state == SG_OPEN_STOP && td == 33, "open lid, 1 Hz: state %d at t %ld", C.state, td);
    return 0;
}

/* M2 (spec §8.9 step 5.3): a repeated onset sample is left out of the fit. The purge timer starts
   30 s after the entry (the flow stays below 95 %), and the analyzer misses its updates from
   t = 26 to 35, so the onset is first seen at t = 32 (timer start) on the t = 25 reading: a
   sample ~7 s old placed at t = 0. Kept, it bent the decay (curvature well below 0.8, closed lid
   judged open, as in the stress harness's sc13); left out, the fit sees the exact decay. */
static int heldAtTimerStart(long t) { return t >= 26 && t <= 35; }
static int t_onset_sample_stale(void)
{
    const char *T = "M2 repeated onset sample left out of the lid fit";
    sg_inputs in = base(20.9);
    double prev = 20.9;
    long t;
    newCtl();
    in.flow = 10; in.sp = 20;                            /* below 0.95 x purgeFlow: 30 s timer */
    tickAt(&in, 1); sg_op_purge(&C); tickAt(&in, 2);
    for (t = 3; t <= 200 && !C.sd.dropChecked && C.state == SG_PURGE; t++) {
        double v = 20.9 * exp(-KEXP * (double)(t - 3));
        if (heldAtTimerStart(t)) v = prev;
        prev = v; in.o2 = v;
        tickAt(&in, (double)t);
    }
    CHECK(T, C.state == SG_PURGE && C.sd.lidResult == SG_LID_PASSED,
          "closed lid judged open: state %d, '%s'", C.state, C.alarms[SG_A_OPENSTOP].msg);
    CHECK(T, C.sd.onsetT == 0 && C.sd.timerT0 == 32, "onset el %g timerT0 %g (want 0, 32)",
          C.sd.onsetT, C.sd.timerT0);
    CHECK(T, fabs(C.sd.kin - 1) < 1e-9 && fabs(C.sd.curv - 1) < 1e-9,
          "ratio %.12f curvature %.12f (want 1, 1)", C.sd.kin, C.sd.curv);
    return 0;
}

/* M3 (spec §8.14 Pinned and the minimum-flow note): epid's output deadband ODEL (0.01) can leave
   OVAL just inside the drive limit for good (the stress harness's sc07: OVAL 0.9936 against
   DRVH 1.0 while the PID asked for 1.0). The pinned tests allow ODEL: OVAL ≥ DRVH − max(ODEL,
   1e-6) and OVAL ≤ DRVL + max(ODEL, 1e-6). Coordinator approval 2026-10-01 under the user's rule
   that "can't act" must be loud. */
static int t_pinned_odel(void)
{
    const char *T = "M3 pinned within ODEL of the drive limit";
    const char *MSG = "PID pinned at max flow: check enclosure seal";
    const char *NOTE = "note: PID at minimum flow and O2 still below target";
    sg_inputs in = base(0.99);
    double t;
    newCtl();
    C.p.pinnedTime = 5;
    CHECK(T, regulateAtTarget(&in, 0.994) == 1 && !C.settling, "resume at target failed");
    CHECK(T, C.epid.DRVH == 1.0 && C.epid.OVAL == 0.994 && C.epid.ODEL == 0.01, "DRVH %g OVAL %g ODEL %g",
          C.epid.DRVH, C.epid.OVAL, C.epid.ODEL);
    for (t = 2; t <= 5; t++) tickO2(&in, 0.99, t);
    CHECK(T, !C.alarms[SG_A_PINNED].active, "Pinned before pinnedTime");
    tickO2(&in, 0.99, 6);
    CHECK(T, alarmIs(SG_A_PINNED, 2, MSG) && logTime(MSG) == 6, "Pinned not raised at pinnedTime");
    /* 0.015 below DRVH (more than ODEL): not pinned */
    newCtl();
    C.p.pinnedTime = 5;
    CHECK(T, regulateAtTarget(&in, 0.985) == 1, "resume (0.985) failed");
    for (t = 2; t <= 20; t++) tickO2(&in, 0.99, t);
    CHECK(T, !C.alarms[SG_A_PINNED].active, "Pinned at DRVH - 0.015");
    /* the minimum-flow note, within ODEL of DRVL (0.05) */
    newCtl();
    C.p.pinnedTime = 5;
    CHECK(T, regulateAtTarget(&in, 0.056) == 1, "resume (0.056) failed");
    for (t = 2; t <= 6; t++) tickO2(&in, 0.5, t);
    CHECK(T, countLog(0, NOTE) == 1 && logTime(NOTE) == 6, "minimum-flow note at DRVL + 0.006: %d",
          countLog(0, NOTE));
    return 0;
}

/* M4 (spec §8.2 step 6, §8.6): aboveCount counts fresh readings only. One spurious 15 % reading
   in REGULATE, held over 4 missed updates, reached lidFilter 5 and stopped a closed lid (its 10 s
   rise, 1.4 %/s, is far above lidSlope); 0-3 held ticks did not. Now no hold length stops it,
   up to frozenTime. */
static int t_above_fresh(void)
{
    const char *T = "M4 lid detector counts fresh readings only";
    int held;
    for (held = 0; held <= 25; held++) {
        sg_inputs in = base(0.99);
        double t;
        newCtl();
        CHECK(T, regulateAtTarget(&in, 0.3) == 1, "resume failed");
        for (t = 2; t <= 160; t++) {
            double v = 0.99 + wob(t);
            if (t >= 100 && t <= 100 + held) v = 15.0;    /* the glitch, then held */
            in.o2 = v; tickAt(&in, t);
        }
        CHECK(T, C.state == SG_REGULATE, "glitch held %d ticks: state %d (%s)", held, C.state,
              C.alarms[SG_A_OPENSTOP].msg);
    }
    return 0;
}

/* M5: a real lid lift (O2 rising 1.5 %/s in REGULATE, crossing lidLevel 10 % at t = 106.0) with
   15 % of the O2 updates missed at random still trips, at the lidFilter-th fresh reading above
   10 %: each missed update before it delays the trip by 1 s, nothing else does. Without misses
   the trip is at t = 111 (readings 107..111). The draw below (seed 1) misses 2 such updates:
   within lidFilter + 2 s of the crossing, as the coordinator asked; all 500 draws trip. */
static unsigned int lcg(unsigned int *s) { *s = *s * 1103515245u + 12345u; return (*s >> 8) & 0xffffu; }
static long liftRun(unsigned int seed, double missFrac, int *missesBefore)
{
    sg_inputs in = base(0.99);
    double prev = 0.99, t;
    unsigned int s = seed;
    newCtl();
    if (regulateAtTarget(&in, 0.3) != 1) return -2;
    *missesBefore = 0;
    for (t = 2; t <= 200; t++) {
        double v = (t < 100 ? 0.99 : fmin(20.9, 0.99 + 1.5 * (t - 100))) + wob(t);
        int miss = t >= 100 && lcg(&s) < missFrac * 65536;
        if (miss) v = prev;
        if (miss && t >= 107) (*missesBefore)++;     /* where a fresh reading is above 10 % */
        prev = v; in.o2 = v; tickAt(&in, t);
        if (C.state == SG_OPEN_STOP) return (long)t;
    }
    return -1;
}
static int t_lift_with_misses(void)
{
    const char *T = "M5 lid lift with 15 % missed O2 updates";
    int m, worst = 0;
    unsigned int seed;
    long td = liftRun(1, 0, &m);
    CHECK(T, td == 111 && m == 0, "no misses: tripped at t %ld (want 111)", td);
    CHECK(T, strstr(C.alarms[SG_A_OPENSTOP].msg, "enclosure opened, flow stopped") != NULL,
          "not the lid detector: '%s'", C.alarms[SG_A_OPENSTOP].msg);
    td = liftRun(1, 0.15, &m);
    CHECK(T, td > 0 && td <= 106 + 5 + 2, "seed 1: tripped at t %ld (want <= 113, %d misses)", td, m);
    for (seed = 1; seed <= 500; seed++) {
        td = liftRun(seed, 0.15, &m);
        CHECK(T, td == 111 + m, "seed %u: tripped at t %ld, want 111 + %d missed updates", seed, td, m);
        if (m > worst) worst = m;
    }
    CHECK(T, worst >= 3, "the draws never miss 3 updates in the window (%d): weak test", worst);
    return 0;
}

int main(void)
{
    static int (*const tests[])(void) = {
        t_onset_fresh, t_onset_sample_stale, t_pinned_odel, t_above_fresh, t_lift_with_misses,
        t_resume_idle, t_resume_idle_high, t_resume_invalid, t_resume_purge, t_set_mode,
        t_mark_new_run, t_mfc_disconnect, t_mfc_blip, t_resume_mfc, t_forecast_text, t_progress_text,
        t_no_decay, t_drop_skip_level,
        t_lid_exact_repeats, t_lid_noise_repeats, t_lid_10s_updates, t_lid_open_slow, t_lid_open_plateau,
        t_lid_wait, t_lid_defaults, t_purge_timeout, t_blind_purge, t_handoff_invalid, t_precheck_hold, t_hold_resume_mismatch,
        t_reconnect_mismatch, t_mfc_frozen, t_gas,
        t_flow_high, t_flow_low, t_not_reached, t_pinned_low, t_cyl_low, t_total_backwards,
        t_total_nan, t_ledger_disconnected, t_new_cylinder_disconnected, t_o2_nan, t_restart_mfc, t_restart_lid,
        t_command_nan, t_follow, t_follow_ramp, t_nocompute, t_write_fail,
        t_pid_bumpless, t_seed_scope, t_pid_bumpless_seed, t_channels_changed, t_channels_changed_midtick, t_helium_io, t_flow_steady, t_ledger_seq, t_cyl_minmax,
    };
    const int n = (int)(sizeof tests / sizeof tests[0]);
    int i, fails = 0;
    for (i = 0; i < n; i++) fails += tests[i]();
    printf("%s: %d/%d tests passed\n", fails ? "FAIL" : "PASS", n - fails, n);
    return fails ? 1 : 0;
}
