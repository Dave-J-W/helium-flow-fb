#include <stdio.h>
#include <string.h>
#include <math.h>
#include "sgFmt.h"

static int fails = 0;
static void eq(const char *got, const char *want, const char *what) {
    if (strcmp(got, want) != 0) { printf("FAIL %s: got '%s' want '%s'\n", what, got, want); fails++; }
}
int main(void) {
    char b[64];
    eq(sg_fmtN(b, sizeof b, 0.125, 2), "0.13", "toFixed tie rounds up");        /* printf gives 0.12 */
    eq(sg_fmtN(b, sizeof b, 2.5, 0), "3", "toFixed tie 2.5");
    eq(sg_fmtN(b, sizeof b, 1.005, 2), "1.00", "toFixed 1.005 is below the tie in binary");
    eq(sg_fmtN(b, sizeof b, -0.001, 2), "-0.00", "toFixed keeps the sign");
    eq(sg_fmtN(b, sizeof b, 0.0, 1), "0.0", "toFixed zero");
    eq(sg_fmtN(b, sizeof b, 19.4449, 1), "19.4", "toFixed plain");
    eq(sg_fmtN(b, sizeof b, NAN, 2), "\xE2\x80\x93", "fmtN NaN is an en dash");
    eq(sg_fmtN(b, sizeof b, 1e21, 2), "1e+21", "toFixed falls back to ToString above 1e21");
    eq(sg_fmtN(b, sizeof b, -2.5e22, 1), "-2.5e+22", "toFixed fallback keeps the sign");
    eq(sg_fmtJs(b, sizeof b, 2.0), "2", "String(2)");
    eq(sg_fmtJs(b, sizeof b, 0.8), "0.8", "String(0.8)");
    eq(sg_fmtJs(b, sizeof b, 0.1 + 0.2), "0.30000000000000004", "String(0.1+0.2)");
    eq(sg_fmtJs(b, sizeof b, 8.8e-4), "0.00088", "String(8.8e-4)");
    eq(sg_fmtJs(b, sizeof b, 1e-5), "0.00001", "String(1e-5)");
    eq(sg_fmtJs(b, sizeof b, 1e-7), "1e-7", "String(1e-7)");
    eq(sg_fmtJs(b, sizeof b, 216000), "216000", "String(216000)");
    eq(sg_fmtJs(b, sizeof b, -9.3), "-9.3", "String(-9.3)");
    eq(sg_fmtJs(b, sizeof b, 1e21), "1e+21", "String(1e21)");
    eq(sg_fmtT(b, sizeof b, 3725.4), "1:02:05", "fmtT");
    eq(sg_fmtT(b, sizeof b, -5), "0:00:00", "fmtT negative");
    eq(sg_fmtDur(b, sizeof b, 5.5), "5.5 h", "fmtDur < 10 h");
    eq(sg_fmtDur(b, sizeof b, 30.4), "30 h", "fmtDur 10-48 h");
    eq(sg_fmtDur(b, sizeof b, 132), "5.5 d", "fmtDur days");
    printf("%s: %d failure(s)\n", fails ? "FAIL" : "PASS", fails);
    return fails ? 1 : 0;
}
