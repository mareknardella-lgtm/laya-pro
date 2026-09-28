# Windows Launch Script for Laya Pro
param(
    [string]$BackendHost = "127.0.0.1",
    [int]$BackendPort = 8765,
    [switch]$NoLayaDaemon,
    [switch]$Stop
)

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$SupervisorScript = Join-Path $ScriptDir "run_backend.ps1"

if ($Stop) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File $SupervisorScript -StopChildren
    exit 0
}

$SupervisorArgs = @(
    "-BackendHost", $BackendHost,
    "-BackendPort", "$BackendPort"
)

if ($NoLayaDaemon.IsPresent) {
    $SupervisorArgs += "-NoLayaDaemon"
}

& powershell -NoProfile -ExecutionPolicy Bypass -File $SupervisorScript @SupervisorArgs

if ($LASTEXITCODE -ne 0) {
    throw "L'avvio del backend è fallito con codice $LASTEXITCODE."
}