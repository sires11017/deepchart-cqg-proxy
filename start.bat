@echo off
cd /d "%~dp0"

:: ── Launcher mode ──────────────────────────────────────────────────────────
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
::  Launches the patched Deepchart.exe (Launcher) which does everything:
::   - Finds/downloads Python
::   - Installs pip packages
::   - Fixes hosts file
::   - Applies profiles
::   - Starts proxy servers
::   - Launches the real Deepchart
:: ═══════════════════════════════════════════════════════════════════════════════

:: ── Launch the all-in-one launcher ──────────────────────────────────────────
set "LAUNCHER=%~dp0patched_run\Deepchart.exe"
if not exist "%LAUNCHER%" (
    echo patched_run\Deepchart.exe not found. Run patcher.ps1 first.
    exit /b 1
)
start "" "%LAUNCHER%"
exit /b 0
