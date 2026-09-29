#!/bin/bash
# run_ioc.sh: start the BENCH IOC (iocBoot/iocLSS_sample_gas/st.cmd) on the MinGW bench.
#   powershell -ExecutionPolicy Bypass -File ioc/tools/msys.ps1 ioc/tools/run_ioc.sh [startup-file]
# Run it through msys.ps1 from PowerShell (NOT from Git Bash: Git Bash's HOME hides
# ~/epics-sim-env.sh). Build first (ioc/tools/build.sh). READONLY=1 in the environment starts the
# station read-only. startup-file defaults to st.cmd (relative to the iocBoot directory).
#
# Refuses to start (exit 2) unless Channel Access and PV Access are confined to this PC: the IOC
# is a CA server, and one started with the default environment beacons to the whole network.
HERE="$(cd "$(dirname "$0")" && pwd)"
fail() { echo "run_ioc.sh: refusing to start: $*" >&2; exit 2; }

source "$HERE/bench_env.sh" || fail "the bench environment (ioc/tools/bench_env.sh) did not load"
[ "$EPICS_CA_AUTO_ADDR_LIST" = NO ] || fail "EPICS_CA_AUTO_ADDR_LIST is not NO"
[ "$EPICS_CAS_INTF_ADDR_LIST" = 127.0.0.1 ] || fail "EPICS_CAS_INTF_ADDR_LIST is not 127.0.0.1"
[ "$EPICS_CAS_BEACON_ADDR_LIST" = 127.0.0.1 ] || fail "EPICS_CAS_BEACON_ADDR_LIST is not 127.0.0.1"
[ "${EPICS_CAS_AUTO_BEACON_ADDR_LIST^^}" = NO ] || fail "EPICS_CAS_AUTO_BEACON_ADDR_LIST is not NO"
[ -n "$EPICS_CA_ADDR_LIST" ] || fail "EPICS_CA_ADDR_LIST is empty"
for h in $EPICS_CA_ADDR_LIST; do
  [ "${h%%:*}" = 127.0.0.1 ] || fail "EPICS_CA_ADDR_LIST has a host other than 127.0.0.1 ($h)"
  [ "${h##*:}" != 5064 ] || fail "EPICS_CA_ADDR_LIST reaches port 5064 (the PC IOC's; P4-R1)"
done
# 5064 belongs to the PC IOC (run_ioc_pc.sh); the bench IOC serves on 5076 (bench_env.sh)
[ -n "$EPICS_CAS_SERVER_PORT" ] && [ "$EPICS_CAS_SERVER_PORT" != 5064 ] \
  || fail "EPICS_CAS_SERVER_PORT is unset or 5064 (the PC IOC's port)"
[ "${EPICS_PVA_AUTO_ADDR_LIST^^}" = NO ] || fail "EPICS_PVA_AUTO_ADDR_LIST is not NO"
for h in $EPICS_PVA_ADDR_LIST; do
  [ "${h%%:*}" = 127.0.0.1 ] || fail "EPICS_PVA_ADDR_LIST has a host other than 127.0.0.1 ($h)"
done
[ "$EPICS_PVAS_INTF_ADDR_LIST" = 127.0.0.1 ] || fail "EPICS_PVAS_INTF_ADDR_LIST is not 127.0.0.1"
[ "$EPICS_PVAS_BEACON_ADDR_LIST" = 127.0.0.1 ] || fail "EPICS_PVAS_BEACON_ADDR_LIST is not 127.0.0.1"
[ "${EPICS_PVAS_AUTO_BEACON_ADDR_LIST^^}" = NO ] || fail "EPICS_PVAS_AUTO_BEACON_ADDR_LIST is not NO"

source "$HERE/ioc_env.sh" || exit 2
exec "$IOC_EXE" "${1:-st.cmd}"
