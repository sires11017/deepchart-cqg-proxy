@echo off
setlocal

set "SCRIPT=%~dp0run_patched_deepchart.ps1"

if not exist "%SCRIPT%" (
    echo Missing runner: "%SCRIPT%"
    pause
    exit /b 1
)

start "" powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%SCRIPT%"
exit /b 0