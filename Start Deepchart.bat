@echo off
cd /d "%~dp0"
if "%1"=="--worker" goto :main

net session >nul 2>&1
if %errorLevel% neq 0 (
    powershell -NoProfile -Command "Start-Process -Verb RunAs -FilePath '%~f0' -WindowStyle Hidden"
    exit /b 0
)
start /b "" cmd /c "%~f0 --worker >nul 2>&1"
exit /b 0

:main
:: ── Find pythonw ─────────────────────────────────────────────────────────
:: ── Resolve upstream IPs before hosts redirect ─────────────────────────────
for /f "tokens=2 delims=:" %%a in ('nslookup demoapi.cqg.com 8.8.8.8 2^>nul ^| findstr /C:"Address:" ^| findstr /V /C:"8.8.8.8"') do set "CQG_UPSTREAM_IP=%%a"
if defined CQG_UPSTREAM_IP set "CQG_UPSTREAM_IP=%CQG_UPSTREAM_IP: =%"
if not defined CQG_UPSTREAM_IP set "CQG_UPSTREAM_IP=208.48.16.22"
for /f "tokens=2 delims=:" %%a in ('nslookup depth-it.historical.deepcharts.com 8.8.8.8 2^>nul ^| findstr /C:"Address:" ^| findstr /V /C:"8.8.8.8"') do set "HIST_UPSTREAM_IP=%%a"
if defined HIST_UPSTREAM_IP set "HIST_UPSTREAM_IP=%HIST_UPSTREAM_IP: =%"
if not defined HIST_UPSTREAM_IP set "HIST_UPSTREAM_IP=%CQG_UPSTREAM_IP%"

set "PYTHONW="
for %%v in (pythonw python3w) do (
    for /f "delims=" %%a in ('where %%v 2^>nul') do (
        for /f "tokens=*" %%b in ('%%v --version 2^>^&1') do (
            echo %%b | findstr /i "Python 3" >nul && set "PYTHONW=%%v" && goto :pyfnd
        )
    )
)
for %%p in (C:\Python314\pythonw.exe C:\Python313\pythonw.exe "%ProgramFiles%\Python314\pythonw.exe" "%ProgramFiles%\Python313\pythonw.exe" "%LocalAppData%\Programs\Python\Python314\pythonw.exe" "%LocalAppData%\Programs\Python\Python313\pythonw.exe") do (
    if exist %%p set "PYTHONW=%%p" && goto :pyfnd
)
for %%v in (python python3 py) do (
    for /f "delims=" %%a in ('where %%v 2^>nul') do (
        for /f "tokens=*" %%b in ('%%v --version 2^>^&1') do (
            echo %%b | findstr /i "Python 3" >nul && set "PYTHON=%%v" && goto :pyfnd
        )
    )
)
:pyfnd
if not defined PYTHONW set "PYTHONW=%PYTHON%"
if not defined PYTHONW set "PYTHONW=python"

taskkill /F /IM python.exe >nul 2>&1
taskkill /F /IM pythonw.exe >nul 2>&1
taskkill /F /IM VolumetricaBridge.exe >nul 2>&1
taskkill /F /IM Deepchart.exe >nul 2>&1
timeout /t 3 /nobreak >nul

start /b "" "%PYTHONW%" "%~dp0vol_hist_server.py" >nul 2>&1
timeout /t 3 /nobreak >nul

start /b "" "%PYTHONW%" "%~dp0bridge_mitm_proxy.py" >nul 2>&1
timeout /t 3 /nobreak >nul

set "DC_PATH=%~dp0patched_run"
if not exist "%DC_PATH%\Deepchart.exe" (
    if exist "C:\Deepchart\patched_run\Deepchart.exe" set "DC_PATH=C:\Deepchart\patched_run"
)
powershell -NoProfile -Command "Start-Process -WindowStyle Hidden -FilePath '%DC_PATH%\bridge\VolumetricaBridge.exe'"
timeout /t 6 /nobreak >nul

start "" "%DC_PATH%\Deepchart.exe"
timeout /t 5 /nobreak >nul
