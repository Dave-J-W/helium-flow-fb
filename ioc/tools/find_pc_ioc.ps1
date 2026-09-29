# find_pc_ioc.ps1: DETECT ONLY (never stops or signals anything). Lists running lssSampleGas.exe
# processes that are PC IOCs: command line containing st.cmd.pc, or the PID given with -PidFile
# (from logs-pc/ioc.pid). One line per process: "<PID> <command line>".
# Exit 0 = at least one found, 1 = none, 3 = the process list could not be read (callers must
# treat 3 as "refuse to start").
param([int]$PidFile = 0)
$ErrorActionPreference = 'Stop'
try {
    $procs = @(Get-CimInstance Win32_Process -Filter "Name='lssSampleGas.exe'")
} catch {
    Write-Output "error: cannot list processes: $($_.Exception.Message)"
    exit 3
}
$found = @()
foreach ($p in $procs) {
    $cl = [string]$p.CommandLine
    if ($cl -like '*st.cmd.pc*' -or ($PidFile -ne 0 -and $p.ProcessId -eq $PidFile)) { $found += $p }
}
foreach ($p in $found) { Write-Output ("{0} {1}" -f $p.ProcessId, $p.CommandLine) }
if ($found.Count -gt 0) { exit 0 } else { exit 1 }
