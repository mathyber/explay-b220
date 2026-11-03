@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Project Python was not found. See README.md.
    pause
    exit /b 1
)
if not exist "analysis\firmware-patches\cut\b220-cut.bin" (
    echo Build the firmware first: run "Start B220 CUT.cmd" once.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -X utf8 -m tools.icon_studio
if errorlevel 1 pause
