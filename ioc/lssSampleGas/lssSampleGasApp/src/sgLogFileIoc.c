/* sgLogFileIoc.c: sgLogFile.c as built into the IOC library, with SG_IN_IOC (every log line is
   also printed with errlogPrintf, spec §8.19). A separate source file, not a per-library CFLAGS:
   the test programs and the library are built in the same O.<arch> directory, so a shared
   sgLogFile object cannot carry the define for one and not the other. */
#define SG_IN_IOC 1
#include "sgLogFile.c"
