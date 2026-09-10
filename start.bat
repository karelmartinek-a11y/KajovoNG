@echo off
setlocal
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1" %*
set "result=%errorlevel%"
if not "%result%"=="0" (
    echo.
    echo Spusteni selhalo. Podrobnosti jsou uvedeny vyse.
    pause
)
exit /b %result%
