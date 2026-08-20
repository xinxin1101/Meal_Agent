$ErrorActionPreference = 'Stop'

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$StatePath = Join-Path $ProjectRoot '.runtime\dev-processes.json'

if (-not (Test-Path $StatePath)) {
    Write-Host 'No MealPilot development process state was found.'
    exit 0
}

$State = Get-Content $StatePath -Raw | ConvertFrom-Json
foreach ($ServiceName in @('api', 'web', 'recipe_worker')) {
    if ($null -eq $State.$ServiceName) { continue }
    $ProcessId = [int]$State.$ServiceName.pid
    $Process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($null -ne $Process) {
        Stop-Process -Id $ProcessId -Force
        Write-Host "Stopped $ServiceName (PID $ProcessId)."
    } else {
        Write-Host "$ServiceName (PID $ProcessId) was already stopped."
    }
}

Remove-Item -LiteralPath $StatePath -Force
