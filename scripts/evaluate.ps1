$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot
$env:SILICONFLOW_MULTI_AGENT_ENABLED = 'false'
& .\.venv\Scripts\python.exe .\scripts\evaluate.py
& .\.venv\Scripts\python.exe -m pytest -q
Write-Host "MVP evaluation passed: version-pinned deterministic, safety, negotiation, runtime, and API scenarios."
