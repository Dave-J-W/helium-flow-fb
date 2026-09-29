#!/bin/bash
# Replay the reference traces through the C core, twice: from t = 0 as the reference ran, and
# with every time shifted to epoch-like values (the IOC's `now`). --regen remakes the traces.
set -e
cd "$(dirname "$0")/.."
if [ -f ~/epics-sim-env.sh ]; then source ~/epics-sim-env.sh; fi    # the bench environment
: "${EPICS_HOST_ARCH:?set EPICS_HOST_ARCH (source the EPICS environment first)}"
if [ ! -f test/golden/summary.json ] || [ "$1" = "--regen" ]; then
  node test/ref/make_traces.js --html ../simulator/sample_gas_simulator.html --out test/golden
fi
BIN="lssSampleGas/lssSampleGasApp/src/O.$EPICS_HOST_ARCH"
"$BIN/sgReplay" --max-diffs 5 test/golden/sc*.trace
"$BIN/sgReplay" --max-diffs 5 --time-offset 1789200000 test/golden/sc*.trace
