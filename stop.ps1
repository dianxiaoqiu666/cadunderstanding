$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
& .venv\Scripts\python.exe -B scripts/stop.py
exit $LASTEXITCODE
