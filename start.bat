@echo off
title Deepchart CQG Proxy
cd /d "%~dp0"

:: ── Auto-elevate to admin ──────────────────────────────────────────────────
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo Requesting administrator privileges...
    powershell -NoProfile -Command "Start-Process -Verb RunAs -FilePath '%~f0' -WindowStyle Normal"
    exit /b 0
)

echo ============================================
echo   Deepchart CQG Proxy — One-Click Launcher
echo ============================================
echo.

:: ── Find Python ───────────────────────────────────────────────────────────
echo [1/8] Looking for Python...
set "PYTHON="
for %%v in (python python3 py) do (
    for /f "delims=" %%a in ('where %%v 2^>nul') do (
        for /f "tokens=*" %%b in ('%%v --version 2^>^&1') do (
            echo %%b | findstr /i "Python 3" >nul && set "PYTHON=%%v" && goto :python_found
        )
    )
)
for %%p in (C:\Python314 C:\Python313 C:\Python312 %ProgramFiles%\Python314 %ProgramFiles%\Python313 %LocalAppData%\Programs\Python\Python314 %LocalAppData%\Programs\Python\Python313) do (
    if exist "%%p\python.exe" set "PYTHON=%%p\python.exe" && goto :python_found
)
:python_found
if not defined PYTHON (
    echo [!] Python not found. Install Python 3.14+ from https://www.python.org/downloads/
    pause
    exit /b 1
)
echo    [+] Python: %PYTHON%

:: ── Install dependencies ──────────────────────────────────────────────────
echo [2/8] Installing Python packages...
"%PYTHON%" -m pip install -r "%~dp0requirements.txt" >nul 2>&1
if %errorLevel% equ 0 ( echo    [+] Dependencies installed ) else ( echo    [!] pip install failed )

:: ── Fix hosts ─────────────────────────────────────────────────────────────
echo [3/8] Checking hosts file...
set "HOSTS=%windir%\System32\drivers\etc\hosts"
findstr /i "demoapi.cqg.com" "%HOSTS%" >nul 2>&1
if %errorLevel% neq 0 (
    echo    [*] Adding CQG domains to hosts file...
    for /f "tokens=3 delims=: " %%i in ('netsh interface ip show addresses ^| findstr "IP Address" ^| findstr /v "127.0.0.1"') do set "LOCAL_IP=%%i" & goto :ip_found
    for /f "tokens=3 delims=: " %%a in ('ipconfig ^| findstr /i "IPv4"') do if not defined LOCAL_IP set "LOCAL_IP=%%a"
    :ip_found
    if not defined LOCAL_IP set "LOCAL_IP=127.0.0.1"
    powershell -NoProfile -Command "$h='%HOSTS%'; $ip='%LOCAL_IP%'; $e=@(\"$ip  demoapi.cqg.com\",\"$ip  api.cqg.com\",\"$ip  depth-it.historical.deepcharts.com\",\"$ip  data-b.historical.deepcharts.com\"); $c=Get-Content $h -Raw; foreach($x in $e){if($c -notmatch [regex]::Escape($x)){$c+=\"`r`n$x\"}}; Set-Content $h -Value $c -Force"
    echo    [+] Hosts updated (IP: %LOCAL_IP%)
) else (
    echo    [+] Hosts already configured
)

:: ── Check patched_run ────────────────────────────────────────────────────
echo [4/8] Checking patched Deepchart...
set "DC_PATH=%~dp0patched_run"
if not exist "%DC_PATH%\Deepchart.exe" (
    echo    [*] Running patcher...
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0patcher.ps1" -NoPause
    if not exist "%DC_PATH%\Deepchart.exe" (
        echo    [!] Patcher failed. Run setup.ps1 manually first.
        pause
        exit /b 1
    )
) else ( echo    [+] Found at %DC_PATH% )

:: ── Kill old processes ────────────────────────────────────────────────────
echo [5/8] Stopping old processes...
for %%p in (VolumetricaBridge Deepchart) do (
    taskkill /F /IM %%p.exe >nul 2>&1
)
for /f "usebackq" %%a in (`wmic process where "name='python.exe' and (commandline like '%%bridge_mitm%%' or commandline like '%%vol_hist%%')" get processid 2^>nul ^| findstr /r "[0-9]"`) do (
    taskkill /F /PID %%a >nul 2>&1
)
timeout /t 2 /nobreak >nul

:: ── Start servers ─────────────────────────────────────────────────────────
echo [6/8] Starting proxy servers...

start "Hist Server" "%PYTHON%" "%~dp0vol_hist_server.py"
echo    [+] Historical Server starting...
timeout /t 3 /nobreak >nul

start "Bridge Proxy" "%PYTHON%" "%~dp0bridge_mitm_proxy.py"
echo    [+] Bridge Proxy starting...
timeout /t 3 /nobreak >nul

:: ── Launch VolumetricaBridge ──────────────────────────────────────────────
echo [7/8] Launching VolumetricaBridge...
start "VBridge" "%DC_PATH%\bridge\VolumetricaBridge.exe"
timeout /t 6 /nobreak >nul

:: ── Launch Deepchart ──────────────────────────────────────────────────────
echo [8/8] Launching Deepchart...
start "Deepchart" "%DC_PATH%\Deepchart.exe"

echo.
echo ============================================
echo   All services running!
echo   Close this window to stop everything.
echo ============================================
echo.
timeout /t 5 /nobreak >nul
