@echo off
rem The main firmware: SD Apps -> Nokia font -> white clock -> CUT (wallpapers, menus,
rem boot animation, icons, font antialiasing). Missing earlier steps are built here.
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Project Python was not found. See README.md.
    pause
    exit /b 1
)
set FONT=analysis\donor-fonts\font\NokiaStandardMultiscript.ttf
if not exist "analysis\firmware-patches\sd-app\sd-app.bin" (
    ".venv\Scripts\python.exe" -X utf8 -m tools.build_sd_app
    if errorlevel 1 goto failed
)
if not exist "analysis\firmware-patches\main-font\b220-narrow.bin" (
    if not exist "%FONT%" (
        echo The main font was not found: %FONT%
        echo Put NokiaStandardMultiscript.ttf there, see CUT_RU.md.
        goto failed
    )
    ".venv\Scripts\python.exe" -X utf8 -m tools.build_font_patch --font "%FONT%" --directory "analysis\firmware-patches\main-font" --large-height 15 --small-height 13 --wide-cells
    if errorlevel 1 goto failed
)
if not exist "analysis\firmware-patches\white-clock\b220-white-clock.bin" (
    ".venv\Scripts\python.exe" -X utf8 -m tools.build_white_clock
    if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -X utf8 -m tools.build_cut --prepare-card
if errorlevel 1 goto failed
start "" "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -STA -WindowStyle Hidden -File "%~dp0emulator\window.ps1" -Firmware "%~dp0analysis\firmware-patches\cut\b220-cut.bin"
exit /b 0
:failed
pause
exit /b 1
