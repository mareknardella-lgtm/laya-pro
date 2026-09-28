$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) { throw 'Create .venv and install backend test dependencies first.' }
Push-Location $Root
try {
    & $Python -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw "pytest exited with code $LASTEXITCODE" }
    & $Python -m compileall -q backend/app backend/tests
    if ($LASTEXITCODE -ne 0) { throw "compileall exited with code $LASTEXITCODE" }
} finally {
    Pop-Location
}
