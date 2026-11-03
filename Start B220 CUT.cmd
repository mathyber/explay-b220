@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Project Python was not found. See README.md.
    pause
    exit /b 1
)
if not exist "analysis\firmware-patches\white-clock\b220-white-clock.bin" (
    echo Build the white-clock firmware first: run "Start B220 WHITE CLOCK.cmd" once.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -X utf8 -m tools.build_cut --prepare-card
if errorlevel 1 (
    pause
    exit /b 1
)
start "" "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -STA -WindowStyle Hidden -File "%~dp0emulator\window.ps1" -Firmware "%~dp0analysis\firmware-patches\cut\b220-cut.bin"
