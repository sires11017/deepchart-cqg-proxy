@echo off
setlocal
title Deepcharts Launcher

:: Auto-elevate to admin (required for port 443)
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo Requesting administrator privileges...
    powershell -NoProfile -Command "Start-Process -Verb RunAs -FilePath '%~f0' -WindowStyle Normal"
    exit /b 0
)

cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "start_servers.ps1"
pause
