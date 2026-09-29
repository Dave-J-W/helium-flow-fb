#!/bin/bash
# Build the lssSampleGas top on the MinGW bench. The repo path has a space, which GNU make cannot
# handle, so the build runs through a junction without spaces: ~/bench/<worktree>-lssSampleGas ->
# this worktree's ioc/lssSampleGas. The junction name is per-worktree (P2-R5): more than one
# worktree of this repo can exist side by side (e.g. o2-purge-plan2, o2-purge-plan3), and a single
# shared junction name would silently build whichever worktree it was last pointed at. If the
# junction already exists but points somewhere else (a stale junction from a different worktree,
# or one left over before this per-worktree naming), it is recreated -- but only if it really is
# a junction: this script must never rm -rf a real directory that happens to sit at $LINK (the
# KNOWN LIMITATION's rsync fallback below makes $LINK a real, non-space directory on purpose).
set -e
source ~/epics-sim-env.sh
REPO_TOP="$(cd "$(dirname "$0")/../lssSampleGas" && pwd -W)"      # C:/Users/.../ioc/lssSampleGas
REPO_TOP_POSIX="$(cd "$(dirname "$0")/../lssSampleGas" && pwd)"
LINK="$HOME/bench/$(basename "$(cd "$(dirname "$0")/../.." && pwd)")-lssSampleGas"
# `-e` alone misses a dangling junction (target removed but the reparse point still there: `-e`
# follows it and reports false); `-L` catches the reparse point itself either way.
if [ -e "$LINK" ] || [ -L "$LINK" ]; then
  if [ -L "$LINK" ]; then
    TARGET="$(readlink -f "$LINK" 2>/dev/null || echo "")"
    if [ "$TARGET" != "$REPO_TOP_POSIX" ]; then
      echo "build.sh: $LINK points at '$TARGET', not this worktree ('$REPO_TOP_POSIX'); recreating" >&2
      # Removes the junction (reparse point) only, never its target: verified on a throwaway
      # link/target pair that the target's contents survive this. Also removes a dangling
      # junction (no target to touch). Refuses (non-empty error) on a real, non-empty directory,
      # which cannot happen here since the `-L` guard above already excluded real directories.
      cmd //c rmdir "$(cygpath -w "$LINK")"
    fi
  else
    echo "build.sh: $LINK exists and is a real directory, not a junction -- refusing to remove" \
         "it automatically (it may be the rsync fallback's tree, or something unrelated left" \
         "there by hand). Move or delete it yourself, then re-run." >&2
    exit 1
  fi
fi
if [ ! -e "$LINK" ]; then
  cmd //c mklink //J "$(cygpath -w "$LINK")" "$(cygpath -w "$REPO_TOP")"
fi
cd "$LINK"
# The junction alone is not enough once the IOC links against its own library: GNU make's
# $(abspath ...) and `pwd -P` both resolve a junction to its real target (confirmed with
# `make -f /dev/stdin` printing $(abspath .) from inside $LINK: the space-containing repo path),
# and base's PROD_DEPLIB_DIRS / SHRLIB_DEPLIB_DIRS (configure/os/CONFIG.Common.UnixCommon) build the
# -L flags with $(abspath $(INSTALL_LIB)/...), which the shell then word-splits at the space.
# WHAT WORKS (Plan 2 task 3, verified: lssSampleGas.exe links against sampleGasSupport):
# INSTALL_LOCATION outside the repo, in configure/CONFIG_SITE.local (gitignored; the committed
# CONFIG_SITE.local.example documents it). Every install path, and so every -L flag, is then
# space-free, while the sources and the O.<arch> build directories stay in the repo. The file is
# written here, per worktree, when it is missing; an existing one is left alone.
# (Documented fallback, not needed: replace the mklink block above with an
# `rsync -a --delete "$REPO_TOP/" "$LINK/"` copy, after removing the junction, so $LINK is a
# real, space-free directory instead of a reparse point pointing back at the repo.)
INSTALL_DIR="$LINK-install"
CSL="configure/CONFIG_SITE.local"
if [ ! -f "$CSL" ]; then
  echo "build.sh: writing $CSL (INSTALL_LOCATION=$INSTALL_DIR)" >&2
  printf '%s\n' "# written by ioc/tools/build.sh (see CONFIG_SITE.local.example)" \
                "INSTALL_LOCATION=$INSTALL_DIR" "CONFIG_INSTALLS =" > "$CSL"
fi
mkdir -p "$INSTALL_DIR"
make "$@"
