$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$analysisPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $analysisPython)) {
    throw 'Create the local environment first: python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements-analysis.txt'
}
$env:PYTHONIOENCODING = 'utf-8'
$analysisSteps = @(
    @('tools/analyze_firmware.py'),
    @('tools/carve_resources.py'),
    @('tools/map_startup.py'),
    @('tools/boot_probe.py', '--out', 'analysis/boot_probe_ps_strict.json'),
    @('tools/boot_probe.py', '--fdl', '--out', 'analysis/boot_probe_fdl_strict.json'),
    @('tools/boot_probe.py', '--stub-zero-mmio', '--out', 'analysis/boot_probe_ps_zero.json'),
    @('tools/boot_probe.py', '--fdl', '--stub-zero-mmio', '--out', 'analysis/boot_probe_fdl_zero.json'),
    @('tools/verify_artifacts.py')
)
foreach ($analysisStep in $analysisSteps) {
    & $analysisPython @analysisStep
    if ($LASTEXITCODE -ne 0) { throw ('Analysis failed: ' + ($analysisStep -join ' ')) }
}
