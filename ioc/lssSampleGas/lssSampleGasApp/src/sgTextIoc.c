/* sgTextIoc.c: sgText.c as built into the IOC library, with SG_IN_IOC (thread-safe
   epicsTime_localtime). A separate source for the same reason as sgLogFileIoc.c: the tests share
   this O.<arch> directory and link sgText.c without EPICS. */
#define SG_IN_IOC 1
#include "sgText.c"
