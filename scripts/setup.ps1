param([string]$SourceProject)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
New-Item -ItemType Directory -Force -Path '.tmp','.cache' | Out-Null
$env:TEMP = Join-Path $projectRoot '.tmp'
$env:TMP = $env:TEMP
$env:PIP_CACHE_DIR = Join-Path $projectRoot '.cache\pip'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONDONTWRITEBYTECODE = '1'
if ($SourceProject) {
    $sourcePython = Join-Path $SourceProject '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $sourcePython)) { throw 'Source Python environment is missing.' }
    & $sourcePython -B scripts/migrate_dependencies.py --source $SourceProject
    if ($LASTEXITCODE -ne 0) { throw 'Source dependency migration failed.' }
    & .venv\Scripts\python.exe -m pip install --requirement requirements.lock.txt
} else {
    if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
        python -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed.' }
    }
    & .venv\Scripts\python.exe -m pip install --requirement requirements.lock.txt
}
if ($LASTEXITCODE -ne 0) { throw 'Dependency setup failed.' }
& .venv\Scripts\python.exe -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Dependency verification failed.' }
Write-Output 'Ready. Start with: powershell -ExecutionPolicy Bypass -File .\start.ps1'
