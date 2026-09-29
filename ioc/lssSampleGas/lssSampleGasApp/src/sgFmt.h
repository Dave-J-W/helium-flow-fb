/* sgFmt.h: formatting that reproduces the reference's JavaScript output byte for byte. */
#ifndef SGFMT_H
#define SGFMT_H
#include <stddef.h>
/* Number.prototype.toFixed(d): exact decimal expansion, ties away from zero.
   Non-finite x gives "–" (U+2013), as the reference's fmtN. Returns buf. */
char *sg_fmtN(char *buf, size_t n, double x, int d);
/* String(x): shortest round-trip digits; decimal notation when the decimal exponent e of x
   (x = d.ddd × 10^e) satisfies -6 <= e < 21, i.e. 1e-6 <= |x| < 1e21 ("0.000001"), else
   "1e-7" / "1.5e+21" style. NaN -> "NaN". Returns buf. */
char *sg_fmtJs(char *buf, size_t n, double x);
/* fmtT: h:mm:ss of round(max(0, s)). */
char *sg_fmtT(char *buf, size_t n, double s);
/* fmtDur (hours): "–" if non-finite; < 48 h: "5.5 h" (1 decimal below 10 h, else 0); else "5.5 d". */
char *sg_fmtDur(char *buf, size_t n, double h);
#endif
