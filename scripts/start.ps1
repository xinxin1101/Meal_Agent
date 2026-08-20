$ErrorActionPreference = 'Stop'

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$RuntimeDir = Join-Path $ProjectRoot '.runtime'
$StatePath = Join-Path $RuntimeDir 'dev-processes.json'
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$Vite = Join-Path $ProjectRoot 'frontend\node_modules\vite\bin\vite.js'
$BundledNode = 'C:\Users\13425\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'

if (-not (Test-Path $Python)) { throw 'Missing .venv. Run .\scripts\bootstrap.ps1 first.' }
if (-not (Test-Path $Vite)) { throw 'Missing frontend dependencies. Run pnpm install in .\frontend first.' }

if (Test-Path $BundledNode) { $Node = $BundledNode }
else {
    $NodeCommand = Get-Command node -ErrorAction SilentlyContinue
    if ($null -eq $NodeCommand) { throw 'Node.js was not found. Install Node.js first.' }
    $Node = $NodeCommand.Source
}

function Test-TcpPortOpen {
    param([int]$Port)

    $Client = [System.Net.Sockets.TcpClient]::new()
    try {
        $ConnectTask = $Client.ConnectAsync('127.0.0.1', $Port)
        if (-not $ConnectTask.Wait(500)) { return $false }
        return $Client.Connected
    } catch {
        return $false
    } finally {
        $Client.Dispose()
    }
}

function Start-MealPilotProcess {
    param([string]$FilePath, [string[]]$ProcessArguments, [string]$WorkingDirectory, [int]$Port)

    $ExistingListener = Get-ListenerProcess -Port $Port
    if ($null -ne $ExistingListener) {
        throw "Port $Port is already in use. Stop the conflicting service before starting MealPilot."
    }
    $StartInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $StartInfo.FileName = $FilePath
    $StartInfo.Arguments = [string]::Join(' ', $ProcessArguments)
    $StartInfo.WorkingDirectory = $WorkingDirectory
    $StartInfo.UseShellExecute = $false
    $StartInfo.CreateNoWindow = $true
    $StartInfo.RedirectStandardOutput = $true
    $StartInfo.RedirectStandardError = $true
    $ChildProcess = [System.Diagnostics.Process]::Start($StartInfo)
    Start-Sleep -Milliseconds 500
    $ChildProcess.Refresh()
    if ($ChildProcess.HasExited) {
        $ErrorText = $ChildProcess.StandardError.ReadToEnd()
        throw "Service on port $Port exited during startup. $ErrorText"
    }
    # Do not infer readiness by parsing netstat output: its format can vary by
    # Windows locale/version. A direct loopback TCP connection is authoritative.
    for ($Attempt = 0; $Attempt -lt 60; $Attempt++) {
        if (Test-TcpPortOpen -Port $Port) {
            $ListenerProcess = Get-ListenerProcess -Port $Port
            if ($null -ne $ListenerProcess) { return $ListenerProcess }
            return $ChildProcess
        }
        Start-Sleep -Milliseconds 250
    }

    Stop-Process -Id $ChildProcess.Id -Force -ErrorAction SilentlyContinue
    $ChildProcess.WaitForExit(1000) | Out-Null
    $OutputText = $ChildProcess.StandardOutput.ReadToEnd()
    $ErrorText = $ChildProcess.StandardError.ReadToEnd()
    throw "Service did not open port $Port within 15 seconds. Output: $OutputText Error: $ErrorText"
}

function Start-MealPilotBackgroundProcess {
    param([string]$FilePath, [string[]]$ProcessArguments, [string]$WorkingDirectory)
    $StartInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $StartInfo.FileName = $FilePath
    $StartInfo.Arguments = [string]::Join(' ', $ProcessArguments)
    $StartInfo.WorkingDirectory = $WorkingDirectory
    $StartInfo.UseShellExecute = $false
    $StartInfo.CreateNoWindow = $true
    $ChildProcess = [System.Diagnostics.Process]::Start($StartInfo)
    Start-Sleep -Milliseconds 500
    $ChildProcess.Refresh()
    if ($ChildProcess.HasExited) { throw 'Recipe acquisition worker exited during startup.' }
    return $ChildProcess
}

function Get-ListenerProcess {
    param([int]$Port)
    $Line = netstat -ano -p tcp | Select-String -Pattern (":$Port\s+.*LISTENING\s+\d+$") | Select-Object -First 1
    if ($null -eq $Line) { return $null }
    $ProcessId = [int](($Line.ToString().Trim() -split '\s+')[-1])
    return Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
}

New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null
if (Test-Path $StatePath) {
    $Existing = Get-Content $StatePath -Raw | ConvertFrom-Json
    $Running = @()
    foreach ($ProcessId in @($Existing.api.pid, $Existing.web.pid, $Existing.recipe_worker.pid)) {
        $Process = Get-Process -Id ([int]$ProcessId) -ErrorAction SilentlyContinue
        if ($null -ne $Process) { $Running += $Process }
    }
    if ($Running.Count -gt 0) { throw 'MealPilot development services are already running. Use .\scripts\stop.ps1 first.' }
    Remove-Item -LiteralPath $StatePath -Force
}

try {
    $Api = Start-MealPilotProcess -FilePath $Python -ProcessArguments @('-m', 'uvicorn', 'mealpilot.main:app', '--app-dir', 'backend', '--host', '127.0.0.1', '--port', '8000', '--no-access-log') -WorkingDirectory $ProjectRoot -Port 8000
    $Web = Start-MealPilotProcess -FilePath $Node -ProcessArguments @($Vite, '--host', '127.0.0.1', '--port', '5173', '--strictPort') -WorkingDirectory (Join-Path $ProjectRoot 'frontend') -Port 5173
    $RecipeWorker = Start-MealPilotBackgroundProcess -FilePath $Python -ProcessArguments @('-m', 'mealpilot.ingestion.worker') -WorkingDirectory $ProjectRoot
} catch {
    if ($null -ne $Api) { Stop-Process -Id $Api.Id -Force -ErrorAction SilentlyContinue }
    if ($null -ne $Web) { Stop-Process -Id $Web.Id -Force -ErrorAction SilentlyContinue }
    if ($null -ne $RecipeWorker) { Stop-Process -Id $RecipeWorker.Id -Force -ErrorAction SilentlyContinue }
    throw
}

@{ api = @{ pid = $Api.Id; port = 8000; url = 'http://127.0.0.1:8000' }; web = @{ pid = $Web.Id; port = 5173; url = 'http://127.0.0.1:5173' }; recipe_worker = @{ pid = $RecipeWorker.Id }; started_at = (Get-Date).ToUniversalTime().ToString('o') } |
    ConvertTo-Json | Set-Content -LiteralPath $StatePath -Encoding UTF8

Write-Host 'MealPilot development services started.'
Write-Host 'Frontend: http://127.0.0.1:5173'
Write-Host 'Backend:  http://127.0.0.1:8000/docs'
Write-Host 'Recipe acquisition worker: running (idle until an admin queues a job)'
Write-Host 'Stop with: .\scripts\stop.ps1'
