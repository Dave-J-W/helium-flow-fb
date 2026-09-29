/* devEpidSoft emulation, identical to the reference's processEpid (line 1459). Test use only:
   in the IOC the real epid record does this. */
#ifndef SGEPIDSIM_H
#define SGEPIDSIM_H
#include "sgCore.h"
typedef struct { double I, P, D, ePrev, lastT, OVAL; int fbonPrev; } sg_epid_sim;
void   sg_epid_sim_reset(sg_epid_sim *e);   /* I = P = D = ePrev = OVAL = 0, lastT NaN, fbonPrev 0 */
/* One processing. outl = the Setpoint record VAL (bumpless source). Returns the new OVAL. */
double sg_epid_sim_process(sg_epid_sim *e, const sg_epid_cfg *cfg, int fbon, double now,
                           double pidScan, double outl);
#endif
