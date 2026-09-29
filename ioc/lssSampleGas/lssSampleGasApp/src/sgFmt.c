/* sgFmt.c: formatting that reproduces the reference's JavaScript output byte for byte. */
/* C99-conforming printf on MinGW ("%.60f" must give the exact decimal expansion). The Makefile
   sets it too; this keeps the file correct when built on its own. */
#ifndef __USE_MINGW_ANSI_STDIO
#define __USE_MINGW_ANSI_STDIO 1
#endif
#include "sgFmt.h"
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

/* Number.prototype.toFixed(d): round the exact decimal expansion of |x| at d decimal
 * places, ties away from zero (never banker's rounding, which a plain "%.*f" printf
 * can apply). "%.60f" under the MinGW ANSI printf gives the exact decimal expansion
 * of the double (well within 60 places for any double), which this then rounds by
 * hand. The sign is taken from x < 0 directly (not from the rounded magnitude), so
 * e.g. (-0.001).toFixed(2) keeps its "-" even though the rounded value is "0.00",
 * while -0.0 (x < 0 is false) prints "0.00".
 *
 * Per ECMA-262 Number::toFixed: if |x| >= 10^21, the digit count d is ignored and
 * the result is just ToString(x) (e.g. (1e21).toFixed(2) === "1e+21",
 * (-2.5e22).toFixed(1) === "-2.5e+22") -- delegate to sg_fmtJs, which already
 * produces sign-prefixed ToString output.
 */
char *sg_fmtN(char *buf, size_t n, double x, int d)
{
    if (n == 0) return buf;
    if (!isfinite(x)) {
        snprintf(buf, n, "\xE2\x80\x93");
        return buf;
    }
    if (fabs(x) >= 1e21) return sg_fmtJs(buf, n, x);
    if (d < 0) d = 0;
    if (d > 90) d = 90; /* keep the fixed decimal-digit buffer below safely bounded */

    int neg = (x < 0.0);
    double ax = fabs(x);

    char tmp[400];
    snprintf(tmp, sizeof tmp, "%.60f", ax);

    char *dot = strchr(tmp, '.');
    size_t intLen = dot ? (size_t)(dot - tmp) : strlen(tmp);

    char decPart[96];
    size_t decLen = 0;
    if (dot) {
        decLen = strlen(dot + 1);
        if (decLen > sizeof(decPart) - 1) decLen = sizeof(decPart) - 1;
        memcpy(decPart, dot + 1, decLen);
    }
    decPart[decLen] = '\0';
    while (decLen < (size_t)d + 1 && decLen < sizeof(decPart) - 1) {
        decPart[decLen++] = '0';
        decPart[decLen] = '\0';
    }

    /* intPart reserves one spare byte at [0] in case rounding carries a new leading digit. */
    char intPart[340];
    size_t off = 1;
    if (intLen > sizeof(intPart) - 2) intLen = sizeof(intPart) - 2;
    memcpy(intPart + off, tmp, intLen);
    intPart[off + intLen] = '\0';

    int roundUp = (decPart[d] >= '5' && decPart[d] <= '9');
    if (roundUp) {
        int i = d - 1;
        int carry = 1;
        while (i >= 0 && carry) {
            if (decPart[i] == '9') { decPart[i] = '0'; i--; }
            else { decPart[i]++; carry = 0; }
        }
        if (carry) {
            int k = (int)(off + intLen) - 1;
            while (k >= (int)off && carry) {
                if (intPart[k] == '9') { intPart[k] = '0'; k--; }
                else { intPart[k]++; carry = 0; }
            }
            if (carry) {
                off--;
                intPart[off] = '1';
                intLen++;
            }
        }
    }

    char out[420];
    size_t pos = 0;
    if (neg) out[pos++] = '-';
    memcpy(out + pos, intPart + off, intLen);
    pos += intLen;
    if (d > 0) {
        out[pos++] = '.';
        memcpy(out + pos, decPart, (size_t)d);
        pos += (size_t)d;
    }
    out[pos] = '\0';

    snprintf(buf, n, "%s", out);
    return buf;
}

/* String(x): the shortest decimal digit string that round-trips to x, per ECMA-262
 * Number::toString. We search precisions p = 1..17 significant digits (17 always
 * round-trips any double) via "%.*e", parsing back with strtod, and take the first
 * exact match. That gives a sign, a digit string D (trailing zeros stripped) and an
 * exponent e such that |x| = 0.D[0]D[1]...  * 10^(e+1). Decimal notation is used for
 * -6 <= e < 21 (matching real toString: 1e-6 -> "0.000001" but 1e-7 -> "1e-7", and
 * 1e21 -> "1e+21"); otherwise exponential "D[0]"."rest"e"+/-"exp.
 */
char *sg_fmtJs(char *buf, size_t n, double x)
{
    if (n == 0) return buf;
    if (isnan(x)) { snprintf(buf, n, "NaN"); return buf; }
    if (isinf(x)) { snprintf(buf, n, x > 0.0 ? "Infinity" : "-Infinity"); return buf; }
    if (x == 0.0) { snprintf(buf, n, "0"); return buf; }

    int neg = (x < 0.0);
    double ax = fabs(x);

    char tmp[40];
    int p;
    for (p = 1; p <= 17; p++) {
        snprintf(tmp, sizeof tmp, "%.*e", p - 1, ax);
        if (strtod(tmp, NULL) == ax) break;
    }

    char *epos = strchr(tmp, 'e');
    int e = atoi(epos + 1);

    char D[24];
    size_t dlen = 0;
    for (char *c = tmp; c < epos; c++) {
        if (*c != '.') D[dlen++] = *c;
    }
    D[dlen] = '\0';
    while (dlen > 1 && D[dlen - 1] == '0') D[--dlen] = '\0';

    char out[64];
    size_t pos = 0;
    if (neg) out[pos++] = '-';

    if (e >= -6 && e < 21) {
        if (e >= (int)dlen - 1) {
            memcpy(out + pos, D, dlen); pos += dlen;
            int zeros = e - ((int)dlen - 1);
            for (int z = 0; z < zeros; z++) out[pos++] = '0';
        } else if (e >= 0) {
            memcpy(out + pos, D, (size_t)e + 1); pos += (size_t)e + 1;
            out[pos++] = '.';
            size_t rest = dlen - (size_t)(e + 1);
            memcpy(out + pos, D + e + 1, rest); pos += rest;
        } else {
            out[pos++] = '0';
            out[pos++] = '.';
            int zeros = -e - 1;
            for (int z = 0; z < zeros; z++) out[pos++] = '0';
            memcpy(out + pos, D, dlen); pos += dlen;
        }
    } else {
        out[pos++] = D[0];
        if (dlen > 1) {
            out[pos++] = '.';
            memcpy(out + pos, D + 1, dlen - 1); pos += dlen - 1;
        }
        out[pos++] = 'e';
        out[pos++] = (e < 0) ? '-' : '+';
        char ebuf[8];
        int ae = e < 0 ? -e : e;
        int elen = snprintf(ebuf, sizeof ebuf, "%d", ae);
        memcpy(out + pos, ebuf, (size_t)elen); pos += (size_t)elen;
    }
    out[pos] = '\0';

    snprintf(buf, n, "%s", out);
    return buf;
}

/* fmtT: h:mm:ss of round(max(0, s)). */
char *sg_fmtT(char *buf, size_t n, double s)
{
    double t = (s > 0.0) ? s : 0.0;
    long total = (long)round(t);
    long h = total / 3600;
    long m = (total % 3600) / 60;
    long sec = total % 60;
    snprintf(buf, n, "%ld:%02ld:%02ld", h, m, sec);
    return buf;
}

/* fmtDur (hours): matches the reference's fmtDur. */
char *sg_fmtDur(char *buf, size_t n, double h)
{
    if (!isfinite(h)) {
        snprintf(buf, n, "\xE2\x80\x93");
        return buf;
    }
    char num[64];
    if (h < 48.0) {
        sg_fmtN(num, sizeof num, h, (h < 10.0) ? 1 : 0);
        snprintf(buf, n, "%s h", num);
    } else {
        sg_fmtN(num, sizeof num, h / 24.0, 1);
        snprintf(buf, n, "%s d", num);
    }
    return buf;
}
