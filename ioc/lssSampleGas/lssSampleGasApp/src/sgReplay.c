/* sgReplay.c: replays reference traces (ioc/test/golden/sc<NN>.trace, made by
   ioc/test/ref/make_traces.js) through the C core and reports every difference.

   usage: sgReplay [--max-diffs N] [--ignore field,...] [--time-offset S] trace...

   The core's effects (log lines and Alicat puts) go into a FIFO; each L/A line of the trace pops
   the FIFO head and must match it. Before an executable line (R S I C T P) the FIFO must be empty.
   After a difference the replay resynchronises (substitution: pop both; missing: skip the trace
   line; extra: drop the record) so one difference does not cascade. X lines compare the core's
   state, H lines its usage report and run-out forecast (relative 1e-12, absolute floor 1e-9). At
   the end of each trace the scenario's SELFTEST row (reference lines 2225-2245) is applied to the
   core's own output, and for scenario 19 also the spec §8.17 check Dispensed = Σ runs (1 L).

   --time-offset S adds S (a multiple of 3600 s) to every time in the traces, so the core runs on
   epoch-like times (the IOC's `now`) while the reference ran from t = 0. */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "sgCore.h"
#include "sgEpidSim.h"
#include "sgFmt.h"

#define LINE_MAX_LEN 32768         /* an H line holds up to SG_RUNS runs */
#define MSG_LEN      1100
#define FIFO_CAP     1024

/* ------------------------------------------------------------------ known differences */
/* Reference text -> core text, applied (substring replacement) to trace L lines before comparing. */
static const struct { const char *ref, *core, *spec; } KNOWN_DIFFS[] = {
    { "IOC started via start_ioc (autosaved settings restored)",
      "IOC started (autosaved settings restored)", "§8.15" },
    { "operator pressed Resume PID", "operator pressed Resume Flow", "§8.5" },
};

/* ------------------------------------------------------------------ SELFTEST rows */
/* Copied from the reference (lines 2225-2245); rows 10 and 11 read "Resume Flow" (spec §14.2).
   `has` entries are the reference's regex texts; they are used as substring alternatives split
   on '|' with backslash escapes removed. check 17 / 19 = the reference's check functions. */
typedef struct { int n; double dur; const char *state; const char *has[3]; int check; } st_row;
static const st_row SELFTEST[] = {
    { 1,  3600,   "REGULATE",  { "lid check passed", "→ REGULATE" }, 0 },
    { 2,  600,    "OPEN_STOP", { "enclosure open\\?" }, 0 },
    { 3,  1200,   "OPEN_STOP", { "enclosure opened, flow stopped" }, 0 },
    { 4,  1800,   "REGULATE",  { "lid check skipped" }, 0 },
    { 5,  1800,   "REGULATE",  { "lid check passed" }, 0 },
    { 6,  14400,  "REGULATE",  { "flow ≥ 2× expected" }, 0 },
    { 7,  3600,   "REGULATE",  { "flow mismatch" }, 0 },
    { 8,  1200,   "REGULATE",  { "MFC was on hold, resumed" }, 0 },
    { 9,  1200,   "REGULATE",  { "MFC on hold, cannot resume" }, 0 },
    { 10, 2400,   "REGULATE",  { "O2 reading frozen", "operator pressed Resume Flow" }, 0 },
    { 11, 1200,   "REGULATE",  { "blind purge", "operator pressed Resume Flow" }, 0 },
    { 12, 1200,   "REGULATE",  { "ramp rate 0", "handoff minimum" }, 0 },
    { 13, 1200,   "REGULATE",  { "flow mismatch" }, 0 },
    { 14, 1200,   "REGULATE",  { "IOC crashed", "resume regulation" }, 0 },
    { 15, 3600,   "REGULATE",  { "PID pinned at max flow|O2 abnormally high" }, 0 },
    { 16, 10800,  "REGULATE",  { "settled: O2 reached 0.500" }, 0 },
    { 17, 172800, NULL,        { NULL }, 17 },
    { 18, 3600,   "REGULATE",  { "lid check passed" }, 0 },
    { 19, 777600, NULL,        { NULL }, 19 },
};
#define NROWS ((int)(sizeof SELFTEST / sizeof SELFTEST[0]))
#define MAXHAS 3
#define MAXALT 4

/* ------------------------------------------------------------------ X-line fields */
enum { XF_STATE, XF_LASTCMD, XF_OVAL, XF_FBON, XF_WORST, XF_OVERRIDE, XF_CYLLEFT, XF_CUML, XF_N };
static const char *const XF_NAMES[XF_N] = {
    "state", "lastCmd", "OVAL", "FBON", "worstSev", "overrideCount", "cylLeftL", "cumL"
};
static int ignoreField[XF_N];

/* ------------------------------------------------------------------ replay state */
typedef struct { char kind; int sev; char key[8]; double v; char msg[MSG_LEN]; } rec;
static rec fifo[FIFO_CAP];
static int fHead, fCount;

static sg_ctl ctl;                 /* large: static, not on the stack */
static sg_epid_sim esim;
static sg_report rep;
static int down;                   /* the reference's crash(): IOC not running */
static const char *tag;            /* "sc01" */
static long nDiffs;
static long maxDiffs = 10;
static double timeOffset;          /* --time-offset */

static const st_row *row;          /* SELFTEST row of the current trace, or NULL */
static char alt[MAXHAS][MAXALT][128];
static int nAlt[MAXHAS], hasFound[MAXHAS];

static void report(long line, const char *what, const char *got, const char *want)
{
    nDiffs++;
    if (nDiffs <= maxDiffs) printf("%s:%ld: %s got '%s' want '%s'\n", tag, line, what, got, want);
}

static void recText(const rec *r, char *buf, size_t n)
{
    char vb[64];
    if (r->kind == 'L') snprintf(buf, n, "L %d %s", r->sev, r->msg);
    else if (strcmp(r->key, "run") == 0) snprintf(buf, n, "A run");
    else snprintf(buf, n, "A %s %s", r->key, sg_fmtJs(vb, sizeof vb, r->v));
}

static rec *pushRec(void)
{
    rec *r;
    if (fCount == FIFO_CAP) {      /* should never happen: report the oldest as extra and drop it */
        char buf[MSG_LEN + 32];
        recText(&fifo[fHead], buf, sizeof buf);
        report(0, "extra output (FIFO full)", buf, "");
        fHead = (fHead + 1) % FIFO_CAP; fCount--;
    }
    r = &fifo[(fHead + fCount) % FIFO_CAP];
    fCount++;
    memset(r->key, 0, sizeof r->key);
    r->msg[0] = '\0'; r->v = 0; r->sev = 0;
    return r;
}

static void selftestSee(const char *msg)
{
    int i, j;
    if (!row) return;
    for (i = 0; i < MAXHAS; i++)
        for (j = 0; j < nAlt[i]; j++)
            if (strstr(msg, alt[i][j])) hasFound[i] = 1;
}

static void pushLog(int sev, const char *msg)
{
    rec *r = pushRec();
    r->kind = 'L'; r->sev = sev;
    snprintf(r->msg, sizeof r->msg, "%s", msg);
    selftestSee(msg);
}

static void ioLog(void *ctx, double t, int sev, const char *msg) { (void)ctx; (void)t; pushLog(sev, msg); }
static void ioPut(const char *key, double v)
{
    rec *r = pushRec();
    r->kind = 'A'; r->v = v;
    snprintf(r->key, sizeof r->key, "%s", key);
}
static void ioSp(void *ctx, double v) { (void)ctx; ioPut("sp", v); }
static void ioRamp(void *ctx, double v) { (void)ctx; ioPut("ramp", v); }
static void ioRun(void *ctx) { (void)ctx; ioPut("run", 0); }

static void flushExtra(long line)
{
    char buf[MSG_LEN + 32];
    while (fCount) {
        recText(&fifo[fHead], buf, sizeof buf);
        report(line, "extra output", buf, "");
        fHead = (fHead + 1) % FIFO_CAP; fCount--;
    }
}

/* ------------------------------------------------------------------ parsing helpers */
static double num(const char *s)
{
    char *end;
    double v;
    if (!s || strcmp(s, "null") == 0 || strcmp(s, "NaN") == 0 || strcmp(s, "undefined") == 0) return NAN;
    v = strtod(s, &end);
    return end == s ? NAN : v;
}

static int numEq(double core, double ref, double tol)
{
    if (isnan(ref)) return isnan(core);
    if (isnan(core)) return 0;
    if (isinf(ref) || isinf(core)) return core == ref;
    return fabs(core - ref) <= tol;
}

/* Split off up to `n` space-separated tokens; tok[n] = rest of the line (may be ""). */
static int splitTokens(char *s, char **tok, int n)
{
    int k = 0;
    while (k < n) {
        char *sp;
        tok[k++] = s;
        sp = strchr(s, ' ');
        if (!sp) { tok[k] = s + strlen(s); return k; }
        *sp = '\0'; s = sp + 1;
    }
    tok[n] = s;
    return n;
}

static void applyKnownDiffs(char *msg, size_t n)
{
    size_t i;
    for (i = 0; i < sizeof KNOWN_DIFFS / sizeof KNOWN_DIFFS[0]; i++) {
        char out[MSG_LEN];
        const char *ref = KNOWN_DIFFS[i].ref, *core = KNOWN_DIFFS[i].core, *src = msg, *hit;
        size_t len = 0, rl = strlen(ref), cl = strlen(core);
        if (!strstr(msg, ref)) continue;
        while ((hit = strstr(src, ref)) != NULL && len + (size_t)(hit - src) + cl < sizeof out) {
            memcpy(out + len, src, (size_t)(hit - src)); len += (size_t)(hit - src);
            memcpy(out + len, core, cl); len += cl;
            src = hit + rl;
        }
        snprintf(out + len, sizeof out - len, "%s", src);
        snprintf(msg, n, "%s", out);
    }
}

static int modeIndex(const char *s)
{
    if (s && s[0] >= 'A' && s[0] <= 'D' && s[1] == '\0') return s[0] - 'A';
    return -1;
}

static int eventType(const char *s)
{
    if (strcmp(s, "purge") == 0) return SG_EV_PURGE;
    if (strcmp(s, "zero") == 0) return SG_EV_ZERO;
    if (strcmp(s, "cylinder") == 0) return SG_EV_CYLINDER;
    if (strcmp(s, "newrun") == 0) return SG_EV_NEWRUN;
    return 0;
}

static const char *coreStateName(void)
{
    if (down) return "IOC_DOWN";
    return (ctl.state >= 0 && ctl.state < SG_NSTATES) ? sg_state_names[ctl.state] : "null";
}

static char *fmtOrNull(char *buf, size_t n, double v)
{
    if (isnan(v)) snprintf(buf, n, "null");
    else sg_fmtJs(buf, n, v);
    return buf;
}

/* ------------------------------------------------------------------ line handlers */
static void doL(long line, char *rest)
{
    char *tok[3];
    char msg[MSG_LEN], want[MSG_LEN + 32], got[MSG_LEN + 32];
    int sev;
    splitTokens(rest, tok, 2);          /* tok[0] = t, tok[1] = sev, tok[2] = message */
    sev = atoi(tok[1]);
    snprintf(msg, sizeof msg, "%s", tok[2]);
    applyKnownDiffs(msg, sizeof msg);
    snprintf(want, sizeof want, "L %d %s", sev, msg);
    if (!fCount) { report(line, "missing output", "", want); return; }
    {
        rec *r = &fifo[fHead];
        if (!(r->kind == 'L' && r->sev == sev && strcmp(r->msg, msg) == 0)) {
            recText(r, got, sizeof got);
            report(line, "output differs", got, want);
        }
        fHead = (fHead + 1) % FIFO_CAP; fCount--;
    }
}

static void doA(long line, char *rest)
{
    char *tok[3];
    char want[MSG_LEN], got[MSG_LEN + 32];
    const char *key;
    double v = 0;
    int isRun;
    splitTokens(rest, tok, 2);          /* tok[0] = t, tok[1] = sp|ramp|run, tok[2] = value */
    key = tok[1];
    isRun = strcmp(key, "run") == 0;
    if (!isRun) v = num(tok[2]);
    if (isRun) snprintf(want, sizeof want, "A run");
    else snprintf(want, sizeof want, "A %s %s", key, tok[2]);
    if (!fCount) { report(line, "missing output", "", want); return; }
    {
        rec *r = &fifo[fHead];
        int ok = r->kind == 'A' && strcmp(r->key, key) == 0 && (isRun || numEq(r->v, v, 1e-9));
        if (!ok) {
            recText(r, got, sizeof got);
            report(line, "output differs", got, want);
        }
        fHead = (fHead + 1) % FIFO_CAP; fCount--;
    }
}

static void doX(long line, char *rest)
{
    char *tok[XF_N + 2];
    double coreV[XF_N], refV[XF_N];
    char bad[256] = "", got[512], want[512], b[XF_N][64];
    int i, n;
    if (down) return;                   /* reference state is IOC_DOWN: nothing to compare */
    n = splitTokens(rest, tok, XF_N + 1);
    if (n < XF_N + 1) { report(line, "short X line", "", rest); return; }
    /* tok[0] = t; tok[1 + field] */
    coreV[XF_LASTCMD] = ctl.lastCmd;       coreV[XF_OVAL] = ctl.epid.OVAL;
    coreV[XF_FBON] = ctl.epid.FBON;        coreV[XF_WORST] = sg_worst_sev(&ctl);
    coreV[XF_OVERRIDE] = (double)ctl.overrideCount;
    coreV[XF_CYLLEFT] = ctl.cylLeftL;      coreV[XF_CUML] = ctl.cumL;
    for (i = XF_LASTCMD; i < XF_N; i++) refV[i] = num(tok[1 + i]);
    for (i = 0; i < XF_N; i++) {
        int ok;
        if (ignoreField[i]) continue;
        ok = (i == XF_STATE) ? strcmp(coreStateName(), tok[1]) == 0 : numEq(coreV[i], refV[i], 1e-9);
        if (!ok) {
            if (bad[0]) strncat(bad, ",", sizeof bad - strlen(bad) - 1);
            strncat(bad, XF_NAMES[i], sizeof bad - strlen(bad) - 1);
        }
    }
    if (!bad[0]) return;
    for (i = XF_LASTCMD; i < XF_N; i++) fmtOrNull(b[i], sizeof b[i], coreV[i]);
    snprintf(got, sizeof got, "%s %s %s %s %s %s %s %s", coreStateName(), b[1], b[2], b[3], b[4],
             b[5], b[6], b[7]);
    snprintf(want, sizeof want, "%s %s %s %s %s %s %s %s", tok[1], tok[2], tok[3], tok[4], tok[5],
             tok[6], tok[7], tok[8]);
    {
        char what[300];
        snprintf(what, sizeof what, "X [%s]", bad);
        report(line, what, got, want);
    }
}

/* H line: the reference's usageReport() and cylForecast() results against the core's. */
static const char *hTok(char **p)           /* next space-separated token, "" at the end */
{
    char *s = *p, *sp;
    if (!*s) return s;
    sp = strchr(s, ' ');
    if (sp) { *sp = '\0'; *p = sp + 1; } else *p = s + strlen(s);
    return s;
}

/* Relative 1e-12 with an absolute floor of 1e-9; NaN (null) matches only NaN. */
static int hEq(double core, double ref)
{
    if (isnan(ref) || isnan(core)) return isnan(ref) && isnan(core);
    return fabs(core - ref) <= fmax(1e-9, 1e-12 * fabs(ref));
}

static void doH(long line, char *rest)
{
    char *p = rest, what[96], got[64], want[64];
    double ref, lo = NAN, hi = NAN;
    int i, k, nRuns, nEst, bad = 0;
    if (down) return;
    sg_usage_report(&ctl, &rep);
    hTok(&p);                                           /* t */
#define HCMP(name, coreV, refV) do { double c_ = (coreV), r_ = (refV); \
        if (!bad && !hEq(c_, r_)) { bad = 1; snprintf(what, sizeof what, "H [%s]", name); \
            fmtOrNull(got, sizeof got, c_); fmtOrNull(want, sizeof want, r_); } } while (0)
#define HTIME() (num(hTok(&p)) + timeOffset)
    HCMP("wStart", rep.wStart, HTIME());
    HCMP("wEnd", rep.wEnd, HTIME());
    HCMP("dispensed", rep.dispensed, num(hTok(&p)));
    HCMP("inRuns", rep.inRuns, num(hTok(&p)));
    HCMP("cyls", rep.cyls, num(hTok(&p)));
    HCMP("cylEquiv", rep.cylEquiv, num(hTok(&p)));
    ref = num(hTok(&p));
    HCMP("nRuns", rep.nRuns, ref);
    nRuns = isnan(ref) ? 0 : (int)ref;
    for (i = 0; i < nRuns; i++) {
        const sg_run *r = i < rep.nRuns ? &rep.runs[i] : NULL;
        char nm[40];
#define RCMP(field, coreV, refV) do { snprintf(nm, sizeof nm, "run %d %s", i + 1, field); \
        HCMP(nm, r ? (coreV) : NAN, refV); } while (0)
        RCMP("start", r->start, HTIME());
        RCMP("end", r->end, HTIME());               /* null + offset stays NaN */
        RCMP("purges", r->purges, num(hTok(&p)));
        RCMP("L", r->L, num(hTok(&p)));
        RCMP("cylinders", r->cylinders, num(hTok(&p)));
        RCMP("finished", r->finished, num(hTok(&p)));
#undef RCMP
    }
    ref = num(hTok(&p));
    HCMP("nEst", ctl.nCylEst, ref);
    nEst = isnan(ref) ? 0 : (int)ref;
    for (k = 0; k < nEst; k++) {
        const sg_cylest *e = k < ctl.nCylEst && k < SG_NWIN ? &ctl.cylEst[k] : NULL;
        char nm[40];
        double h = num(hTok(&p)), rate = num(hTok(&p));
        snprintf(nm, sizeof nm, "est %d h", k);
        HCMP(nm, e ? e->h : NAN, h);
        snprintf(nm, sizeof nm, "est %d rate", k);
        HCMP(nm, e ? e->rate : NAN, rate);
        if (!isnan(h)) { if (isnan(lo) || h < lo) lo = h; if (isnan(hi) || h > hi) hi = h; }
    }
    HCMP("cylMedianH", ctl.cylMedianH, num(hTok(&p)));
    HCMP("cylMinH", ctl.cylMinH, lo);                  /* the core's range = the reference's */
    HCMP("cylMaxH", ctl.cylMaxH, hi);                  /* min / max of the window estimates */
    if (*p) { if (!bad) { bad = 1; snprintf(what, sizeof what, "H [trailing fields]"); got[0] = '\0';
              snprintf(want, sizeof want, "%.40s", p); } }
#undef HTIME
#undef HCMP
    if (bad) report(line, what, got, want);
}

static void doR(void)
{
    sg_reinit(&ctl, timeOffset);
    ctl.cylBase = NAN; ctl.nCyl = 0; ctl.nCylEst = 0; ctl.cylMedianH = NAN; ctl.cylLeftL = NAN;
    ctl.cylMinH = NAN; ctl.cylMaxH = NAN;
    ctl.cumL = 0; ctl.lastTotal = NAN; ctl.nLedger = 0; ctl.nSnaps = 0;
    sg_enter(&ctl, SG_IDLE, "station reset");
    sg_epid_sim_reset(&esim);
    down = 0;
}

static void doS(long line, char *rest)
{
    char *tok[3];
    int m;
    splitTokens(rest, tok, 2);
    m = modeIndex(tok[0]);
    if (m < 0) { report(line, "bad mode in S line", "", tok[0]); return; }
    ctl.p.mode = m;
    ctl.p.target = num(tok[1]);
}

static void doI(char *rest)
{
    char *tok[10];
    sg_inputs in;
    memset(&in, 0, sizeof in);
    splitTokens(rest, tok, 8);  /* t o2 sevr flow sp running ramp total | gas */
    in.o2 = num(tok[1]); in.o2Sevr = atoi(tok[2]);
    in.flow = num(tok[3]); in.sp = num(tok[4]); in.running = atoi(tok[5]) != 0;
    in.ramp = num(tok[6]); in.total = num(tok[7]);
    snprintf(in.gas, sizeof in.gas, "%s", tok[8]);
    in.mfcConnected = 1;
    in.writeEnabled = 1;                /* the reference always writes: the spec §8.14 setpoint-
                                           follow check runs, and must never come on */
    sg_set_inputs(&ctl, &in);
}

static void doC(long line, char *rest)
{
    char *tok[3];
    const char *name, *arg;
    double t;
    splitTokens(rest, tok, 2);          /* t name | arg */
    t = num(tok[0]) + timeOffset; name = tok[1]; arg = tok[2];
    if (strcmp(name, "restart") == 0) {
        down = 0;
        sg_restart(&ctl, t);
        sg_epid_sim_reset(&esim);
        return;
    }
    if (strcmp(name, "crash") == 0) {
        if (down) return;
        down = 1;
        pushLog(2, "IOC crashed: controller stopped, Alicat holds its last setpoint");
        return;
    }
    if (down) return;                   /* the controller is not running: calls do nothing */
    if (strcmp(name, "opPurge") == 0) sg_op_purge(&ctl);
    else if (strcmp(name, "opFlowZero") == 0) sg_op_flow_zero(&ctl);
    else if (strcmp(name, "opResume") == 0) sg_op_resume_flow(&ctl, NULL, 0);
    else if (strcmp(name, "opIdle") == 0) sg_op_idle(&ctl);
    else if (strcmp(name, "setTarget") == 0) sg_set_target(&ctl, num(arg), "operator");
    else if (strcmp(name, "newCylinder") == 0) sg_new_cylinder(&ctl, arg);
    else if (strcmp(name, "ledgerEvent") == 0) {
        int ty = eventType(arg);
        if (ty) sg_ledger_event(&ctl, ty);
        else report(line, "unknown ledger event", "", arg);
    } else report(line, "unknown command", "", name);
}

/* The spec §8.14 setpoint-follow check (not in the reference) runs in every trace: the longest run
   of ticks with Setpoint_RBV off lastCmd is reported, so the margin to its alarm is visible. */
static int followMax, followMaxAll, followJudged;

static void doT(char *rest)
{
    if (down) return;
    sg_tick(&ctl, num(rest) + timeOffset);
    if (ctl.followSec > followMax) followMax = ctl.followSec;
    if (ctl.in.writeEnabled && ctl.state != SG_IDLE && ctl.in.running && isfinite(ctl.lastCmd))
        followJudged++;
}

/* The glue's contract (sgCore.h): on FBON 0 -> 1 it writes PID:Out = cfg.OUTL (= lastCmd) before
   processing. The reference's integral starts from the Setpoint record's VAL (spVal) instead, so
   the replay passes spVal to keep the reference's results, and counts the bumpless starts where
   cfg.OUTL would have differed (reported per trace; not a failure). */
static long outlStarts, outlDiffers, outlStartsAll, outlDiffersAll;

static void doP(char *rest)
{
    char *tok[3];
    sg_epid_cfg cfg;
    double t, spVal;
    if (down) return;
    splitTokens(rest, tok, 1);
    t = num(tok[0]) + timeOffset; spVal = num(tok[1]);
    if (sg_pid_prepare(&ctl, &cfg)) {
        double oval;
        if (cfg.FBON && !esim.fbonPrev) {
            outlStarts++;
            if (!numEq(cfg.OUTL, spVal, 1e-9)) outlDiffers++;
        }
        oval = sg_epid_sim_process(&esim, &cfg, cfg.FBON, t, ctl.p.pidScan, spVal);
        sg_pid_done(&ctl, oval);
    }
}

/* ------------------------------------------------------------------ SELFTEST */
static void selftestSetup(int scenario)
{
    int i, j;
    row = NULL;
    memset(nAlt, 0, sizeof nAlt); memset(hasFound, 0, sizeof hasFound);
    for (i = 0; i < NROWS; i++) if (SELFTEST[i].n == scenario) row = &SELFTEST[i];
    if (!row) return;
    for (i = 0; i < MAXHAS && row->has[i]; i++) {
        const char *s = row->has[i];
        char *d = alt[i][0];
        size_t len = 0;
        nAlt[i] = 1;
        for (; *s; s++) {
            if (*s == '|' && nAlt[i] < MAXALT) { d[len] = '\0'; d = alt[i][nAlt[i]++]; len = 0; continue; }
            if (*s == '\\' && s[1]) s++;                 /* regex escape: \? -> ? */
            if (len < sizeof alt[0][0] - 1) d[len++] = *s;
        }
        d[len] = '\0';
        for (j = 0; j < nAlt[i]; j++) if (!alt[i][j][0]) nAlt[i] = 0;   /* never match "" */
    }
}

/* Returns 1 if the row passed (or there is no row); problems go to buf. */
static int selftestEval(char *buf, size_t n)
{
    size_t len = 0;
    int i;
    buf[0] = '\0';
    if (!row) return 1;
#define ADDP(...) do { if (len < n) { int w_ = snprintf(buf + len, n - len, "%s", len ? "; " : ""); \
                       if (w_ > 0) len += (size_t)w_; } \
                       if (len < n) { int w_ = snprintf(buf + len, n - len, __VA_ARGS__); \
                       if (w_ > 0) len += (size_t)w_; } } while (0)
    if (row->state && strcmp(coreStateName(), row->state) != 0)
        ADDP("ended %s, expected %s", coreStateName(), row->state);
    for (i = 0; i < MAXHAS && row->has[i]; i++)
        if (!hasFound[i]) ADDP("no log matching /%s/", row->has[i]);
    if (row->check == 17) {
        int k, cnt = 0;
        for (k = 0; k < ctl.nCylEst && k < SG_NWIN; k++) if (!isnan(ctl.cylEst[k].h)) cnt++;
        if (cnt < 3) ADDP("fewer than 3 forecast windows");
    } else if (row->check == 19) {
        double sum = 0;
        sg_usage_report(&ctl, &rep);
        if (!(rep.nRuns == 2 && rep.runs[0].purges == 23)) {
            if (rep.nRuns > 0) ADDP("runs %d, first %d purges", rep.nRuns, rep.runs[0].purges);
            else ADDP("runs %d, first undefined purges", rep.nRuns);
        }
        /* spec §8.17 validation (the reference meets it: |5195.53 - 5195.53| = 0) */
        for (i = 0; i < rep.nRuns; i++) sum += rep.runs[i].L;
        if (!(fabs(rep.dispensed - sum) <= 1))
            ADDP("Dispensed %.3f L != sum of runs %.3f L (tolerance 1 L)", rep.dispensed, sum);
    }
#undef ADDP
    return len == 0;
}

/* ------------------------------------------------------------------ one trace */
static int scenarioNumber(const char *path)
{
    const char *base = path, *p;
    for (p = path; *p; p++) if (*p == '/' || *p == '\\') base = p + 1;
    if (base[0] == 's' && base[1] == 'c') return atoi(base + 2);
    return -1;
}

static char *traceTag(const char *path)
{
    static char buf[64];
    const char *base = path, *p;
    char *dot;
    for (p = path; *p; p++) if (*p == '/' || *p == '\\') base = p + 1;
    snprintf(buf, sizeof buf, "%s", base);
    dot = strrchr(buf, '.');
    if (dot) *dot = '\0';
    return buf;
}

static int replayTrace(const char *path, long *diffsOut)
{
    static char line[LINE_MAX_LEN];
    char probs[1024];
    sg_io io;
    FILE *f;
    long ln = 0;
    int stOk;

    tag = traceTag(path);
    f = fopen(path, "r");
    if (!f) { printf("%s: cannot open %s\n", tag, path); *diffsOut = 1; return 0; }
    memset(&io, 0, sizeof io);
    io.log = ioLog; io.put_setpoint = ioSp; io.put_ramp = ioRamp; io.put_run = ioRun;
    row = NULL;
    sg_init(&ctl, &io, timeOffset);
    sg_epid_sim_reset(&esim);
    fHead = fCount = 0;                 /* drop sg_init's own "IOC started" line */
    down = 0; nDiffs = 0; outlStarts = 0; outlDiffers = 0; followMax = 0;
    selftestSetup(scenarioNumber(path));

    while (fgets(line, sizeof line, f)) {
        size_t len = strlen(line);
        char type, *rest;
        ln++;
        while (len && (line[len - 1] == '\n' || line[len - 1] == '\r')) line[--len] = '\0';
        if (!len) continue;
        type = line[0];
        rest = (len > 2 && line[1] == ' ') ? line + 2 : line + len;
        switch (type) {
        case 'L': doL(ln, rest); break;
        case 'A': doA(ln, rest); break;
        case 'X': doX(ln, rest); break;
        case 'H': doH(ln, rest); break;
        case 'R': flushExtra(ln); doR(); break;
        case 'S': flushExtra(ln); doS(ln, rest); break;
        case 'I': flushExtra(ln); doI(rest); break;
        case 'C': flushExtra(ln); doC(ln, rest); break;
        case 'T': flushExtra(ln); doT(rest); break;
        case 'P': flushExtra(ln); doP(rest); break;
        default: report(ln, "unknown line type", "", line); break;
        }
    }
    fclose(f);
    flushExtra(ln + 1);

    stOk = selftestEval(probs, sizeof probs);
    if (!stOk) printf("%s: selftest: %s\n", tag, probs);
    if (outlDiffers)
        printf("%s: note: cfg.OUTL (lastCmd) != Setpoint VAL at %ld of %ld bumpless starts\n", tag,
               outlDiffers, outlStarts);
    outlStartsAll += outlStarts; outlDiffersAll += outlDiffers;
    if (followMax > followMaxAll) followMaxAll = followMax;
    if (nDiffs == 0 && stOk) printf("%s: PASS (%ld lines)\n", tag, ln);
    else printf("%s: FAIL (%ld differences in %ld lines%s)\n", tag, nDiffs, ln,
                stOk ? "" : "; selftest failed");
    *diffsOut = nDiffs;
    return nDiffs == 0 && stOk;
}

/* ------------------------------------------------------------------ main */
static int parseIgnore(const char *list)
{
    char buf[512], *s, *next;
    snprintf(buf, sizeof buf, "%s", list);
    for (s = buf; s && *s; s = next) {
        int i, found = 0;
        next = strchr(s, ',');
        if (next) *next++ = '\0';
        if (!*s) continue;
        for (i = 0; i < XF_N; i++) if (strcmp(s, XF_NAMES[i]) == 0) { ignoreField[i] = 1; found = 1; }
        if (!found) {
            fprintf(stderr, "sgReplay: unknown --ignore field '%s' (fields:", s);
            for (i = 0; i < XF_N; i++) fprintf(stderr, " %s", XF_NAMES[i]);
            fprintf(stderr, ")\n");
            return 0;
        }
    }
    return 1;
}

static void usage(void)
{
    fprintf(stderr, "usage: sgReplay [--max-diffs N] [--ignore field,...] [--time-offset S] trace...\n");
}

int main(int argc, char **argv)
{
    int i, nTraces = 0, nPass = 0;
    long total = 0;
    for (i = 1; i < argc; i++) {            /* options first, wherever they appear */
        if (strcmp(argv[i], "--max-diffs") == 0 && i + 1 < argc) maxDiffs = atol(argv[++i]);
        else if (strcmp(argv[i], "--ignore") == 0 && i + 1 < argc) { if (!parseIgnore(argv[++i])) return 2; }
        else if (strcmp(argv[i], "--time-offset") == 0 && i + 1 < argc) {
            char *end;
            timeOffset = strtod(argv[++i], &end);
            if (*end || !isfinite(timeOffset) || fmod(timeOffset, 3600) != 0) {
                fprintf(stderr, "sgReplay: --time-offset must be a multiple of 3600 s, got '%s'\n", argv[i]);
                return 2;
            }
        }
        else if (strcmp(argv[i], "--help") == 0 || strcmp(argv[i], "-h") == 0) { usage(); return 0; }
        else if (strncmp(argv[i], "--", 2) == 0) { usage(); return 2; }
        else nTraces++;
    }
    if (!nTraces) { usage(); return 2; }
    if (timeOffset != 0) printf("sgReplay: all times shifted by %.0f s\n", timeOffset);
    for (i = 1; i < argc; i++) {
        long d = 0;
        if (strcmp(argv[i], "--max-diffs") == 0 || strcmp(argv[i], "--ignore") == 0 ||
            strcmp(argv[i], "--time-offset") == 0) { i++; continue; }
        nPass += replayTrace(argv[i], &d);
        total += d;
        fflush(stdout);
    }
    printf("sgReplay: %ld bumpless PID starts, cfg.OUTL != Setpoint VAL at %ld\n", outlStartsAll,
           outlDiffersAll);
    printf("sgReplay: setpoint-follow check judged %d ticks; longest run of Setpoint_RBV off "
           "lastCmd %d tick(s) (the alarm needs more than holdDetect + mismatchMargin)\n",
           followJudged, followMaxAll);
    printf("sgReplay: %d/%d traces passed, %ld differences in total\n", nPass, nTraces, total);
    return nPass == nTraces ? 0 : 1;
}
