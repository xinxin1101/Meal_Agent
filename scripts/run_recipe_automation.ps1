param(
    [switch]$Execute
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$arguments = @((Join-Path $root "scripts\automate_meishichina.py"))
if ($Execute) {
    $arguments += @("--execute", "--acknowledge-personal-study")
}
& $python @arguments
exit $LASTEXITCODE
