# Run a bash script under MSYS2 MINGW64 from the repo root: powershell -File ioc/tools/msys.ps1 <script> [args]
param([Parameter(Mandatory=$true)][string]$Script, [Parameter(ValueFromRemainingArguments=$true)]$Rest)
$env:MSYSTEM = 'MINGW64'; $env:CHERE_INVOKING = '1'
& C:\msys64\usr\bin\bash.exe -l $Script @Rest
exit $LASTEXITCODE
