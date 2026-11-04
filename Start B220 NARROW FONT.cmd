@echo off
setlocal
cd /d "%~dp0"
set FIRMWARE=analysis\firmware-patches\condensed-font\cut\b220-cut.bin
if not exist "%FIRMWARE%" (
    echo Experimental firmware was not found: %FIRMWARE%
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -X utf8 -m tools.experiment_card "%FIRMWARE%"
if errorlevel 1 (
    pause
    exit /b 1
)
start "" "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -STA -WindowStyle Hidden -File "%~dp0emulator\window.ps1" -Firmware "%~dp0%FIRMWARE%"
