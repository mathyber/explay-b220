@echo off
rem Intermediate step (white clock, still the stock boot animation, wallpapers and
rem menus). The main firmware is "Start B220 CUT.cmd".
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Project Python was not found. See README.md.
    pause
    exit /b 1
)
rem Main font: Nokia Standard Multiscript (Nokia S60), 15/13 px, glyphs as wide as their slots hold.
set FONT=analysis\donor-fonts\font\NokiaStandardMultiscript.ttf
if not exist "analysis\firmware-patches\main-font\b220-narrow.bin" (
    if not exist "%FONT%" (
        echo The main font was not found: %FONT%
        echo Put NokiaStandardMultiscript.ttf there, see CUT_RU.md.
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
    ".venv\Scripts\python.exe" -X utf8 -m tools.build_font_patch --font "%FONT%" --directory "analysis\firmware-patches\main-font" --large-height 15 --small-height 13 --wide-cells
    if errorlevel 1 (
        pause
        exit /b 1
    )
)
".venv\Scripts\python.exe" -X utf8 -m tools.build_white_clock --prepare-card
if errorlevel 1 (
    pause
    exit /b 1
)
start "" "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -STA -WindowStyle Hidden -File "%~dp0emulator\window.ps1" -Firmware "%~dp0analysis\firmware-patches\white-clock\b220-white-clock.bin"
