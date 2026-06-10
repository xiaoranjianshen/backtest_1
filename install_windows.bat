@echo off
setlocal
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\install_windows.ps1" %*
if errorlevel 1 (
    echo.
    echo Installation failed. Press any key to exit.
    pause >nul
    exit /b 1
)

echo.
echo Installation finished. Press any key to exit.
pause >nul
