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

:: ── Find or download Python ──────────────────────────────────────────────
echo [1/8] Setting up Python...
set "PYTHON="
for %%v in (python python3 py) do (
    for /f "delims=" %%a in ('where %%v 2^>nul') do (
        for /f "tokens=*" %%b in ('%%v --version 2^>^&1') do (
            echo %%b | findstr /i "Python 3" >nul && set "PYTHON=%%v" && goto :python_ready
        )
    )
)
for %%p in (C:\Python314\python.exe C:\Python313\python.exe C:\Python312\python.exe "%ProgramFiles%\Python314\python.exe" "%ProgramFiles%\Python313\python.exe" "%LocalAppData%\Programs\Python\Python314\python.exe" "%LocalAppData%\Programs\Python\Python313\python.exe") do (
    if exist %%p set "PYTHON=%%p" && goto :python_ready
)

:: Download embeddable Python if not installed
if not defined PYTHON (
    echo    [*] Python not found — downloading portable Python 3.14...
    set "PYDIR=%~dp0_python"
    if not exist "%PYDIR%" mkdir "%PYDIR%"
    powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.14.6/python-3.14.6-embed-amd64.zip' -OutFile '%TEMP%\_py.zip'" >nul 2>&1
    if not exist "%TEMP%\_py.zip" (
        echo    [!] Download failed. Install Python manually from https://www.python.org/downloads/
        pause
        exit /b 1
    )
    powershell -NoProfile -Command "Expand-Archive -Path '%TEMP%\_py.zip' -DestinationPath '%PYDIR%' -Force" >nul 2>&1
    del "%TEMP%\_py.zip" 2>nul
    :: Enable pip by removing import site restriction in python._pth
    for %%f in ("%PYDIR%\python*._pth") do (
        powershell -NoProfile -Command "(Get-Content '%%f') -replace '^import site$', '#import site' | Set-Content '%%f'"
    )
    :: Download get-pip.py and install pip
    powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile '%TEMP%\_getpip.py'" >nul 2>&1
    "%PYDIR%\python.exe" "%TEMP%\_getpip.py" --quiet >nul 2>&1
    del "%TEMP%\_getpip.py" 2>nul
    set "PYTHON=%PYDIR%\python.exe"
    echo    [+] Downloaded and configured portable Python at %PYDIR%
    goto :python_ready
)

:python_ready
"%PYTHON%" --version 2>&1 | findstr "Python 3" >nul
if errorlevel 1 (
    echo    [!] Python is not working at: %PYTHON%
    pause
    exit /b 1
)
for /f "tokens=*" %%v in ('"%PYTHON%" --version 2^>^&1') do echo    [+] Python: %%v

:: ── Install dependencies ──────────────────────────────────────────────────
echo [2/8] Installing Python packages...
"%PYTHON%" -m pip install -r "%~dp0requirements.txt" >nul 2>&1
if %errorLevel% equ 0 ( echo    [+] Dependencies installed ) else ( echo    [!] pip install failed )

:: ── Fix hosts ─────────────────────────────────────────────────────────────
echo [3/8] Updating hosts file...
for /f "tokens=3 delims=: " %%i in ('netsh interface ip show addresses ^| findstr "IP Address" ^| findstr /v "127.0.0.1"') do set "LOCAL_IP=%%i" & goto :ip_found
for /f "tokens=3 delims=: " %%a in ('ipconfig ^| findstr /i "IPv4"') do if not defined LOCAL_IP set "LOCAL_IP=%%a"
:ip_found
if not defined LOCAL_IP set "LOCAL_IP=127.0.0.1"
powershell -NoProfile -Command ^
  "$h=Join-Path ([Environment]::SystemDirectory) 'drivers\etc\hosts';" ^
  "$ip='%LOCAL_IP%';" ^
  "$d=@('demoapi.cqg.com','api.cqg.com','depth-it.historical.deepcharts.com','data-b.historical.deepcharts.com');" ^
  "$c=Get-Content $h -Raw;" ^
  "foreach($x in $d){$c=$c -replace '(?m)^\d+\.\d+\.\d+\.\d+\s+'+$x.Replace('.','\.')+'[ \t]*(\r?\n|$)',''}" ^
  "foreach($x in $d){$c+=\"`r`n$ip  $x\"}" ^
  "Set-Content $h -Value $c -Force"
echo    [+] Hosts updated (IP: %LOCAL_IP%)

:: ── Check patched_run ────────────────────────────────────────────────────
echo [4/8] Checking patched Deepchart...
set "DC_PATH=%~dp0patched_run"
if not exist "%DC_PATH%\Deepchart.exe" (
    echo    [*] Running patcher...
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0patcher.ps1" -NoPause 2>&1
    if not exist "%DC_PATH%\Deepchart.exe" (
        echo    [!] Deepchart not found. Install Deepchart first, then run start.bat again.
        pause
        exit /b 1
    )
) else ( echo    [+] Found at %DC_PATH% )

:: ── Kill old processes ────────────────────────────────────────────────────
echo [5/8] Stopping old proxy processes only...
for %%p in (VolumetricaBridge Deepchart) do (
    taskkill /F /IM %%p.exe >nul 2>&1
)
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name = 'python.exe'\" | Where-Object { $$_.CommandLine -match 'bridge_mitm|vol_hist' } | ForEach-Object { Stop-Process -Id $$_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1
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
