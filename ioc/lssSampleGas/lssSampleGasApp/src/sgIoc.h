/* sgIoc.h: pure-C glue pieces used by the IOC (SNL) that sit between it and the verified
   controller core (sgCore.h): the write gate and shadow logging (spec §8.3, §8.20), the PV-name
   apply rules (spec §8.21), the persistent monthly log (spec §8.19, §7.6), and the usage-report
   text (spec §7.7 He:Rep:Text). No EPICS dependency; sgLogFile.c is the only file that touches
   errlogPrintf, and only when SG_IN_IOC is defined (the IOC library build defines it; the test
   build does not). Plan 2 task 2. */
#ifndef SGIOC_H
#define SGIOC_H
#include <stddef.h>
#include "sgCore.h"

/* ---------------------------------------------------------------- write gate (spec §8.3, §8.20)
   The glue's single gated put function: the IOC's io.put_setpoint / put_ramp / put_run
   implementations call sg_gate_request instead of writing the Alicat directly.

   Field ownership, every tick (safety-critical -- read this before touching either field):
   - g->mfcConnected is set from the current tick's in.mfcConnected, through
     sg_gate_set_connected, before any sg_gate_request call in that tick (sgCore.h's contract:
     treat the MFC as connected in the same tick in.mfcConnected says so). Never assign it
     directly: sg_gate_set_connected is what empties the queue on a disconnect (P2-R6).
   - g->writeEnable is set exactly ONCE, at start, by sg_gate_init (from the autosaved
     Par:writeEnable, before sg_restart). After that it changes ONLY through
     sg_gate_set_enable -- never assign it directly from Par:writeEnable on a later tick. Doing
     so would skip the 0 -> 1 transition's log line and its IDLE entry (spec §8.20, §14.3b): the
     controller would start writing a shadow-computed setpoint to the Alicat with no operator
     confirmation and no valve-left-where-it-is guarantee. */
enum sg_act { SG_ACT_SP, SG_ACT_RAMP, SG_ACT_RUN };
typedef struct { int kind; double v; } sg_action;
typedef struct {
    sg_action q[16]; int n;
    int writeEnable, mfcConnected;
    char lastShadowText[3][32]; int haveShadow[3];  /* dedup of shadow log lines per PV, compared
                                                         on the formatted text actually logged (a
                                                         NaN streak, or a value that rounds to the
                                                         same text, logs once) */
} sg_gate;
/* Start-up initialization: writeEnable = the autosaved Par:writeEnable, everything else zeroed
   (empty queue, no dedup state). No log, no sg_enter -- this is "the IOC came up already in this
   mode", not a transition, so it must not fire §8.20's 0->1 side effects. Call once, before
   sg_restart, and never call sg_gate_set_enable to establish the initial value. */
void sg_gate_init(sg_gate *g, int writeEnable);
/* Every tick, before the tick's first sg_gate_request, with that tick's in.mfcConnected (P2-R6).
   A 1 -> 0 transition empties the queue: an action queued before a disconnect is never handed
   out after the reconnect, even if the queue was not drained while disconnected. */
void sg_gate_set_connected(sg_gate *g, int connected);
/* Queue the action, collapsing by kind (kind must be one of enum sg_act; other values are
   ignored):
   - writeEnable && mfcConnected: if an action of this kind is already queued, overwrite its
     value in place (same queue position) with the newest v; otherwise append. Because there are
     only 3 kinds, the queue can never hold more than 3 entries and overflow cannot occur -- a
     fast-changing Setpoint can never crowd out a pending Run or RampRate, and the final value
     handed to sg_gate_next for a kind is always the newest one requested.
   - writeEnable && !mfcConnected: dropped silently. Not shadow mode (writes are enabled); this
     is a disconnected Alicat, which the core's own §8.18 alarm already reports. Logging
     "shadow mode: would write" here would be actively misleading.
   - !writeEnable: log "shadow mode: would write <PV> = <v>" (v formatted %.2f) for SG_ACT_SP /
     SG_ACT_RAMP, or "shadow mode: would write Run" (no value) for SG_ACT_RUN, but only when the
     formatted text differs from the last text logged for that kind. */
void sg_gate_request(sg_gate *g, sg_ctl *c, int kind, double v);
/* Pop the oldest queued action into *out, FIFO order. Re-checks writeEnable && mfcConnected at
   pop time (not just at enqueue time): if either is false now, the queue is emptied and this
   returns 0, however many actions were queued -- an action queued while writes were enabled must
   never be handed out after a later 1 -> 0 (or a disconnect), including onto a newly pvAssign'd
   Alicat after an §8.21 MFC-name change. Otherwise returns 1, or 0 when already empty. */
int  sg_gate_next(sg_gate *g, sg_action *out);
/* Par:writeEnable transition (spec §8.20). en must differ from g->writeEnable's current value to
   have any effect (a no-op call with en == the current state does nothing, no log, no queue or
   dedup change). Establish the *initial* value with sg_gate_init instead, never with this.
   0 -> 1: log "writes enabled: Alicat left at <sp:.2f> SLPM", then sg_enter(c, SG_IDLE,
           "writes enabled") (which logs the state transition itself); no action is queued -- the
           valve is left where it is.
   1 -> 0: empty the queue (belt-and-suspenders with sg_gate_next's own re-check) and reset the
           shadow dedup (so shadow mode logs fresh from this point, not against a value logged
           long before this switch); log MAJOR "writes disabled: shadow mode, Alicat holds
           <sp:.2f> SLPM"; the state is not changed. */
void sg_gate_set_enable(sg_gate *g, sg_ctl *c, int en, double sp);

/* ---------------------------------------------------------------- PV names (spec §8.21, §7.9) */
typedef struct { char mfc[40], o2[40], cyl[40], stn[40]; } sg_names;
enum { SG_CFG_OK, SG_CFG_UNCHANGED, SG_CFG_REJECT_STATE, SG_CFG_NOT_CONFIGURED };
/* Apply edited names over the active ones. Trims leading/trailing whitespace from every field of
   *edit before comparing or storing. Decides, in order:
     1. state != SG_IDLE            -> SG_CFG_REJECT_STATE, status "rejected: release control
        first (state <STATE>)", *active and *mfcChanged untouched.
     2. trimmed edit == *active     -> SG_CFG_UNCHANGED, status left untouched (the caller already
        has "PV names unchanged" to log), *mfcChanged = 0, *active untouched.
     3. trimmed mfc or o2 is empty  -> SG_CFG_NOT_CONFIGURED, status "not configured: Cfg:<MFC or
        O2> is empty" (MFC checked first), *active updated to the trimmed names, *mfcChanged set
        from whether mfc changed.
     4. otherwise                   -> SG_CFG_OK, status left untouched (the caller fills in the
        "applied <time>: ..." text once it knows the connection state), *active updated to the
        trimmed names, *mfcChanged set from whether mfc changed.
   mfcChanged and status may be NULL. status, when written, is NUL-terminated within n bytes (no
   write at all if n == 0). */
int  sg_cfg_apply(const sg_names *edit, sg_names *active, int state, int *mfcChanged,
                  char *status, size_t n);
/* MFC and O2 both non-empty. */
int  sg_cfg_configured(const sg_names *active);
/* Helium autosave around an Alicat PV change (spec §8.21 step 5 with §8.15). The helium set is
   otherwise saved at most every 300 s, so an IOC restart soon after a Cfg:MFC change restored the
   OLD Alicat's LastTotal, CylBase and usage log against the new totalizer: the difference was
   counted as usage (a CumL jump of the whole totalizer) or logged as "went backwards" (bench
   acceptance test_pvnames, 2026-09-29). Call once per tick, after the ledger: mfcChanged = a
   changed Cfg:MFC was applied this tick, lastTotal = the ledger's reference (sg_ctl.lastTotal).
   Returns 1 when the helium set must be saved now: on the change itself (the re-baselined, unset
   state) and again on the first later tick with a finite lastTotal (the new Alicat's reference).
   *pending carries the second save between ticks. */
int  sg_cfg_he_save(int *pending, int mfcChanged, double lastTotal);

/* ---------------------------------------------------------------- persistent log (spec §8.19,
   §7.6) */
#define SG_LOGRING 200
typedef struct {
    char dir[256]; char stn[40]; char ring[SG_LOGRING][320]; int head, count;
    int writeFailLatched;   /* 1 while a file-write failure streak is already reported */
} sg_logfile;
/* dir is the directory the monthly files live in (e.g. the iocBoot logs/ directory); it is not
   created here. stn is the station label used in the file name and every line. */
void sg_logfile_init(sg_logfile *L, const char *dir, const char *stn);
/* Formats "YYYY-MM-DD hh:mm:ss  <STN>  [MINOR|MAJOR]  <msg>" (sev 0 leaves 5 blank columns where
   MINOR/MAJOR would go, as the reference's log view) using epochT's local time, appends it
   (fflush'd) to <dir>/sampleGas_<STN>_YYYY-MM.log (the month of epochT's local time), adds it to
   the ring (newest overwrites the oldest past SG_LOGRING lines), and, when built into the IOC
   (SG_IN_IOC defined), prints it with errlogPrintf. If localtime fails on epochT (never observed,
   only a defensive fallback), the timestamp becomes the placeholder "????-??-?? ??:??:??" and the
   file name's month becomes "unknown" -- the line is still written and still added to the ring;
   it is never silently dropped. A file-write (fopen) error is reported to stderr once per failure
   streak (latched in writeFailLatched, cleared the next time a write succeeds) and otherwise
   ignored -- this function never aborts. */
void sg_logfile_line(sg_logfile *L, double epochT, int sev, const char *msg);
/* Newest first, lines '\n'-joined, NUL-terminated within n bytes. Returns strlen(buf). */
size_t sg_logfile_text(const sg_logfile *L, char *buf, size_t n);

/* ---------------------------------------------------------------- texts */
/* He:Rep:Text (spec §7.7): the reference's Admin usage text (sample_gas_simulator.html lines
   1979-1985), with dates "YYYY-MM-DD hh:mm" (local time) in place of the reference's
   "day N.NN". NUL-terminated within n bytes. */
void sg_report_text(const sg_ctl *c, const sg_report *r, char *buf, size_t n);

/* ---------------------------------------------------------------- start-up (sgStart.c)
   User direction 2026-09-28: the failure state is not doing anything, so the controller never
   settles into a silent IDLE for a missing Alicat, and every "cannot act" state is an alarm. */
enum { SG_START_RESTART, SG_START_WAIT, SG_START_NOTCONF };
/* Before the first sg_restart, every tick: NOTCONF (station not configured: IDLE "restart: not
   configured"), WAIT (configured but the Alicat is not connected or Setpoint_RBV not finite:
   do not call sg_restart yet, it would park the controller in IDLE for good), RESTART (run the
   normal spec §8.15 decision now; an invalid O2 does not hold it: that gives OPEN_LOOP). */
int  sg_start_decision(int configured, const sg_inputs *in);
/* While waiting: once, Alm:Mismatch MAJOR "waiting for the Alicat PVs (<names>): controller not
   acting yet" (logged MAJOR by sg_set_alarm). *raised is the caller's latch. sg_restart's reinit
   clears the alarm when the wait ends. */
void sg_start_wait_alarm(sg_ctl *c, int *raised, const char *names);
/* A station that cannot act because it is not configured, while writes are enabled: on = 1 raises
   Alm:Mismatch MAJOR "not configured: <text>" (text e.g. "Cfg:MFC is empty"); on = 0 clears it
   silently if this function raised it. The core never runs on an unconfigured station, so it
   does not compete for Mismatch there. */
void sg_not_configured_alarm(sg_ctl *c, int *raised, int on, const char *text);

/* ================================================================ the station (IOC only)
   sgIoc.c, built only into the IOC library (sampleGasSupport): one sg_station per `seq sampleGas`
   instance, i.e. per station prefix $(P). It owns the core (sg_ctl), the write gate, the
   persistent log and every DBADDR of the station's own records, which it reads and writes with
   dbAccess (never CA). The SNL program (sampleGas.st) owns the Channel Access side: it assigns the
   remote channels (Alicat, O2, cylinder) with pvAssign, feeds their values, connection states,
   severities and time stamps in once per tick, and makes the gated Alicat puts the station hands
   out through sgIocNextAction. All calls on one station come from its own SNL thread; stations
   share no controller state (the statics in sgIoc.c are const tables plus the mutex-protected
   station list that the diagnostic iocsh command sgIocShow reads).

   Per tick, from the SNL:  sgIocInput* for every channel -> sgIocTick -> drain sgIocNextAction
   (pvPut SYNC each, report through sgIocPutDone) -> if sgIocNamesChanged: pvAssign every
   channel sgIocChanReassign says -> sgIocSecondsToNextTick for the next delay. */
typedef struct sg_station sg_station;

/* Remote channels, by group. SG_GRP_OUT is in enum sg_act order (Setpoint, RampRate, Run). */
enum { SG_GRP_D, SG_GRP_S, SG_GRP_OUT, SG_NGRP };
enum { SG_CD_O2, SG_CD_FLOW, SG_CD_SP, SG_CD_RAMP, SG_CD_TOTAL, SG_CD_RUNNING, SG_CD_CYL, SG_NCD };
enum { SG_CS_GAS, SG_CS_STATUS, SG_CS_UNITS, SG_NCS };
#define SG_NCO 3

/* Resolves every record of the station (prefix = $(P)) with dbNameToAddr; a missing record is
   fatal: each missing name is printed and NULL returned. Then reads the start-up state the
   autosave restore left in the records (Cfg:* names, Par:*, Mode, Mode:*, Diag:OverrideCount,
   He:*, Par:writeEnable) into the core and the gate (sgCore.h "First tick"). logDir: directory
   of the monthly log files (NULL = "logs", created if missing). heliumSet: the autosave request
   file of the helium set, for manual_save after NewCylinder / MarkNewRun (NULL =
   "sampleGas_helium.req"). */
sg_station *sgIocCreate(const char *prefix, const char *logDir, const char *heliumSet,
                        const char *readOnly, const char *writeMfc, const char *forceShadow);
/* readOnly (macro READONLY; Plan 2 task 6): read-only only when given and not "0"; NULL or empty
   = not read-only (user direction 2026-09-28: write locks for the PC trial only, and a missing
   macro must not leave production silently unable to act; st.cmd.pc always passes it
   explicitly, READONLY=$(LSS_PC_READONLY=1)). Read-only: the three
   Alicat put channels get no name (the SNL never assigns them), they are not required for
   mfcConnected, and Par:writeEnable 0 -> 1 is refused (put back to 0, MAJOR "read-only IOC:
   writes cannot be enabled").
   writeMfc (macro WRITE_MFC, NULL/empty = no pin): writes only while the applied Cfg:MFC is
   exactly this prefix; otherwise the same as read-only (no put channel names, writeEnable 1
   refused with MAJOR "writes refused: Alicat <active> is not the permitted <WRITE_MFC>").
   forceShadow (macro FORCE_SHADOW, "1" = on): Par:writeEnable is set to 0 at start whatever
   autosave restored, so a restart never resumes writing without the operator switching Live. */
int         sgIocReadOnly(sg_station *s);
int         sgIocWritesPermitted(sg_station *s);   /* not read-only and WRITE_MFC satisfied */
void        sgIocNames(sg_station *s, sg_names *active);                /* the applied names */
/* Full PV name for channel idx of group grp ("" = leave unassigned: empty Cfg field). */
const char *sgIocChanName(sg_station *s, int grp, int idx);
/* 1 if this channel must be (re)assigned now; clears the flag. All are set at create. */
int         sgIocChanReassign(sg_station *s, int grp, int idx);
/* The SNL's view of each channel this tick: value, pvAssigned, pvConnected, pvSeverity and
   pvTimeStamp (a zero time stamp = nothing received yet). */
void        sgIocInputD(sg_station *s, int idx, double v, int assigned, int connected, int sevr,
                        unsigned tsSec, unsigned tsNsec);
void        sgIocInputS(sg_station *s, int idx, const char *v, int assigned, int connected,
                        int sevr, unsigned tsSec, unsigned tsNsec);
void        sgIocInputPut(sg_station *s, int idx, int assigned, int connected);
/* Spec §8.15 step 1: 1 when the O2 and every Alicat channel are connected and have delivered a
   value (or the station is not configured: nothing to wait for). */
int         sgIocReady(sg_station *s);
/* Returns 1. For the SNL only: naming the monitored arrays in a when() condition puts their
   monitor events into that state's wake-up mask (see sampleGas.st, state init). */
int         sgIocTouch(const void *a, const void *b);
void        sgIocWaitEnd(sg_station *s);          /* logs the outcome of the wait, Cfg:Status */
/* Seconds until the next tick (a whole wall-clock second); schedules that tick's `now`. */
double      sgIocSecondsToNextTick(sg_station *s);
/* One tick, spec §8.1 order (see sgIoc.c). */
void        sgIocTick(sg_station *s);
/* Next gated Alicat put (kind = enum sg_act), 0 when none is left. Drain fully every tick. */
int         sgIocNextAction(sg_station *s, int *kind, double *v);
void        sgIocPutDone(sg_station *s, int kind, int pvStat);           /* pvPut's status */
int         sgIocNamesChanged(sg_station *s);     /* 1 once after an applied Cfg:Apply */

#endif
