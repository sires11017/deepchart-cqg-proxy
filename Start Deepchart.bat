@echo off
cd /d "%~dp0"
:: Simple launcher — delegates to the all-in-one Deepchart.exe
set "LAUNCHER=%~dp0patched_run\Deepchart.exe"
if exist "%LAUNCHER%" (
    start "" "%LAUNCHER%"
) else (
    echo patched_run\Deepchart.exe not found.
    pause
)
