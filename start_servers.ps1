param([string]$DeepchartPath)

$root = Split-Path -Parent $MyInvocation.MyCommand.Path

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
$isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "[!] NOT running as Administrator — binding to port 443 will likely fail."
}

# Find Python
function Find-Python {
    # Check portable Python bundled by start.bat
    $portable = Join-Path $root "_python\python.exe"
    if (Test-Path $portable) {
        try { $v = & $portable --version 2>&1; if ($v -match "Python 3") { return $portable } } catch {}
    }
    $candidates = @("python","python3","py")
    foreach ($ver in @("314","313","312","311","310")) {
        $candidates += "${env:ProgramFiles}\Python$ver\python.exe"
        $candidates += "${env:LOCALAPPDATA}\Programs\Python\Python$ver\python.exe"
        $candidates += "C:\Python$ver\python.exe"
    }
    foreach ($c in $candidates) {
        try { $v = & $c --version 2>&1; if ($v -match "Python 3") { return (Get-Command $c -ErrorAction SilentlyContinue).Source } } catch {}
    }
    return "python"
}
$pythonExe = Find-Python
Write-Host "[+] Using Python: $pythonExe"

# ── Kill any existing bridge / vol_hist / Deepchart / VolumetricaBridge ──
Write-Host "[*] Killing existing bridge_mitm_proxy / vol_hist_server / Deepchart / VolumetricaBridge processes ..."
Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'python3.exe'" |
  Where-Object { $_.CommandLine -match "bridge_mitm_proxy|vol_hist_server" } |
  ForEach-Object {
    Write-Host "  Killing PID $($_.ProcessId)"
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
  }
foreach ($name in @('Deepchart', 'VolumetricaBridge')) {
    Get-Process -Name $name -ErrorAction SilentlyContinue | ForEach-Object {
        Write-Host "  Killing $name PID $($_.Id)"
        try { $_.CloseMainWindow() | Out-Null } catch {}
        Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
    }
}
Start-Sleep -Seconds 2

# ── Check port 12010 (Volumetrica Historical) ─────────────────────────────
$port12010 = Get-NetTCPConnection -LocalPort 12010 -ErrorAction SilentlyContinue
if ($port12010) {
    $p = Get-Process -Id $port12010.OwningProcess -ErrorAction SilentlyContinue
    Write-Host "[!] Port 12010 already in use by PID $($port12010.OwningProcess) ($($p.ProcessName))"
}

# ── Check & free port 443 (Bridge MITM) ───────────────────────────────────
$port443 = Get-NetTCPConnection -LocalPort 443 -ErrorAction SilentlyContinue
if ($port443) {
    $proc = Get-Process -Id $port443.OwningProcess -ErrorAction SilentlyContinue
    $svc  = Get-CimInstance Win32_Service -Filter "ProcessId = $($port443.OwningProcess)" -ErrorAction SilentlyContinue
    $svcName = if ($svc) { ($svc | Select-Object -First 1).Name } else { "unknown" }
    Write-Host "[!] Port 443 in use by PID $($port443.OwningProcess) ($($proc.ProcessName) / $svcName) on $($port443.LocalAddress)"

    if ($svcName -eq "iphlpsvc") {
        Write-Host "[*] Attempting to stop iphlpsvc (IP Helper) to free port 443..."
        Stop-Service iphlpsvc -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
    } else {
        Write-Host "[*] Attempting to stop service $svcName ..."
        Stop-Service $svcName -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
    }

    $port443 = Get-NetTCPConnection -LocalPort 443 -ErrorAction SilentlyContinue
    if ($port443) {
        Write-Host "[!] Could not free port 443. Try running as Administrator or run: net stop iphlpsvc"
    } else {
        Write-Host "[+] Port 443 is now free."
    }
}

# ── Start Volumetrica Historical Server (port 12010) ──────────────────────
Write-Host ""
Write-Host "Starting Volumetrica Historical Server (port 12010)..."
$hist = Start-Process -WindowStyle Hidden -PassThru -FilePath $pythonExe -ArgumentList "`"$root\vol_hist_server.py`""
Start-Sleep -Seconds 3

$histPort = Get-NetTCPConnection -LocalPort 12010 -ErrorAction SilentlyContinue
$histOk = $histPort -and $histPort.OwningProcess -eq $hist.Id
if ($histOk) {
    Write-Host "[+] Volumetrica Historical Server running (PID $($hist.Id) on port 12010)"
} elseif ($histPort) {
    Write-Host "[!] Port 12010 is open but owned by PID $($histPort.OwningProcess) (not our PID $($hist.Id))"
} else {
    Write-Host "[!] Volumetrica Historical Server did NOT bind to port 12010"
}

# ── Start Bridge MITM Proxy (port 443) ────────────────────────────────────
Write-Host ""
Write-Host "Starting Bridge MITM Proxy (port 443)..."
$bridge = Start-Process -WindowStyle Hidden -PassThru -FilePath $pythonExe -ArgumentList "`"$root\bridge_mitm_proxy.py`""
Start-Sleep -Seconds 3

$bridgePort = Get-NetTCPConnection -LocalPort 443 -ErrorAction SilentlyContinue
$bridgeOk = $bridgePort -and $bridgePort.OwningProcess -eq $bridge.Id
if ($bridgeOk) {
    Write-Host "[+] Bridge MITM Proxy running (PID $($bridge.Id) on port 443)"
} elseif ($bridgePort) {
    Write-Host "[!] Port 443 is open but owned by PID $($bridgePort.OwningProcess) (not our PID $($bridge.Id))"
} else {
    Write-Host "[!] Bridge MITM Proxy did NOT bind to port 443"
}

# ── Launch Deepchart via patched runner ───────────────────────────────────
Write-Host ""
Write-Host "Launching patched Deepchart..."
$runner = Join-Path $root "run_patched_deepchart.ps1"
if (Test-Path $runner) {
    $dcArg = if ($DeepchartPath) { " -DeepchartPath `"$DeepchartPath`"" } else { "" }
    $ps = Start-Process -FilePath "powershell.exe" -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$runner`"$dcArg" -WindowStyle Hidden -PassThru
    Write-Host "[+] Patched Deepchart launcher started (PID $($ps.Id))."
} else {
    Write-Host "[!] run_patched_deepchart.ps1 not found at: $runner"
}

# ── Summary ────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "=============================="
if ($histOk) { Write-Host "  VOL HIST : RUNNING (12010)" } else { Write-Host "  VOL HIST : NOT RUNNING" }
if ($bridgeOk) { Write-Host "  BRIDGE   : RUNNING (443)" } else { Write-Host "  BRIDGE   : NOT RUNNING" }
Write-Host ""
Write-Host "  Deepchart launcher triggered."
Write-Host "=============================="
Write-Host ""
if (-not $isAdmin) { Write-Host "[TIP] Run as Administrator to avoid port-binding issues." }
Write-Host ""
Write-Host "Close each server window to stop it."
