/* sampleGas.st: the SNL shell of the 15LSS_sample_gas controller (Plan 2 task 3).

   One instance per station:
     seq sampleGas, "P=<prefix>,READONLY=0[,WRITE_MFC=<Alicat prefix>][,FORCE_SHADOW=1]
                     [,LOGDIR=<dir>][,HESET=<req>]"
   READONLY must be exactly 0 for writes to be possible (anything else, or absent, = read-only:
   the Alicat Setpoint, RampRate and Run channels are never assigned, no put is ever made, and
   Par:writeEnable 1 is refused). WRITE_MFC pins the one Alicat writes may go to; FORCE_SHADOW=1
   starts with Par:writeEnable 0 (sgIoc.h, sgIocCreate).
   All controller logic is in C (sgIoc.c, and the core behind it, sgCore.h); this program owns
   only what needs Channel Access: the remote channels of spec §6 (Alicat, O2 analyzer, cylinder
   pressure), assigned at run time with pvAssign from the station's Cfg:* names (spec §8.21), the
   1 Hz wall-clock tick (spec §8.1) and the gated Alicat puts (spec §8.3, §8.20).

   Options:
   +s  safe mode (implies +r, reentrant): every station has its own copy of these variables, and
       the monitored values change only at the tick's synchronisation point, so one tick works
       on one consistent snapshot (spec §8.1). In safe mode an empty assign is an anonymous
       channel: pvAssigned() is false and pvConnected() true, so the feed passes both.
   -c  do not wait for the channels before starting: the init state does the spec §8.15 wait
       (at most 30 s) itself, and a station with empty names must still start. */
program sampleGas
option +s;
option -c;

%%#include "sgIoc.h"

/* Remote inputs, index = the SG_CD_* / SG_CS_* enums of sgIoc.h:
   dIn  0 O2, 1 Flow_RBV, 2 Setpoint_RBV, 3 RampRate_RBV, 4 Total_RBV, 5 Running_RBV, 6 CYL
   sIn  0 Gas_RBV (state string), 1 Status, 2 FlowUnits_RBV */
double dIn[7];
assign dIn to {"", "", "", "", "", "", ""};
monitor dIn;
string sIn[3];
assign sIn to {"", "", ""};
monitor sIn;
/* Alicat puts, enum sg_act order: 0 Setpoint, 1 RampRate, 2 Run. Never monitored. */
double dOut[3];
assign dOut to {"", "", ""};

typename sg_station *stn;   /* this instance's station: all controller state lives there */
double dt;                  /* seconds to the next tick */
int kind;
double val;
int waited;                 /* seconds of the start-up connection wait so far */

ss ctl {
    state init {
        entry {
            stn = sgIocCreate(macValueGet("P"), macValueGet("LOGDIR"), macValueGet("HESET"),
                              macValueGet("READONLY"), macValueGet("WRITE_MFC"),
                              macValueGet("FORCE_SHADOW"));
            if (stn) assignChannels();
        }
        when (!stn) {
            printf("sampleGas: station not started (see the messages above)\n");
        } exit
        /* spec §8.15 step 1: connected (with a first value), or ... sgIocTouch only names dIn
           and sIn in the condition: seq wakes a state set on a monitor event only for the
           channels its conditions reference, and ready() hides them in a function, so without
           it only the connect events (which come before the first values) would wake us. */
        when (sgIocTouch(dIn, sIn) && ready()) {
            sgIocWaitEnd(stn);
            dt = sgIocSecondsToNextTick(stn);
        } state tick
        when (delay(1.0)) {
            waited = 1;
            sgIocWaitBeat(stn);
        } state wait
    }

    /* ... or 30 s, with a heartbeat every second meanwhile (Sts:TickAge, spec §13.4); a state of
       its own so the self-transitions restart the 1 s delay without re-running init's entry */
    state wait {
        when (sgIocTouch(dIn, sIn) && ready()) {
            sgIocWaitEnd(stn);
            dt = sgIocSecondsToNextTick(stn);
        } state tick
        when (delay(1.0) && waited < 29) {
            waited++;
            sgIocWaitBeat(stn);
        } state wait
        when (delay(1.0)) {                         /* ... 30 s: the missing ones are invalid */
            feedInputs();
            sgIocWaitEnd(stn);
            dt = sgIocSecondsToNextTick(stn);
        } state tick
    }

    state tick {
        when (delay(dt)) {
            feedInputs();
            sgIocTick(stn);
            while (sgIocNextAction(stn, &kind, &val)) {   /* drained fully, every tick (P2-R6) */
                if (!sgIocWritesPermitted(stn)) continue;  /* never a put (none can be queued) */
                dOut[kind] = val;
                sgIocPutDone(stn, kind, pvPut(dOut[kind], SYNC, 2.0));
            }
            if (sgIocNamesChanged(stn)) assignChannels();
            dt = sgIocSecondsToNextTick(stn);
        } state tick
    }
}

/* SNL functions (seq 2.2) come after the state sets: defined in the initial section, snc 2.2.9
   leaves their references to the program variables untranslated (a C compile error in
   reentrant / safe mode), in the final section it translates them. */

/* (Re)assign every channel whose name the station says changed ("" = unassigned). */
void assignChannels()
{
    int k;
    for (k = 0; k < 7; k++)
        if (sgIocChanReassign(stn, SG_GRP_D, k)) pvAssign(dIn[k], sgIocChanName(stn, SG_GRP_D, k));
    for (k = 0; k < 3; k++)
        if (sgIocChanReassign(stn, SG_GRP_S, k)) pvAssign(sIn[k], sgIocChanName(stn, SG_GRP_S, k));
    for (k = 0; k < 3; k++)                 /* read-only: the put channels are never assigned */
        if (sgIocChanReassign(stn, SG_GRP_OUT, k) && !sgIocReadOnly(stn))
            pvAssign(dOut[k], sgIocChanName(stn, SG_GRP_OUT, k));
}

/* This tick's snapshot of every channel into the station. */
void feedInputs()
{
    int k;
    for (k = 0; k < 7; k++)
        sgIocInputD(stn, k, dIn[k], pvAssigned(dIn[k]), pvConnected(dIn[k]), pvSeverity(dIn[k]),
                    pvTimeStamp(dIn[k]).secPastEpoch, pvTimeStamp(dIn[k]).nsec);
    for (k = 0; k < 3; k++)
        sgIocInputS(stn, k, sIn[k], pvAssigned(sIn[k]), pvConnected(sIn[k]), pvSeverity(sIn[k]),
                    pvTimeStamp(sIn[k]).secPastEpoch, pvTimeStamp(sIn[k]).nsec);
    for (k = 0; k < 3; k++)
        sgIocInputPut(stn, k, pvAssigned(dOut[k]), pvConnected(dOut[k]));
}

int ready()
{
    feedInputs();
    return sgIocReady(stn);
}
