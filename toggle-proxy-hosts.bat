@echo off
>nul 2>&1 net session || (
    echo This script needs administrator privileges.
    echo Right-click and select "Run as administrator".
    pause
    exit /b 1
)

REM Auto-detect local IP (same as fix_hosts.bat)
for /f "tokens=3 delims=: " %%i in ('netsh interface ip show addresses ^| findstr "IP Address" ^| findstr /v "127.0.0.1"') do (
    set "LOCAL_IP=%%i"
    goto :foundip
)
for /f "tokens=3 delims=: " %%a in ('ipconfig ^| findstr /i "IPv4"') do (
    if not defined LOCAL_IP set "LOCAL_IP=%%a"
)
:foundip
if not defined LOCAL_IP (
    echo Could not detect IP. Run fix_hosts.bat first.
    pause
    exit /b 1
)

echo Detected IP: %LOCAL_IP%

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$e1='%LOCAL_IP% real.deepcharts.com';" ^
  "$e2='%LOCAL_IP% depth-it.deepcharts.com';" ^
  "$e3='%LOCAL_IP% depth-it.historical.deepcharts.com';" ^
  "$e4='%LOCAL_IP% data-b.historical.deepcharts.com';" ^
  "$path=Join-Path ([Environment]::SystemDirectory) 'drivers\etc\hosts';" ^
  "$content=Get-Content $path -ReadCount 0;" ^
  "foreach($e in @($e1,$e2,$e3,$e4)){" ^
    "if($content -contains $e){" ^
      "$content=@($content|?{$_ -ne $e});" ^
      "Write-Host \"[$e] - REMOVED\"" ^
    "}else{" ^
      "$content+=$e;" ^
      "Write-Host \"[$e] - ADDED\"" ^
    "}" ^
  "}" ^
   "$content | Set-Content $path -Force;"

if %errorlevel% equ 0 (
    echo Done. Toggle hosts for other trading software, then re-run to use Deepchart.
) else (
    echo Failed.
)
pause
