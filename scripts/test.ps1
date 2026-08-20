$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot
$env:SILICONFLOW_MULTI_AGENT_ENABLED = 'false'
& .\.venv\Scripts\python.exe -m pytest
