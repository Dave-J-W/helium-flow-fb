/* sgIoc.c: the glue inside the IOC (Plan 2 task 3), between the SNL program sampleGas.st and the
   verified controller core (sgCore.h). One sg_station per `seq sampleGas` instance. Read the
   contract at the top of sgCore.h and the station section of sgIoc.h first.

   What lives here and what does not:
   - The station's OWN records ($(P)*) are read and written with dbAccess (dbGetField /
     dbPutField, dbProcess for the epid record). Every one of them is resolved once, at create,
     with dbNameToAddr; a missing record is a fatal start-up error naming the PV.
   - The REMOTE channels (Alicat, O2, cylinder) are the SNL's: it feeds their values and
     connection states in (sgIocInput*) and makes the Alicat puts this module hands out
     (sgIocNextAction). This module never talks to the Alicat, and no record links to it: the
     write gate (sgGate.c) is the only path, and it queues, it does not write.
   - Threading: every function runs on the station's own SNL thread. The only statics are const
     tables, plus the mutex-protected list of stations (and its mutex) that the diagnostic iocsh
     command sgIocShow prints; two stations in one IOC share no controller state.
   - Writes (fix round 1): READONLY must be exactly "0" for any write to be possible (fails
     closed); WRITE_MFC pins the one Alicat prefix writes may go to; FORCE_SHADOW=1 starts with
     Par:writeEnable 0 whatever autosave restored.

   One tick (spec §8.1; sgCore.h "Call order"), sgIocTick:
     1. inputs: the SNL's channel feed -> sg_inputs (the per-field contract of sgCore.h), the
        gate's connected flag (sg_gate_set_connected, same tick, P2-R6), sg_set_inputs
     2. parameters: Par:*, Mode:*, Mode (target and mode through sg_set_target / sg_set_mode on a
        real change only)
     3. first tick only: sg_restart (or IDLE "restart: not configured", spec §8.15 step 0); then
        Par:writeEnable (through sg_gate_set_enable, on a change only)
     4. commands, spec §8.1 order: FlowZero, Purge, ResumeFlow, ReleaseIdle, NewCylinder,
        MarkNewRun, ResetOverrideCount, Cfg:RestoreDefaults, Cfg:Apply; each reset to 0
     5. sg_tick (configured stations only: "runs no state machine", spec §8.21)
     6. PID step if due: sg_pid_prepare -> PID:Out = sg_pid_bumpless_i and ODEL 0 on the
        record's FBON 0 -> 1 (first output = lastCmd) -> epid fields, PID:CVAL -> process PID ->
        OVAL (NaN if the process failed) -> sg_pid_done (a non-finite OVAL: nothing commanded,
        the core's "cannot compute" Mismatch alarm, spec §8.3)
     7. PID:Out = lastCmd while FBON = 0 (spec §7.8, §8.3)
     8. publish: only what changed since the last tick (a last-published copy per PV); Alm:*
        before Sts:Banner / Sts:WorstSevr (spec §8.14); He:* arrays when the ledger or the usage
        log changed (and every minute); Log:Text last
     9. autosave manual_save of the helium set after NewCylinder / MarkNewRun (spec §10), after
        the He:* records hold the new values
   The SNL then drains sgIocNextAction (gated puts) and re-assigns channels if the names changed.
*/
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#ifdef _WIN32
#  include <direct.h>
#  define SG_MKDIR(d) _mkdir(d)
#else
#  include <sys/stat.h>
#  define SG_MKDIR(d) mkdir(d, 0775)
#endif

#define USE_TYPED_RSET              /* base 7: the untyped rset is deprecated (warnings) */
#include <dbDefs.h>
#include <dbAccess.h>
#include <dbLock.h>
#include <epicsMutex.h>
#include <epicsTime.h>
#include <errlog.h>
#include <iocsh.h>

#include "sgIoc.h"
#include "sgPvTable.h"
#include "sgFmt.h"

#include <epicsExport.h>

#define SG_NALM       SG_A_PINNEDLOW   /* Alm:* PVs: every alarm but the internal PINNEDLOW */
#define SG_MAXPAR     80
#define SG_MAXMODEF   8
#define SG_MAXBEHIND  60.0             /* s: catch up to this far behind the wall clock, then skip */
#define SG_APPLYWAIT  5.0              /* s: §8.21 step 6, wait for the new channels */
#define SG_STALETICKS 3                /* see chanUpdate */
#define SG_PUTTXT     16384            /* Log:Text NELM, the largest text */

/* ================================================================ record tables */
enum { PK_NUM, PK_STR, PK_TXT, PK_ARR };

/* Records the glue writes (and a few it also reads at start). PK_TXT = a char array put: lsi
   records through ".VAL$" (their SIZV), CHAR waveforms directly. */
#define SG_PUBS(X) \
    X(STS_STATE,      "Sts:State",              PK_NUM) \
    X(STS_STATEDESC,  "Sts:StateDesc.VAL$",     PK_TXT) \
    X(STS_LASTACTION, "Sts:LastAction.VAL$",    PK_TXT) \
    X(STS_PROGRESS,   "Sts:Progress.VAL$",      PK_TXT) \
    X(STS_INRANGE,    "Sts:InRange",            PK_NUM) \
    X(STS_O2,         "Sts:O2",                 PK_NUM) \
    X(STS_O2VALID,    "Sts:O2Valid",            PK_NUM) \
    X(STS_EXPFLOW,    "Sts:ExpectedFlow",       PK_NUM) \
    X(STS_LASTCMD,    "Sts:LastCmd",            PK_NUM) \
    X(STS_FLOW,       "Sts:Flow",               PK_NUM) \
    X(STS_SPRBV,      "Sts:SetpointRBV",        PK_NUM) \
    X(STS_MFCRUN,     "Sts:MfcRunning",         PK_NUM) \
    X(STS_MFCSTATUS,  "Sts:MfcStatus",          PK_STR) \
    X(STS_HEARTBEAT,  "Sts:Heartbeat",          PK_NUM) \
    X(STS_BANNER,     "Sts:Banner.VAL$",        PK_TXT) \
    X(STS_WORST,      "Sts:WorstSevr",          PK_NUM) \
    X(STS_WRITEEN,    "Sts:WriteEnable",        PK_NUM) \
    X(STS_CYLP,       "Sts:CylPressure",        PK_NUM) \
    X(HE_LEFTL,       "He:LeftL",               PK_NUM) \
    X(HE_EMPTYMIN,    "He:EmptyMinH",           PK_NUM) \
    X(HE_EMPTYMAX,    "He:EmptyMaxH",           PK_NUM) \
    X(HE_EMPTYMED,    "He:EmptyMedianH",        PK_NUM) \
    X(HE_FCTEXT,      "He:ForecastText.VAL$",   PK_TXT) \
    X(HE_CUML,        "He:CumL",                PK_NUM) \
    X(HE_LASTTOTAL,   "He:LastTotal",           PK_NUM) \
    X(HE_CYLBASE,     "He:CylBase",             PK_NUM) \
    X(HE_HISTT,       "He:HistT",               PK_ARR) \
    X(HE_HISTUSED,    "He:HistUsed",            PK_ARR) \
    X(HE_HISTN,       "He:HistN",               PK_NUM) \
    X(HE_EVT,         "He:EvT",                 PK_ARR) \
    X(HE_EVTYPE,      "He:EvType",              PK_ARR) \
    X(HE_EVL,         "He:EvL",                 PK_ARR) \
    X(HE_EVN,         "He:EvN",                 PK_NUM) \
    X(HE_SNAPT,       "He:SnapT",               PK_ARR) \
    X(HE_SNAPL,       "He:SnapL",               PK_ARR) \
    X(HE_SNAPN,       "He:SnapN",               PK_NUM) \
    X(HE_EST3D,       "He:Est3dH",              PK_NUM) \
    X(HE_RATE3D,      "He:Rate3d",              PK_NUM) \
    X(HE_EST2D,       "He:Est2dH",              PK_NUM) \
    X(HE_RATE2D,      "He:Rate2d",              PK_NUM) \
    X(HE_EST1D,       "He:Est1dH",              PK_NUM) \
    X(HE_RATE1D,      "He:Rate1d",              PK_NUM) \
    X(HE_EST12H,      "He:Est12hH",             PK_NUM) \
    X(HE_RATE12H,     "He:Rate12h",             PK_NUM) \
    X(HE_EST6H,       "He:Est6hH",              PK_NUM) \
    X(HE_RATE6H,      "He:Rate6h",              PK_NUM) \
    X(REP_DISP,       "He:Rep:Dispensed",       PK_NUM) \
    X(REP_INRUNS,     "He:Rep:InRuns",          PK_NUM) \
    X(REP_CYLS,       "He:Rep:Cylinders",       PK_NUM) \
    X(REP_CYLEQ,      "He:Rep:CylEquiv",        PK_NUM) \
    X(REP_WSTART,     "He:Rep:WinStart",        PK_NUM) \
    X(REP_WEND,       "He:Rep:WinEnd",          PK_NUM) \
    X(REP_RUNSTART,   "He:Rep:RunStart",        PK_ARR) \
    X(REP_RUNEND,     "He:Rep:RunEnd",          PK_ARR) \
    X(REP_RUNPURGES,  "He:Rep:RunPurges",       PK_ARR) \
    X(REP_RUNL,       "He:Rep:RunL",            PK_ARR) \
    X(REP_RUNCYL,     "He:Rep:RunCyl",          PK_ARR) \
    X(REP_RUNFIN,     "He:Rep:RunFinished",     PK_ARR) \
    X(REP_RUNN,       "He:Rep:RunN",            PK_NUM) \
    X(REP_TEXT,       "He:Rep:Text",            PK_TXT) \
    X(DG_ABOVE,       "Diag:AboveCount",        PK_NUM) \
    X(DG_MAXRATE,     "Diag:MaxRate",           PK_NUM) \
    X(DG_LIDARMED,    "Diag:LidArmed",          PK_NUM) \
    X(DG_HOLDSEC,     "Diag:HoldSec",           PK_NUM) \
    X(DG_HOLDATT,     "Diag:HoldAttempts",      PK_NUM) \
    X(DG_SETTLING,    "Diag:Settling",          PK_NUM) \
    X(DG_SETTLEDIR,   "Diag:SettleDir",         PK_NUM) \
    X(DG_SETTLET0,    "Diag:SettleT0",          PK_NUM) \
    X(DG_TOWARD,      "Diag:TowardRate",        PK_NUM) \
    X(DG_SLOPE,       "Diag:O2Slope",           PK_NUM) \
    X(DG_STALLSEC,    "Diag:StallSec",          PK_NUM) \
    X(DG_STALLLAT,    "Diag:StallLatched",      PK_NUM) \
    X(DG_FLOWSTEADY,  "Diag:FlowSteady",        PK_NUM) \
    X(DG_INFINE,      "Diag:InFine",            PK_NUM) \
    X(DG_PINNEDSEC,   "Diag:PinnedSec",         PK_NUM) \
    X(DG_PELAPSED,    "Diag:PurgeElapsed",      PK_NUM) \
    X(DG_PTIMEOUT,    "Diag:PurgeTimeout",      PK_NUM) \
    X(DG_O2START,     "Diag:O2Start",           PK_NUM) \
    X(DG_ONSET,       "Diag:LidOnsetT",         PK_NUM) \
    X(DG_LIDRATIO,    "Diag:LidRatio",          PK_NUM) \
    X(DG_LIDCURV,     "Diag:LidCurv",           PK_NUM) \
    X(DG_LIDRESULT,   "Diag:LidResult",         PK_NUM) \
    X(DG_ESTO2,       "Diag:EstO2",             PK_NUM) \
    X(DG_BLIND,       "Diag:Blind",             PK_NUM) \
    X(DG_HPHASE,      "Diag:HandoffPhase",      PK_NUM) \
    X(DG_OVRCOUNT,    "Diag:OverrideCount",     PK_NUM) \
    X(DG_OVRLOG,      "Diag:OverrideLog.VAL$",  PK_TXT) \
    X(LOG_TEXT,       "Log:Text",               PK_TXT) \
    X(CFG_ACT_MFC,    "Cfg:Active:MFC",         PK_STR) \
    X(CFG_ACT_O2,     "Cfg:Active:O2",          PK_STR) \
    X(CFG_ACT_CYL,    "Cfg:Active:CYL",         PK_STR) \
    X(CFG_ACT_STN,    "Cfg:Active:STN",         PK_STR) \
    X(CFG_PENDING,    "Cfg:Pending",            PK_NUM) \
    X(CFG_CONN_MFC,   "Cfg:Conn:MFC",           PK_NUM) \
    X(CFG_CONN_O2,    "Cfg:Conn:O2",            PK_NUM) \
    X(CFG_CONN_CYL,   "Cfg:Conn:CYL",           PK_NUM) \
    X(CFG_STATUS,     "Cfg:Status.VAL$",        PK_TXT) \
    X(PID_OUT,        "PID:Out",                PK_NUM)

/* Records (and fields) the glue only reads, or writes outside the publish cache. */
#define SG_INS(X) \
    X(IN_WRITEEN,     "Par:writeEnable") \
    X(IN_MODE,        "Mode") \
    X(IN_MODE_ZRST,   "Mode.ZRST") \
    X(IN_MODE_ONST,   "Mode.ONST") \
    X(IN_MODE_TWST,   "Mode.TWST") \
    X(IN_MODE_THST,   "Mode.THST") \
    X(IN_CMD_FZERO,   "Cmd:FlowZero") \
    X(IN_CMD_PURGE,   "Cmd:Purge") \
    X(IN_CMD_RESUME,  "Cmd:ResumeFlow") \
    X(IN_CMD_IDLE,    "Cmd:ReleaseIdle") \
    X(IN_CMD_NEWCYL,  "Cmd:NewCylinder") \
    X(IN_CMD_NEWRUN,  "Cmd:MarkNewRun") \
    X(IN_CMD_RSTOVR,  "Cmd:ResetOverrideCount") \
    X(IN_CFG_RESTORE, "Cfg:RestoreDefaults") \
    X(IN_CFG_APPLY,   "Cfg:Apply") \
    X(IN_CFG_MFC,     "Cfg:MFC") \
    X(IN_CFG_O2,      "Cfg:O2") \
    X(IN_CFG_CYL,     "Cfg:CYL") \
    X(IN_CFG_STN,     "Cfg:STN") \
    X(IN_DEF_MFC,     "Cfg:Default:MFC") \
    X(IN_DEF_O2,      "Cfg:Default:O2") \
    X(IN_DEF_CYL,     "Cfg:Default:CYL") \
    X(IN_DEF_STN,     "Cfg:Default:STN") \
    X(IN_PID_VAL,     "PID.VAL") \
    X(IN_PID_KP,      "PID.KP") \
    X(IN_PID_KI,      "PID.KI") \
    X(IN_PID_DRVL,    "PID.DRVL") \
    X(IN_PID_DRVH,    "PID.DRVH") \
    X(IN_PID_ODEL,    "PID.ODEL") \
    X(IN_PID_FBON,    "PID.FBON") \
    X(IN_PID_FBOP,    "PID.FBOP") \
    X(IN_PID_OVAL,    "PID.OVAL") \
    X(IN_PID_CVAL,    "PID:CVAL")

#define PUB_ENUM(id, name, kind) id,
#define PUB_DEF(id, name, kind) { name, kind },
#define IN_ENUM(id, name) id,
#define IN_DEF(id, name) name,
enum { SG_PUBS(PUB_ENUM) P_N };
enum { SG_INS(IN_ENUM) IN_N };
static const struct { const char *suffix; int kind; } pubDefs[P_N] = { SG_PUBS(PUB_DEF) };
static const char *const inDefs[IN_N] = { SG_INS(IN_DEF) };

/* enum sg_alarm order (sgCore.h, spec §7.5) */
static const char *const almNames[SG_NALM] = {
    "OpenStop", "PurgeIncomplete", "O2Bad", "OpenLoop", "Override", "HoldStuck", "Mismatch",
    "FlowHigh", "FlowLow", "Pinned", "O2High", "NotReached", "CylLow", "Gas", "Units", "Shadow"
};
static const char *const slotLetter[4] = { "A", "B", "C", "D" };

/* Remote channel suffixes (spec §6.1); NULL = the full name is a Cfg field (O2, CYL). */
static const char *const sfxD[SG_NCD] = { NULL, "Flow_RBV", "Setpoint_RBV", "RampRate_RBV",
                                          "Total_RBV", "Running_RBV", NULL };
static const char *const sfxS[SG_NCS] = { "Gas_RBV", "Status", "FlowUnits_RBV" };
static const char *const sfxO[SG_NCO] = { "Setpoint", "RampRate", "Run" };

/* ================================================================ station */
typedef struct {
    DBADDR a; int kind, have, failed;
    double d;                           /* PK_NUM: last published */
    char s[MAX_STRING_SIZE];            /* PK_STR: last published */
    char *t; double *arr; long n, cap;  /* PK_TXT / PK_ARR: last published, capacity */
} pubSlot;

typedef struct {
    char name[64];
    /* this tick's feed from the SNL */
    int assigned, conn, sevr; unsigned tsSec, tsNsec; double v; char s[MAX_STRING_SIZE];
    /* derived */
    int ok;                             /* connected and holding a value of this assignment */
    int have; double lastV; char lastS[MAX_STRING_SIZE];   /* last received value */
    int stale, staleTicks; unsigned stSec, stNsec;         /* after a re-assignment */
    int reassign;
} sg_chan;

struct sg_station {
    char P[64], heSet[128];
    sg_ctl c;
    sg_gate g;
    sg_logfile L;
    sg_names active;
    int configured, readOnly, forceShadow, trial;
    int waitAlarm, ncAlarm;             /* sgStart.c latches: start-up wait, not configured */
    char writeMfc[40];                  /* WRITE_MFC: the only Alicat prefix writes may go to */
    sg_chan ch[SG_NGRP][SG_NCD];        /* [grp][idx]; SG_NCD is the largest group */
    int namesChanged;
    /* clock */
    double tickNow, nextNow; int scheduled, clockBack, inTick;
    int started; long heartbeat;
    int liveInWait, releaseInWait;      /* D2, D8: remembered during the start-up wait */
    int caConnected;                    /* the Alicat's CA state this tick (the gate's flag) */
    sg_stale flowStale;                 /* G3: Flow_RBV time-stamp clock */
    /* addresses */
    pubSlot pub[P_N], alm[SG_NALM], almMsg[SG_NALM];
    DBADDR in[IN_N];
    DBADDR par[SG_MAXPAR]; int targetIdx;
    DBADDR modeF[4][SG_MAXMODEF], modeName[4];
    int nResolved, nMissing;
    /* state kept between ticks */
    int putFail[SG_NCO], logDirty;     /* putFail: last pvStat per put channel (sg_put_done) */
    int applyPending; double applyT;
    int heSave, havePubHe; long pubLedgerSeq;
    int mfcApplied, heRebase;           /* sg_cfg_he_save: Cfg:MFC changed this tick; 2nd save due */
    /* big buffers (never on the SNL thread's stack) */
    sg_helium he;
    sg_report rep;
    char txt[SG_PUTTXT], banner[4096], progress[1024];
};

static const int grpSize[SG_NGRP] = { SG_NCD, SG_NCS, SG_NCO };

static void registerStation(sg_station *s);    /* sgIocShow's list (diagnostics only) */

/* Writes are possible at all only if the IOC is not read-only and, when WRITE_MFC is set, the
   applied Cfg:MFC is exactly that prefix (fix round 1, finding 1: Cfg:MFC is editable and
   autosaved, so without this pin a re-enabled write would go to whatever prefix was entered).
   Otherwise the put channels get no name and Par:writeEnable 1 is refused. */
static int writesPermitted(const sg_station *s)
{
    if (s->readOnly) return 0;
    return s->writeMfc[0] == '\0' || strcmp(s->active.mfc, s->writeMfc) == 0;
}

static void logWriteRefusal(sg_station *s)
{
    if (s->readOnly) sg_log(&s->c, 2, "read-only IOC: writes cannot be enabled");
    else sg_log(&s->c, 2, "writes refused: Alicat %s is not the permitted %s",
                s->active.mfc[0] ? s->active.mfc : "(none)", s->writeMfc);
}

/* ================================================================ small helpers */
static double wallNow(void)
{
    epicsTimeStamp ts;
    epicsTimeGetCurrent(&ts);
    return (double)ts.secPastEpoch + POSIX_TIME_AT_EPICS_EPOCH + ts.nsec * 1e-9;
}

static const char *stateText(int st)
{
    return (st >= 0 && st < SG_NSTATES) ? sg_state_names[st] : "\xe2\x80\x94";
}

static void localStamp(char *buf, size_t n, double t)      /* YYYY-MM-DD hh:mm:ss */
{
    time_t tt = (time_t)t;
    struct tm tmv;
    if (epicsTime_localtime(&tt, &tmv) != epicsTimeOK) { snprintf(buf, n, "?"); return; }
    snprintf(buf, n, "%04d-%02d-%02d %02d:%02d:%02d", tmv.tm_year + 1900, tmv.tm_mon + 1,
             tmv.tm_mday, tmv.tm_hour, tmv.tm_min, tmv.tm_sec);
}

static void copyStr(char *dst, size_t n, const char *src)
{
    size_t k = strlen(src);
    if (n == 0) return;
    if (k >= n) k = n - 1;
    memcpy(dst, src, k);
    dst[k] = '\0';
}

static int sameD(double a, double b) { return (isnan(a) && isnan(b)) || a == b; }

/* ---------------------------------------------------------------- dbAccess reads */
static double getD(DBADDR *a)
{
    double v;
    if (!a->precord || dbGetField(a, DBR_DOUBLE, &v, NULL, NULL, NULL)) return NAN;
    return v;
}

static void getS(DBADDR *a, char buf[MAX_STRING_SIZE])
{
    buf[0] = '\0';
    if (!a->precord || dbGetField(a, DBR_STRING, buf, NULL, NULL, NULL)) buf[0] = '\0';
    buf[MAX_STRING_SIZE - 1] = '\0';
}

static long getArr(DBADDR *a, double *buf, long max)
{
    long n = max;
    if (!a->precord || dbGetField(a, DBR_DOUBLE, buf, NULL, &n, NULL)) return 0;
    return n < 0 ? 0 : (n > max ? max : n);
}

/* ---------------------------------------------------------------- publishing */
static void putFailed(pubSlot *p, long status)
{
    if (!p->failed)
        errlogPrintf("sampleGas: put to %s failed (status %ld)\n", p->a.precord->name, status);
    p->failed = 1;
    p->have = 0;
}

static void pubNum(pubSlot *p, double v)
{
    long st;
    if (p->have && sameD(p->d, v)) return;
    st = dbPutField(&p->a, DBR_DOUBLE, &v, 1);
    if (st) { putFailed(p, st); return; }
    p->d = v; p->have = 1; p->failed = 0;
}

static void pubStr(pubSlot *p, const char *v)
{
    char b[MAX_STRING_SIZE];
    long st;
    copyStr(b, sizeof b, v);
    if (p->have && strcmp(p->s, b) == 0) return;
    st = dbPutField(&p->a, DBR_STRING, b, 1);
    if (st) { putFailed(p, st); return; }
    memcpy(p->s, b, sizeof b); p->have = 1; p->failed = 0;
}

/* Text into a char array (lsi .VAL$, CHAR waveform), truncated to the record's size on a UTF-8
   character boundary; NUL included in the put. */
static void pubTxt(pubSlot *p, const char *v)
{
    size_t n = strlen(v);
    long st;
    if (p->cap < 1) return;
    if (n > (size_t)p->cap - 1) {
        n = (size_t)p->cap - 1;
        while (n > 0 && ((unsigned char)v[n] & 0xC0) == 0x80) n--;
    }
    if (p->have && strlen(p->t) == n && memcmp(p->t, v, n) == 0) return;
    memcpy(p->t, v, n);
    p->t[n] = '\0';
    st = dbPutField(&p->a, DBR_CHAR, p->t, (long)n + 1);
    if (st) { putFailed(p, st); return; }
    p->have = 1; p->failed = 0;
}

static void pubArr(pubSlot *p, const double *v, long n)
{
    long st;
    if (n < 0) n = 0;
    if (n > p->cap) n = p->cap;
    if (p->have && p->n == n && memcmp(p->arr, v, (size_t)n * sizeof *v) == 0) return;
    st = dbPutField(&p->a, DBR_DOUBLE, v, n);
    if (st) { putFailed(p, st); return; }
    memcpy(p->arr, v, (size_t)n * sizeof *v);
    p->n = n; p->have = 1; p->failed = 0;
}

static void putD(DBADDR *a, double v)          /* uncached write (commands, PID) */
{
    long st = dbPutField(a, DBR_DOUBLE, &v, 1);
    if (st) errlogPrintf("sampleGas: put to %s failed (status %ld)\n", a->precord->name, st);
}

/* ================================================================ core effects */
/* Log time stamps: inside a tick, the tick's `now` (also for the operator calls, which the core
   runs before sg_tick, i.e. while c->now is still the previous tick's); outside a tick (create,
   the connection wait) the wall clock. Never c->now alone: an unconfigured station never runs
   sg_tick, so its c->now does not advance. */
static void stLog(void *ctx, double t, int sev, const char *msg)
{
    sg_station *s = ctx;
    (void)t;
    sg_logfile_line(&s->L, s->inTick ? s->tickNow : floor(wallNow()), sev, msg);
    s->logDirty = 1;
}
static void stSp(void *ctx, double v)   { sg_station *s = ctx; sg_gate_request(&s->g, &s->c, SG_ACT_SP, v); }
static void stRamp(void *ctx, double v) { sg_station *s = ctx; sg_gate_request(&s->g, &s->c, SG_ACT_RAMP, v); }
static void stRun(void *ctx)            { sg_station *s = ctx; sg_gate_request(&s->g, &s->c, SG_ACT_RUN, 1); }

/* ================================================================ resolution */
static int resolve(sg_station *s, DBADDR *a, const char *suffix)
{
    char name[192];
    snprintf(name, sizeof name, "%s%s", s->P, suffix);
    if (dbNameToAddr(name, a)) {
        errlogPrintf("sampleGas: FATAL: record %s not found\n", name);
        memset(a, 0, sizeof *a);
        s->nMissing++;
        return 1;
    }
    s->nResolved++;
    return 0;
}

static void resolvePub(sg_station *s, pubSlot *p, const char *suffix, int kind)
{
    p->kind = kind;
    if (resolve(s, &p->a, suffix)) return;
    if (kind == PK_TXT || kind == PK_ARR) {
        long ne = p->a.no_elements;
        if (ne < 2 || (kind == PK_TXT && p->a.dbr_field_type != DBR_CHAR)) {
            errlogPrintf("sampleGas: FATAL: %s%s is not a %s array\n", s->P, suffix,
                         kind == PK_TXT ? "char" : "numeric");
            s->nMissing++;
            return;
        }
        p->cap = ne;
        if (kind == PK_TXT) p->t = calloc((size_t)ne + 1, 1);
        else p->arr = calloc((size_t)ne, sizeof(double));
        if ((kind == PK_TXT && !p->t) || (kind == PK_ARR && !p->arr)) {
            errlogPrintf("sampleGas: FATAL: out of memory for %s%s\n", s->P, suffix);
            s->nMissing++;
        }
    }
}

static int resolveAll(sg_station *s)
{
    char sfx[96];
    int i, x, j;
    s->nResolved = s->nMissing = 0;
    for (i = 0; i < P_N; i++) resolvePub(s, &s->pub[i], pubDefs[i].suffix, pubDefs[i].kind);
    for (i = 0; i < IN_N; i++) resolve(s, &s->in[i], inDefs[i]);
    for (i = 0; i < SG_NALM; i++) {
        snprintf(sfx, sizeof sfx, "Alm:%s", almNames[i]);
        resolvePub(s, &s->alm[i], sfx, PK_NUM);
        snprintf(sfx, sizeof sfx, "Alm:%s:Msg.VAL$", almNames[i]);
        resolvePub(s, &s->almMsg[i], sfx, PK_TXT);
    }
    if (sgNParPvs > SG_MAXPAR || sgNModePvs > SG_MAXMODEF) {
        errlogPrintf("sampleGas: FATAL: PV table larger than the glue's tables\n");
        return 1;
    }
    s->targetIdx = -1;
    for (i = 0; i < sgNParPvs; i++) {
        resolve(s, &s->par[i], sgParPvs[i].suffix);
        if (strcmp(sgParPvs[i].suffix, "Par:target") == 0) s->targetIdx = i;
    }
    for (x = 0; x < 4; x++) {
        snprintf(sfx, sizeof sfx, "Mode:%s:name", slotLetter[x]);
        resolve(s, &s->modeName[x], sfx);
        for (j = 0; j < sgNModePvs; j++) {
            snprintf(sfx, sizeof sfx, "Mode:%s:%s", slotLetter[x], sgModePvs[j].suffix);
            resolve(s, &s->modeF[x][j], sfx);
        }
    }
    return s->nMissing;
}

/* Distinct records among the resolved addresses (for sgIocCheck). */
static int countRecords(sg_station *s)
{
    enum { MAXA = P_N + IN_N + 2 * SG_NALM + SG_MAXPAR + 4 * (SG_MAXMODEF + 1) };
    const void *recs[MAXA];
    int n = 0, i, x, j;
#define ADDREC(ad) do { const void *r_ = (ad).precord; int k_;                          \
        if (r_) { for (k_ = 0; k_ < n && recs[k_] != r_; k_++) {} if (k_ == n) recs[n++] = r_; } \
    } while (0)
    for (i = 0; i < P_N; i++) ADDREC(s->pub[i].a);
    for (i = 0; i < IN_N; i++) ADDREC(s->in[i]);
    for (i = 0; i < SG_NALM; i++) { ADDREC(s->alm[i].a); ADDREC(s->almMsg[i].a); }
    for (i = 0; i < sgNParPvs; i++) ADDREC(s->par[i]);
    for (x = 0; x < 4; x++) {
        ADDREC(s->modeName[x]);
        for (j = 0; j < sgNModePvs; j++) ADDREC(s->modeF[x][j]);
    }
#undef ADDREC
    return n;
}

static void freeStation(sg_station *s)
{
    int i;
    if (!s) return;
    for (i = 0; i < P_N; i++) { free(s->pub[i].t); free(s->pub[i].arr); }
    for (i = 0; i < SG_NALM; i++) { free(s->almMsg[i].t); }
    free(s);
}

/* ================================================================ names and channels */
static void notConfiguredText(const sg_names *a, char *buf, size_t n)
{
    snprintf(buf, n, "not configured: Cfg:%s is empty", a->mfc[0] == '\0' ? "MFC" : "O2");
}

static void readEditNames(sg_station *s, sg_names *e)
{
    char b[MAX_STRING_SIZE];
    getS(&s->in[IN_CFG_MFC], b); copyStr(e->mfc, sizeof e->mfc, b);
    getS(&s->in[IN_CFG_O2], b);  copyStr(e->o2, sizeof e->o2, b);
    getS(&s->in[IN_CFG_CYL], b); copyStr(e->cyl, sizeof e->cyl, b);
    getS(&s->in[IN_CFG_STN], b); copyStr(e->stn, sizeof e->stn, b);
}

static void logNames(sg_station *s, const char *lead)
{
    sg_log(&s->c, 0, "%sMFC %s, O2 %s, CYL %s", lead,
           s->active.mfc[0] ? s->active.mfc : "none", s->active.o2[0] ? s->active.o2 : "none",
           s->active.cyl[0] ? s->active.cyl : "none");
}

static void stationLabel(sg_station *s, char *buf, size_t n)
{
    size_t k;
    if (s->active.stn[0]) { copyStr(buf, n, s->active.stn); return; }
    copyStr(buf, n, s->P);                              /* fallback: the prefix without ':' */
    k = strlen(buf);
    if (k && buf[k - 1] == ':') buf[k - 1] = '\0';
}

/* A channel gets a new name: forget its values; until it delivers a value of the new PV (a time
   stamp different from the old one; or, for a PV that never processes, SG_STALETICKS connected
   ticks) it counts as not connected. */
static void chanRename(sg_chan *ch, const char *name)
{
    copyStr(ch->name, sizeof ch->name, name);
    ch->have = 0; ch->lastV = NAN; ch->lastS[0] = '\0'; ch->ok = 0;
    ch->stale = 1; ch->staleTicks = 0; ch->stSec = ch->tsSec; ch->stNsec = ch->tsNsec;
    ch->reassign = 1;
}

/* Build the channel names from s->active; rename only the groups asked for. */
static void setChanNames(sg_station *s, int o2, int mfc, int cyl)
{
    char nm[96];
    int i;
    const sg_names *a = &s->active;
    if (o2) chanRename(&s->ch[SG_GRP_D][SG_CD_O2], a->o2);
    if (cyl) chanRename(&s->ch[SG_GRP_D][SG_CD_CYL], a->cyl);
    if (mfc) {
        for (i = 0; i < SG_NCD; i++) {
            if (!sfxD[i]) continue;
            if (a->mfc[0]) snprintf(nm, sizeof nm, "%s%s", a->mfc, sfxD[i]); else nm[0] = '\0';
            chanRename(&s->ch[SG_GRP_D][i], nm);
        }
        for (i = 0; i < SG_NCS; i++) {
            if (a->mfc[0]) snprintf(nm, sizeof nm, "%s%s", a->mfc, sfxS[i]); else nm[0] = '\0';
            chanRename(&s->ch[SG_GRP_S][i], nm);
        }
        for (i = 0; i < SG_NCO; i++) {        /* writes not permitted: the put channels get no name */
            if (a->mfc[0] && writesPermitted(s)) snprintf(nm, sizeof nm, "%s%s", a->mfc, sfxO[i]);
            else nm[0] = '\0';
            chanRename(&s->ch[SG_GRP_OUT][i], nm);
            s->putFail[i] = 0;                /* a new Alicat: no failure streak yet */
        }
    }
}

/* Once per tick (count = 1) or as a pure check (count = 0). */
static int chanUpdate(sg_chan *ch, int grp, int count)
{
    int ok = ch->assigned && ch->conn;
    if (ok && grp != SG_GRP_OUT) {
        if (ch->tsSec == 0 && ch->tsNsec == 0) ok = 0;                 /* nothing received yet */
        else if (ch->stale) {
            if (ch->tsSec == ch->stSec && ch->tsNsec == ch->stNsec && ch->staleTicks < SG_STALETICKS) {
                if (count) ch->staleTicks++;
                ok = 0;
            } else if (count) ch->stale = 0;
        }
    }
    if (count) {
        ch->ok = ok;
        if (ok && grp != SG_GRP_OUT) {
            ch->have = 1;
            if (grp == SG_GRP_D) ch->lastV = ch->v;
            else memcpy(ch->lastS, ch->s, sizeof ch->lastS);
        }
    }
    return ok;
}

static int chanOkNow(sg_station *s, int grp, int idx) { return chanUpdate(&s->ch[grp][idx], grp, 0); }

/* The required Alicat channels of spec §6.1 (Status and FlowUnits_RBV are optional). */
static int mfcRequiredOk(sg_station *s, int count)
{
    static const int reqD[] = { SG_CD_FLOW, SG_CD_SP, SG_CD_RAMP, SG_CD_TOTAL, SG_CD_RUNNING };
    size_t i;
    int ok = 1;
    for (i = 0; i < sizeof reqD / sizeof reqD[0]; i++)
        if (!(count ? s->ch[SG_GRP_D][reqD[i]].ok : chanOkNow(s, SG_GRP_D, reqD[i]))) ok = 0;
    if (!(count ? s->ch[SG_GRP_S][SG_CS_GAS].ok : chanOkNow(s, SG_GRP_S, SG_CS_GAS))) ok = 0;
    if (writesPermitted(s))                     /* else: no put channels to require */
        for (i = 0; i < SG_NCO; i++)
            if (!(count ? s->ch[SG_GRP_OUT][i].ok : chanOkNow(s, SG_GRP_OUT, (int)i))) ok = 0;
    return ok;
}

static int isMfcChan(int g, int i) { return g != SG_GRP_D || (i != SG_CD_O2 && i != SG_CD_CYL); }

static void listAdd(char *buf, size_t n, size_t *len, const char *item)
{
    if (*len + 1 < n) {
        int w = snprintf(buf + *len, n - *len, "%s%s", *len ? ", " : "", item);
        if (w > 0) *len += (size_t)w;
        if (*len >= n) *len = n - 1;
    }
}

/* Comma list of the configured channels not connected; returns their count. With every Alicat
   channel missing, the list says "<MFC>* (all Alicat channels)" instead of eleven names. */
static int notConnectedList(sg_station *s, char *buf, size_t n)
{
    int g, i, cnt = 0, mfcMissing = 0, mfcTotal = 0, allMfc, listed = 0;
    size_t len = 0;
    char all[96];
    if (n) buf[0] = '\0';
    for (g = 0; g < SG_NGRP; g++)
        for (i = 0; i < grpSize[g]; i++)
            if (isMfcChan(g, i) && s->ch[g][i].name[0]) {
                mfcTotal++;
                if (!chanOkNow(s, g, i)) mfcMissing++;
            }
    allMfc = mfcTotal > 0 && mfcMissing == mfcTotal;
    for (g = 0; g < SG_NGRP; g++)
        for (i = 0; i < grpSize[g]; i++) {
            sg_chan *ch = &s->ch[g][i];
            if (ch->name[0] == '\0' || chanOkNow(s, g, i)) continue;
            cnt++;
            if (allMfc && isMfcChan(g, i)) {
                if (!listed) {
                    snprintf(all, sizeof all, "%s* (all Alicat channels)", s->active.mfc);
                    listAdd(buf, n, &len, all);
                    listed = 1;
                }
                continue;
            }
            listAdd(buf, n, &len, ch->name);
        }
    return cnt;
}

static void setAppliedStatus(sg_station *s, double t)
{
    char ts[40], list[512], st[800];
    localStamp(ts, sizeof ts, t);
    if (notConnectedList(s, list, sizeof list) == 0)
        snprintf(st, sizeof st, "applied %s: all connected", ts);
    else snprintf(st, sizeof st, "applied %s: not connected: %s", ts, list);
    pubTxt(&s->pub[CFG_STATUS], st);
}

/* ================================================================ parameters */
static void modeStateString(sg_station *s, int x, const char *name)
{
    char cur[MAX_STRING_SIZE], b[MAX_STRING_SIZE];
    DBADDR *a = &s->in[IN_MODE_ZRST + x];
    getS(a, cur);
    copyStr(b, sizeof b, name);
    if (strcmp(cur, b) != 0) {
        long st = dbPutField(a, DBR_STRING, b, 1);
        if (st) errlogPrintf("sampleGas: put to %s.%cRST failed\n", a->precord->name, "ZOTT"[x]);
    }
}

/* Par:*, Mode:*, Mode. atStart: the autosaved values go into c->p directly (sgCore.h
   "Ownership"); later, target and mode only through sg_set_target / sg_set_mode on a real
   change. A non-finite value (not a legal parameter) is ignored. */
static void readParams(sg_station *s, int atStart)
{
    sg_ctl *c = &s->c;
    int i, x, j;
    double v;
    for (i = 0; i < sgNParPvs; i++) {
        double *f = (double *)((char *)&c->p + sgParPvs[i].offset);
        v = getD(&s->par[i]);
        if (!isfinite(v)) continue;
        if (i == s->targetIdx && !atStart) {
            if (v != c->p.target) sg_set_target(c, v, "operator");
        } else *f = v;
    }
    for (x = 0; x < 4; x++) {
        char nm[MAX_STRING_SIZE];
        for (j = 0; j < sgNModePvs; j++) {
            v = getD(&s->modeF[x][j]);
            if (isfinite(v)) *(double *)((char *)&c->p.modes[x] + sgModePvs[j].offset) = v;
        }
        getS(&s->modeName[x], nm);
        if (atStart || strcmp(nm, c->p.modes[x].name) != 0) {
            copyStr(c->p.modes[x].name, sizeof c->p.modes[x].name, nm);
            modeStateString(s, x, nm);
        }
    }
    v = getD(&s->in[IN_MODE]);
    if (isfinite(v) && v >= 0 && v <= 3) {
        int m = (int)v;
        if (atStart) c->p.mode = m;
        else if (m != c->p.mode) sg_set_mode(c, m, "operator");
    }
}

/* ================================================================ create */
sg_station *sgIocCreate(const char *prefix, const char *logDir, const char *heliumSet,
                        const char *readOnly, const char *writeMfc, const char *forceShadow)
{
    sg_station *s;
    sg_names edit, none;
    sg_io io;
    char status[256], label[64];
    int r, dropped;
    long n1, n2, n3;
    double v;

    if (!prefix || !*prefix) {
        errlogPrintf("sampleGas: FATAL: macro P (the station prefix) is missing\n");
        return NULL;
    }
    s = calloc(1, sizeof *s);
    if (!s) { errlogPrintf("sampleGas: FATAL: out of memory\n"); return NULL; }
    copyStr(s->P, sizeof s->P, prefix);
    copyStr(s->heSet, sizeof s->heSet, heliumSet && *heliumSet ? heliumSet : "sampleGas_helium.req");
    /* read-only only when asked for (user direction 2026-09-28, write locks for the PC trial
       only; st.cmd.pc always passes READONLY explicitly, default 1 there) */
    s->readOnly = readOnly && *readOnly && strcmp(readOnly, "0") != 0;
    copyStr(s->writeMfc, sizeof s->writeMfc, writeMfc ? writeMfc : "");
    s->forceShadow = forceShadow && *forceShadow && strcmp(forceShadow, "0") != 0;
    /* trial mode (st.cmd.pc): FORCE_SHADOW or WRITE_MFC given. Only there does an Alicat PV
       change turn writes off (spec §8.21 step 3; user direction 2026-09-28) */
    s->trial = s->forceShadow || s->writeMfc[0];
    if (resolveAll(s)) {
        errlogPrintf("sampleGas: FATAL: station %s not started: %d record(s) missing\n", s->P,
                     s->nMissing);
        freeStation(s);
        return NULL;
    }

    s->tickNow = floor(wallNow());
    sg_init(&s->c, NULL, s->tickNow);                  /* no "IOC started" line (sgCore.h) */
    memset(&io, 0, sizeof io);
    io.ctx = s; io.log = stLog; io.put_setpoint = stSp; io.put_ramp = stRamp; io.put_run = stRun;
    s->c.io = io;

    /* PV names (spec §8.21 "At IOC start"): the autosaved (or macro default) Cfg fields */
    readEditNames(s, &edit);
    memset(&none, 0, sizeof none);
    s->active = none;
    status[0] = '\0';
    r = sg_cfg_apply(&edit, &s->active, SG_IDLE, NULL, status, sizeof status);
    (void)r;
    s->configured = sg_cfg_configured(&s->active);

    if (!logDir || !*logDir) logDir = "logs";
    SG_MKDIR(logDir);                                   /* exists already: fine */
    stationLabel(s, label, sizeof label);
    sg_logfile_init(&s->L, logDir, label);

    /* autosaved state into the core, before sg_restart (sgCore.h "First tick") */
    readParams(s, 1);
    v = getD(&s->pub[DG_OVRCOUNT].a);
    s->c.overrideCount = isfinite(v) && v > 0 ? (long)v : 0;

    memset(&s->he, 0, sizeof s->he);
    s->he.cumL = getD(&s->pub[HE_CUML].a);
    s->he.lastTotal = getD(&s->pub[HE_LASTTOTAL].a);
    s->he.cylBase = getD(&s->pub[HE_CYLBASE].a);
    n1 = getArr(&s->pub[HE_HISTT].a, s->he.histT, SG_CYLHIST);
    n2 = getArr(&s->pub[HE_HISTUSED].a, s->he.histUsed, SG_CYLHIST);
    v = getD(&s->pub[HE_HISTN].a);
    s->he.histN = (int)fmin(fmin(isfinite(v) ? v : 0, (double)n1), (double)n2);
    n1 = getArr(&s->pub[HE_EVT].a, s->he.evT, SG_LEDGER);
    n2 = getArr(&s->pub[HE_EVTYPE].a, s->he.evType, SG_LEDGER);
    n3 = getArr(&s->pub[HE_EVL].a, s->he.evL, SG_LEDGER);
    v = getD(&s->pub[HE_EVN].a);
    s->he.evN = (int)fmin(fmin(isfinite(v) ? v : 0, (double)n1), fmin((double)n2, (double)n3));
    n1 = getArr(&s->pub[HE_SNAPT].a, s->he.snapT, SG_SNAPS);
    n2 = getArr(&s->pub[HE_SNAPL].a, s->he.snapL, SG_SNAPS);
    v = getD(&s->pub[HE_SNAPN].a);
    s->he.snapN = (int)fmin(fmin(isfinite(v) ? v : 0, (double)n1), (double)n2);
    if (s->he.histN < 0) s->he.histN = 0;
    if (s->he.evN < 0) s->he.evN = 0;
    if (s->he.snapN < 0) s->he.snapN = 0;
    dropped = sg_helium_import(&s->c, &s->he);

    /* the write gate's start-up value: not a transition (sgIoc.h) */
    v = getD(&s->in[IN_WRITEEN]);
    if (isfinite(v) && v != 0) {
        if (!writesPermitted(s)) {                     /* autosaved 1, writes not possible */
            putD(&s->in[IN_WRITEEN], 0);
            v = 0;
            logWriteRefusal(s);
        } else if (s->forceShadow) {  /* FORCE_SHADOW: a restart never resumes writing */
            putD(&s->in[IN_WRITEEN], 0);
            v = 0;
            sg_log(&s->c, 1, "start in shadow mode (FORCE_SHADOW): Par:writeEnable set to 0");
        }
    }
    sg_gate_init(&s->g, isfinite(v) && v != 0);

    setChanNames(s, 1, 1, 1);
    pubStr(&s->pub[CFG_ACT_MFC], s->active.mfc);
    pubStr(&s->pub[CFG_ACT_O2], s->active.o2);
    pubStr(&s->pub[CFG_ACT_CYL], s->active.cyl);
    pubStr(&s->pub[CFG_ACT_STN], s->active.stn);

    logNames(s, "PV names: ");
    if (s->readOnly)
        sg_log(&s->c, 0, "read-only IOC: the Alicat Setpoint, RampRate and Run are not connected");
    else if (s->writeMfc[0])
        sg_log(&s->c, writesPermitted(s) ? 0 : 1, "writes permitted only to Alicat %s%s",
               s->writeMfc, writesPermitted(s) ? "" : " (not the active one: no put channels)");
    if (dropped)
        sg_log(&s->c, 1, "helium autosave: %d entr%s dropped on restore (invalid or out of order)",
               dropped, dropped == 1 ? "y" : "ies");
    if (!s->configured) {
        notConfiguredText(&s->active, status, sizeof status);
        pubTxt(&s->pub[CFG_STATUS], status);
        sg_log(&s->c, 1, "%s: station stays in IDLE", status);
    } else   /* spec §8.15 step 1: the Alicat wait has no time limit (D10) */
        sg_log(&s->c, 0, "waiting for the O2 (up to 30 s) and Alicat PVs (no time limit) to connect");
    registerStation(s);
    return s;
}

void sgIocNames(sg_station *s, sg_names *active) { *active = s->active; }

int sgIocReadOnly(sg_station *s) { return s->readOnly; }

int sgIocWritesPermitted(sg_station *s) { return writesPermitted(s); }

const char *sgIocChanName(sg_station *s, int grp, int idx)
{
    if (grp < 0 || grp >= SG_NGRP || idx < 0 || idx >= grpSize[grp]) return "";
    return s->ch[grp][idx].name;
}

int sgIocChanReassign(sg_station *s, int grp, int idx)
{
    int r;
    if (grp < 0 || grp >= SG_NGRP || idx < 0 || idx >= grpSize[grp]) return 0;
    r = s->ch[grp][idx].reassign;
    s->ch[grp][idx].reassign = 0;
    return r;
}

/* ================================================================ SNL feed */
static void feedCommon(sg_chan *ch, int assigned, int connected, int sevr, unsigned tsSec,
                       unsigned tsNsec)
{
    ch->assigned = assigned ? 1 : 0;
    ch->conn = connected ? 1 : 0;
    ch->sevr = sevr < 0 ? 3 : (sevr > 3 ? 3 : sevr);
    ch->tsSec = tsSec; ch->tsNsec = tsNsec;
}

void sgIocInputD(sg_station *s, int idx, double v, int assigned, int connected, int sevr,
                 unsigned tsSec, unsigned tsNsec)
{
    if (idx < 0 || idx >= SG_NCD) return;
    feedCommon(&s->ch[SG_GRP_D][idx], assigned, connected, sevr, tsSec, tsNsec);
    s->ch[SG_GRP_D][idx].v = v;
}

void sgIocInputS(sg_station *s, int idx, const char *v, int assigned, int connected, int sevr,
                 unsigned tsSec, unsigned tsNsec)
{
    if (idx < 0 || idx >= SG_NCS) return;
    feedCommon(&s->ch[SG_GRP_S][idx], assigned, connected, sevr, tsSec, tsNsec);
    copyStr(s->ch[SG_GRP_S][idx].s, sizeof s->ch[SG_GRP_S][idx].s, v ? v : "");
}

void sgIocInputPut(sg_station *s, int idx, int assigned, int connected)
{
    if (idx < 0 || idx >= SG_NCO) return;
    feedCommon(&s->ch[SG_GRP_OUT][idx], assigned, connected, 0, 0, 0);
}

/* The wait covers the O2 and every Alicat channel of spec §6.1, the optional Status and
   FlowUnits_RBV included (in control only the required ones count, see buildInputs); not the
   optional cylinder. */
int sgIocReady(sg_station *s)
{
    int g, i;
    if (!s->configured) return 1;
    for (g = 0; g < SG_NGRP; g++)
        for (i = 0; i < grpSize[g]; i++)
            if ((isMfcChan(g, i) || (g == SG_GRP_D && i == SG_CD_O2)) && s->ch[g][i].name[0]
                && !chanOkNow(s, g, i))
                return 0;
    return 1;
}

int sgIocTouch(const void *a, const void *b) { (void)a; (void)b; return 1; }

/* Once a second during the start-up connection wait (spec §8.15 step 1, up to 30 s): the program
   is alive, so Sts:Heartbeat keeps changing and Sts:TickAge does not call it stalled (found in
   the Linux smoke start, 2026-09-29). The tick clock counts from here: its reference moves to
   this second, so the first tick after the wait is not taken for 30 s of missed ticks. */
void sgIocWaitBeat(sg_station *s)
{
    s->heartbeat++;
    s->tickNow = floor(wallNow());
    pubNum(&s->pub[STS_HEARTBEAT], (double)s->heartbeat);
}

void sgIocWaitEnd(sg_station *s)
{
    char list[512];
    if (!s->configured) return;
    if (notConnectedList(s, list, sizeof list) == 0) sg_log(&s->c, 0, "PV connections: all connected");
    else sg_log(&s->c, 1, "PVs not connected (treated as invalid): %s", list);
    setAppliedStatus(s, wallNow());
}

/* ================================================================ clock (spec §8.1) */
/* `now` counts +1 per tick (sgCore.h: never skipped or repeated) and each tick is started at
   the wall-clock second equal to its `now`. Late ticks (a slow put, a stall) are caught up back
   to back; beyond SG_MAXBEHIND the clock skips ahead (logged). If the wall clock steps back,
   ticks go on at 1 Hz with `now` ahead of it (logged), so `now` never repeats. */
double sgIocSecondsToNextTick(sg_station *s)
{
    double wall = wallNow(), dt;
    s->nextNow = s->started || s->heartbeat ? s->tickNow + 1 : floor(wall) + 1;
    dt = s->nextNow - wall;
    if (dt < -SG_MAXBEHIND) {
        sg_log(&s->c, 1, "tick clock %.0f s behind the wall clock: skipped ahead", -dt);
        s->nextNow = floor(wall) + 1;
        dt = s->nextNow - wall;
    } else if (dt < 0) dt = 0;
    if (dt > 1.5) {
        if (!s->clockBack)
            sg_log(&s->c, 1, "wall clock %.0f s behind the controller clock (stepped back?): "
                   "ticks continue at 1 Hz", dt - 1);
        s->clockBack = 1;
        dt = 1.0;
    } else s->clockBack = 0;
    s->scheduled = 1;
    return dt;
}

/* ================================================================ one tick */
static void buildInputs(sg_station *s, sg_inputs *in)
{
    int g, i;
    sg_chan *d = s->ch[SG_GRP_D];
    sg_chan *gas = &s->ch[SG_GRP_S][SG_CS_GAS];
    for (g = 0; g < SG_NGRP; g++)
        for (i = 0; i < grpSize[g]; i++) chanUpdate(&s->ch[g][i], g, 1);
    memset(in, 0, sizeof *in);
    /* sgCore.h sg_inputs: last received values, never a default for a disconnection */
    in->o2 = d[SG_CD_O2].have ? d[SG_CD_O2].lastV : NAN;
    in->o2Sevr = d[SG_CD_O2].ok ? d[SG_CD_O2].sevr : 3;
    in->flow = d[SG_CD_FLOW].have ? d[SG_CD_FLOW].lastV : NAN;
    in->sp = d[SG_CD_SP].have ? d[SG_CD_SP].lastV : NAN;
    in->ramp = d[SG_CD_RAMP].have ? d[SG_CD_RAMP].lastV : NAN;
    in->total = d[SG_CD_TOTAL].ok ? d[SG_CD_TOTAL].lastV : NAN;
    /* before any value: "running" (1), so a never-seen Alicat is not taken for one on hold */
    in->running = d[SG_CD_RUNNING].have ? d[SG_CD_RUNNING].lastV != 0 : 1;
    copyStr(in->gas, sizeof in->gas, gas->have ? gas->lastS : "(no reading)");
    s->caConnected = mfcRequiredOk(s, 1) && d[SG_CD_FLOW].sevr < 3;
    /* G3 (spec §8.18): Flow_RBV frozen while the Alicat should be flowing counts as not
       responding for the core (alarm, re-send on recovery), but not for the gate: puts go on */
    in->mfcStale = s->caConnected &&
        sg_flow_stale(&s->flowStale, s->tickNow, d[SG_CD_FLOW].ok, d[SG_CD_FLOW].tsSec,
                      d[SG_CD_FLOW].tsNsec,
                      d[SG_CD_SP].ok && d[SG_CD_SP].lastV > 0 && in->running,
                      fmax(SG_FLOW_STALE_S, s->c.p.frozenTime));
    in->mfcConnected = s->caConnected && !in->mfcStale;
    in->writeEnabled = s->g.writeEnable;       /* re-read after the tick's switch, sgIocTick */
}

/* Par:writeEnable transitions go through the gate only (sgIoc.h, spec §8.20). */
static void syncWriteEnable(sg_station *s)
{
    double v = getD(&s->in[IN_WRITEEN]);
    int en;
    if (!isfinite(v)) return;
    en = v != 0;
    if (en && !writesPermitted(s)) {           /* Plan 2 task 6: refused, put back to 0 */
        putD(&s->in[IN_WRITEEN], 0);
        logWriteRefusal(s);
        if (s->g.writeEnable) sg_gate_set_enable(&s->g, &s->c, 0, s->c.in.sp);
        if (!s->started) s->liveInWait = 0;
        return;
    }
    if (en != s->g.writeEnable) {
        sg_gate_set_enable(&s->g, &s->c, en, s->c.in.sp);
        /* D2: a 0 -> 1 during the start-up wait makes the restart decision enter IDLE (a switch
           back to shadow before the Alicat connects cancels it) */
        if (!s->started) s->liveInWait = en;
    }
}

static int takeCmd(sg_station *s, int idx)
{
    double v = getD(&s->in[idx]);
    if (!(v != 0) || isnan(v)) return 0;
    putD(&s->in[idx], 0);
    return 1;
}

/* A command that needs the state machine: refused on an unconfigured station and while the
   start-up still waits for the Alicat (the restart decision has not run; it would override). */
static int rejectNotConfigured(sg_station *s, const char *what)
{
    char t[128];
    if (!s->configured) {
        notConfiguredText(&s->active, t, sizeof t);
        sg_log(&s->c, 0, "%s ignored: %s", what, t);
        return 1;
    }
    if (!s->started) {
        sg_log(&s->c, 0, "%s ignored: waiting for the Alicat PVs, controller not acting yet", what);
        return 1;
    }
    return 0;
}

/* Spec §8.21 "Cmd:Apply at runtime", steps 1-6 (6 finishes in checkApply). */
static void cfgApply(sg_station *s)
{
    sg_ctl *c = &s->c;
    sg_names edit, old = s->active;
    char status[256], label[64];
    int mfcChanged = 0, o2Changed, cylChanged, r;

    readEditNames(s, &edit);
    status[0] = '\0';
    r = sg_cfg_apply(&edit, &s->active, c->state, &mfcChanged, status, sizeof status);
    if (r == SG_CFG_REJECT_STATE) {
        pubTxt(&s->pub[CFG_STATUS], status);
        sg_log(c, 0, "PV name change ignored in %s", stateText(c->state));
        return;
    }
    if (r == SG_CFG_UNCHANGED) { sg_log(c, 0, "PV names unchanged"); return; }

    o2Changed = strcmp(old.o2, s->active.o2) != 0;
    cylChanged = strcmp(old.cyl, s->active.cyl) != 0;
    /* step 3, in trial mode only (user direction 2026-09-28): in production a changed Cfg:MFC
       keeps writes as they were */
    if (mfcChanged && s->g.writeEnable && s->trial) {
        sg_log(c, 2, "Alicat PV changed: writes disabled; re-enable after checking the new MFC");
        putD(&s->in[IN_WRITEEN], 0);
        sg_gate_set_enable(&s->g, c, 0, c->in.sp);
    }
    sg_channels_changed(c, o2Changed, mfcChanged);                                /* step 4 */
    if (mfcChanged) {                                                             /* step 5 */
        sg_log(c, 1, "Alicat PV changed: totalizer re-baselined; press New He cylinder if the "
               "cylinder differs");
        s->mfcApplied = 1;              /* helium autosave now and at the new reference (tick 9) */
        s->havePubHe = 0;               /* publish the cleared usage log this tick, for that save */
    }
    stationLabel(s, label, sizeof label);
    copyStr(s->L.stn, sizeof s->L.stn, label);                  /* Cfg:STN: the log label only */
    s->configured = sg_cfg_configured(&s->active);
    setChanNames(s, o2Changed, mfcChanged, cylChanged);
    s->namesChanged = 1;
    pubStr(&s->pub[CFG_ACT_MFC], s->active.mfc);                                  /* step 6 */
    pubStr(&s->pub[CFG_ACT_O2], s->active.o2);
    pubStr(&s->pub[CFG_ACT_CYL], s->active.cyl);
    pubStr(&s->pub[CFG_ACT_STN], s->active.stn);
    if (r == SG_CFG_NOT_CONFIGURED) {
        pubTxt(&s->pub[CFG_STATUS], status);
        logNames(s, "operator: PV names \xe2\x86\x92 ");
        s->applyPending = 0;
    } else { s->applyPending = 1; s->applyT = s->tickNow; }
}

static void cfgRestoreDefaults(sg_station *s)
{
    static const int from[4] = { IN_DEF_MFC, IN_DEF_O2, IN_DEF_CYL, IN_DEF_STN };
    static const int to[4] = { IN_CFG_MFC, IN_CFG_O2, IN_CFG_CYL, IN_CFG_STN };
    char b[MAX_STRING_SIZE];
    int i;
    for (i = 0; i < 4; i++) {
        long st;
        getS(&s->in[from[i]], b);
        st = dbPutField(&s->in[to[i]], DBR_STRING, b, 1);
        if (st) errlogPrintf("sampleGas: put to %s failed\n", s->in[to[i]].precord->name);
    }
    sg_log(&s->c, 0, "admin: PV name fields set to the defaults (not applied)");
}

/* Spec §8.1 order; §8.5 rejections; §8.21 "not configured". */
static void commands(sg_station *s)
{
    sg_ctl *c = &s->c;
    char why[SG_MSG];
    if (takeCmd(s, IN_CMD_FZERO) && !rejectNotConfigured(s, "Flow Zero")) sg_op_flow_zero(c);
    if (takeCmd(s, IN_CMD_PURGE) && !rejectNotConfigured(s, "Purge") && !sg_op_purge(c))
        sg_log(c, 0, "Purge ignored in %s", stateText(c->state));
    if (takeCmd(s, IN_CMD_RESUME) && !rejectNotConfigured(s, "Resume Flow")
        && !sg_op_resume_flow(c, why, sizeof why))
        sg_log(c, 0, "%s", why);
    if (takeCmd(s, IN_CMD_IDLE)) {
        if (s->started || !s->configured) sg_op_idle(c);
        else {                                  /* D8: the restart decision will enter IDLE */
            s->releaseInWait = 1;
            sg_log(c, 0, "Release control noted: the controller stays in IDLE when the Alicat "
                         "PVs connect");
        }
    }
    if (takeCmd(s, IN_CMD_NEWCYL) && !rejectNotConfigured(s, "New He cylinder")) {
        sg_new_cylinder(c, "operator");
        s->heSave = 1;
    }
    if (takeCmd(s, IN_CMD_NEWRUN) && !rejectNotConfigured(s, "Mark new run")) {
        sg_mark_new_run(c);
        s->heSave = 1;
    }
    if (takeCmd(s, IN_CMD_RSTOVR)) sg_reset_override_count(c);
    if (takeCmd(s, IN_CFG_RESTORE)) cfgRestoreDefaults(s);
    if (takeCmd(s, IN_CFG_APPLY)) cfgApply(s);
}

/* Spec §8.11 steps 2-5 with the real epid record (sgCore.h sg_epid_cfg). */
static void epidStep(sg_station *s, const sg_epid_cfg *cfg)
{
    sg_ctl *c = &s->c;
    dbCommon *prec = s->in[IN_PID_VAL].precord;
    double fbop = getD(&s->in[IN_PID_FBOP]), oval = NAN, fbon = cfg->FBON ? 1 : 0;
    double odel = cfg->ODEL, i0;
    long st = 0;

    /* bumpless start: epid reads PID:Out through OUTL into I on its FBON 0 -> 1, i.e. when the
       record was last processed with FBON 0 (FBOP) -- including the first step after IOC start.
       After a restart or Resume Flow (cfg->SEED), PID:Out = lastCmd - P makes the first output
       lastCmd; ODEL 0 for this one processing keeps the deadband from holding the stale OVAL
       instead. After HANDOFF, PID:Out = lastCmd: epid's own start, as the reference (§8.11;
       sgCore.h sg_epid_cfg). */
    if (cfg->FBON && !(fbop != 0)) {
        if (cfg->SEED) {
            i0 = sg_pid_bumpless_i(cfg);
            if (isfinite(i0)) putD(&s->pub[PID_OUT].a, i0);
            odel = 0;
        } else if (isfinite(cfg->OUTL)) putD(&s->pub[PID_OUT].a, cfg->OUTL);
    }
    putD(&s->in[IN_PID_CVAL], cfg->CVAL);

    dbScanLock(prec);
    st |= dbPut(&s->in[IN_PID_VAL], DBR_DOUBLE, &cfg->VAL, 1);
    st |= dbPut(&s->in[IN_PID_KP], DBR_DOUBLE, &cfg->KP, 1);
    st |= dbPut(&s->in[IN_PID_KI], DBR_DOUBLE, &cfg->KI, 1);
    st |= dbPut(&s->in[IN_PID_DRVL], DBR_DOUBLE, &cfg->DRVL, 1);
    st |= dbPut(&s->in[IN_PID_DRVH], DBR_DOUBLE, &cfg->DRVH, 1);
    st |= dbPut(&s->in[IN_PID_ODEL], DBR_DOUBLE, &odel, 1);
    st |= dbPut(&s->in[IN_PID_FBON], DBR_DOUBLE, &fbon, 1);
    if (!st) {
        st = dbProcess(prec);
        if (dbGet(&s->in[IN_PID_OVAL], DBR_DOUBLE, &oval, NULL, NULL, NULL)) oval = NAN;
    }
    dbScanUnlock(prec);
    if (st) {
        errlogPrintf("sampleGas: %s: epid configure/process failed (status %ld)\n", prec->name, st);
        oval = NAN;              /* D4: not computed = not a number, which the core makes loud */
    }
    if (cfg->FBON) s->pub[PID_OUT].have = 0;           /* epid owns PID:Out while FBON = 1 */

    /* While FBON = 0 devEpidSoft still recomputes OVAL (= P + the stale I), where the reference
       (processEpid, and sgEpidSim.c in the replay) holds it. The record is processed anyway, so
       its FBOP follows FBON and the next FBON = 1 is a bumpless start; the core gets its own
       held OVAL back, as in the reference. With FBON = 0 sg_pid_done commands nothing. */
    if (!cfg->FBON) oval = c->epid.OVAL;

    /* a non-finite OVAL is never commanded: sg_pid_done keeps the last one and raises the MAJOR
       "controller cannot compute a flow" Mismatch source (spec §8.3, §8.11 step 5; D4) */
    sg_pid_done(c, oval);
}

static void checkApply(sg_station *s)
{
    char list[8];
    if (!s->applyPending) return;
    if (notConnectedList(s, list, sizeof list) == 0 || s->tickNow - s->applyT >= SG_APPLYWAIT) {
        setAppliedStatus(s, s->applyT);
        logNames(s, "operator: PV names \xe2\x86\x92 ");
        s->applyPending = 0;
    }
}

/* spec §8.8: Alm:Units while FlowUnits_RBV is not SLPM (D3; an alarm, never a refusal). Every
   tick of a running station; no reading of the current Alicat keeps the level. */
static void checkUnits(sg_station *s)
{
    sg_chan *u = &s->ch[SG_GRP_S][SG_CS_UNITS];
    if (!(s->configured && s->started)) return;
    sg_units_alarm(&s->c, u->ok ? u->lastS : NULL);
}

/* ---------------------------------------------------------------- publish */
static void overrideText(const sg_ctl *c, char *buf, size_t n)
{
    size_t len = 0;
    int i;
    buf[0] = '\0';
    for (i = c->nOverride - 1; i >= 0 && len + 1 < n; i--) {
        time_t tt = (time_t)c->overrideT[i];
        struct tm tmv;
        int w;
        if (epicsTime_localtime(&tt, &tmv) != epicsTimeOK) memset(&tmv, 0, sizeof tmv);
        w = snprintf(buf + len, n - len, "%s%02d:%02d:%02d  %s", len ? "\n" : "", tmv.tm_hour,
                     tmv.tm_min, tmv.tm_sec, c->overrideLog[i]);
        if (w < 0) break;
        len += (size_t)w;
        if (len >= n) { len = n - 1; break; }
    }
}

static void publishHelium(sg_station *s)
{
    sg_ctl *c = &s->c;
    sg_helium *h = &s->he;
    sg_report *r = &s->rep;
    double a[SG_RUNS];
    int i, k;

    for (k = 0; k < SG_NWIN; k++) {
        int ok = k < c->nCylEst;
        pubNum(&s->pub[HE_EST3D + 2 * k], ok ? c->cylEst[k].h : NAN);
        pubNum(&s->pub[HE_RATE3D + 2 * k], ok ? c->cylEst[k].rate : NAN);
    }
    pubNum(&s->pub[HE_LEFTL], c->cylLeftL);
    pubNum(&s->pub[HE_EMPTYMIN], c->cylMinH);
    pubNum(&s->pub[HE_EMPTYMAX], c->cylMaxH);
    pubNum(&s->pub[HE_EMPTYMED], c->cylMedianH);
    sg_forecast_text(c, s->txt, sizeof s->txt);
    pubTxt(&s->pub[HE_FCTEXT], s->txt);
    pubNum(&s->pub[HE_CUML], c->cumL);
    pubNum(&s->pub[HE_LASTTOTAL], c->lastTotal);
    pubNum(&s->pub[HE_CYLBASE], c->cylBase);

    /* arrays and the report: after a ledger event, every minute (the usage log and the hourly
       snapshots change on minute ticks), and on the first tick; unchanged arrays are not put */
    if (s->havePubHe && c->ledgerSeq == s->pubLedgerSeq && fmod(s->tickNow, 60) != 0) return;
    s->havePubHe = 1;
    s->pubLedgerSeq = c->ledgerSeq;
    sg_helium_export(c, h);
    pubArr(&s->pub[HE_HISTT], h->histT, h->histN);
    pubArr(&s->pub[HE_HISTUSED], h->histUsed, h->histN);
    pubNum(&s->pub[HE_HISTN], h->histN);
    pubArr(&s->pub[HE_EVT], h->evT, h->evN);
    pubArr(&s->pub[HE_EVTYPE], h->evType, h->evN);
    pubArr(&s->pub[HE_EVL], h->evL, h->evN);
    pubNum(&s->pub[HE_EVN], h->evN);
    pubArr(&s->pub[HE_SNAPT], h->snapT, h->snapN);
    pubArr(&s->pub[HE_SNAPL], h->snapL, h->snapN);
    pubNum(&s->pub[HE_SNAPN], h->snapN);

    sg_usage_report(c, r);
    pubNum(&s->pub[REP_DISP], r->dispensed);
    pubNum(&s->pub[REP_INRUNS], r->inRuns);
    pubNum(&s->pub[REP_CYLS], r->cyls);
    pubNum(&s->pub[REP_CYLEQ], r->cylEquiv);
    pubNum(&s->pub[REP_WSTART], r->wStart);
    pubNum(&s->pub[REP_WEND], r->wEnd);
    for (i = 0; i < r->nRuns; i++) a[i] = r->runs[i].start;
    pubArr(&s->pub[REP_RUNSTART], a, r->nRuns);
    for (i = 0; i < r->nRuns; i++) a[i] = isnan(r->runs[i].end) ? 0 : r->runs[i].end;  /* 0 = open */
    pubArr(&s->pub[REP_RUNEND], a, r->nRuns);
    for (i = 0; i < r->nRuns; i++) a[i] = r->runs[i].purges;
    pubArr(&s->pub[REP_RUNPURGES], a, r->nRuns);
    for (i = 0; i < r->nRuns; i++) a[i] = r->runs[i].L;
    pubArr(&s->pub[REP_RUNL], a, r->nRuns);
    for (i = 0; i < r->nRuns; i++) a[i] = r->runs[i].cylinders;
    pubArr(&s->pub[REP_RUNCYL], a, r->nRuns);
    for (i = 0; i < r->nRuns; i++) a[i] = r->runs[i].finished;
    pubArr(&s->pub[REP_RUNFIN], a, r->nRuns);
    pubNum(&s->pub[REP_RUNN], r->nRuns);
    sg_report_text(c, r, s->txt, sizeof s->txt);
    pubTxt(&s->pub[REP_TEXT], s->txt);
}

static void publish(sg_station *s)
{
    sg_ctl *c = &s->c;
    const sg_sd *sd = &c->sd;
    sg_chan *d = s->ch[SG_GRP_D];
    sg_chan *st = &s->ch[SG_GRP_S][SG_CS_STATUS];
    sg_names edit, tmp;
    char t[256];
    int k, mfcAll;

    /* Sts */
    pubNum(&s->pub[STS_STATE], c->state);
    if (s->configured && !s->started)
        pubTxt(&s->pub[STS_STATEDESC], "Waiting for the Alicat PVs: controller not acting yet.");
    else if (s->configured)
        pubTxt(&s->pub[STS_STATEDESC], sg_state_desc[c->state >= 0 ? c->state : 0]);
    else { notConfiguredText(&s->active, t, sizeof t); pubTxt(&s->pub[STS_STATEDESC], t); }
    pubTxt(&s->pub[STS_LASTACTION], c->lastAction);
    sg_progress_text(c, s->progress, sizeof s->progress);
    pubTxt(&s->pub[STS_PROGRESS], s->progress);
    pubNum(&s->pub[STS_INRANGE], c->o2ok && fabs(c->o2 - c->p.target) <= c->p.tol);
    pubNum(&s->pub[STS_O2], c->o2ok ? c->o2 : NAN);                 /* NaN -> INVALID (UDF) */
    pubNum(&s->pub[STS_O2VALID], c->o2ok);
    pubNum(&s->pub[STS_EXPFLOW], sg_expected_flow(c));
    pubNum(&s->pub[STS_LASTCMD], c->lastCmd);
    pubNum(&s->pub[STS_FLOW], d[SG_CD_FLOW].ok ? c->in.flow : NAN);
    pubNum(&s->pub[STS_SPRBV], d[SG_CD_SP].ok ? c->in.sp : NAN);
    pubNum(&s->pub[STS_MFCRUN], c->in.running);
    pubStr(&s->pub[STS_MFCSTATUS], st->have ? st->lastS : "");
    pubNum(&s->pub[STS_WRITEEN], s->g.writeEnable);
    pubNum(&s->pub[STS_CYLP], d[SG_CD_CYL].ok && d[SG_CD_CYL].sevr < 3 ? d[SG_CD_CYL].lastV : NAN);

    /* Diag */
    pubNum(&s->pub[DG_ABOVE], c->aboveCount);
    pubNum(&s->pub[DG_MAXRATE], c->maxRate);
    pubNum(&s->pub[DG_LIDARMED], c->lidArmed);
    pubNum(&s->pub[DG_HOLDSEC], c->holdSec);
    pubNum(&s->pub[DG_HOLDATT], c->holdAttempts);
    pubNum(&s->pub[DG_SETTLING], c->settling);
    pubNum(&s->pub[DG_SETTLEDIR], c->settleDir);
    pubNum(&s->pub[DG_SETTLET0], c->settleT0);
    pubNum(&s->pub[DG_TOWARD], c->towardRate);
    pubNum(&s->pub[DG_SLOPE], c->o2Slope);
    pubNum(&s->pub[DG_STALLSEC], c->stallSec);
    pubNum(&s->pub[DG_STALLLAT], c->stallLatched);
    pubNum(&s->pub[DG_FLOWSTEADY], c->flowSteady);
    pubNum(&s->pub[DG_INFINE], c->inFine);
    pubNum(&s->pub[DG_PINNEDSEC], c->pinnedSec);
    pubNum(&s->pub[DG_PELAPSED], sd->elapsed);
    pubNum(&s->pub[DG_PTIMEOUT], c->state != SG_PURGE ? NAN
           : (isnan(sd->timeout) || sd->timeout == 0) ? sg_purge_timeout(c) : sd->timeout);
    pubNum(&s->pub[DG_O2START], sd->o2Start);
    pubNum(&s->pub[DG_ONSET], sd->onsetT);
    pubNum(&s->pub[DG_LIDRATIO], sd->kin);
    pubNum(&s->pub[DG_LIDCURV], sd->curv);
    pubNum(&s->pub[DG_LIDRESULT], sd->lidResult);
    pubNum(&s->pub[DG_ESTO2], sd->est);
    pubNum(&s->pub[DG_BLIND], c->blind);
    pubNum(&s->pub[DG_HPHASE], sd->phaseDelay);
    pubNum(&s->pub[DG_OVRCOUNT], (double)c->overrideCount);
    overrideText(c, s->txt, sizeof s->txt);
    pubTxt(&s->pub[DG_OVRLOG], s->txt);

    publishHelium(s);

    /* Cfg */
    readEditNames(s, &edit);
    tmp = s->active;
    pubNum(&s->pub[CFG_PENDING], sg_cfg_apply(&edit, &tmp, SG_IDLE, NULL, NULL, 0) != SG_CFG_UNCHANGED);
    mfcAll = s->active.mfc[0] != '\0';
    for (k = SG_CD_FLOW; k <= SG_CD_RUNNING; k++) if (!d[k].ok) mfcAll = 0;
    for (k = 0; k < SG_NCS; k++) if (!s->ch[SG_GRP_S][k].ok) mfcAll = 0;
    if (writesPermitted(s))
        for (k = 0; k < SG_NCO; k++) if (!s->ch[SG_GRP_OUT][k].ok) mfcAll = 0;
    pubNum(&s->pub[CFG_CONN_MFC], mfcAll);
    pubNum(&s->pub[CFG_CONN_O2], d[SG_CD_O2].ok);
    pubNum(&s->pub[CFG_CONN_CYL], d[SG_CD_CYL].ok);

    /* Alm, then Banner and WorstSevr (spec §8.14 "Publishing") */
    for (k = 0; k < SG_NALM; k++) {
        const sg_alarm_slot *a = &c->alarms[k];
        pubNum(&s->alm[k], a->active ? a->sev : 0);
        pubTxt(&s->almMsg[k], a->active ? a->msg : "");
    }
    sg_banner(c, s->banner, sizeof s->banner);
    pubTxt(&s->pub[STS_BANNER], s->banner);
    pubNum(&s->pub[STS_WORST], sg_worst_sev(c));
    pubNum(&s->pub[STS_HEARTBEAT], (double)s->heartbeat);

    if (s->logDirty) {                                 /* last: it carries this tick's lines */
        sg_logfile_text(&s->L, s->txt, sizeof s->txt);
        pubTxt(&s->pub[LOG_TEXT], s->txt);
        s->logDirty = 0;
    }
}

void sgIocTick(sg_station *s)
{
    sg_ctl *c = &s->c;
    sg_inputs in;
    sg_epid_cfg cfg;
    double now;

    if (!s->scheduled) sgIocSecondsToNextTick(s);
    s->scheduled = 0;
    now = s->nextNow;
    s->tickNow = now;
    s->inTick = 1;
    s->heartbeat++;

    buildInputs(s, &in);                                                   /* 1 */
    sg_gate_set_connected(&s->g, s->caConnected);   /* CA state: a frozen reading keeps puts */
    sg_set_inputs(c, &in);
    /* 2. On the first tick as at start (atStart = 1: values straight into c->p), so no operator
       call (sg_set_target / sg_set_mode) runs before the restart decision (fix round 1). */
    readParams(s, !s->started);
    if (!s->started) {                                                     /* 3 */
        /* user direction 2026-09-28: no restart decision without the Alicat (sg_restart would
           park the controller in IDLE for good); wait, loudly and without a time limit */
        switch (sg_start_decision(s->configured, &in)) {
        case SG_START_RESTART: {
            /* D2, D8: Live switched on, or Release control pressed, during the wait -> IDLE */
            const char *idle = sg_start_idle_reason(s->releaseInWait, s->liveInWait);
            if (s->waitAlarm) sg_log(c, 0, "Alicat PVs connected: running the restart decision");
            s->started = 1;
            memset(s->putFail, 0, sizeof s->putFail);
            if (idle) sg_start_idle(c, now, idle);    /* both reinit: the wait alarm clears */
            else sg_restart(c, now);
            break;
        }
        case SG_START_NOTCONF:
            s->started = 1;
            sg_reinit(c, now);
            sg_enter(c, SG_IDLE, "restart: not configured");
            break;
        default: {
            char list[512];
            notConnectedList(s, list, sizeof list);
            sg_start_wait_alarm(c, &s->waitAlarm, list);
            break;
        }
        }
    }
    {   /* spec §8.21 "not configured" on the banner while writes are enabled (user direction) */
        char t[128];
        if (!s->configured) {
            notConfiguredText(&s->active, t, sizeof t);
            /* the text after "not configured: " */
            sg_not_configured_alarm(c, &s->ncAlarm, s->g.writeEnable, t + strlen("not configured: "));
        } else sg_not_configured_alarm(c, &s->ncAlarm, 0, "");
    }
    /* after the restart decision: a 0 -> 1 seen in the decision's own tick (made during the SNL's
       first 30 s) enters IDLE (spec §8.20, the valve stays where it is) instead of being
       overridden by a restart into REGULATE; one made in an earlier tick of the wait for the
       Alicat is remembered (liveInWait) and makes the decision itself enter IDLE (D2) */
    syncWriteEnable(s);
    commands(s);                                                           /* 4 */
    /* a switch this tick (Par:writeEnable, or the PC trial's Cfg:MFC rule) holds for the whole
       tick's setpoint-follow judgement (sgCore.h sg_inputs.writeEnabled) */
    c->in.writeEnabled = s->g.writeEnable;
    if (s->configured && s->started) {
        sg_tick(c, now);                                                   /* 5 */
        if (sg_pid_due(c, now) && sg_pid_prepare(c, &cfg)) epidStep(s, &cfg);   /* 6 */
    }
    if (!c->epid.FBON) {                                                   /* 7 */
        if (isfinite(c->lastCmd)) pubNum(&s->pub[PID_OUT], c->lastCmd);
    } else s->pub[PID_OUT].have = 0;
    checkUnits(s);
    /* G2: shadow mode on the banner and to the alarm server (spec §8.20) */
    sg_shadow_alarm(c, s->configured && s->started, s->g.writeEnable);
    checkApply(s);
    publish(s);                                                            /* 8 */
    /* after a Cfg:MFC change the helium set is saved at once and at the new totalizer's first
       reference, not up to 300 s later: a restart must not restore the old Alicat's reference */
    if (sg_cfg_he_save(&s->heRebase, s->mfcApplied, c->lastTotal)) s->heSave = 1;
    s->mfcApplied = 0;
    if (s->heSave) {                                                       /* 9 */
        char cmd[192];
        s->heSave = 0;
        snprintf(cmd, sizeof cmd, "manual_save(\"%s\")", s->heSet);
        if (iocshCmd(cmd)) sg_log(c, 1, "helium autosave: %s failed", cmd);
    }
    s->inTick = 0;
}

int sgIocNextAction(sg_station *s, int *kind, double *v)
{
    sg_action a;
    if (!sg_gate_next(&s->g, &a)) return 0;
    *kind = a.kind;
    *v = a.v;
    return 1;
}

/* spec §8.3 (D1a): a failed put is a MAJOR Mismatch source while that channel's streak lasts */
void sgIocPutDone(sg_station *s, int kind, int pvStat)
{
    const char *pv[SG_NCO];
    int k;
    if (kind < 0 || kind >= SG_NCO) return;
    for (k = 0; k < SG_NCO; k++) pv[k] = s->ch[SG_GRP_OUT][k].name;
    sg_put_done(&s->c, s->putFail, pv, kind, pvStat);
}

int sgIocNamesChanged(sg_station *s)
{
    int r = s->namesChanged;
    s->namesChanged = 0;
    return r;
}

/* ================================================================ iocsh: sgIocCheck */
/* Resolves every record of a station without starting anything (no writes): the load check. */
static void sgIocCheck(const char *prefix)
{
    sg_station *s;
    if (!prefix || !*prefix) { printf("usage: sgIocCheck <prefix>\n"); return; }
    s = calloc(1, sizeof *s);
    if (!s) { printf("sgIocCheck: out of memory\n"); return; }
    copyStr(s->P, sizeof s->P, prefix);
    resolveAll(s);
    printf("sgIocCheck %s: %d addresses resolved in %d records, %d missing\n", s->P,
           s->nResolved, countRecords(s), s->nMissing);
    freeStation(s);
}

/* ================================================================ iocsh: sgIocShow */
/* Diagnostics only: the stations' channel view. Reads another thread's station without a lock
   (a torn value in a debug print is acceptable; nothing is written). */
#define SG_MAXSTATIONS 8
static sg_station *stationList[SG_MAXSTATIONS];
static epicsMutexId stationLock;

static void registerStation(sg_station *s)
{
    int i;
    if (!stationLock) return;
    epicsMutexMustLock(stationLock);
    for (i = 0; i < SG_MAXSTATIONS; i++) if (!stationList[i]) { stationList[i] = s; break; }
    epicsMutexUnlock(stationLock);
}

static void sgIocShow(const char *prefix)
{
    static const char *const grpName[SG_NGRP] = { "in", "str", "put" };
    int k, g, i, found = 0;
    if (!stationLock) return;
    epicsMutexMustLock(stationLock);
    for (k = 0; k < SG_MAXSTATIONS; k++) {
        sg_station *s = stationList[k];
        if (!s || (prefix && *prefix && strcmp(prefix, s->P) != 0)) continue;
        found = 1;
        printf("%s: %s, state %s, heartbeat %ld, writes %s, mfcConnected %d, o2Sevr %d\n", s->P,
               s->configured ? "configured" : "not configured", stateText(s->c.state),
               s->heartbeat, s->g.writeEnable ? "enabled" : "shadow", s->c.in.mfcConnected,
               s->c.in.o2Sevr);
        for (g = 0; g < SG_NGRP; g++)
            for (i = 0; i < grpSize[g]; i++) {
                sg_chan *ch = &s->ch[g][i];
                printf("  %-3s %d %-32s asg %d conn %d sevr %d ts %u.%09u ok(now) %d have %d stale %d",
                       grpName[g], i, ch->name[0] ? ch->name : "(none)", ch->assigned, ch->conn,
                       ch->sevr, ch->tsSec, ch->tsNsec, chanOkNow(s, g, i), ch->have, ch->stale);
                if (g == SG_GRP_D) printf(" v %g\n", ch->v);
                else if (g == SG_GRP_S) printf(" v \"%s\"\n", ch->s);
                else printf("\n");
            }
    }
    epicsMutexUnlock(stationLock);
    if (!found) printf("sgIocShow: no station%s%s\n", prefix && *prefix ? " " : "",
                       prefix ? prefix : "");
}

static const iocshArg chkArg0 = { "prefix", iocshArgString };
static const iocshArg *const chkArgs[] = { &chkArg0 };
static const iocshFuncDef chkDef = { "sgIocCheck", 1, chkArgs };
static void chkCall(const iocshArgBuf *a) { sgIocCheck(a[0].sval); }
static const iocshFuncDef showDef = { "sgIocShow", 1, chkArgs };
static void showCall(const iocshArgBuf *a) { sgIocShow(a[0].sval); }
static void sgIocRegister(void)
{
    if (!stationLock) stationLock = epicsMutexMustCreate();
    iocshRegister(&chkDef, chkCall);
    iocshRegister(&showDef, showCall);
}
epicsExportRegistrar(sgIocRegister);
