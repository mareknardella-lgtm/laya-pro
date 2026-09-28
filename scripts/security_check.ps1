$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) { throw 'Create .venv first.' }
Push-Location $Root
try {
    & $Python scripts/security_boundary_check.py
    if ($LASTEXITCODE -ne 0) { throw "Security Boundary Checker exited with code $LASTEXITCODE" }
} finally {
    Pop-Location
}
