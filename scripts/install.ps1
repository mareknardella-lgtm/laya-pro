# Windows Installation Script for Laya Pro
$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "         LAYA PRO - WINDOWS ENVIRONMENT INSTALLER           " -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

# 1. Resolve Python 3.9+ or 3.11+
$PythonExe = $null
if (Test-Path "$ProjectRoot\.venv\Scripts\python.exe") {
    $PythonExe = "$ProjectRoot\.venv\Scripts\python.exe"
} else {
    $pyCandidates = @(
        "C:\Users\Marek\AppData\Local\Programs\Python\Python39\python.exe",
        "py",
        "python"
    )
    foreach ($c in $pyCandidates) {
        if ($c -eq "py" -or $c -eq "python") {
            try {
                $ver = & $c -V 2>&1
                if ($LASTEXITCODE -eq 0) { $PythonExe = $c; break }
            } catch {}
        } elseif (Test-Path $c) {
            $PythonExe = $c
            break
        }
    }
}

if (-not $PythonExe) {
    throw "Python executable not found. Please install Python 3.9 or higher and add to PATH."
}

Write-Host "Using Python launcher: $PythonExe" -ForegroundColor Green

# 2. Create virtual environment if missing
$VenvDir = Join-Path $ProjectRoot ".venv"
if (-not (Test-Path $VenvDir)) {
    Write-Host "Creating virtual environment at $VenvDir ..." -ForegroundColor Yellow
    if ($PythonExe -eq "py") {
        & py -3 -m venv $VenvDir
    } else {
        & $PythonExe -m venv $VenvDir
    }
}

$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    throw "Failed to locate virtual environment interpreter at $VenvPython"
}

# 3. Upgrade pip and install dependencies
Write-Host "Upgrading pip and installing requirements..." -ForegroundColor Yellow
& $VenvPython -m pip install --upgrade pip -q
& $VenvPython -m pip install -r (Join-Path $ProjectRoot "requirements.txt") -q
& $VenvPython -m pip install -e (Join-Path $ProjectRoot "backend[test]") -q

# 4. Initialize .env if missing
$EnvFile = Join-Path $ProjectRoot ".env"
$EnvExample = Join-Path $ProjectRoot ".env.example"
if (-not (Test-Path $EnvFile) -and (Test-Path $EnvExample)) {
    Write-Host "Creating .env from .env.example ..." -ForegroundColor Yellow
    Copy-Item $EnvExample $EnvFile
}

Write-Host "`nInstallation successfully completed!" -ForegroundColor Green
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "  1. Review and edit .env with your operator token and project roots."
Write-Host "  2. Run '.\scripts\test.ps1' to verify the environment."
Write-Host "  3. Run '.\scripts\start.ps1' to launch the supervised application."
