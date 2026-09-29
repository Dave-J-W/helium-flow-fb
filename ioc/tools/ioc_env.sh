# ioc_env.sh: sourced by run_ioc.sh and run_ioc_pc.sh AFTER their CA checks. cd's to the iocBoot
# directory, rewrites envPaths' MSYS paths (/home/...) to Windows ones (C:/msys64/home/...) that
# the native IOC can read, puts base's, the support modules' and the install's
# bin/<arch> directories on PATH (the IOC's DLLs), and sets IOC_EXE. Does not start anything.
IOC_BOOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../lssSampleGas/iocBoot/iocLSS_sample_gas" && pwd)"
cd "$IOC_BOOT" || return 1
if [ ! -f envPaths ]; then
  echo "ioc_env.sh: $IOC_BOOT/envPaths missing: build first (ioc/tools/build.sh)" >&2
  return 1
fi
sed -i 's#"/home/#"C:/msys64/home/#' envPaths
_arch="${EPICS_HOST_ARCH:-windows-x64-mingw}"
_path=""
for _v in TOP EPICS_BASE SNCSEQ STD CALC ASYN AUTOSAVE SSCAN; do
  _w="$(sed -n "s/^epicsEnvSet(\"$_v\",\"\\(.*\\)\")\$/\\1/p" envPaths)"
  [ -n "$_w" ] || continue
  _u="$(cygpath -u "$_w")"
  [ "$_v" = TOP ] && IOC_TOP="$_u"
  [ -d "$_u/bin/$_arch" ] && _path="$_path:$_u/bin/$_arch"
done
export PATH="${_path#:}:$PATH"
IOC_EXE="$IOC_TOP/bin/$_arch/lssSampleGas.exe"
if [ ! -x "$IOC_EXE" ]; then
  echo "ioc_env.sh: $IOC_EXE missing: build first (ioc/tools/build.sh)" >&2
  return 1
fi
unset _arch _path _v _w _u
