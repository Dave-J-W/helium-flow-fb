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
    CHECK(T, countLog(0, "lid check skipped: purge started at 5.00 % (< 18 %)") == 1, "skip log");
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

int main(void)
{
    static int (*const tests[])(void) = {
        t_resume_idle, t_resume_idle_high, t_resume_invalid, t_resume_purge, t_set_mode,
        t_mark_new_run, t_mfc_disconnect, t_mfc_blip, t_resume_mfc, t_forecast_text, t_progress_text,
        t_no_decay, t_purge_timeout, t_blind_purge, t_handoff_invalid, t_precheck_hold, t_hold_resume_mismatch, t_gas,
        t_flow_high, t_flow_low, t_not_reached, t_pinned_low, t_cyl_low, t_total_backwards,
        t_total_nan, t_ledger_disconnected, t_new_cylinder_disconnected, t_o2_nan, t_restart_mfc, t_command_nan,
        t_pid_bumpless, t_pid_bumpless_seed, t_channels_changed, t_channels_changed_midtick, t_helium_io, t_flow_steady, t_ledger_seq, t_cyl_minmax,
    };
    const int n = (int)(sizeof tests / sizeof tests[0]);
    int i, fails = 0;
    for (i = 0; i < n; i++) fails += tests[i]();
    printf("%s: %d/%d tests passed\n", fails ? "FAIL" : "PASS", n - fails, n);
    return fails ? 1 : 0;
}
