$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot
& .\.venv\Scripts\python.exe -m uvicorn mealpilot.main:app --app-dir backend --reload
