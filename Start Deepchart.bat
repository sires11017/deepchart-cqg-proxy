@echo off
title Deepcharts Live Feed
cd /d "%~dp0"

echo ====================================
echo  Starting Deepcharts Live Feed
echo ====================================
echo.

:: Kill any existing processes
taskkill /F /IM python.exe 2>nul >nul
taskkill /F /IM VolumetricaBridge.exe 2>nul >nul
taskkill /F /IM Deepchart.exe 2>nul >nul
timeout /t 3 /nobreak >nul

:: Start Volumetrica Historical Server
echo [*] Starting Historical Server (port 12010)...
start "Hist Server" python "%~dp0vol_hist_server.py"
timeout /t 3 /nobreak >nul

:: Start Bridge MITM Proxy (port 443)
echo [*] Starting Bridge Proxy (port 443)...
start "Bridge Proxy" python "%~dp0bridge_mitm_proxy.py"
timeout /t 3 /nobreak >nul

:: Start VolumetricaBridge (try common paths)
set "DC_PATH=C:\Deepchart\patched_run"
if not exist "%DC_PATH%\Deepchart.exe" (
    if exist "%~dp0patched_run\Deepchart.exe" set "DC_PATH=%~dp0patched_run"
)
echo [*] Starting Deepchart from %DC_PATH%...
start "VBridge" "%DC_PATH%\bridge\VolumetricaBridge.exe"
timeout /t 10 /nobreak >nul

:: Start Deepchart
echo [*] Starting Deepchart...
start "Deepchart" "%DC_PATH%\Deepchart.exe"

echo.
echo ====================================
echo  All services started.
echo  Deepchart should open a window.
echo  Close this batch window to stop everything.
echo ====================================
echo.
timeout /t 5 /nobreak >nul
