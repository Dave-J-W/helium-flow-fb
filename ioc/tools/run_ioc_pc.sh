#!/bin/bash
# run_ioc_pc.sh: start the IOC on THIS PC against the real 15IDC enclosure (Plan 2 task 6):
#   powershell -ExecutionPolicy Bypass -File ioc/tools/msys.ps1 ioc/tools/run_ioc_pc.sh [--allow-writes] [--dry-run]
# Run it through msys.ps1 from PowerShell, not from Git Bash.
# READ-ONLY by default: the Alicat Setpoint/RampRate/Run are never connected and
# Par:writeEnable 1 is refused. --allow-writes (only with the user present and their explicit go)
# makes writes possible; they then still need Par:writeEnable 1 (every start is in shadow mode,
# FORCE_SHADOW), go only through the write gate, and only to 15IDC:Alicat1: (WRITE_MFC).
# --dry-run: every check and the environment set-up, print what would run, do not start the IOC.
#
# The only way to writes is the --allow-writes flag (fix round 1, finding 2): the flag sets
# LSS_PC_ALLOW_WRITES=1 and LSS_PC_READONLY=0, both read-only in this shell before the local file
# is sourced and re-checked before the start; st.cmd.pc takes READONLY from LSS_PC_READONLY only
# (default 1), and the glue allows writes only for READONLY exactly 0. An inherited READONLY or
# LSS_PC_* variable is discarded.
#
# CA client: the beamline address list from ioc/tools/beamline_env.local.sh (local, never
# committed; template: beamline_env.local.sh.example). CA server: forced to 127.0.0.1 (no beacons
# off the PC; st.cmd.pc sets the same again). The served prefix is the one in st.cmd.pc
# (LSSPC:...), refused if it looks like a beamline one. Refuses to start while another PC IOC
# runs (logs-pc/ioc.pid, or any lssSampleGas.exe with st.cmd.pc on its command line; detection
# only, nothing is ever stopped).
HERE="$(cd "$(dirname "$0")" && pwd)"
fail() { echo "run_ioc_pc.sh: refusing to start: $*" >&2; exit 2; }

unset READONLY LSS_PC_ALLOW_WRITES LSS_PC_READONLY
_allow=0
DRY=0
for _a in "$@"; do
  case "$_a" in
    --allow-writes) _allow=1 ;;
    --dry-run) DRY=1 ;;
    *) echo "usage: run_ioc_pc.sh [--allow-writes] [--dry-run]" >&2; exit 2 ;;
  esac
done
LSS_PC_ALLOW_WRITES=$_allow
if [ "$_allow" = 1 ]; then LSS_PC_READONLY=0; else LSS_PC_READONLY=1; fi
readonly LSS_PC_ALLOW_WRITES LSS_PC_READONLY _allow
export LSS_PC_ALLOW_WRITES LSS_PC_READONLY

# start from a clean CA/PVA environment, then the client list, then the confined server side
for _v in $(env | sed -n 's/^\(EPICS_CA[A-Z_]*\|EPICS_PVA[A-Z_]*\)=.*/\1/p'); do unset "$_v"; done
LOCAL="$HERE/beamline_env.local.sh"
[ -f "$LOCAL" ] || fail "$LOCAL is missing (copy beamline_env.local.sh.example and fill it in)"
source "$LOCAL" || fail "$LOCAL did not load"
[ -n "$EPICS_CA_ADDR_LIST" ] || fail "EPICS_CA_ADDR_LIST is empty after $LOCAL"
[ "$EPICS_CA_AUTO_ADDR_LIST" = NO ] || fail "EPICS_CA_AUTO_ADDR_LIST must be NO in $LOCAL"
unset READONLY
export EPICS_HOST_ARCH=windows-x64-mingw
export EPICS_CAS_INTF_ADDR_LIST=127.0.0.1
export EPICS_CAS_BEACON_ADDR_LIST=127.0.0.1
export EPICS_CAS_AUTO_BEACON_ADDR_LIST=NO
export EPICS_PVA_ADDR_LIST=127.0.0.1
export EPICS_PVA_AUTO_ADDR_LIST=NO
export EPICS_PVAS_INTF_ADDR_LIST=127.0.0.1
export EPICS_PVAS_BEACON_ADDR_LIST=127.0.0.1
export EPICS_PVAS_AUTO_BEACON_ADDR_LIST=NO
[ "$EPICS_CAS_INTF_ADDR_LIST" = 127.0.0.1 ] && [ "$EPICS_CAS_BEACON_ADDR_LIST" = 127.0.0.1 ] \
  && [ "$EPICS_CAS_AUTO_BEACON_ADDR_LIST" = NO ] || fail "CA server side not confined"

ST="$HERE/../lssSampleGas/iocBoot/iocLSS_sample_gas/st.cmd.pc"
P="$(sed -n 's/^epicsEnvSet("P", *"\(.*\)")$/\1/p' "$ST")"
[ -n "$P" ] || fail "no served prefix (epicsEnvSet(\"P\", ...)) found in $ST"
case "${P^^}" in 15ID*) fail "served prefix $P looks like a beamline prefix" ;; esac

# a second PC IOC? (detect only)
PIDFILE="$HERE/../lssSampleGas/iocBoot/iocLSS_sample_gas/logs-pc/ioc.pid"
_pf=0
if [ -f "$PIDFILE" ]; then _pf="$(tr -dc '0-9' < "$PIDFILE")"; [ -n "$_pf" ] || _pf=0; fi
PS=/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe
_running="$("$PS" -NoProfile -ExecutionPolicy Bypass -File "$(cygpath -w "$HERE/find_pc_ioc.ps1")" -PidFile "$_pf")"
_rc=$?
_running="${_running//$'\r'/}"
case "$_rc" in
  0) fail "a PC IOC is already running: $_running" ;;
  1) ;;
  *) fail "cannot tell whether a PC IOC is running ($_running)" ;;
esac

# the flag and the derived values, unchanged by anything sourced above
[ "$LSS_PC_ALLOW_WRITES" = "$_allow" ] || fail "LSS_PC_ALLOW_WRITES changed"
if [ "$_allow" = 1 ]; then [ "$LSS_PC_READONLY" = 0 ] || fail "LSS_PC_READONLY inconsistent"
else [ "$LSS_PC_READONLY" = 1 ] || fail "LSS_PC_READONLY inconsistent"; fi
[ -z "${READONLY+x}" ] || fail "READONLY is set in the environment"

echo "run_ioc_pc.sh: prefix $P, LSS_PC_READONLY=$LSS_PC_READONLY, CA client list: $EPICS_CA_ADDR_LIST" >&2
source "$HERE/ioc_env.sh" || exit 2
if [ "$DRY" = 1 ]; then
  env | grep '^EPICS_\|^LSS_PC_' | sort
  echo "run_ioc_pc.sh: --dry-run: would run, in $IOC_BOOT: $IOC_EXE st.cmd.pc (LSS_PC_READONLY=$LSS_PC_READONLY)"
  exit 0
fi

# start in the background of this shell with the terminal as its stdin (the iocsh console), write
# the pidfile, wait; Ctrl-C reaches the IOC, this shell keeps waiting and removes the pidfile
mkdir -p logs-pc   # the glue creates it too, but only once seq starts (after iocInit)
"$IOC_EXE" st.cmd.pc <&0 &
IOC_PID=$!
WINPID="$(cat "/proc/$IOC_PID/winpid" 2>/dev/null)"
echo "${WINPID:-$IOC_PID}" > logs-pc/ioc.pid \
  || echo "run_ioc_pc.sh: WARNING: could not write logs-pc/ioc.pid (the command-line check still guards)" >&2
trap 'true' INT TERM
rc=0
while kill -0 "$IOC_PID" 2>/dev/null; do wait "$IOC_PID"; rc=$?; done
[ "$(tr -dc '0-9' < logs-pc/ioc.pid 2>/dev/null)" = "${WINPID:-$IOC_PID}" ] && rm -f logs-pc/ioc.pid
exit "$rc"
