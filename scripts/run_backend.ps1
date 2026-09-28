# Unified Supervisor for Laya Pro (Laya System 1 Daemon + FastAPI Backend)
param(
    [string]$LayaHost = "127.0.0.1",
    [int]$LayaPort = 8000,
    [string]$BackendHost = "127.0.0.1",
    [int]$BackendPort = 8765,
    [int]$TimeoutSeconds = 45,
    [switch]$NoLayaDaemon = $false,
    [switch]$StopChildren = $false
)

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$PidFile = Join-Path $ProjectRoot "backend\data\supervisor_pids.json"

function Kill-Tree ($processId) {
    if (-not $processId) { return }
    try {
        # Kill child processes first
        Get-CimInstance Win32_Process -Filter "ParentProcessId = $processId" | ForEach-Object {
            Kill-Tree $_.ProcessId
        }
        Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
    } catch {}
}

if ($StopChildren) {
    try {
        # Find and kill processes holding the ports
        $conn = Get-NetTCPConnection -LocalPort $BackendPort, $LayaPort -State Listen -ErrorAction SilentlyContinue
        if ($conn) {
            $conn | ForEach-Object { Kill-Tree $_.OwningProcess }
        }
    } catch {}
    if (Test-Path $PidFile) {
        try {
            $pids = Get-Content $PidFile -Raw | ConvertFrom-Json
            if ($pids.backend_pid) { Kill-Tree $pids.backend_pid }
            if ($pids.laya_pid) { Kill-Tree $pids.laya_pid }
            Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
        } catch {}
    }
    exit 0
}

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "             LAYA PRO - UNIFIED SUPERVISOR                  " -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

# 1. Resolve Python executables
$LayaPython = Join-Path $ProjectRoot ".venv-laya\Scripts\python.exe"
if (-not (Test-Path $LayaPython)) {
    $LayaPython = "python"
}

$BackendPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $BackendPython)) {
    $py39 = "C:\Users\Marek\AppData\Local\Programs\Python\Python39\python.exe"
    if (Test-Path $py39) {
        $BackendPython = $py39
        $BackendArgsPrefix = @()
    } else {
        $BackendPython = "py"
        $BackendArgsPrefix = @("-3.9")
    }
} else {
    $BackendArgsPrefix = @()
}

$LayaScript = Join-Path $ProjectRoot "scripts\run_local_laya.py"

function Test-PortOpen ($targetHost, $targetPort) {
    try {
        $tcp = New-Object System.Net.Sockets.TcpClient
        $iar = $tcp.BeginConnect($targetHost, $targetPort, $null, $null)
        $wait = $iar.AsyncWaitHandle.WaitOne(200, $false)
        if ($wait) {
            $tcp.EndConnect($iar)
            $tcp.Close()
            return $true
        }
        $tcp.Close()
        return $false
    } catch {
        return $false
    }
}

function Test-HttpHealth ($url) {
    try {
        $res = Invoke-RestMethod -Uri $url -Method Get -TimeoutSec 2 -ErrorAction SilentlyContinue
        return ($null -ne $res)
    } catch {
        return $false
    }
}

$LayaProcess = $null
$BackendProcess = $null

function Stop-SupervisedProcesses {
    Write-Host "`nInitiating graceful shutdown of supervised processes..." -ForegroundColor Yellow

    if ($BackendProcess -and -not $BackendProcess.HasExited) {
        Write-Host "Stopping FastAPI backend (PID $($BackendProcess.Id))..." -ForegroundColor Gray
        Kill-Tree $BackendProcess.Id
    }

    if ($LayaProcess -and -not $LayaProcess.HasExited) {
        Write-Host "Stopping Laya System 1 daemon (PID $($LayaProcess.Id))..." -ForegroundColor Gray
        Kill-Tree $LayaProcess.Id
    }

    if (Test-Path $PidFile) {
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    }

    Write-Host "Shutdown complete. Resources released." -ForegroundColor Green
}

try {
    # 2. Check Laya port
    $layaRunning = Test-PortOpen $LayaHost $LayaPort
    if ($layaRunning) {
        Write-Host "[Laya System 1] Detected existing service on port $LayaPort" -ForegroundColor Yellow
        if (-not (Test-HttpHealth "http://${LayaHost}:${LayaPort}/health")) {
            throw "Port $LayaPort is in use by an unrecognized or unhealthy process."
        }
        Write-Host "[Laya System 1] Existing daemon is healthy. Reusing running instance." -ForegroundColor Green
    } elseif (-not $NoLayaDaemon) {
        Write-Host "[Laya System 1] Starting local Laya daemon on port $LayaPort..." -ForegroundColor White

        $env:LAYA_HOST = $LayaHost
        $env:LAYA_PORT = [string]$LayaPort

        $layaStartInfo = New-Object System.Diagnostics.ProcessStartInfo
        $layaStartInfo.FileName = $LayaPython
        $layaStartInfo.Arguments = "`"$LayaScript`""
        $layaStartInfo.WorkingDirectory = $ProjectRoot
        $layaStartInfo.RedirectStandardOutput = $false
        $layaStartInfo.RedirectStandardError = $false
        $layaStartInfo.UseShellExecute = $false
        $layaStartInfo.CreateNoWindow = $true

        $LayaProcess = New-Object System.Diagnostics.Process
        $LayaProcess.StartInfo = $layaStartInfo

        $null = $LayaProcess.Start()

        # Wait for Laya readiness
        $layaReady = $false
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        while ($sw.Elapsed.TotalSeconds -lt $TimeoutSeconds) {
            if ($LayaProcess.HasExited) {
                $ec = $LayaProcess.ExitCode
                throw "Laya daemon process exited unexpectedly with code $ec"
            }
            if (Test-HttpHealth "http://${LayaHost}:${LayaPort}/health") {
                $layaReady = $true
                break
            }
            Start-Sleep -Milliseconds 500
        }

        if (-not $layaReady) {
            throw "Timed out waiting for Laya daemon on port $LayaPort"
        }
        $lpId = $LayaProcess.Id
        Write-Host "[Laya System 1] Ready on http://${LayaHost}:${LayaPort}/v1/systemone (PID $lpId)" -ForegroundColor Green
    }

    # 3. Check and start FastAPI backend
    $backendRunning = Test-PortOpen $BackendHost $BackendPort
    if ($backendRunning) {
        Write-Host "[FastAPI Backend] Port $BackendPort is already in use." -ForegroundColor Yellow
        if (Test-HttpHealth "http://${BackendHost}:${BackendPort}/health") {
            Write-Host "[FastAPI Backend] Existing backend is healthy on port $BackendPort" -ForegroundColor Green
        } else {
            throw "Port $BackendPort is in use by an unrecognized process."
        }
    } else {
        Write-Host "[FastAPI Backend] Starting backend server on port $BackendPort..." -ForegroundColor White

        $backendArgs = $BackendArgsPrefix + @("-m", "uvicorn", "backend.app.main:app", "--host", $BackendHost, "--port", "$BackendPort")

        $backendStartInfo = New-Object System.Diagnostics.ProcessStartInfo
        $backendStartInfo.FileName = $BackendPython
        $backendStartInfo.Arguments = ($backendArgs -join " ")
        $backendStartInfo.WorkingDirectory = $ProjectRoot
        $backendStartInfo.RedirectStandardOutput = $false
        $backendStartInfo.RedirectStandardError = $false
        $backendStartInfo.UseShellExecute = $false
        $backendStartInfo.CreateNoWindow = $true

        $BackendProcess = New-Object System.Diagnostics.Process
        $BackendProcess.StartInfo = $backendStartInfo

        $null = $BackendProcess.Start()

        # Wait for Backend readiness
        $backendReady = $false
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        while ($sw.Elapsed.TotalSeconds -lt 20) {
            if ($BackendProcess.HasExited) {
                $bec = $BackendProcess.ExitCode
                throw "Backend process exited unexpectedly with code $bec"
            }
            if (Test-HttpHealth "http://${BackendHost}:${BackendPort}/health") {
                $backendReady = $true
                break
            }
            Start-Sleep -Milliseconds 400
        }

        if (-not $backendReady) {
            throw "Timed out waiting for Backend on port $BackendPort"
        }
        $bpId = $BackendProcess.Id
        Write-Host "[FastAPI Backend] Ready on http://${BackendHost}:${BackendPort} (PID $bpId)" -ForegroundColor Green
    }

    # Record PIDs for clean external tracking and teardown
    $pidsRecord = @{
        laya_pid = if ($LayaProcess) { $LayaProcess.Id } else { $null }
        backend_pid = if ($BackendProcess) { $BackendProcess.Id } else { $null }
    } | ConvertTo-Json
    [System.IO.File]::WriteAllText($PidFile, $pidsRecord)

    Write-Host "`nAll services online and operational:" -ForegroundColor Cyan
    Write-Host "  * Laya System 1: http://${LayaHost}:${LayaPort}/v1/systemone" -ForegroundColor White
    Write-Host "  * Backend API:   http://${BackendHost}:${BackendPort}" -ForegroundColor White
    Write-Host "  * Dashboard UI:  http://${BackendHost}:${BackendPort}/dashboard/" -ForegroundColor White
    Write-Host "`nPress Ctrl+C to terminate all supervised processes." -ForegroundColor Gray

    # 4. Supervision loop
    while ($true) {
        if ($LayaProcess -and $LayaProcess.HasExited) {
            $ec = $LayaProcess.ExitCode
            Write-Host "[ALERT] Laya daemon exited with code $ec." -ForegroundColor Red
            break
        }
        if ($BackendProcess -and $BackendProcess.HasExited) {
            $bec = $BackendProcess.ExitCode
            Write-Host "[ALERT] Backend process exited with code $bec." -ForegroundColor Red
            break
        }
        Start-Sleep -Seconds 1
    }

} catch {
    $err = $_.Exception.Message
    Write-Host "`n[ERROR] $err" -ForegroundColor Red
} finally {
    Stop-SupervisedProcesses
}

