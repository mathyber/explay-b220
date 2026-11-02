@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Project Python was not found. See README.md.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -X utf8 -m tools.extract_k550_fonts --download
if errorlevel 1 (
    pause
    exit /b 1
)
if not exist "analysis\firmware-patches\sd-app\sd-app.bin" (
    ".venv\Scripts\python.exe" -X utf8 -m tools.build_sd_app
    if errorlevel 1 (
        pause
        exit /b 1
    )
)
".venv\Scripts\python.exe" -X utf8 -m tools.build_font_patch --font "analysis\donor-fonts\k550\Sans-Serif_Europe_Reg.ttf" --directory "analysis\firmware-patches\k550-font" --prepare-card
if errorlevel 1 (
    pause
    exit /b 1
)
start "" "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -STA -WindowStyle Hidden -File "%~dp0emulator\window.ps1" -Firmware "%~dp0analysis\firmware-patches\k550-font\b220-narrow.bin"
