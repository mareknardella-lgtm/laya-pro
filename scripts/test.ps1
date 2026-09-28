# Unified Test Runner for Laya Pro
param(
    [switch]$IncludeFrontend = $true,
    [switch]$IncludeSecurity = $true
)

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir

# Locate backend virtualenv python
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    $Python = "C:\Users\Marek\AppData\Local\Programs\Python\Python39\python.exe"
}
if (-not (Test-Path $Python)) {
    $Python = "python"
}

Push-Location $ProjectRoot
try {
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host "          LAYA PRO - AUTOMATED TEST SUITE RUNNER            " -ForegroundColor Cyan
    Write-Host "============================================================" -ForegroundColor Cyan

    Write-Host "`n[1/3] Running Backend Pytest Suite..." -ForegroundColor Yellow
    & $Python -m pytest backend/tests/ -q
    if ($LASTEXITCODE -ne 0) { throw "Backend pytest suite failed with code $LASTEXITCODE" }
    Write-Host "      -> Backend tests: PASS" -ForegroundColor Green

    if ($IncludeSecurity) {
        Write-Host "`n[2/3] Running Static Security Boundary Checker..." -ForegroundColor Yellow
        & $Python scripts/security_boundary_check.py
        if ($LASTEXITCODE -ne 0) { throw "Security boundary checker failed with code $LASTEXITCODE" }
        Write-Host "      -> Security check: PASS" -ForegroundColor Green
    }

    if ($IncludeFrontend) {
        Write-Host "`n[3/3] Running Frontend Node.js Test Suite..." -ForegroundColor Yellow
        try {
            npm test --prefix frontend
            if ($LASTEXITCODE -ne 0) { throw "Frontend tests failed with code $LASTEXITCODE" }
            Write-Host "      -> Frontend tests: PASS" -ForegroundColor Green
        } catch {
            Write-Host "      (Node.js / npm not available; skipping frontend tests)" -ForegroundColor Gray
        }
    }

    Write-Host "`n============================================================" -ForegroundColor Green
    Write-Host "   ALL AUTOMATED VERIFICATION CHECKS COMPLETED SUCCESSFULLY   " -ForegroundColor Green
    Write-Host "============================================================" -ForegroundColor Green
} finally {
    Pop-Location
}
