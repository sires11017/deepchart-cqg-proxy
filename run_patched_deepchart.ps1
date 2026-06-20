param([string]$DeepchartPath)

$root = Split-Path -Parent $MyInvocation.MyCommand.Path

# Try to find Deepchart path
if (-not $DeepchartPath -or -not (Test-Path (Join-Path $DeepchartPath "Deepchart.exe"))) {
    # Check config.json from setup.ps1
    $configPath = Join-Path $root "config.json"
    if (Test-Path $configPath) {
        $config = Get-Content $configPath -Raw | ConvertFrom-Json
        if ($config.DeepchartPath -and (Test-Path (Join-Path $config.DeepchartPath "Deepchart.exe"))) {
            $DeepchartPath = $config.DeepchartPath
        }
    }
}

# Fallback to common locations
if (-not $DeepchartPath -or -not (Test-Path (Join-Path $DeepchartPath "Deepchart.exe"))) {
    $tryPaths = @(
        "C:\Deepchart\patched_run"
        (Join-Path $root "patched_run")
        "C:\Program Files\Deepchart"
        "${env:LOCALAPPDATA}\Programs\Deepchart"
    )
    foreach ($p in $tryPaths) {
        if (Test-Path (Join-Path $p "Deepchart.exe")) {
            $DeepchartPath = $p
            break
        }
    }
}

if (-not $DeepchartPath -or -not (Test-Path (Join-Path $DeepchartPath "Deepchart.exe"))) {
    Write-Host "[!] Deepchart.exe not found. Run setup.ps1 first or pass DeepchartPath parameter."
    exit 1
}

$bridge = Join-Path $DeepchartPath 'bridge\VolumetricaBridge.exe'
$bridgeDir = Split-Path -Parent $bridge
$app = Join-Path $DeepchartPath 'Deepchart.exe'
$appDir = Split-Path -Parent $app

# Unblock files
Get-ChildItem $DeepchartPath -Recurse -File | Unblock-File -ErrorAction SilentlyContinue

if (-not (Test-Path $bridge)) {
    Write-Host "[!] VolumetricaBridge.exe not found at $bridge"
    exit 1
}

Write-Host "[*] Launching VolumetricaBridge from $bridgeDir..."
try {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $bridge
    $psi.WorkingDirectory = $bridgeDir
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $p = [System.Diagnostics.Process]::Start($psi)
    Write-Host "[+] VolumetricaBridge started (PID $($p.Id))"
} catch {
    Write-Host "[!] Failed to start VolumetricaBridge: $_"
    exit 1
}

Start-Sleep -Seconds 6

Write-Host "[*] Launching Deepchart from $appDir..."
try {
    $psi2 = New-Object System.Diagnostics.ProcessStartInfo
    $psi2.FileName = $app
    $psi2.WorkingDirectory = $appDir
    $psi2.UseShellExecute = $false
    [System.Diagnostics.Process]::Start($psi2)
    Write-Host "[+] Deepchart launched"
} catch {
    Write-Host "[!] Failed to start Deepchart: $_"
}

Start-Sleep -Seconds 2
Get-Process -Name Deepchart,VolumetricaBridge -ErrorAction SilentlyContinue |
    Select-Object ProcessName, Id, StartTime, Path |
    Format-List
