TOP=../..
include $(TOP)/configure/CONFIG
# C99-conforming printf on MinGW (exact decimal expansion; sgFmt relies on it)
USR_CPPFLAGS_WIN32 += -D__USE_MINGW_ANSI_STDIO=1

# Pure-C controller core, testable without an IOC
CORE_SRCS = sgFmt.c sgCore.c sgAlarms.c sgChecks.c sgPid.c sgLedger.c

TESTPROD_HOST += sgFmtTest
sgFmtTest_SRCS += sgFmtTest.c $(CORE_SRCS)
sgFmtTest_SYS_LIBS_Linux += m

# Unit tests for the core paths the replay cannot reach (spec additions, untraced branches)
TESTPROD_HOST += sgUnitTest
sgUnitTest_SRCS += sgUnitTest.c $(CORE_SRCS)
sgUnitTest_SYS_LIBS_Linux += m

# Replays the reference traces (ioc/test/golden) through the core: ioc/tools/replay.sh
TESTPROD_HOST += sgReplay
sgReplay_SRCS += sgReplay.c sgEpidSim.c $(CORE_SRCS)
sgReplay_SYS_LIBS_Linux += m

# Pure-C IOC glue pieces (write gate, PV-name rules, persistent log, report text; sgIoc.h), no
# EPICS dependency: the write gate and shadow logging (spec §8.3, §8.20), the PV-name apply rules
# (spec §8.21), the persistent monthly log (spec §8.19, §7.6), and the usage-report text (§7.7)
TESTPROD_HOST += sgIocTest
sgIocTest_SRCS += sgIocTest.c sgGate.c sgCfg.c sgLogFile.c sgText.c sgStart.c $(CORE_SRCS)
sgIocTest_SYS_LIBS_Linux += m

# ---------------------------------------------------------------- the IOC (Plan 2 task 3)
# The support library: the core, the glue and the SNL program. The SNL code must be in a library
# (a DLL on Windows): the generated registerRecordDeviceDriver code imports the SNL registrar
# (__imp_pvar_func_sampleGasRegistrar), which only a DLL can export. sgLogFileIoc.c and
# sgTextIoc.c are sgLogFile.c and sgText.c with SG_IN_IOC defined (errlogPrintf echo,
# thread-safe epicsTime_localtime); separate sources because the tests above share this
# O.<arch> directory and must link the plain ones without EPICS.
LIBRARY_IOC += sampleGasSupport
sampleGasSupport_SRCS += $(CORE_SRCS)
sampleGasSupport_SRCS += sgGate.c sgCfg.c sgLogFileIoc.c sgTextIoc.c sgPvTable.c sgStart.c
sampleGasSupport_SRCS += sgIoc.c
sampleGasSupport_SRCS += sampleGas.st
sampleGasSupport_LIBS += std calc seq pv autosave
sampleGasSupport_LIBS += $(EPICS_BASE_IOC_LIBS)
sampleGasSupport_SYS_LIBS_Linux += m

# The IOC binary and its dbd (spec §5.1, §5.2)
PROD_IOC = lssSampleGas
DBD += lssSampleGas.dbd
lssSampleGas_DBD += base.dbd
lssSampleGas_DBD += asyn.dbd
lssSampleGas_DBD += stdSupport.dbd
lssSampleGas_DBD += calcSupport.dbd
lssSampleGas_DBD += asSupport.dbd
lssSampleGas_DBD += sampleGasSeq.dbd
lssSampleGas_SRCS += lssSampleGas_registerRecordDeviceDriver.cpp
lssSampleGas_SRCS_DEFAULT += lssSampleGasMain.cpp
lssSampleGas_SRCS_vxWorks += -nil-
lssSampleGas_LIBS += sampleGasSupport std calc sscan autosave asyn seq pv
lssSampleGas_LIBS += $(EPICS_BASE_IOC_LIBS)

include $(TOP)/configure/RULES
