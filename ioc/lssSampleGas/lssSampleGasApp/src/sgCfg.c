/* sgCfg.c: the PV-name apply rules (spec §8.21, §7.9). Pure logic: no sg_ctl fields are
   touched here (the caller does the sg_channels_changed / sg_gate_set_enable / ledger
   re-baseline work spec §8.21 also calls for, using the mfcChanged this function returns, and
   the helium autosave sg_cfg_he_save asks for). */
#include <ctype.h>
#include <math.h>
#include <stdio.h>
#include <string.h>
#include "sgIoc.h"

static void trimInto(char *dst, size_t n, const char *src)
{
    const char *b = src, *e;
    while (*b && isspace((unsigned char)*b)) b++;
    e = b + strlen(b);
    while (e > b && isspace((unsigned char)e[-1])) e--;
    if (n == 0) return;
    if ((size_t)(e - b) >= n) e = b + n - 1;
    memcpy(dst, b, (size_t)(e - b));
    dst[e - b] = '\0';
}

static void trimNames(const sg_names *in, sg_names *out)
{
    trimInto(out->mfc, sizeof out->mfc, in->mfc);
    trimInto(out->o2, sizeof out->o2, in->o2);
    trimInto(out->cyl, sizeof out->cyl, in->cyl);
    trimInto(out->stn, sizeof out->stn, in->stn);
}

static int namesEqual(const sg_names *a, const sg_names *b)
{
    return strcmp(a->mfc, b->mfc) == 0 && strcmp(a->o2, b->o2) == 0
        && strcmp(a->cyl, b->cyl) == 0 && strcmp(a->stn, b->stn) == 0;
}

/* sg_state_names indexed directly, as sgCore.c's own (internal) stateName(); guard against a
   caller passing an out-of-range state (e.g. SG_NONE). */
static const char *stateText(int s)
{
    return (s >= 0 && s < SG_NSTATES) ? sg_state_names[s] : "\xe2\x80\x93";
}

int sg_cfg_apply(const sg_names *edit, sg_names *active, int state, int *mfcChanged,
                 char *status, size_t n)
{
    sg_names trimmed;
    int changed;

    if (state != SG_IDLE) {
        /* *mfcChanged is deliberately left untouched here (matching sgIoc.h's doc): nothing was
           decided, so there is nothing to report through it. */
        if (status && n) snprintf(status, n, "rejected: release control first (state %s)",
                                   stateText(state));
        return SG_CFG_REJECT_STATE;
    }

    trimNames(edit, &trimmed);

    if (namesEqual(&trimmed, active)) {
        if (mfcChanged) *mfcChanged = 0;
        return SG_CFG_UNCHANGED;
    }

    changed = strcmp(trimmed.mfc, active->mfc) != 0;

    if (trimmed.mfc[0] == '\0') {
        if (status && n) snprintf(status, n, "not configured: Cfg:MFC is empty");
        *active = trimmed;
        if (mfcChanged) *mfcChanged = changed;
        return SG_CFG_NOT_CONFIGURED;
    }
    if (trimmed.o2[0] == '\0') {
        if (status && n) snprintf(status, n, "not configured: Cfg:O2 is empty");
        *active = trimmed;
        if (mfcChanged) *mfcChanged = changed;
        return SG_CFG_NOT_CONFIGURED;
    }

    *active = trimmed;
    if (mfcChanged) *mfcChanged = changed;
    return SG_CFG_OK;
}

int sg_cfg_configured(const sg_names *active)
{
    return active->mfc[0] != '\0' && active->o2[0] != '\0';
}

int sg_cfg_he_save(int *pending, int mfcChanged, double lastTotal)
{
    if (mfcChanged) {                       /* the re-baselined (unset) state, at once */
        *pending = 1;
        return 1;
    }
    if (*pending && isfinite(lastTotal)) {  /* the new Alicat's reference is set */
        *pending = 0;
        return 1;
    }
    return 0;
}
