param([switch]$NoPause)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

# ── Auto-elevate ──────────────────────────────────────────────────────────
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
$isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "Requesting administrator privileges..."
    $args = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    if ($NoPause) { $args += " -NoPause" }
    Start-Process PowerShell -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`"" -Wait
    exit
}

# ── Config file ───────────────────────────────────────────────────────────
$configPath = Join-Path $root "config.json"
$config = @{}
if (Test-Path $configPath) {
    $config = Get-Content $configPath -Raw | ConvertFrom-Json
}

# ── Find Python ───────────────────────────────────────────────────────────
function Find-Python {
    # Check portable Python bundled by start.bat
    $portable = Join-Path $root "_python\python.exe"
    if (Test-Path $portable) {
        try { $ver = & $portable --version 2>&1; if ($ver -match "Python 3") { return $portable } } catch {}
    }
    $candidates = @(
        "python", "python3"
        "C:\Python314\python.exe"
        "C:\Python313\python.exe"
        "C:\Python312\python.exe"
        "C:\Python311\python.exe"
        "${env:ProgramFiles}\Python314\python.exe"
        "${env:ProgramFiles}\Python313\python.exe"
        "${env:ProgramFiles}\Python312\python.exe"
        "${env:ProgramFiles}\Python311\python.exe"
        "${env:LOCALAPPDATA}\Programs\Python\Python314\python.exe"
        "${env:LOCALAPPDATA}\Programs\Python\Python313\python.exe"
        "${env:LOCALAPPDATA}\Programs\Python\Python312\python.exe"
        "${env:LOCALAPPDATA}\Programs\Python\Python311\python.exe"
    )
    foreach ($c in $candidates) {
        try {
            $ver = & $c --version 2>&1
            if ($ver -match "Python 3\.(1[4-9]|[2-9]\d)") {
                return (Get-Command $c).Source
            }
            if ($ver -match "Python 3\.(1[0-3]|\d+)") {
                $p = (Get-Command $c).Source
                Write-Host "  [!] Found $c ($($ver.Trim())) — Python 3.14+ recommended"
                return $p
            }
        } catch {}
    }
    return $null
}

Write-Host "============================================"
Write-Host " Deepchart CQG Proxy — Setup"
Write-Host "============================================"
Write-Host ""

# Step 1: Check Python
Write-Host "[1/5] Checking Python..."
$python = Find-Python
if (-not $python) {
    Write-Host "[!] Python 3 not found. Install Python 3.14+ from https://www.python.org/downloads/"
    Write-Host "    Make sure to check 'Add Python to PATH' during installation."
    if (-not $NoPause) { Read-Host "Press Enter to exit" }
    exit 1
}
Write-Host "  [+] Python: $python"
$pyVer = & $python --version 2>&1
Write-Host "       $($pyVer.Trim())"

# Step 2: Install deps
Write-Host ""
Write-Host "[2/5] Installing Python dependencies..."
try {
    & $python -m pip install -r (Join-Path $root "requirements.txt") 2>&1 | ForEach-Object { Write-Host "       $_" }
} catch {
    Write-Host "  [!] pip install failed: $_"
    if (-not $NoPause) { Read-Host "Press Enter to exit" }
    exit 1
}

# Step 3: Hosts file
Write-Host ""
Write-Host "[3/5] Setting up hosts file (CQG domain redirect)..."
$hostsBat = Join-Path $root "fix_hosts.bat"
if (Test-Path $hostsBat) {
    & $hostsBat
} else {
    Write-Host "  [!] fix_hosts.bat not found — run it manually later."
}

# Step 4: Deepchart path + patcher
Write-Host ""
Write-Host "[4/5] Deepchart installation + patching..."
$dcPath = if ($config.DeepchartPath) { $config.DeepchartPath } else { $null }
if (-not $dcPath -or -not (Test-Path (Join-Path $dcPath "Deepchart.exe"))) {
    $tryPaths = @(
        "C:\Deepchart\patched_run"
        "C:\Program Files\Deepchart"
        "${env:LOCALAPPDATA}\Programs\Deepchart"
        "${env:USERPROFILE}\Desktop\Deepchart"
        "${env:USERPROFILE}\Downloads\Deepchart"
    )
    $defaultPath = $tryPaths | Where-Object { Test-Path (Join-Path $_ "Deepchart.exe") } | Select-Object -First 1
    if (-not $defaultPath) { $defaultPath = "C:\Deepchart\patched_run" }
    Write-Host "  Enter the path to your Deepchart installation folder"
    Write-Host "  (where Deepchart.exe is located):"
    $input = Read-Host "  Path [$defaultPath]"
    if ([string]::IsNullOrWhiteSpace($input)) { $input = $defaultPath }
    $dcPath = $input
}
if (Test-Path (Join-Path $dcPath "Deepchart.exe")) {
    Write-Host "  [+] Deepchart found at: $dcPath"
    @{ DeepchartPath = $dcPath } | ConvertTo-Json | Set-Content $configPath -Force
    # Run patcher
    $patcher = Join-Path $root "patcher.ps1"
    if (Test-Path $patcher) {
        Write-Host "  [*] Running patcher to create patched_run/ ..."
        & $patcher -DeepchartPath $dcPath -NoPause
    }
} else {
    Write-Host "  [!] Deepchart.exe not found at: $dcPath"
    Write-Host "  You can still run the proxy servers — configure Deepchart path later."
    $dcPath = $null
}

# Step 5: Trust MITM CA
Write-Host ""
Write-Host "[5/5] Trusting MITM certificate (so Windows doesn't warn)..."
$mitmDir = Join-Path $root "mitm_ca"
$caPem = Join-Path $mitmDir "ca.pem"
if (Test-Path $caPem) {
    try {
        Import-Certificate -FilePath $caPem -CertStoreLocation Cert:\LocalMachine\Root -ErrorAction SilentlyContinue | Out-Null
        Write-Host "  [+] Certificate trusted."
    } catch {
        Write-Host "  [!] Could not install certificate automatically."
        Write-Host "       Manually install $caPem as Trusted Root CA."
    }
} else {
    Write-Host "  [-] No mitm_ca/ca.pem found — will be auto-generated on first proxy run."
}

# ── Done ──────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "============================================"
Write-Host " Setup complete!"
Write-Host "============================================"
Write-Host ""
Write-Host "Quick start:"
Write-Host "  1. Run 'start_servers.ps1' as Administrator to start everything"
Write-Host "     (or 'Start Deepchart.bat' for a simpler launcher)"
if ($dcPath) {
    Write-Host "  2. Deepchart will open automatically from patched_run/"
    Write-Host "  3. Add a connection: Data Feed = CQG, use demo credentials"
}
Write-Host ""
Write-Host "Already ran setup again? Just run 'patcher.ps1' to re-patch Deepchart."
Write-Host ""

if (-not $NoPause) { Read-Host "Press Enter to exit" }
