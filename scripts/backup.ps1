param([string]$OutputDirectory = ".\backups")
$ErrorActionPreference = "Stop"
$resolvedRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$target = [System.IO.Path]::GetFullPath((Join-Path $resolvedRoot $OutputDirectory))
if (-not $target.StartsWith($resolvedRoot, [System.StringComparison]::OrdinalIgnoreCase)) { throw "Backup target must be inside the repository." }
New-Item -ItemType Directory -Force -Path $target | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$file = Join-Path $target "mealpilot-$stamp.dump"
docker compose -f docker-compose.production.yml exec -T postgres pg_dump -U mealpilot -d mealpilot -Fc -f "/backups/mealpilot-$stamp.dump"
Write-Host "Backup created: $file"
