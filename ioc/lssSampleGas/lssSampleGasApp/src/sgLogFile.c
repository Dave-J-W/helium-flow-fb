/* sgLogFile.c: the persistent monthly log (spec §8.19, §7.6). Every call to sg_logfile_line
   appends one flushed line to the month's file (so the log survives an IOC restart, per §8.19),
   keeps a newest-first ring for Diag:Log:Text (sg_logfile_text), and, when built into the IOC
   (SG_IN_IOC defined by the IOC library build, not by the test build), echoes it with
   errlogPrintf. A file-write error is reported once per failure streak, to stderr, and otherwise
   ignored: this module never aborts the IOC over a logging problem, and never silently drops a
   line even if its own timestamp cannot be computed. */
#include <stdio.h>
#include <string.h>
#include <time.h>
#include "sgIoc.h"
#ifdef SG_IN_IOC
#include <errlog.h>
#include <epicsTime.h>
#endif

/* Local time into *out; 0 on failure. In the IOC (two stations = two threads) the thread-safe
   epicsTime_localtime; in the tests (one thread) plain localtime. */
static int localTm(time_t t, struct tm *out)
{
#ifdef SG_IN_IOC
    return epicsTime_localtime(&t, out) == epicsTimeOK;
#else
    struct tm *p = localtime(&t);
    if (!p) return 0;
    *out = *p;
    return 1;
#endif
}

void sg_logfile_init(sg_logfile *L, const char *dir, const char *stn)
{
    snprintf(L->dir, sizeof L->dir, "%s", dir);
    snprintf(L->stn, sizeof L->stn, "%s", stn);
    L->head = 0;
    L->count = 0;
    L->writeFailLatched = 0;
}

/* Month rollover is by the line's own timestamp (local time, as the rest of the IOC log), not by
   wall-clock at the moment sg_logfile_line runs: no open file handle is held between calls, so
   there is nothing to roll over -- each line just opens (and closes) the file for its own month. */
void sg_logfile_line(sg_logfile *L, double epochT, int sev, const char *msg)
{
    time_t tt = (time_t)epochT;
    struct tm tmBuf;
    const char *sevStr;
    char ts[40], ym[24];
    char line[320];
    char path[600];
    FILE *f;

    if (localTm(tt, &tmBuf)) {
        snprintf(ts, sizeof ts, "%04d-%02d-%02d %02d:%02d:%02d", tmBuf.tm_year + 1900,
                 tmBuf.tm_mon + 1, tmBuf.tm_mday, tmBuf.tm_hour, tmBuf.tm_min, tmBuf.tm_sec);
        snprintf(ym, sizeof ym, "%04d-%02d", tmBuf.tm_year + 1900, tmBuf.tm_mon + 1);
    } else {
        /* Defensive fallback only (not observed on the bench): never silently drop the line just
           because its own timestamp could not be computed. */
        snprintf(ts, sizeof ts, "\?\?\?\?-\?\?-\?\? \?\?:\?\?:\?\?");   /* \? avoids trigraphs */
        snprintf(ym, sizeof ym, "unknown");
    }

    sevStr = (sev == 1) ? "MINOR" : (sev == 2) ? "MAJOR" : "";
    snprintf(line, sizeof line, "%s  %s  %-5s  %s", ts, L->stn, sevStr, msg);

    snprintf(path, sizeof path, "%s/sampleGas_%s_%s.log", L->dir, L->stn, ym);
    f = fopen(path, "a");
    if (f) {
        fprintf(f, "%s\n", line);
        fflush(f);
        fclose(f);
        L->writeFailLatched = 0;               /* the failure streak, if any, just ended */
    } else if (!L->writeFailLatched) {
        fprintf(stderr, "sgLogFile: cannot open '%s' for append\n", path);
        L->writeFailLatched = 1;               /* report once per streak, not every line */
    }

    snprintf(L->ring[L->head], sizeof L->ring[0], "%s", line);
    L->head = (L->head + 1) % SG_LOGRING;
    if (L->count < SG_LOGRING) L->count++;

#ifdef SG_IN_IOC
    errlogPrintf("%s\n", line);
#endif
}

size_t sg_logfile_text(const sg_logfile *L, char *buf, size_t n)
{
    size_t len = 0;
    int i;
    if (n == 0) return 0;
    buf[0] = '\0';
    for (i = 0; i < L->count && len < n - 1; i++) {
        int idx = ((L->head - 1 - i) % SG_LOGRING + SG_LOGRING) % SG_LOGRING;   /* newest first */
        int w = snprintf(buf + len, n - len, "%s%s", i ? "\n" : "", L->ring[idx]);
        if (w < 0) break;
        len += (size_t)w;
        if (len >= n - 1) { len = strlen(buf); break; }
    }
    return len;
}
