@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Project Python was not found. See README.md.
    pause
    exit /b 1
)
if not exist "analysis\toolchains\zig-windows-x86_64-0.13.0\zig.exe" (
    ".venv\Scripts\python.exe" -X utf8 -m tools.setup_arm_toolchain
    if errorlevel 1 (
        pause
        exit /b 1
    )
)
".venv\Scripts\python.exe" -X utf8 -m tools.build_sd_app
if errorlevel 1 (
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -X utf8 -m tools.install_sd_app --ensure
if errorlevel 1 (
    pause
    exit /b 1
)
start "" "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -STA -WindowStyle Hidden -File "%~dp0emulator\window.ps1" -Firmware "%~dp0analysis\firmware-patches\sd-app\sd-app.bin"
