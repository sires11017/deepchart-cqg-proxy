@echo off
cd /d "%~dp0"

:: ── Worker mode ──────────────────────────────────────────────────────────────
if "%1"=="--worker" goto :main

:: ── Auto-elevate to admin ────────────────────────────────────────────────────
net session >nul 2>&1
if %errorLevel% neq 0 (
    powershell -NoProfile -Command "Start-Process -Verb RunAs -FilePath '%~f0' -WindowStyle Hidden"
    exit /b 0
)

:: ── Re-launch as hidden background worker ─────────────────────────────────────
start /b "" cmd /c "%~f0 --worker >nul 2>&1"
exit /b 0

:main
:: ═══════════════════════════════════════════════════════════════════════════════
::  HIDDEN WORKER — No console windows (except Deepchart)
:: ═══════════════════════════════════════════════════════════════════════════════

:: ── Step 0: Resolve upstream IPs BEFORE hosts redirect ──────────────────────
:: Use nslookup with public DNS (bypasses hosts file) to get real IPs
for /f "tokens=2 delims=:" %%a in ('nslookup demoapi.cqg.com 8.8.8.8 2^>nul ^| findstr /C:"Address:" ^| findstr /V /C:"#"') do set "CQG_UPSTREAM_IP=%%a"
if defined CQG_UPSTREAM_IP set "CQG_UPSTREAM_IP=%CQG_UPSTREAM_IP: =%"
if not defined CQG_UPSTREAM_IP set "CQG_UPSTREAM_IP=208.48.16.22"
for /f "tokens=2 delims=:" %%a in ('nslookup depth-it.historical.deepcharts.com 8.8.8.8 2^>nul ^| findstr /C:"Address:" ^| findstr /V /C:"#"') do set "HIST_UPSTREAM_IP=%%a"
if defined HIST_UPSTREAM_IP set "HIST_UPSTREAM_IP=%HIST_UPSTREAM_IP: =%"
if not defined HIST_UPSTREAM_IP set "HIST_UPSTREAM_IP=%CQG_UPSTREAM_IP%"

:: ── Find or download Python (prefer pythonw) ─────────────────────────────────
set "PYTHON="
set "PYTHONW="
for %%v in (pythonw python3w py) do (
    for /f "delims=" %%a in ('where %%v 2^>nul') do (
        for /f "tokens=*" %%b in ('%%v --version 2^>^&1') do (
            echo %%b | findstr /i "Python 3" >nul && set "PYTHONW=%%v" && goto :python_found
        )
    )
)
for %%p in (C:\Python314\pythonw.exe C:\Python313\pythonw.exe "%ProgramFiles%\Python314\pythonw.exe" "%ProgramFiles%\Python313\pythonw.exe" "%LocalAppData%\Programs\Python\Python314\pythonw.exe" "%LocalAppData%\Programs\Python\Python313\pythonw.exe") do (
    if exist %%p set "PYTHONW=%%p" && goto :python_found
)
for %%v in (python python3 py) do (
    for /f "delims=" %%a in ('where %%v 2^>nul') do (
        for /f "tokens=*" %%b in ('%%v --version 2^>^&1') do (
            echo %%b | findstr /i "Python 3" >nul && set "PYTHON=%%v" && goto :python_found
        )
    )
)
for %%p in (C:\Python314\python.exe C:\Python313\python.exe "%ProgramFiles%\Python314\python.exe" "%ProgramFiles%\Python313\python.exe" "%LocalAppData%\Programs\Python\Python314\python.exe" "%LocalAppData%\Programs\Python\Python313\python.exe") do (
    if exist %%p set "PYTHON=%%p" && goto :python_found
)
if not defined PYTHONW if not defined PYTHON (
    set "PYDIR=%~dp0_python"
    if not exist "%PYDIR%" mkdir "%PYDIR%"
    powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.14.6/python-3.14.6-embed-amd64.zip' -OutFile '%TEMP%\_py.zip'"
    powershell -NoProfile -Command "Expand-Archive -Path '%TEMP%\_py.zip' -DestinationPath '%PYDIR%' -Force"
    del "%TEMP%\_py.zip" 2>nul
    for %%f in ("%PYDIR%\python*._pth") do powershell -NoProfile -Command "(Get-Content '%%f') -replace '^import site$', '#import site' | Set-Content '%%f'"
    powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile '%TEMP%\_getpip.py'"
    "%PYDIR%\python.exe" "%TEMP%\_getpip.py" --quiet >nul 2>&1
    del "%TEMP%\_getpip.py" 2>nul
    set "PYTHON=%PYDIR%\python.exe"
    set "PYTHONW=%PYDIR%\pythonw.exe"
    if not exist "%PYTHONW%" set "PYTHONW=%PYTHON%"
    goto :python_ready
)
:python_found
if not defined PYTHONW set "PYTHONW=%PYTHON%"
if not defined PYTHON set "PYTHON=%PYTHONW%"

:python_ready
"%PYTHON%" -m pip install -r "%~dp0requirements.txt" >nul 2>&1

:: ── Fix hosts ────────────────────────────────────────────────────────────────
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

:: ── Check patched_run ────────────────────────────────────────────────────────
set "DC_PATH=%~dp0patched_run"
if not exist "%DC_PATH%\Deepchart.exe" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0patcher.ps1" -NoPause 2>&1 >nul
    if not exist "%DC_PATH%\Deepchart.exe" exit /b 1
)

:: ── Apply profiles ───────────────────────────────────────────────────────────
powershell -NoProfile -Command ^
  "$r='%~dp0';$p=Join-Path $r 'profiles';$t=Join-Path $r 'patched_run\data';" ^
  "if(Test-Path $p){New-Item $t -ItemType Directory -Force|Out-Null;" ^
    "Get-ChildItem $p -Directory|ForEach-Object{Copy-Item $_.FullName (Join-Path $t $_.Name) -Recurse -Force};" ^
    "Get-ChildItem $p -File|ForEach-Object{Copy-Item $_.FullName $t -Force}}"
powershell -NoProfile -Command ^
  "$s=Join-Path '%~dp0' 'profiles\Roaming';$d=\"$env:APPDATA\Deepchart\";" ^
  "if(Test-Path $s){New-Item $d -ItemType Directory -Force|Out-Null;Get-ChildItem $s|ForEach-Object{Copy-Item $_.FullName $d -Force}}"

:: ── Kill all old processes ──────────────────────────────────────────────────
taskkill /F /IM python.exe >nul 2>&1
taskkill /F /IM pythonw.exe >nul 2>&1
taskkill /F /IM VolumetricaBridge.exe >nul 2>&1
taskkill /F /IM Deepchart.exe >nul 2>&1
timeout /t 2 /nobreak >nul

:: ── Start servers (hidden, no console windows) ──────────────────────────────
start /b "" "%PYTHONW%" "%~dp0vol_hist_server.py" >nul 2>&1
timeout /t 3 /nobreak >nul

start /b "" "%PYTHONW%" "%~dp0bridge_mitm_proxy.py" >nul 2>&1
timeout /t 4 /nobreak >nul

:: ── Launch VolumetricaBridge (hidden) ────────────────────────────────────────
powershell -NoProfile -Command "Start-Process -WindowStyle Hidden -FilePath '%DC_PATH%\bridge\VolumetricaBridge.exe'"
timeout /t 6 /nobreak >nul

:: ── Launch Deepchart (visible — the only window the user sees) ──────────────
start "" "%DC_PATH%\Deepchart.exe"
timeout /t 5 /nobreak >nul
