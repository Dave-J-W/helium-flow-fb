#!/bin/bash
# linux_build_check.sh: build the IOC on Linux the way the beamline host will (gcc, linux-x86_64,
# the production module versions) and run its tests there. Meant for WSL Ubuntu 22.04 (gcc 11,
# like the host's 11.5); everything goes under $PREFIX (default ~/epics-check), nothing else is
# touched.
#
#   sudo ioc/tools/linux_build_check.sh deps           # once: build packages (apt)
#   PACKAGE=<lssSampleGas.tar.gz> linux_build_check.sh all  # base + modules (first time ~30-40 min)
#                                                            # + IOC + tests
#   PACKAGE=<lssSampleGas.tar.gz> linux_build_check.sh ioc  # rebuild only the IOC + tests
#
# PACKAGE is the tarball of docs/ioc/INSTALL.md section 1B (made on the repo PC with git archive,
# LF endings); the IOC is built from it with configure/RELEASE.local as INSTALL.md describes. The smoke start runs the
# BENCH start-up (SIM: prefix) with Channel Access confined to 127.0.0.1 inside Linux.
set -euo pipefail
PREFIX="${PREFIX:-$HOME/epics-check}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
SUP="$PREFIX/support"
JOBS="$(nproc)"
export EPICS_HOST_ARCH=linux-x86_64

MODULES=(   # dir name (as in configure/RELEASE) | GitHub repo | tag
  "sequencer-mirror-R2-2-9 mdavidsaver/sequencer-mirror R2-2-9"
  "asyn-R4-44-2 epics-modules/asyn R4-44-2"
  "sscan-R2-11-6 epics-modules/sscan R2-11-6"
  "calc-R3-7-5 epics-modules/calc R3-7-5"
  "autosave-R5-11 epics-modules/autosave R5-11"
  "std-R3-6-4 epics-modules/std R3-6-4"
)

deps() {
  apt-get update -q
  apt-get install -y -q build-essential perl libreadline-dev re2c git rsync libtirpc-dev \
    pkg-config ca-certificates curl
}

base() {
  mkdir -p "$PREFIX"
  [ -d "$PREFIX/base" ] || git clone -q --depth 1 --branch R7.0.8.1 --recurse-submodules \
    https://github.com/epics-base/epics-base.git "$PREFIX/base"
  make -C "$PREFIX/base" -j"$JOBS" > "$PREFIX/base.log" 2>&1 || { tail -30 "$PREFIX/base.log"; exit 1; }
  echo "base: built"
}

modules() {
  mkdir -p "$SUP"
  # shared by all (each module includes $(TOP)/../RELEASE.local); each module's own dependencies go
  # in its configure/RELEASE.local, so no module sees one that isn't built yet (asyn with CALC set
  # tries to build its sCalcout support)
  printf 'SUPPORT=%s\nEPICS_BASE=%s\n' "$SUP" "$PREFIX/base" > "$SUP/RELEASE.local"
  declare -A DEPS=(
    [sequencer-mirror-R2-2-9]=""
    [asyn-R4-44-2]="SNCSEQ=sequencer-mirror-R2-2-9"
    [sscan-R2-11-6]="SNCSEQ=sequencer-mirror-R2-2-9"
    [calc-R3-7-5]="SNCSEQ=sequencer-mirror-R2-2-9 SSCAN=sscan-R2-11-6"
    [autosave-R5-11]=""
    [std-R3-6-4]="SNCSEQ=sequencer-mirror-R2-2-9 ASYN=asyn-R4-44-2"
  )
  for m in "${MODULES[@]}"; do
    set -- $m
    local dir="$SUP/$1"
    [ -d "$dir" ] || git clone -q --depth 1 --branch "$3" "https://github.com/$2.git" "$dir"
    # drop the stale absolute paths in the module's own RELEASE
    sed -i -E '/^(SUPPORT|EPICS_BASE|SNCSEQ|ASYN|SSCAN|CALC|AUTOSAVE|STD|IPAC|BUSY)=/d' \
      "$dir/configure/RELEASE"
    : > "$dir/configure/RELEASE.local"
    for d in ${DEPS[$1]}; do echo "${d%%=*}=\$(SUPPORT)/${d#*=}" >> "$dir/configure/RELEASE.local"; done
    case "$1" in
      asyn-*) echo "TIRPC=YES" > "$dir/configure/CONFIG_SITE.local" ;;   # glibc has no Sun RPC
    esac
    make -C "$dir" -j"$JOBS" > "$PREFIX/$1.log" 2>&1 || { tail -30 "$PREFIX/$1.log"; exit 1; }
    echo "$1: built"
  done
}

ioc() {
  local top="$PREFIX/ChemMat/lssSampleGas"
  rm -rf "$top"
  mkdir -p "$PREFIX/ChemMat"
  # the package made per docs/ioc/INSTALL.md section 1B (git archive on the repo PC): a worktree's
  # .git points at a Windows path, which git in Linux can't follow
  [ -f "${PACKAGE:-}" ] || { echo "set PACKAGE=<lssSampleGas.tar.gz>" >&2; exit 2; }
  tar -xzf "$PACKAGE" -C "$PREFIX/ChemMat"
  if grep -rlI $'\r' "$top" > /dev/null; then echo "package has CR line endings" >&2; exit 1; fi
  printf 'SUPPORT=%s\nEPICS_BASE=%s\n' "$SUP" "$PREFIX/base" > "$top/configure/RELEASE.local"
  echo "IOC: package $PACKAGE"
  make -C "$top" -j"$JOBS" > "$PREFIX/ioc.log" 2>&1 || { tail -40 "$PREFIX/ioc.log"; exit 1; }
  echo "IOC: built; warnings in its own sources:"
  grep -E "lssSampleGasApp/src/[^ ]+:[0-9]+:[0-9]+: warning" "$PREFIX/ioc.log" | sort -u | head -40 || true
  local t="$top/lssSampleGasApp/src/O.linux-x86_64"
  echo "--- tests"
  "$t/sgUnitTest" | tail -1
  (cd "$t" && ./sgIocTest | tail -1)
  "$t/sgFmtTest" | tail -1
  if [ -d "$REPO/ioc/test/golden" ]; then
    (cd "$REPO/ioc" && "$t/sgReplay" --max-diffs 5 test/golden/sc*.trace | tail -2 &&
     "$t/sgReplay" --max-diffs 5 --time-offset 1789200000 test/golden/sc*.trace | tail -1)
  else
    echo "replay: no traces at $REPO/ioc/test/golden (make them on the bench first)"
  fi
  smoke "$top"
}

smoke() {   # start the bench start-up for ~20 s, read the heartbeat and TickAge twice, exit
  local boot="$1/iocBoot/iocLSS_sample_gas"
  export EPICS_CA_AUTO_ADDR_LIST=NO EPICS_CA_ADDR_LIST=127.0.0.1 \
         EPICS_CAS_INTF_ADDR_LIST=127.0.0.1 EPICS_CAS_BEACON_ADDR_LIST=127.0.0.1 \
         EPICS_CAS_AUTO_BEACON_ADDR_LIST=NO EPICS_PVA_AUTO_ADDR_LIST=NO \
         EPICS_PVA_ADDR_LIST=127.0.0.1 EPICS_CA_MAX_ARRAY_BYTES=100000
  echo "--- smoke start (bench st.cmd, SIM: prefix, localhost only; no simulator, so it waits)"
  (cd "$boot" && mkdir -p autosave logs &&
   # t = 15 and 20 s: inside the 30 s connection wait (heartbeat must advance, TickAge stay 0-1);
   # t = 40 s: ticking, with the MAJOR "waiting for the Alicat" and no clock catch-up
   { sleep 15; echo 'dbgf SIM:SampleGas:Sts:Heartbeat'; echo 'dbgf SIM:SampleGas:Sts:TickAge';
     sleep 5;  echo 'dbgf SIM:SampleGas:Sts:Heartbeat'; echo 'dbgf SIM:SampleGas:Sts:TickAge';
     sleep 20; echo 'dbgf SIM:SampleGas:Sts:Heartbeat'; echo 'dbgf SIM:SampleGas:Sts:TickAge';
     echo 'dbgf SIM:SampleGas:Sts:WorstSevr'; echo exit; } |
   timeout 90 ../../bin/linux-x86_64/lssSampleGas st.cmd 2>&1 |
   grep -E "DBF_|waiting|PV names|behind|skipped|FATAL|Error|error" | head -20)
}

case "${1:-all}" in
  deps) deps ;;
  base) base ;;
  modules) modules ;;
  ioc) ioc ;;
  all) base; modules; ioc ;;
  *) echo "usage: $0 deps|base|modules|ioc|all" >&2; exit 2 ;;
esac
