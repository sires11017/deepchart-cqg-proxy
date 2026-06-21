param([switch]$NoWindow, [string]$DeepchartPath)

$root = Split-Path -Parent $MyInvocation.MyCommand.Path

# ── Auto-detect Python ──────────────────────────────────────────────────
function Find-Python {
    $candidates = @("pythonw","python3w","python","python3","py")
    foreach ($ver in @("314","313","312","311","310")) {
        $candidates += "${env:ProgramFiles}\Python$ver\pythonw.exe"
        $candidates += "${env:LOCALAPPDATA}\Programs\Python\Python$ver\pythonw.exe"
        $candidates += "C:\Python$ver\pythonw.exe"
        $candidates += "${env:ProgramFiles}\Python$ver\python.exe"
        $candidates += "${env:LOCALAPPDATA}\Programs\Python\Python$ver\python.exe"
        $candidates += "C:\Python$ver\python.exe"
    }
    foreach ($c in $candidates) {
        try { $v = & $c --version 2>&1; if ($v -match "Python 3") { return (Get-Command $c -ErrorAction SilentlyContinue).Source } } catch {}
    }
    return "pythonw"
}
$pythonExe = Find-Python

# ── Auto-detect Deepchart path ──────────────────────────────────────────
if (-not $DeepchartPath) {
    $configPath = Join-Path $root "config.json"
    if (Test-Path $configPath) {
        $cfg = Get-Content $configPath -Raw | ConvertFrom-Json
        if ($cfg.DeepchartPath) { $DeepchartPath = $cfg.DeepchartPath }
    }
}
if (-not $DeepchartPath -or -not (Test-Path (Join-Path $DeepchartPath "Deepchart.exe"))) {
    foreach ($p in @("C:\Deepchart\patched_run", (Join-Path $root "patched_run"))) {
        if (Test-Path (Join-Path $p "Deepchart.exe")) { $DeepchartPath = $p; break }
    }
}
if (-not $DeepchartPath) { $DeepchartPath = "C:\Deepchart\patched_run" }

$patchedRun = $DeepchartPath
$dpcLogDir = "C:\Deepchart\data"
$logFile = Join-Path $root "logs\watchdog_$(Get-Date -Format 'yyyyMMdd_HHmmss').log"
$restartCount = @{}

function Write-Log {
    param($Msg)
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') [WATCHDOG] $Msg"
    Write-Host $line
    try { Add-Content -Path $logFile -Value $line -ErrorAction SilentlyContinue } catch {}
}

function Get-ScriptPids {
    param($ScriptName)
    $pids = @()
    Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe' OR Name = 'python3.exe' OR Name = 'python3w.exe'" -ErrorAction SilentlyContinue | ForEach-Object {
        if ($_.CommandLine -match [regex]::Escape($ScriptName)) {
            $pids += $_.ProcessId
        }
    }
    $pids
}

function Kill-All {
    Write-Log "Killing all Deepchart-related processes..."
    foreach ($name in @('Deepchart_app', 'Deepchart', 'VolumetricaBridge')) {
        Get-Process -Name $name -ErrorAction SilentlyContinue | ForEach-Object {
            Write-Log "  Killing $name PID $($_.Id)"
            try { $_.CloseMainWindow() | Out-Null } catch {}
            Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
        }
    }
    Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe' OR Name = 'python3.exe' OR Name = 'python3w.exe'" -ErrorAction SilentlyContinue | ForEach-Object {
        if ($_.CommandLine -match "bridge_mitm_proxy|vol_hist_server") {
            Write-Log "  Killing python PID $($_.ProcessId): $($_.CommandLine)"
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }
    }
    Start-Sleep -Seconds 2
}

function Start-ProcessWithLog {
    param($FilePath, $Arguments, $WorkingDir, $Label)
    try {
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = $FilePath
        $psi.Arguments = $Arguments
        $psi.WorkingDirectory = $WorkingDir
        $psi.WindowStyle = if ($NoWindow) { [System.Diagnostics.ProcessWindowStyle]::Hidden } else { [System.Diagnostics.ProcessWindowStyle]::Normal }
        $psi.UseShellExecute = $true
        if ($FilePath -match 'python\.exe$') { $psi.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Normal }
        $proc = [System.Diagnostics.Process]::Start($psi)
        if ($proc) { Write-Log "$Label started (PID $($proc.Id))"; return $proc.Id } else { Write-Log "$Label FAILED to start"; return $null }
    } catch {
        Write-Log "$Label start error: $_"
        return $null
    }
}

function Start-AllServices {
    Kill-All

    $port443 = Get-NetTCPConnection -LocalPort 443 -ErrorAction SilentlyContinue
    if ($port443) {
        $svc = Get-CimInstance Win32_Service -Filter "ProcessId = $($port443.OwningProcess)" -ErrorAction SilentlyContinue
        $svcName = if ($svc) { ($svc | Select-Object -First 1).Name } else { "unknown" }
        if ($svcName -eq "iphlpsvc") { Stop-Service iphlpsvc -Force -ErrorAction SilentlyContinue; Start-Sleep -Seconds 2 }
        elseif ($svcName -ne "unknown") { Stop-Service $svcName -Force -ErrorAction SilentlyContinue; Start-Sleep -Seconds 2 }
    }

    Write-Log "Starting Volumetrica Historical Server..."
    $script:histPid = Start-ProcessWithLog -FilePath $pythonExe -Arguments "`"$root\vol_hist_server.py`"" -WorkingDir $root -Label "VolHist"
    Start-Sleep -Seconds 2

    Write-Log "Starting Bridge MITM Proxy..."
    $script:bridgePid = Start-ProcessWithLog -FilePath $pythonExe -Arguments "`"$root\bridge_mitm_proxy.py`"" -WorkingDir $root -Label "BridgeProxy"
    Start-Sleep -Seconds 3

    $vbPath = Join-Path $patchedRun "bridge\VolumetricaBridge.exe"
    if (Test-Path $vbPath) {
        Write-Log "Starting VolumetricaBridge..."
        $script:vbPid = Start-ProcessWithLog -FilePath $vbPath -Arguments "" -WorkingDir (Join-Path $patchedRun "bridge") -Label "VolumetricaBridge"
    }
    Start-Sleep -Seconds 4

    $dcPath = Join-Path $patchedRun "Deepchart.exe"
    if (Test-Path $dcPath) {
        Write-Log "Starting Deepchart..."
        $script:dcPid = Start-ProcessWithLog -FilePath $dcPath -Arguments "" -WorkingDir $patchedRun -Label "Deepchart"
    }
}

$script:dpcStatePath = Join-Path $root "logs\watchdog_dpc_state.txt"

function Get-DpcLogState {
    $dpcLogs = Get-ChildItem "$dpcLogDir\DPC_Log_*.log" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending
    if (-not $dpcLogs) { return $null, 0, "1970-01-01" }
    $latest = $dpcLogs[0]
    return $latest.FullName, $latest.Length, $latest.LastWriteTime.ToString("yyyy-MM-dd HH:mm:ss")
}

function Read-DpcStateFile {
    if (-not (Test-Path $script:dpcStatePath)) { return "" }
    $val = Get-Content $script:dpcStatePath -First 1 -ErrorAction SilentlyContinue
    if (-not $val) { return "" }
    return $val
}

function Write-DpcStateFile {
    param($Value)
    try { Set-Content -Path $script:dpcStatePath -Value $Value -Force -ErrorAction SilentlyContinue } catch {}
}

function Check-And-Restart {
    $foundError = $false

    $dpcPath, $dpcSize, $dpcTime = Get-DpcLogState
    $lastState = Read-DpcStateFile
    $currentState = "$dpcPath|$dpcSize|$dpcTime"

    $checkErrors = $false
    if ($lastState -ne $currentState) {
        $checkErrors = $true
        Write-DpcStateFile -Value $currentState
    }

    if ($checkErrors -and $dpcPath -and (Test-Path $dpcPath)) {
        $content = Get-Content $dpcPath -Tail 50 -ErrorAction SilentlyContinue
        $errorMatches = $content | Select-String -Pattern "Cannot access a disposed object" -SimpleMatch -Context 3,0
        $foundRecentError = $false
        $now = Get-Date
        foreach ($m in $errorMatches) {
            $contextLines = $m.Context.PreContext -join "`n"
            if ($contextLines -match 'DT:\s*(\d{2}/\d{2}/\d{4}\s+\d{1,2}:\d{2}:\d{2}\s*(?:AM|PM))') {
                try {
                    $errTime = [datetime]::ParseExact($matches[1], "dd/MM/yyyy h:mm:ss tt", [System.Globalization.CultureInfo]::InvariantCulture)
                    if (($now - $errTime).TotalSeconds -gt 15) {
                        $skipMsg = "Skipping old DPC error (" + $errTime + ") - more than 15s ago"
                        Write-Log $skipMsg
                        continue
                    }
                } catch {}
            }
            $foundRecentError = $true
            break
        }
        if ($foundRecentError) {
            Write-Log "DETECTED: Disposed stream error in DPC log! Triggering restart..."
            $foundError = $true
        }
    }

    $bridgeLogs = Get-ChildItem "$root\logs\bridge_mitm_*.log" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending
    if ($bridgeLogs) {
        $latestBridge = $bridgeLogs[0]
        $bridgeContent = Get-Content $latestBridge.FullName -Tail 10 -ErrorAction SilentlyContinue
        if ($bridgeContent -match "Error|error|CLOSE frame|closed connection") {
            $tsMatch = $bridgeContent | Select-String -Pattern "Error|error|CLOSE frame|closed connection" | Select-Object -Last 3
            if ($tsMatch) {
                Write-Log "Bridge proxy log shows errors"
            }
        }
    }

    $bridgeProcs = Get-ScriptPids -ScriptName "bridge_mitm_proxy.py"
    $histProcs = Get-ScriptPids -ScriptName "vol_hist_server.py"
    if ($bridgeProcs.Count -eq 0) {
        Write-Log "Bridge proxy process not found! Restarting..."
        $foundError = $true
    }
    if ($histProcs.Count -eq 0) {
        Write-Log "VolHist server process not found! Restarting..."
        $foundError = $true
    }

    $dc = Get-Process -Name "Deepchart_app" -ErrorAction SilentlyContinue
    if (-not $dc) {
        $dc = Get-Process -Name "Deepchart" -ErrorAction SilentlyContinue
    }
    if (-not $dc) {
        Write-Log "Deepchart process not found! Restarting..."
        $foundError = $true
    }

    $vb = Get-Process -Name "VolumetricaBridge" -ErrorAction SilentlyContinue
    if (-not $vb) {
        Write-Log "VolumetricaBridge process not found! Restarting..."
        $foundError = $true
    }

    $p443 = Get-NetTCPConnection -LocalPort 443 -ErrorAction SilentlyContinue
    if (-not $p443) {
        Write-Log "Port 443 not listening! Bridge proxy may be down."
        $foundError = $true
    }

    $p12010 = Get-NetTCPConnection -LocalPort 12010 -ErrorAction SilentlyContinue
    if (-not $p12010) {
        Write-Log "Port 12010 not listening! Historical server may be down."
        $foundError = $true
    }

    if ($foundError) {
        $label = "$(Get-Date -Format 'yyyyMMdd_HHmmss')"
        $script:restartCount[$label] = ($script:restartCount.Keys.Count + 1)
        Write-Log "=== RESTART #$($script:restartCount[$label]) ==="
        return $true
    }
    return $false
}

Write-Log "==========================================="
Write-Log "Deepchart Watchdog started"
Write-Log "Root: $root"
Write-Log "Patched: $patchedRun"
Write-Log "==========================================="

Start-AllServices
Start-Sleep -Seconds 15

$checkInterval = 5
$maxRestarts = 200

while ($true) {
    $needsRestart = Check-And-Restart

    if ($needsRestart) {
        if ($script:restartCount.Keys.Count -ge $maxRestarts) {
            Write-Log "MAX RESTARTS ($maxRestarts) REACHED! Exiting watchdog."
            break
        }
        Start-AllServices
        Write-Log "Restart complete. Waiting 10s before next check..."
        Start-Sleep -Seconds 10
    }

    Start-Sleep -Seconds $checkInterval
}
