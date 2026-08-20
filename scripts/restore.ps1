param([Parameter(Mandatory = $true)][string]$BackupFile)
$ErrorActionPreference = "Stop"
$resolvedRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$resolvedBackup = (Resolve-Path $BackupFile).Path
$backupRoot = [System.IO.Path]::GetFullPath((Join-Path $resolvedRoot "backups"))
if (-not $resolvedBackup.StartsWith($backupRoot, [System.StringComparison]::OrdinalIgnoreCase)) { throw "Restore source must be inside the repository backups directory." }
$name = Split-Path $resolvedBackup -Leaf
Write-Warning "This replaces the MealPilot PostgreSQL database with $name. Stop API and worker before continuing."
$answer = Read-Host "Type RESTORE to continue"
if ($answer -ne "RESTORE") { throw "Restore cancelled." }
docker compose -f docker-compose.production.yml exec -T postgres pg_restore -U mealpilot -d mealpilot --clean --if-exists "/backups/$name"
Write-Host "Restore completed. Restart api and worker, then verify /ready."
