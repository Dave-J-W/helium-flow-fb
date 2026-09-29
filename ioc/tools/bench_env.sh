# Source after nothing else: confines CA to this PC and lets clients see both bench servers.
# The plant simulator (caproto) serves on port 5066; the bench IOC on 5076 (EPICS_CAS_SERVER_PORT).
# Two CA servers on one Windows host cannot share a port for unicast name searches, and 5064 is
# left to the PC IOC (run_ioc_pc.sh, LSSPC:, connected to the real beamline PVs; Plan 4 ruling
# P4-R1): nothing on the bench serves or searches 5064.
source ~/epics-sim-env.sh || {
    echo "bench_env.sh: ~/epics-sim-env.sh not found -- refusing to leave CA unconfined" >&2
    return 1 2>/dev/null || exit 1
}
export EPICS_CAS_SERVER_PORT=5076
export EPICS_CA_ADDR_LIST="127.0.0.1:5076 127.0.0.1:5066"
export EPICS_CA_AUTO_ADDR_LIST=NO
