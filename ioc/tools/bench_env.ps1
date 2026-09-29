# Dot-source after nothing else: confines CA/PVA to this PC and lets clients see both bench
# servers. The plant simulator (caproto) serves on port 5066; the bench IOC on 5076
# (EPICS_CAS_SERVER_PORT). Two CA servers on one Windows host cannot share a port for unicast name
# searches, and 5064 is left to the PC IOC (run_ioc_pc.sh, LSSPC:, real beamline PVs; Plan 4
# ruling P4-R1): nothing on the bench serves or searches 5064.
#
# This is the PowerShell equivalent of ioc/tools/bench_env.sh + ~/epics-sim-env.sh: every
# variable ~/epics-sim-env.sh sets, plus the three overrides bench_env.sh adds. Keep the two files
# in sync if either changes.
#
# Usage: . .\ioc\tools\bench_env.ps1

$env:EPICS_HOST_ARCH = 'windows-x64-mingw'
$env:EPICS_CA_ADDR_LIST = '127.0.0.1'
$env:EPICS_CA_AUTO_ADDR_LIST = 'NO'
$env:EPICS_CAS_INTF_ADDR_LIST = '127.0.0.1'
$env:EPICS_CAS_BEACON_ADDR_LIST = '127.0.0.1'
$env:EPICS_CAS_AUTO_BEACON_ADDR_LIST = 'NO'
$env:EPICS_PVA_ADDR_LIST = '127.0.0.1'
$env:EPICS_PVA_AUTO_ADDR_LIST = 'NO'
$env:EPICS_PVAS_INTF_ADDR_LIST = '127.0.0.1'
$env:EPICS_PVAS_BEACON_ADDR_LIST = '127.0.0.1'
$env:EPICS_PVAS_AUTO_BEACON_ADDR_LIST = 'NO'

# bench_env.sh overrides, so CA clients on this PC can reach both the plant simulator (5066)
# and the bench IOC (5076):
$env:EPICS_CAS_SERVER_PORT = '5076'
$env:EPICS_CA_ADDR_LIST = '127.0.0.1:5076 127.0.0.1:5066'
$env:EPICS_CA_AUTO_ADDR_LIST = 'NO'

Write-Host 'EPICS bench env: CA/PVA confined to 127.0.0.1 (CA searches 127.0.0.1:5076 and :5066)'
