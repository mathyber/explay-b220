@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Project Python was not found. See README.md.
    pause
    exit /b 1
)
if not exist "analysis\toolchains\ghidra_12.2_PUBLIC\ghidraRun.bat" (
    ".venv\Scripts\python.exe" -B -m tools.setup_reverse_tools
    if errorlevel 1 (
        pause
        exit /b 1
    )
)
".venv\Scripts\python.exe" -B -m tools.prepare_reverse_project
if errorlevel 1 (
    pause
    exit /b 1
)
set "JAVA_HOME=%~dp0analysis\toolchains\jdk-25.0.5+8"
set "APPDATA=%~dp0analysis\toolchains\ghidra-local\settings"
set "LOCALAPPDATA=%~dp0analysis\toolchains\ghidra-local\cache"
set "JAVA_TOOL_OPTIONS=-Duser.home="%~dp0analysis\toolchains\ghidra-local\home" -Dapplication.settingsdir="%APPDATA%" -Dapplication.cachedir="%LOCALAPPDATA%" -Dapplication.tempdir="%~dp0analysis\toolchains\ghidra-local\temp" -Djava.io.tmpdir="%~dp0analysis\toolchains\ghidra-local\temp""
call "%~dp0analysis\toolchains\ghidra_12.2_PUBLIC\ghidraRun.bat" "%~dp0analysis\reverse\projects\B220-original-pilot.gpr:/SC6600L_S915D_B220_L7A_64X32_M224_qcif.bin"
