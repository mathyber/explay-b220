param(
    [switch]$Strict,
    [switch]$ContinuePastDspWait,
    [switch]$BootOnly,
    [int]$Instructions = 200000000,
    [double]$Seconds = 180,
    [int]$Frames = 0,
    [string]$Out = ''
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$emulatorPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not $Out) {
    if ($Strict) { $Out = 'analysis/emulator/strict.json' }
    elseif ($BootOnly) { $Out = 'analysis/emulator/boot-to-dsp.json' }
    else { $Out = 'analysis/emulator/current.json' }
}
if (-not (Test-Path -LiteralPath $emulatorPython)) {
    throw 'Create .venv and install requirements-analysis.txt first.'
}
$emulatorArgs = @('-m', 'emulator.run', '--instructions', "$Instructions", '--seconds', "$Seconds", '--frames', "$Frames", '--out', $Out)
if (-not $Strict) { $emulatorArgs += '--explore' }
if ($BootOnly) { $emulatorArgs += @('--until', '0x526912') }
& $emulatorPython @emulatorArgs
if ($LASTEXITCODE -ne 0) { throw 'Emulator failed; inspect the Python error above.' }
