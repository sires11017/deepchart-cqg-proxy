param([string]$DeepchartPath, [switch]$NoPause)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "============================================"
Write-Host " Deepchart CQG Proxy — Patcher"
Write-Host "============================================"
Write-Host ""

# ── Find Deepchart ──────────────────────────────────────────────────────────
if (-not $DeepchartPath -or -not (Test-Path (Join-Path $DeepchartPath "Deepchart.exe"))) {
    $configPath = Join-Path $root "config.json"
    if (Test-Path $configPath) {
        $cfg = Get-Content $configPath -Raw | ConvertFrom-Json
        if ($cfg.DeepchartPath -and (Test-Path (Join-Path $cfg.DeepchartPath "Deepchart.exe"))) {
            $DeepchartPath = $cfg.DeepchartPath
        }
    }
}

if (-not $DeepchartPath -or -not (Test-Path (Join-Path $DeepchartPath "Deepchart.exe"))) {
    $tryPaths = @(
        "${env:LOCALAPPDATA}\Programs\Deepchart"
        "${env:ProgramFiles}\Deepchart"
        "${env:PROGRAMW6432}\Deepchart"
        "${env:USERPROFILE}\Desktop\Deepchart"
        "${env:USERPROFILE}\Downloads\Deepchart"
        "C:\Deepchart"
        "C:\Deepchart\patched_run"
    )
    $found = $null
    foreach ($p in $tryPaths) {
        if (Test-Path (Join-Path $p "Deepchart.exe")) { $found = $p; break }
    }
    if ($found) {
        Write-Host "  [+] Auto-detected Deepchart at: $found"
        $DeepchartPath = $found
    } else {
        Write-Host "  Could not find Deepchart automatically."
        Write-Host "  Enter the folder path where Deepchart.exe is located:"
        $input = Read-Host "Path"
        if ([string]::IsNullOrWhiteSpace($input)) { exit 1 }
        $DeepchartPath = $input
        if (-not (Test-Path (Join-Path $DeepchartPath "Deepchart.exe"))) {
            Write-Host "[!] Deepchart.exe not found at: $DeepchartPath"
            exit 1
        }
    }
}

Write-Host "[+] Deepchart found at: $DeepchartPath"

# ── Create patched_run ──────────────────────────────────────────────────────
$target = Join-Path $root "patched_run"
Write-Host "[*] Copying Deepchart to $target ..."

# Remove old patched_run if exists
if (Test-Path $target) {
    Remove-Item -Recurse -Force $target -ErrorAction SilentlyContinue
}

# Copy all files except .bak*
Copy-Item -Path $DeepchartPath -Destination $target -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem $target -Recurse -Include "*.bak*" | Remove-Item -Force -ErrorAction SilentlyContinue
Get-ChildItem $target -Recurse -Include "*.bak" | Remove-Item -Force -ErrorAction SilentlyContinue
Get-ChildItem $target -Recurse -Include "testwrite.*" | Remove-Item -Force -ErrorAction SilentlyContinue

Write-Host "[+] Deepchart copied to $target"

# ── Save original Deepchart.exe before overwriting ─────────────────────────
$originalExe = Join-Path $target "Deepchart.exe"
$savedExe = Join-Path $target "Deepchart.Original.exe"
if (Test-Path $originalExe) {
    if (Test-Path $savedExe) { Remove-Item -Force $savedExe -ErrorAction SilentlyContinue }
    Rename-Item -Path $originalExe -NewName "Deepchart.Original.exe" -Force
    Write-Host "[+] Saved original Deepchart as Deepchart.Original.exe"
}

# ── Compile Launcher.cs → Deepchart.exe ─────────────────────────────────────
$launcherCs = Join-Path $root "Launcher.cs"
$launcherExe = Join-Path $target "Deepchart.exe"

if (Test-Path $launcherCs) {
    Write-Host "[*] Compiling Launcher.cs → patched Deepchart.exe ..."

    # Find C# compiler
    $cscPaths = @(
        "${env:windir}\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
        "${env:windir}\Microsoft.NET\Framework\v4.0.30319\csc.exe"
        "${env:windir}\Microsoft.NET\Framework64\v4.8.9032\csc.exe"
        "${env:windir}\Microsoft.NET\Framework\v4.8.9032\csc.exe"
    )
    $csc = $null
    foreach ($p in $cscPaths) {
        if (Test-Path $p) { $csc = $p; break }
    }

    if (-not $csc) {
        Write-Host "[!] C# compiler (csc.exe) not found. Compile manually:"
        Write-Host "    csc.exe /r:System.Core.dll /out:`"$launcherExe`" `"$launcherCs`""
    } else {
        & $csc /r:System.Core.dll /out:"$launcherExe" "$launcherCs" 2>&1 | ForEach-Object { Write-Host "       $_" }
        if (Test-Path $launcherExe) {
            Write-Host "[+] Compiled: patched Deepchart.exe ($((Get-Item $launcherExe).Length) bytes)"
        } else {
            Write-Host "[!] Compilation failed."
        }
    }
} else {
    Write-Host "[!] Launcher.cs not found — cannot compile patched Deepchart.exe"
}

# ── Copy user profiles (templates, workspaces, settings) ────────────────────
$profilesDir = Join-Path $root "profiles"
$dataTarget = Join-Path $target "data"
if (Test-Path $profilesDir) {
    Write-Host "[*] Copying user profiles (templates/workspaces/settings)..."
    # Copy each subdirectory in profiles/ into patched_run/data/
    Get-ChildItem $profilesDir -Directory | ForEach-Object {
        $dest = Join-Path $dataTarget $_.Name
        Copy-Item -Path $_.FullName -Destination $dest -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "    [+] $($_.Name)"
    }
    # Also copy individual files at root of profiles/
    Get-ChildItem $profilesDir -File | ForEach-Object {
        Copy-Item -Path $_.FullName -Destination $dataTarget -Force -ErrorAction SilentlyContinue
    }
    Write-Host "    [+] Profiles applied"
} else {
    Write-Host "  [-] No profiles/ directory found — skipping"
}

# ── Copy roaming config (APPDATA) — only if not already configured ───────────
$roamingSrc = Join-Path $profilesDir "Roaming"
$roamingDst = "$env:APPDATA\Deepchart"
if (Test-Path $roamingSrc) {
    $existingConfig = Join-Path $roamingDst "config.settings"
    if (Test-Path $existingConfig) {
        Write-Host "  [-] Roaming config already exists at $roamingDst — skipping"
    } else {
        Write-Host "[*] Applying roaming config (APPDATA\Deepchart)..."
        New-Item -ItemType Directory -Path $roamingDst -Force -ErrorAction SilentlyContinue | Out-Null
        Get-ChildItem $roamingSrc | ForEach-Object {
            Copy-Item -Path $_.FullName -Destination $roamingDst -Force -ErrorAction SilentlyContinue
            Write-Host "    [+] $($_.Name)"
        }
    }
}

# ── Done ────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "============================================"
Write-Host " Patching complete!"
Write-Host "============================================"
Write-Host ""
Write-Host "  patched_run is ready at: $target"
Write-Host "  Profiles (templates/workspaces) installed"
Write-Host ""
Write-Host "Now run:"
Write-Host "  1. start.bat (one-click launcher — does everything)"
Write-Host "  OR"
Write-Host "  2. setup.ps1 (install Python deps + hosts)"
Write-Host "  3. start_servers.ps1 (launch everything)"
Write-Host ""

if (-not $NoPause) { Read-Host "Press Enter to exit" }
