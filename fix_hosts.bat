@echo off
title Deepchart Proxy - Hosts File Setup
cd /d "%~dp0"

>nul 2>&1 net session || (
    echo This script needs administrator privileges.
    echo Right-click and select "Run as administrator".
    pause
    exit /b 1
)

echo ============================================
echo  Deepchart CQG Proxy - Hosts File Setup
echo ============================================
echo.
echo This will add CQG domains to your hosts file
echo so traffic goes through the proxy.
echo.

REM Auto-detect the most likely local IP
for /f "tokens=3 delims=: " %%i in ('netsh interface ip show addresses ^| findstr "IP Address" ^| findstr /v "127.0.0.1"') do (
    set "LOCAL_IP=%%i"
    goto :foundip
)

REM Fallback: try ipconfig
for /f "tokens=3 delims=: " %%a in ('ipconfig ^| findstr /i "IPv4"') do (
    if not defined LOCAL_IP set "LOCAL_IP=%%a"
)

:foundip
if not defined LOCAL_IP (
    echo Could not detect your IP address automatically.
    set /p LOCAL_IP="Enter your local IP (run ipconfig to find it): "
)
if not defined LOCAL_IP (
    echo No IP entered. Exiting.
    pause
    exit /b 1
)

echo Detected IP: %LOCAL_IP%
echo.
echo This IP will be used for the following domains:
echo   %LOCAL_IP%  demoapi.cqg.com
echo   %LOCAL_IP%  api.cqg.com
echo   %LOCAL_IP%  depth-it.historical.deepcharts.com
echo   %LOCAL_IP%  data-b.historical.deepcharts.com
echo.
choice /c YN /m "Is this correct?"
if errorlevel 2 (
    set /p LOCAL_IP="Enter your local IP manually: "
    if not defined LOCAL_IP (
        echo No IP entered. Exiting.
        pause
        exit /b 1
    )
)

echo.
echo Updating hosts file...

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    $hosts = Join-Path ([Environment]::SystemDirectory) 'drivers\etc\hosts'; ^
    $ip = '%LOCAL_IP%'; ^
    $entries = @( ^
        \"$ip  demoapi.cqg.com\", ^
        \"$ip  api.cqg.com\", ^
        \"$ip  depth-it.historical.deepcharts.com\", ^
        \"$ip  data-b.historical.deepcharts.com\" ^
    ); ^
    $content = Get-Content $hosts -Raw; ^
    $domains = @('demoapi.cqg.com','api.cqg.com','depth-it.historical.deepcharts.com','data-b.historical.deepcharts.com'); ^
    foreach ($d in $domains) { ^
        $content = $content -replace '(?m)^\d+\.\d+\.\d+\.\d+\s+' + $d.Replace('.','\.'), \"$ip  $d\"; ^
    }; ^
    foreach ($e in $entries) { ^
        if ($content -notmatch [regex]::Escape($e)) { ^
            $content += \"`r`n$e\"; ^
        } ^
    }; ^
    Set-Content $hosts -Value $content -Force; ^
    Write-Host 'Hosts file updated successfully!'

echo.
echo Done! The proxy will now intercept these domains.
echo.
pause
